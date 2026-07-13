# Design 14 — Observability & Cost Governance

**Status:** Proposed (feature)
**Value:** Makes the system operable and its LLM spend accountable. Today a
production incident (stranded room, dead realtime, Foundry outage) is invisible
until a user complains, and there is no per-customer view of LLM cost even though
every agent message already records `input_tokens`/`output_tokens`. This design
turns existing latent signals into dashboards, alerts, and budgets.
**Effort:** M (~1 sprint)

---

## Problem / opportunity

- **No structured telemetry.** No tracing, no metrics, no structured logs; the
  app is a black box in production. The failure modes this review found (C2
  stranded rooms, H6 dead realtime, LLM timeouts) are exactly the things that
  need alerting.
- **Cost is unmanaged.** `Message.input_tokens`/`output_tokens` are persisted
  ([models.py:107](../../backend/app/db/models.py#L107)) but never aggregated.
  With `POST /messages` spawning up to 6 real LLM turns and no rate limiting
  ([Design 07](07-rate-limiting-and-abuse-controls.md)), per-customer spend can
  run away silently.
- **Audit exists but isn't surfaced.** `audit_log` captures mutations; there's
  no way to view or query it operationally.

## Goals

- Structured logs, metrics, and distributed traces exported to Azure Monitor /
  Application Insights.
- A per-room and per-customer cost/usage view from the token data already
  captured.
- Alerts on the concrete failure modes (stranded rooms, LLM error rate, realtime
  publish failures, auth failures).
- Optional per-room/per-customer token budgets that pause the loop when exceeded.

## Non-goals

- Billing/invoicing (this is cost *visibility* and *guardrails*, not metering
  for charge-back).
- Replacing the audit trail (this consumes it).

## Design

### Telemetry

- Adopt **OpenTelemetry**: instrument FastAPI (requests), SQLAlchemy (queries),
  the LLM backend (a span per `complete`/`stream` with model, token counts,
  latency, `stop_reason`), and the orchestrator loop (a span per cycle, room id
  as an attribute). Export via the OTLP/Azure Monitor exporter to Application
  Insights; console exporter in dev.
- **Structured logging** (JSON) with correlation ids (request id, room id,
  loop/run id from [Design 02](02-orchestrator-resilience-and-durable-loop.md)),
  so a single onboarding session is traceable end to end.
- **Metrics:** counters/histograms for messages posted, cycles run, LLM latency,
  LLM errors, tokens in/out (by agent, by room), realtime publish
  success/failure, WS connections, rate-limit rejections, auth failures.

### Cost governance

- A `usage` rollup (materialized from `messages`, or an incrementally-maintained
  `room_usage` table) exposing tokens and estimated cost per room/customer/day.
  Cost estimate = tokens × per-model rate from config (rates are env/config, not
  hardcoded).
- `GET /api/admin/usage` (admin-gated) returns per-room/customer aggregates for
  a dashboard; the frontend admin panel gets a usage view.
- **Budgets (guardrail):** optional `Room.token_budget` / customer-level budget;
  when exceeded, the orchestrator pauses the room with a distinct reason
  (`reason: "token_budget_exhausted"`) reusing the existing pause machinery, and
  an admin can raise the budget to resume. This complements the *cycle* budget
  (which bounds turns) with a *cost* budget (which bounds spend).

### Alerting

Azure Monitor alerts on:

- Rooms stuck `active` with `cycles_used >= cycle_limit` for > N minutes
  (the C2 stranded-room signature — a safety net even after
  [Design 02](02-orchestrator-resilience-and-durable-loop.md)).
- LLM error rate / p95 latency thresholds.
- Realtime publish failure rate (the H6/M6 signal).
- Auth failure spikes (credential stuffing / misconfig).
- A health/synthetic check on `/api/health` + a realtime round-trip.

### Audit surfacing

`GET /api/admin/audit` (admin-gated, filterable by room/actor/action/time) so
the regulated-industry audit trail is actually queryable, with export tying into
[Design 13](13-room-lifecycle-templates-and-export.md).

## Implementation sketch

- `requirements.txt`: `opentelemetry-*` (FastAPI/SQLAlchemy instrumentation,
  OTLP/Azure Monitor exporter).
- `services/telemetry.py`: OTel setup wired in `main.lifespan`; a `record_llm`
  helper for spans/metrics; structured-logging config.
- `orchestrator.py` / `foundry_client.py`: spans + metric emits per cycle/call
  (token data already available).
- `db/models.py` + migration: `room_usage` (or a SQL view); optional
  `Room.token_budget`.
- `api/admin.py`: `usage` + `audit` endpoints.
- `config.py`: exporter config, per-model cost rates, budget defaults.
- Frontend: admin usage + audit views.

## Testing

- LLM span/metric emitted per `complete` with correct token counts (assert via
  an in-memory OTel exporter in tests).
- Usage rollup: a room with known agent messages aggregates to the expected
  token totals and cost estimate.
- Budget: a room exceeding `token_budget` pauses with
  `reason: "token_budget_exhausted"` and resumes after an admin raise.
- Audit endpoint returns and filters rows written by mutating endpoints
  (dovetails with the audit-trail test gap M20).

## Rollout & risks

- Land telemetry first (pure observability, no behavior change) — it also makes
  validating every *other* design's rollout far easier.
- Cost views are read-only and safe; budgets change loop behavior, so ship them
  last and default-off.
- **Risk:** telemetry must not log secrets or full message content — scrub
  attributes (ids and counts, not bodies); coordinate with the secret-scanning
  in [Design 09](09-ci-quality-gates-and-supply-chain.md).
- Cost *estimates* depend on maintained rate config; label them clearly as
  estimates, not billing.
