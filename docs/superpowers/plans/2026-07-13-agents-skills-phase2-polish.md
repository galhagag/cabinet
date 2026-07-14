# Agents Skills — Phase 2 Polish Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the four small polish items deferred from Phase 1 of Agents Skills: instructions version history, live WS refresh of open Instructions/Skills tabs, and the double-toast-on-own-edit fix.

**Architecture:** No new tables, no new architecture. Instructions history reuses the existing immutable `AuditLog` audit trail by enriching its `detail` JSON with old/new text (mirrors how the row already exists per edit — Task 1). Live-refresh replicates the existing `driveRefreshSignal` counter-prop pattern already used for `DrivePanel` (Task 3). Double-toast dedup reuses the existing `getUserEmail()` self-comparison pattern already used in `ChatThread.tsx` (Task 4).

**Tech Stack:** FastAPI + SQLAlchemy (async) + Pydantic on the backend, pytest for backend tests; React + TypeScript + Vite on the frontend (no test framework installed — frontend tasks verify via `tsc` + manual dev-server walkthrough per project convention).

## Scope note

Per the handoff doc (`docs/superpowers/specs/2026-07-13-agents-skills-phase2-handoff.md`), Tools, MCP, and Memory are each separate sub-projects requiring their own brainstorming session before a plan exists — they are **not** covered here. Of the four "small polish items" listed in that doc, this plan covers three (instructions history, WS live-refresh, double toast). The fourth — per-agent model/parameter selection — was re-confirmed with the user on 2026-07-13 as still having no clear need, so no task is included for it; it stays deferred as-is.

## Global Constraints

- Room-scoped read/write endpoints must depend on `require_room_member` (existing convention in `backend/app/api/rooms.py`), not a weaker auth check.
- `AuditLog` rows are immutable — never update or delete an existing row; only ever `session.add()` new ones (see module docstring, `backend/app/db/models.py:1-5`).
- No new database tables or columns in this plan — every task reuses existing schema (`AuditLog.detail` JSON, existing WS event dicts).
- No new frontend dependencies (no test framework, no state-management library) — this frontend has none today and none of these tasks need one.
- Frontend API calls go through the shared `request()` helper in `frontend/src/api.ts` — no ad hoc `fetch()` calls.
- New/changed WS event payload shapes must be reflected in the `RoomWsEvent` union in `frontend/src/types.ts`.

---

### Task 1: Backend — instructions edit history (audit-log enrichment + read endpoint)

**Files:**
- Modify: `backend/app/schemas.py:39-41` (insert new schema after `InstructionsUpdate`)
- Modify: `backend/app/api/rooms.py:25-38` (import), `backend/app/api/rooms.py:359-391` (enrich existing endpoint), add new endpoint after it
- Test: `backend/tests/test_room_agent_instructions.py`

**Interfaces:**
- Produces: `InstructionsHistoryEntryOut` Pydantic model (`actor: str`, `old_instructions: str`, `new_instructions: str`, `created_at: datetime`) and `GET /api/rooms/{room_id}/agents/{agent_key}/instructions/history` → `list[InstructionsHistoryEntryOut]`, newest first. Task 2 (frontend) consumes this exact endpoint and field names.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_room_agent_instructions.py`:

```python
def test_instructions_history_records_old_and_new_text(client):
    room = make_room(client, "InstructionsHistoryBank")
    client.put(
        f"/api/rooms/{room['id']}/agents/fce/instructions",
        json={"instructions": "First version."},
    )
    client.put(
        f"/api/rooms/{room['id']}/agents/fce/instructions",
        json={"instructions": "Second version."},
    )
    resp = client.get(f"/api/rooms/{room['id']}/agents/fce/instructions/history")
    assert resp.status_code == 200
    entries = resp.json()
    assert len(entries) == 2
    # newest first
    assert entries[0]["old_instructions"] == "First version."
    assert entries[0]["new_instructions"] == "Second version."
    assert entries[0]["actor"]
    assert entries[1]["old_instructions"] == ""
    assert entries[1]["new_instructions"] == "First version."


