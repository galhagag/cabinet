# Admin Global Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a platform admin upload, list, and delete "global" skills (skills applied to an agent in every room) from the Admin panel, and add the missing DELETE capability end-to-end (blob storage → service → API → UI) that doesn't exist today.

**Architecture:** Backend already has upload/list for global skills (`AgentSkill.room_id = NULL`) — this plan adds a symmetric `DELETE /api/admin/agents/{agent_key}/skills/{skill_id}` route, backed by a new `SkillsService.delete()` method and a new `BlobStorageProvider.delete()` method on both storage backends. The frontend gets three new `api.ts` functions and a `GlobalSkillsSection` component embedded in each agent's existing `AdminPanel.tsx` card.

**Tech Stack:** FastAPI + SQLAlchemy async ORM + SQLite (tests)/Postgres (prod), React + TypeScript, pytest, `TestClient`.

## Global Constraints

- All three global-skill operations (upload/list/delete) are gated by `require_admin` ([deps.py:84-100](../../../backend/app/api/deps.py#L84-L100)) — same as the existing upload/list routes.
- Blob delete must be idempotent in both providers (missing blob is not an error) — the DB row deletion is what matters for correctness, and it must never be blocked by a storage-layer 404.
- No schema/migration changes — `RoomSkillOverride.skill_id` already has `ondelete="CASCADE"` ([models.py:232-234](../../../backend/app/db/models.py#L232-L234)), and the SQLite test harness already runs with `PRAGMA foreign_keys=ON` ([db/base.py:49-52](../../../backend/app/db/base.py#L49-L52)), so cascade deletes work in tests exactly as they will in Postgres.
- No enable/disable toggle for global skills — deleting is the only way to retract one; a room's own enable/disable stays exactly as `AgentSkillsTab`/`RoomSkillOverride` already work today.
- Backend tests run via `cd backend && .venv/bin/pytest tests/<file> -v`.
- Frontend has no automated test runner — verify with `cd frontend && npx tsc --noEmit`, plus a manual browser check of the new UI.

---

### Task 1: Blob provider `delete()` method

**Files:**
- Modify: `backend/app/services/blob_storage.py:11-14` (Protocol), `:17-37` (`LocalBlobStorageProvider`), `:40-76` (`AzureBlobStorageProvider`)
- Test: `backend/tests/test_blob_storage.py` (new)

**Interfaces:**
- Produces: `BlobStorageProvider.delete(path: str) -> None`, implemented on both `LocalBlobStorageProvider` and `AzureBlobStorageProvider`. Idempotent — deleting an already-missing blob does not raise.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_blob_storage.py`:

```python
"""LocalBlobStorageProvider.delete: idempotent removal of a stored blob."""
from app.services.blob_storage import LocalBlobStorageProvider


def test_delete_removes_the_blob(tmp_path):
    provider = LocalBlobStorageProvider(str(tmp_path))
    import asyncio

    asyncio.run(provider.upload("skills/global/fce/x.md", b"content"))
    target = tmp_path / "skills/global/fce/x.md"
    assert target.exists()

    asyncio.run(provider.delete("skills/global/fce/x.md"))
    assert not target.exists()


def test_delete_missing_blob_does_not_raise(tmp_path):
    provider = LocalBlobStorageProvider(str(tmp_path))
    import asyncio

    asyncio.run(provider.delete("skills/global/fce/never-uploaded.md"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_blob_storage.py -v`
Expected: FAIL with `AttributeError: 'LocalBlobStorageProvider' object has no attribute 'delete'`

- [ ] **Step 3: Implement `delete()` on both providers**

In `backend/app/services/blob_storage.py`, add `delete` to the `Protocol` (after `download`, line 14):

```python
class BlobStorageProvider(Protocol):
    async def upload(self, path: str, data: bytes) -> str: ...

    async def download(self, path: str) -> bytes: ...

    async def delete(self, path: str) -> None: ...
```

Add to `LocalBlobStorageProvider` (after its `download` method, line 37):

```python
    async def delete(self, path: str) -> None:
        self._resolve(path).unlink(missing_ok=True)
```

Add to `AzureBlobStorageProvider` (after its `download` method, line 76):

```python
    async def delete(self, path: str) -> None:
        from azure.core.exceptions import ResourceNotFoundError

        service = await self._get_service()
        blob = service.get_blob_client(
            container=self._settings.blob_container, blob=path
        )
        try:
            await blob.delete_blob()
        except ResourceNotFoundError:
            pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/pytest tests/test_blob_storage.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/blob_storage.py backend/tests/test_blob_storage.py
git commit -m "feat: add idempotent delete() to BlobStorageProvider"
```

---

### Task 2: `SkillsService.delete()` + `DELETE` admin route

**Files:**
- Modify: `backend/app/services/skills.py:51-109` (add method to `SkillsService`)
- Modify: `backend/app/api/admin.py:99-114` (add route after `list_global_skills`)
- Test: `backend/tests/test_admin_global_skills.py` (new)

**Interfaces:**
- Consumes: `BlobStorageProvider.delete(path: str) -> None` (Task 1); `AgentSkill` model ([models.py:195-215](../../../backend/app/db/models.py#L195-L215)); `get_skills_service` dependency ([deps.py:111-112](../../../backend/app/api/deps.py#L111-L112)).
- Produces: `SkillsService.delete(session, *, skill: AgentSkill, actor: str = "system") -> None`; route `DELETE /api/admin/agents/{agent_key}/skills/{skill_id}` returning 204.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_admin_global_skills.py`:

```python
"""Admin global skills: delete (room_id=NULL skills shared across every room)."""
from .conftest import make_room

MD_SKILL = b"# Global Screening Policy\nAlways screen counterparties above EUR 10k.\n"


def _upload_global_skill(client, agent_key: str = "fce") -> dict:
    resp = client.post(
        f"/api/admin/agents/{agent_key}/skills",
        files={"file": ("global.md", MD_SKILL, "text/markdown")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_delete_global_skill_removes_it_from_list(client):
    skill = _upload_global_skill(client)
    resp = client.delete(f"/api/admin/agents/fce/skills/{skill['id']}")
    assert resp.status_code == 204

    listed = client.get("/api/admin/agents/fce/skills").json()
    assert listed == []


def test_delete_unknown_skill_404s(client):
    resp = client.delete("/api/admin/agents/fce/skills/not-a-real-id")
    assert resp.status_code == 404


def test_delete_room_scoped_skill_via_admin_route_404s(client):
    room = make_room(client, "RoomScopedBank")
    resp = client.post(
        f"/api/rooms/{room['id']}/agents/fce/skills",
        files={"file": ("room.md", b"# Room Only\nLocal rule.", "text/markdown")},
    )
    assert resp.status_code == 201, resp.text
    room_skill = resp.json()

    resp = client.delete(f"/api/admin/agents/fce/skills/{room_skill['id']}")
    assert resp.status_code == 404


def test_delete_with_wrong_agent_key_404s(client):
    skill = _upload_global_skill(client, agent_key="fce")
    resp = client.delete(f"/api/admin/agents/data_expert/skills/{skill['id']}")
    assert resp.status_code == 404


def test_deleting_global_skill_removes_room_override(client):
    room = make_room(client, "OverrideCascadeBank")
    skill = _upload_global_skill(client)

    resp = client.put(
        f"/api/rooms/{room['id']}/agents/fce/skills/{skill['id']}",
        json={"enabled": False},
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

    resp = client.delete(f"/api/admin/agents/fce/skills/{skill['id']}")
    assert resp.status_code == 204

    listed = client.get(f"/api/rooms/{room['id']}/agents/fce/skills").json()
    assert listed == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_admin_global_skills.py -v`
Expected: FAIL — `405 Method Not Allowed` (no DELETE route registered yet)

- [ ] **Step 3: Add `SkillsService.delete()`**

In `backend/app/services/skills.py`, add after the `ingest` method (after line 109):

```python
    async def delete(
        self,
        session: AsyncSession,
        *,
        skill: AgentSkill,
        actor: str = "system",
    ) -> None:
        """Remove a global skill: its blob, its DB row, and an audit entry."""
        await self._blob.delete(skill.blob_path)
        session.add(
            AuditLog(
                room_id=skill.room_id,
                actor=actor,
                action="global_skill_deleted",
                detail={
                    "agent_key": skill.agent_key,
                    "skill_id": skill.id,
                    "skill_name": skill.skill_name,
                },
            )
        )
        await session.delete(skill)
        await session.commit()
```

- [ ] **Step 4: Add the `DELETE` route**

In `backend/app/api/admin.py`, add after `list_global_skills` (after line 113):

```python
@router.delete("/agents/{agent_key}/skills/{skill_id}", status_code=204)
async def delete_global_skill(
    agent_key: str,
    skill_id: str,
    session: AsyncSession = Depends(get_session),
    skills_service: SkillsService = Depends(get_skills_service),
    user_email: str = Depends(require_admin),
) -> None:
    skill = await session.get(AgentSkill, skill_id)
    if skill is None or skill.agent_key != agent_key or skill.room_id is not None:
        raise HTTPException(status_code=404, detail="global skill not found")
    await skills_service.delete(session, skill=skill, actor=user_email)
```

No new imports needed — `admin.py` already imports `HTTPException`,
`AgentSkill`, `get_skills_service`, and `require_admin` for the existing
routes.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_admin_global_skills.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Run the full backend suite to check for regressions**

Run: `cd backend && .venv/bin/pytest -v`
Expected: all tests pass (no regressions in `test_skills_upload.py`, `test_skill_toggle.py`, `test_hardening.py`, etc.)

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/skills.py backend/app/api/admin.py backend/tests/test_admin_global_skills.py
git commit -m "feat: add DELETE endpoint for admin global skills"
```

---

### Task 3: Admin-gating coverage for the new `DELETE` route

**Files:**
- Modify: `backend/tests/test_hardening.py` (add a new test near `test_admin_read_endpoints_denied_for_non_admin`, after line 292)

**Interfaces:**
- Consumes: `DELETE /api/admin/agents/{agent_key}/skills/{skill_id}` (Task 2).

- [ ] **Step 1: Write the failing test**

Add to the end of `backend/tests/test_hardening.py`:

```python
# ---------------------------------------------------------------------------
# Admin DELETE route must be gated exactly like the other admin writes
# ---------------------------------------------------------------------------
def test_admin_delete_skill_gated_by_allowlist(client, monkeypatch):
    from app.config import reset_settings_cache

    admin = {"X-User-Email": "boss@thetaray.com"}
    skill = client.post(
        "/api/admin/agents/fce/skills",
        files={"file": ("global.md", b"# Policy\nScreen everything.", "text/markdown")},
    ).json()

    monkeypatch.setenv("CABINET_ADMIN_EMAILS", "boss@thetaray.com")
    reset_settings_cache()
    try:
        denied = client.delete(f"/api/admin/agents/fce/skills/{skill['id']}")
        assert denied.status_code == 403

        allowed = client.delete(
            f"/api/admin/agents/fce/skills/{skill['id']}", headers=admin
        )
        assert allowed.status_code == 204
    finally:
        monkeypatch.delenv("CABINET_ADMIN_EMAILS")
        reset_settings_cache()
```

Note: the skill is uploaded *before* `CABINET_ADMIN_EMAILS` is set, while the
allowlist is still empty (open dev-mode access) — matching how
`test_admin_allowlist_gates_baseline_updates` ([test_hardening.py:203-222](../../../backend/tests/test_hardening.py#L203-L222))
sets up its fixture data before flipping the gate on.

- [ ] **Step 2: Run the test**

Run: `cd backend && .venv/bin/pytest tests/test_hardening.py::test_admin_delete_skill_gated_by_allowlist -v`
Expected: PASS. Unlike a typical red-green cycle, this is confirmatory —
Task 2 already wired `require_admin` onto the route the same way as every
other admin route, so this test's job is to lock that gating in place, not
to drive new production code. If it unexpectedly fails, re-check that the
route from Task 2 Step 4 depends on `require_admin`.

- [ ] **Step 3: Run the full hardening suite**

Run: `cd backend && .venv/bin/pytest tests/test_hardening.py -v`
Expected: all pass, including the new test

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_hardening.py
git commit -m "test: cover admin-gating for the global skill DELETE route"
```

---

### Task 4: Frontend `api.ts` — global skill client functions + 204 handling

**Files:**
- Modify: `frontend/src/api.ts:40-73` (`request<T>` helper), `:78-88` (Admin section)

**Interfaces:**
- Consumes: `SkillOut` type ([types.ts:144-153](../../../frontend/src/types.ts#L144-L153)).
- Produces: `uploadGlobalSkill(agentKey: string, file: File): Promise<SkillOut>`, `listGlobalSkills(agentKey: string): Promise<SkillOut[]>`, `deleteGlobalSkill(agentKey: string, skillId: string): Promise<void>`.

- [ ] **Step 1: Fix `request<T>` to handle 204 No Content**

The helper currently always calls `res.json()`, which throws on an empty
204 body — there are no existing `DELETE` calls in this client today, so
this gap has never been hit. In `frontend/src/api.ts`, replace the tail of
`request` (lines 70-73):

```ts
    throw new ApiError(res.status, detail);
  }

  return (await res.json()) as T;
}
```

with:

```ts
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}
```

- [ ] **Step 2: Add the global-skill client functions**

In `frontend/src/api.ts`, after `updateAgentConfig` (after line 88, still in
the `// --- Admin` section):

```ts
export const uploadGlobalSkill = (agentKey: string, file: File) => {
  const form = new FormData();
  form.append("file", file);
  return request<SkillOut>(`/api/admin/agents/${agentKey}/skills`, {
    method: "POST",
    body: form,
  });
};

export const listGlobalSkills = (agentKey: string) =>
  request<SkillOut[]>(`/api/admin/agents/${agentKey}/skills`);

export const deleteGlobalSkill = (agentKey: string, skillId: string) =>
  request<void>(`/api/admin/agents/${agentKey}/skills/${skillId}`, {
    method: "DELETE",
  });
```

`SkillOut` is already imported at the top of the file ([api.ts:15](../../../frontend/src/api.ts#L15)) — no import change needed.

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api.ts
git commit -m "feat: add client functions for admin global skills"
```

---

### Task 5: `AdminPanel.tsx` — `GlobalSkillsSection` UI

**Files:**
- Modify: `frontend/src/components/AdminPanel.tsx` (imports, new component, wire into `AgentEditor`)
- Modify: `frontend/src/styles.css` (one new rule near `.skill-toggle-btn`, line 1122-1124)

**Interfaces:**
- Consumes: `uploadGlobalSkill`, `listGlobalSkills`, `deleteGlobalSkill` (Task 4); `SkillOut`, `AgentKey` types; `pushToast`, `toastError` from `../toast`.

- [ ] **Step 1: Update imports**

In `frontend/src/components/AdminPanel.tsx`, replace the top of the file
(lines 1-4):

```tsx
import { useEffect, useState } from "react";
import { listAgentConfigs, updateAgentConfig } from "../api";
import type { AgentConfigOut } from "../types";
import { pushToast, toastError } from "../toast";
```

with:

```tsx
import { useEffect, useRef, useState } from "react";
import {
  deleteGlobalSkill,
  listAgentConfigs,
  listGlobalSkills,
  updateAgentConfig,
  uploadGlobalSkill,
} from "../api";
import type { AgentConfigOut, AgentKey, SkillOut } from "../types";
import { pushToast, toastError } from "../toast";
```

- [ ] **Step 2: Add the `GlobalSkillsSection` component**

In `frontend/src/components/AdminPanel.tsx`, add this new component after
the `AgentEditor` function closes (after line 50, before
`export default function AdminPanel()`):

```tsx
function GlobalSkillsSection({ agentKey }: { agentKey: AgentKey }) {
  const [skills, setSkills] = useState<SkillOut[] | null>(null);
  const [uploading, setUploading] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    setSkills(null);
    listGlobalSkills(agentKey)
      .then(setSkills)
      .catch((err) => {
        setSkills([]);
        toastError(err, "Failed to load global skills");
      });
  }, [agentKey]);

  const upload = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file || uploading) return;
    setUploading(true);
    try {
      const skill = await uploadGlobalSkill(agentKey, file);
      setSkills((prev) => [...(prev ?? []), skill]);
      pushToast("info", `Global skill "${skill.skill_name}" added`);
      if (fileRef.current) fileRef.current.value = "";
    } catch (err) {
      toastError(err, "Global skill upload failed");
    } finally {
      setUploading(false);
    }
  };

  const remove = async (skill: SkillOut) => {
    setDeletingId(skill.id);
    try {
      await deleteGlobalSkill(agentKey, skill.id);
      setSkills((prev) => (prev ?? []).filter((s) => s.id !== skill.id));
      pushToast("info", `Global skill "${skill.skill_name}" deleted`);
    } catch (err) {
      toastError(err, "Failed to delete global skill");
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="agent-skills-tab">
      <h4 className="skills-heading">Global skills — applied in every room</h4>
      <label className="field">
        <span className="field-label">Add a global skill</span>
        <input ref={fileRef} type="file" accept=".md,.zip" />
      </label>
      <p className="muted skill-note">
        A <code>.md</code> file extends the agent's context directly; a{" "}
        <code>.zip</code> bundle must contain a <code>SKILL.md</code> at its
        root. Individual rooms can still disable a global skill from their own
        Skills tab; deleting it here removes it everywhere.
      </p>
      <button className="btn btn-primary" onClick={upload} disabled={uploading}>
        {uploading ? "Uploading…" : "Upload"}
      </button>

      {skills === null && <div className="muted">Loading…</div>}
      {skills !== null && skills.length === 0 && (
        <div className="muted">No global skills for this agent yet.</div>
      )}
      <ul className="skill-list">
        {(skills ?? []).map((s) => (
          <li key={s.id} className="skill-item">
            <span className="skill-name">{s.skill_name}</span>
            <span className={`skill-type skill-type-${s.skill_type}`}>{s.skill_type}</span>
            <span className="muted">{new Date(s.created_at).toLocaleString()}</span>
            <button
              className="btn btn-small skill-delete-btn"
              onClick={() => void remove(s)}
              disabled={deletingId === s.id}
            >
              {deletingId === s.id ? "…" : "Delete"}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

- [ ] **Step 3: Wire it into `AgentEditor`**

In `frontend/src/components/AdminPanel.tsx`, the `AgentEditor` function's
`return` currently ends with (lines 43-48):

```tsx
      <div className="agent-editor-footer">
        <button className="btn btn-primary" onClick={save} disabled={saving || !dirty || !prompt.trim()}>
          {saving ? "Saving…" : dirty ? "Save baseline prompt" : "Saved"}
        </button>
      </div>
    </section>
  );
}
```

Add `<GlobalSkillsSection agentKey={config.agent_key} />` right after the
footer `</div>` and before `</section>`:

```tsx
      <div className="agent-editor-footer">
        <button className="btn btn-primary" onClick={save} disabled={saving || !dirty || !prompt.trim()}>
          {saving ? "Saving…" : dirty ? "Save baseline prompt" : "Saved"}
        </button>
      </div>
      <GlobalSkillsSection agentKey={config.agent_key} />
    </section>
  );
}
```

- [ ] **Step 4: Add the delete-button CSS rule**

In `frontend/src/styles.css`, right after `.skill-toggle-btn` (lines 1122-1124):

```css
.skill-toggle-btn {
  margin-left: auto;
}
```

add:

```css
.skill-delete-btn {
  margin-left: auto;
}
```

- [ ] **Step 5: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors

- [ ] **Step 6: Manual verification in the browser**

Run: `cd frontend && npm run dev` (and `cd backend && .venv/bin/uvicorn app.main:app --reload` if not already running)

In the browser:
1. Open the app, click the **Admin** nav tab.
2. Under either agent's card, use "Add a global skill" to upload a small
   `.md` file (e.g. a file containing `# Test Skill\nSome content.`).
3. Confirm it appears in the list below with its name, type badge, and
   timestamp.
4. Click **Delete** on it, confirm it disappears from the list and a toast
   confirms the deletion.
5. Create a room, open its **Agents Skills** tab for the same agent, upload
   another global skill via Admin, and confirm it shows up there too
   (proving it's genuinely global, not room-scoped) — matches the existing
   `test_global_skill_applies_to_every_room` backend guarantee.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/AdminPanel.tsx frontend/src/styles.css
git commit -m "feat: add global skills upload/list/delete UI to Admin panel"
```
