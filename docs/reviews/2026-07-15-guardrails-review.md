# 2026-07-15 Guardrails Review

**Date:** 2026-07-15
**Scope:** Current backend, frontend, infra, docs, and test surfaces related to
security, abuse prevention, deployment safety, and operational guardrails.
**Method:** Re-verified the current source after the 2026-07-12 review so this
document only calls out findings that are still open or still need external
validation.

This review is intentionally narrower than the 2026-07-12 all-open findings
register in [2026-07-12-codebase-review.md](2026-07-12-codebase-review.md): the
main question here is whether the app now has the right guardrails, and where
the remaining gaps still are.

## Current Posture

Several of the July 12 high-severity issues are now closed in code:

- Non-dev boot is now fail-closed via
  [backend/app/config.py](../../backend/app/config.py#L213-L255).
- Admin and room access are now scoped through the current authz layer in
  [backend/app/api/deps.py](../../backend/app/api/deps.py) and
  [backend/app/api/rooms.py](../../backend/app/api/rooms.py).
- The orchestrator now serializes per-room execution and handles LLM failures
  inside [backend/app/agents/orchestrator.py](../../backend/app/agents/orchestrator.py).
- Prompt compilation and transcript framing now treat untrusted content as data
  in [backend/app/agents/prompt_compiler.py](../../backend/app/agents/prompt_compiler.py)
  and [backend/app/agents/orchestrator.py](../../backend/app/agents/orchestrator.py).
- Room message and enrichment payloads now have explicit length bounds in
  [backend/app/schemas.py](../../backend/app/schemas.py).
- Database migrations now exist under
  [backend/alembic](../../backend/alembic).

The remaining guardrail work is now concentrated at the platform edges:
operational secrets hygiene, abuse controls, CI/release gates, deployment
parity, and production validation.

## Open Findings

### Critical

- **Operational secret hygiene is still weak.** A local
  [infra/.env](../../infra/.env) remains part of the working setup. Even though
  the file is ignored by git, it is still an on-disk secret carrier and should
  not be used for live or shared credentials. Treat any non-dev secret values
  currently stored there as exposed, rotate them, and move them behind Key
  Vault before the next deployment. Related design:
  [08-secrets-and-oauth-key-management.md](../designs/08-secrets-and-oauth-key-management.md).

### High

- **No CI gate exists.** The repository still has no `.github/workflows`
  directory, so nothing enforces backend tests, frontend build, migration
  checks, or Docker builds before merge. This is the largest remaining process
  gap. Related design:
  [09-ci-quality-gates-and-supply-chain.md](../designs/09-ci-quality-gates-and-supply-chain.md).

- **Backend builds are not reproducible.**
  [backend/requirements.txt](../../backend/requirements.txt#L2-L20) still uses
  open-ended version ranges, which means each fresh install or Docker build can
  resolve a different dependency graph. Related design:
  [09-ci-quality-gates-and-supply-chain.md](../designs/09-ci-quality-gates-and-supply-chain.md).

- **Skill upload ingestion is still unbounded.**
  [backend/app/api/skills.py](../../backend/app/api/skills.py#L49) reads the
  full upload into memory, and
  [backend/app/services/skills.py](../../backend/app/services/skills.py#L33-L48)
  extracts `SKILL.md` from zip bundles without size caps or compression-ratio
  checks. That leaves a straightforward memory and zip-bomb DoS path. Related
  design:
  [06-prompt-injection-and-untrusted-content.md](../designs/06-prompt-injection-and-untrusted-content.md).

- **No app-level rate limiting exists.** There is no rate-limit service or
  route dependency under [backend/app](../../backend/app), and searches for
  rate-limit handling return no implementation. Expensive endpoints such as room
  messaging, invites, and skill uploads are still unthrottled. Related design:
  [07-rate-limiting-and-abuse-controls.md](../designs/07-rate-limiting-and-abuse-controls.md).

- **Local/prod parity still drifts.**
  [infra/docker-compose.yml](../../infra/docker-compose.yml#L26-L31) forwards
  only a subset of the runtime env surface, while
  [frontend/vite.config.ts](../../frontend/vite.config.ts#L7-L14) and
  [README.md](../../README.md#L40-L45) still disagree on local ports and proxy
  targets. This keeps local validation further away from the actual deployed
  shape. Related design:
  [09-ci-quality-gates-and-supply-chain.md](../designs/09-ci-quality-gates-and-supply-chain.md).

- **The Azure realtime path is implemented but not yet proven.**
  [backend/app/api/rooms.py](../../backend/app/api/rooms.py#L488-L494) now
  exposes a broker token endpoint and
  [backend/app/services/realtime.py](../../backend/app/services/realtime.py#L147-L149)
  mints Azure Web PubSub client access, but there is no staging evidence in the
  repo showing a full negotiate-connect-fanout cycle against Azure. Treat this
  as a production-readiness gap until it is exercised outside the in-process
  broker. Related design:
  [04-realtime-fanout-and-webpubsub.md](../designs/04-realtime-fanout-and-webpubsub.md).

### Medium

- **Non-dev safety still depends on explicit environment classification.**
  [backend/app/config.py](../../backend/app/config.py#L64) defaults
  `CABINET_ENV` to `dev`, and the non-dev safety checks live in
  [backend/app/config.py](../../backend/app/config.py#L213-L255). The code is
  correct, but deployment automation now has to carry part of the safety model:
  every non-dev entrypoint must set `CABINET_ENV` explicitly.

- **Guardrail coverage is thinner than the implementation surface.** The test
  suite under [backend/tests](../../backend/tests) covers broker token minting
  and orchestrator resilience, but it still does not contain an explicit
  Entra-mode WebSocket handshake test or an audit-log completeness sweep across
  mutating endpoints. Related designs:
  [04-realtime-fanout-and-webpubsub.md](../designs/04-realtime-fanout-and-webpubsub.md),
  [05-persistence-migrations-and-schema-integrity.md](../designs/05-persistence-migrations-and-schema-integrity.md),
  [09-ci-quality-gates-and-supply-chain.md](../designs/09-ci-quality-gates-and-supply-chain.md).

## Changes Since 2026-07-12

The most important difference from the July 12 review is that the app's core
control plane is materially better now. The remaining risks are no longer the
original fail-open authz and orchestrator correctness issues; they are mostly
about making the current implementation harder to misuse, easier to validate,
and safer to operate.

That means the next guardrails phase should not start with another broad auth or
orchestrator rewrite. It should focus on ingress controls, delivery gates,
deployment parity, and production proof.

## Recommended Next Artifact

Use [2026-07-15-guardrails-plan.md](../superpowers/plans/2026-07-15-guardrails-plan.md)
as the execution plan for the remaining work.