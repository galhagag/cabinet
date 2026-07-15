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

function AgentEditor({ config, onSaved }: { config: AgentConfigOut; onSaved: (c: AgentConfigOut) => void }) {
  const [prompt, setPrompt] = useState(config.system_prompt);
  const [saving, setSaving] = useState(false);
  const dirty = prompt !== config.system_prompt;

  const save = async () => {
    if (!prompt.trim() || saving) return;
    setSaving(true);
    try {
      const updated = await updateAgentConfig(config.agent_key, prompt);
      onSaved(updated);
      pushToast("info", `Saved baseline prompt for ${config.display_name}`);
    } catch (err) {
      toastError(err, `Failed to save ${config.display_name}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className={`panel agent-editor agent-editor-${config.agent_key}`}>
      <div className="agent-editor-header">
        <h3>
          <span className={`agent-chip agent-chip-${config.agent_key}`}>{config.display_name}</span>
          <span className="muted agent-key-label">({config.agent_key})</span>
        </h3>
        <span className="muted">
          Last updated: {new Date(config.updated_at).toLocaleString()}
        </span>
      </div>
      <textarea
        className="prompt-editor"
        rows={14}
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        spellCheck={false}
      />
      <div className="agent-editor-footer">
        <button className="btn btn-primary" onClick={save} disabled={saving || !dirty || !prompt.trim()}>
          {saving ? "Saving…" : dirty ? "Save baseline prompt" : "Saved"}
        </button>
      </div>
      <GlobalSkillsSection agentKey={config.agent_key} />
    </section>
  );
}

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
        A <code>.md</code> file up to <code>1 MB</code> extends the agent's
        context directly; a <code>.zip</code> bundle up to <code>5 MB</code>
        must contain a <code>SKILL.md</code> at its root. Individual rooms can
        still disable a global skill from their own Skills tab; deleting it
        here removes it everywhere.
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

export default function AdminPanel() {
  const [configs, setConfigs] = useState<AgentConfigOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listAgentConfigs()
      .then((c) => setConfigs(c))
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  return (
    <div className="admin">
      <h2>Agent Administration — Global Baseline Prompts</h2>
      <p className="muted admin-note">
        These are the global baseline system prompts for every Cabinet Room. Per-room
        context enrichment is <strong>appended</strong> after the baseline (plus any
        acquired skills) and <strong>never overwrites</strong> it.
      </p>
      {error && <div className="inline-error">Could not load agent configs: {error}</div>}
      {configs === null && !error && <div className="muted">Loading agent configurations…</div>}
      {configs?.map((config) => (
        <AgentEditor
          key={config.agent_key}
          config={config}
          onSaved={(updated) =>
            setConfigs((prev) =>
              prev ? prev.map((c) => (c.agent_key === updated.agent_key ? updated : c)) : prev,
            )
          }
        />
      ))}
    </div>
  );
}
