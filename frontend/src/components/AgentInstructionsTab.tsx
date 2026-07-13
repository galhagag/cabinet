import { useEffect, useState } from "react";
import { getInstructionsHistory, getRoomAgent, updateRoomAgentInstructions } from "../api";
import type { AgentKey, InstructionsHistoryEntryOut } from "../types";
import { pushToast, toastError } from "../toast";

export default function AgentInstructionsTab({
  roomId,
  agentKey,
  refreshSignal = 0,
}: {
  roomId: string;
  agentKey: AgentKey;
  refreshSignal?: number;
}) {
  const [systemPrompt, setSystemPrompt] = useState<string | null>(null);
  const [instructions, setInstructions] = useState("");
  const [saved, setSaved] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState<InstructionsHistoryEntryOut[] | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);

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
  }, [roomId, agentKey, refreshSignal]);

  const save = async () => {
    setSaving(true);
    try {
      const updated = await updateRoomAgentInstructions(roomId, agentKey, instructions);
      setSaved(updated.instructions);
      pushToast("info", "Instructions saved");
      setHistory(null);
    } catch (err) {
      toastError(err, "Failed to save instructions");
    } finally {
      setSaving(false);
    }
  };

  const toggleHistory = () => {
    const next = !historyOpen;
    setHistoryOpen(next);
    if (next && history === null) {
      setHistoryLoading(true);
      getInstructionsHistory(roomId, agentKey)
        .then(setHistory)
        .catch((err) => toastError(err, "Failed to load instructions history"))
        .finally(() => setHistoryLoading(false));
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
        <button className="btn btn-small" onClick={toggleHistory}>
          {historyOpen ? "Hide history" : "Show history"}
        </button>
        <button
          className="btn btn-primary"
          onClick={save}
          disabled={saving || instructions === saved}
        >
          {saving ? "Saving…" : instructions === saved ? "Saved" : "Save instructions"}
        </button>
      </div>

      {historyOpen && (
        <div className="instructions-history">
          {historyLoading && <div className="muted">Loading history…</div>}
          {!historyLoading && history !== null && history.length === 0 && (
            <div className="muted">No previous edits.</div>
          )}
          {!historyLoading &&
            history !== null &&
            history.map((entry, i) => (
              <div key={i} className="instructions-history-entry">
                <div className="muted">
                  {new Date(entry.created_at).toLocaleString()} — {entry.actor}
                </div>
                <pre className="instructions-history-old">{entry.old_instructions || "(empty)"}</pre>
                <pre className="instructions-history-new">{entry.new_instructions || "(empty)"}</pre>
              </div>
            ))}
        </div>
      )}
    </div>
  );
}
