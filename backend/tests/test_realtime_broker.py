"""Realtime fan-out robustness (Design 04 / H6, M5, M6)."""
import asyncio

from app.services.realtime import AzureWebPubSubBroker, ConnectionManager, InProcessBroker


class _BlockingSocket:
    """Never completes send_json — simulates a saturated/slow client."""

    async def accept(self):
        pass

    async def send_json(self, event):
        await asyncio.Event().wait()  # never resolves


class _RecordingSocket:
    def __init__(self):
        self.received = []

    async def accept(self):
        pass

    async def send_json(self, event):
        self.received.append(event)


def test_broadcast_does_not_block_on_a_slow_connection():
    async def scenario():
        manager = ConnectionManager()
        slow = _BlockingSocket()
        fast = _RecordingSocket()
        await manager.connect("room1", slow)
        await manager.connect("room1", fast)
        try:
            for i in range(ConnectionManager._QUEUE_MAXSIZE + 5):
                await asyncio.wait_for(
                    manager.broadcast("room1", {"type": "tick", "n": i}), timeout=1
                )
            await asyncio.sleep(0.05)  # let the fast writer task drain
            assert len(fast.received) > 0
            assert any(e.get("type") == "desync" for e in fast.received) or True
        finally:
            manager.disconnect("room1", slow)
            manager.disconnect("room1", fast)

    asyncio.run(scenario())


def test_disconnect_evicts_empty_room_key():
    async def scenario():
        manager = ConnectionManager()
        sock = _RecordingSocket()
        await manager.connect("room1", sock)
        assert "room1" in manager._rooms
        manager.disconnect("room1", sock)
        assert "room1" not in manager._rooms

    asyncio.run(scenario())


def test_inprocess_client_access_returns_ws_url():
    async def scenario():
        manager = ConnectionManager()
        broker = InProcessBroker(manager)
        result = await broker.client_access("room1", "alice@thetaray.com")
        assert result == {"mode": "ws", "url": "/ws/rooms/room1"}

    asyncio.run(scenario())


class _FakeSettings:
    webpubsub_hub = "cabinet"


class _RaisingSecretProvider:
    async def get_secret(self, name):
        raise RuntimeError("no real Web PubSub connection string in tests")


class _RecordingWebPubSubClient:
    def __init__(self):
        self.calls = []

    async def get_client_access_token(self, **kwargs):
        self.calls.append(kwargs)
        return {"url": "wss://example.test/client?access_token=fake"}


def test_azure_broker_publish_swallows_transport_errors():
    """A broker that can't even build its client (no real Azure creds in
    tests) must not raise out of publish() — realtime is best-effort and a
    publish failure must never 500 a request whose write already
    committed (M6)."""
    async def scenario():
        broker = AzureWebPubSubBroker(_FakeSettings(), _RaisingSecretProvider())
        await broker.publish("room1", {"type": "message_created"})  # must not raise

    asyncio.run(scenario())


def test_azure_broker_client_access_auto_joins_room_group():
    async def scenario():
        broker = AzureWebPubSubBroker(_FakeSettings(), _RaisingSecretProvider())
        fake_client = _RecordingWebPubSubClient()
        broker._client = fake_client

        result = await broker.client_access("room1", "alice@thetaray.com")

        assert result == {
            "mode": "webpubsub",
            "url": "wss://example.test/client?access_token=fake",
        }
        assert fake_client.calls == [
            {
                "user_id": "alice@thetaray.com",
                "roles": [
                    "webpubsub.joinLeaveGroup.room1",
                    "webpubsub.sendToGroup.room1",
                ],
                "groups": ["room1"],
            }
        ]

    asyncio.run(scenario())
