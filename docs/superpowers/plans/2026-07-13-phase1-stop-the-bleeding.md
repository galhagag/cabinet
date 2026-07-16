# Phase 1 — Stop the Bleeding: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the four small, high-impact, low-risk gaps the
[2026-07-12 codebase review](../../reviews/2026-07-12-codebase-review.md) calls
"stop the bleeding": the broken frontend build (C1), the fail-open auth/admin
boot defaults (H1/H2 + M8), the unscoped room listing and ungated admin reads
(H3/H4), and the unhandled LLM failure that strands a room forever (C2).

**Architecture:** Four independent tasks, each scoped to one design doc under
`docs/designs/` and touching a disjoint set of files. There is no shared state
or file overlap between tasks, so they can be implemented, tested, and PR'd in
parallel, each in its own branch off `main`.

**Tech Stack:** FastAPI / SQLAlchemy (async) / pytest (backend), Vite / React /
TypeScript (frontend). No new dependencies.

## Global Constraints

- Backend tests: `cd backend && python -m pytest tests -q` — must pass with
  zero failures before every commit that touches backend code.
- Frontend build: `cd frontend && npm run build` — must succeed (this *is* the
  regression test for Task 1; there is no frontend test runner yet per
  [Design 09](../../designs/09-ci-quality-gates-and-supply-chain.md)).
- One branch + one PR per task, per the project's PR workflow — never commit
  directly to `main`.
- Every task's last step updates its design doc's **Status** block with a
  one-line "Phase 1 progress" note (see each task's final step) so future
  readers don't re-flag what's already shipped.
- This plan intentionally does **not** implement: Design 02 Stages 2–3 (H5
  per-room serialization, M4 background loop), Design 03's M3 (invite
  revocation) or the Entra identity Lows (`oid` keying, JWKS refetch
  protection), or any of Design 10 beyond C1. Those are separate future
  phases — do not scope-creep into them.

---

## Task 1: Fix the broken frontend build (C1)

**Files:**
- Modify: `frontend/src/App.tsx:8`
- Modify: `docs/designs/10-frontend-reliability-and-ux.md` (status note)

**Interfaces:**
- Consumes: nothing from other tasks in this plan.
- Produces: nothing consumed by other tasks in this plan.

- [ ] **Step 1: Reproduce the build failure**

Run: `cd frontend && npm run build`
Expected: FAIL with something like
`error TS2304: Cannot find name 'signIn'.` and `error TS2304: Cannot find name 'signOut'.`
pointing at `App.tsx` lines 82 and 94.

- [ ] **Step 2: Fix the import**

`frontend/src/App.tsx` line 8 currently reads:

```ts
import { getActiveAccount, isEntraAuth } from "./auth";
```

Change it to:

```ts
import { getActiveAccount, isEntraAuth, signIn, signOut } from "./auth";
```

(`signIn` and `signOut` are already exported from `frontend/src/auth.ts` —
verified at lines 75 and 80 — so no changes are needed there.)

- [ ] **Step 3: Verify the build passes**

Run: `cd frontend && npm run build`
Expected: PASS — `tsc` reports no errors and `vite build` completes.

- [ ] **Step 4: Commit**

```bash
git checkout -b fix/frontend-build-signin-signout-10
git add frontend/src/App.tsx
git commit -m "fix: import signIn/signOut in App.tsx to unbreak the build (C1)"
```

- [ ] **Step 5: Update the design doc**

In `docs/designs/10-frontend-reliability-and-ux.md`, immediately under the
`**Status:** Proposed` line, add:

```markdown
**Phase 1 progress:** C1 (missing `signIn`/`signOut` imports) shipped in
`fix/frontend-build-signin-signout-10`. Remaining: H7–H9, M10–M14, and the
frontend Lows — not yet started.
```

Commit this as part of the same branch:

```bash
git add docs/designs/10-frontend-reliability-and-ux.md
git commit -m "docs: note Phase 1 (C1) progress in design 10"
```

- [ ] **Step 6: Push and open a PR**

```bash
git push -u origin fix/frontend-build-signin-signout-10
gh pr create --title "fix: unbreak frontend build (C1)" --body "$(cat <<'EOF'
## Summary
- `App.tsx` called `signIn()`/`signOut()` without importing them, breaking
  `tsc --noEmit` (and therefore `npm run build`) with TS2304. Entra sign-in
  was completely dead as a result.
- Adds the missing import. No behavior change beyond making sign-in work.

Addresses C1 from the [2026-07-12 codebase review](../../blob/main/docs/reviews/2026-07-12-codebase-review.md).
See [Design 10](../../blob/main/docs/designs/10-frontend-reliability-and-ux.md).

## Test plan
- [x] `npm run build` passes
EOF
)"
```

---

