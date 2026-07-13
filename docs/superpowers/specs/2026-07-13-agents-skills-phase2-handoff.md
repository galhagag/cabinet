# Agents Skills — Phase 2+ Handoff

This document hands off the work Phase 1 deliberately deferred. It assumes
zero context: read it cold, and you should know what's left, why each piece
is its own project, and what to resolve before designing each one.

## Where things stand

Phase 1 shipped in [PR #26](https://github.com/galhagag/cabinet/pull/26)
(merged to `main`). It replaced the room's "Upload skill" button with a full
"Agents Skills" tab: click into Data Expert or FCE to see **Instructions**
(per-room, per-agent context), **Skills** (upload + enable/disable toggle),
and **Usage** (token stats) — plus three disabled placeholder tabs, **Tools**,
**MCPs**, and **Memory**, which this document covers.

Background reading, in order:
- [Design spec](2026-07-13-agents-skills-design.md) — the product/architecture
  decisions behind Phase 1.
- [Implementation plan](../plans/2026-07-13-agents-skills-config.md) — the
  8-task build, including a mid-flight plan correction (Task 2/3 boundary)
  worth reading before touching `orchestrator.py`.

**Current architecture, in one paragraph:** `Orchestrator.compiled_prompt()`
(`backend/app/agents/orchestrator.py`) builds each agent's system prompt as
`baseline ⊕ enabled skills ⊕ room enrichment ⊕ per-agent instructions`,
append-only — nothing added by a skill, room, or agent-detail edit can ever
override the global baseline. Per-agent, per-room state lives on `RoomAgent`
(`instructions` column) and a `room_skill_overrides` table (row presence =
disabled, keeping a shared/global skill's on/off state scoped to the room
that toggled it). The frontend's `AgentDetailPanel.tsx` owns the tab strip —
adding a real tab later means flipping that tab's `comingSoon: true` to
`false` and wiring in a content component, same pattern as the Skills/Usage
tabs already there.

None of the three remaining tabs exist as more than a disabled button today.
**Tools and MCP in particular are not "finish the UI for an existing
feature" — the LLM backends currently never send a `tools` parameter at
all** (`backend/app/agents/foundry_client.py`), so both require new
execution paths in the orchestrator, not just frontend work.

## Remaining work

### 1. Tools (largest, most architecturally invasive)

**What it is:** Let an agent invoke real functions mid-turn (e.g. "look up
this rule," "run a calculation") instead of only producing text.

**Why it's separate:** Today, one agent turn is exactly one LLM call →
one message (`_run_mention_reply` / `run_autonomous_loop` in
`orchestrator.py`). Tool-calling requires a sub-loop *within* a turn: LLM
requests a tool → orchestrator executes it → result feeds back to the LLM →
repeat until the LLM produces final text. That interacts with the existing
cycle-budget system (does a tool round-trip consume a cycle? almost
certainly not — a "cycle" should stay one visible chat message) and with
`LLMBackend.complete()`'s signature, which has no concept of tools or of a
non-final response today.

**What Phase 1's research surfaced** (see the design spec's Sources): the
dominant pattern across Claude's own Tools docs, Vertex AI, and Bedrock is
*deny tools by default, grant the minimum set each tier needs* — and tool
*description* quality is reported as the single biggest lever on whether a
model uses a tool correctly. Both should shape the per-agent "add tool"
UI's defaults, not just the backend.

**Open questions to resolve before brainstorming this:**
- What tools would Data Expert/FCE actually use? This is a product question
  (e.g. "query the data catalog," "look up a regulatory rule," "calculate a
  risk score") that has to be answered before any engineering starts —
  don't let this become plumbing with no tools behind it.
- Does a tool call consume a cycle? Get a budget-accounting answer before
  writing code, not during review.
- What's the execution sandbox for a tool that isn't a pure lookup (e.g.
  touches the room's Google Drive connection, or an external API)? This is
  a compliance-sensitive product (AML/regulatory onboarding) — tool
  execution needs the same security scrutiny as any other write path.

### 2. MCP (depends on Tools)

**What it is:** Let an agent connect to external MCP servers as a source of
tools, rather than only hand-built ones.

**Why it comes after Tools, not in parallel:** MCP servers are exposed *as*
tools to the model (Anthropic's own Managed Agents architecture requires
every `mcp_servers` entry to be referenced by an `mcp_toolset` in the same
`tools` array) — so the tool-calling execution path has to exist first.
Building MCP registration UI before Tools exists would have nothing to
plug into.

**What Phase 1's research surfaced:**
- Auth should be kept separate from server registration: the agent
  definition declares *which* MCP server it connects to (name + URL); the
  *session* supplies auth via a reference to a pre-registered credential
  vault. For this product, that split matters even more than usual — a
  customer's core-banking API credentials must never live inside a reusable
  agent definition.
- The default permission policy for MCP tool calls in Anthropic's own
  connector is `always_ask` (human approval per call) — worth defaulting to
  the same here rather than silently trusting an external server.
- Real-world caveat: as of the research done for Phase 1, MCP write
  operations were reported as unstable for critical actions across several
  platforms. Consider scoping the first version to **read-only** MCP
  connections and revisit write access later.

**Open questions to resolve before brainstorming this:**
- Room-scoped, global, or both (mirroring how skills work today)?
- What does "connection health" look like in the UI — is a broken MCP
  server a silent failure, a visible error state, or does it just drop out
  of the available tool list?

### 3. Memory (independent, smaller, vaguer)

**What it is:** A persistent store of facts an agent should always
remember for a room, beyond the message window.

**Why it's separate:** `Orchestrator._history_as_turns()` only looks back
`Settings.history_window` messages. A multi-week onboarding engagement will
exceed that — this is a real, already-observed gap, not a hypothetical.

**Open questions to resolve before brainstorming this (this one is
genuinely less scoped than Tools/MCP — expect the brainstorming session to
spend more time here):**
- Is "memory" actually a distinct feature, or is the real fix smarter
  truncation (e.g. summarizing dropped-out-of-window history into a compact
  block, appended the same way instructions are today)? Don't assume a
  dedicated memory store is the right architecture before considering this.
- If it is a distinct store: who writes to it — the agent itself (e.g. an
  explicit "remember this" action, which implies Tools exists first), a
  human, or an automatic summarization job?
- Per-agent (like instructions) or shared across both agents in a room
  (like the original room enrichment)?

### 4. Small polish items (bundle these — each is hours, not a project)

- **Instructions preview / version history.** The design spec deferred
  these as "workflow affordances on the Instructions tab," not separate
  tabs. Note before building version history: `AuditLog` already records a
  `room_agent_instructions_updated` action, but its `detail` JSON currently
  only stores `{"agent_key": ...}` — **not** the old or new text
  (`backend/app/api/rooms.py`, `update_room_agent_instructions`). Showing a
  real history means either enriching that audit entry with the actual
  text or adding a dedicated history table — it is not already there
  waiting to be surfaced.
- **Per-agent model/parameter selection.** Currently global
  (`Settings.foundry_model` etc.). Flagged in Phase 1's spec as having "no
  clear need yet" — worth confirming that's still true before building it.
- **WS events don't live-refresh an open tab** (final-review finding,
  Minor). `agent_instructions_updated` / `agent_skill_toggled` currently
  only produce a toast in `RoomView.tsx`; a second client with the
  Instructions or Skills tab already open won't see the change until they
  re-navigate. Fix means giving `AgentInstructionsTab`/`AgentSkillsTab` a
  way to hear those events (prop-drilled refresh callback, or a shared
  context) — no such wiring exists today.
- **Double toast on your own edit** (final-review finding, Minor). The
  acting user sees both a local "saved" toast and the broadcast echo. Fix
  is either suppressing the local toast (rely on the WS echo) or having the
  broadcast skip the originating client.

## Recommended sequencing

```
Tools ──depends on──> MCP
Memory            (independent — pick up anytime)
Polish items      (independent — pick up anytime, or bundle with any of the above)
```

Tools is the correct starting point if you're doing these roughly in order
— it's both the largest unknown and the hard prerequisite for MCP. Memory
and the polish items don't block or get blocked by anything above and can
be done in any order, by anyone, in parallel with Tools/MCP work.

## How to pick one up

Each of Tools, MCP, and Memory is its own sub-project: don't jump to an
implementation plan directly from this document. Run the brainstorming
skill fresh for whichever one you're picking up, pointing it at this
handoff doc and the Phase 1 design spec as background — each gets its own
spec → plan → implementation cycle, the same way Phase 1 did. The small
polish items are simple enough to skip straight to a plan once you've
decided which fix approach to take (noted above for each).
