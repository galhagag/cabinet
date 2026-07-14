# Agent Tools — Design Spec

## Goal

Let Data Expert and FCE invoke real functions mid-turn — look up a file in the
room's connected Google Drive, or search the web — instead of only producing
text. This is the first of the three sub-projects flagged in
[the Phase 2+ handoff doc](2026-07-13-agents-skills-phase2-handoff.md) and
is a hard prerequisite for MCP (MCP servers are exposed to the model *as*
tools, so this execution path has to exist first).

Ships with exactly two built-in tools — not empty plumbing:

1. **`drive_search`** — search/read files in the room's already-connected
   Google Drive folder.
2. **`web_search`** — general web search via the Tavily API.

## Non-goals

- MCP server registration or execution — separate sub-project, next up after
  this one ships.
- Memory — independent sub-project, picked up after MCP.
- Human-in-the-loop approval per tool call. Both tools are read-only lookups
  against data the room already has access to (Drive: via its own OAuth
  grant; web search: no side effects) — v1 auto-executes. Approval gating is
  worth revisiting if a future tool has side effects (writes, sends, etc.).
- User-uploaded/custom tools. The two tools are built-in, defined in code —
  there is no "add a tool" upload flow analogous to skill upload.
- A global admin surface for tool configuration. Tools have no editable
  fields (no equivalent of `AgentGlobalConfig.system_prompt`) — the only
  per-room lever is the existing-shape enable/disable toggle.
- A fourth LLM backend. All three that already exist (Mock, Foundry/Claude,
  AzureOpenAI/GPT) *do* gain tool support as part of this spec — that's in
  scope, symmetric with how `complete()` is implemented identically across
  all three today — but adding a new backend entirely is unrelated to this
  work.

## Architecture

Today, one agent turn is exactly one `LLMBackend.complete()` call → one
`Message` (`_run_mention_reply` / `run_autonomous_loop` in
`orchestrator.py`). Tool-calling needs a sub-loop *within* a turn: LLM
requests a tool → orchestrator executes it → result feeds back to the LLM →
repeat until the LLM produces final text.

- **`LLMBackend.complete()`** (`backend/app/agents/foundry_client.py`) gains
  a `tools: list[ToolSpec] | None = None` parameter. `ToolSpec` is a
  `{name, description, parameters}` dataclass, `parameters` being a JSON
  Schema dict — the same shape Claude's Messages API, Azure OpenAI's Chat
  Completions API, and MCP itself all already use for tool definitions, so
  it carries forward unchanged if/when MCP tools get mixed into this same
  list later.
- **`LLMResult`** gains `tool_calls: list[ToolCall] | None = None`
  (`ToolCall` = `{id, name, arguments}`). A backend returns `tool_calls`
  instead of (not in addition to) final `text` when the model wants to
  invoke a tool.
- **`ChatTurn`** is extended to optionally carry structured tool-call /
  tool-result content, not only a plain string, so a completed round-trip
  can be replayed back to the model in its own native format on the next
  `complete()` call. Each backend owns translating between this common
  shape and its vendor-specific wire format (Claude's `tool_use`/
  `tool_result` content blocks vs. OpenAI's `tool_calls`/`tool`-role
  messages) — `MockLLM` gets a deterministic scripted trigger (a magic
  phrase in the last turn, mirroring how it already triggers
  `HANDOFF_TO_HUMAN`) so tests can exercise the full loop with zero network
  calls.
- **`Orchestrator`** owns the loop, not any individual backend: call
  `complete()` → if `tool_calls` is present, execute each via a new
  `ToolExecutor` → append the results as turns → call `complete()` again →
  repeat until the model returns final text or a per-turn cap is hit
  (default 5 rounds; configurable via `Settings.max_tool_rounds`). On cap
  exceeded, one final `complete()` call is made *without* `tools` attached,
  forcing a text-only answer so a turn can never silently end with nothing
  to show. This is the only place all three backends' tool calls become
  visible in one shot, which the "show sources in the reply" requirement
  below needs.