## Task 2: Fail-closed production configuration (H1, H2, M8)

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/api/deps.py`
- Modify: `backend/tests/conftest.py`
- Create: `backend/tests/test_config_guard.py`
- Modify: `infra/.env.example`
- Modify: `docs/designs/01-fail-closed-production-config.md` (status note)

**Interfaces:**
- Consumes: nothing from other tasks in this plan.
- Produces: nothing consumed by other tasks in this plan. (`Settings` gains
  `env`, `allowed_origins`, `validate_for_environment()`, and `ConfigError` —
  no other task in this plan touches `config.py`.)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_config_guard.py`:

```python
"""Fail-closed production config guard (Design 01 / H1, H2, M8)."""
import pytest

from app.config import ConfigError, Settings


def _prod_settings(**overrides) -> Settings:
    base = dict(
        env="production",
        auth_mode="entra",
        entra_tenant_id="tenant-1",
        entra_client_id="client-1",
        admin_emails="admin@thetaray.com",
        secrets_provider="azure_keyvault",
        allowed_origins="https://app.example.com",
    )
    base.update(overrides)
    return Settings(**base)


def test_prod_settings_fully_configured_validates_clean():
    _prod_settings().validate_for_environment()  # must not raise


def test_dev_settings_default_validates_clean():
    Settings().validate_for_environment()  # dev defaults are all dev-safe


def test_invalid_env_value_raises():
    with pytest.raises(ConfigError, match="CABINET_ENV"):
        Settings(env="production-ish").validate_for_environment()


@pytest.mark.parametrize(
    "overrides,expected_fragment",
    [
        ({"auth_mode": "dev"}, "CABINET_AUTH_MODE"),
        ({"entra_tenant_id": ""}, "CABINET_ENTRA_TENANT_ID"),
        ({"entra_client_id": ""}, "CABINET_ENTRA_CLIENT_ID"),
        ({"admin_emails": ""}, "CABINET_ADMIN_EMAILS"),
        ({"secrets_provider": "env"}, "CABINET_SECRETS_PROVIDER"),
        ({"allowed_origins": "*"}, "CABINET_ALLOWED_ORIGINS"),
    ],
)
def test_prod_missing_required_var_raises(overrides, expected_fragment):
    with pytest.raises(ConfigError, match=expected_fragment):
        _prod_settings(**overrides).validate_for_environment()


def test_prod_allows_env_secrets_with_explicit_escape_hatch(monkeypatch):
    monkeypatch.setenv("CABINET_ALLOW_ENV_SECRETS", "1")
    try:
        _prod_settings(secrets_provider="env").validate_for_environment()  # must not raise
    finally:
        monkeypatch.delenv("CABINET_ALLOW_ENV_SECRETS")


def test_bad_int_env_var_raises_config_error(monkeypatch):
    from app.config import reset_settings_cache

    monkeypatch.setenv("CABINET_CYCLE_LIMIT", "not-a-number")
    reset_settings_cache()
    try:
        with pytest.raises(ConfigError, match="CABINET_CYCLE_LIMIT"):
            Settings()
    finally:
        monkeypatch.delenv("CABINET_CYCLE_LIMIT")
        reset_settings_cache()


def test_negative_int_env_var_raises_config_error(monkeypatch):
    from app.config import reset_settings_cache

    monkeypatch.setenv("CABINET_CYCLE_LIMIT", "-1")
    reset_settings_cache()
    try:
        with pytest.raises(ConfigError, match="CABINET_CYCLE_LIMIT"):
            Settings()
    finally:
        monkeypatch.delenv("CABINET_CYCLE_LIMIT")
        reset_settings_cache()


def test_settings_is_frozen():
    settings = Settings()
    with pytest.raises(Exception):  # dataclasses.FrozenInstanceError
        settings.admin_emails = "hacked@evil.example"
```

Also add, to `backend/tests/test_admin_config.py`, a test for the H2
runtime defense-in-depth (empty allowlist + entra mode ⇒ deny):

```python
def test_admin_denied_when_entra_mode_and_allowlist_empty(entra_client):
    from .conftest import install_mock_entra, make_entra_keypair, make_entra_token

    private_key, jwks = make_entra_keypair()
    install_mock_entra(entra_client.app, jwks)
    token = make_entra_token(private_key)

    resp = entra_client.put(
        "/api/admin/agents/fce",
        json={"system_prompt": "hijacked"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_config_guard.py tests/test_admin_config.py -q`
Expected: FAIL — `ConfigError` doesn't exist yet, `validate_for_environment`
doesn't exist yet, and the entra-mode admin test currently gets `200` instead
of `403`.

- [ ] **Step 3: Add `ConfigError`, `_env_int`, and the new settings fields**

In `backend/app/config.py`, add near the top (after the `_env` helper at
line 39):

