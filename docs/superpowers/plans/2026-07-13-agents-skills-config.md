# Agents Skills — Room-Level Per-Agent Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the room's "Upload skill" button into a full "Agents Skills" tab where a room member clicks into Data Expert or FCE and configures Instructions, Skills (with enable/disable), and Usage — with disabled placeholder tabs for Tools/MCPs/Memory.

**Architecture:** Two new pieces of room-scoped state (`RoomAgent.instructions`, `room_skill_overrides`) feed into `Orchestrator.compiled_prompt()`'s existing append-only layering. Four new/changed REST endpoints (all `require_room_member`-gated) expose them; two new WS events keep multiple open clients in sync. The frontend replaces the single-purpose `SkillUploadDialog` modal with an in-room tab (`Chat` / `Agents Skills`) that drills from an agent list into a tabbed detail panel.

**Tech Stack:** FastAPI + SQLAlchemy (async) + Alembic + pytest (backend), React + TypeScript + Vite (frontend, no test runner — verified via `tsc` + manual browser check).

## Global Constraints

- All new room-scoped endpoints (instructions, skill toggle, usage) use `require_room_member` — never `require_admin`. Any room member (owner or not) may call them.
- `agent_key` path params are validated against `AGENT_KEYS` (400 if unrecognized), matching the existing convention in `skills.py`/`admin.py`.
- Every mutation is audit-logged via the existing `AuditLog` table.
- `instructions` accepts and defaults to `""` — never validated as non-empty (unlike `system_prompt`).
- Toggling a skill never deletes it or mutates the shared `AgentSkill` row — always via the room-scoped `room_skill_overrides` table (row presence = disabled).
- Compiled-prompt layering order is fixed and tested: baseline → enabled skills → room enrichment → per-agent instructions.
- New Alembic migrations must chain from the current head (`cca35f727fa3`) and pass the existing round-trip test (`backend/tests/test_migrations.py`).
- No automated frontend test runner exists in this repo (`frontend/package.json` has no test script). Frontend tasks are verified by `npx tsc --noEmit` plus manual verification via `npm run dev` — do not introduce a new test framework as part of this plan.

---

### Task 1: Per-room, per-agent Instructions (backend)

**Files:**
- Modify: `backend/app/db/models.py:85-95` (`RoomAgent` class)
- Create: `backend/alembic/versions/f3a8b2c9d1e4_add_room_agent_instructions.py`
- Modify: `backend/app/schemas.py:27-30` (after `RoomAgentOut`)
- Modify: `backend/app/api/rooms.py:1-30` (imports), insert new endpoints before line 301 (`get_compiled_prompt`)
- Modify: `backend/tests/conftest.py` (add shared `drain_until` helper)
- Modify: `backend/tests/test_websocket.py` (use the shared helper instead of a local one)
- Create: `backend/tests/test_room_agent_instructions.py`

**Interfaces:**
- Consumes: `require_room_member`, `get_broker`, `RealtimeBroker` (already imported in `rooms.py`); `AGENT_KEYS` (already imported).
- Produces: `RoomAgentDetailOut` schema (`agent_key`, `display_name`, `system_prompt`, `instructions`) and `InstructionsUpdate` schema (`instructions: str`), used by Task 3 (orchestrator) and Task 5 (frontend types).

- [ ] **Step 1: Add the `instructions` column to `RoomAgent`**

Edit `backend/app/db/models.py`, in the `RoomAgent` class:

```python
class RoomAgent(Base):
    __tablename__ = "room_agents"
    __table_args__ = (UniqueConstraint("room_id", "agent_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"))
    agent_key: Mapped[str] = mapped_column(String(32))
    display_name: Mapped[str] = mapped_column(String(128))
    instructions: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    room: Mapped[Room] = relationship(back_populates="agents")
```

(Only the `instructions` line is new, inserted after `display_name`.)

- [ ] **Step 2: Write the Alembic migration**

Create `backend/alembic/versions/f3a8b2c9d1e4_add_room_agent_instructions.py`:

```python
"""add room_agents.instructions

Revision ID: f3a8b2c9d1e4
Revises: cca35f727fa3
Create Date: 2026-07-13 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f3a8b2c9d1e4'
down_revision: Union[str, Sequence[str], None] = 'cca35f727fa3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'room_agents',
        sa.Column('instructions', sa.Text(), nullable=False, server_default=''),
    )


def downgrade() -> None:
    op.drop_column('room_agents', 'instructions')
```

- [ ] **Step 3: Run the migration round-trip test to confirm it applies cleanly**

Run: `cd backend && .venv/bin/pytest tests/test_migrations.py -v`
Expected: PASS (upgrade head + downgrade base both succeed against the new revision).

- [ ] **Step 4: Add the new schemas**

Edit `backend/app/schemas.py`, immediately after the `RoomAgentOut` class (currently lines 27-30):

```python
class RoomAgentOut(BaseModel):
    agent_key: str
    display_name: str


class RoomAgentDetailOut(BaseModel):
    agent_key: str
    display_name: str
    system_prompt: str
    instructions: str


class InstructionsUpdate(BaseModel):
    instructions: str = ""
```

- [ ] **Step 5: Add the shared WS-draining test helper to conftest**

Edit `backend/tests/conftest.py`, add this function (near `make_room`, after its definition):

```python
def drain_until(ws, event_type: str, limit: int = 40) -> dict:
    for _ in range(limit):
        event = ws.receive_json()
        if event.get("type") == event_type:
            return event
    raise AssertionError(f"never received {event_type}")
```

- [ ] **Step 6: Move `test_websocket.py` onto the shared helper**

Replace the full contents of `backend/tests/test_websocket.py` with:

```python
"""Real-time stream: room events fan out to connected WebSocket clients."""
from .conftest import drain_until, make_room


def test_ws_receives_message_and_pause_events(client):
    room = make_room(client, "WsBank")
    with client.websocket_connect(f"/ws/rooms/{room['id']}") as ws:
        client.post(f"/api/rooms/{room['id']}/messages", json={"content": "go"})

        first = drain_until(ws, "message_created")
        assert first["message"]["sender_type"] == "human"

        paused = drain_until(ws, "room_paused")
        assert paused["cycles_used"] == 6
        assert paused["cycle_limit"] == 6


def test_ws_receives_agent_thinking_indicator(client):
    room = make_room(client, "WsBank2")
    with client.websocket_connect(f"/ws/rooms/{room['id']}") as ws:
        client.post(
            f"/api/rooms/{room['id']}/messages",
            json={"content": "@FCE quick check"},
        )
        thinking = drain_until(ws, "agent_thinking")
        assert thinking["agent_key"] == "fce"


def test_ws_cleans_up_on_ungracious_disconnect(client):
    """Any exit path from the receive loop — not just WebSocketDisconnect —
    must still deregister the connection (Design 04 Lows)."""
    room = make_room(client, "WsCleanupBank")
    with client.websocket_connect(f"/ws/rooms/{room['id']}") as ws:
        manager = client.app.state.manager
        assert room["id"] in manager._rooms
    # Context manager exit closes the socket; the server's finally must run.
    manager = client.app.state.manager
    assert room["id"] not in manager._rooms
```

- [ ] **Step 7: Run the websocket tests to confirm the refactor didn't break anything**

Run: `cd backend && .venv/bin/pytest tests/test_websocket.py -v`
Expected: PASS (3 tests).

- [ ] **Step 8: Write the failing tests for the instructions endpoints**

Create `backend/tests/test_room_agent_instructions.py`:

