# Cabinet of Experts — Design Docs

Each document below is a self-contained, executable design proposal. They fall
into two groups: **remediations** (fix an issue from the
[2026-07-12 review](../reviews/2026-07-12-codebase-review.md)) and **features**
(new capability). Every design states its problem, goals/non-goals, the concrete
change (with file-level implementation sketches), test plan, and rollout risks.

## Remediations

| # | Design | Addresses | Effort |
|---|--------|-----------|--------|
| 01 | [Fail-closed production configuration](01-fail-closed-production-config.md) | H1, H2, M8, config Lows | S |
| 02 | [Orchestrator resilience & durable loop](02-orchestrator-resilience-and-durable-loop.md) | C2, H5, M4, handoff/resume Lows | L |
| 03 | [Authorization & tenancy hardening](03-authorization-and-tenancy-hardening.md) | H3, H4, M3, Entra identity Lows | M |
| 04 | [Realtime fan-out & Web PubSub](04-realtime-fanout-and-webpubsub.md) | H6, M5, M6, WS lifecycle Lows | M |
| 05 | [Persistence, migrations & schema integrity](05-persistence-migrations-and-schema-integrity.md) | H13, M7, M16, M17, schema Lows | M |
| 06 | [Prompt injection & untrusted content](06-prompt-injection-and-untrusted-content.md) | H14, M1, M15 | M |
| 07 | [Rate limiting & abuse controls](07-rate-limiting-and-abuse-controls.md) | M9 | S |
| 08 | [Secrets & OAuth key management](08-secrets-and-oauth-key-management.md) | H10, M2 | S |
| 09 | [CI, quality gates & supply chain](09-ci-quality-gates-and-supply-chain.md) | H11, H12, M18, M19, M20, infra Lows | M |
| 10 | [Frontend reliability & UX](10-frontend-reliability-and-ux.md) | C1, H7, H8, H9, M10–M14, frontend Lows | M |

## Features / upgrades

| # | Design | Value | Effort |
|---|--------|-------|--------|
| 11 | [Streaming token responses](11-streaming-agent-responses.md) | Perceived latency; live agent typing | M |
| 12 | [Knowledge grounding & Drive RAG](12-knowledge-grounding-and-drive-rag.md) | Agents cite real customer docs | L |
| 13 | [Room lifecycle, templates & export](13-room-lifecycle-templates-and-export.md) | Onboarding throughput; audit deliverables | M |
| 14 | [Observability & cost governance](14-observability-and-cost-governance.md) | Ops visibility; per-customer LLM cost control | M |

Effort key: **S** ≈ ≤2 days · **M** ≈ ~1 sprint · **L** ≈ multi-sprint.

## Recommended order

1. **01, 10 (C1 slice), 02 (try/finally slice), 03** — small, high-impact
   safety and correctness fixes.
2. **02 (full), 04, 05, 06, 08** — production-readiness for the agent loop,
   realtime, data, and secrets.
3. **07, 09** — abuse controls and the CI/supply-chain backbone.
4. **11–14** — features, once the platform underneath is trustworthy.