```python
class ConfigError(Exception):
    """Raised when configuration is invalid or unsafe for the environment."""


def _env_int(name: str, default: int, *, min_value: int | None = None) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if min_value is not None and value < min_value:
        raise ConfigError(f"{name} must be >= {min_value}, got {value}")
    return value
```

Replace the four raw `int(_env(...))` field definitions with `_env_int`
(same field names, same defaults, now validated):

```python
    agent_max_tokens: int = field(
        default_factory=lambda: _env_int("CABINET_AGENT_MAX_TOKENS", 2048, min_value=1)
    )
    history_window: int = field(
        default_factory=lambda: _env_int("CABINET_HISTORY_WINDOW", 40, min_value=1)
    )
```

```python
    default_cycle_limit: int = field(
        default_factory=lambda: _env_int("CABINET_CYCLE_LIMIT", 6, min_value=1)
    )
```

```python
    invite_ttl_hours: int = field(
        default_factory=lambda: _env_int("CABINET_INVITE_TTL_HOURS", 168, min_value=1)
    )
```

Add two new fields — put `env` right after `app_name` and `allowed_origins`
right after `entra_client_id`:

```python
    # "dev" | "staging" | "production" — gates validate_for_environment().
    env: str = field(default_factory=lambda: _env("CABINET_ENV", "dev"))
```

```python
    # Comma-separated CORS origins. "*" is the dev default; production must
    # set this to the real frontend origin(s) — enforced below.
    allowed_origins: str = field(
        default_factory=lambda: _env("CABINET_ALLOWED_ORIGINS", "*")
    )
```

- [ ] **Step 4: Add `validate_for_environment()` and freeze `Settings`**

Change the class decorator from `@dataclass` to `@dataclass(frozen=True)`.

Add this method as the last member of the `Settings` class:

```python
    def validate_for_environment(self) -> None:
        """Refuse to boot with an unsafe config outside dev.

        Raising here (called once from the FastAPI lifespan, before any
        provider is built) turns "deployed, forgot an env var" into a loud
        crash-loop instead of silent identity impersonation or an open admin
        surface — see H1/H2/M8 in the 2026-07-12 review.
        """
        if self.env not in ("dev", "staging", "production"):
            raise ConfigError(
                f"CABINET_ENV must be one of dev|staging|production, got {self.env!r}"
            )
        if self.env == "dev":
            return
        if self.auth_mode != "entra":
            raise ConfigError(
                "CABINET_AUTH_MODE must be 'entra' when CABINET_ENV is staging/production"
            )
        if not self.entra_tenant_id:
            raise ConfigError(
                "CABINET_ENTRA_TENANT_ID must be set when CABINET_ENV is staging/production"
            )
        if not self.entra_client_id:
            raise ConfigError(
                "CABINET_ENTRA_CLIENT_ID must be set when CABINET_ENV is staging/production"
            )
        if not self.admin_emails:
            raise ConfigError(
                "CABINET_ADMIN_EMAILS must be set when CABINET_ENV is staging/production"
            )
        if self.secrets_provider != "azure_keyvault" and _env("CABINET_ALLOW_ENV_SECRETS") != "1":
            raise ConfigError(
                "CABINET_SECRETS_PROVIDER must be 'azure_keyvault' when CABINET_ENV is "
                "staging/production (set CABINET_ALLOW_ENV_SECRETS=1 to override)"
            )
        if not self.allowed_origins or self.allowed_origins == "*":
            raise ConfigError(
                "CABINET_ALLOWED_ORIGINS must be a non-wildcard value when CABINET_ENV "
                "is staging/production"
            )
```

- [ ] **Step 5: Stop `_load_local_dev_env` from leaking into every process**

Replace the unconditional top-level call at line 35 (`_load_local_dev_env()`)
— delete that line — and change `get_settings()` to call it conditionally,
only on first construction:

```python
@lru_cache(maxsize=1)
def get_settings() -> Settings:
    if os.environ.get("CABINET_SKIP_LOCAL_DOTENV") != "1":
        _load_local_dev_env()
    return Settings()
```

In `backend/tests/conftest.py`, add one line to `_configure_env` (right after
the existing `monkeypatch.setenv` calls) so the test process never picks up a
developer's real `infra/.env`:

```python
    monkeypatch.setenv("CABINET_SKIP_LOCAL_DOTENV", "1")
```

- [ ] **Step 6: Wire the boot guard and config-driven CORS into `main.py`**

In `backend/app/main.py`, add the guard as the first line of `lifespan`:

```python
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    settings.validate_for_environment()
    ...
```

Replace the hard-coded CORS middleware in `create_app()`:

```python
    origins = [o.strip() for o in settings.allowed_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=settings.auth_mode == "entra" and origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
```

