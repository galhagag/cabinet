# Design 03 — Authorization & Tenancy Hardening

**Status:** Proposed

**Phase 1 progress:** H3 (list_rooms scoped to membership) and H4 (admin read
endpoints gated by require_admin) shipped in
`fix/authz-list-rooms-admin-reads-03`. Remaining: M3 (single-use/revocable
invites) and the Entra identity Lows (`oid` keying, JWKS refetch protection) —
not yet started.

**Addresses:** H3 (`list_rooms` leaks all rooms + message previews), H4 (admin
read endpoints ungated), M3 (invites multi-use, non-revocable), and the Entra
identity Lows (mutable `preferred_username` as the identity key; unknown-`kid`
JWKS refetch amplification).
**Effort:** M (~1 sprint)

---

## Problem

The room-membership boundary is enforced on `/messages` but has holes elsewhere,
and the identity it's built on is weaker than it should be for a regulated
multi-tenant system where each room is a *different customer's* onboarding.

- **H3 — room enumeration.** `list_rooms` takes no identity dependency and
  returns every room including `last_message.content`
  ([rooms.py:148](../../backend/app/api/rooms.py#L148)). Any authenticated user
  (any token holder in the tenant) reads a message preview of every customer's
  room.
- **H4 — admin disclosure.** `list_agent_configs`, `get_agent_config`,
  `list_global_skills` omit `require_admin`
  ([admin.py:22](../../backend/app/api/admin.py#L22)); only mutations are gated.
  Proprietary baseline prompts and the skill inventory are readable by anyone.
- **M3 — invite reuse.** `join_room` checks only existence + expiry
  ([rooms.py:167](../../backend/app/api/rooms.py#L167)); no `used`/`max_uses`,
  no revoke endpoint. One leaked link admits unlimited strangers for 7 days.
- **Identity Lows.** `EntraTokenValidator` keys identity on the mutable,
  reassignable `preferred_username`
  ([entra_auth.py:94](../../backend/app/services/entra_auth.py#L94)); a renamed
  UPN could inherit another principal's room membership / admin rights. Unknown
  `kid` triggers an unconditional JWKS refetch
  ([entra_auth.py:56](../../backend/app/services/entra_auth.py#L56)), so a
  stream of random-`kid` tokens amplifies load on the backend and Microsoft.

## Goals

- Every room-scoped read is filtered to the caller's memberships.
- Admin *reads* are gated exactly like admin *writes*.
- Invite links are single-use by default, bounded, and revocable.
- Authorization keys on a stable, unique principal identifier.
- JWKS refetch is rate-limited / negatively cached.

## Non-goals

- Role granularity beyond the existing `owner|member` and the admin allowlist
  (a full RBAC model is a later feature).
- Replacing the allowlist with Entra app-roles — noted as a follow-up.

## Design

### 1. Scope `list_rooms` to the caller

```python
@router.get("", response_model=list[RoomOut])
async def list_rooms(
    session: AsyncSession = Depends(get_session),
    user_email: str = Depends(get_current_user_email),
) -> list[RoomOut]:
    result = await session.execute(
        select(Room)
        .join(RoomMember, RoomMember.room_id == Room.id)
        .where(RoomMember.user_email == user_email)
        .options(selectinload(Room.agents))
        .order_by(Room.created_at)
    )
    ...
```

Admins may optionally see all rooms via an explicit `?all=true` that itself
requires `require_admin`. `last_message` previews remain, but only for rooms the
caller belongs to.

### 2. Gate admin reads

Add `_admin: str = Depends(require_admin)` to `list_agent_configs`,
`get_agent_config`, and `list_global_skills`. Combined with
[Design 01](01-fail-closed-production-config.md)'s fail-closed allowlist, this
closes the disclosure entirely.

### 3. Single-use, revocable invites

Schema (migration via [Design 05](05-persistence-migrations-and-schema-integrity.md)):

```
room_invites
  + max_uses     INT   DEFAULT 1
  + use_count    INT   DEFAULT 0
  + revoked_at   TIMESTAMPTZ NULL
```

`join_room` becomes an atomic conditional claim so concurrent joins can't
over-consume a single-use link:

```python
claimed = await session.execute(
    update(RoomInvite)
    .where(
        RoomInvite.token == token,
        RoomInvite.revoked_at.is_(None),
        RoomInvite.expires_at > now,
        RoomInvite.use_count < RoomInvite.max_uses,
    )
    .values(use_count=RoomInvite.use_count + 1)
    .returning(RoomInvite.room_id)
)
```

Add `DELETE /api/rooms/{id}/invites/{token}` (owner/admin) that sets
`revoked_at`, and `GET /api/rooms/{id}/invites` to list active links. Shorten
the default TTL from 168h to e.g. 48h (`CABINET_INVITE_TTL_HOURS`). Every
join/revoke writes an `audit_log` row.

### 4. Stable identity claim

In `EntraTokenValidator`, derive the authorization principal from `oid` (+ `tid`
for tenant scoping) — immutable per Microsoft's guidance — and carry
`preferred_username`/`email` only as a *display* attribute. This is a data-model
change: `RoomMember`/`AuditLog.actor` should key on `oid` with email stored
alongside for readability. Provide a one-time backfill that maps existing
email-keyed rows (dev data only; prod has no rows yet).

> Note: this is the one change with real migration weight. If we want to keep
> email as the key short-term, at minimum validate that `preferred_username`
> equals a verified `email` claim and log when they diverge — but `oid` is the
> correct long-term key and cheaper to adopt before there's production data.

### 5. JWKS refetch protection

- Cache the "unknown kid → refetch" path behind a short cooldown (e.g. one
  refetch per `kid` per 5 minutes) and a global minimum interval, so a flood of
  random-`kid` tokens triggers at most one upstream fetch per window.
- Negatively cache genuinely-unknown `kid`s for a short TTL so repeated bad
  tokens short-circuit to 401 without a fetch.

## Implementation sketch

- `rooms.py`: identity dep + membership join on `list_rooms`; atomic
  `join_room`; new revoke/list-invites endpoints.
- `admin.py`: `require_admin` on the three read endpoints.
- `entra_auth.py`: return a principal object `{oid, tid, email, name}`;
  cooldown/negative-cache around `_fetch_jwks`.
- `deps.py`: `get_current_user_principal` (structured) alongside the existing
  email dep, migrating callers incrementally.
- `models.py` + migration: invite columns; (stage 4) principal keying.

## Testing

- `test_hardening.py`: extend — a non-member calling `list_rooms` sees only
  their rooms; a non-admin gets 403 on all three admin reads; a single-use
  invite works once then 409s; a revoked invite 404/410s; two concurrent joins
  on a single-use link yield exactly one membership.
- `test_entra_auth.py`: identity resolves from `oid`; a token whose
  `preferred_username` changed but `oid` is stable maps to the same principal; a
  burst of random-`kid` tokens triggers ≤1 JWKS fetch.

## Rollout & risks

- H3/H4 fixes are pure tightening — low risk, ship immediately. Watch for any
  frontend code assuming `list_rooms` returns *all* rooms (it shouldn't).
- Invite changes are additive (defaults preserve "works at least once").
- The `oid` keying is the riskiest piece; sequence it before production data
  exists, or gate behind a feature flag with the email-equals-verified-email
  interim check.
