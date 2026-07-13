# Design 09 — CI, Quality Gates & Supply Chain

**Status:** Proposed
**Addresses:** H11 (no CI), H12 (unpinned backend deps, no lockfile), M18
(docker-compose env allowlist drift), M19 (README/port drift), M20 (test
coverage gaps), and infra Lows (no restart/healthcheck, build-context junk,
`npm install` vs `npm ci`, blanket deprecation ignore, doc drift).
**Effort:** M (~1 sprint)

---

## Problem

The repo uses a PR workflow (PRs #2–#8) but nothing verifies a PR. There is no
`.github/`, no pipeline, no lint/format/type tooling beyond `tsc` inside the
frontend build, and backend dependencies are open-ended ranges with no lockfile
([requirements.txt:2](../../backend/requirements.txt#L2)). Result: a branch that
breaks the loop-budget invariant, auth, or the build can merge green; two
`docker build`s a week apart aren't the same application. Compounding drift:

- `docker-compose.yml` api env allowlist predates the Azure OpenAI backend and
  omits `CABINET_AZURE_OPENAI_*`, `CABINET_SECRET_*`, `CABINET_AUTH_MODE`,
  `CABINET_ENTRA_*`, `CABINET_ADMIN_EMAILS`
  ([docker-compose.yml:24](../../infra/docker-compose.yml#L24)) — so the
  "prod-parity" stack boots the team's current `azure_openai` mode with no
  endpoint or key.
- README says :8000/:5173; vite serves :5180 → :8010
  ([README.md:40](../../README.md#L40)).
- No orchestrator-failure, Entra-WS-auth, or audit-trail tests
  ([backend/tests](../../backend/tests)).
- No `restart:`/healthcheck on compose; `.worktrees/` and root `*.png` ship in
  the build context; `Dockerfile.frontend` uses `npm install`;
  `ignore::DeprecationWarning` is blanket.

## Goals

- Every PR runs the full backend suite, frontend typecheck/build, lint/format,
  and both Docker builds — all in mock mode, no cloud credentials.
- Dependencies are pinned and reproducible; upgrades are deliberate.
- The compose stack and docs match the code.
- The test suite covers the failure paths that matter (agent loop errors, Entra
  WS auth, audit trail).

## Design

### 1. GitHub Actions CI

`.github/workflows/ci.yml`, triggered on PR + push to `main`:

- **backend:** matrix on Python 3.12/3.13; install pinned deps; `ruff check`,
  `ruff format --check`, `mypy app`, `pytest -q` (mock mode — the harness was
  built for exactly this, no secrets needed); upload coverage.
- **frontend:** `npm ci`; `tsc --noEmit` (this alone catches C1 from the
  review); `eslint`, `prettier --check`; `npm run build`.
- **docker:** build both images (no push) to catch Dockerfile/context breakage.
- **migrations:** `alembic upgrade head` + autogenerate-diff-is-empty check
  (from [Design 05](05-persistence-migrations-and-schema-integrity.md)).
- Branch protection on `main`: require the workflow green before merge.

### 2. Dependency pinning & supply chain

- **Backend:** adopt `uv` (or `pip-tools`) — keep `requirements.in` with ranges,
  compile a fully-pinned, hashed `requirements.txt`; CI and Docker install the
  locked file. `docker build` becomes reproducible.
- **Frontend:** `Dockerfile.frontend` uses `npm ci` (requires the committed
  lockfile; drop the `package-lock.json*` wildcard so a missing lock fails
  loudly).
- **Automation:** Dependabot (or Renovate) for both ecosystems + GitHub Actions,
  grouped minor/patch, so upgrades are reviewed PRs, not silent `docker build`
  drift.
- **Scanning:** `pip-audit` / `npm audit` (non-blocking initially) and CodeQL in
  CI.

### 3. Tooling

- **Backend:** `ruff` (lint + format), `mypy` (start non-strict, tighten).
  Replace the blanket `pytest.ini` `ignore::DeprecationWarning` with targeted
  ignores so real deprecations surface; add `pytest-cov` with a floor.
- **Frontend:** `eslint` (typescript-eslint, react-hooks) + `prettier`.
- **Pre-commit:** a `.pre-commit-config.yaml` running ruff/prettier/eslint and a
  secret-scanner (`gitleaks`) so `infra/.env`-style content can't be committed.

### 4. Fix compose & docs drift

- **compose:** pass the full env surface through (or `env_file: infra/.env`
  wholesale) so every configured mode actually boots; add `restart: unless-stopped`
  and healthchecks (backend `/api/health` exists at
  [main.py:76](../../backend/app/main.py#L76); add a frontend nginx check) and a
  `depends_on: condition: service_healthy` gate.
- **Dockerfiles:** add `HEALTHCHECK`; confirm non-root (backend already is);
  multi-stage frontend build serving static assets via nginx.
- **build context:** add `.worktrees/`, `.playwright-mcp/`, `*.png`, `*.db`,
  `.venv/`, `__pycache__/` to `.dockerignore` and (`.worktrees/`, `*.png`) to
  `.gitignore` so a stray `git add .` can't commit them.
- **docs:** reconcile README ports with `vite.config.ts`; update
  `ARCHITECTURE.md` §2.2/§5/§8 to document the `azure_openai` LLM backend and
  the actual 13-module test suite.

### 5. Close test gaps (M20)

Add, alongside the design-specific tests referenced elsewhere:

- Orchestrator failure path (mid-loop LLM error) — see
  [Design 02](02-orchestrator-resilience-and-durable-loop.md).
- Entra-mode WebSocket `?access_token=` handshake (valid/expired/wrong-audience)
  — see [Design 04](04-realtime-fanout-and-webpubsub.md).
- Audit-trail assertion: every mutating endpoint writes the expected
  `audit_log` row (validates §9's claim) — see
  [Design 05](05-persistence-migrations-and-schema-integrity.md).

## Implementation sketch

- `.github/workflows/ci.yml`, `.github/dependabot.yml`, `codeql.yml`.
- `backend/requirements.in` + locked `requirements.txt`; `ruff.toml`,
  `mypy.ini`; `pytest.ini` targeted warnings + cov.
- `frontend/.eslintrc`, `.prettierrc`; `Dockerfile.frontend` `npm ci`.
- `.pre-commit-config.yaml`; `.dockerignore`/`.gitignore` additions.
- `infra/docker-compose.yml` env + healthchecks + restart.
- Doc edits: `README.md`, `docs/ARCHITECTURE.md`.

## Testing

- CI is self-verifying: open a throwaway PR that (a) breaks `tsc`, (b) breaks a
  pytest, (c) adds an un-migrated model column — confirm each fails the
  corresponding job.
- `docker compose up` with the current `infra/.env` reaches a healthy backend
  and a frontend that loads (guards M18).

## Rollout & risks

- Land the pinned lockfile first (deterministic builds), then CI, then branch
  protection (so the first green baseline exists before enforcement).
- **Risk:** enabling `mypy`/`ruff` on an unlinted codebase produces a wall of
  findings; start with a lenient config and a baseline, tighten over time.
- Dependabot noise: group and schedule weekly.