(This is behavior-preserving in dev: `allowed_origins` defaults to `"*"`, so
`origins == ["*"]` and `allow_credentials` stays `False`, identical to today.)

- [ ] **Step 7: Fail-close `require_admin` (H2 defense in depth)**

In `backend/app/api/deps.py`, replace `require_admin`:

```python
def require_admin(user_email: str = Depends(get_current_user_email)) -> str:
    """Gate platform-admin surfaces behind CABINET_ADMIN_EMAILS.

    An empty allowlist means open access in "dev" auth mode only — an empty
    allowlist under "entra" auth is refused outright, so a forgotten
    CABINET_ADMIN_EMAILS can never open the admin surface to every verified
    user (defense in depth alongside the boot guard in config.py).
    """
    settings = get_settings()
    allowlist = {
        e.strip().lower() for e in settings.admin_emails.split(",") if e.strip()
    }
    if settings.auth_mode == "entra" and not allowlist:
        raise HTTPException(status_code=403, detail="admin access required")
    if allowlist and user_email.lower() not in allowlist:
        raise HTTPException(status_code=403, detail="admin access required")
    return user_email
```

- [ ] **Step 8: Document the new variables in `infra/.env.example`**

Add a new section right after the file's header comment (before
`## Database`... i.e. before the `CABINET_DATABASE_URL` block):

```
# ── Deployment environment ────────────────────────────────────────────────
# dev | staging | production. Anything other than "dev" enforces a
# fail-closed boot guard: entra auth, a non-empty admin allowlist, Key Vault
# secrets, and non-wildcard CORS all become required (see config.py
# validate_for_environment()). Escape hatch for a non-KV secrets provider in
# staging: CABINET_ALLOW_ENV_SECRETS=1.
CABINET_ENV=dev
CABINET_ALLOW_ENV_SECRETS=
```

And, in the `## Authentication` block (right after `CABINET_ENTRA_CLIENT_ID=`),
add:

```
# Comma-separated CORS origins for the frontend. "*" (dev default) is
# rejected by the boot guard once CABINET_ENV is staging/production.
CABINET_ALLOWED_ORIGINS=*
```

- [ ] **Step 9: Run the full backend test suite**

Run: `cd backend && python -m pytest tests -q`
Expected: PASS — all existing tests plus the new ones in
`test_config_guard.py` and `test_admin_config.py`.

- [ ] **Step 10: Commit**

```bash
git checkout -b fix/config-fail-closed-01
git add backend/app/config.py backend/app/main.py backend/app/api/deps.py \
        backend/tests/conftest.py backend/tests/test_config_guard.py \
        backend/tests/test_admin_config.py infra/.env.example
git commit -m "fix: fail-closed production config guard + CORS + admin gate (H1, H2, M8)"
```

- [ ] **Step 11: Update the design doc**

In `docs/designs/01-fail-closed-production-config.md`, under
`**Status:** Proposed`, add:

```markdown
**Phase 1 progress:** Shipped in full — `CABINET_ENV` boot guard,
config-driven CORS, safe int parsing, frozen `Settings`, contained dotenv
load, and the `require_admin` fail-closed tweak — in
`fix/config-fail-closed-01`. (The optional `pydantic-settings` follow-up
noted in "Rollout & risks" was not done.)
```

```bash
git add docs/designs/01-fail-closed-production-config.md
git commit -m "docs: note Phase 1 progress in design 01"
```

- [ ] **Step 12: Push and open a PR**

```bash
git push -u origin fix/config-fail-closed-01
gh pr create --title "fix: fail-closed production config (H1, H2, M8)" --body "$(cat <<'EOF'
## Summary
- Adds `CABINET_ENV` (dev|staging|production) and a
  `Settings.validate_for_environment()` boot guard, invoked once in the
  FastAPI lifespan: staging/production now refuses to boot unless auth_mode
  is entra, the admin allowlist is non-empty, secrets come from Key Vault
  (or the explicit `CABINET_ALLOW_ENV_SECRETS=1` escape hatch), and CORS is
  a real origin list — never `*`.
- CORS origins are now configuration-driven instead of hard-coded `*`.
- `require_admin` now denies (instead of allowing) when auth_mode is entra
  and the admin allowlist is empty — defense in depth if the boot guard is
  ever bypassed (H2).
- Safe int env parsing (`_env_int`) replaces bare `int(_env(...))` — bad or
  negative values now raise a clear `ConfigError` instead of a cryptic
  `ValueError` or a silently-broken negative cycle limit.
- `Settings` is now frozen; the dev-only `.env` autoload no longer runs at
  import time and no longer leaks into the test process.

Addresses H1, H2, M8, and the config Lows from the
[2026-07-12 codebase review](../../blob/main/docs/reviews/2026-07-12-codebase-review.md).
See [Design 01](../../blob/main/docs/designs/01-fail-closed-production-config.md).

## Test plan
- [x] `pytest tests -q` passes, including new `test_config_guard.py`
- [x] Dev defaults are unchanged (CORS `*`, dev auth, existing tests green)
EOF
)"
```

