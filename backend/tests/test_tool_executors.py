"""Built-in tool executors: drive_search (Google Drive v3) and web_search
(Tavily) — both plain httpx calls, tested via httpx.MockTransport with zero
live network."""
import httpx

from app.agents.tools import ToolContext, ToolExecutionError, drive_search, web_search
from app.db.base import get_sessionmaker
from app.db.models import Room

from .conftest import install_mock_google, make_room


def _drive_files_handler(files):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer ya29.mock-access-token"
        return httpx.Response(200, json={"files": files})

    return handler


def _link_drive(client, room_id: str) -> None:
    state = client.get(f"/api/rooms/{room_id}/gdrive/authorize").json()["state"]
    client.get("/api/gdrive/callback", params={"code": "c", "state": state})
    client.post(
        f"/api/rooms/{room_id}/gdrive/folder",
        json={"folder_id": "1AbCdEf", "folder_name": "Onboarding Docs"},
    )


async def _run_drive_search(client, room_id: str, query: str, transport=None) -> str:
    async with get_sessionmaker()() as session:
        room_row = await session.get(Room, room_id)
        ctx = ToolContext(
            session=session,
            room=room_row,
            settings=client.app.state.settings,
            secret_provider=client.app.state.secret_provider,
            google_oauth=client.app.state.google_oauth,
            transport=transport,
        )
        return await drive_search({"query": query}, ctx)


def test_drive_search_returns_no_connection_message_when_unlinked(client):
    room = make_room(client, "ToolsDriveBank1")
    result = client.portal.call(_run_drive_search, client, room["id"], "schema")
    assert "no google drive is connected" in result.lower()


def test_drive_search_finds_files_in_connected_folder(client):
    install_mock_google(client.app)
    room = make_room(client, "ToolsDriveBank2")
    _link_drive(client, room["id"])
    transport = httpx.MockTransport(
        _drive_files_handler(
            [
                {
                    "id": "f1",
                    "name": "Schema Mapping.docx",
                    "mimeType": "application/vnd.google-apps.document",
                    "webViewLink": "https://drive.google.com/f1",
                }
            ]
        )
    )
    result = client.portal.call(_run_drive_search, client, room["id"], "schema", transport)
    assert "Schema Mapping.docx" in result
    assert "https://drive.google.com/f1" in result


def test_drive_search_raises_tool_execution_error_on_http_failure(client):
    install_mock_google(client.app)
    room = make_room(client, "ToolsDriveBank3")
    _link_drive(client, room["id"])

    def failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    transport = httpx.MockTransport(failing_handler)

    async def run():
        try:
            await _run_drive_search(client, room["id"], "schema", transport)
            raise AssertionError("expected ToolExecutionError")
        except ToolExecutionError:
            pass

    client.portal.call(run)


def test_drive_search_raises_tool_execution_error_on_malformed_response(client):
    install_mock_google(client.app)
    room = make_room(client, "ToolsDriveBank4")
    _link_drive(client, room["id"])

    def malformed_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"files": [{"id": "f1"}]})

    transport = httpx.MockTransport(malformed_handler)

    async def run():
        try:
            await _run_drive_search(client, room["id"], "schema", transport)
            raise AssertionError("expected ToolExecutionError")
        except ToolExecutionError:
            pass

    client.portal.call(run)


def test_drive_search_raises_tool_execution_error_on_non_dict_response(client):
    install_mock_google(client.app)
    room = make_room(client, "ToolsDriveBank5")
    _link_drive(client, room["id"])

    def non_dict_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1, 2, 3])

    transport = httpx.MockTransport(non_dict_handler)

    async def run():
        try:
            await _run_drive_search(client, room["id"], "schema", transport)
            raise AssertionError("expected ToolExecutionError")
        except ToolExecutionError:
            pass

    client.portal.call(run)


def _tavily_handler(results):
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.tavily.com/search"
        assert request.headers["authorization"] == "Bearer mock-tavily-key"
        return httpx.Response(200, json={"results": results})

    return handler


async def _run_web_search(client, room_id: str, query: str, transport) -> str:
    async with get_sessionmaker()() as session:
        room_row = await session.get(Room, room_id)
        ctx = ToolContext(
            session=session,
            room=room_row,
            settings=client.app.state.settings,
            secret_provider=client.app.state.secret_provider,
            google_oauth=client.app.state.google_oauth,
            transport=transport,
        )
        return await web_search({"query": query}, ctx)


def test_web_search_returns_formatted_results(client):
    room = make_room(client, "ToolsWebBank1")
    transport = httpx.MockTransport(
        _tavily_handler(
            [
                {
                    "title": "FATF Guidance",
                    "url": "https://fatf.org/x",
                    "content": "Rolling window guidance for AML monitoring.",
                }
            ]
        )
    )
    result = client.portal.call(_run_web_search, client, room["id"], "FATF rolling window", transport)
    assert "FATF Guidance" in result
    assert "https://fatf.org/x" in result


def test_web_search_raises_tool_execution_error_on_http_failure(client):
    room = make_room(client, "ToolsWebBank2")

    def failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    transport = httpx.MockTransport(failing_handler)

    async def run():
        try:
            await _run_web_search(client, room["id"], "x", transport)
            raise AssertionError("expected ToolExecutionError")
        except ToolExecutionError:
            pass

    client.portal.call(run)


def test_web_search_raises_tool_execution_error_on_malformed_response(client):
    room = make_room(client, "ToolsWebBank3")

    def malformed_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    transport = httpx.MockTransport(malformed_handler)

    async def run():
        try:
            await _run_web_search(client, room["id"], "x", transport)
            raise AssertionError("expected ToolExecutionError")
        except ToolExecutionError:
            pass

    client.portal.call(run)


def test_web_search_raises_tool_execution_error_on_non_dict_response(client):
    room = make_room(client, "ToolsWebBank4")

    def non_dict_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1, 2, 3])

    transport = httpx.MockTransport(non_dict_handler)

    async def run():
        try:
            await _run_web_search(client, room["id"], "x", transport)
            raise AssertionError("expected ToolExecutionError")
        except ToolExecutionError:
            pass

    client.portal.call(run)