```python
"""Per-room, per-agent instructions: optional, empty by default, room-member editable."""
from .conftest import drain_until, make_room


def test_instructions_empty_by_default(client):
    room = make_room(client, "InstructionsBank")
    resp = client.get(f"/api/rooms/{room['id']}/agents/fce")
    assert resp.status_code == 200
    body = resp.json()
    assert body["instructions"] == ""
    assert body["agent_key"] == "fce"
    assert "system_prompt" in body


def test_update_instructions_then_get_reflects_it(client):
    room = make_room(client, "InstructionsBank2")
    resp = client.put(
        f"/api/rooms/{room['id']}/agents/data_expert/instructions",
        json={"instructions": "Focus on SEPA Instant rails for this customer."},
    )
    assert resp.status_code == 200
    assert resp.json()["instructions"] == "Focus on SEPA Instant rails for this customer."

    fetched = client.get(f"/api/rooms/{room['id']}/agents/data_expert").json()
    assert fetched["instructions"] == "Focus on SEPA Instant rails for this customer."


def test_instructions_are_per_agent_not_shared(client):
    room = make_room(client, "InstructionsBank3")
    client.put(
        f"/api/rooms/{room['id']}/agents/data_expert/instructions",
        json={"instructions": "Data Expert only context."},
    )
    fce = client.get(f"/api/rooms/{room['id']}/agents/fce").json()
    assert fce["instructions"] == ""


def test_instructions_are_per_room_not_shared(client):
    room_a = make_room(client, "InstructionsBankA")
    room_b = make_room(client, "InstructionsBankB")
    client.put(
        f"/api/rooms/{room_a['id']}/agents/fce/instructions",
        json={"instructions": "Room A only."},
    )
    b_instructions = client.get(f"/api/rooms/{room_b['id']}/agents/fce").json()["instructions"]
    assert b_instructions == ""


def test_empty_instructions_payload_is_accepted(client):
    room = make_room(client, "InstructionsBank4")
    resp = client.put(
        f"/api/rooms/{room['id']}/agents/fce/instructions",
        json={"instructions": ""},
    )
    assert resp.status_code == 200
    assert resp.json()["instructions"] == ""


def test_unknown_agent_key_400(client):
    room = make_room(client, "InstructionsBank5")
    resp = client.get(f"/api/rooms/{room['id']}/agents/not-a-real-agent")
    assert resp.status_code == 400


def test_non_member_cannot_read_or_update_instructions(client):
    room = make_room(client, "InstructionsBank6")
    resp = client.get(
        f"/api/rooms/{room['id']}/agents/fce",
        headers={"X-User-Email": "outsider@bank.example"},
    )
    assert resp.status_code == 403

    resp2 = client.put(
        f"/api/rooms/{room['id']}/agents/fce/instructions",
        json={"instructions": "hijacked"},
        headers={"X-User-Email": "outsider@bank.example"},
    )
    assert resp2.status_code == 403


def test_ws_receives_agent_instructions_updated(client):
    room = make_room(client, "InstructionsBankWs")
    with client.websocket_connect(f"/ws/rooms/{room['id']}") as ws:
        client.put(
            f"/api/rooms/{room['id']}/agents/fce/instructions",
            json={"instructions": "Live update test."},
        )
        event = drain_until(ws, "agent_instructions_updated")
        assert event["agent_key"] == "fce"
        assert event["room_id"] == room["id"]
```

- [ ] **Step 9: Run the new tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_room_agent_instructions.py -v`
Expected: FAIL with 404s (routes don't exist yet).

- [ ] **Step 10: Implement the endpoints**

Edit `backend/app/api/rooms.py`. First, update the imports (lines 12-28):

```python
from ..agents.orchestrator import Orchestrator, RealtimeBroker
from ..agents.profiles import AGENT_KEYS, DISPLAY_NAMES
from ..config import get_settings
from ..db.base import get_session
from ..db.models import (
    AgentGlobalConfig,
    AuditLog,
    Message,
    Room,
    RoomAgent,
    RoomInvite,
    RoomMember,
)
from ..schemas import (
    CompiledPromptOut,
    InstructionsUpdate,
    InviteCreateOut,
    JoinRequest,
    RealtimeTokenOut,
    RoomAgentDetailOut,
    RoomAgentOut,
    RoomCreate,
    RoomLastMessageOut,
    RoomMemberOut,
    RoomOut,
)
from .deps import get_current_user_email, get_broker, get_orchestrator, require_room_member
```

Then insert this helper and these two endpoints directly before the existing `get_compiled_prompt` endpoint (before line 301):

```python
async def _get_agent_config_and_room_agent(
    session: AsyncSession, room_id: str, agent_key: str
) -> tuple[AgentGlobalConfig, RoomAgent]:
    if agent_key not in AGENT_KEYS:
        raise HTTPException(status_code=400, detail=f"unknown agent: {agent_key}")

    config = await session.get(AgentGlobalConfig, agent_key)
    if config is None:
        raise HTTPException(status_code=404, detail=f"unknown agent: {agent_key}")

    result = await session.execute(
        select(RoomAgent).where(
            RoomAgent.room_id == room_id, RoomAgent.agent_key == agent_key
        )
    )
    room_agent = result.scalar_one_or_none()
    if room_agent is None:
        raise HTTPException(status_code=404, detail="room not found")

    return config, room_agent


def _room_agent_detail_out(
    agent_key: str, config: AgentGlobalConfig, room_agent: RoomAgent
) -> RoomAgentDetailOut:
    return RoomAgentDetailOut(
        agent_key=agent_key,
        display_name=room_agent.display_name,
        system_prompt=config.system_prompt,
        instructions=room_agent.instructions,
    )


@router.get(
    "/{room_id}/agents/{agent_key}",
    response_model=RoomAgentDetailOut,
)
async def get_room_agent(
    room_id: str,
    agent_key: str,
    session: AsyncSession = Depends(get_session),
    _member: str = Depends(require_room_member),
) -> RoomAgentDetailOut:
    config, room_agent = await _get_agent_config_and_room_agent(session, room_id, agent_key)
    return _room_agent_detail_out(agent_key, config, room_agent)


@router.put(
    "/{room_id}/agents/{agent_key}/instructions",
    response_model=RoomAgentDetailOut,
)
async def update_room_agent_instructions(
    room_id: str,
    agent_key: str,
    payload: InstructionsUpdate,
    session: AsyncSession = Depends(get_session),
    broker: RealtimeBroker = Depends(get_broker),
    user_email: str = Depends(require_room_member),
) -> RoomAgentDetailOut:
    config, room_agent = await _get_agent_config_and_room_agent(session, room_id, agent_key)

    room_agent.instructions = payload.instructions
    session.add(
        AuditLog(
            room_id=room_id,
            actor=user_email,
            action="room_agent_instructions_updated",
            detail={"agent_key": agent_key},
        )
    )
    await session.commit()
    await broker.publish(
        room_id,
        {
            "type": "agent_instructions_updated",
            "room_id": room_id,
            "agent_key": agent_key,
        },
    )
    return _room_agent_detail_out(agent_key, config, room_agent)
```

- [ ] **Step 11: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_room_agent_instructions.py -v`
Expected: PASS (8 tests).

- [ ] **Step 12: Run the full backend suite to check for regressions**

Run: `cd backend && .venv/bin/pytest -v`
Expected: PASS (all existing + new tests).

- [ ] **Step 13: Commit**

```bash
git add backend/app/db/models.py backend/alembic/versions/f3a8b2c9d1e4_add_room_agent_instructions.py backend/app/schemas.py backend/app/api/rooms.py backend/tests/conftest.py backend/tests/test_websocket.py backend/tests/test_room_agent_instructions.py
git commit -m "feat: add per-room per-agent instructions endpoints"
```

---

### Task 2: Per-room skill enable/disable toggle (backend)

**Files:**
- Modify: `backend/app/db/models.py` (new `RoomSkillOverride` class, insert after `AgentSkill`)
- Create: `backend/alembic/versions/b7d4e1a9c3f2_add_room_skill_overrides.py`
- Modify: `backend/app/schemas.py` (add `SkillToggleUpdate`; add `enabled` field to `SkillOut`)
- Modify: `backend/app/api/skills.py` (imports, `list_skills`, new `toggle_skill` endpoint)
- Create: `backend/tests/test_skill_toggle.py`

**Interfaces:**
- Consumes: `require_room_member`, `get_broker`, `RealtimeBroker`, `AuditLog` (new import into `skills.py`).
- Produces: `RoomSkillOverride` model and `SkillOut.enabled: bool`, consumed by Task 3 (orchestrator filtering) and Task 5 (frontend types).

- [ ] **Step 1: Add the `RoomSkillOverride` model**

Edit `backend/app/db/models.py`, insert this class immediately after the `AgentSkill` class (which ends right before `class AuditLog(Base):`):

```python
class RoomSkillOverride(Base):
    """Room-scoped disable toggle for a skill (global or room-owned).

    Row presence means "disabled in this room" — this keeps a global skill's
    on/off state scoped to the room where a member toggled it, since
    AgentSkill.room_id is NULL (shared) for global skills.
    """

    __tablename__ = "room_skill_overrides"

    room_id: Mapped[str] = mapped_column(
        ForeignKey("rooms.id", ondelete="CASCADE"), primary_key=True
    )
    skill_id: Mapped[str] = mapped_column(
        ForeignKey("agent_skills.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
```

- [ ] **Step 2: Write the Alembic migration**

Create `backend/alembic/versions/b7d4e1a9c3f2_add_room_skill_overrides.py`:

```python
"""add room_skill_overrides

Revision ID: b7d4e1a9c3f2
Revises: f3a8b2c9d1e4
Create Date: 2026-07-13 12:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7d4e1a9c3f2'
down_revision: Union[str, Sequence[str], None] = 'f3a8b2c9d1e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'room_skill_overrides',
        sa.Column('room_id', sa.String(length=36), nullable=False),
        sa.Column('skill_id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['room_id'], ['rooms.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['skill_id'], ['agent_skills.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('room_id', 'skill_id'),
    )


def downgrade() -> None:
    op.drop_table('room_skill_overrides')
```