def test_instructions_history_is_per_agent(client):
    room = make_room(client, "InstructionsHistoryBank2")
    client.put(
        f"/api/rooms/{room['id']}/agents/data_expert/instructions",
        json={"instructions": "Data Expert only."},
    )
    resp = client.get(f"/api/rooms/{room['id']}/agents/fce/instructions/history")
    assert resp.status_code == 200
    assert resp.json() == []


def test_instructions_history_non_member_403(client):
    room = make_room(client, "InstructionsHistoryBank3")
    resp = client.get(
        f"/api/rooms/{room['id']}/agents/fce/instructions/history",
        headers={"X-User-Email": "outsider@bank.example"},
    )
    assert resp.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_room_agent_instructions.py -k history -v`
Expected: FAIL — `test_instructions_history_records_old_and_new_text` and `test_instructions_history_is_per_agent` fail on `assert resp.status_code == 200` (actual 404, route doesn't exist yet); `test_instructions_history_non_member_403` fails the same way (404 instead of 403).

- [ ] **Step 3: Add the `InstructionsHistoryEntryOut` schema**

In `backend/app/schemas.py`, insert between the existing `InstructionsUpdate` and `AgentUsageOut` classes (currently lines 39-43):

```python
class InstructionsUpdate(BaseModel):
    instructions: str = ""


class InstructionsHistoryEntryOut(BaseModel):
    actor: str
    old_instructions: str
    new_instructions: str
    created_at: datetime