---

## Task 3: Authorization hardening — room enumeration + admin reads (H3, H4)

**Files:**
- Modify: `backend/app/api/rooms.py`
- Modify: `backend/app/api/admin.py`
- Modify: `backend/tests/test_hardening.py`
- Modify: `docs/designs/03-authorization-and-tenancy-hardening.md` (status note)

**Interfaces:**
- Consumes: nothing from other tasks in this plan.
- Produces: nothing consumed by other tasks in this plan.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_hardening.py`:

```python
# ---------------------------------------------------------------------------
# H3: list_rooms must be scoped to the caller's memberships
# ---------------------------------------------------------------------------
def test_list_rooms_scoped_to_membership(client):
    mine = make_room(client, "MyBank")
    stranger = {"X-User-Email": "stranger@elsewhere.example"}
    client.post(
        "/api/rooms",
        json={"customer_name": "StrangerBank", "enrichment_prompt": None},
        headers=stranger,
    )

    mine_ids = {r["id"] for r in client.get("/api/rooms").json()}
    assert mine_ids == {mine["id"]}

    stranger_ids = {r["id"] for r in client.get("/api/rooms", headers=stranger).json()}
    assert mine["id"] not in stranger_ids
    assert len(stranger_ids) == 1


# ---------------------------------------------------------------------------
# H4: admin READ endpoints must be gated exactly like admin writes
# ---------------------------------------------------------------------------
def test_admin_read_endpoints_denied_for_non_admin(client, monkeypatch):
    from app.config import reset_settings_cache

    monkeypatch.setenv("CABINET_ADMIN_EMAILS", "boss@thetaray.com")
    reset_settings_cache()
    try:
        assert client.get("/api/admin/agents").status_code == 403
        assert client.get("/api/admin/agents/fce").status_code == 403
        assert client.get("/api/admin/agents/fce/skills").status_code == 403

        admin = {"X-User-Email": "boss@thetaray.com"}
        assert client.get("/api/admin/agents", headers=admin).status_code == 200
        assert client.get("/api/admin/agents/fce", headers=admin).status_code == 200
        assert client.get("/api/admin/agents/fce/skills", headers=admin).status_code == 200
    finally:
        monkeypatch.delenv("CABINET_ADMIN_EMAILS")
        reset_settings_cache()
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_hardening.py -q`
Expected: FAIL —
`test_list_rooms_scoped_to_membership` fails because `stranger_ids` contains
2 rooms, not 1; `test_admin_read_endpoints_denied_for_non_admin` fails because
the three GETs return `200` instead of `403`.

- [ ] **Step 3: Scope `list_rooms` to the caller's memberships**

In `backend/app/api/rooms.py`, replace the `list_rooms` handler (it already
imports `get_current_user_email` and `RoomMember`, so no import changes are
needed):

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
    rooms = list(result.scalars().all())
    room_ids = [r.id for r in rooms]
    last_messages = await _last_messages_by_room(session, room_ids)
    member_counts = await _member_counts_by_room(session, room_ids)
    return [
        _room_out(
            room,
            last_message=last_messages.get(room.id),
            member_count=member_counts.get(room.id, 0),
        )
        for room in rooms
    ]
```

- [ ] **Step 4: Gate the three admin read endpoints**

In `backend/app/api/admin.py` (it already imports `require_admin`), add
`_admin: str = Depends(require_admin)` to each read handler's signature:

```python
@router.get("/agents", response_model=list[AgentConfigOut])
async def list_agent_configs(
    session: AsyncSession = Depends(get_session),
    _admin: str = Depends(require_admin),
) -> list[AgentConfigOut]:
```

```python
@router.get("/agents/{agent_key}", response_model=AgentConfigOut)
async def get_agent_config(
    agent_key: str,
    session: AsyncSession = Depends(get_session),
    _admin: str = Depends(require_admin),
) -> AgentConfigOut:
```

```python
@router.get("/agents/{agent_key}/skills", response_model=list[SkillOut])
async def list_global_skills(
    agent_key: str,
    session: AsyncSession = Depends(get_session),
    _admin: str = Depends(require_admin),
) -> list[SkillOut]:
```

(Only the signatures change — leave each function body untouched.)

- [ ] **Step 5: Run the full backend test suite**

Run: `cd backend && python -m pytest tests -q`
Expected: PASS — including the two new tests. (No existing test asserts
`list_rooms` returns rooms the caller doesn't belong to, and no existing test
calls the three admin GETs with a non-empty allowlist configured, so nothing
regresses.)

