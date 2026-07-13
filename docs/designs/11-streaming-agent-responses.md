# Design 11 — Streaming Agent Responses

**Status:** Proposed (feature)
**Value:** Cuts perceived latency dramatically — a 40-turn onboarding session
currently makes humans wait 30–60s of dead air per agent turn behind a static
"thinking" dot. Streaming shows the expert composing in real time, which is the
single biggest UX upgrade available.
**Effort:** M (~1 sprint) — depends on [Design 04](04-realtime-fanout-and-webpubsub.md)
(working prod realtime) and pairs with [Design 02](02-orchestrator-resilience-and-durable-loop.md)
(terminal-event contract).

---

## Problem / opportunity

Today an agent turn is atomic: the orchestrator awaits the *entire* completion
([orchestrator.py:145](../../backend/app/agents/orchestrator.py#L145)), persists
one `Message`, then broadcasts one `message_created`. The client shows
`agent_thinking` until the whole reply lands. With real Foundry latency that's a
long, opaque wait, and it makes the 6-cycle autonomous loop feel glacial.

The Anthropic Messages API (and the `AnthropicFoundry`/`AsyncAnthropicFoundry`
clients this system targets) supports token streaming. The realtime layer
already fans out arbitrary JSON events. The pieces are in place to stream.

## Goals

- Stream agent output token-by-token (or chunked) to all room clients as it's
  generated.
- Persist the final, complete message exactly as today (audit trail unchanged).
- Preserve the terminal-event contract from Design 02 (`agent_thinking` →
  deltas → `message_created`/`agent_error`).
- Degrade cleanly to whole-message delivery when streaming is unavailable
  (mock mode, Web PubSub message-size limits, older clients).

## Non-goals

- Streaming tool-use / function-calling (no tools in the agent loop today).
- Changing the loop-budget or alternation semantics.

## Design

### Backend

Extend the `LLMBackend` protocol with an optional streaming method:

```python
async def stream(self, *, agent_key, system_prompt, turns) -> AsyncIterator[Delta]:
    ...
# Delta = {text: str}  # plus a final usage summary
```

- `FoundryLLM.stream` uses `client.messages.stream(...)` and yields text deltas;
  accumulates the full text + token usage for persistence.
- `MockLLM.stream` yields the scripted reply in a few chunks (deterministic for
  tests).
- The orchestrator, per turn:
  1. broadcast `{"type": "agent_thinking", "agent_key", "message_id": <preallocated uuid>}`
  2. `async for delta in llm.stream(...)`: broadcast
     `{"type": "message_delta", "message_id", "agent_key", "text": delta}`
  3. on completion: persist the full `Message` (with the preallocated id), then
     broadcast the existing `message_created` (now the "commit"/finalize event).
  4. on error mid-stream: `agent_error` (Design 02) — clients discard the
     partial.

Preallocating the message id lets deltas and the final `message_created`
correlate client-side. Persistence still happens once, at the end — partial
tokens are never written to the DB, so the transcript stays clean.

### Realtime

`message_delta` is a new best-effort event type. Because Web PubSub has
per-message size limits and deltas are frequent, batch small deltas
(e.g. flush every ~50ms or ~40 tokens) to bound event rate. The
non-blocking-fan-out work in [Design 04](04-realtime-fanout-and-webpubsub.md) is
what makes high-frequency deltas safe (they can't stall the loop or slow
clients).

### Frontend

- `useMessages` (from [Design 10](10-frontend-reliability-and-ux.md)) gains a
  `applyDelta(message_id, text)` that appends into a streaming placeholder row
  (rendered with a caret/typing affordance).
- `message_created` finalizes the placeholder into a committed message; if the
  finalize arrives without prior deltas (non-streaming path), it just inserts —
  same reducer, no special-casing.
- A missed-delta gap (detected via the reconnect resync) is healed by the final
  `message_created` carrying the complete text, so streaming never causes
  permanent corruption.

## Implementation sketch

- `foundry_client.py`: `stream` on the protocol + both backends; a
  `supports_streaming` flag so the orchestrator can fall back.
- `orchestrator.py`: streaming turn path (guarded by config
  `CABINET_STREAMING=1` and backend support); reuse the same persist/finalize.
- `schemas.py`: `message_delta` event shape.
- Frontend: delta handling in the message store + streaming row UI.
- `config.py`: `CABINET_STREAMING` toggle + delta-batch tunables.

## Testing

- `MockLLM.stream` yields 3 chunks → orchestrator emits `agent_thinking`, 3
  `message_delta`, one `message_created`; the persisted message equals the
  concatenation; token usage recorded once.
- Error after chunk 2 → `agent_error`, no `Message` row persisted, room paused
  (Design 02).
- Fallback: with streaming disabled, behavior is byte-identical to today
  (regression guard).
- Frontend: deltas accrete into one row that finalizes on `message_created`; a
  late-joining client that only sees `message_created` renders the same result.

## Rollout & risks

- Ship behind `CABINET_STREAMING`, default off until Design 04 prod realtime is
  verified.
- **Risk:** delta volume × room size × Web PubSub cost/limits — mitigated by
  batching and best-effort semantics.
- Keep the final `message_created` authoritative so any delta loss self-heals.
