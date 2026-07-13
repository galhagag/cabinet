"""Real-time fan-out.

Dev/in-process: the FastAPI WebSocket hub (``ConnectionManager``) keeps
per-room connection sets and the ``InProcessBroker`` publishes straight into
it. Production: ``AzureWebPubSubBroker`` implements the orchestrator's same
``RealtimeBroker.publish()`` protocol over Azure Web PubSub (group = room id).
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict

from fastapi import WebSocket

from ..config import Settings
from .secrets import SecretProvider

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Per-room sets of live WebSocket connections.

    Each connection gets its own bounded queue and writer task so a slow
    client's socket write never blocks broadcast() for other members or the
    orchestrator's critical path (Design 04 / M5).
    """

    _QUEUE_MAXSIZE = 32

    def __init__(self) -> None:
        self._rooms: dict[str, set[WebSocket]] = defaultdict(set)
        self._queues: dict[WebSocket, asyncio.Queue] = {}
        self._writers: dict[WebSocket, asyncio.Task] = {}

    async def connect(self, room_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._rooms[room_id].add(websocket)
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._QUEUE_MAXSIZE)
        self._queues[websocket] = queue
        self._writers[websocket] = asyncio.create_task(
            self._writer(room_id, websocket, queue)
        )

    def disconnect(self, room_id: str, websocket: WebSocket) -> None:
        room = self._rooms.get(room_id)
        if room is not None:
            room.discard(websocket)
            if not room:
                del self._rooms[room_id]
        writer = self._writers.pop(websocket, None)
        if writer is not None:
            writer.cancel()
        self._queues.pop(websocket, None)

    async def _writer(
        self, room_id: str, websocket: WebSocket, queue: "asyncio.Queue[dict]"
    ) -> None:
        try:
            while True:
                event = await queue.get()
                await websocket.send_json(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.disconnect(room_id, websocket)

    async def broadcast(self, room_id: str, event: dict) -> None:
        for websocket in list(self._rooms.get(room_id, ())):
            queue = self._queues.get(websocket)
            if queue is None:
                continue
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Slow consumer: drop the oldest queued event and tell the
                # client it may be desynced, instead of blocking broadcast()
                # — and therefore the orchestrator's critical path — on one
                # backgrounded tab.
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait({"type": "desync", "reason": "slow_consumer"})
                except asyncio.QueueFull:
                    pass


class InProcessBroker:
    """RealtimeBroker publishing directly into the in-process WS hub."""

    def __init__(self, manager: ConnectionManager) -> None:
        self._manager = manager

    async def publish(self, room_id: str, event: dict) -> None:
        await self._manager.broadcast(room_id, event)

    async def client_access(self, room_id: str, user_email: str) -> dict:
        return {"mode": "ws", "url": f"/ws/rooms/{room_id}"}


class AzureWebPubSubBroker:
    """Production RealtimeBroker over Azure Web PubSub (lazy SDK import)."""

    def __init__(self, settings: Settings, secret_provider: SecretProvider) -> None:
        self._settings = settings
        self._secrets = secret_provider
        self._client = None
        self._client_lock = asyncio.Lock()

    async def _get_client(self):
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    # aio client: send_to_group is awaited — the sync SDK
                    # would block the event loop on every agent turn.
                    from azure.messaging.webpubsubservice.aio import (
                        WebPubSubServiceClient,
                    )

                    connection_string = await self._secrets.get_secret(
                        "webpubsub-connection-string"
                    )
                    self._client = WebPubSubServiceClient.from_connection_string(
                        connection_string, hub=self._settings.webpubsub_hub
                    )
        return self._client

    async def publish(self, room_id: str, event: dict) -> None:
        try:
            client = await self._get_client()
            await client.send_to_group(
                room_id, json.dumps(event), content_type="application/json"
            )
        except Exception:
            # Realtime is best-effort; the DB is the source of truth and a
            # publish failure must never 500 a request whose write already
            # committed (M6). The client recovers via listMessages on
            # reconnect (Design 10).
            logger.warning(
                "Web PubSub publish failed for room %s", room_id, exc_info=True
            )

    async def client_access(self, room_id: str, user_email: str) -> dict:
        client = await self._get_client()
        result = await client.get_client_access_token(
            user_id=user_email,
            roles=[
                f"webpubsub.joinLeaveGroup.{room_id}",
                f"webpubsub.sendToGroup.{room_id}",
            ],
        )
        return {"mode": "webpubsub", "url": result["url"]}


def build_realtime(
    settings: Settings, secret_provider: SecretProvider
) -> tuple[ConnectionManager, InProcessBroker | AzureWebPubSubBroker]:
    manager = ConnectionManager()
    if settings.realtime_provider == "azure_webpubsub":
        return manager, AzureWebPubSubBroker(settings, secret_provider)
    if settings.realtime_provider == "inprocess":
        return manager, InProcessBroker(manager)
    raise ValueError(f"unknown realtime provider: {settings.realtime_provider}")