- [ ] **Step 6: Commit**

```bash
git checkout -b fix/authz-list-rooms-admin-reads-03
git add backend/app/api/rooms.py backend/app/api/admin.py backend/tests/test_hardening.py
git commit -m "fix: scope list_rooms to membership + gate admin reads (H3, H4)"
```

- [ ] **Step 7: Update the design doc**

In `docs/designs/03-authorization-and-tenancy-hardening.md`, under
`**Status:** Proposed`, add:

```markdown
**Phase 1 progress:** H3 (list_rooms scoped to membership) and H4 (admin read
endpoints gated by require_admin) shipped in
`fix/authz-list-rooms-admin-reads-03`. Remaining: M3 (single-use/revocable
invites) and the Entra identity Lows (`oid` keying, JWKS refetch protection) —
not yet started.
```

```bash
git add docs/designs/03-authorization-and-tenancy-hardening.md
git commit -m "docs: note Phase 1 progress in design 03"
```

- [ ] **Step 8: Push and open a PR**

```bash
git push -u origin fix/authz-list-rooms-admin-reads-03
gh pr create --title "fix: room-list scoping + admin read gating (H3, H4)" --body "$(cat <<'EOF'
## Summary
- `GET /api/rooms` now filters to the caller's own memberships instead of
  returning every customer's room (with a message preview) to any
  authenticated caller (H3).
- The three admin *read* endpoints (`list_agent_configs`, `get_agent_config`,
  `list_global_skills`) are now gated by `require_admin`, matching the
  existing gate on the admin *write* endpoints (H4).

Addresses H3, H4 from the
[2026-07-12 codebase review](../../blob/main/docs/reviews/2026-07-12-codebase-review.md).
See [Design 03](../../blob/main/docs/designs/03-authorization-and-tenancy-hardening.md).

## Test plan
- [x] `pytest tests -q` passes, including two new regression tests
EOF
)"
```

---

## Task 4: Orchestrator crash-safety — no more stranded rooms (C2)

**Files:**
- Modify: `backend/app/agents/foundry_client.py`
- Modify: `backend/app/agents/orchestrator.py`
- Modify: `backend/tests/test_llm_backend.py`
- Create: `backend/tests/test_orchestrator_resilience.py`
- Modify: `docs/designs/02-orchestrator-resilience-and-durable-loop.md` (status note)

**Interfaces:**
- Consumes: nothing from other tasks in this plan.
- Produces: `LLMError` (new exception in `foundry_client.py`) and
  `Orchestrator._fail_turn` (new method in `orchestrator.py`) — not consumed
  by any other task in this plan, but future Design 02 Stage 2/3 work will
  build on both.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_llm_backend.py` (needs `import pytest` added to
the top of the file):

```python
def test_azure_openai_complete_wraps_sdk_errors_as_llmerror():
    from app.agents.foundry_client import LLMError

    def failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "boom"}})

    backend = _backend(failing_handler)
    with pytest.raises(LLMError):
        _run(
            backend.complete(
                agent_key="data_expert",
                system_prompt="You are helpful.",
                turns=[ChatTurn(role="user", content="hi")],
            )
        )
```

Create `backend/tests/test_orchestrator_resilience.py`:

```python
"""LLM-failure crash safety in the autonomous loop (Design 02 Stage 1 / C2).

Today, an LLM failure mid-loop 500s the request after the cycle was already
claimed, leaving the room ACTIVE at an exhausted budget forever — no agent
ever speaks again and /resume returns 409. This must instead pause the room
with a visible system notice so /resume works.
"""
from app.agents.foundry_client import LLMResult

from .conftest import make_room


class _FlakyLLM:
    """Succeeds `ok_calls` times, then raises LLMError on every call after."""

    def __init__(self, ok_calls: int) -> None:
        self._ok_calls = ok_calls
        self._calls = 0

    async def complete(self, *, agent_key, system_prompt, turns):
        from app.agents.foundry_client import LLMError

        self._calls += 1
        if self._calls > self._ok_calls:
            raise LLMError("simulated upstream failure")
        return LLMResult(text=f"[{agent_key}] turn {self._calls}", input_tokens=1, output_tokens=1)


def test_llm_failure_mid_loop_pauses_room_and_allows_resume(client):
    room = make_room(client, "FlakyBank")
    client.app.state.orchestrator._llm = _FlakyLLM(ok_calls=2)

    resp = client.post(
        f"/api/rooms/{room['id']}/messages",
        json={"content": "Please plan the full onboarding together."},
    )
    assert resp.status_code == 200
    body = resp.json()

    kinds = [m["sender_type"] for m in body["messages"]]
    assert kinds.count("agent") == 2, "two clean turns before the simulated failure"
    assert kinds.count("system") == 1, "a system notice must record the failure"
    assert body["room_status"] == "paused_awaiting_human"

    # The core bug: today this 409s because nothing ever paused the room.
    resume = client.get(f"/api/rooms/{room['id']}").json()
    assert resume["status"] == "paused_awaiting_human"
    resume_resp = client.post(f"/api/rooms/{room['id']}/resume")
    assert resume_resp.status_code == 200


