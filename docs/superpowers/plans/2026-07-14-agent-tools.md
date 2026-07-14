# Agent Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Data Expert and FCE invoke two real built-in tools — `drive_search` (the room's connected Google Drive) and `web_search` (Tavily) — mid-turn, via a new orchestrator-owned tool round-trip loop that works identically across all three LLM backends.

**Architecture:** `ChatTurn`/`LLMResult` gain optional tool-call/tool-result fields; each `LLMBackend` (Mock, Foundry/Claude, AzureOpenAI/GPT) translates to/from its own vendor wire format. `Orchestrator` owns a bounded round-trip loop per turn — a tool call never consumes an extra cycle. A code-based `TOOL_REGISTRY` plus a `room_tool_overrides` table (byte-for-byte the same shape as `room_skill_overrides`) control per-room enable/disable. Both tools are plain `httpx` REST calls — no new SDKs.

**Tech Stack:** FastAPI + SQLAlchemy (async) + Alembic + pytest (backend); React + TypeScript + Vite (frontend, verified via `tsc --noEmit` + manual walkthrough — no test runner exists in this repo).

## Global Constraints

- All new room-scoped endpoints (`tools` list/toggle) use `require_room_member` — never `require_admin`.
- `agent_key` path params are validated against `AGENT_KEYS` (400 if unrecognized), matching `skills.py`/`rooms.py` convention.
- Every mutation is audit-logged via the existing `AuditLog` table.
- Toggling a tool never mutates `TOOL_REGISTRY` — always via the room-scoped `room_tool_overrides` table (row presence = disabled), mirroring `room_skill_overrides` exactly.
- A tool round-trip never consumes one of the room's cycles — `Room.cycles_used` still increments exactly once per *turn*, regardless of how many tool calls it took.
- Per-turn tool-round cap defaults to 5 (`Settings.max_tool_rounds`, env `CABINET_MAX_TOOL_ROUNDS`); once exceeded, one final `complete()` call is made without `tools` attached to force text.
- Both built-in tools are read-only lookups, auto-executed — no human-approval gate in this plan.
- New Alembic migrations chain from the current head (`b7d4e1a9c3f2`) and pass `backend/tests/test_migrations.py`.
- No new frontend test framework — verify via `npx tsc --noEmit` plus manual `npm run dev` walkthrough.
- No new Python SDK dependencies for Tavily/Drive — direct `httpx` calls only (`requirements.txt`'s `httpx` dependency already carries the comment "Google OAuth token exchange / Drive API").
- Test external HTTP calls via `httpx.MockTransport`, the same approach `test_gdrive_oauth.py`/`test_llm_backend.py` already use — never hit live Google/Tavily/Anthropic/OpenAI endpoints in tests.

---

### Task 1: Tool registry, `room_tool_overrides`, and the two tool executors

**Files:**
- Modify: `backend/app/db/models.py:219-236` (after `RoomSkillOverride`)
- Modify: `backend/app/config.py:110-117` (after `history_window`), `:167-172` (after `token_encryption_key_previous_secret`)
- Modify: `backend/app/services/secrets.py:32-50` (`_dev_default`)
- Create: `backend/alembic/versions/c2e91f4a7b6d_add_room_tool_overrides.py`
- Create: `backend/app/agents/tools.py`
- Test: `backend/tests/test_tool_executors.py`

**Interfaces:**
- Consumes: `GDriveConnection`, `Room` (`..db.models`); `DATA_EXPERT_KEY`, `FCE_KEY` (`.profiles`); `Settings` (`..config`); `SecretProvider` (`..services.secrets`); `GoogleOAuthService` (`..services.google_oauth`).
- Produces: `RoomToolOverride` ORM model; `ToolContext` dataclass (`session, room, settings, secret_provider, google_oauth, transport=None`); `ToolExecutionError`; `ToolDefinition` dataclass (`name, description, parameters, default_agents, executor`); `TOOL_REGISTRY: dict[str, ToolDefinition]`; `ToolRunner` (method `run(name, arguments, ctx) -> str`). Task 2 consumes `TOOL_REGISTRY`/`RoomToolOverride`. Task 5 consumes `ToolContext`/`ToolExecutionError`/`ToolRunner`/`TOOL_REGISTRY`.

- [ ] **Step 1: Write the failing executor tests**

Create `backend/tests/test_tool_executors.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_tool_executors.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agents.tools'` (module doesn't exist yet).

- [ ] **Step 3: Add the `RoomToolOverride` model**

Edit `backend/app/db/models.py`, immediately after `RoomSkillOverride` (currently ending at line 235):

```python
class RoomToolOverride(Base):
    """Room-scoped disable toggle for a built-in tool — identical precedent
    to RoomSkillOverride. Tools are code-defined (TOOL_REGISTRY), not DB
    rows; this table only ever records the disabled exception.
    """

    __tablename__ = "room_tool_overrides"

    room_id: Mapped[str] = mapped_column(
        ForeignKey("rooms.id", ondelete="CASCADE"), primary_key=True
    )
    tool_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
```

- [ ] **Step 4: Write the Alembic migration**

Create `backend/alembic/versions/c2e91f4a7b6d_add_room_tool_overrides.py`:

```python
"""add room_tool_overrides

Revision ID: c2e91f4a7b6d
Revises: b7d4e1a9c3f2
Create Date: 2026-07-14 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c2e91f4a7b6d'
down_revision: Union[str, Sequence[str], None] = 'b7d4e1a9c3f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'room_tool_overrides',
        sa.Column('room_id', sa.String(length=36), nullable=False),
        sa.Column('tool_name', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['room_id'], ['rooms.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('room_id', 'tool_name'),
    )


def downgrade() -> None:
    op.drop_table('room_tool_overrides')
```

Run: `cd backend && .venv/bin/pytest tests/test_migrations.py -v`
Expected: PASS.

- [ ] **Step 5: Add the two new `Settings` fields**

Edit `backend/app/config.py`. After `history_window` (currently lines 113-116):

```python
    history_window: int = field(
        default_factory=lambda: _env_int("CABINET_HISTORY_WINDOW", 40, min_value=1)
    )
    # Hard cap on LLM<->tool round-trips within a single turn — independent
    # of the cycle budget, purely a runaway-loop / API-cost safety valve.
    max_tool_rounds: int = field(
        default_factory=lambda: _env_int("CABINET_MAX_TOOL_ROUNDS", 5, min_value=1)
    )
```

After `token_encryption_key_previous_secret` (currently line 171):

```python
    token_encryption_key_previous_secret: str = "token-encryption-key-previous"

    # --- Tools ------------------------------------------------------------
    # Secret NAME resolved through the SecretProvider (Key Vault in prod).
    tavily_api_key_secret: str = "tavily-api-key"
```

- [ ] **Step 6: Add the dev-default secret for Tavily**

Edit `backend/app/services/secrets.py`, in `_dev_default` (currently lines 32-50), add a branch before the final `else`:

```python
    elif name == "azure-openai-api-key":
        value = "mock-azure-openai-key"
    elif name == "tavily-api-key":
        value = "mock-tavily-key"
    else:
        raise KeyError(f"secret not configured: {name}")
```

- [ ] **Step 7: Write `backend/app/agents/tools.py`**

```python
"""Built-in agent tools: a code-based registry (not uploaded content, unlike
skills) plus the two tool executors. Both are plain httpx REST calls — no
new SDK dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..db.models import GDriveConnection, Room
from ..services.google_oauth import GoogleOAuthService
from ..services.secrets import SecretProvider
from .profiles import DATA_EXPERT_KEY, FCE_KEY


class ToolExecutionError(Exception):
    """A tool call failed (network, API error, etc.) — the caller feeds this
    back to the model as an error tool_result rather than failing the turn."""


@dataclass
class ToolContext:
    session: AsyncSession
    room: Room
    settings: Settings
    secret_provider: SecretProvider
    google_oauth: GoogleOAuthService
    transport: httpx.AsyncBaseTransport | None = None


ToolExecutorFn = Callable[[dict, ToolContext], Awaitable[str]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict  # JSON Schema
    default_agents: tuple[str, ...]
    executor: ToolExecutorFn = field(compare=False)


def _escape_drive_query(value: str) -> str:
    """Escape a value for Google Drive's `q` query language (single quotes
    and backslashes are the only characters that matter inside a quoted
    string there)."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


async def drive_search(arguments: dict, ctx: ToolContext) -> str:
    query = str(arguments.get("query", ""))
    result = await ctx.session.execute(
        select(GDriveConnection).where(GDriveConnection.room_id == ctx.room.id)
    )
    conn = result.scalar_one_or_none()
    if conn is None or conn.status not in ("connected", "linked") or not conn.google_folder_id:
        return "No Google Drive is connected for this room."

    token = await ctx.google_oauth.ensure_fresh_access_token(ctx.session, conn)
    params = {
        "q": (
            f"'{conn.google_folder_id}' in parents and trashed = false "
            f"and fullText contains '{_escape_drive_query(query)}'"
        ),
        "fields": "files(id,name,mimeType,webViewLink)",
        "pageSize": "5",
    }
    async with httpx.AsyncClient(transport=ctx.transport) as client:
        try:
            response = await client.get(
                "https://www.googleapis.com/drive/v3/files",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ToolExecutionError(f"drive_search failed: {exc}") from exc

    files = response.json().get("files", [])
    if not files:
        return f"No Drive files found matching {query!r}."
    lines = [f"- {f['name']} ({f['webViewLink']})" for f in files]
    return f"Found {len(files)} file(s) in this room's Drive:\n" + "\n".join(lines)


async def web_search(arguments: dict, ctx: ToolContext) -> str:
    query = str(arguments.get("query", ""))
    api_key = await ctx.secret_provider.get_secret(ctx.settings.tavily_api_key_secret)
    async with httpx.AsyncClient(transport=ctx.transport) as client:
        try:
            response = await client.post(
                "https://api.tavily.com/search",
                json={"query": query, "max_results": 5},
                headers={"Authorization": f"Bearer {api_key}"},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ToolExecutionError(f"web_search failed: {exc}") from exc

    results = response.json().get("results", [])
    if not results:
        return f"No web results found for {query!r}."
    lines = [
        f"- {r['title']}: {r['url']}\n  {r.get('content', '')[:280]}" for r in results
    ]
    return f"Found {len(results)} web result(s):\n" + "\n".join(lines)


_QUERY_PARAM = {
    "type": "object",
    "properties": {"query": {"type": "string", "description": "The search terms."}},
    "required": ["query"],
}

TOOL_REGISTRY: dict[str, ToolDefinition] = {
    "drive_search": ToolDefinition(
        name="drive_search",
        description=(
            "Search for files by name or content in this room's connected "
            "Google Drive folder."
        ),
        parameters=_QUERY_PARAM,
        default_agents=(DATA_EXPERT_KEY, FCE_KEY),
        executor=drive_search,
    ),
    "web_search": ToolDefinition(
        name="web_search",
        description="Search the public web for current information.",
        parameters=_QUERY_PARAM,
        default_agents=(DATA_EXPERT_KEY, FCE_KEY),
        executor=web_search,
    ),
}


class ToolRunner:
    """Looks up and executes a registered tool by name."""

    async def run(self, name: str, arguments: dict, ctx: ToolContext) -> str:
        tool = TOOL_REGISTRY.get(name)
        if tool is None:
            raise ToolExecutionError(f"unknown tool: {name}")
        return await tool.executor(arguments, ctx)
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_tool_executors.py -v`
Expected: PASS (6 tests).

- [ ] **Step 9: Commit**

```bash
git add backend/app/db/models.py backend/app/config.py backend/app/services/secrets.py \
  backend/alembic/versions/c2e91f4a7b6d_add_room_tool_overrides.py \
  backend/app/agents/tools.py backend/tests/test_tool_executors.py
git commit -m "feat: add tool registry, room_tool_overrides, drive_search + web_search executors"
```

---

### Task 2: Tools API — list + toggle, audit log, WS event

**Files:**
- Modify: `backend/app/schemas.py:147-149` (after `SkillToggleUpdate`)
- Create: `backend/app/api/tools.py`
- Modify: `backend/app/main.py:19` (import), `:78` (router registration)
- Test: `backend/tests/test_tool_toggle.py`

**Interfaces:**
- Consumes: `TOOL_REGISTRY`, `RoomToolOverride` (Task 1).
- Produces: `ToolOut` (`name, description, enabled`), `ToolToggleUpdate` (`enabled: bool`) schemas; WS event `agent_tool_toggled` (`room_id, agent_key, tool_name, enabled`). Task 6 (frontend types) mirrors these exactly.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_tool_toggle.py`:

```python
"""Per-room tool enable/disable toggle — built-in tools stay scoped per room."""
from .conftest import drain_until, make_room


def test_tools_list_defaults_enabled(client):
    room = make_room(client, "ToolsBank1")
    listed = client.get(f"/api/rooms/{room['id']}/agents/fce/tools").json()
    names = {t["name"] for t in listed}
    assert names == {"drive_search", "web_search"}
    assert all(t["enabled"] for t in listed)


def test_toggle_off_then_list_reflects_it(client):
    room = make_room(client, "ToolsBank2")
    resp = client.put(
        f"/api/rooms/{room['id']}/agents/fce/tools/web_search",
        json={"enabled": False},
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

    listed = client.get(f"/api/rooms/{room['id']}/agents/fce/tools").json()
    toggled = next(t for t in listed if t["name"] == "web_search")
    assert toggled["enabled"] is False


def test_toggle_back_on_restores_it(client):
    room = make_room(client, "ToolsBank3")
    client.put(f"/api/rooms/{room['id']}/agents/fce/tools/web_search", json={"enabled": False})
    resp = client.put(f"/api/rooms/{room['id']}/agents/fce/tools/web_search", json={"enabled": True})
    assert resp.json()["enabled"] is True


def test_disabling_in_one_room_does_not_affect_another(client):
    room_a = make_room(client, "ToolsBankA")
    room_b = make_room(client, "ToolsBankB")
    client.put(f"/api/rooms/{room_a['id']}/agents/fce/tools/web_search", json={"enabled": False})

    a_listed = client.get(f"/api/rooms/{room_a['id']}/agents/fce/tools").json()
    b_listed = client.get(f"/api/rooms/{room_b['id']}/agents/fce/tools").json()
    assert next(t for t in a_listed if t["name"] == "web_search")["enabled"] is False
    assert next(t for t in b_listed if t["name"] == "web_search")["enabled"] is True


def test_toggle_unknown_tool_404(client):
    room = make_room(client, "ToolsBank4")
    resp = client.put(
        f"/api/rooms/{room['id']}/agents/fce/tools/not-a-real-tool",
        json={"enabled": False},
    )
    assert resp.status_code == 404


def test_toggle_is_idempotent(client):
    room = make_room(client, "ToolsBank5")
    for _ in range(2):
        resp = client.put(
            f"/api/rooms/{room['id']}/agents/fce/tools/web_search",
            json={"enabled": False},
        )
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False


def test_unknown_agent_key_400(client):
    room = make_room(client, "ToolsBank6")
    resp = client.get(f"/api/rooms/{room['id']}/agents/not-a-real-agent/tools")
    assert resp.status_code == 400


def test_ws_receives_agent_tool_toggled(client):
    room = make_room(client, "ToolsBankWs")
    with client.websocket_connect(f"/ws/rooms/{room['id']}") as ws:
        client.put(f"/api/rooms/{room['id']}/agents/fce/tools/web_search", json={"enabled": False})
        event = drain_until(ws, "agent_tool_toggled")
        assert event["tool_name"] == "web_search"
        assert event["enabled"] is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_tool_toggle.py -v`
Expected: FAIL with 404s — no `/tools` route exists yet.

- [ ] **Step 3: Add the schemas**

Edit `backend/app/schemas.py`, immediately after `SkillToggleUpdate` (currently lines 147-149):

```python
class SkillToggleUpdate(BaseModel):
    enabled: bool


# --- Tools ------------------------------------------------------------------
class ToolOut(BaseModel):
    name: str
    description: str
    enabled: bool = True


class ToolToggleUpdate(BaseModel):
    enabled: bool
```

- [ ] **Step 4: Write `backend/app/api/tools.py`**

```python
"""Tools API: built-in per-agent tool catalog and per-room enable/disable
toggle. Mirrors skills.py exactly — tools are code-defined (TOOL_REGISTRY),
so there is no upload endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agents.orchestrator import RealtimeBroker
from ..agents.profiles import AGENT_KEYS
from ..agents.tools import TOOL_REGISTRY, ToolDefinition
from ..db.base import get_session
from ..db.models import AuditLog, RoomToolOverride
from ..schemas import ToolOut, ToolToggleUpdate
from .deps import get_broker, require_room_member

router = APIRouter(tags=["tools"])


def _tools_for_agent(agent_key: str) -> list[ToolDefinition]:
    return [t for t in TOOL_REGISTRY.values() if agent_key in t.default_agents]


@router.get(
    "/api/rooms/{room_id}/agents/{agent_key}/tools",
    response_model=list[ToolOut],
)
async def list_tools(
    room_id: str,
    agent_key: str,
    session: AsyncSession = Depends(get_session),
    _member: str = Depends(require_room_member),
) -> list[ToolOut]:
    if agent_key not in AGENT_KEYS:
        raise HTTPException(status_code=400, detail=f"unknown agent: {agent_key}")

    overrides = await session.execute(
        select(RoomToolOverride.tool_name).where(RoomToolOverride.room_id == room_id)
    )
    disabled = set(overrides.scalars().all())
    return [
        ToolOut(name=t.name, description=t.description, enabled=t.name not in disabled)
        for t in _tools_for_agent(agent_key)
    ]


@router.put(
    "/api/rooms/{room_id}/agents/{agent_key}/tools/{tool_name}",
    response_model=ToolOut,
)
async def toggle_tool(
    room_id: str,
    agent_key: str,
    tool_name: str,
    payload: ToolToggleUpdate,
    session: AsyncSession = Depends(get_session),
    broker: RealtimeBroker = Depends(get_broker),
    user_email: str = Depends(require_room_member),
) -> ToolOut:
    if agent_key not in AGENT_KEYS:
        raise HTTPException(status_code=400, detail=f"unknown agent: {agent_key}")
    tool = TOOL_REGISTRY.get(tool_name)
    if tool is None or agent_key not in tool.default_agents:
        raise HTTPException(status_code=404, detail="tool not found")

    existing = await session.get(RoomToolOverride, (room_id, tool_name))
    if payload.enabled and existing is not None:
        await session.delete(existing)
    elif not payload.enabled and existing is None:
        session.add(RoomToolOverride(room_id=room_id, tool_name=tool_name))

    session.add(
        AuditLog(
            room_id=room_id,
            actor=user_email,
            action="room_tool_toggled",
            detail={
                "agent_key": agent_key,
                "tool_name": tool_name,
                "enabled": payload.enabled,
            },
        )
    )
    await session.commit()
    await broker.publish(
        room_id,
        {
            "type": "agent_tool_toggled",
            "room_id": room_id,
            "agent_key": agent_key,
            "tool_name": tool_name,
            "enabled": payload.enabled,
        },
    )
    return ToolOut(name=tool.name, description=tool.description, enabled=payload.enabled)
```

- [ ] **Step 5: Register the router**

Edit `backend/app/main.py`. Change the import on line 19:

```python
from .api import admin, gdrive, messages, rooms, skills, tools, ws
```

Add the router registration after `app.include_router(skills.router)`:

```python
    app.include_router(skills.router)
    app.include_router(tools.router)
    app.include_router(ws.router)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_tool_toggle.py -v`
Expected: PASS (8 tests).

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas.py backend/app/api/tools.py backend/app/main.py \
  backend/tests/test_tool_toggle.py
git commit -m "feat: add tools list/toggle API, room_tool_toggled audit log, WS event"
```

---

### Task 3: LLMBackend protocol foundation — ToolSpec/ToolCall/ToolResult + MockLLM

**Files:**
- Modify: `backend/app/agents/foundry_client.py:37-59` (`ChatTurn`, `LLMResult`, `LLMBackend`), `:97-112` (`MockLLM.complete`)
- Test: `backend/tests/test_llm_tool_calling.py`

**Interfaces:**
- Produces: `ToolSpec` (`name, description, parameters`), `ToolCall` (`id, name, arguments`), `ToolResult` (`tool_call_id, content, is_error=False`); `ChatTurn` gains `tool_calls: list[ToolCall] | None = None` and `tool_results: list[ToolResult] | None = None`; `LLMResult` gains `tool_calls: list[ToolCall] | None = None`; `LLMBackend.complete()` gains `tools: list[ToolSpec] | None = None`. Task 4 consumes all of these for FoundryLLM/AzureOpenAILLM. Task 5 consumes all of these for the orchestrator loop.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_llm_tool_calling.py`:

```python
"""Common tool-calling vocabulary (ToolSpec/ToolCall/ToolResult/ChatTurn) and
MockLLM's deterministic scripted tool-call trigger — exercises the full
round trip with zero network calls."""
import asyncio

from app.agents.foundry_client import ChatTurn, MockLLM, ToolCall, ToolResult, ToolSpec


def _run(coro):
    return asyncio.run(coro)


_WEB_SEARCH = ToolSpec(
    name="web_search",
    description="Search the web.",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
)


def test_mock_llm_requests_a_tool_when_tools_are_offered_and_triggered():
    backend = MockLLM()
    result = _run(
        backend.complete(
            agent_key="fce",
            system_prompt="You are helpful.",
            turns=[ChatTurn(role="user", content="please use tools to check this")],
            tools=[_WEB_SEARCH],
        )
    )
    assert result.tool_calls == [
        ToolCall(id="mock-call-1", name="web_search", arguments={"query": "mock query"})
    ]


def test_mock_llm_does_not_request_a_tool_without_the_trigger_phrase():
    backend = MockLLM()
    result = _run(
        backend.complete(
            agent_key="fce",
            system_prompt="You are helpful.",
            turns=[ChatTurn(role="user", content="just a normal message")],
            tools=[_WEB_SEARCH],
        )
    )
    assert result.tool_calls is None


def test_mock_llm_ignores_the_trigger_phrase_when_no_tools_are_offered():
    backend = MockLLM()
    result = _run(
        backend.complete(
            agent_key="fce",
            system_prompt="You are helpful.",
            turns=[ChatTurn(role="user", content="please use tools to check this")],
            tools=None,
        )
    )
    assert result.tool_calls is None


def test_mock_llm_returns_final_text_after_a_tool_result_is_fed_back():
    backend = MockLLM()
    result = _run(
        backend.complete(
            agent_key="fce",
            system_prompt="You are helpful.",
            turns=[
                ChatTurn(role="user", content="please use tools to check this"),
                ChatTurn(
                    role="assistant",
                    content="",
                    tool_calls=[
                        ToolCall(id="mock-call-1", name="web_search", arguments={"query": "mock query"})
                    ],
                ),
                ChatTurn(
                    role="user",
                    content="",
                    tool_results=[ToolResult(tool_call_id="mock-call-1", content="some result")],
                ),
            ],
            tools=[_WEB_SEARCH],
        )
    )
    assert result.tool_calls is None
    assert result.text != ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_llm_tool_calling.py -v`
Expected: FAIL — `ImportError: cannot import name 'ToolSpec' from 'app.agents.foundry_client'`.

- [ ] **Step 3: Extend the dataclasses and Protocol**

Edit `backend/app/agents/foundry_client.py`. Replace lines 37-59 (`ChatTurn` through `LLMBackend`):

```python
@dataclass(frozen=True)
class ChatTurn:
    role: str  # "user" | "assistant"
    content: str = ""
    # Set on an assistant turn that requested one or more tool calls.
    tool_calls: list[ToolCall] | None = None
    # Set on the following user turn carrying each call's result.
    tool_results: list[ToolResult] | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON Schema


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class ToolResult:
    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class LLMResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: list[ToolCall] | None = None


class LLMError(Exception):
    """The LLM backend failed to produce a completion (timeout, API error,
    refusal-that-isn't-handled-server-side, etc.). Callers must treat this as
    recoverable: pause the room, never leave it stranded active."""


class LLMBackend(Protocol):
    async def complete(
        self,
        *,
        agent_key: str,
        system_prompt: str,
        turns: list[ChatTurn],
        tools: list[ToolSpec] | None = None,
    ) -> LLMResult: ...
```

(Note: `LLMError` moves below `LLMResult` here only because `ChatTurn` now forward-references `ToolCall`/`ToolResult`, which must be defined before anything that isn't using a string-quoted forward reference; keeping the whole block together as shown avoids ordering issues.)

- [ ] **Step 4: Update `MockLLM.complete`**

Edit `backend/app/agents/foundry_client.py`, replace the `complete` method of `MockLLM` (currently lines 97-112):

```python
    async def complete(
        self,
        *,
        agent_key: str,
        system_prompt: str,
        turns: list[ChatTurn],
        tools: list[ToolSpec] | None = None,
    ) -> LLMResult:
        last = turns[-1].content if turns else ""
        # Deterministic scripted trigger: the phrase only ever appears in the
        # turn that precedes the FIRST round of a tool loop — once the
        # orchestrator appends the (empty-content) tool_results turn as the
        # new last turn, this naturally stops matching, so the loop can
        # never re-trigger on its own tool-result turn.
        if tools and "use tools" in last.lower():
            return LLMResult(
                text="",
                tool_calls=[
                    ToolCall(id="mock-call-1", name=tools[0].name, arguments={"query": "mock query"})
                ],
            )
        flavor = self._FLAVOR.get(agent_key, "Acknowledged.")
        reply = f"[{agent_key}·mock] {flavor} (re: {self._quote(last)})"
        if "wrap up" in last.lower():
            reply += " HANDOFF_TO_HUMAN"
        # ~4 chars/token — a rough but deterministic stand-in for real usage,
        # good enough to exercise the token-usage UI in dev/CI.
        prompt_chars = len(system_prompt) + sum(len(t.content) for t in turns)
        return LLMResult(
            text=reply,
            input_tokens=max(1, prompt_chars // 4),
            output_tokens=max(1, len(reply) // 4),
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_llm_tool_calling.py tests/test_llm_backend.py -v`
Expected: PASS (all tests, including the pre-existing `test_llm_backend.py` suite — confirms the signature change is backward compatible).

- [ ] **Step 6: Commit**

```bash
git add backend/app/agents/foundry_client.py backend/tests/test_llm_tool_calling.py
git commit -m "feat: add ToolSpec/ToolCall/ToolResult and MockLLM scripted tool-call support"
```

---

### Task 4: FoundryLLM (Claude) + AzureOpenAILLM (GPT) tool translation

**Files:**
- Modify: `backend/app/agents/foundry_client.py:17-29` (imports), `115-169` (`FoundryLLM`), `172-239` (`AzureOpenAILLM`)
- Modify: `backend/tests/test_llm_backend.py` (append tests)

**Interfaces:**
- Consumes: `ChatTurn`, `ToolSpec`, `ToolCall`, `ToolResult`, `LLMResult` (Task 3).
- Produces: `FoundryLLM.__init__` gains an `http_client: httpx.AsyncClient | None = None` param (mirroring `AzureOpenAILLM`'s existing one), enabling `httpx.MockTransport`-based testing. Both classes' `complete()` accept and honor `tools`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_llm_backend.py`:

```python
from app.agents.foundry_client import FoundryLLM, ToolCall, ToolResult, ToolSpec


def _foundry_backend(handler) -> FoundryLLM:
    settings = Settings(foundry_resource="test-resource", foundry_model="claude-opus-4-8")
    transport = httpx.MockTransport(handler)
    return FoundryLLM(
        settings, api_key="test-key", http_client=httpx.AsyncClient(transport=transport)
    )


def _foundry_tool_use_handler():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "msg_test_tool",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-4-8",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "drive_search",
                        "input": {"query": "schema doc"},
                    }
                ],
                "stop_reason": "tool_use",
                "stop_sequence": None,
                "usage": {"input_tokens": 20, "output_tokens": 8},
            },
        )

    return handler


def _foundry_text_handler(text="Final answer"):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "msg_test_final",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-4-8",
                "content": [{"type": "text", "text": text}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 15, "output_tokens": 6},
            },
        )

    return handler


_DRIVE_SEARCH_SPEC = ToolSpec(
    name="drive_search",
    description="Search Drive.",
    parameters={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
)


def test_foundry_complete_returns_tool_calls():
    backend = _foundry_backend(_foundry_tool_use_handler())
    result = _run(
        backend.complete(
            agent_key="data_expert",
            system_prompt="You are helpful.",
            turns=[ChatTurn(role="user", content="find the schema doc")],
            tools=[_DRIVE_SEARCH_SPEC],
        )
    )
    assert result.tool_calls == [
        ToolCall(id="toolu_1", name="drive_search", arguments={"query": "schema doc"})
    ]
    assert result.input_tokens == 20
    assert result.output_tokens == 8


def test_foundry_complete_replays_tool_result_and_returns_final_text():
    backend = _foundry_backend(_foundry_text_handler("Based on the doc, use column X."))
    result = _run(
        backend.complete(
            agent_key="data_expert",
            system_prompt="You are helpful.",
            turns=[
                ChatTurn(role="user", content="find the schema doc"),
                ChatTurn(
                    role="assistant",
                    content="",
                    tool_calls=[
                        ToolCall(id="toolu_1", name="drive_search", arguments={"query": "schema doc"})
                    ],
                ),
                ChatTurn(
                    role="user",
                    content="",
                    tool_results=[
                        ToolResult(tool_call_id="toolu_1", content="Found: Schema Mapping.docx")
                    ],
                ),
            ],
        )
    )
    assert result.text == "Based on the doc, use column X."
    assert result.tool_calls is None


def _tool_call_completion_handler():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test-tool",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "web_search",
                                        "arguments": '{"query": "AML rolling window"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18},
            },
        )

    return handler


def test_azure_openai_complete_returns_tool_calls():
    backend = _backend(_tool_call_completion_handler())
    result = _run(
        backend.complete(
            agent_key="fce",
            system_prompt="You are helpful.",
            turns=[ChatTurn(role="user", content="look this up")],
            tools=[
                ToolSpec(
                    name="web_search",
                    description="Search the web.",
                    parameters={
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                )
            ],
        )
    )
    assert result.tool_calls == [
        ToolCall(id="call_1", name="web_search", arguments={"query": "AML rolling window"})
    ]
    assert result.input_tokens == 12
    assert result.output_tokens == 6


def test_azure_openai_complete_replays_tool_result_and_returns_final_text():
    backend = _backend(_chat_completion_handler(content="Based on the search, proceed."))
    result = _run(
        backend.complete(
            agent_key="fce",
            system_prompt="You are helpful.",
            turns=[
                ChatTurn(role="user", content="look this up"),
                ChatTurn(
                    role="assistant",
                    content="",
                    tool_calls=[
                        ToolCall(id="call_1", name="web_search", arguments={"query": "AML rolling window"})
                    ],
                ),
                ChatTurn(
                    role="user",
                    content="",
                    tool_results=[ToolResult(tool_call_id="call_1", content="Some search result")],
                ),
            ],
        )
    )
    assert result.text == "Based on the search, proceed."
    assert result.tool_calls is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_llm_backend.py -v -k "tool_calls or tool_result"`
Expected: FAIL — `TypeError: FoundryLLM.__init__() got an unexpected keyword argument 'http_client'` and `complete()` raising on the unrecognized `tools` kwarg / translation not implemented yet.

- [ ] **Step 3: Add `import json` and the message-translation helpers**

Edit `backend/app/agents/foundry_client.py`. Add to the top-level imports (after `import re`):

```python
import json
import re
```

Add these two module-level functions right before the `FoundryLLM` class:

```python
def _turn_to_anthropic_message(turn: ChatTurn) -> dict:
    if turn.tool_calls:
        content: list[dict] = []
        if turn.content:
            content.append({"type": "text", "text": turn.content})
        content.extend(
            {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
            for c in turn.tool_calls
        )
        return {"role": "assistant", "content": content}
    if turn.tool_results:
        content = [
            {
                "type": "tool_result",
                "tool_use_id": r.tool_call_id,
                "content": r.content,
                "is_error": r.is_error,
            }
            for r in turn.tool_results
        ]
        return {"role": "user", "content": content}
    return {"role": turn.role, "content": turn.content}
```

- [ ] **Step 4: Update `FoundryLLM` to accept `http_client` and translate tools**

Replace the whole `FoundryLLM` class (currently lines 115-169) with:

```python
class FoundryLLM:
    """Claude on Microsoft Foundry through the official SDK client."""

    def __init__(
        self,
        settings: Settings,
        api_key: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        from anthropic import AsyncAnthropicFoundry

        self._settings = settings
        kwargs: dict = {"resource": settings.foundry_resource}
        if http_client is not None:
            kwargs["http_client"] = http_client
        if settings.foundry_auth == "entra":
            # Microsoft Entra ID authentication (managed identity / workload
            # identity on ACA/AKS). azure-identity is a production-only dep.
            # aio credential: token acquisition must not block the event loop.
            from azure.identity.aio import (
                DefaultAzureCredential,
                get_bearer_token_provider,
            )

            kwargs["azure_ad_token_provider"] = get_bearer_token_provider(
                DefaultAzureCredential(),
                "https://cognitiveservices.azure.com/.default",
            )
        else:
            kwargs["api_key"] = api_key
        self._client = AsyncAnthropicFoundry(**kwargs)

    async def complete(
        self,
        *,
        agent_key: str,
        system_prompt: str,
        turns: list[ChatTurn],
        tools: list[ToolSpec] | None = None,
    ) -> LLMResult:
        kwargs: dict = {
            "model": self._settings.foundry_model,
            "max_tokens": self._settings.agent_max_tokens,
            "system": system_prompt,
            "messages": [_turn_to_anthropic_message(t) for t in turns],
        }
        if tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]
        try:
            response = await self._client.messages.create(**kwargs)
        except Exception as exc:
            raise LLMError(f"Foundry completion failed for {agent_key}: {exc}") from exc
        input_tokens = getattr(response.usage, "input_tokens", 0) or 0
        output_tokens = getattr(response.usage, "output_tokens", 0) or 0

        # Refusal fallbacks aren't server-side on Foundry — degrade politely.
        if response.stop_reason == "refusal":
            return LLMResult(
                text=(
                    "I can't help with that request as phrased. "
                    "Could a human colleague rephrase or narrow the ask? "
                    "HANDOFF_TO_HUMAN"
                ),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        if response.stop_reason == "tool_use":
            calls = [
                ToolCall(id=block.id, name=block.name, arguments=block.input)
                for block in response.content
                if block.type == "tool_use"
            ]
            text = "".join(block.text for block in response.content if block.type == "text")
            return LLMResult(
                text=text,
                tool_calls=calls,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        text = "".join(
            block.text for block in response.content if block.type == "text"
        )
        return LLMResult(text=text, input_tokens=input_tokens, output_tokens=output_tokens)
```

- [ ] **Step 5: Update `AzureOpenAILLM` to translate tools**

Add this module-level helper right before the `AzureOpenAILLM` class:

```python
def _turn_to_openai_messages(turn: ChatTurn) -> list[dict]:
    """Returns a list because a tool_results turn expands into one 'tool'
    role message per result — OpenAI has no equivalent of Claude nesting
    multiple tool_result blocks inside a single message."""
    if turn.tool_calls:
        return [
            {
                "role": "assistant",
                "content": turn.content or None,
                "tool_calls": [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                    }
                    for c in turn.tool_calls
                ],
            }
        ]
    if turn.tool_results:
        return [
            {"role": "tool", "tool_call_id": r.tool_call_id, "content": r.content}
            for r in turn.tool_results
        ]
    return [{"role": turn.role, "content": turn.content}]
```

Replace the `complete` method of `AzureOpenAILLM` (currently lines 204-239) with:

```python
    async def complete(
        self,
        *,
        agent_key: str,
        system_prompt: str,
        turns: list[ChatTurn],
        tools: list[ToolSpec] | None = None,
    ) -> LLMResult:
        messages = [{"role": "system", "content": system_prompt}]
        for t in turns:
            messages.extend(_turn_to_openai_messages(t))

        kwargs: dict = {
            "model": self._settings.azure_openai_deployment,
            "max_completion_tokens": self._settings.agent_max_tokens,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
        try:
            response = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise LLMError(f"Azure OpenAI completion failed for {agent_key}: {exc}") from exc
        choice = response.choices[0]
        usage = response.usage
        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0

        # Content filtering is enforced server-side and returns no message
        # text — degrade the same way FoundryLLM does for a Claude refusal.
        if choice.finish_reason == "content_filter":
            return LLMResult(
                text=(
                    "I can't help with that request as phrased. "
                    "Could a human colleague rephrase or narrow the ask? "
                    "HANDOFF_TO_HUMAN"
                ),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        if choice.message.tool_calls:
            calls = [
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=json.loads(tc.function.arguments or "{}"),
                )
                for tc in choice.message.tool_calls
            ]
            return LLMResult(
                text=choice.message.content or "",
                tool_calls=calls,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        return LLMResult(
            text=choice.message.content or "",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_llm_backend.py -v`
Expected: PASS (all tests — pre-existing ones plus the four new tool-calling tests).

- [ ] **Step 7: Commit**

```bash
git add backend/app/agents/foundry_client.py backend/tests/test_llm_backend.py
git commit -m "feat: translate tool calls to/from Claude and Azure OpenAI wire formats"
```

---

### Task 5: Orchestrator tool-loop integration

**Files:**
- Modify: `backend/app/db/models.py:99-126` (`Message`)
- Create: `backend/alembic/versions/d581a3c9f0e2_add_message_tool_invocations.py`
- Modify: `backend/app/schemas.py:95-107` (`MessageOut`)
- Modify: `backend/app/agents/orchestrator.py:17-36` (imports, `Orchestrator.__init__`), `179-261` (`_run_mention_reply`, `run_autonomous_loop`), `446-463` (`_msg_event`)
- Modify: `backend/app/main.py:37-49` (lifespan wiring)
- Test: `backend/tests/test_agent_tools_loop.py`

**Interfaces:**
- Consumes: `TOOL_REGISTRY`, `ToolContext`, `ToolExecutionError`, `ToolRunner` (Task 1); `ChatTurn`, `ToolCall`, `ToolResult`, `ToolSpec` (Task 3); `RoomToolOverride` (Task 1).
- Produces: `Message.tool_invocations: list[dict] | None`; `Orchestrator.__init__(settings, llm, broker, *, secret_provider, google_oauth)` (new required kwargs). Task 6 (frontend types) mirrors `MessageOut.tool_invocations`.

- [ ] **Step 1: Add the `Message.tool_invocations` column**

Edit `backend/app/db/models.py`, in the `Message` class (currently lines 99-126), insert after `output_tokens`:

```python
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # One entry per tool call made while producing this reply, e.g.
    # {"tool": "web_search", "query": "...", "success": True}. None for
    # messages that never used a tool.
    tool_invocations: Mapped[list | None] = mapped_column(JSON, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
```

- [ ] **Step 2: Write the Alembic migration**

Create `backend/alembic/versions/d581a3c9f0e2_add_message_tool_invocations.py`:

```python
"""add messages.tool_invocations

Revision ID: d581a3c9f0e2
Revises: c2e91f4a7b6d
Create Date: 2026-07-14 09:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd581a3c9f0e2'
down_revision: Union[str, Sequence[str], None] = 'c2e91f4a7b6d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('messages', sa.Column('tool_invocations', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('messages', 'tool_invocations')
```

Run: `cd backend && .venv/bin/pytest tests/test_migrations.py -v`
Expected: PASS.

- [ ] **Step 3: Add `tool_invocations` to `MessageOut`**

Edit `backend/app/schemas.py`, in `MessageOut` (currently lines 95-107):

```python
class MessageOut(BaseModel):
    id: str
    room_id: str
    sender_type: str
    sender_name: str
    agent_key: str | None
    mention_target: str | None
    cycle_number: int | None
    content: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    tool_invocations: list[dict] | None = None
    created_at: datetime
```

- [ ] **Step 4: Write the failing orchestrator tests**

Create `backend/tests/test_agent_tools_loop.py`:

```python
"""Tool-calling round-trip inside a single turn: a tool call never consumes
an extra cycle, invocations are recorded on the final Message, execution
failures degrade gracefully, and a runaway tool loop is capped."""
from sqlalchemy import select

from app.agents.foundry_client import LLMResult
from app.agents.tools import ToolCall, ToolExecutionError
from app.db.base import get_sessionmaker
from app.db.models import AuditLog

from .conftest import make_room


class _ToolCallingLLM:
    """First call requests one tool; once the tool result is fed back,
    returns final text with an immediate hand-off so the autonomous loop
    doesn't keep going past this one turn."""

    def __init__(self, tool_name: str = "web_search") -> None:
        self._tool_name = tool_name

    async def complete(self, *, agent_key, system_prompt, turns, tools=None):
        already_used = any(t.tool_results for t in turns)
        if not already_used:
            return LLMResult(
                text="",
                tool_calls=[ToolCall(id="call-1", name=self._tool_name, arguments={"query": "x"})],
            )
        return LLMResult(
            text=f"[{agent_key}] final answer HANDOFF_TO_HUMAN",
            input_tokens=3,
            output_tokens=2,
        )


class _AlwaysToolCallingLLM:
    """Always requests a tool, never producing final text on its own —
    exercises the per-turn tool-round cap."""

    def __init__(self) -> None:
        self.calls_with_tools = 0
        self.calls_without_tools = 0

    async def complete(self, *, agent_key, system_prompt, turns, tools=None):
        if tools:
            self.calls_with_tools += 1
            return LLMResult(
                text="",
                tool_calls=[
                    ToolCall(id=f"call-{self.calls_with_tools}", name="web_search", arguments={"query": "x"})
                ],
            )
        self.calls_without_tools += 1
        return LLMResult(text="final forced answer", input_tokens=1, output_tokens=1)


class _FailingToolRunner:
    async def run(self, name, arguments, ctx):
        raise ToolExecutionError("simulated tool failure")


def test_tool_round_trip_records_invocations_and_final_text(client):
    room = make_room(client, "ToolLoopBank1")
    client.app.state.orchestrator._llm = _ToolCallingLLM()

    resp = client.post(
        f"/api/rooms/{room['id']}/messages",
        json={"content": "@fce use tools please"},
    )
    assert resp.status_code == 200
    body = resp.json()
    agent_msg = next(m for m in body["messages"] if m["sender_type"] == "agent")
    assert "final answer" in agent_msg["content"]
    assert agent_msg["tool_invocations"] == [{"tool": "web_search", "query": "x"}]


def test_tool_round_trip_does_not_consume_extra_cycle(client):
    room = make_room(client, "ToolLoopBank2")
    client.app.state.orchestrator._llm = _ToolCallingLLM()

    resp = client.post(
        f"/api/rooms/{room['id']}/messages",
        json={"content": "use tools please"},
    )
    assert resp.status_code == 200
    assert resp.json()["cycles_used"] == 1


def test_tool_execution_failure_feeds_error_back_without_pausing_room(client):
    room = make_room(client, "ToolLoopBank3")
    client.app.state.orchestrator._llm = _ToolCallingLLM()
    client.app.state.orchestrator._tool_runner = _FailingToolRunner()

    resp = client.post(
        f"/api/rooms/{room['id']}/messages",
        json={"content": "@fce use tools please"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["room_status"] == "active"
    agent_msg = next(m for m in body["messages"] if m["sender_type"] == "agent")
    assert "final answer" in agent_msg["content"]

    async def fetch_audit():
        async with get_sessionmaker()() as session:
            result = await session.execute(
                select(AuditLog).where(AuditLog.action == "tool_invoked")
            )
            return result.scalars().all()

    rows = client.portal.call(fetch_audit)
    assert len(rows) == 1
    assert rows[0].detail["success"] is False


def test_runaway_tool_loop_is_capped_then_forced_to_final_text(client):
    room = make_room(client, "ToolLoopBank4")
    fake = _AlwaysToolCallingLLM()
    client.app.state.orchestrator._llm = fake

    resp = client.post(
        f"/api/rooms/{room['id']}/messages",
        json={"content": "@fce use tools please"},
    )
    assert resp.status_code == 200
    max_rounds = client.app.state.settings.max_tool_rounds
    assert fake.calls_with_tools == max_rounds
    assert fake.calls_without_tools == 1
    agent_msg = next(m for m in resp.json()["messages"] if m["sender_type"] == "agent")
    assert agent_msg["content"] == "final forced answer"
```

- [ ] **Step 5: Run the tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_agent_tools_loop.py -v`
Expected: FAIL — `TypeError: Orchestrator.__init__() missing 2 required keyword-only arguments: 'secret_provider' and 'google_oauth'` (raised during app startup in the `client` fixture) or, once that's stubbed, `AttributeError`/`KeyError` from `LLMResult.tool_calls` not being consumed anywhere yet.

- [ ] **Step 6: Wire the tool loop into `Orchestrator`**

Edit `backend/app/agents/orchestrator.py`. Replace the imports (currently lines 17-32):

```python
from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict
from typing import Protocol

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..db.models import (
    AgentGlobalConfig,
    AgentSkill,
    Message,
    Room,
    RoomAgent,
    RoomSkillOverride,
    RoomToolOverride,
)
from ..services.google_oauth import GoogleOAuthService
from ..services.secrets import SecretProvider
from .foundry_client import ChatTurn, LLMBackend, LLMError, ToolCall, ToolResult, ToolSpec
from .profiles import AGENT_KEYS, DATA_EXPERT_KEY, DISPLAY_NAMES, FCE_KEY
from .prompt_compiler import SkillSection, compile_system_prompt, parse_mention
from .tools import TOOL_REGISTRY, ToolContext, ToolExecutionError, ToolRunner
```

Replace `Orchestrator.__init__` (currently lines 73-79):

```python
    def __init__(
        self,
        settings: Settings,
        llm: LLMBackend,
        broker: RealtimeBroker,
        *,
        secret_provider: SecretProvider,
        google_oauth: GoogleOAuthService,
    ) -> None:
        self._settings = settings
        self._llm = llm
        self._broker = broker
        self._secret_provider = secret_provider
        self._google_oauth = google_oauth
        self._tool_runner = ToolRunner()
        self._room_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
```

- [ ] **Step 7: Add `_enabled_tools`, `_run_tool_loop`, `_log_tool_invocation`**

In `backend/app/agents/orchestrator.py`, add these three methods immediately before `compiled_prompt` (currently starting at line 345):

```python
    # ------------------------------------------------------------------
    # Tool calling: a bounded LLM<->tool round-trip within one turn
    # ------------------------------------------------------------------
    async def _enabled_tools(
        self, session: AsyncSession, room: Room, agent_key: str
    ) -> list[ToolSpec]:
        overrides = await session.execute(
            select(RoomToolOverride.tool_name).where(RoomToolOverride.room_id == room.id)
        )
        disabled = set(overrides.scalars().all())
        return [
            ToolSpec(name=t.name, description=t.description, parameters=t.parameters)
            for t in TOOL_REGISTRY.values()
            if agent_key in t.default_agents and t.name not in disabled
        ]

    async def _log_tool_invocation(
        self,
        session: AsyncSession,
        room: Room,
        agent_key: str,
        call: ToolCall,
        *,
        success: bool,
    ) -> None:
        query = str(call.arguments.get("query", ""))[:200]
        session.add(
            AuditLog(
                room_id=room.id,
                actor=f"agent:{agent_key}",
                action="tool_invoked",
                detail={
                    "tool_name": call.name,
                    "agent_key": agent_key,
                    "query": query,
                    "success": success,
                },
            )
        )
        await session.commit()

    async def _run_tool_loop(
        self,
        session: AsyncSession,
        room: Room,
        agent_key: str,
        system_prompt: str,
        turns: list[ChatTurn],
    ) -> tuple["LLMResultLike", list[dict]]:
        """Bounded LLM<->tool round-trip. Returns the final (tools-less)
        result plus every invocation made along the way, for
        Message.tool_invocations.

        A round-trip never claims an additional cycle — the caller already
        claimed exactly one for this turn before calling in.
        """
        tools = await self._enabled_tools(session, room, agent_key)
        invocations: list[dict] = []

        for _ in range(self._settings.max_tool_rounds):
            result = await self._llm.complete(
                agent_key=agent_key,
                system_prompt=system_prompt,
                turns=turns,
                tools=tools or None,
            )
            if not result.tool_calls:
                return result, invocations

            turns = turns + [
                ChatTurn(role="assistant", content=result.text, tool_calls=result.tool_calls)
            ]
            tool_results: list[ToolResult] = []
            ctx = ToolContext(
                session=session,
                room=room,
                settings=self._settings,
                secret_provider=self._secret_provider,
                google_oauth=self._google_oauth,
            )
            for call in result.tool_calls:
                query = str(call.arguments.get("query", ""))
                try:
                    output = await self._tool_runner.run(call.name, call.arguments, ctx)
                    tool_results.append(ToolResult(tool_call_id=call.id, content=output))
                    invocations.append({"tool": call.name, "query": query})
                    await self._log_tool_invocation(session, room, agent_key, call, success=True)
                except ToolExecutionError as exc:
                    tool_results.append(
                        ToolResult(tool_call_id=call.id, content=str(exc), is_error=True)
                    )
                    invocations.append({"tool": call.name, "query": query, "success": False})
                    await self._log_tool_invocation(session, room, agent_key, call, success=False)
            turns = turns + [ChatTurn(role="user", content="", tool_results=tool_results)]

        final = await self._llm.complete(
            agent_key=agent_key, system_prompt=system_prompt, turns=turns, tools=None
        )
        return final, invocations
```

Add `AuditLog` to the models import added in Step 6 (it's not yet imported in `orchestrator.py` — extend the import block from Step 6 to include it):

```python
from ..db.models import (
    AgentGlobalConfig,
    AgentSkill,
    AuditLog,
    Message,
    Room,
    RoomAgent,
    RoomSkillOverride,
    RoomToolOverride,
)
```

- [ ] **Step 8: Wire `_run_tool_loop` into both turn paths**

In `_run_mention_reply` (currently lines 179-206), replace:

```python
        try:
            result = await self._llm.complete(
                agent_key=agent_key, system_prompt=system_prompt, turns=turns
            )
        except LLMError as exc:
            fail_msg = await self._fail_turn(session, room, agent_key, exc)
            return [fail_msg]
        msg = Message(
            room_id=room.id,
            sender_type="agent",
            sender_name=DISPLAY_NAMES[agent_key],
            agent_key=agent_key,
            content=result.text,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
```

with:

```python
        try:
            result, invocations = await self._run_tool_loop(
                session, room, agent_key, system_prompt, turns
            )
        except LLMError as exc:
            fail_msg = await self._fail_turn(session, room, agent_key, exc)
            return [fail_msg]
        msg = Message(
            room_id=room.id,
            sender_type="agent",
            sender_name=DISPLAY_NAMES[agent_key],
            agent_key=agent_key,
            content=result.text,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            tool_invocations=invocations or None,
        )
```

In `run_autonomous_loop` (currently lines 211-261), apply the identical replacement (same `try/except` block shape, this time also carrying `cycle_number=cycle`):

```python
            try:
                result, invocations = await self._run_tool_loop(
                    session, room, speaker, system_prompt, turns
                )
            except LLMError as exc:
                fail_msg = await self._fail_turn(session, room, speaker, exc)
                created.append(fail_msg)
                break

            msg = Message(
                room_id=room.id,
                sender_type="agent",
                sender_name=DISPLAY_NAMES[speaker],
                agent_key=speaker,
                cycle_number=cycle,
                content=result.text,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                tool_invocations=invocations or None,
            )
```

- [ ] **Step 9: Include `tool_invocations` in the WS message event**

In `_msg_event` (currently lines 446-463), add the field:

```python
    @staticmethod
    def _msg_event(m: Message) -> dict:
        return {
            "type": "message_created",
            "message": {
                "id": m.id,
                "room_id": m.room_id,
                "sender_type": m.sender_type,
                "sender_name": m.sender_name,
                "agent_key": m.agent_key,
                "mention_target": m.mention_target,
                "cycle_number": m.cycle_number,
                "content": m.content,
                "input_tokens": m.input_tokens,
                "output_tokens": m.output_tokens,
                "tool_invocations": m.tool_invocations,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            },
        }
```

- [ ] **Step 10: Wire `Orchestrator`'s new dependencies in `main.py`**

Edit `backend/app/main.py`. Replace the lifespan body from `secret_provider = build_secret_provider(settings)` through the `google_oauth` line (currently within lines 37-49):

```python
    secret_provider = build_secret_provider(settings)
    blob_provider = build_blob_provider(settings, secret_provider)
    manager, broker = build_realtime(settings, secret_provider)
    llm = await build_llm_backend(settings, secret_provider)
    google_oauth = GoogleOAuthService(settings, secret_provider)

    app.state.settings = settings
    app.state.secret_provider = secret_provider
    app.state.blob_provider = blob_provider
    app.state.manager = manager
    app.state.broker = broker
    app.state.orchestrator = Orchestrator(
        settings, llm, broker, secret_provider=secret_provider, google_oauth=google_oauth
    )
    app.state.skills_service = SkillsService(blob_provider)
    app.state.google_oauth = google_oauth
    app.state.entra_validator = (
        EntraTokenValidator(settings) if settings.auth_mode == "entra" else None
    )
```

- [ ] **Step 11: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_agent_tools_loop.py -v`
Expected: PASS (4 tests).

Then run the full backend suite to confirm nothing regressed:

Run: `cd backend && .venv/bin/pytest -q`
Expected: all tests PASS.

- [ ] **Step 12: Commit**

```bash
git add backend/app/db/models.py backend/app/schemas.py backend/app/agents/orchestrator.py \
  backend/app/main.py backend/alembic/versions/d581a3c9f0e2_add_message_tool_invocations.py \
  backend/tests/test_agent_tools_loop.py
git commit -m "feat: wire tool-calling round-trip into the orchestrator turn loop"
```

---

### Task 6: Frontend data layer (types + API client)

**Files:**
- Modify: `frontend/src/types.ts:143-225` (after `SkillToggleUpdate`, `MessageOut`, `RoomWsEvent`)
- Modify: `frontend/src/api.ts:1-17` (imports), `163-186` (after skills section)

**Interfaces:**
- Consumes: `ToolOut`, `ToolToggleUpdate` shapes (Task 2); `MessageOut.tool_invocations` (Task 5); `agent_tool_toggled` WS event (Task 2).
- Produces: `ToolOut`, `ToolToggleUpdate`, `ToolInvocation`, `WsAgentToolToggled` TS interfaces; `listTools`, `toggleTool` API functions. Task 7 consumes `ToolOut`/`listTools`/`toggleTool`. Task 8 consumes `ToolInvocation`/`WsAgentToolToggled`.

- [ ] **Step 1: Add the new types**

Edit `frontend/src/types.ts`. After `SkillToggleUpdate` (currently lines 155-157):

```typescript
export interface SkillToggleUpdate {
  enabled: boolean;
}

// --- Tools --------------------------------------------------------------------
export interface ToolOut {
  name: string;
  description: string;
  enabled: boolean;
}

export interface ToolToggleUpdate {
  enabled: boolean;
}

export interface ToolInvocation {
  tool: string;
  query: string;
  success?: boolean;
}
```

Add `tool_invocations` to `MessageOut` (currently lines 95-107):

```typescript
export interface MessageOut {
  id: string;
  room_id: string;
  sender_type: SenderType;
  sender_name: string;
  agent_key: string | null;
  mention_target: string | null;
  cycle_number: number | null;
  content: string;
  input_tokens: number | null;
  output_tokens: number | null;
  tool_invocations: ToolInvocation[] | null;
  created_at: string;
}
```

Add the WS event type and extend the union (currently lines 198-225):

```typescript
export interface WsAgentSkillToggled {
  type: "agent_skill_toggled";
  room_id: string;
  agent_key: string;
  skill_id: string;
  enabled: boolean;
}

export interface WsAgentToolToggled {
  type: "agent_tool_toggled";
  room_id: string;
  agent_key: string;
  tool_name: string;
  enabled: boolean;
}

export interface WsDriveLinked {
  type: "drive_linked";
  google_folder_id?: string;
  google_folder_name?: string;
}

export interface WsDriveConnected {
  type: "drive_connected";
}

export type RoomWsEvent =
  | WsMessageCreated
  | WsAgentThinking
  | WsRoomPaused
  | WsRoomResumed
  | WsSkillAdded
  | WsAgentInstructionsUpdated
  | WsAgentSkillToggled
  | WsAgentToolToggled
  | WsDriveLinked
  | WsDriveConnected;
```

- [ ] **Step 2: Add the API client functions**

Edit `frontend/src/api.ts`. Add `ToolOut` to the type imports (currently lines 3-16):

```typescript
import type {
  AgentConfigOut,
  AgentUsageOut,
  CompiledPromptOut,
  GDriveAuthorizeOut,
  GDriveStatusOut,
  InviteCreateOut,
  MessageOut,
  PostMessageResult,
  RoomAgentDetailOut,
  RoomMemberOut,
  RoomOut,
  SkillOut,
  ToolOut,
} from "./types";
```

Add after the Skills section (currently ending at line 185):

```typescript
// --- Tools ------------------------------------------------------------------------
export const listTools = (roomId: string, agentKey: string) =>
  request<ToolOut[]>(`/api/rooms/${roomId}/agents/${agentKey}/tools`);

export const toggleTool = (roomId: string, agentKey: string, toolName: string, enabled: boolean) =>
  request<ToolOut>(`/api/rooms/${roomId}/agents/${agentKey}/tools/${toolName}`, {
    method: "PUT",
    body: JSON.stringify({ enabled }),
  });
```

- [ ] **Step 3: Type-check**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types.ts frontend/src/api.ts
git commit -m "feat: add frontend types and API client for tools"
```

---

### Task 7: `AgentToolsTab.tsx` + `AgentDetailPanel` wiring

**Files:**
- Create: `frontend/src/components/AgentToolsTab.tsx`
- Modify: `frontend/src/components/AgentDetailPanel.tsx`

**Interfaces:**
- Consumes: `listTools`, `toggleTool` (Task 6); `ToolOut`, `AgentKey` (Task 6); `toastError` (`../toast`, existing).

- [ ] **Step 1: Create `AgentToolsTab.tsx`**

Modeled directly on `AgentSkillsTab.tsx` — list + toggle, no upload (nothing to upload; tools are built-in):

```tsx
import { useEffect, useState } from "react";
import { listTools, toggleTool } from "../api";
import type { AgentKey, ToolOut } from "../types";
import { toastError } from "../toast";

export default function AgentToolsTab({
  roomId,
  agentKey,
}: {
  roomId: string;
  agentKey: AgentKey;
}) {
  const [tools, setTools] = useState<ToolOut[] | null>(null);
  const [togglingName, setTogglingName] = useState<string | null>(null);

  useEffect(() => {
    setTools(null);
    listTools(roomId, agentKey)
      .then(setTools)
      .catch((err) => {
        setTools([]);
        toastError(err, "Failed to load tools");
      });
  }, [roomId, agentKey]);

  const toggle = async (tool: ToolOut) => {
    setTogglingName(tool.name);
    try {
      const updated = await toggleTool(roomId, agentKey, tool.name, !tool.enabled);
      setTools((prev) => (prev ?? []).map((t) => (t.name === updated.name ? updated : t)));
    } catch (err) {
      toastError(err, "Failed to toggle tool");
    } finally {
      setTogglingName(null);
    }
  };

  return (
    <div className="agent-tools-tab">
      <h4 className="skills-heading">Tools for this agent</h4>
      {tools === null && <div className="muted">Loading…</div>}
      {tools !== null && tools.length === 0 && (
        <div className="muted">No tools available for this agent.</div>
      )}
      <ul className="skill-list">
        {(tools ?? []).map((t) => (
          <li key={t.name} className={`skill-item ${t.enabled ? "" : "skill-item-disabled"}`}>
            <span className="skill-name">{t.name}</span>
            <span className="muted">{t.description}</span>
            <button
              className="btn btn-small skill-toggle-btn"
              onClick={() => void toggle(t)}
              disabled={togglingName === t.name}
            >
              {togglingName === t.name ? "…" : t.enabled ? "Disable" : "Enable"}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

(Reuses the existing `.skill-list`/`.skill-item`/`.skill-toggle-btn` CSS classes already defined for `AgentSkillsTab` — no new stylesheet rules needed here.)

- [ ] **Step 2: Wire the tab into `AgentDetailPanel.tsx`**

Replace the full contents of `frontend/src/components/AgentDetailPanel.tsx`:

```tsx
import { useState } from "react";
import type { RoomAgentOut } from "../types";
import AgentInstructionsTab from "./AgentInstructionsTab";
import AgentSkillsTab from "./AgentSkillsTab";
import AgentToolsTab from "./AgentToolsTab";
import AgentUsageTab from "./AgentUsageTab";

type Tab = "instructions" | "skills" | "usage" | "tools" | "mcps" | "memory";

const TABS: { key: Tab; label: string; comingSoon?: boolean }[] = [
  { key: "instructions", label: "Instructions" },
  { key: "skills", label: "Skills" },
  { key: "usage", label: "Usage" },
  { key: "tools", label: "Tools" },
  { key: "mcps", label: "MCPs", comingSoon: true },
  { key: "memory", label: "Memory", comingSoon: true },
];

export default function AgentDetailPanel({
  roomId,
  agent,
  onBack,
}: {
  roomId: string;
  agent: RoomAgentOut;
  onBack: () => void;
}) {
  const [tab, setTab] = useState<Tab>("instructions");

  return (
    <div className="agent-detail-panel">
      <div className="agent-detail-header">
        <button className="btn-icon" onClick={onBack} aria-label="Back to agent list">
          ←
        </button>
        <span className={`agent-chip agent-chip-${agent.agent_key}`}>{agent.display_name}</span>
      </div>

      <nav className="agent-detail-tabs">
        {TABS.map((t) => (
          <button
            key={t.key}
            className={`nav-link ${tab === t.key ? "nav-active" : ""} ${
              t.comingSoon ? "agent-detail-tab-soon" : ""
            }`}
            onClick={() => !t.comingSoon && setTab(t.key)}
            disabled={t.comingSoon}
            title={t.comingSoon ? "Coming soon" : undefined}
          >
            {t.label}
            {t.comingSoon && <span className="agent-detail-tab-badge">soon</span>}
          </button>
        ))}
      </nav>

      <div className="agent-detail-tab-content">
        {tab === "instructions" && (
          <AgentInstructionsTab roomId={roomId} agentKey={agent.agent_key} />
        )}
        {tab === "skills" && <AgentSkillsTab roomId={roomId} agentKey={agent.agent_key} />}
        {tab === "usage" && <AgentUsageTab roomId={roomId} agentKey={agent.agent_key} />}
        {tab === "tools" && <AgentToolsTab roomId={roomId} agentKey={agent.agent_key} />}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Type-check**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/AgentToolsTab.tsx frontend/src/components/AgentDetailPanel.tsx
git commit -m "feat: add Tools tab to Agents Skills detail panel"
```

---

### Task 8: Chat "Sources" disclosure, WS toast, final manual verification

**Files:**
- Modify: `frontend/src/components/ChatThread.tsx`
- Modify: `frontend/src/components/RoomView.tsx` (WS switch, currently lines 89-111)
- Modify: `frontend/src/styles.css:859` (before `.msg-usage`)

**Interfaces:**
- Consumes: `MessageOut.tool_invocations`, `WsAgentToolToggled` (Task 6).

- [ ] **Step 1: Add the Sources disclosure to `ChatThread.tsx`**

Edit `frontend/src/components/ChatThread.tsx`. Insert right after the closing `</div>` of `msg-content` and before the `TokenUsage` conditional (currently around lines 108-112):

```tsx
              <div className="msg-content">
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                  {msg.content}
                </ReactMarkdown>
              </div>
              {msg.tool_invocations && msg.tool_invocations.length > 0 && (
                <details className="msg-sources">
                  <summary>Sources ({msg.tool_invocations.length})</summary>
                  <ul>
                    {msg.tool_invocations.map((inv, i) => (
                      <li key={i}>
                        {inv.tool === "web_search" ? "🔍" : "📁"} {inv.tool}: "{inv.query}"
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              {msg.sender_type === "agent" &&
                (msg.input_tokens !== null || msg.output_tokens !== null) && (
                  <TokenUsage input={msg.input_tokens ?? 0} output={msg.output_tokens ?? 0} />
                )}
```

- [ ] **Step 2: Add the CSS**

Edit `frontend/src/styles.css`, immediately before `.msg-usage` (currently line 859):

```css
.msg-sources {
  margin-top: 0.35rem;
  font-size: 0.8rem;
  color: var(--text-muted);
}

.msg-sources summary {
  cursor: pointer;
  user-select: none;
}

.msg-sources ul {
  margin: 0.25rem 0 0 1rem;
  padding: 0;
}

.msg-usage {
```

(Only the three new rules are added — `.msg-usage {` on the last line is the existing line, shown for anchor context; do not duplicate it.)

- [ ] **Step 3: Add the WS toast for `agent_tool_toggled`**

Edit `frontend/src/components/RoomView.tsx`. Insert right after the `case "agent_skill_toggled":` block (currently lines 98-106):

```tsx
        case "agent_skill_toggled":
          pushToast(
            "info",
            `Skill ${event.enabled ? "enabled" : "disabled"} for ${agentDisplayName(
              roomRef.current,
              event.agent_key,
            )}`,
          );
          break;
        case "agent_tool_toggled":
          pushToast(
            "info",
            `Tool ${event.enabled ? "enabled" : "disabled"} for ${agentDisplayName(
              roomRef.current,
              event.agent_key,
            )}`,
          );
          break;
```

- [ ] **Step 4: Type-check**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 5: Manual verification**

Run the backend (`CABINET_LLM_MODE=mock`, the default) and frontend dev servers per this repo's usual local setup (`cd backend && .venv/bin/uvicorn app.main:app --reload`, `cd frontend && npm run dev`), then in the browser:

- Create a room, open it, and send `@FCE please use tools to check something` — confirm the reply renders normally and a collapsed `Sources (1)` disclosure appears under FCE's bubble; expanding it shows `🔍 web_search: "mock query"`.
- Open the room's "Agents Skills" tab → click into an agent → click the "Tools" tab (no longer disabled) → confirm `drive_search` and `web_search` are listed, both enabled; click "Disable" on one, confirm the button flips to "Enable" and a toast appears; open the same room in a second browser tab and confirm the toast also appears there.
- Send a normal message without "use tools" and confirm no Sources disclosure appears (mock trigger phrase only fires the scripted path intentionally).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/ChatThread.tsx frontend/src/components/RoomView.tsx frontend/src/styles.css
git commit -m "feat: show tool-call sources under chat replies, toast on tool toggle"
```
