# Design 05 — Persistence, Migrations & Schema Integrity

**Status:** Proposed
**Addresses:** H13 (no migrations; `create_all` on startup; seed race), M7
(client-side `time.time_ns` ordering key), M16 (room-create check-then-insert →
500 not 409), M17 (cascade delete destroys the audit transcript; `AuditLog`
unindexed/unjoinable), and schema Lows (enum-as-string, missing indexes,
naive/aware datetime, unbounded `display_name`, seed race).
**Effort:** M (~1 sprint)

---

## Problem

The data layer is well-built for a single-process SQLite dev loop but has no
production migration path and several portability/integrity gaps that only bite
on Postgres and/or multi-replica.

- **H13 — no migrations.** `init_db` runs `Base.metadata.create_all` on every
  startup ([db/base.py:57](../../backend/app/db/base.py#L57)); Alembic is absent
  from the repo and `requirements.txt`. `create_all` never `ALTER`s an existing
  table, so the first prod deploy that adds/renames a column silently no-ops and
  produces runtime `UndefinedColumnError`. Multi-replica cold boot also runs
  `seed_global_config` (check-then-insert,
  [orchestrator.py:321](../../backend/app/agents/orchestrator.py#L321))
  concurrently → one replica can crash on a PK `IntegrityError`.
- **M7 — ordering key is wall-clock.** `seq` defaults to `time.time_ns`
  ([models.py:97](../../backend/app/db/models.py#L97)), assigned app-side per
  process. Two replicas with ms clock skew interleave the "immutable" transcript
  out of true order; `_first_speaker` then reads the wrong last agent and one
  agent speaks twice.
- **M16 — create race.** Room creation `SELECT ... then add` has no
  `IntegrityError` handling ([rooms.py:111](../../backend/app/api/rooms.py#L111));
  the unique-constraint loser 500s instead of returning 409.
- **M17 — audit integrity.** `messages.room_id` is `ondelete="CASCADE"` and
  `Room` has `cascade="all, delete-orphan"`
  ([models.py:98](../../backend/app/db/models.py#L98)), so any future room-delete
  physically destroys the regulatory transcript the module docstring calls
  immutable. `AuditLog.room_id` is a bare unindexed `String(36)` with no FK.
- **Lows.** State-machine columns (`status`, `sender_type`, `role`,
  connection `status`) are plain strings whose legal values live only in
  comments; `RoomInvite.room_id`, `AgentSkill.room_id`/`agent_key`,
  `AuditLog.room_id` are unindexed; naive-vs-aware datetime is patched in exactly
  one place ([rooms.py:178](../../backend/app/api/rooms.py#L178)) rather than
  systemically; `JoinRequest.display_name` has no max against `String(256)` →
  Postgres-only `StringDataRightTruncation`.

## Goals

- A repeatable, reviewable schema-migration process; `create_all` gone from the
  prod path.
- Authoritative, monotonic message ordering that is correct across replicas.
- Concurrency-safe writes that return correct HTTP status codes.
- The audit transcript cannot be silently destroyed.
- State values and string lengths are enforced by the schema, not comments.

## Design

### 1. Alembic migrations (H13)

- Add `alembic` to `requirements.txt`; `alembic init`; autogenerate the initial
  migration from current metadata as the baseline.
- Replace `init_db`'s `create_all` with:
  - **dev/test:** keep `create_all` for the zero-config SQLite loop, gated on
    `CABINET_ENV=dev` (see [Design 01](01-fail-closed-production-config.md)).
  - **staging/prod:** run `alembic upgrade head` as a **release/init step**
    (a separate container command or ACA job), never inside app startup, so N
    replicas don't race DDL.
- Make `seed_global_config` idempotent and race-safe with an upsert
  (`INSERT ... ON CONFLICT DO NOTHING` on Postgres / `INSERT OR IGNORE` on
  SQLite) instead of check-then-insert, or fold the seed into a migration.

### 2. Authoritative ordering (M7)

Replace the client-side `time.time_ns` default with a **DB-assigned monotonic
sequence**:

- **Postgres:** a `BIGSERIAL`/`Sequence` for `seq` (global monotonic is enough
  for ordering; per-room ordering is `WHERE room_id ORDER BY seq`).
- **SQLite (tests):** `INTEGER PRIMARY KEY AUTOINCREMENT` semantics via a
  sequence-like default; a single writer makes wall-clock ties impossible
  anyway, but aligning on a DB-side counter keeps dev/prod consistent.

Keep `(seq, id)` as the sort key. This also removes the NTP-step-back hazard on
a single host. `_first_speaker` and `_history_as_turns` need no change beyond
trusting `seq`.

### 3. Concurrency-safe room creation (M16)

Wrap the insert and translate the unique violation:

```python
try:
    session.add(room); await session.flush()
except IntegrityError:
    await session.rollback()
    raise HTTPException(409, "a room for this customer already exists")
```

Apply the same pattern anywhere a unique constraint can lose a race
(`RoomMember (room_id, user_email)` on concurrent joins — coordinate with the
atomic invite claim in [Design 03](03-authorization-and-tenancy-hardening.md)).

### 4. Protect the audit transcript (M17)

- Change room deletion policy to **soft delete**: add `Room.deleted_at`
  (nullable); "delete" sets the timestamp and hides the room from listings but
  preserves messages and audit rows. Physical cascade delete of messages is
  removed (`ondelete="RESTRICT"` or no cascade) so the transcript can't vanish.
- Give `AuditLog.room_id` a real FK (nullable, `ondelete="SET NULL"`) and an
  index, so audit rows stay joinable and survive room lifecycle.
- If hard deletion is ever required for data-retention/GDPR, make it an explicit,
  audited, admin-only operation — not an ORM cascade side effect.

### 5. Enforce enums and lengths (Lows)

- Introduce Python `enum.StrEnum` for `room.status`, `message.sender_type`,
  `member.role`, `gdrive.status`, and map with SQLAlchemy `Enum(...)` (native
  enum on Postgres, `VARCHAR + CHECK` on SQLite). This makes a typo'd status a
  write error instead of silent state-machine corruption.
- Add a `TZDateTime` `TypeDecorator` that always stores/returns tz-aware UTC,
  applied to every `DateTime` column, so dev (SQLite naive) and prod (aware)
  serialize identically — removing the one-off patch at
  [rooms.py:178](../../backend/app/api/rooms.py#L178).
- Add `max_length` to all free-text Pydantic fields that map to bounded columns
  (`display_name`, `sender_name`, etc.) — see also [Design 06](06-prompt-injection-and-untrusted-content.md)
  for the large-body limits.
- Add the missing indexes (`room_invites.room_id`, `agent_skills.room_id`,
  `agent_skills.agent_key`, `audit_log.room_id`, `audit_log.created_at`).

## Implementation sketch

- `requirements.txt`: `alembic`.
- New `backend/alembic/` + `alembic.ini`; baseline migration; a migration per
  change above.
- `db/base.py`: gate `create_all`; add `TZDateTime`.
- `db/models.py`: `seq` server-side sequence; enums; FK/index on `AuditLog`;
  `Room.deleted_at`; relax message cascade.
- `orchestrator.py`: upsert seed.
- `api/rooms.py`: `IntegrityError` → 409; filter `deleted_at IS NULL` in
  listings.
- `schemas.py`: `max_length` on bounded fields.

## Testing

- `test_migrations.py`: `alembic upgrade head` then `downgrade base` runs clean
  on SQLite; the autogenerate diff against metadata is empty (guards against
  model/migration drift in CI).
- `test_rooms.py`: concurrent create for one customer → exactly one 201, one
  409; ordering test that interleaves inserts and asserts `seq` monotonicity.
- `test_audit.py` (new, also serves M20): a room soft-delete preserves messages
  and audit rows; audit rows are queryable by `room_id`.
- Enum: writing an invalid `status` raises at the DB layer.

## Rollout & risks

- The `seq` change is the most delicate: existing dev rows have ns-scale values;
  a Postgres sequence should start above any migrated max. Prod has no data yet,
  so this is low-risk if sequenced before go-live.
- Switching off `create_all` in prod means the deploy pipeline **must** run
  `alembic upgrade` — wire it into [Design 09](09-ci-quality-gates-and-supply-chain.md)'s
  release job and the [Azure runbook](../../infra/azure/README.md).
- Soft delete changes list semantics; audit any query that assumed hard deletes.
