# Agents Skills — Room-Level Per-Agent Configuration (Phase 1)

## Goal

Turn the room-level "Upload skill" button into a fuller "Agents Skills" area
where a room member can click into either agent (Data Expert, FCE) and see a
dedicated page: instructions, skills (with enable/disable), and a usage
summary — plus placeholder tabs establishing where Tools, MCPs, and Memory
will land in future phases.

This is Phase 1 of a larger initiative. Tools and MCP *execution* are
explicitly out of scope here — they require a new tool-calling execution path
in the orchestrator and get their own specs once this phase ships.

## Non-goals

- Renaming or changing the global "Admin" nav tab / `AdminPanel.tsx`. Admins
  keep editing the global baseline `system_prompt` exactly as they do today.
- A dynamic agent roster. Data Expert and FCE stay hardcoded in
  `profiles.py`; this is about configuring the two agents in more depth, not
  creating new ones.
- Building Tools or MCP execution. Their tabs are visible but disabled.
- Deleting uploaded skills. "Add" and "toggle on/off" are in scope; removal
  is not.

## Information architecture

`RoomView` gains a tab switcher in place of the current single-view layout:

```
[ Chat ]  [ Agents Skills ]
```

Drive and Invite remain header-action buttons, unchanged. "Upload skill" is
removed from the header — its functionality moves into the new tab.

