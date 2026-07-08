import { useEffect, useState } from "react";
import { listAgentConfigs, updateAgentConfig } from "../api";
import type { AgentConfigOut } from "../types";
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
    </section>
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