- Tool execution failures (Drive API error, Tavily timeout, etc.) feed back
  as an error tool-result so the model can adapt or hand off — they do not
  raise `LLMError` or pause the room. Only a failure of the top-level
  `complete()` call itself (today's existing behavior) triggers `_fail_turn`.

## Tool registry & data model

Tools are code, not uploaded content, so there is no `AgentTool` table
mirroring `AgentSkill`. A new `backend/app/agents/tools.py` module holds a
static registry:

```python
TOOL_REGISTRY: dict[str, ToolDefinition]  # name -> {description, parameters, default_agents, executor}
```

Both `drive_search` and `web_search` default-enabled for both
`DATA_EXPERT_KEY` and `FCE_KEY`.

One new table, **`room_tool_overrides`**, byte-for-byte the same shape as
the existing `room_skill_overrides` (Phase 1's precedent for "room-scoped
disable of a thing that's otherwise on by default"):

```
room_id    FK -> rooms.id, ON DELETE CASCADE
tool_name  String, matches a TOOL_REGISTRY key
PRIMARY KEY (room_id, tool_name)
```

Row presence means "disabled in this room" — same idempotent
upsert/delete-on-toggle semantics as skill overrides. New Alembic migration.

`Orchestrator` gains `_enabled_tools(session, room, agent_key)`: filters
`TOOL_REGISTRY` to entries whose `default_agents` includes `agent_key` and
that have no `room_tool_overrides` row for this room, converting the
survivors into `ToolSpec`s passed to `complete()`.

## The two built-in tools

**`drive_search(query: str)`** — calls the Google Drive v3 `files.list`
REST endpoint directly via `httpx` (no new SDK — `requirements.txt`'s
`httpx` dependency already carries the comment "Google OAuth token exchange
/ Drive API," anticipating exactly this), scoped to the room's connected
`google_folder_id` and authenticated with
`GoogleOAuthService.ensure_fresh_access_token()` — the existing
refresh-on-demand helper already used for the Drive *connection* flow. No
new auth flow, no new secret: this reuses the room's own already-granted
OAuth scope, so it's a read-only lookup against data the room's members
already gave Cabinet access to, not a new trust boundary. If the room has no
connected Drive, the tool is simply omitted from `_enabled_tools` for that
room (nothing to search).

**`web_search(query: str)`** — calls Tavily's search API directly via
`httpx`: `POST https://api.tavily.com/search`, `Authorization: Bearer
<key>`, JSON body `{"query": ...}`. Also no new SDK — Tavily's API is plain
REST/JSON (verified against
[Tavily's API reference](https://docs.tavily.com/documentation/api-reference/endpoint/search)).
New secret `tavily-api-key`, provisioned through the existing
`SecretProvider`/Key Vault pattern — global, not per-room, since it's a
platform-level API key rather than something tied to one room's identity.

## Cycle accounting & safety

A tool round-trip does **not** consume one of the room's cycles —
`room.cycles_used` still increments exactly once per *turn* (per visible
chat message), regardless of how many tool calls it took to produce that
message. This matches the existing invariant that a cycle is "one visible
message," and is enforced simply by leaving `_claim_cycle` where it already
is (once, before the tool loop starts) and not calling it again inside the
loop.

The per-turn tool-round cap (default 5, see Architecture) is the
independent safety valve that stops a single runaway turn from looping
forever or from running up an unbounded Tavily/Drive API bill — it's an
in-memory counter scoped to one call of `_run_mention_reply` /
`run_autonomous_loop`'s per-turn loop, not persisted state.

## Message model & visible sources

Compliance reviewers need to see what a reply is actually based on, not
just trust the prose — this is a regulated onboarding product with an
existing immutable audit trail (`Message`, `AuditLog`).

`Message` gains a nullable `tool_invocations: JSON` column — populated once,
on the final agent `Message` for a turn (not one row per intermediate
round-trip), e.g.:

```json
[{"tool": "web_search", "query": "FATF rolling window AML guidance 2026"},
 {"tool": "drive_search", "query": "schema mapping doc", "result_count": 2}]
```

`ChatThread.tsx` renders a collapsed `▸ Sources (N)` disclosure under the
message bubble when `tool_invocations` is non-empty, listing each
invocation's tool + query.

## API changes

Mirrors the existing skills endpoints exactly (`require_room_member`,
`agent_key` validation, same response shapes):

- **`GET /api/rooms/{room_id}/agents/{agent_key}/tools`** *(new)* — list of
  `{name, description, enabled}` for every tool in this agent's
  `default_agents` set.
- **`PUT /api/rooms/{room_id}/agents/{agent_key}/tools/{tool_name}`**
  *(new)* — body `{enabled: bool}`. Upserts/deletes the
  `room_tool_overrides` row, same idempotent semantics as the skill toggle.
  Audit-logged as `room_tool_toggled`.

## Real-time sync & audit

- New WS event `agent_tool_toggled` — `{room_id, agent_key, tool_name,
  enabled}`, matching `agent_skill_toggled`'s precedent (including the
  known "doesn't live-refresh an open tab" gap already tracked for
  Instructions/Skills in the Phase 2 polish backlog — not re-solved here).
- New `AuditLog` action `tool_invoked` — `{tool_name, agent_key, query
  (truncated to a safe length), success: bool}`, one row per actual
  invocation. Truncated/summarized rather than storing full tool output, to
  avoid the audit trail becoming an unbounded copy of search-result content.
- New `AuditLog` action `room_tool_toggled` — `{tool_name, agent_key,
  enabled}`, matching `room_skill_toggled`.

## Frontend

`AgentDetailPanel.tsx`'s `tools` tab flips `comingSoon: true` → `false`. New
`AgentToolsTab.tsx`, modeled directly on `AgentSkillsTab.tsx`: list + toggle,
no upload affordance (nothing to upload).

## Permissions & edge cases

- Same tier as skill toggling: any room member, not owner-only — consistent
  with "who can already configure this agent today."
- Toggling a tool off doesn't cancel an in-flight turn already using it —
  same "recompiled from current row state on every turn" behavior as skill
  toggles.
- A room with no connected Drive simply never offers `drive_search` as an
  enabled tool — no error state, it's just absent from the list (same
  spirit as an agent with zero skills uploaded).
- Tavily/Drive API failures degrade to an error tool-result fed back to the
  model, never a paused room, never a lost turn.

## Testing plan

- `orchestrator` — tool round-trip completes and the final `Message` carries
  `tool_invocations`; the per-turn cap forces a final tools-less call
  instead of hanging; a tool execution failure feeds an error result back
  without raising `LLMError` or pausing the room; `cycles_used` increments
  exactly once regardless of how many tool rounds a turn took.
- Each backend (`FoundryLLM`, `AzureOpenAILLM`, `MockLLM`) — translation of
  `ToolSpec`/turns into the vendor wire format, and translation of a
  vendor tool-call response back into `LLMResult.tool_calls`.
- `drive_search` / `web_search` executors — unit tests against a mocked
  `httpx` transport (no live Google/Tavily calls in CI).
- API tests — tools GET/PUT toggle (member can, non-member 403, idempotent
  upsert/delete), `AuditLog` rows for both `tool_invoked` and
  `room_tool_toggled`.
- Frontend — no test runner exists in this repo (per Phase 1's established
  convention); verify via `npx tsc --noEmit` plus a manual dev-server
  walkthrough: Tools tab list/toggle, and a `▸ Sources` disclosure rendering
  under a reply that used a tool.
