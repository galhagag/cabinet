# Edit Latest User Message Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a room member edit only the latest human message they authored. The UI should preserve the visible transcript, visually retire the superseded turn and the dependent agent/system replies it triggered, and rerun the edited turn as a fresh exchange without destroying the audit trail.

**Architecture:** Keep the transcript append-first. An edit creates a new human `Message` row, links it to the prior row via `edit_of_id`, marks the superseded turn and its trailing non-human replies with `superseded_at`, and dispatches the replacement through the same orchestrator path used by a brand-new post. The chat thread keeps both versions visible, but room summaries, agent context compilation, and "current" UI state ignore superseded rows.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + Alembic + pytest (backend); React + TypeScript + Vite (`npm run build`) for the frontend.

## Reflection Of Data

| Surface | What the user sees | What the backend/database stores |
|---|---|---|
| Chat thread | The original latest user message and the agent/system replies it triggered become visually dimmed and non-editable. The replacement human message appears as a new row with an `Edited` badge. | Original rows stay in `messages`. `superseded_at` is set on the old human row and its trailing non-human reply block. The replacement row is inserted with `edit_of_id = <original message id>`. |
| Sidebar preview | The room preview follows the newest non-superseded message, never the retired one. | `rooms.py` last-message query filters out `superseded_at IS NOT NULL` rows when choosing the preview row. |
| Agent context | Future turns use only the edited content and later active history. Superseded content never re-enters the prompt window. | `Orchestrator._history_as_turns()` and `_first_speaker()` ignore superseded rows. |
| Audit trail | Members can still see that an edit happened; historical rows are not deleted. | `messages` retains both the original and replacement rows; `audit_log` records `message_edited` with actor, target id, replacement id, and superseded ids. |
| Live updates | Other open clients see the old turn grey out and the replacement exchange appear without a manual refresh. | Existing `message_created` events continue for the new rows. A new `message_edited` event carries the superseded ids so other clients can retire the stale rows in place. |

## Global Constraints

- Only the latest non-superseded human message in the room is editable, and only when `sender_name` matches the current member.
- Never rewrite `Message.content`. Editing must be modeled as replacement plus supersession metadata.
- The edit path must be atomic: never leave the room with superseded rows but no replacement message because a later step failed.
- The superseded reply block for this feature is simple by design: once the target is constrained to the latest human turn, every later `agent` or `system` row in that room belongs to the edited turn and is superseded with it.
- `list_messages` continues returning full room history, including superseded rows, because the UI needs them for visible provenance.
- Room-summary queries and agent-history queries must exclude superseded rows.
- Every edit mutation is room-member scoped via `require_room_member`; no admin-only path.
- The Alembic migration must chain from the current head `b7d4e1a9c3f2` and pass `backend/tests/test_migrations.py`.

## Task 1: Schema And Contracts

