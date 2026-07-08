"""Real-time fan-out.

Dev/in-process: the FastAPI WebSocket hub (``ConnectionManager``) keeps
per-room connection sets and the ``InProcessBroker`` publishes straight into
it. Production: ``AzureWebPubSubBroker`` implements the orchestrator's same
``RealtimeBroker.publish()`` protocol over Azure Web PubSub (group = room id).
"""
from __future__ import annotations

import json
from collections import defaultdict

from fastapi import WebSocket

from ..config import Settings
from .secrets import SecretProvider


class ConnectionManager:
    """Per-room sets of live WebSocket connections."""

    def __init__(self) -> None:
        self._rooms: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, room_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._rooms[room_id].add(websocket)

    def disconnect(self, room_id: str, websocket: WebSocket) -> None:
        self._rooms.get(room_id, set()).discard(websocket)

    async def broadcast(self, room_id: str, event: dict) -> None:
        dead: list[WebSocket] = []
        for websocket in list(self._rooms.get(room_id, ())):
            try:
                await websocket.send_json(event)
            except Exception:
                dead.append(websocket)
        for websocket in dead:
            self.disconnect(room_id, websocket)


class InProcessBroker:
    """RealtimeBroker publishing directly into the in-process WS hub."""

    def __init__(self, manager: ConnectionManager) -> None:
        self._manager = manager

    async def publish(self, room_id: str, event: dict) -> None:
        await self._manager.broadcast(room_id, event)


class AzureWebPubSubBroker:
    """Production RealtimeBroker over Azure Web PubSub (lazy SDK import)."""

    def __init__(self, settings: Settings, secret_provider: SecretProvider) -> None:
        self._settings = settings
        self._secrets = secret_provider
        self._client = None

    async def _get_client(self):
        if self._client is None:
            # aio client: send_to_group is awaited — the sync SDK would block
            # the event loop on every agent turn.
            from azure.messaging.webpubsubservice.aio import WebPubSubServiceClient

            connection_string = await self._secrets.get_secret(
                "webpubsub-connection-string"
            )
            self._client = WebPubSubServiceClient.from_connection_string(
                connection_string, hub=self._settings.webpubsub_hub
            )
        return self._client

    async def publish(self, room_id: str, event: dict) -> None:
        client = await self._get_client()
        await client.send_to_group(
            room_id, json.dumps(event), content_type="application/json"
        )


def build_realtime(
    settings: Settings, secret_provider: SecretProvider
) -> tuple[ConnectionManager, InProcessBroker | AzureWebPubSubBroker]:
    manager = ConnectionManager()
    if settings.realtime_provider == "azure_webpubsub":
        return manager, AzureWebPubSubBroker(settings, secret_provider)
    if settings.realtime_provider == "inprocess":
        return manager, InProcessBroker(manager)
    raise ValueError(f"unknown realtime provider: {settings.realtime_provider}")
