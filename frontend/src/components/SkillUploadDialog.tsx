import { useEffect, useRef, useState } from "react";
import { listSkills, toggleSkill, uploadSkill } from "../api";
import type { AgentKey, SkillOut } from "../types";
import { pushToast, toastError } from "../toast";

const AGENTS: { key: AgentKey; label: string }[] = [
  { key: "data_expert", label: "Data Expert" },
  { key: "fce", label: "FCE" },
];

export default function SkillUploadDialog({ roomId }: { roomId: string }) {
  const [open, setOpen] = useState(false);
  const [agentKey, setAgentKey] = useState<AgentKey>("data_expert");
  const [skills, setSkills] = useState<SkillOut[] | null>(null);
  const [uploading, setUploading] = useState(false);
  const [togglingId, setTogglingId] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!open) return;
    setSkills(null);
    listSkills(roomId, agentKey)
      .then(setSkills)
      .catch((err) => {
        setSkills([]);
        toastError(err, "Failed to load skills");
      });
  }, [open, agentKey, roomId]);

  const upload = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file || uploading) return;
    setUploading(true);
    try {
      const skill = await uploadSkill(roomId, agentKey, file);
      setSkills((prev) => [...(prev ?? []), skill]);
      pushToast("info", `Skill "${skill.skill_name}" added to ${agentKey === "fce" ? "FCE" : "Data Expert"}`);
      if (fileRef.current) fileRef.current.value = "";
    } catch (err) {
      toastError(err, "Skill upload failed");
    } finally {
      setUploading(false);
    }
  };

  const toggle = async (skill: SkillOut) => {
    if (togglingId) return;
    setTogglingId(skill.id);
    try {
      const updated = await toggleSkill(roomId, agentKey, skill.id, !skill.enabled);
      setSkills((prev) =>
        (prev ?? []).map((s) => (s.id === updated.id ? updated : s)),
      );
      pushToast(
        "info",
        `Skill "${updated.skill_name}" ${updated.enabled ? "enabled" : "disabled"}`,
      );
    } catch (err) {
      toastError(err, "Failed to toggle skill");
    } finally {
      setTogglingId(null);
    }
  };

  return (
    <>
      <button className="btn" onClick={() => setOpen(true)}>
        Skills
      </button>
      {open && (
        <div className="modal-overlay" onClick={() => setOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Upload agent skill</h3>
              <button className="modal-close" onClick={() => setOpen(false)} aria-label="Close">
                ×
              </button>
            </div>

            <label className="field">
              <span className="field-label">Agent</span>
              <div className="agent-select">
                {AGENTS.map((a) => (
                  <button
                    key={a.key}
                    className={`btn btn-small ${agentKey === a.key ? "btn-primary" : ""}`}
                    onClick={() => setAgentKey(a.key)}
                  >
                    {a.label}
                  </button>
                ))}
              </div>
            </label>

            <label className="field">
              <span className="field-label">Skill file</span>
              <input ref={fileRef} type="file" accept=".md,.zip" />
            </label>

            <p className="muted skill-note">
              A <code>.md</code> file extends the agent's context directly; a <code>.zip</code>{" "}
              bundle must contain a <code>SKILL.md</code> at its root.
            </p>

            <button className="btn btn-primary" onClick={upload} disabled={uploading}>
              {uploading ? "Uploading…" : "Upload"}
            </button>

            <h4 className="skills-heading">
              Linked skills — {agentKey === "fce" ? "FCE" : "Data Expert"}
            </h4>
            <p className="muted skill-note">
              Toggles control whether a skill is compiled into this agent's
              system prompt. Disabled skills stay linked and can be re-enabled
              any time.
            </p>
            {skills === null && <div className="muted">Loading…</div>}
            {skills !== null && skills.length === 0 && (
              <div className="muted">No skills linked to this agent yet.</div>
            )}
            <ul className="skill-list">
              {(skills ?? []).map((s) => {
                const isGlobal = s.room_id === null;
                return (
                  <li
                    key={s.id}
                    className={`skill-item ${s.enabled ? "" : "skill-item-disabled"}`}
                  >
                    <span className="skill-name">{s.skill_name}</span>
                    <span className={`skill-type skill-type-${s.skill_type}`}>
                      {s.skill_type}
                    </span>
                    {isGlobal && (
                      <span className="skill-scope" title="Applies in every room; managed by platform admins">
                        global
                      </span>
                    )}
                    <span className="muted">{new Date(s.created_at).toLocaleString()}</span>
                    <label
                      className={`skill-toggle ${isGlobal ? "skill-toggle-locked" : ""}`}
                      title={
                        isGlobal
                          ? "Global skills are admin-managed"
                          : s.enabled
                            ? "Disable for this agent"
                            : "Enable for this agent"
                      }
                    >
                      <input
                        type="checkbox"
                        checked={s.enabled}
                        disabled={isGlobal || togglingId === s.id}
                        onChange={() => void toggle(s)}
                      />
                      <span className="skill-toggle-track" />
                      <span className="skill-toggle-label">
                        {s.enabled ? "On" : "Off"}
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
          </div>
        </div>
      )}
    </>
  );
}
