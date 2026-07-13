# Design 01 — Fail-Closed Production Configuration

**Status:** Proposed
**Addresses:** H1 (auth fails open), H2 (admin gate open when allowlist empty),
M8 (hard-coded wildcard CORS), config Lows (unvalidated int parsing, mutable
cached `Settings`, import-time `os.environ` mutation).
**Effort:** S (≤2 days)

---

## Problem

Every provider switch defaults to its *safe-for-laptops* value, and nothing
asserts that a real deployment moved off those defaults:

- `auth_mode` defaults to `"dev"`, which trusts a client-supplied
  `X-User-Email` header verbatim ([deps.py:42](../../backend/app/api/deps.py#L42)).
- `admin_emails` defaults to `""`; `require_admin` does
  `if allowlist and user_email.lower() not in allowlist`, so an empty allowlist
  authorizes everyone ([deps.py:95](../../backend/app/api/deps.py#L95)).
- CORS is hard-coded `allow_origins=["*"]` with no env switch
  ([main.py:61](../../backend/app/main.py#L61)).

The tracked `infra/.env` used by `docker-compose` sets none of the tightening
vars, so the most common production mistake — "deployed, forgot an env var" — is
also the most dangerous state: full identity impersonation + open admin.

Secondary robustness gaps in the same file: `int(_env(...))` raises an
uninformative `ValueError` on a malformed value and accepts negatives (a
negative `cycle_limit` permanently pauses all rooms); `Settings` is a mutable
dataclass cached in `lru_cache` and also parked on `app.state`, so any code can
mutate global config at runtime; `_load_local_dev_env()` mutates `os.environ` at
import time, leaking dev vars into the test process.

## Goals

- A deployment that is not explicitly `dev` **refuses to boot** unless the
  security-critical settings are coherent.
- CORS origins are configuration-driven.
- Misconfiguration surfaces as a single, clear, fail-fast error naming the
  offending variable — never as silent insecurity.

## Non-goals

- Replacing the auth mechanism itself (see [03](03-authorization-and-tenancy-hardening.md)
  for identity-claim hardening).
- Introducing a settings framework migration (pydantic-settings) — optional
  follow-up, noted below.

## Design

### 1. An explicit deployment environment

Add `CABINET_ENV` with values `dev | staging | production` (default `dev`).
Introduce a `Settings.validate_for_environment()` invoked once in `lifespan`
*before* any provider is built. In `staging`/`production` it enforces:

| Rule | Rationale |
|------|-----------|
| `auth_mode == "entra"` | No trusted-header identity outside dev. |
| `entra_tenant_id` and `entra_client_id` non-empty | Entra mode is unusable without them. |
| `admin_emails` non-empty | Empty allowlist = open admin (H2). |
| `secrets_provider == "azure_keyvault"` (or an explicit `CABINET_ALLOW_ENV_SECRETS=1` escape hatch) | Env secrets in prod → ephemeral crypto (see [08](08-secrets-and-oauth-key-management.md)). |
| `allowed_origins` set and not `*` | No wildcard CORS in prod. |
| `cycle_limit > 0`, `agent_max_tokens > 0`, `history_window > 0` | Reject nonsensical loop config. |

Any failure raises `ConfigError` with a message like
`CABINET_ADMIN_EMAILS must be set when CABINET_ENV=production`. FastAPI's
lifespan propagates it and the container exits non-zero — a crash-loop is the
correct, loud failure mode.

### 2. Config-driven CORS

```python
# config.py
allowed_origins: str = field(
    default_factory=lambda: _env("CABINET_ALLOWED_ORIGINS", "*")
)
```

```python
# main.py — create_app()
origins = [o.strip() for o in settings.allowed_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=settings.auth_mode == "entra" and origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
```

The boot guard forbids `*` in prod, so the invalid `origins=["*"]` +
credentials combination can never arise.

### 3. Safe int parsing + immutable settings

Add a helper that fails fast with the variable name and rejects out-of-range:

```python
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

Mark the dataclass `@dataclass(frozen=True)`. Remove the redundant
`app.state.settings` mutation path (routers already read via `get_settings()`).

### 4. Contain the import-time env load

Move `_load_local_dev_env()` out of module import and into `get_settings()`
(still cached, so it runs once), or gate it behind `CABINET_ENV=dev`. The test
`conftest.py` should call `load_dotenv` never, and instead set a known-clean
env, so a developer's `infra/.env` can't change test behavior.

## Implementation sketch

- `backend/app/config.py`: add `CABINET_ENV`, `allowed_origins`,
  `_env_int`, `ConfigError`, `validate_for_environment()`; `frozen=True`.
- `backend/app/main.py`: call `settings.validate_for_environment()` at the top
  of `lifespan`; read CORS from settings.
- `backend/app/api/deps.py`: `require_admin` should treat an empty allowlist as
  *deny* whenever `auth_mode == "entra"` (defense in depth even if the boot
  guard is bypassed).
- `infra/.env.example`: document `CABINET_ENV`, `CABINET_ALLOWED_ORIGINS`, and
  a "production checklist" comment block.

## Testing

- `test_config_guard.py`: parametrized — for each required prod var, unset it,
  assert `validate_for_environment()` raises `ConfigError` naming that var; a
  fully-configured prod settings validates clean.
- `test_admin_config.py`: add a case asserting that in `entra` mode with empty
  allowlist, `require_admin` returns 403 (not 200).
- CORS: assert the middleware is configured with the parsed origin list, not
  `*`, when `CABINET_ALLOWED_ORIGINS` is set.

## Rollout & risks

- **Risk:** the guard could block a legitimate staging box that intentionally
  runs env secrets. Mitigated by the explicit `CABINET_ALLOW_ENV_SECRETS=1`
  escape hatch (logged loudly at boot).
- **Migration:** existing dev flows are unaffected (`CABINET_ENV` defaults to
  `dev`). The first prod deploy after this lands must set the new vars — call
  this out in the [Azure go-live checklist](../../infra/azure/README.md).
- **Optional follow-up:** migrate `Settings` to `pydantic-settings` for typed
  validation and `.env` layering, which subsumes `_env_int` and the frozen
  concern.
