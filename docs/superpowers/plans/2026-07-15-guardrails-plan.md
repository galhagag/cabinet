# Guardrails Hardening Plan

> **For agentic workers:** Keep this plan focused on the still-open guardrails
> from [2026-07-15-guardrails-review.md](../../reviews/2026-07-15-guardrails-review.md).
> Do not reopen already-fixed work around room authz, orchestrator locking, or
> prompt framing unless a new failing test proves a regression.

**Goal:** Close the remaining guardrail gaps at the platform edges: secret
hygiene, abuse controls, CI/release gates, deployment parity, and production
validation.

**References:**

- [08-secrets-and-oauth-key-management.md](../../designs/08-secrets-and-oauth-key-management.md)
- [07-rate-limiting-and-abuse-controls.md](../../designs/07-rate-limiting-and-abuse-controls.md)
- [09-ci-quality-gates-and-supply-chain.md](../../designs/09-ci-quality-gates-and-supply-chain.md)
- [04-realtime-fanout-and-webpubsub.md](../../designs/04-realtime-fanout-and-webpubsub.md)
- [05-persistence-migrations-and-schema-integrity.md](../../designs/05-persistence-migrations-and-schema-integrity.md)

## Global Constraints

- Every phase must end with an executable validation step, not only a docs
  update.
- Prefer small PRs with one guardrail theme each.
- Do not ship a production-facing phase without backend tests and a frontend
  build pass in the same branch.
- Any phase that touches deployment or secrets must update the runbook under
  [infra/azure/README.md](../../../infra/azure/README.md).

## Phase 0: Secret Hygiene And Deployment Baseline

**Outcome:** No live or shared secret depends on a local developer `.env` file,
and production boot requirements are documented and verifiable.

- [ ] Rotate every non-dev secret currently present in the local
  [infra/.env](../../../infra/.env).
- [ ] Remove live credential usage from local shared setup; keep only safe dev
  toggles in local env files and move deploy-time secrets to Key Vault.
- [ ] Document the required non-dev boot contract in
  [infra/azure/README.md](../../../infra/azure/README.md): `CABINET_ENV`, Entra
  settings, admin allowlist, Key Vault secrets, and allowed origins.
- [ ] Add an explicit deployment verification step that fails if
  `CABINET_ENV`, `CABINET_AUTH_MODE`, or required Key Vault secrets are absent.

**Acceptance:** A fresh non-dev deployment can boot only from explicit env plus
Key Vault; no live secret value needs to be copied from a developer machine.

## Phase 1: Ingress Abuse Controls

**Outcome:** Upload and messaging paths have bounded memory, bounded cost, and
predictable error behavior.

- [x] Add upload size caps to
  [backend/app/api/skills.py](../../../backend/app/api/skills.py) before reading
  the full body.
- [x] Harden
  [backend/app/services/skills.py](../../../backend/app/services/skills.py) with
  zip member size limits, compression-ratio checks, and a small allowed-file
  surface for `SKILL.md` bundles.
- [x] Implement app-level rate limiting per
  [07-rate-limiting-and-abuse-controls.md](../../designs/07-rate-limiting-and-abuse-controls.md)
  for at least message post, room resume, invite create, and skill upload.
- [x] Return clear `413` and `429` responses and surface retry guidance in the
  frontend where relevant.
- [x] Add focused backend tests for oversized markdown, zip-bomb rejection, and
  per-route rate-limit behavior.

**Acceptance:** Oversized or abusive uploads fail before decompression, bursty
message abuse returns `429`, and the new protections are covered by tests.

## Phase 2: Delivery Gates And Reproducibility

**Outcome:** Guardrails are enforced before merge, and builds are repeatable.

- [x] Add a GitHub Actions pipeline under `.github/workflows` that runs backend
  tests, frontend build/typecheck, and both Docker builds.
- [x] Add a migration check to CI so schema drift cannot merge silently.
- [x] Pin backend dependencies using `uv` or `pip-tools`, keeping ranges in a
  source file and a fully locked install file in the repo.
- [x] Switch the frontend image build to deterministic install behavior and make
  lockfile absence fail loudly.
- [ ] Add branch protection and required status checks once the first green CI
  baseline exists.

**Acceptance:** A PR that breaks pytest, the frontend build, or Docker images
fails automatically before merge; repeat installs produce the same dependency
set.

## Phase 3: Dev/Prod Parity

**Outcome:** Local validation matches the runtime shape closely enough to trust
it for release prep.

- [ ] Expand
  [infra/docker-compose.yml](../../../infra/docker-compose.yml) so the full auth,
  secrets, and provider env surface is available to the backend container.
- [ ] Add restart policies and healthchecks for the backend and frontend
  containers.
- [ ] Reconcile
  [README.md](../../../README.md) with
  [frontend/vite.config.ts](../../../frontend/vite.config.ts) so documented
  ports and proxies match the actual dev setup.
- [ ] Update
  [docs/ARCHITECTURE.md](../../../docs/ARCHITECTURE.md) where runtime modes or
  deploy assumptions have drifted from the code.
- [ ] Ensure every non-dev deployment manifest sets `CABINET_ENV` explicitly.

**Acceptance:** `docker compose` boots a representative stack without silent env
drift, and the docs match the actual developer entrypoints.

## Phase 4: Production Proof And Coverage Closure

**Outcome:** The remaining guardrails are not only implemented but exercised
against the real production topology.

- [ ] Validate the Azure Web PubSub negotiate/connect/fanout flow in staging,
  using the token endpoint in
  [backend/app/api/rooms.py](../../../backend/app/api/rooms.py) and the Azure
  broker path in [backend/app/services/realtime.py](../../../backend/app/services/realtime.py).
- [ ] Add explicit Entra-mode WebSocket auth tests.
- [ ] Add an audit-log completeness test sweep across all mutating endpoints.
- [ ] Add a small release checklist proving CI green, migrations current,
  realtime staging verified, and secrets sourced from Key Vault.

**Acceptance:** Realtime is proven outside the in-process broker, auth-critical
WebSocket paths are tested, and audit expectations are enforced in CI.

## Suggested PR Breakdown

- PR 1: Secret hygiene docs and deployment contract
- PR 2: Upload caps and zip-bomb defense
- PR 3: Rate limiting and related tests
- PR 4: CI, lockfiles, and branch protection
- PR 5: Compose/docs parity cleanup
- PR 6: Azure realtime staging proof and remaining guardrail tests

## Stop Conditions

Pause and reassess if any phase uncovers a regression in the already-closed
authz, orchestrator, or prompt-compilation work. Those would be new defects and
should be handled before continuing with the rest of this plan.