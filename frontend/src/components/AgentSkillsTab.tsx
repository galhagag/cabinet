import { useEffect, useRef, useState } from "react";
import { listSkills, toggleSkill, uploadSkill } from "../api";
import type { AgentKey, SkillOut } from "../types";
import { pushToast, toastError } from "../toast";

export default function AgentSkillsTab({
  roomId,
  agentKey,
  refreshSignal = 0,
}: {
  roomId: string;
  agentKey: AgentKey;
  refreshSignal?: number;
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
  }, [roomId, agentKey, refreshSignal]);

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
        A <code>.md</code> file up to <code>1 MB</code> extends the agent's context directly; a <code>.zip</code>{" "}
        bundle up to <code>5 MB</code> must contain a <code>SKILL.md</code> at its root.
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
