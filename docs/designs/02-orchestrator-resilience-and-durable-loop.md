# Design 02 — Orchestrator Resilience & Durable Agent Loop

**Status:** Proposed
**Addresses:** C2 (no error handling around the LLM call → stranded room),
H5 (no per-room loop serialization → interleaved loops), M4 (loop runs inside
the HTTP request, no background task, no idempotency), plus the handoff-token
substring and duplicate-`room_resumed` Lows.
**Effort:** L (multi-sprint; ships in three stages)

---

## Problem

The autonomous loop is the heart of the product and its most fragile code.

1. **No failure handling (C2).** `run_autonomous_loop` awaits
   `self._llm.complete(...)` with no `try/except`
   ([orchestrator.py:145](../../backend/app/agents/orchestrator.py#L145)).
   If Foundry times out on cycle N, the request 500s *after* the cycle was
   claimed and committed ([`_claim_cycle`](../../backend/app/agents/orchestrator.py#L172))
   and *after* `agent_thinking` was broadcast, but *before* `_pause_if_exhausted`.
   The room is left `active` with `cycles_used == cycle_limit`. `/resume`
   requires `status == PAUSED` and returns 409, so **no agent ever speaks again**
   until a human posts; every client shows a permanent typing indicator because
   no terminal event type exists.

2. **No per-room serialization (H5).** `handle_human_message` resets
   `cycles_used=0, status=ACTIVE` unconditionally
   ([orchestrator.py:74](../../backend/app/agents/orchestrator.py#L74)). Two
   concurrent posts to the same room run two interleaved loops: the reset hands
   an in-flight loop a fresh budget, `_first_speaker` can pick the agent the
   other loop is mid-turn on (breaking alternation), `cycle_number` restarts at
   1 while numbered messages already exist, and the pair can produce ~12 turns
   since the last human. `_claim_cycle` is atomic *per claim*, but nothing
   serializes *whole loops*.

3. **Loop runs in the request (M4).** With a real backend at 30–60s/turn a POST
   can take 3–6 minutes. A proxy timeout or client disconnect cancels the
   coroutine mid-loop (same stranded state as C2); a redeploy loses remaining
   turns. There is no idempotency, so a client retry re-posts and burns a fresh
   budget.

4. **Handoff is a substring check.** `if HANDOFF_TOKEN in result.text`
   ([orchestrator.py:164](../../backend/app/agents/orchestrator.py#L164)) — a
   human who gets an agent to *quote* the token ends the loop, and the literal
   token is persisted/broadcast unstripped.

## Goals

- An LLM failure never strands a room; it always resolves to a terminal state
  the UI and `/resume` understand.
- Exactly one autonomous loop runs per room at a time; alternation and cycle
  numbering stay correct under concurrency and across replicas.
- The HTTP POST returns promptly; the loop survives proxy timeouts and (Stage 3)
  process restarts.
- Message posting is idempotent under client retry.

## Non-goals

- Streaming tokens (that is [Design 11](11-streaming-agent-responses.md); this
  design keeps whole-message turns but fixes their lifecycle).
- Cross-region durable queue selection beyond "Azure Service Bus or a DB-backed
  outbox" — the interface is defined here, the broker is an ops choice.

## Design

Delivered in three stages so value lands incrementally without a big-bang
rewrite.

### Stage 1 — Make the loop crash-safe (fixes C2, handoff Low)

Wrap each turn. On failure, emit a terminal event and pause the room so
`/resume` works:

```python
try:
    result = await self._llm.complete(agent_key=speaker, ...)
except LLMError as exc:              # new: FoundryLLM raises this on API failure/timeout
    await self._fail_turn(session, room, speaker, exc)
    break
```

`_fail_turn` persists a `sender_type="system"` message
(`"⚠️ {DisplayName} could not respond (upstream error). The room is paused —
resume to retry."`), sets `status=PAUSED` via the same atomic conditional
UPDATE used elsewhere, and broadcasts a new
`{"type": "agent_error", "agent_key": ..., "recoverable": true}` event that
completes the `agent_thinking` lifecycle client-side.

Add an explicit **terminal-event contract**: every `agent_thinking` is followed
by exactly one of `message_created` (that agent) or `agent_error`. The frontend
([Design 10](10-frontend-reliability-and-ux.md)) keys its typing indicator off
this pairing.

Replace the handoff substring check with a disciplined sentinel: require the
token to appear on its **own final line**, strip it from the persisted/broadcast
content, and record the handoff as message metadata (`ended_loop=True`) rather
than inline text.

Configure an explicit per-call timeout on `FoundryLLM` (SDK `timeout=`) and a
bounded retry (e.g. 2 attempts with jittered backoff) for transient errors
(429/503), distinct from terminal errors (auth, `refusal`).

### Stage 2 — Serialize loops per room (fixes H5, duplicate-resume Low)

Introduce a room-scoped lock around the *entire* human-message→loop critical
section:

- **Single replica (now):** an in-process `asyncio.Lock` keyed by room id (a
  `defaultdict(asyncio.Lock)` on the orchestrator). A second concurrent POST to
  the same room awaits the first, then runs cleanly with correct alternation.
- **Multi-replica (prod):** a Postgres advisory lock
  (`SELECT pg_advisory_xact_lock(hashtext(:room_id))`) held for the transaction
  that claims the loop, so only one replica drives a given room. On SQLite the
  advisory-lock call is a no-op and the in-process lock suffices (tests).

Fix the duplicate-`room_resumed`: derive `was_paused` from the `RETURNING` of
the status-transition UPDATE (as `/resume` already does at
[messages.py:79](../../backend/app/api/messages.py#L79)) instead of reading
pre-UPDATE ORM state at [orchestrator.py:73](../../backend/app/agents/orchestrator.py#L73).

### Stage 3 — Move the loop off the request path (fixes M4)

`POST /messages` becomes: persist the human message + an idempotency record,
enqueue a `run_loop(room_id)` job, return `202` immediately with the human
message and `room_status`. A worker (in-process `asyncio` task group for single
replica; Azure Service Bus consumer or a DB-backed outbox table polled by a
worker for prod) runs the loop and drives realtime as today.

- **Idempotency:** `MessageCreate` carries a client-generated
  `Idempotency-Key`; a unique row `(room_id, idempotency_key)` makes a retry
  return the original result instead of re-posting.
- **Recovery:** the outbox row (`status: queued|running|done|failed`,
  `attempts`, `locked_by`, `locked_at`) lets a restarted worker reclaim a loop
  that died mid-flight, and lets an operator see stuck loops.

Stage 3 depends on [Design 04](04-realtime-fanout-and-webpubsub.md) being done,
since once the loop is off the request path, realtime is the *only* delivery
channel — it must actually reach browsers in prod.

## Implementation sketch

- `foundry_client.py`: define `LLMError`; raise it on SDK exceptions/timeouts;
  add `timeout` + bounded retry; keep the `refusal` degrade as a non-error.
- `orchestrator.py`: `try/except` per turn; `_fail_turn`; terminal-event
  contract; sentinel-line handoff with stripping; per-room lock; advisory lock
  helper; `was_paused` from `RETURNING`.
- `schemas.py`: `agent_error` event shape; `Message.ended_loop` flag (or reuse
  metadata).
- Stage 3: `outbox` model + Alembic migration (see
  [Design 05](05-persistence-migrations-and-schema-integrity.md)); a
  `LoopWorker` abstraction with `InProcessWorker` and `ServiceBusWorker`.

## Testing

- **C2 regression:** a `MockLLM` configured to raise on cycle 3 → assert the
  room ends `PAUSED`, an `agent_error` event was published, a system message
  persisted, and a subsequent `/resume` succeeds (today it 409s).
- **H5:** fire two concurrent `handle_human_message` for one room → assert
  strict speaker alternation, no duplicate `cycle_number`, total agent turns
  ≤ `cycle_limit`.
- **Handoff:** a message quoting `HANDOFF_TO_HUMAN` mid-sentence does *not* end
  the loop; a genuine trailing sentinel does and is stripped from stored content.
- **Idempotency (Stage 3):** same `Idempotency-Key` twice → one human message,
  one loop.
- **Timeout/retry:** `MockLLM` raising a transient error twice then succeeding →
  one persisted turn, two retries counted.

## Rollout & risks

- Stages 1–2 are backward-compatible and low-risk; ship them first — Stage 1
  alone closes the critical stranding bug.
- Stage 3 changes the POST contract from `200 + full transcript` to
  `202 + human message`; the frontend must already consume realtime as the
  source of truth (Design 10 H7/H8) before this flips, or messages won't appear.
- Advisory locks add a DB round-trip per loop; negligible vs LLM latency.
