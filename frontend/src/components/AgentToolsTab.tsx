import { useEffect, useState } from "react";
import { listTools, toggleTool } from "../api";
import type { AgentKey, ToolOut } from "../types";
import { toastError } from "../toast";

export default function AgentToolsTab({
  roomId,
  agentKey,
}: {
  roomId: string;
  agentKey: AgentKey;
}) {
  const [tools, setTools] = useState<ToolOut[] | null>(null);
  const [togglingName, setTogglingName] = useState<string | null>(null);

  useEffect(() => {
    setTools(null);
    listTools(roomId, agentKey)
      .then(setTools)
      .catch((err) => {
        setTools([]);
        toastError(err, "Failed to load tools");
      });
  }, [roomId, agentKey]);

  const toggle = async (tool: ToolOut) => {
    setTogglingName(tool.name);
    try {
      const updated = await toggleTool(roomId, agentKey, tool.name, !tool.enabled);
      setTools((prev) => (prev ?? []).map((t) => (t.name === updated.name ? updated : t)));
    } catch (err) {
      toastError(err, "Failed to toggle tool");
    } finally {
      setTogglingName(null);
    }
  };

  return (
    <div className="agent-tools-tab">
      <h4 className="skills-heading">Tools for this agent</h4>
      {tools === null && <div className="muted">Loading…</div>}
      {tools !== null && tools.length === 0 && (
        <div className="muted">No tools available for this agent.</div>
      )}
      <ul className="skill-list">
        {(tools ?? []).map((t) => (
          <li key={t.name} className={`skill-item ${t.enabled ? "" : "skill-item-disabled"}`}>
            <span className="skill-name">{t.name}</span>
            <span className="muted">{t.description}</span>
            <button
              className="btn btn-small skill-toggle-btn"
              onClick={() => void toggle(t)}
              disabled={togglingName === t.name}
            >
              {togglingName === t.name ? "…" : t.enabled ? "Disable" : "Enable"}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
