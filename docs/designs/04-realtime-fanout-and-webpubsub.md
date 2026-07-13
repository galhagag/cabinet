# Design 04 — Realtime Fan-out & Web PubSub

**Status:** Proposed
**Addresses:** H6 (production realtime is silently dead), M5 (sequential
`send_json` blocks the loop on one slow client), M6 (`AzureWebPubSubBroker`
error handling + client-construction race), and the WS lifecycle Lows (only
`WebSocketDisconnect` caught; `_rooms` never evicts empty keys; access token in
query string).
**Effort:** M (~1 sprint)

---

## Problem

The realtime layer works in dev (in-process) but the production path is
incomplete and the dev path has back-pressure and cleanup bugs.

- **H6 — prod realtime dead.** With `CABINET_REALTIME_PROVIDER=azure_webpubsub`,
  `build_realtime` returns `AzureWebPubSubBroker` for *publishing*
  ([realtime.py:86](../../backend/app/services/realtime.py#L86)), but
  `/ws/rooms/{id}` still registers browsers on the in-process
  `ConnectionManager` that nothing publishes into
  ([ws.py](../../backend/app/api/ws.py)), and there is no endpoint that mints a
  Web PubSub client-access URL. Deploy with the documented prod vars and every
  browser connects, gets pongs, and receives **zero** `message_created` /
  `room_paused` events.
- **M5 — head-of-line blocking.** `ConnectionManager.broadcast` awaits
  `send_json` sequentially ([realtime.py:32](../../backend/app/services/realtime.py#L32)),
  directly in the orchestrator's critical path. One member with a saturated TCP
  buffer (backgrounded mobile tab) blocks every other member *and* stalls the
  agent loop between turns.
- **M6 — broker fragility.** `AzureWebPubSubBroker.publish` has no error
  handling and `_get_client` is check-then-act
  ([realtime.py:61](../../backend/app/services/realtime.py#L61)); a transient
  failure 500s `handle_human_message` *after* the human message committed
  (client retry duplicates the post), and concurrent first-publishes build two
  clients, leaking one.
- **Lows.** `ws.py` catches only `WebSocketDisconnect` with no `finally`, so any
  other error in the receive loop leaks a closed socket into `_rooms`; `_rooms`
  (a `defaultdict(set)`) never deletes empty room keys; the Entra token rides in
  `?access_token=`, landing in proxy/access logs.

## Goals

- Realtime works identically (from the client's perspective) in dev and on Azure
  Web PubSub, selected by config with no code change.
- A slow or dead client never blocks other clients or the agent loop.
- Publish failures never corrupt request state or duplicate messages.
- WebSocket connections are always cleaned up; connection state doesn't grow
  unboundedly.

## Design

### 1. Complete the Web PubSub path (H6)

Add a **negotiate endpoint** that issues a short-lived client-access URL scoped
to the room group, after the same membership check `/ws` does today:

```python
@router.get("/api/rooms/{room_id}/realtime-token")
async def realtime_token(room_id, _member = Depends(require_room_member),
                         broker = Depends(get_broker)):
    # only meaningful for the Web PubSub broker; in-process returns the WS URL
    return await broker.client_access(room_id, user_email)
```

Extend the `RealtimeBroker` protocol with `client_access(room_id, user)`:

- `InProcessBroker` → returns `{"mode": "ws", "url": "/ws/rooms/{id}"}` (today's
  behavior; the frontend already knows this URL).
- `AzureWebPubSubBroker` → returns
  `{"mode": "webpubsub", "url": <client_access_uri with roles=[webpubsub.joinLeaveGroup.{room}, webpubsub.sendToGroup.{room}] and short TTL>}`
  via `get_client_access_token(...)`.

The frontend `ws.ts` ([Design 10](10-frontend-reliability-and-ux.md)) calls the
negotiate endpoint first and connects to whichever URL it gets, so the same
client code drives both modes. In Web PubSub mode the backend no longer needs
the in-process hub for browser delivery; `/ws/...` stays only for the dev
provider. This also removes the token-in-query-string concern in prod (Web
PubSub uses its own short-lived per-connection token, not the API bearer token).

### 2. Non-blocking fan-out (M5)

Give each in-process connection a bounded `asyncio.Queue` and a dedicated writer
task; `broadcast` does `queue.put_nowait(event)`:

- Fast path: enqueue is O(1) and never awaits the socket.
- Slow client: its queue fills; on overflow apply a **drop-oldest** policy and
  send a single `{"type": "desync", "reason": "slow_consumer"}` marker so the
  client refetches (ties into the reconnect-resync protocol in Design 10). One
  laggard can no longer stall the loop or other members.

The orchestrator continues to `await broker.publish(...)`, but `publish` now
returns as soon as events are enqueued.

### 3. Robust broker (M6)

- Guard `_get_client` construction with an `asyncio.Lock` (build once).
- Wrap `publish` in `try/except`: log and swallow (realtime is best-effort;
  the DB is the source of truth). A publish failure must **never** propagate
  into `handle_human_message` and 500 a committed write. Persisted messages are
  recoverable by the client via `listMessages` on reconnect.
- Add retry with backoff for transient Web PubSub errors, bounded so it can't
  extend the loop indefinitely.

### 4. WS lifecycle cleanup (Lows)

```python
await manager.connect(room_id, websocket)
try:
    while True:
        text = await websocket.receive_text()
        if text == "ping":
            await websocket.send_json({"type": "pong"})
except WebSocketDisconnect:
    pass
finally:
    manager.disconnect(room_id, websocket)   # runs on ANY exit path
```

In `ConnectionManager.disconnect`, delete the room key when its set becomes
empty so `_rooms` doesn't grow with room count. Add a periodic reaper (or
per-connection idle timeout) as defense in depth.

## Implementation sketch

- `services/realtime.py`: `client_access` on the protocol + both impls;
  per-connection queue + writer task in `ConnectionManager`; lock + try/except
  + retry in `AzureWebPubSubBroker`; evict empty room keys.
- `api/ws.py`: `try/finally` around the receive loop.
- `api/rooms.py` (or a new `api/realtime.py`): the negotiate endpoint.
- `schemas.py`: `desync` event; negotiate response model.

## Testing

- `test_websocket.py`: extend — a deliberately non-reading client does not block
  a second client from receiving events (M5); a client that errors on send is
  removed from `_rooms` and the room key is evicted when empty (Lows).
- New `test_realtime_broker.py`: `AzureWebPubSubBroker.publish` swallows a
  simulated transport error without raising; concurrent first-publishes build
  exactly one client; negotiate returns a `webpubsub` URL in prod mode and a
  `ws` URL in dev mode.
- Entra-mode WS auth test (also called out in M20): the `?access_token=`
  handshake accepts a valid token and rejects expired/wrong-audience.

## Rollout & risks

- Items 2–4 are dev-path improvements shippable now with low risk.
- Item 1 (Web PubSub) can't be integration-tested without an Azure resource;
  cover the negotiate/token-shape logic with mocks and validate end-to-end in a
  staging slot. Until then, prod realtime should be considered unverified —
  which is itself the H6 finding.
- Sequencing: this design is a prerequisite for [Design 02](02-orchestrator-resilience-and-durable-loop.md)
  Stage 3 (once the loop leaves the request, realtime is the only delivery path).
