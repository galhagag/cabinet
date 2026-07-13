import { useEffect, useState } from "react";
import { getRoomAgent, updateRoomAgentInstructions } from "../api";
import type { AgentKey } from "../types";
import { pushToast, toastError } from "../toast";

export default function AgentInstructionsTab({
  roomId,
  agentKey,
}: {
  roomId: string;
  agentKey: AgentKey;
}) {
  const [systemPrompt, setSystemPrompt] = useState<string | null>(null);
  const [instructions, setInstructions] = useState("");
  const [saved, setSaved] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    getRoomAgent(roomId, agentKey)
      .then((detail) => {
        setSystemPrompt(detail.system_prompt);
        setInstructions(detail.instructions);
        setSaved(detail.instructions);
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, [roomId, agentKey]);

  const save = async () => {
    setSaving(true);
    try {
      const updated = await updateRoomAgentInstructions(roomId, agentKey, instructions);
      setSaved(updated.instructions);
      pushToast("info", "Instructions saved");
    } catch (err) {
      toastError(err, "Failed to save instructions");
    } finally {
      setSaving(false);
    }
  };

  if (error) return <div className="inline-error">Could not load agent: {error}</div>;
  if (loading) return <div className="muted">Loading…</div>;

  return (
    <div className="agent-instructions-tab">
      <div className="field">
        <span className="field-label">System prompt (global baseline — read-only)</span>
        <pre className="system-prompt-view">{systemPrompt}</pre>
      </div>

      <label className="field">
        <span className="field-label">Instructions for this room (optional)</span>
        <textarea
          className="prompt-editor"
          rows={10}
          value={instructions}
          onChange={(e) => setInstructions(e.target.value)}
          placeholder="Add context specific to this customer engagement — e.g. system landscape, timelines, known constraints."
          spellCheck={false}
        />
      </label>

      <div className="agent-editor-footer">
        <button
          className="btn btn-primary"
          onClick={save}
          disabled={saving || instructions === saved}
        >
          {saving ? "Saving…" : instructions === saved ? "Saved" : "Save instructions"}
        </button>
      </div>
    </div>
  );
}