class AgentUsageOut(BaseModel):
```

- [ ] **Step 4: Enrich the audit entry and add the history endpoint**

In `backend/app/api/rooms.py`, add `InstructionsHistoryEntryOut` to the schema import block (alphabetical, before `InstructionsUpdate`):

```python
from ..schemas import (
    AgentUsageOut,
    CompiledPromptOut,
    InstructionsHistoryEntryOut,
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
```

Replace `update_room_agent_instructions` (lines 359-391) with:

```python
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

    old_instructions = room_agent.instructions
    room_agent.instructions = payload.instructions
    session.add(
        AuditLog(
            room_id=room_id,
            actor=user_email,
            action="room_agent_instructions_updated",
            detail={
                "agent_key": agent_key,
                "old_instructions": old_instructions,
                "new_instructions": payload.instructions,
            },
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


@router.get(
    "/{room_id}/agents/{agent_key}/instructions/history",
    response_model=list[InstructionsHistoryEntryOut],
)
async def get_instructions_history(
    room_id: str,
    agent_key: str,
    session: AsyncSession = Depends(get_session),
    _member: str = Depends(require_room_member),
) -> list[InstructionsHistoryEntryOut]:
    if agent_key not in AGENT_KEYS:
        raise HTTPException(status_code=400, detail=f"unknown agent: {agent_key}")

    result = await session.execute(
        select(AuditLog)
        .where(
            AuditLog.room_id == room_id,
            AuditLog.action == "room_agent_instructions_updated",
        )
        .order_by(AuditLog.created_at.desc())
    )
    return [
        InstructionsHistoryEntryOut(
            actor=entry.actor,
            old_instructions=entry.detail.get("old_instructions", ""),
            new_instructions=entry.detail.get("new_instructions", ""),
            created_at=entry.created_at,
        )
        for entry in result.scalars().all()
        if entry.detail.get("agent_key") == agent_key
    ]
```

(`_get_agent_config_and_room_agent`, `_room_agent_detail_out`, `AGENT_KEYS`, `AuditLog`, `select` are all already imported/defined above this point in the file — no other import changes needed.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_room_agent_instructions.py -v`
Expected: PASS — all tests in the file, including the 3 new ones.

- [ ] **Step 6: Run the full backend suite to check for regressions**

Run: `cd backend && python -m pytest tests -q`
Expected: PASS, no regressions.

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas.py backend/app/api/rooms.py backend/tests/test_room_agent_instructions.py
git commit -m "feat: record old/new text on instructions edits, add history endpoint"
```

---

### Task 2: Frontend — instructions history UI

**Files:**
- Modify: `frontend/src/types.ts:39-41` (add type)
- Modify: `frontend/src/api.ts:3-16` (import), `frontend/src/api.ts:120-128` (add API call)
- Modify: `frontend/src/components/AgentInstructionsTab.tsx` (full rewrite of the component)
- Modify: `frontend/src/styles.css:1014-1018` (footer layout), add new rules after `:1214`

**Interfaces:**
- Consumes: `GET /api/rooms/{roomId}/agents/{agentKey}/instructions/history` from Task 1, returning `{ actor, old_instructions, new_instructions, created_at }[]`.

- [ ] **Step 1: Add the `InstructionsHistoryEntryOut` type**

In `frontend/src/types.ts`, insert after the existing `InstructionsUpdate` interface (currently lines 39-41):

```ts
export interface InstructionsUpdate {
  instructions: string;
}

export interface InstructionsHistoryEntryOut {
  actor: string;
  old_instructions: string;
  new_instructions: string;
  created_at: string;
}
```

- [ ] **Step 2: Add the API call**

In `frontend/src/api.ts`, add `InstructionsHistoryEntryOut` to the type import block (line 3-16, alphabetical, after `InviteCreateOut`):

```ts
import type {
  AgentConfigOut,
  AgentUsageOut,
  CompiledPromptOut,
  GDriveAuthorizeOut,
  GDriveStatusOut,
  InstructionsHistoryEntryOut,
  InviteCreateOut,
  MessageOut,
  PostMessageResult,
  RoomAgentDetailOut,
  RoomMemberOut,
  RoomOut,
  SkillOut,
} from "./types";
```

Add a new export right after `updateRoomAgentInstructions` (currently ending at line 128):

```ts
export const updateRoomAgentInstructions = (
  roomId: string,
  agentKey: string,
  instructions: string,
) =>
  request<RoomAgentDetailOut>(`/api/rooms/${roomId}/agents/${agentKey}/instructions`, {
    method: "PUT",
    body: JSON.stringify({ instructions }),
  });

export const getInstructionsHistory = (roomId: string, agentKey: string) =>
  request<InstructionsHistoryEntryOut[]>(
    `/api/rooms/${roomId}/agents/${agentKey}/instructions/history`,
  );
```

- [ ] **Step 3: Add a history panel to `AgentInstructionsTab`**

Replace the full contents of `frontend/src/components/AgentInstructionsTab.tsx`:

```tsx
import { useEffect, useState } from "react";
import { getInstructionsHistory, getRoomAgent, updateRoomAgentInstructions } from "../api";
import type { AgentKey, InstructionsHistoryEntryOut } from "../types";
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
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState<InstructionsHistoryEntryOut[] | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);

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
      setHistory(null);
    } catch (err) {
      toastError(err, "Failed to save instructions");
    } finally {
      setSaving(false);
    }
  };

  const toggleHistory = () => {
    const next = !historyOpen;
    setHistoryOpen(next);
    if (next && history === null) {
      setHistoryLoading(true);
      getInstructionsHistory(roomId, agentKey)
        .then(setHistory)
        .catch((err) => toastError(err, "Failed to load instructions history"))
        .finally(() => setHistoryLoading(false));
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
        <button className="btn btn-small" onClick={toggleHistory}>
          {historyOpen ? "Hide history" : "Show history"}
        </button>
        <button
          className="btn btn-primary"
          onClick={save}
          disabled={saving || instructions === saved}
        >
          {saving ? "Saving…" : instructions === saved ? "Saved" : "Save instructions"}
        </button>
      </div>

      {historyOpen && (
        <div className="instructions-history">
          {historyLoading && <div className="muted">Loading history…</div>}
          {!historyLoading && history !== null && history.length === 0 && (
            <div className="muted">No previous edits.</div>
          )}
          {!historyLoading &&
            history !== null &&
            history.map((entry, i) => (
              <div key={i} className="instructions-history-entry">
                <div className="muted">
                  {new Date(entry.created_at).toLocaleString()} — {entry.actor}
                </div>
                <pre className="instructions-history-old">{entry.old_instructions || "(empty)"}</pre>
                <pre className="instructions-history-new">{entry.new_instructions || "(empty)"}</pre>
              </div>
            ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Add CSS**

In `frontend/src/styles.css`, change `.agent-editor-footer`'s justification (currently lines 1014-1018) so the two buttons sit at opposite ends:

```css
.agent-editor-footer {
  margin-top: 0.7rem;
  display: flex;
  justify-content: space-between;
}
```

Then add new rules directly after the existing `.system-prompt-view` block (after line 1214):

```css
.instructions-history {
  margin-top: 0.9rem;
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.instructions-history-entry {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 7px;
  padding: 0.6rem 0.8rem;
}

.instructions-history-old,
.instructions-history-new {
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
  font-size: 0.78rem;
  line-height: 1.45;
  white-space: pre-wrap;
  margin: 0.3rem 0 0;
}

.instructions-history-old {
  color: var(--text-muted);
  text-decoration: line-through;
}
```

- [ ] **Step 5: Typecheck**

Run: `cd frontend && npm run build`
Expected: builds cleanly, no TypeScript errors.

- [ ] **Step 6: Manually verify in the browser**

Start both servers per `README.md`:
```bash
cd backend && uvicorn app.main:app --reload      # terminal 1
cd frontend && npm run dev                        # terminal 2
```
Open `http://localhost:5173`, create or open a room, go to the **Agents Skills** tab → pick an agent → **Instructions** tab. Edit the instructions and save twice with different text. Click **Show history** and confirm both edits appear, newest first, each showing the old text (struck through) and new text, with a timestamp and actor email. Click **Hide history** and confirm the panel collapses.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/types.ts frontend/src/api.ts frontend/src/components/AgentInstructionsTab.tsx frontend/src/styles.css
git commit -m "feat: show instructions edit history in the Instructions tab"
```

---

### Task 3: Frontend — live WS refresh for open Instructions/Skills tabs

**Files:**
- Modify: `frontend/src/components/RoomView.tsx:36`, `:52-114`, `:300-302`
- Modify: `frontend/src/components/AgentsSkillsView.tsx` (full rewrite)
- Modify: `frontend/src/components/AgentDetailPanel.tsx` (full rewrite)
- Modify: `frontend/src/components/AgentInstructionsTab.tsx` (props + effect dep only — builds on Task 2's version)
- Modify: `frontend/src/components/AgentSkillsTab.tsx` (props + effect dep only)

**Interfaces:**
- Produces: `AgentInstructionsTab` and `AgentSkillsTab` both gain an optional `refreshSignal?: number` prop (default `0`) that, when it changes, triggers a refetch — same contract as `DrivePanel`'s existing `refreshSignal` prop (`frontend/src/components/DrivePanel.tsx:8,11,32`).

- [ ] **Step 1: Add two refresh-signal counters and bump them on the relevant WS events**

In `frontend/src/components/RoomView.tsx`, add state next to the existing `driveRefreshSignal` (currently line 36):

```tsx
const [driveRefreshSignal, setDriveRefreshSignal] = useState(0);
const [instructionsRefreshSignal, setInstructionsRefreshSignal] = useState(0);
const [skillsRefreshSignal, setSkillsRefreshSignal] = useState(0);
```

In the `handleWsEvent` switch (currently lines 89-106), bump the matching signal alongside each existing toast:

```tsx
case "agent_instructions_updated":
  pushToast(
    "info",
    `Instructions updated for ${agentDisplayName(roomRef.current, event.agent_key)}`,
  );
  setInstructionsRefreshSignal((n) => n + 1);
  break;
case "agent_skill_toggled":
  pushToast(
    "info",
    `Skill ${event.enabled ? "enabled" : "disabled"} for ${agentDisplayName(
      roomRef.current,
      event.agent_key,
    )}`,
  );
  setSkillsRefreshSignal((n) => n + 1);
  break;
```

- [ ] **Step 2: Thread the signals down to `AgentsSkillsView`**

In `frontend/src/components/RoomView.tsx`, update the render call (currently lines 300-302):

```tsx
{activeTab === "agents" && room && (
  <AgentsSkillsView
    roomId={roomId}
    agents={room.agents}
    instructionsRefreshSignal={instructionsRefreshSignal}
    skillsRefreshSignal={skillsRefreshSignal}
  />
)}
```

- [ ] **Step 3: Thread the signals through `AgentsSkillsView` and `AgentDetailPanel`**

Replace the full contents of `frontend/src/components/AgentsSkillsView.tsx`:

```tsx
import { useState } from "react";
import type { RoomAgentOut } from "../types";
import AgentDetailPanel from "./AgentDetailPanel";

export default function AgentsSkillsView({
  roomId,
  agents,
  instructionsRefreshSignal = 0,
  skillsRefreshSignal = 0,
}: {
  roomId: string;
  agents: RoomAgentOut[];
  instructionsRefreshSignal?: number;
  skillsRefreshSignal?: number;
}) {
  const [selected, setSelected] = useState<RoomAgentOut | null>(null);

  if (selected) {
    return (
      <AgentDetailPanel
        roomId={roomId}
        agent={selected}
        onBack={() => setSelected(null)}
        instructionsRefreshSignal={instructionsRefreshSignal}
        skillsRefreshSignal={skillsRefreshSignal}
      />
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

Replace the full contents of `frontend/src/components/AgentDetailPanel.tsx`:

```tsx
import { useState } from "react";
import type { RoomAgentOut } from "../types";
import AgentInstructionsTab from "./AgentInstructionsTab";
import AgentSkillsTab from "./AgentSkillsTab";
import AgentUsageTab from "./AgentUsageTab";

type Tab = "instructions" | "skills" | "usage" | "tools" | "mcps" | "memory";

const TABS: { key: Tab; label: string; comingSoon?: boolean }[] = [
  { key: "instructions", label: "Instructions" },
  { key: "skills", label: "Skills" },
  { key: "usage", label: "Usage" },
  { key: "tools", label: "Tools", comingSoon: true },
  { key: "mcps", label: "MCPs", comingSoon: true },
  { key: "memory", label: "Memory", comingSoon: true },
];

export default function AgentDetailPanel({
  roomId,
  agent,
  onBack,
  instructionsRefreshSignal = 0,
  skillsRefreshSignal = 0,
}: {
  roomId: string;
  agent: RoomAgentOut;
  onBack: () => void;
  instructionsRefreshSignal?: number;
  skillsRefreshSignal?: number;
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
          <AgentInstructionsTab
            roomId={roomId}
            agentKey={agent.agent_key}
            refreshSignal={instructionsRefreshSignal}
          />
        )}
        {tab === "skills" && (
          <AgentSkillsTab
            roomId={roomId}
            agentKey={agent.agent_key}
            refreshSignal={skillsRefreshSignal}
          />
        )}
        {tab === "usage" && <AgentUsageTab roomId={roomId} agentKey={agent.agent_key} />}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Accept the signal in `AgentInstructionsTab` and `AgentSkillsTab`**

In `frontend/src/components/AgentInstructionsTab.tsx` (the version from Task 2), change the function signature and fetch effect:

```tsx
export default function AgentInstructionsTab({
  roomId,
  agentKey,
  refreshSignal = 0,
}: {
  roomId: string;
  agentKey: AgentKey;
  refreshSignal?: number;
}) {
```

```tsx
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
  }, [roomId, agentKey, refreshSignal]);
```

In `frontend/src/components/AgentSkillsTab.tsx`, same shape:

```tsx
export default function AgentSkillsTab({
  roomId,
  agentKey,
  refreshSignal = 0,
}: {
  roomId: string;
  agentKey: AgentKey;
  refreshSignal?: number;
}) {
```

```tsx
  useEffect(() => {
    setSkills(null);
    listSkills(roomId, agentKey)
      .then(setSkills)
      .catch((err) => {
        setSkills([]);
        toastError(err, "Failed to load skills");
      });
  }, [roomId, agentKey, refreshSignal]);
```

- [ ] **Step 5: Typecheck**

Run: `cd frontend && npm run build`
Expected: builds cleanly, no TypeScript errors.

- [ ] **Step 6: Manually verify with two browser sessions**

Start both dev servers (see Task 2 Step 6). Open the same room in two browser windows (or one normal + one incognito, joined via an invite link), both with the same agent's **Instructions** tab open. In window A, edit and save the instructions. Confirm window B's textarea updates to the new text without needing to navigate away and back. Repeat for the **Skills** tab: toggle a skill on/off in window A and confirm window B's toggle state updates live.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/RoomView.tsx frontend/src/components/AgentsSkillsView.tsx frontend/src/components/AgentDetailPanel.tsx frontend/src/components/AgentInstructionsTab.tsx frontend/src/components/AgentSkillsTab.tsx
git commit -m "feat: live-refresh open Instructions/Skills tabs on WS updates"
```

---

### Task 4: Fix double toast on your own instructions edit

**Files:**
- Modify: `backend/app/api/rooms.py:383-390` (broadcast payload — builds on Task 1's version)
- Modify: `backend/tests/test_room_agent_instructions.py` (extend the existing WS test)
- Modify: `frontend/src/types.ts:192-196` (`WsAgentInstructionsUpdated`)
- Modify: `frontend/src/components/RoomView.tsx` (builds on Task 3's version)

**Interfaces:**
- Produces: the `agent_instructions_updated` WS event gains an `actor: string` field (the email of whoever made the edit).

- [ ] **Step 1: Write the failing backend test**

In `backend/tests/test_room_agent_instructions.py`, replace `test_ws_receives_agent_instructions_updated`:

```python
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
        assert event["actor"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python -m pytest tests/test_room_agent_instructions.py::test_ws_receives_agent_instructions_updated -v`
Expected: FAIL — `KeyError: 'actor'`, since the broadcast payload doesn't include it yet.

- [ ] **Step 3: Add `actor` to the broadcast payload**

In `backend/app/api/rooms.py`, in `update_room_agent_instructions` (the version from Task 1), change the `broker.publish` call:

```python
    await broker.publish(
        room_id,
        {
            "type": "agent_instructions_updated",
            "room_id": room_id,
            "agent_key": agent_key,
            "actor": user_email,
        },
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && python -m pytest tests/test_room_agent_instructions.py -v`
Expected: PASS, all tests in the file.

- [ ] **Step 5: Commit the backend change**

```bash
git add backend/app/api/rooms.py backend/tests/test_room_agent_instructions.py
git commit -m "feat: include actor email in agent_instructions_updated WS event"
```

- [ ] **Step 6: Add `actor` to the frontend WS event type**

In `frontend/src/types.ts`, update `WsAgentInstructionsUpdated` (currently lines 192-196):

```ts
export interface WsAgentInstructionsUpdated {
  type: "agent_instructions_updated";
  room_id: string;
  agent_key: string;
  actor: string;
}
```

- [ ] **Step 7: Skip the toast when the event was caused by the current user**

In `frontend/src/components/RoomView.tsx`, add the import and update the case (building on Task 3's version):

```tsx
import { getUserEmail } from "../api";
```

```tsx
case "agent_instructions_updated":
  if (event.actor !== getUserEmail()) {
    pushToast(
      "info",
      `Instructions updated for ${agentDisplayName(roomRef.current, event.agent_key)}`,
    );
  }
  setInstructionsRefreshSignal((n) => n + 1);
  break;
```

(The refresh signal still bumps unconditionally — the acting user's own tab should also pick up the canonical saved value, and `AgentInstructionsTab`'s save handler already sets local state from the response, so this is a harmless redundant fetch for the actor, not a behavior change.)

- [ ] **Step 8: Typecheck**

Run: `cd frontend && npm run build`
Expected: builds cleanly, no TypeScript errors.

- [ ] **Step 9: Manually verify with two browser sessions**

Using the same two-window setup as Task 3 Step 6: in window A, edit and save instructions. Confirm window A shows exactly **one** toast ("Instructions saved") — not two. Confirm window B still shows its "Instructions updated for …" toast.

- [ ] **Step 10: Commit the frontend change**

```bash
git add frontend/src/types.ts frontend/src/components/RoomView.tsx
git commit -m "fix: suppress duplicate toast for the user who made the instructions edit"
```

---

## Plan self-review notes

- **Coverage:** Task 1+2 = instructions history; Task 3 = WS live-refresh; Task 4 = double toast. Per-agent model selection intentionally has no task (confirmed still deferred, per Scope note above).
- **Skills flow is single-toast today** (confirmed via research: `AgentSkillsTab`'s `toggle()` pushes no local toast, only the WS echo shows one) — the doc's "double toast" description matches Instructions only, so Task 4 is scoped there and does not touch `agent_skill_toggled`.
- **No new migration** — Task 1 reuses the existing `AuditLog.detail` JSON column; filtering by `agent_key` happens in Python after the `room_id`-scoped query since JSON-key filtering isn't portable across the SQLite/Postgres split this project supports.