**Files:**
- Modify: `backend/app/db/models.py`
- Modify: `backend/app/schemas.py`
- Create: `backend/alembic/versions/<new_revision>_add_message_edit_chain.py`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`

**Interfaces:**
- `Message.edit_of_id: str | None` — points from the replacement message to the message it edited.
- `Message.superseded_at: datetime | None` — set once when a later edit retires this row from active history.
- `MessageEdit` request body with `content` only.
- `MessageEditResult` response body with `messages`, `superseded_message_ids`, `room_status`, `cycles_used`, and `cycle_limit`.
- `WsMessageEdited` event with `type`, `room_id`, `message_id`, `replacement_message_id`, and `superseded_message_ids`.

- [ ] Add nullable `edit_of_id` and `superseded_at` columns to `Message`.
- [ ] Extend `MessageOut` so the frontend can render both the replacement chain and superseded state.
- [ ] Add `MessageEdit` and `MessageEditResult` Pydantic models.
- [ ] Mirror the new response and event shapes in `frontend/src/types.ts`.
- [ ] Add `editMessage(roomId, messageId, content)` to `frontend/src/api.ts`.

## Task 2: Backend Edit Flow

**Files:**
- Modify: `backend/app/api/messages.py`
- Modify: `backend/app/agents/orchestrator.py`
- Modify: `backend/app/api/rooms.py`

**Interfaces:**
- New route: `POST /api/rooms/{room_id}/messages/{message_id}/edit`
- New orchestrator helpers:
  - `_dispatch_human_message(...)` or equivalent extracted shared helper for the existing post flow and the new edit flow
  - `supersede_latest_turn(...)` returning the ids retired by the edit

- [ ] Extract the shared "human turn dispatch" logic out of `handle_human_message()` so post and edit use the same reset, mention, and autonomous-loop path.
- [ ] Implement route validation in `messages.py`:
  - 404 when the target message is absent or belongs to another room.
  - 403 when the target is not a human message or was authored by a different member.
  - 409 when the target is no longer the latest non-superseded human message.
- [ ] Supersede the target row and every later non-human row in the room in the same transaction that inserts the replacement human row.
- [ ] Reuse the normal dispatch path for the replacement content so cycle reset, room resume, mentions, and follow-up agent replies behave exactly like a new post.
- [ ] Publish one `message_edited` event after commit, alongside the existing `message_created` events produced by the replacement flow.
- [ ] Record an `AuditLog(action="message_edited")` row with `target_message_id`, `replacement_message_id`, `superseded_message_ids`, and old/new content hashes or snippets.
- [ ] Update `_history_as_turns()` and `_first_speaker()` to ignore superseded rows.
- [ ] Update `_last_messages_by_room()` in `rooms.py` to prefer the latest non-superseded row for sidebar previews.

## Task 3: Frontend Thread UX

**Files:**
- Modify: `frontend/src/components/ChatThread.tsx`
- Modify: `frontend/src/components/RoomView.tsx`
- Modify: `frontend/src/styles.css`

**UI Rules:**
- Only one message in the thread can expose an edit affordance: the latest non-superseded human message, and only when it belongs to the current user.
- Editing is inline in the bubble, not a modal. This keeps the action local to the turn being replaced.
- The replacement row should show an `Edited` badge when `edit_of_id` is present.
- Superseded rows should remain readable but visually retired via subdued color/opacity and no action buttons.

- [ ] In `ChatThread.tsx`, compute the single editable message id by scanning from the bottom for the latest non-superseded human row.
- [ ] Show a small edit icon/button only on that row when it belongs to the current user.
- [ ] Swap that bubble into an inline textarea with `Cancel` and `Save` actions.
- [ ] Render a subtle `Edited` badge on replacement rows where `edit_of_id !== null`.
- [ ] Add a `msg-superseded` treatment in `styles.css` for retired human/agent/system rows.
- [ ] In `RoomView.tsx`, call the new edit API, mark superseded ids locally, merge the returned replacement messages, and keep room status/cycle counters in sync.
- [ ] Handle `message_edited` websocket events by marking superseded rows in place; rely on follow-on `message_created` events for the replacement rows.
- [ ] Surface a precise toast for 409 conflicts such as "That message is no longer the latest editable turn" and refresh room state after conflict responses.

## Task 4: Data-Presentation Edge Cases

- [ ] Ensure the room preview in the sidebar follows the latest non-superseded message, not `messages[messages.length - 1]` blindly. Update `RoomView`'s `onActivity()` mirroring logic to choose the last active row.
- [ ] Preserve token-usage chips on superseded agent replies so historical cost context is still visible.
- [ ] Keep mention behavior unchanged: editing a latest message that contains `@DataExpert` or `@FCE` should route through the same mention path as a new post.
- [ ] Treat system replies caused by the edited turn the same as agent replies: they are visible history, but superseded once the human turn is replaced.
- [ ] Leave `Composer.tsx` unchanged except for any optional disable-state wiring while an edit save is in flight.

## Task 5: Testing And Verification

**Backend tests:**
- Create: `backend/tests/test_message_edit.py`
- Modify: `backend/tests/test_rooms.py`
- Modify: `backend/tests/test_migrations.py`

**Frontend verification:**
- Run: `cd frontend && npm run build`

- [ ] Add backend coverage for:
  - successful edit of the latest authored human message
  - 403 on another member's message
  - 403 on agent/system messages
  - 409 when the target is no longer the latest human turn
  - room preview excluding superseded rows
  - websocket `message_edited` delivery
  - agent history excluding superseded rows
- [ ] Verify the Alembic upgrade path and schema integrity tests pass.
- [ ] Build the frontend and manually verify the room page with an edit flow:
  - edit icon only on the latest editable human bubble
  - superseded original and replies dim correctly
  - replacement row shows `Edited`
  - sidebar preview and active room header reflect the replacement exchange

## Out Of Scope

- Editing older human turns.
- Editing messages authored by other members.
- Editing agent or system messages.
- Hard-deleting superseded rows from the transcript.
- Any change to the room cycle-budget semantics beyond reusing the existing reset-on-human-input behavior.