def test_llm_failure_on_first_turn_still_pauses_and_resumes(client):
    room = make_room(client, "InstaFlakyBank")
    client.app.state.orchestrator._llm = _FlakyLLM(ok_calls=0)

    resp = client.post(f"/api/rooms/{room['id']}/messages", json={"content": "go"})
    assert resp.status_code == 200
    body = resp.json()
    assert [m["sender_type"] for m in body["messages"]] == ["human", "system"]
    assert body["room_status"] == "paused_awaiting_human"
    assert client.post(f"/api/rooms/{room['id']}/resume").status_code == 200
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_llm_backend.py tests/test_orchestrator_resilience.py -q`
Expected: FAIL —
`LLMError` doesn't exist yet (import error), and even once it's added, the
orchestrator has no try/except so a raised `LLMError` propagates as an
unhandled 500 instead of a 200 with a paused room.

- [ ] **Step 3: Add `LLMError` and wrap both SDK calls**

In `backend/app/agents/foundry_client.py`, add near the top (after the
`ChatTurn`/`LLMResult` dataclasses, before `class LLMBackend`):

```python
class LLMError(Exception):
    """The LLM backend failed to produce a completion (timeout, API error,
    refusal-that-isn't-handled-server-side, etc.). Callers must treat this as
    recoverable: pause the room, never leave it stranded active."""
```

In `FoundryLLM.complete`, wrap the network call:

```python
    async def complete(
        self, *, agent_key: str, system_prompt: str, turns: list[ChatTurn]
    ) -> LLMResult:
        try:
            response = await self._client.messages.create(
                model=self._settings.foundry_model,
                max_tokens=self._settings.agent_max_tokens,
                system=system_prompt,
                messages=[{"role": t.role, "content": t.content} for t in turns],
            )
        except Exception as exc:
            raise LLMError(f"Foundry completion failed for {agent_key}: {exc}") from exc
        input_tokens = getattr(response.usage, "input_tokens", 0) or 0
```

(Everything from `input_tokens = ...` onward is unchanged — only the call
itself moves inside the new `try` block.)

In `AzureOpenAILLM.complete`, wrap the network call the same way:

```python
    async def complete(
        self, *, agent_key: str, system_prompt: str, turns: list[ChatTurn]
    ) -> LLMResult:
        messages = [{"role": "system", "content": system_prompt}] + [
            {"role": t.role, "content": t.content} for t in turns
        ]
        try:
            response = await self._client.chat.completions.create(
                model=self._settings.azure_openai_deployment,
                max_completion_tokens=self._settings.agent_max_tokens,
                messages=messages,
            )
        except Exception as exc:
            raise LLMError(f"Azure OpenAI completion failed for {agent_key}: {exc}") from exc
        choice = response.choices[0]
```

(Everything from `choice = response.choices[0]` onward is unchanged.)

- [ ] **Step 4: Catch `LLMError` in the autonomous loop and add `_fail_turn`**

In `backend/app/agents/orchestrator.py`, add imports at the top:

```python
import logging

logger = logging.getLogger(__name__)
```

and change the `from .foundry_client import ChatTurn, LLMBackend` line to:

```python
from .foundry_client import ChatTurn, LLMBackend, LLMError
```

In `run_autonomous_loop`, wrap the `await self._llm.complete(...)` call:

```python
            await self._broker.publish(
                room.id, {"type": "agent_thinking", "agent_key": speaker}
            )
            try:
                result = await self._llm.complete(
                    agent_key=speaker, system_prompt=system_prompt, turns=turns
                )
            except LLMError as exc:
                fail_msg = await self._fail_turn(session, room, speaker, exc)
                created.append(fail_msg)
                break