Selecting "Agents Skills" replaces the chat thread with the agent management
view (chat state is preserved underneath, not unmounted — same pattern as
today's WS-driven state). Two steps within this view:

1. **Agent list** — cards for Data Expert and FCE.
2. **Agent detail** (click a card, back arrow returns to the list) — a tab
   strip:
   - **Instructions** — this room's per-agent context (see below). Empty by
     default, optional. The agent's global `system_prompt` baseline is shown
     above it, read-only — editable only via the existing admin surface.
   - **Skills** — this agent's applicable skills (global + room-scoped) with
     an enable/disable toggle each, plus the existing add-skill upload
     (`.md`/`.zip`) relocated here from the old modal.
   - **Usage** — token usage for this agent in this room (message count,
     total input/output tokens), computed from existing `Message` rows.
   - **Tools** *(disabled — "coming soon")*
   - **MCPs** *(disabled — "coming soon")*
   - **Memory** *(disabled — "coming soon")*

## Data model changes

Two additions, both via a new Alembic migration (the project manages schema
with `alembic upgrade head` in staging/prod — see `backend/alembic/versions/`;
`create_all` is dev/test-only, per `db/base.py`):

1. **`RoomAgent.instructions: Text, nullable=False, default=""`** —
   `RoomAgent` already exists solely as a `(room_id, agent_key) →
   display_name` join row; this adds the column it was missing. One row per
   room per agent, so instructions are genuinely per-agent, not shared across
   both agents in the room.

2. **New table `room_skill_overrides`**:
   ```
   room_id    FK -> rooms.id, ON DELETE CASCADE
   skill_id   FK -> agent_skills.id, ON DELETE CASCADE
   PRIMARY KEY (room_id, skill_id)
   ```
   Row presence means "disabled in this room." No `enabled` boolean column —
   existence *is* the disabled state, so toggling on is a plain row delete
   (idempotent, no ambiguous states to reconcile).

   This is a room-scoped override rather than a column on `AgentSkill`
   itself because `AgentSkill.room_id` can be `NULL` (a global skill shared
   across every room). If "enabled" lived directly on that row, disabling a
   global skill in one room would silently disable it everywhere. The
   override table scopes the toggle to the room where the member clicked it,
   regardless of whether the skill is global or room-owned.

## API changes

All new/changed endpoints are gated by the existing `require_room_member`
dependency (same tier as today's skill-upload endpoint — any room member,
not owner-only, not admin-only). `agent_key` not in `AGENT_KEYS` returns 400,
matching the existing skills endpoints — no 404-vs-missing-row ambiguity for
`RoomAgent`, since room creation already seeds a `RoomAgent` row for every
key in `AGENT_KEYS` ([rooms.py:135](backend/app/api/rooms.py#L135)).

- **`GET /api/rooms/{room_id}/agents/{agent_key}`** *(new)* — returns
  `display_name`, `system_prompt` (read-only, from `AgentGlobalConfig`), and
  `instructions` (this room's `RoomAgent.instructions`) in one call, backing
  the detail page header + Instructions tab.

- **`PUT /api/rooms/{room_id}/agents/{agent_key}/instructions`** *(new)* —
  body `{instructions: str}`. Unlike the admin system-prompt editor, empty
  string is valid and expected (that's the "optional, empty by default"
  requirement) — no non-empty validation. Audit-logged as
  `room_agent_instructions_updated`.

- **`GET /api/rooms/{room_id}/agents/{agent_key}/skills`** *(existing,
  changed)* — response gains an `enabled: bool` field per skill, computed
  from `room_skill_overrides` (absent row ⇒ `true`).

- **`PUT /api/rooms/{room_id}/agents/{agent_key}/skills/{skill_id}`**
  *(new)* — body `{enabled: bool}`. Upserts or deletes the override row.
  Audit-logged as `room_skill_toggled`.

- **`GET /api/rooms/{room_id}/agents/{agent_key}/usage`** *(new)* — returns
  `{message_count, total_input_tokens, total_output_tokens}`, aggregated
  from `Message` rows where `room_id` and `agent_key` match and
  `sender_type = "agent"`. Read-only, no audit log needed.

- **`POST /api/rooms/{room_id}/agents/{agent_key}/skills`** *(existing,
  unchanged)* — upload flow stays exactly as-is, just surfaced from the new
  tab instead of the old modal.

## Prompt compilation changes

`prompt_compiler.compile_system_prompt` gains an `instructions` parameter,
appended as a new `## Agent Instructions (this room)` section *after* the
existing room-wide enrichment section:

```
baseline ⊕ enabled skills ⊕ shared room enrichment ⊕ per-agent instructions
```

`Orchestrator.compiled_prompt()`:
- fetches `RoomAgent.instructions` for the `(room.id, agent_key)` pair and
  passes it through,
- filters the skills query to exclude any `AgentSkill.id` present in
  `room_skill_overrides` for this room.

The existing invariant — compiled prompt always starts with the unmodified
global baseline — is preserved; this only adds a new append-only section at
the end.

## Real-time sync

Matching the existing `skill_added` broadcast, two new room WS events keep
multiple open clients in sync:
- `agent_instructions_updated` — `{room_id, agent_key}`
- `agent_skill_toggled` — `{room_id, agent_key, skill_id, enabled}`

## Permissions & edge cases

- Any room member (owner or not) can edit instructions and toggle skills —
  consistent with who can already upload skills today. No new owner-only
  tier introduced.
- Last-write-wins on instructions edits, no optimistic locking — consistent
  with the admin system-prompt editor's existing behavior.
- Skill toggles are idempotent (upsert/delete), so concurrent toggles just
  converge to whichever happened last.
- A skill toggled off is *not* deleted — it stays in storage, shown visibly
  disabled in the list, and is excluded only from prompt compilation.
- No special handling needed for toggling mid-loop: prompts are already
  recompiled from current row state on every turn.

## Placeholder tabs & roadmap

Recommended by researching current agent-builder practice (Anthropic's
Skills/MCP docs, Vertex AI Agent Builder, Bedrock Agents — see sources
below): tools/MCP separate *what an agent can do* from skills' *what an
agent knows*, which is why they need their own permission model later and
aren't just "skills with extra steps."

- **Tools** *(disabled tab)* — next spec: real function-calling wired into
  the orchestrator's turn loop.
- **MCPs** *(disabled tab)* — next spec: external MCP server registration
  per agent.
- **Memory** *(disabled tab)* — `Orchestrator._history_as_turns` only looks
  back `history_window` messages; a multi-week onboarding engagement will
  exceed that. A persistent "facts this agent should always remember" store
  is a real gap, flagged here though not designed yet.

Deliberately **not** given a tab, kept as roadmap notes only:
- *Guardrails* (tool-call authorization, rate limits) — meaningless before
  Tools exist; belongs inside that spec.
- *Testing/preview mode* and *instructions version history* — these are
  workflow affordances that belong *on* the Instructions tab (e.g. a
  "preview" or "history" button) later, not separate top-level tabs.
- *Per-agent model/parameter selection* — currently a global `Settings`
  concern; no clear need for per-agent control yet.

## Testing plan

- `prompt_compiler` — new tests asserting section ordering
  (baseline → skills → enrichment → instructions) and that empty
  instructions produce no extra section.
- `orchestrator` — `compiled_prompt()` includes `RoomAgent.instructions` and
  excludes overridden-disabled skills; global-skill override in one room
  doesn't leak into another room's compiled prompt.
- API tests — instructions PUT (member can, non-member 403, empty string
  accepted); skills toggle PUT (upsert/delete override, `GET` reflects
  `enabled`); usage GET aggregation correctness.
- Frontend — component tests for the new tab switcher, agent list/detail
  navigation, and the skills toggle UI, following existing component test
  conventions.

## Sources consulted

- [MCP connector — Claude Platform Docs](https://platform.claude.com/docs/en/managed-agents/mcp-connector)
- [Tools — Claude Platform Docs](https://platform.claude.com/docs/en/managed-agents/tools)
- [Skills explained: How Skills compares to prompts, Projects, MCP, and subagents](https://claude.com/blog/skills-explained)
- [Extending Claude's capabilities with skills and MCP](https://claude.com/blog/extending-claude-capabilities-with-skills-mcp-servers)
- [AI Agents in 2026: Tools, Memory, Evals, and Guardrails](https://andriifurmanets.com/blogs/ai-agents-2026-practical-architecture-tools-memory-evals-guardrails)
- [The 2026 Guide to AI Agent Builders (Composio)](https://composio.dev/content/best-ai-agent-builders-and-integrations)