- [ ] **Step 3: Run the migration round-trip test**

Run: `cd backend && .venv/bin/pytest tests/test_migrations.py -v`
Expected: PASS.

- [ ] **Step 4: Add the new schema and extend `SkillOut`**

Edit `backend/app/schemas.py`. Change the existing `SkillOut` class:

```python
class SkillOut(BaseModel):
    id: str
    room_id: str | None
    agent_key: str
    skill_name: str
    skill_type: str
    blob_path: str
    created_at: datetime
    enabled: bool = True
```

And add, directly below it:

```python
class SkillToggleUpdate(BaseModel):
    enabled: bool
```

- [ ] **Step 5: Write the failing tests**

Create `backend/tests/test_skill_toggle.py`:

```python
"""Per-room skill enable/disable toggle — global skills stay scoped per room."""
from .conftest import drain_until, make_room

MD_SKILL = b"# Cross-Border Rule\nFlag any transfer above EUR 50k.\n"


def _upload_skill(client, room_id: str, agent_key: str = "fce") -> dict:
    resp = client.post(
        f"/api/rooms/{room_id}/agents/{agent_key}/skills",
        files={"file": ("rule.md", MD_SKILL, "text/markdown")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_new_skill_defaults_enabled(client):
    room = make_room(client, "ToggleBank1")
    _upload_skill(client, room["id"])
    listed = client.get(f"/api/rooms/{room['id']}/agents/fce/skills").json()
    assert listed[0]["enabled"] is True


def test_toggle_off_excludes_from_compiled_prompt(client):
    room = make_room(client, "ToggleBank2")
    skill = _upload_skill(client, room["id"])

    resp = client.put(
        f"/api/rooms/{room['id']}/agents/fce/skills/{skill['id']}",
        json={"enabled": False},
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

    listed = client.get(f"/api/rooms/{room['id']}/agents/fce/skills").json()
    assert listed[0]["enabled"] is False

    compiled = client.get(
        f"/api/rooms/{room['id']}/agents/fce/compiled-prompt"
    ).json()["compiled_prompt"]
    assert "Cross-Border Rule" not in compiled


def test_toggle_back_on_restores_it(client):
    room = make_room(client, "ToggleBank3")
    skill = _upload_skill(client, room["id"])
    client.put(
        f"/api/rooms/{room['id']}/agents/fce/skills/{skill['id']}",
        json={"enabled": False},
    )
    resp = client.put(
        f"/api/rooms/{room['id']}/agents/fce/skills/{skill['id']}",
        json={"enabled": True},
    )
    assert resp.json()["enabled"] is True
    compiled = client.get(
        f"/api/rooms/{room['id']}/agents/fce/compiled-prompt"
    ).json()["compiled_prompt"]
    assert "Cross-Border Rule" in compiled


def test_disabling_global_skill_in_one_room_does_not_affect_another(client):
    room_a = make_room(client, "ToggleBankA")
    room_b = make_room(client, "ToggleBankB")
    resp = client.post(
        "/api/admin/agents/fce/skills",
        files={"file": ("global-rule.md", MD_SKILL, "text/markdown")},
    )
    assert resp.status_code == 201, resp.text
    global_skill = resp.json()

    client.put(
        f"/api/rooms/{room_a['id']}/agents/fce/skills/{global_skill['id']}",
        json={"enabled": False},
    )

    a_listed = client.get(f"/api/rooms/{room_a['id']}/agents/fce/skills").json()
    b_listed = client.get(f"/api/rooms/{room_b['id']}/agents/fce/skills").json()
    assert a_listed[0]["enabled"] is False
    assert b_listed[0]["enabled"] is True


def test_toggle_unknown_skill_404(client):
    room = make_room(client, "ToggleBank4")
    resp = client.put(
        f"/api/rooms/{room['id']}/agents/fce/skills/not-a-real-id",
        json={"enabled": False},
    )
    assert resp.status_code == 404


def test_toggle_is_idempotent(client):
    room = make_room(client, "ToggleBank5")
    skill = _upload_skill(client, room["id"])
    for _ in range(2):
        resp = client.put(
            f"/api/rooms/{room['id']}/agents/fce/skills/{skill['id']}",
            json={"enabled": False},
        )
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False


def test_ws_receives_agent_skill_toggled(client):
    room = make_room(client, "ToggleBankWs")
    skill = _upload_skill(client, room["id"])
    with client.websocket_connect(f"/ws/rooms/{room['id']}") as ws:
        client.put(
            f"/api/rooms/{room['id']}/agents/fce/skills/{skill['id']}",
            json={"enabled": False},
        )
        event = drain_until(ws, "agent_skill_toggled")
        assert event["skill_id"] == skill["id"]
        assert event["enabled"] is False
```

- [ ] **Step 6: Run the new tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_skill_toggle.py -v`
Expected: FAIL (`enabled` key missing from responses; PUT route 404s).

- [ ] **Step 7: Implement `list_skills` changes and the new toggle endpoint**

Replace the full contents of `backend/app/api/skills.py`:

```python
"""Skills API: runtime .md/.zip skill uploads per agent (room-scoped)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agents.orchestrator import RealtimeBroker
from ..agents.profiles import AGENT_KEYS
from ..db.base import get_session
from ..db.models import AgentSkill, AuditLog, RoomSkillOverride
from ..schemas import SkillOut, SkillToggleUpdate
from ..services.skills import SkillsService
from .deps import get_broker, get_skills_service, require_room_member

router = APIRouter(tags=["skills"])


def _skill_out(skill: AgentSkill, *, enabled: bool) -> SkillOut:
    return SkillOut(
        id=skill.id,
        room_id=skill.room_id,
        agent_key=skill.agent_key,
        skill_name=skill.skill_name,
        skill_type=skill.skill_type,
        blob_path=skill.blob_path,
        created_at=skill.created_at,
        enabled=enabled,
    )


@router.post(
    "/api/rooms/{room_id}/agents/{agent_key}/skills",
    status_code=201,
    response_model=SkillOut,
)
async def upload_skill(
    room_id: str,
    agent_key: str,
    file: UploadFile,
    session: AsyncSession = Depends(get_session),
    skills_service: SkillsService = Depends(get_skills_service),
    broker: RealtimeBroker = Depends(get_broker),
    user_email: str = Depends(require_room_member),
) -> SkillOut:
    if agent_key not in AGENT_KEYS:
        raise HTTPException(status_code=400, detail=f"unknown agent: {agent_key}")

    data = await file.read()
    try:
        skill = await skills_service.ingest(
            session,
            room_id=room_id,
            agent_key=agent_key,
            filename=file.filename or "upload",
            data=data,
            actor=user_email,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await broker.publish(
        room_id,
        {
            "type": "skill_added",
            "room_id": room_id,
            "agent_key": agent_key,
            "skill_name": skill.skill_name,
        },
    )
    return SkillOut.model_validate(skill, from_attributes=True)


@router.get(
    "/api/rooms/{room_id}/agents/{agent_key}/skills",
    response_model=list[SkillOut],
)
async def list_skills(
    room_id: str,
    agent_key: str,
    session: AsyncSession = Depends(get_session),
    _member: str = Depends(require_room_member),
) -> list[SkillOut]:
    result = await session.execute(
        select(AgentSkill)
        .where(
            AgentSkill.agent_key == agent_key,
            (AgentSkill.room_id == room_id) | (AgentSkill.room_id.is_(None)),
        )
        .order_by(AgentSkill.created_at)
    )
    skills = result.scalars().all()

    overrides = await session.execute(
        select(RoomSkillOverride.skill_id).where(RoomSkillOverride.room_id == room_id)
    )
    disabled_ids = set(overrides.scalars().all())

    return [_skill_out(skill, enabled=skill.id not in disabled_ids) for skill in skills]


@router.put(
    "/api/rooms/{room_id}/agents/{agent_key}/skills/{skill_id}",
    response_model=SkillOut,
)
async def toggle_skill(
    room_id: str,
    agent_key: str,
    skill_id: str,
    payload: SkillToggleUpdate,
    session: AsyncSession = Depends(get_session),
    broker: RealtimeBroker = Depends(get_broker),
    user_email: str = Depends(require_room_member),
) -> SkillOut:
    skill = await session.get(AgentSkill, skill_id)
    if skill is None or skill.agent_key != agent_key:
        raise HTTPException(status_code=404, detail="skill not found")
    if skill.room_id is not None and skill.room_id != room_id:
        raise HTTPException(status_code=404, detail="skill not found")

    existing = await session.get(RoomSkillOverride, (room_id, skill_id))
    if payload.enabled and existing is not None:
        await session.delete(existing)
    elif not payload.enabled and existing is None:
        session.add(RoomSkillOverride(room_id=room_id, skill_id=skill_id))

    session.add(
        AuditLog(
            room_id=room_id,
            actor=user_email,
            action="room_skill_toggled",
            detail={
                "agent_key": agent_key,
                "skill_id": skill_id,
                "enabled": payload.enabled,
            },
        )
    )
    await session.commit()
    await broker.publish(
        room_id,
        {
            "type": "agent_skill_toggled",
            "room_id": room_id,
            "agent_key": agent_key,
            "skill_id": skill_id,
            "enabled": payload.enabled,
        },
    )
    return _skill_out(skill, enabled=payload.enabled)
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_skill_toggle.py -v`
Expected: PASS (7 tests).

- [ ] **Step 9: Run the full backend suite to check for regressions**

Run: `cd backend && .venv/bin/pytest -v`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add backend/app/db/models.py backend/alembic/versions/b7d4e1a9c3f2_add_room_skill_overrides.py backend/app/schemas.py backend/app/api/skills.py backend/tests/test_skill_toggle.py
git commit -m "feat: add room-scoped skill enable/disable toggle"
```

---

### Task 3: Prompt layering — instructions + disabled-skill filtering

**Files:**
- Modify: `backend/app/agents/prompt_compiler.py` (full file — small)
- Modify: `backend/app/agents/orchestrator.py:28,296-319` (imports + `compiled_prompt`)
- Create: `backend/tests/test_prompt_layering.py`

**Interfaces:**
- Consumes: `RoomAgent.instructions` (Task 1), `RoomSkillOverride` (Task 2).
- Produces: `compile_system_prompt(baseline, skills=None, enrichment=None, instructions=None)` — the new `instructions` kwarg, consumed nowhere else in this plan (this is the terminal layering step).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_prompt_layering.py`:

```python
"""Full prompt layering: baseline -> skills -> room enrichment -> per-agent instructions."""
from .conftest import make_room

ENRICHMENT = "Customer is a Nordic neobank; SEPA instant payments only."
INSTRUCTIONS = "For this agent: prioritize the core-banking migration timeline."
MD_SKILL = b"# Timeline Skill\nMigration cutover is Q3.\n"


def test_instructions_appear_after_enrichment_in_compiled_prompt(client):
    room = make_room(client, "LayeringBank", enrichment=ENRICHMENT)
    client.post(
        f"/api/rooms/{room['id']}/agents/fce/skills",
        files={"file": ("timeline.md", MD_SKILL, "text/markdown")},
    )
    client.put(
        f"/api/rooms/{room['id']}/agents/fce/instructions",
        json={"instructions": INSTRUCTIONS},
    )

    compiled = client.get(
        f"/api/rooms/{room['id']}/agents/fce/compiled-prompt"
    ).json()["compiled_prompt"]

    assert "## Acquired Skills" in compiled
    assert "## Room Context Enrichment" in compiled
    assert "## Agent Instructions (this room)" in compiled

    skills_pos = compiled.index("## Acquired Skills")
    enrichment_pos = compiled.index("## Room Context Enrichment")
    instructions_pos = compiled.index("## Agent Instructions (this room)")
    assert skills_pos < enrichment_pos < instructions_pos
    assert INSTRUCTIONS in compiled


def test_no_instructions_section_when_empty(client):
    room = make_room(client, "LayeringBank2")
    compiled = client.get(
        f"/api/rooms/{room['id']}/agents/fce/compiled-prompt"
    ).json()["compiled_prompt"]
    assert "## Agent Instructions (this room)" not in compiled
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_prompt_layering.py -v`
Expected: FAIL (`"## Agent Instructions (this room)" in compiled` is `False`).

- [ ] **Step 3: Update `prompt_compiler.py`**

Replace the full contents of `backend/app/agents/prompt_compiler.py`:

```python
"""System-prompt compilation.

