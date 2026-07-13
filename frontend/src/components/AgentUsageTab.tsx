import { useEffect, useState } from "react";
import { getAgentUsage } from "../api";
import type { AgentKey, AgentUsageOut } from "../types";

export default function AgentUsageTab({
  roomId,
  agentKey,
}: {
  roomId: string;
  agentKey: AgentKey;
}) {
  const [usage, setUsage] = useState<AgentUsageOut | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setUsage(null);
    getAgentUsage(roomId, agentKey)
      .then(setUsage)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [roomId, agentKey]);

  if (error) return <div className="inline-error">Could not load usage: {error}</div>;
  if (!usage) return <div className="muted">Loading…</div>;

  return (
    <div className="agent-usage-tab">
      <div className="usage-stat-grid">
        <div className="usage-stat">
          <span className="usage-stat-value">{usage.message_count}</span>
          <span className="usage-stat-label">Replies in this room</span>
        </div>
        <div className="usage-stat">
          <span className="usage-stat-value">{usage.total_input_tokens.toLocaleString()}</span>
          <span className="usage-stat-label">Input tokens</span>
        </div>
        <div className="usage-stat">
          <span className="usage-stat-value">{usage.total_output_tokens.toLocaleString()}</span>
          <span className="usage-stat-label">Output tokens</span>
        </div>
      </div>
    </div>
  );
}
