import { useState } from "react";
import type { RoomAgentOut } from "../types";
import AgentDetailPanel from "./AgentDetailPanel";

export default function AgentsSkillsView({
  roomId,
  agents,
  instructionsRefreshSignal = 0,
  skillsRefreshSignal = 0,
}: {
  roomId: string;
  agents: RoomAgentOut[];
  instructionsRefreshSignal?: number;
  skillsRefreshSignal?: number;
}) {
  const [selected, setSelected] = useState<RoomAgentOut | null>(null);

  if (selected) {
    return (
      <AgentDetailPanel
        roomId={roomId}
        agent={selected}
        onBack={() => setSelected(null)}
        instructionsRefreshSignal={instructionsRefreshSignal}
        skillsRefreshSignal={skillsRefreshSignal}
      />
    );
  }

  return (
    <div className="agents-skills-view">
      <h2>Your team</h2>
      <p className="muted">
        Configure each agent's instructions, skills, and usage for this room.
      </p>
      <div className="agent-card-grid">
        {agents.map((a) => (
          <button
            key={a.agent_key}
            className={`agent-card agent-card-${a.agent_key}`}
            onClick={() => setSelected(a)}
          >
            <span className={`agent-chip agent-chip-${a.agent_key}`}>{a.display_name}</span>
            <span className="muted">Instructions · Skills · Usage</span>
          </button>
        ))}
      </div>
    </div>
  );
}