Invariant (enforced by tests): the compiled prompt always *starts with the
unmodified global baseline*. Skills, room enrichment, and per-agent
instructions are appended in clearly-delimited sections — UI-supplied
context can enrich, never overwrite.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .profiles import MENTION_ALIASES

SKILLS_HEADER = "## Acquired Skills"
ENRICHMENT_HEADER = "## Room Context Enrichment"
INSTRUCTIONS_HEADER = "## Agent Instructions (this room)"


@dataclass(frozen=True)
class SkillSection:
    name: str
    content: str


def compile_system_prompt(
    baseline: str,
    skills: list[SkillSection] | None = None,
    enrichment: str | None = None,
    instructions: str | None = None,
) -> str:
    """baseline ⊕ skills ⊕ enrichment ⊕ instructions, append-only."""
    parts: list[str] = [baseline.rstrip()]

    if skills:
        skill_blocks = "\n\n".join(
            f"### Skill: {s.name}\n{s.content.strip()}" for s in skills
        )
        parts.append(f"{SKILLS_HEADER}\n{skill_blocks}")

    if enrichment and enrichment.strip():
        parts.append(
            f"{ENRICHMENT_HEADER}\n"
            "The following room-specific context ENRICHES the instructions "
            "above. It adds customer detail and never overrides your baseline "
            "role or responsibilities.\n\n" + enrichment.strip()
        )

    if instructions and instructions.strip():
        parts.append(
            f"{INSTRUCTIONS_HEADER}\n"
            "The following per-agent instructions further tailor this agent "
            "for this room. They ENRICH the sections above and never "
            "override them.\n\n" + instructions.strip()
        )

    return "\n\n".join(parts)


# Negative lookbehind: an "@" inside an email address (john@fce-bank.com)
# is preceded by a word character or dot and must NOT count as a mention.
_MENTION_RE = re.compile(r"(?<![\w.])@([A-Za-z_]+)")


def parse_mention(content: str) -> str | None:
    """Return the agent_key targeted by the first recognized @-mention."""
    for match in _MENTION_RE.finditer(content):
        key = MENTION_ALIASES.get(match.group(1).lower())
        if key:
            return key
    return None
```

- [ ] **Step 4: Update `orchestrator.py`**

Edit `backend/app/agents/orchestrator.py`. Change the import on line 28:

```python
from ..db.models import AgentGlobalConfig, AgentSkill, Message, Room, RoomAgent, RoomSkillOverride
```

Then replace the `compiled_prompt` method (lines 296-319):

```python
    async def compiled_prompt(
        self, session: AsyncSession, room: Room, agent_key: str
    ) -> str:
        config = await session.get(AgentGlobalConfig, agent_key)
        if config is None:
            raise ValueError(f"unknown agent: {agent_key}")

        room_agent_result = await session.execute(
            select(RoomAgent.instructions).where(
                RoomAgent.room_id == room.id, RoomAgent.agent_key == agent_key
            )
        )
        instructions = room_agent_result.scalar_one_or_none() or ""

        overrides_result = await session.execute(
            select(RoomSkillOverride.skill_id).where(
                RoomSkillOverride.room_id == room.id
            )
        )
        disabled_ids = set(overrides_result.scalars().all())

        result = await session.execute(
            select(AgentSkill)
            .where(
                AgentSkill.agent_key == agent_key,
                (AgentSkill.room_id == room.id) | (AgentSkill.room_id.is_(None)),
            )
            .order_by(AgentSkill.created_at)
        )
        skills = [
            SkillSection(name=s.skill_name, content=s.content_text)
            for s in result.scalars().all()
            if s.id not in disabled_ids
        ]
        return compile_system_prompt(
            baseline=config.system_prompt,
            skills=skills,
            enrichment=room.enrichment_prompt,
            instructions=instructions,
        )
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd backend && .venv/bin/pytest tests/test_prompt_layering.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Run the full backend suite to check for regressions**

Run: `cd backend && .venv/bin/pytest -v`
Expected: PASS — pay particular attention to `test_prompt_enrichment.py` and `test_skill_toggle.py`, which both exercise `compiled_prompt`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/agents/prompt_compiler.py backend/app/agents/orchestrator.py backend/tests/test_prompt_layering.py
git commit -m "feat: layer per-agent instructions and disabled skills into compiled prompt"
```

---

### Task 4: Per-agent usage summary endpoint

**Files:**
- Modify: `backend/app/schemas.py` (add `AgentUsageOut`)
- Modify: `backend/app/api/rooms.py` (import + new endpoint, placed after the instructions endpoints from Task 1)
- Create: `backend/tests/test_agent_usage.py`

**Interfaces:**
- Consumes: `Message.input_tokens`/`output_tokens`/`agent_key`/`sender_type` (existing columns), `AGENT_KEYS`, `require_room_member`.
- Produces: `AgentUsageOut` (`agent_key`, `message_count`, `total_input_tokens`, `total_output_tokens`), consumed by Task 5/8 (frontend Usage tab).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_agent_usage.py`:

