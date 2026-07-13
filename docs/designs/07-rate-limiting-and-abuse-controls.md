# Design 07 — Rate Limiting & Abuse Controls

**Status:** Proposed
**Addresses:** M9 (no rate limiting on any endpoint).
**Effort:** S (≤2 days for the core; ongoing tuning)

---

## Problem

No endpoint is throttled ([whole backend]). The most expensive path,
`POST /api/rooms/{id}/messages`, drives up to `cycle_limit` real LLM turns per
call; invite creation, skill uploads, and Entra token validation are all
unbounded. A single authenticated member can:

- Amplify cost by rapidly posting messages, each spawning a 6-turn autonomous
  loop (real money on Foundry, and — until [Design 02](02-orchestrator-resilience-and-durable-loop.md)
  Stage 3 — a held request per loop).
- DoS the backend with a flood of uploads (compounding the size issue in
  [Design 06](06-prompt-injection-and-untrusted-content.md)).
- Force JWKS refetches with random-`kid` tokens (also in
  [Design 03](03-authorization-and-tenancy-hardening.md)).

## Goals

- Per-identity and per-room limits on the expensive/abusable endpoints.
- Limits that work across replicas (shared store), degrading gracefully to
  per-process in dev.
- Clear `429` responses with `Retry-After`, surfaced usefully in the UI.

## Non-goals

- Edge/WAF-level DDoS protection (belongs at Azure Front Door / App Gateway;
  this design is app-level fairness and cost control).
- Quota/billing accounting (that is [Design 14](14-observability-and-cost-governance.md)).

## Design

### Mechanism

Adopt a middleware-based limiter with a pluggable backend:

- **Dev/test:** in-process token bucket (per-process; deterministic for tests).
- **Prod:** a shared store — Azure Cache for Redis (or the Web PubSub-adjacent
  infra) keyed by identity + route — so limits hold across Container Apps
  replicas. Abstract behind a `RateLimiter` protocol mirroring the existing
  provider pattern, selected by `CABINET_RATELIMIT_PROVIDER`.

`slowapi` (Starlette-native) or a small custom dependency both fit; a custom
dependency keeps it provider-agnostic and avoids a heavy dep. Prefer a
`RateLimit(key, limit, window)` FastAPI dependency applied per route.

### Policy (initial, tunable via env)

| Endpoint | Key | Limit (initial) |
|----------|-----|-----------------|
| `POST /messages` | (user, room) | 10 / min, burst 3 |
| `POST /resume` | (user, room) | 6 / min |
| skill upload | (user, room) | 5 / 10 min |
| invite create | (user, room) | 20 / hour |
| room create | user | 30 / hour |
| Entra validation (pre-auth) | client IP | 60 / min + JWKS-refetch cooldown (Design 03) |

A **per-room concurrency guard** complements the per-room loop lock from
Design 02: at most one active autonomous loop per room, and a small cap on
queued human messages per room, so one room can't monopolize the worker pool.

### Response contract

- Return `429` with `Retry-After` and a JSON body `{detail, retry_after}`.
- Frontend ([Design 10](10-frontend-reliability-and-ux.md)) shows a toast
  ("You're sending messages too quickly — try again in Ns") and disables the
  composer send until the window passes, rather than silently dropping.

## Implementation sketch

- `services/ratelimit.py`: `RateLimiter` protocol; `InProcessRateLimiter`
  (token bucket) and `RedisRateLimiter`; `build_rate_limiter(settings)`.
- `api/deps.py`: a `rate_limit(key_fn, limit, window)` dependency factory.
- Apply the dependency to the routes above; wire the limiter onto `app.state`.
- `config.py`: `CABINET_RATELIMIT_PROVIDER`, per-route limit overrides,
  `CABINET_REDIS_URL` secret name.

## Testing

- `test_ratelimit.py`: with the in-process limiter, N+1 rapid `POST /messages`
  for one (user, room) → the N+1th returns 429 with `Retry-After`; a different
  user or room is unaffected; the window resets after the interval.
- Concurrency guard: two overlapping loops for one room are serialized/rejected
  per policy (coordinate with Design 02's test).

## Rollout & risks

- Ship the in-process limiter first (protects single-replica dev/staging and is
  fully testable); add the Redis backend before multi-replica prod.
- **Risk:** limits too tight frustrate legitimate collaboration; start
  generous, make them env-tunable, and watch the metrics from
  [Design 14](14-observability-and-cost-governance.md) before tightening.
- The limiter must fail *open* on backend (Redis) unavailability for
  availability, but log loudly — a rate-limiter outage shouldn't take down
  messaging.
