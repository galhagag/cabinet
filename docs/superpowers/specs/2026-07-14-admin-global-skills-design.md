# Admin Panel — Manage Global Skills

## Goal

The backend already supports "global" skills (`AgentSkill.room_id = NULL`,
applied to an agent in every room) via
`POST /api/admin/agents/{agent_key}/skills` and
`GET /api/admin/agents/{agent_key}/skills` ([admin.py:71-113](../../../backend/app/api/admin.py#L71-L113)),
but there is no UI for it and no way to remove a global skill once uploaded.
This spec adds:

1. An "upload / list / delete" global-skills section to each agent's card in
   `AdminPanel.tsx`.
2. The missing delete capability end-to-end (blob + DB row + UI), since an
   admin currently has no way to retract a bad global upload short of
   touching the database and blob storage directly.

## Non-goals

- Enable/disable toggling for global skills. That's inherently room-scoped
  (`RoomSkillOverride`) and already lives in `AgentSkillsTab` — a global
  skill is always "on" until a room opts out, or an admin deletes it
  outright.
- Editing skill content after upload. Re-upload (as a new skill) is the only
  path, consistent with how room-scoped skills work today.
- Any change to the room-scoped skills UI/API (`AgentSkillsTab`,
  `/api/rooms/{room_id}/agents/{agent_key}/skills`) — untouched by this spec.

## API changes

New endpoint in [admin.py](../../../backend/app/api/admin.py), same
`require_admin` gate as the existing global-skill routes:

- **`DELETE /api/admin/agents/{agent_key}/skills/{skill_id}`** *(new)* —
  204 on success. 404 if the skill doesn't exist, isn't global
  (`room_id is not None`), or its `agent_key` doesn't match the path.
  Audit-logged as `global_skill_deleted` with `{agent_key, skill_id,
  skill_name}`.

No changes to the existing `POST`/`GET` global-skill routes.

## Service changes

[`SkillsService`](../../../backend/app/services/skills.py) gains a
`delete(session, *, skill: AgentSkill, actor: str) -> None` method mirroring
`ingest`'s shape: deletes the blob via the injected `BlobStorageProvider`,
deletes the `AgentSkill` row, writes the `AuditLog` entry, commits.

[`BlobStorageProvider`](../../../backend/app/services/blob_storage.py) protocol
gains `delete(path: str) -> None`, **idempotent in both implementations** —
deleting an already-missing blob is not an error, so `SkillsService.delete`
can call it unconditionally with no try/except of its own:
- `LocalBlobStorageProvider` — unlinks the file, ignoring `FileNotFoundError`.
- `AzureBlobStorageProvider` — calls `blob.delete_blob()`, catching and
  ignoring `azure.core.exceptions.ResourceNotFoundError`.

## Data model

No schema changes. Deleting an `AgentSkill` row already cascades to any
`RoomSkillOverride` rows referencing it
(`ondelete="CASCADE"` on `RoomSkillOverride.skill_id`,
[models.py:232-234](../../../backend/app/db/models.py#L232-L234)) — a room
that had disabled a global skill loses that now-meaningless override row for
free, no extra cleanup code needed.

## Frontend changes

- [`api.ts`](../../../frontend/src/api.ts): add `uploadGlobalSkill(agentKey,
  file)`, `listGlobalSkills(agentKey)`, `deleteGlobalSkill(agentKey,
  skillId)` — same request shape as the existing room-skill functions
  ([api.ts:164-185](../../../frontend/src/api.ts#L164-L185)).
- [`AdminPanel.tsx`](../../../frontend/src/components/AdminPanel.tsx): each
  `AgentEditor` card gets a new sub-section below the prompt textarea/save
  button:
  - file input (`.md`/`.zip`) + "Upload" button, same copy/constraints as
    `AgentSkillsTab` ("a `.md` file extends the agent's context directly; a
    `.zip` bundle must contain a `SKILL.md` at its root").
  - a list of this agent's global skills: name, type, created-at, "Delete"
    button per row.
  - loads on mount via `listGlobalSkills`, refreshes the local list on
    successful upload/delete (no extra round-trip fetch), toasts on success
    and on error — matching `AgentSkillsTab`'s existing interaction pattern.

## Permissions & edge cases

- All three operations (upload/list/delete) require `require_admin`, exactly
  like the existing global-skill routes.
- Deleting a global skill takes effect immediately for every room's next
  prompt compilation (compiled fresh each turn from current row state — same
  invariant as room-scoped skill toggles).
- A missing blob (e.g. already cleaned up out-of-band) never blocks deleting
  the DB row — both providers treat delete as idempotent (see above), so the
  row deletion — the part that actually matters for prompt compilation —
  always proceeds.

## Testing plan

- New `backend/tests/test_admin_global_skills.py`:
  - upload → shows up in `GET .../skills` → `DELETE` → gone from the list.
  - `DELETE` on a room-scoped (non-global) skill id, or a mismatched
    `agent_key`, returns 404.
  - deleting a global skill that a room had disabled via
    `RoomSkillOverride` also removes that override row (cascade).
- Extend the existing admin-gating tests in
  [`test_hardening.py`](../../../backend/tests/test_hardening.py)
  (`test_admin_allowlist_gates_baseline_updates`,
  `test_admin_read_endpoints_denied_for_non_admin`) to cover the new
  `DELETE` route alongside the existing upload/list ones.
- Frontend: no automated test runner in this repo (per the Phase 1 spec);
  verify via `npx tsc --noEmit` plus manually uploading and deleting a
  global skill in the browser Admin tab.