```python
"""Per-room, per-agent token usage summary."""
from .conftest import make_room


def test_usage_zero_before_any_agent_reply(client):
    room = make_room(client, "UsageBank1")
    resp = client.get(f"/api/rooms/{room['id']}/agents/data_expert/usage")
    assert resp.status_code == 200
    assert resp.json() == {
        "agent_key": "data_expert",
        "message_count": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
    }


def test_usage_accumulates_from_agent_replies(client):
    room = make_room(client, "UsageBank2")
    client.post(
        f"/api/rooms/{room['id']}/messages",
        json={"content": "@data_expert kick off"},
    )

    usage = client.get(f"/api/rooms/{room['id']}/agents/data_expert/usage").json()
    assert usage["message_count"] == 1
    assert usage["total_input_tokens"] > 0
    assert usage["total_output_tokens"] > 0

    fce_usage = client.get(f"/api/rooms/{room['id']}/agents/fce/usage").json()
    assert fce_usage["message_count"] == 0


def test_usage_unknown_agent_400(client):
    room = make_room(client, "UsageBank3")
    resp = client.get(f"/api/rooms/{room['id']}/agents/nope/usage")
    assert resp.status_code == 400
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_agent_usage.py -v`
Expected: FAIL (404 — route doesn't exist).

- [ ] **Step 3: Add the schema**

Edit `backend/app/schemas.py`, add after `RoomAgentDetailOut`/`InstructionsUpdate` (from Task 1):

```python
class AgentUsageOut(BaseModel):
    agent_key: str
    message_count: int
    total_input_tokens: int
    total_output_tokens: int
```

- [ ] **Step 4: Implement the endpoint**

Edit `backend/app/api/rooms.py`. Add `AgentUsageOut` to the `..schemas` import block (alongside `RoomAgentDetailOut`). Then insert this endpoint directly after `update_room_agent_instructions` (from Task 1) and before `get_compiled_prompt`:

```python
@router.get(
    "/{room_id}/agents/{agent_key}/usage",
    response_model=AgentUsageOut,
)
async def get_agent_usage(
    room_id: str,
    agent_key: str,
    session: AsyncSession = Depends(get_session),
    _member: str = Depends(require_room_member),
) -> AgentUsageOut:
    if agent_key not in AGENT_KEYS:
        raise HTTPException(status_code=400, detail=f"unknown agent: {agent_key}")

    result = await session.execute(
        select(
            func.count(Message.id),
            func.coalesce(func.sum(Message.input_tokens), 0),
            func.coalesce(func.sum(Message.output_tokens), 0),
        ).where(
            Message.room_id == room_id,
            Message.agent_key == agent_key,
            Message.sender_type == "agent",
        )
    )
    message_count, total_input_tokens, total_output_tokens = result.one()
    return AgentUsageOut(
        agent_key=agent_key,
        message_count=message_count,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
    )
```

(`func` is already imported in `rooms.py` — `from sqlalchemy import func, select`.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_agent_usage.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Run the full backend suite**

Run: `cd backend && .venv/bin/pytest -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas.py backend/app/api/rooms.py backend/tests/test_agent_usage.py
git commit -m "feat: add per-agent usage summary endpoint"
```

---

### Task 5: Frontend data layer (types + API client)

**Files:**
- Modify: `frontend/src/types.ts` (full file)
- Modify: `frontend/src/api.ts` (full file)

**Interfaces:**
- Consumes: the 4 backend response shapes from Tasks 1-4 (`RoomAgentDetailOut`, `SkillOut.enabled`, `AgentUsageOut`), plus the 2 new WS event payloads.
- Produces: `getRoomAgent`, `updateRoomAgentInstructions`, `toggleSkill`, `getAgentUsage` functions and their types, consumed by Tasks 6-8.

- [ ] **Step 1: Update `types.ts`**

Replace the full contents of `frontend/src/types.ts`:

```typescript
// TypeScript interfaces mirroring backend/app/schemas.py

export type AgentKey = "data_expert" | "fce";

export type RoomStatus = "active" | "paused_awaiting_human";

export type SenderType = "human" | "agent" | "system";

// --- Admin -----------------------------------------------------------------
export interface AgentConfigOut {
  agent_key: AgentKey;
  display_name: string;
  system_prompt: string;
  updated_at: string;
}

export interface AgentConfigUpdate {
  system_prompt: string;
}

// --- Rooms -------------------------------------------------------------------
export interface RoomCreate {
  customer_name: string;
  enrichment_prompt?: string | null;
}

export interface RoomAgentOut {
  agent_key: AgentKey;
  display_name: string;
}

export interface RoomAgentDetailOut {
  agent_key: AgentKey;
  display_name: string;
  system_prompt: string;
  instructions: string;
}

export interface InstructionsUpdate {
  instructions: string;
}

export interface AgentUsageOut {
  agent_key: AgentKey;
  message_count: number;
  total_input_tokens: number;
  total_output_tokens: number;
}

export interface RoomLastMessageOut {
  sender_type: SenderType;
  sender_name: string;
  agent_key: string | null;
  content: string;
  created_at: string;
}

export interface RoomOut {
  id: string;
  customer_name: string;
  enrichment_prompt: string | null;
  status: RoomStatus;
  cycles_used: number;
  cycle_limit: number;
  created_at: string;
  agents: RoomAgentOut[];
  member_count: number;
  last_message: RoomLastMessageOut | null;
}

export interface RoomMemberOut {
  user_email: string;
  display_name: string;
  role: string;
  joined_at: string;
}

export interface InviteCreateOut {
  token: string;
  room_id: string;
  expires_at: string;
  join_url: string;
}

export interface JoinRequest {
  token: string;
  display_name: string;
}

// --- Messages ---------------------------------------------------------------
export interface MessageCreate {
  content: string;
}

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
  created_at: string;
}

export interface PostMessageResult {
  messages: MessageOut[];
  room_status: RoomStatus;
  cycles_used: number;
  cycle_limit: number;
}

// --- Google Drive -------------------------------------------------------------
export type GDriveStatus =
  | "none"
  | "pending"
  | "connected"
  | "linked"
  | "error"
  | "revoked";

export interface GDriveAuthorizeOut {
  authorize_url: string;
  state: string;
}

export interface GDriveStatusOut {
  status: GDriveStatus;
  google_folder_id?: string | null;
  google_folder_name?: string | null;
  token_expiry?: string | null;
  scopes?: string;
}

export interface GDriveFolderLink {
  folder_id: string;
  folder_name: string;
}

// --- Skills -------------------------------------------------------------------
export interface SkillOut {
  id: string;
  room_id: string | null;
  agent_key: string;
  skill_name: string;
  skill_type: string;
  blob_path: string;
  created_at: string;
  enabled: boolean;
}

export interface SkillToggleUpdate {
  enabled: boolean;
}

// --- Compiled prompt -----------------------------------------------------------
export interface CompiledPromptOut {
  agent_key: string;
  compiled_prompt: string;
}

// --- WebSocket events ------------------------------------------------------------
export interface WsMessageCreated {
  type: "message_created";
  message: MessageOut;
}

export interface WsAgentThinking {
  type: "agent_thinking";
  agent_key: string;
}

export interface WsRoomPaused {
  type: "room_paused";
  cycles_used: number;
  cycle_limit: number;
}

export interface WsRoomResumed {
  type: "room_resumed";
}

export interface WsSkillAdded {
  type: "skill_added";
  agent_key?: string;
  skill_name?: string;
}

export interface WsAgentInstructionsUpdated {
  type: "agent_instructions_updated";
  room_id: string;
  agent_key: string;
}

export interface WsAgentSkillToggled {
  type: "agent_skill_toggled";
  room_id: string;
  agent_key: string;
  skill_id: string;
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
  | WsDriveLinked
  | WsDriveConnected;
```

- [ ] **Step 2: Update `api.ts`**

Replace the full contents of `frontend/src/api.ts`:

```typescript
// Typed REST client for the Cabinet backend.
import { getAccessToken, isEntraAuth } from "./auth";
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
} from "./types";

export const API_BASE: string = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

const EMAIL_KEY = "cabinet_user_email";
const DEFAULT_EMAIL = "dev@thetaray.com";

export function getUserEmail(): string {
  return localStorage.getItem(EMAIL_KEY) || DEFAULT_EMAIL;
}

export function setUserEmail(email: string): void {
  localStorage.setItem(EMAIL_KEY, email.trim() || DEFAULT_EMAIL);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (isEntraAuth) {
    headers.set("Authorization", `Bearer ${await getAccessToken()}`);
  } else {
    headers.set("X-User-Email", getUserEmail());
  }
  if (init.body !== undefined && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  } catch (err) {
    throw new ApiError(0, `Network error: ${err instanceof Error ? err.message : String(err)}`);
  }

  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body: unknown = await res.json();
      if (body && typeof body === "object" && "detail" in body) {
        const d = (body as { detail: unknown }).detail;
        detail = typeof d === "string" ? d : JSON.stringify(d);
      }
    } catch {
      // no JSON body; keep the status text
    }
    throw new ApiError(res.status, detail);
  }

  return (await res.json()) as T;
}

// --- Health -----------------------------------------------------------------
export const getHealth = () => request<{ status: string }>("/api/health");

// --- Admin ------------------------------------------------------------------
export const listAgentConfigs = () => request<AgentConfigOut[]>("/api/admin/agents");

export const getAgentConfig = (agentKey: string) =>
  request<AgentConfigOut>(`/api/admin/agents/${agentKey}`);

export const updateAgentConfig = (agentKey: string, systemPrompt: string) =>
  request<AgentConfigOut>(`/api/admin/agents/${agentKey}`, {
    method: "PUT",
    body: JSON.stringify({ system_prompt: systemPrompt }),
  });

// --- Rooms --------------------------------------------------------------------
export const createRoom = (customerName: string, enrichmentPrompt?: string) =>
  request<RoomOut>("/api/rooms", {
    method: "POST",
    body: JSON.stringify({
      customer_name: customerName,
      enrichment_prompt: enrichmentPrompt || null,
    }),
  });

export const listRooms = () => request<RoomOut[]>("/api/rooms");

export const getRoom = (roomId: string) => request<RoomOut>(`/api/rooms/${roomId}`);

export const listMembers = (roomId: string) =>
  request<RoomMemberOut[]>(`/api/rooms/${roomId}/members`);

export const createInvite = (roomId: string) =>
  request<InviteCreateOut>(`/api/rooms/${roomId}/invites`, { method: "POST" });

export const joinRoom = (token: string, displayName: string) =>
  request<RoomOut>("/api/rooms/join", {
    method: "POST",
    body: JSON.stringify({ token, display_name: displayName }),
  });

// --- Room agents (Agents Skills) ----------------------------------------------
export const getRoomAgent = (roomId: string, agentKey: string) =>
  request<RoomAgentDetailOut>(`/api/rooms/${roomId}/agents/${agentKey}`);

export const updateRoomAgentInstructions = (
  roomId: string,
  agentKey: string,
  instructions: string,
) =>
  request<RoomAgentDetailOut>(`/api/rooms/${roomId}/agents/${agentKey}/instructions`, {
    method: "PUT",
    body: JSON.stringify({ instructions }),
  });

export const getAgentUsage = (roomId: string, agentKey: string) =>
  request<AgentUsageOut>(`/api/rooms/${roomId}/agents/${agentKey}/usage`);

// --- Messages --------------------------------------------------------------------
export const listMessages = (roomId: string) =>
  request<MessageOut[]>(`/api/rooms/${roomId}/messages`);

export const postMessage = (roomId: string, content: string) =>
  request<PostMessageResult>(`/api/rooms/${roomId}/messages`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });

export const resumeRoom = (roomId: string) =>
  request<PostMessageResult>(`/api/rooms/${roomId}/resume`, { method: "POST" });

// --- Compiled prompt ----------------------------------------------------------------
export const getCompiledPrompt = (roomId: string, agentKey: string) =>
  request<CompiledPromptOut>(`/api/rooms/${roomId}/agents/${agentKey}/compiled-prompt`);

// --- Google Drive -------------------------------------------------------------------
export const gdriveAuthorize = (roomId: string) =>
  request<GDriveAuthorizeOut>(`/api/rooms/${roomId}/gdrive/authorize`);

export const gdriveStatus = (roomId: string) =>
  request<GDriveStatusOut>(`/api/rooms/${roomId}/gdrive/status`);

export const gdriveLinkFolder = (roomId: string, folderId: string, folderName: string) =>
  request<GDriveStatusOut>(`/api/rooms/${roomId}/gdrive/folder`, {
    method: "POST",
    body: JSON.stringify({ folder_id: folderId, folder_name: folderName }),
  });

// --- Skills -----------------------------------------------------------------------
export const uploadSkill = (roomId: string, agentKey: string, file: File) => {
  const form = new FormData();
  form.append("file", file);
  return request<SkillOut>(`/api/rooms/${roomId}/agents/${agentKey}/skills`, {
    method: "POST",
    body: form,
  });
};

export const listSkills = (roomId: string, agentKey: string) =>
  request<SkillOut[]>(`/api/rooms/${roomId}/agents/${agentKey}/skills`);

export const toggleSkill = (
  roomId: string,
  agentKey: string,
  skillId: string,
  enabled: boolean,
) =>
  request<SkillOut>(`/api/rooms/${roomId}/agents/${agentKey}/skills/${skillId}`, {
    method: "PUT",
    body: JSON.stringify({ enabled }),
  });
```

- [ ] **Step 3: Type-check**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors. (`SkillUploadDialog.tsx` still compiles fine — it only reads `SkillOut` fields that still exist.)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types.ts frontend/src/api.ts
git commit -m "feat: add frontend types/client for room agent config, skill toggle, usage"
```

---

### Task 6: RoomView tab switcher + agent list + Instructions tab

**Files:**
- Modify: `frontend/src/components/RoomView.tsx` (add tab state + tab bar + conditional rendering)
- Create: `frontend/src/components/AgentsSkillsView.tsx`
- Create: `frontend/src/components/AgentDetailPanel.tsx`
- Create: `frontend/src/components/AgentInstructionsTab.tsx`
- Modify: `frontend/src/styles.css` (append new rules)

**Interfaces:**
- Consumes: `getRoomAgent`, `updateRoomAgentInstructions` (Task 5); `RoomAgentOut`, `RoomAgentDetailOut`, `AgentKey` (Task 5).
- Produces: `AgentsSkillsView({roomId, agents})`, `AgentDetailPanel({roomId, agent, onBack})` — the `agent: RoomAgentOut` prop and `onBack: () => void` signature Task 7/8 will extend (adding tabs), not change.

- [ ] **Step 1: Create the Instructions tab component**

Create `frontend/src/components/AgentInstructionsTab.tsx`:

```tsx
import { useEffect, useState } from "react";
import { getRoomAgent, updateRoomAgentInstructions } from "../api";
import type { AgentKey } from "../types";
import { pushToast, toastError } from "../toast";

export default function AgentInstructionsTab({
  roomId,
  agentKey,
}: {
  roomId: string;
  agentKey: AgentKey;
}) {
  const [systemPrompt, setSystemPrompt] = useState<string | null>(null);
  const [instructions, setInstructions] = useState("");
  const [saved, setSaved] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    getRoomAgent(roomId, agentKey)
      .then((detail) => {
        setSystemPrompt(detail.system_prompt);
        setInstructions(detail.instructions);
        setSaved(detail.instructions);
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, [roomId, agentKey]);

  const save = async () => {
    setSaving(true);
    try {
      const updated = await updateRoomAgentInstructions(roomId, agentKey, instructions);
      setSaved(updated.instructions);
      pushToast("info", "Instructions saved");
    } catch (err) {
      toastError(err, "Failed to save instructions");
    } finally {
      setSaving(false);
    }
  };

  if (error) return <div className="inline-error">Could not load agent: {error}</div>;
  if (loading) return <div className="muted">Loading…</div>;

  return (
    <div className="agent-instructions-tab">
      <div className="field">
        <span className="field-label">System prompt (global baseline — read-only)</span>
        <pre className="system-prompt-view">{systemPrompt}</pre>
      </div>

      <label className="field">
        <span className="field-label">Instructions for this room (optional)</span>
        <textarea
          className="prompt-editor"
          rows={10}
          value={instructions}
          onChange={(e) => setInstructions(e.target.value)}
          placeholder="Add context specific to this customer engagement — e.g. system landscape, timelines, known constraints."
          spellCheck={false}
        />
      </label>

      <div className="agent-editor-footer">
        <button
          className="btn btn-primary"
          onClick={save}
          disabled={saving || instructions === saved}
        >
          {saving ? "Saving…" : instructions === saved ? "Saved" : "Save instructions"}
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Create the agent detail panel shell**

Create `frontend/src/components/AgentDetailPanel.tsx`:

```tsx
import { useState } from "react";
import type { RoomAgentOut } from "../types";
import AgentInstructionsTab from "./AgentInstructionsTab";

type Tab = "instructions" | "tools" | "mcps" | "memory";

const TABS: { key: Tab; label: string; comingSoon?: boolean }[] = [
  { key: "instructions", label: "Instructions" },
  { key: "tools", label: "Tools", comingSoon: true },
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
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Create the agent list view**

Create `frontend/src/components/AgentsSkillsView.tsx`:

```tsx
import { useState } from "react";
import type { RoomAgentOut } from "../types";
import AgentDetailPanel from "./AgentDetailPanel";

export default function AgentsSkillsView({
  roomId,
  agents,
}: {
  roomId: string;
  agents: RoomAgentOut[];
}) {
  const [selected, setSelected] = useState<RoomAgentOut | null>(null);

  if (selected) {
    return (
      <AgentDetailPanel roomId={roomId} agent={selected} onBack={() => setSelected(null)} />
    );
  }

  return (
    <div className="agents-skills-view">
      <h2>Agents Skills</h2>
      <p className="muted">
        Configure each agent's instructions, skills, and usage for this room.
      </p>
      <div className="agent-card-grid">
        {agents.map((a) => (
          <button
            key={a.agent_key}
            className={`agent-card agent-card-${a.agent_key}`}
            onClick={() => setSelected(a)}
          >
            <span className={`agent-chip agent-chip-${a.agent_key}`}>{a.display_name}</span>
            <span className="muted">Instructions · Skills · Usage</span>
          </button>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Wire the tab switcher into `RoomView.tsx`**

Edit `frontend/src/components/RoomView.tsx`. Add the import (near the other component imports, after `import SkillUploadDialog from "./SkillUploadDialog";`):

```tsx
import AgentsSkillsView from "./AgentsSkillsView";
```

Add tab state alongside the other `useState` declarations (after `const [driveRefreshSignal, setDriveRefreshSignal] = useState(0);`):

```tsx
  const [activeTab, setActiveTab] = useState<"chat" | "agents">("chat");
```

Find this exact block near the end of the component's returned JSX:

```tsx
      {room && <PausedBanner status={room.status} onResume={resume} resuming={resuming} />}

      <ChatThread messages={messages} thinkingAgents={thinkingAgents} />

      <Composer
        onSend={(content) => void send(content)}
        sending={sending}
        disabled={!room}
        disabledHint={!room ? "Loading room…" : undefined}
      />
    </div>
  );
}
```

Replace it with:

```tsx
      <nav className="room-tabs">
        <button
          className={`nav-link ${activeTab === "chat" ? "nav-active" : ""}`}
          onClick={() => setActiveTab("chat")}
        >
          Chat
        </button>
        <button
          className={`nav-link ${activeTab === "agents" ? "nav-active" : ""}`}
          onClick={() => setActiveTab("agents")}
        >
          Agents Skills
        </button>
      </nav>

      <div className="room-chat-pane" style={{ display: activeTab === "chat" ? "contents" : "none" }}>
        {room && <PausedBanner status={room.status} onResume={resume} resuming={resuming} />}
        <ChatThread messages={messages} thinkingAgents={thinkingAgents} />
        <Composer
          onSend={(content) => void send(content)}
          sending={sending}
          disabled={!room}
          disabledHint={!room ? "Loading room…" : undefined}
        />
      </div>

      {activeTab === "agents" && room && (
        <AgentsSkillsView roomId={roomId} agents={room.agents} />
      )}
    </div>
  );
}
```

(The header block above this — with `DrivePanel`, `InviteDialog`, `SkillUploadDialog` — is unchanged; `SkillUploadDialog` stays in the header until Task 7 replaces it.)

- [ ] **Step 5: Add the new CSS rules**

Edit `frontend/src/styles.css`. Append after the `.room-header-actions` block (currently ending at line 595):

```css
/* ---- room tabs (Chat / Agents Skills) ------------------------------------- */
.room-tabs {
  display: flex;
  gap: 0.25rem;
  border-bottom: 1px solid var(--border);
  padding-bottom: 0.4rem;
}
```

Append directly after the `.skill-type { ... }` rule (the last of the existing `.skill-*` rules, right before the `/* ---- drive panel ---- */` comment):

```css
/* ---- agents skills view ---------------------------------------------------------- */
.agents-skills-view {
  max-width: 900px;
  width: 100%;
  margin: 0 auto;
}

.agent-card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 0.8rem;
  margin-top: 1rem;
}

.agent-card {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.4rem;
  background: var(--bg-panel);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius);
  padding: 1rem;
  cursor: pointer;
  text-align: left;
  font: inherit;
}

.agent-card:hover {
  border-color: var(--accent);
}

.agent-detail-panel {
  max-width: 900px;
  width: 100%;
  margin: 0 auto;
}

.agent-detail-header {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  margin-bottom: 0.8rem;
}

.agent-detail-tabs {
  display: flex;
  gap: 0.25rem;
  border-bottom: 1px solid var(--border);
  padding-bottom: 0.4rem;
  flex-wrap: wrap;
}

.agent-detail-tab-soon {
  opacity: 0.55;
  cursor: not-allowed;
}

.agent-detail-tab-badge {
  margin-left: 0.35rem;
  font-size: 0.65rem;
  text-transform: uppercase;
  color: var(--text-muted);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 0 0.3rem;
}

.agent-detail-tab-content {
  margin-top: 1rem;
}

.system-prompt-view {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 7px;
  padding: 0.7rem 0.9rem;
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
  font-size: 0.82rem;
  line-height: 1.5;
  white-space: pre-wrap;
  max-height: 220px;
  overflow-y: auto;
}
```

- [ ] **Step 6: Type-check**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 7: Manually verify in the browser**

Run: `cd frontend && npm run dev` (and the backend dev server per this repo's usual local setup, `CABINET_LLM_MODE=mock`).
- Open a room, confirm the "Chat" / "Agents Skills" tabs appear below the header.
- Click "Agents Skills" — two cards (Data Expert, FCE) should appear.
- Click a card — the detail panel should show Instructions (with the read-only system prompt and an empty editable textarea), plus disabled Tools/MCPs/Memory tabs.
- Type instructions, click "Save instructions", reload the page, reopen the same agent — the saved text should persist.
- Click back to "Chat" — the chat thread and any composer draft should still be there.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/RoomView.tsx frontend/src/components/AgentsSkillsView.tsx frontend/src/components/AgentDetailPanel.tsx frontend/src/components/AgentInstructionsTab.tsx frontend/src/styles.css
git commit -m "feat: add Agents Skills tab with agent list and Instructions detail page"
```

---

### Task 7: Skills tab (replaces SkillUploadDialog)

**Files:**
- Create: `frontend/src/components/AgentSkillsTab.tsx`
- Modify: `frontend/src/components/AgentDetailPanel.tsx` (add the Skills tab)
- Modify: `frontend/src/components/RoomView.tsx` (remove `SkillUploadDialog` from the header)
- Delete: `frontend/src/components/SkillUploadDialog.tsx`
- Modify: `frontend/src/styles.css` (append toggle-button rules)

**Interfaces:**
- Consumes: `listSkills`, `uploadSkill`, `toggleSkill` (Task 5); `SkillOut` (Task 5).
- Produces: nothing new consumed by later tasks — this is a self-contained UI slice.

- [ ] **Step 1: Create the Skills tab component**

Create `frontend/src/components/AgentSkillsTab.tsx`:

```tsx
import { useEffect, useRef, useState } from "react";
import { listSkills, toggleSkill, uploadSkill } from "../api";
import type { AgentKey, SkillOut } from "../types";
import { pushToast, toastError } from "../toast";

export default function AgentSkillsTab({
  roomId,
  agentKey,
}: {
  roomId: string;
  agentKey: AgentKey;
}) {
  const [skills, setSkills] = useState<SkillOut[] | null>(null);
  const [uploading, setUploading] = useState(false);
  const [togglingId, setTogglingId] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    setSkills(null);
    listSkills(roomId, agentKey)
      .then(setSkills)
      .catch((err) => {
        setSkills([]);
        toastError(err, "Failed to load skills");
      });
  }, [roomId, agentKey]);

  const upload = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file || uploading) return;
    setUploading(true);
    try {
      const skill = await uploadSkill(roomId, agentKey, file);
      setSkills((prev) => [...(prev ?? []), skill]);
      pushToast("info", `Skill "${skill.skill_name}" added`);
      if (fileRef.current) fileRef.current.value = "";
    } catch (err) {
      toastError(err, "Skill upload failed");
    } finally {
      setUploading(false);
    }
  };

  const toggle = async (skill: SkillOut) => {
    setTogglingId(skill.id);
    try {
      const updated = await toggleSkill(roomId, agentKey, skill.id, !skill.enabled);
      setSkills((prev) => (prev ?? []).map((s) => (s.id === updated.id ? updated : s)));
    } catch (err) {
      toastError(err, "Failed to toggle skill");
    } finally {
      setTogglingId(null);
    }
  };

  return (
    <div className="agent-skills-tab">
      <label className="field">
        <span className="field-label">Add a skill</span>
        <input ref={fileRef} type="file" accept=".md,.zip" />
      </label>
      <p className="muted skill-note">
        A <code>.md</code> file extends the agent's context directly; a <code>.zip</code>{" "}
        bundle must contain a <code>SKILL.md</code> at its root.
      </p>
      <button className="btn btn-primary" onClick={upload} disabled={uploading}>
        {uploading ? "Uploading…" : "Upload"}
      </button>

      <h4 className="skills-heading">Skills for this agent</h4>
      {skills === null && <div className="muted">Loading…</div>}
      {skills !== null && skills.length === 0 && (
        <div className="muted">No skills uploaded for this agent yet.</div>
      )}
      <ul className="skill-list">
        {(skills ?? []).map((s) => (
          <li key={s.id} className={`skill-item ${s.enabled ? "" : "skill-item-disabled"}`}>
            <span className="skill-name">{s.skill_name}</span>
            <span className={`skill-type skill-type-${s.skill_type}`}>{s.skill_type}</span>
            <span className="muted">{new Date(s.created_at).toLocaleString()}</span>
            <button
              className="btn btn-small skill-toggle-btn"
              onClick={() => void toggle(s)}
              disabled={togglingId === s.id}
            >
              {togglingId === s.id ? "…" : s.enabled ? "Disable" : "Enable"}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

- [ ] **Step 2: Add the Skills tab to `AgentDetailPanel.tsx`**

Edit `frontend/src/components/AgentDetailPanel.tsx`. Add the import:

```tsx
import AgentSkillsTab from "./AgentSkillsTab";
```

Change the `Tab` type and `TABS` array:

```tsx
type Tab = "instructions" | "skills" | "tools" | "mcps" | "memory";

const TABS: { key: Tab; label: string; comingSoon?: boolean }[] = [
  { key: "instructions", label: "Instructions" },
  { key: "skills", label: "Skills" },
  { key: "tools", label: "Tools", comingSoon: true },
  { key: "mcps", label: "MCPs", comingSoon: true },
  { key: "memory", label: "Memory", comingSoon: true },
];
```

Add the render branch in `agent-detail-tab-content`:

```tsx
      <div className="agent-detail-tab-content">
        {tab === "instructions" && (
          <AgentInstructionsTab roomId={roomId} agentKey={agent.agent_key} />
        )}
        {tab === "skills" && <AgentSkillsTab roomId={roomId} agentKey={agent.agent_key} />}
      </div>
```

- [ ] **Step 3: Remove `SkillUploadDialog` from `RoomView.tsx`**

Edit `frontend/src/components/RoomView.tsx`. Remove the import line:

```tsx
import SkillUploadDialog from "./SkillUploadDialog";
```

Remove its usage from the header actions block:

```tsx
        <div className="room-header-actions">
          <DrivePanel roomId={roomId} refreshSignal={driveRefreshSignal} />
          <InviteDialog roomId={roomId} />
        </div>
```

- [ ] **Step 4: Delete the old dialog component**

```bash
rm frontend/src/components/SkillUploadDialog.tsx
```

- [ ] **Step 5: Add the toggle-button/disabled-skill CSS**

Edit `frontend/src/styles.css`. Append after the existing `.skill-type` rule:

```css
.skill-item-disabled {
  opacity: 0.55;
}

.skill-toggle-btn {
  margin-left: auto;
}
```

- [ ] **Step 6: Type-check**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors (confirms no leftover references to the deleted `SkillUploadDialog`).

- [ ] **Step 7: Manually verify in the browser**

Run: `cd frontend && npm run dev`
- Open a room — the header no longer has an "Upload skill" button.
- Go to Agents Skills → an agent → Skills tab. Upload a `.md` file — it should appear in the list, enabled.
- Click "Disable" on it — the row should dim and the button should flip to "Enable".
- Open the room's compiled-prompt debug endpoint (`GET /api/rooms/{room_id}/agents/{agent_key}/compiled-prompt` via curl or browser) and confirm the disabled skill's content is absent.
- Click "Enable" — confirm it's back in the compiled prompt.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/AgentSkillsTab.tsx frontend/src/components/AgentDetailPanel.tsx frontend/src/components/RoomView.tsx frontend/src/styles.css
git rm frontend/src/components/SkillUploadDialog.tsx
git commit -m "feat: replace Upload-skill modal with Agents Skills Skills tab (add + toggle)"
```

---

### Task 8: Usage tab, WS live-updates, final polish

**Files:**
- Create: `frontend/src/components/AgentUsageTab.tsx`
- Modify: `frontend/src/components/AgentDetailPanel.tsx` (add the Usage tab)
- Modify: `frontend/src/components/RoomView.tsx` (handle the 2 new WS event types)
- Modify: `frontend/src/styles.css` (append usage-stat rules)

**Interfaces:**
- Consumes: `getAgentUsage` (Task 5); `WsAgentInstructionsUpdated`, `WsAgentSkillToggled` (Task 5).
- Produces: nothing further — this is the final task in the plan.

- [ ] **Step 1: Create the Usage tab component**

Create `frontend/src/components/AgentUsageTab.tsx`:

```tsx
import { useEffect, useState } from "react";
import { getAgentUsage } from "../api";
import type { AgentKey, AgentUsageOut } from "../types";

export default function AgentUsageTab({
  roomId,
  agentKey,
}: {
  roomId: string;
  agentKey: AgentKey;
}) {
  const [usage, setUsage] = useState<AgentUsageOut | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setUsage(null);
    getAgentUsage(roomId, agentKey)
      .then(setUsage)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [roomId, agentKey]);

  if (error) return <div className="inline-error">Could not load usage: {error}</div>;
  if (!usage) return <div className="muted">Loading…</div>;

  return (
    <div className="agent-usage-tab">
      <div className="usage-stat-grid">
        <div className="usage-stat">
          <span className="usage-stat-value">{usage.message_count}</span>
          <span className="usage-stat-label">Replies in this room</span>
        </div>
        <div className="usage-stat">
          <span className="usage-stat-value">{usage.total_input_tokens.toLocaleString()}</span>
          <span className="usage-stat-label">Input tokens</span>
        </div>
        <div className="usage-stat">
          <span className="usage-stat-value">{usage.total_output_tokens.toLocaleString()}</span>
          <span className="usage-stat-label">Output tokens</span>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Add the Usage tab to `AgentDetailPanel.tsx`**

Edit `frontend/src/components/AgentDetailPanel.tsx`. Add the import:

```tsx
import AgentUsageTab from "./AgentUsageTab";
```

Update `Tab` and `TABS`:

```tsx
type Tab = "instructions" | "skills" | "usage" | "tools" | "mcps" | "memory";

const TABS: { key: Tab; label: string; comingSoon?: boolean }[] = [
  { key: "instructions", label: "Instructions" },
  { key: "skills", label: "Skills" },
  { key: "usage", label: "Usage" },
  { key: "tools", label: "Tools", comingSoon: true },
  { key: "mcps", label: "MCPs", comingSoon: true },
  { key: "memory", label: "Memory", comingSoon: true },
];
```

Add the render branch:

```tsx
        {tab === "usage" && <AgentUsageTab roomId={roomId} agentKey={agent.agent_key} />}
```

- [ ] **Step 3: Handle the new WS events in `RoomView.tsx`**

Edit `frontend/src/components/RoomView.tsx`. In the `handleWsEvent` switch statement, add two cases (alongside the existing `case "skill_added":`):

```tsx
        case "agent_instructions_updated":
          pushToast(
            "info",
            `Instructions updated for ${agentDisplayName(roomRef.current, event.agent_key)}`,
          );
          break;
        case "agent_skill_toggled":
          pushToast(
            "info",
            `Skill ${event.enabled ? "enabled" : "disabled"} for ${agentDisplayName(
              roomRef.current,
              event.agent_key,
            )}`,
          );
          break;
```

- [ ] **Step 4: Add the usage-stat CSS**

Edit `frontend/src/styles.css`. Append after the `.system-prompt-view` block (added in Task 6):

```css
.usage-stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 0.8rem;
}

.usage-stat {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 0.9rem 1rem;
}

.usage-stat-value {
  font-size: 1.4rem;
  font-weight: 700;
}

.usage-stat-label {
  font-size: 0.78rem;
  color: var(--text-muted);
}
```

- [ ] **Step 5: Type-check**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 6: Manually verify in the browser**

Run: `cd frontend && npm run dev`
- Open a room, send a message that triggers an agent reply (or `@mention` an agent).
- Go to Agents Skills → that agent → Usage tab — confirm message count and token counts are non-zero and match what you'd expect from the reply just sent.
- Open the same room in a second browser tab/window; in tab A, edit instructions or toggle a skill; confirm tab B shows a toast (`Instructions updated for …` / `Skill enabled/disabled for …`).
- Confirm the Tools/MCPs/Memory tabs are visibly present but disabled/unclickable, each showing a "soon" badge.

- [ ] **Step 7: Run the full backend suite one more time (final regression pass)**

Run: `cd backend && .venv/bin/pytest -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/AgentUsageTab.tsx frontend/src/components/AgentDetailPanel.tsx frontend/src/components/RoomView.tsx frontend/src/styles.css
git commit -m "feat: add Usage tab and live WS updates for instructions/skill-toggle events"
```