```

(This replaces the current unguarded `result = await self._llm.complete(...)`
call; everything after it in the success path — building `msg`, `session.add`,
etc. — is unchanged, just now inside the loop body that only runs when the
`try` succeeds.)

Add `_fail_turn` as a new method on `Orchestrator`, right after
`run_autonomous_loop`:

```python
    async def _fail_turn(
        self, session: AsyncSession, room: Room, agent_key: str, exc: Exception
    ) -> Message:
        """An LLM call failed mid-loop.

        The cycle was already claimed before the call, so without this the
        room is stranded ACTIVE at an exhausted budget — no agent can ever
        speak again and /resume 409s (Design 02 / C2). Leave a visible system
        notice, pause the room so /resume works, and tell clients the
        pending typing indicator is done.
        """
        logger.warning(
            "LLM completion failed for %s in room %s: %s", agent_key, room.id, exc
        )
        msg = Message(
            room_id=room.id,
            sender_type="system",
            sender_name="System",
            content=(
                f"⚠️ {DISPLAY_NAMES[agent_key]} could not respond (upstream error). "
                "The room is paused — resume to retry."
            ),
        )
        session.add(msg)
        await session.execute(
            update(Room)
            .where(Room.id == room.id, Room.status == ACTIVE)
            .values(status=PAUSED)
        )
        await session.commit()
        await self._broker.publish(room.id, self._msg_event(msg))
        await self._broker.publish(
            room.id,
            {"type": "agent_error", "agent_key": agent_key, "recoverable": True},
        )
        return msg
```

- [ ] **Step 5: Run the full backend test suite**

Run: `cd backend && python -m pytest tests -q`
Expected: PASS — all existing tests plus the new ones in
`test_llm_backend.py` and `test_orchestrator_resilience.py`.

- [ ] **Step 6: Commit**

```bash
git checkout -b fix/orchestrator-crash-safety-02
git add backend/app/agents/foundry_client.py backend/app/agents/orchestrator.py \
        backend/tests/test_llm_backend.py backend/tests/test_orchestrator_resilience.py
git commit -m "fix: pause room with a terminal event on LLM failure mid-loop (C2)"
```

- [ ] **Step 7: Update the design doc**

In `docs/designs/02-orchestrator-resilience-and-durable-loop.md`, under
`**Status:** Proposed`, add:

```markdown
**Phase 1 progress:** Stage 1 (C2 — try/except around the LLM call, a system
notice, pausing the room, and a new `agent_error` terminal event) shipped in
`fix/orchestrator-crash-safety-02`. The Stage 1 timeout/bounded-retry
sub-item and the handoff-sentinel Low were deliberately deferred to keep this
slice minimal. Remaining: Stage 2 (H5 per-room serialization) and Stage 3
(M4 — move the loop off the request path) — not yet started.
```

```bash
git add docs/designs/02-orchestrator-resilience-and-durable-loop.md
git commit -m "docs: note Phase 1 (Stage 1) progress in design 02"
```

- [ ] **Step 8: Push and open a PR**

```bash
git push -u origin fix/orchestrator-crash-safety-02
gh pr create --title "fix: orchestrator crash safety on LLM failure (C2)" --body "$(cat <<'EOF'
## Summary
- `run_autonomous_loop` had no error handling around the LLM call: a
  timeout/failure mid-loop 500'd the request after the cycle was already
  claimed, leaving the room ACTIVE at an exhausted budget forever — no agent
  could ever speak again and `/resume` returned 409 (C2).
- Adds `LLMError`, raised by both `FoundryLLM.complete` and
  `AzureOpenAILLM.complete` on any SDK exception.
- The orchestrator now catches `LLMError` per turn, persists a visible
  system notice, pauses the room, and broadcasts a new `agent_error` event
  so a client's typing indicator resolves instead of hanging forever.
  `/resume` now works after a failure.

This is Stage 1 only of [Design 02](../../blob/main/docs/designs/02-orchestrator-resilience-and-durable-loop.md)
(per-call timeout/retry, H5 per-room serialization, and M4's background loop
are separate, larger follow-ups — not in this PR). Addresses C2 from the
[2026-07-12 codebase review](../../blob/main/docs/reviews/2026-07-12-codebase-review.md).

## Test plan
- [x] `pytest tests -q` passes, including two new orchestrator-resilience tests
      and one new LLMError-wrapping test
EOF
)"
```

---

## Self-Review Notes

- **Spec coverage:** All four findings named in the review's "Stop the
  bleeding" sequencing (C1, C2, H1, H2, H3, H4) are covered; M8 is covered as
  part of Design 01 (it shares the same design doc and file). M4 is
  explicitly *not* fully fixed — only the crash-safety slice that also
  happens to close the "stranded room" symptom it shares with C2 — per the
  Global Constraints note.
- **No overlapping files:** verified against the current repo — Task 2
  touches `config.py`/`main.py`/`deps.py`; Task 3 touches `rooms.py`/`admin.py`;
  Task 4 touches `foundry_client.py`/`orchestrator.py`; Task 1 touches only
  `App.tsx`. Each task's test file additions are also disjoint. Safe to run
  all four in parallel worktrees.
- **Type/name consistency:** `LLMError` (Task 4) and `ConfigError` (Task 2)
  are each used consistently across their task's steps. `_fail_turn`'s
  return type (`Message`) matches how Task 4's Step 4 appends it to
  `created`.
