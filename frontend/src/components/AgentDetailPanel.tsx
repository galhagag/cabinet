import { useState } from "react";
import type { RoomAgentOut } from "../types";
import AgentInstructionsTab from "./AgentInstructionsTab";
import AgentSkillsTab from "./AgentSkillsTab";
import AgentUsageTab from "./AgentUsageTab";

type Tab = "instructions" | "skills" | "usage" | "tools" | "mcps" | "memory";

const TABS: { key: Tab; label: string; comingSoon?: boolean }[] = [
  { key: "instructions", label: "Instructions" },
  { key: "skills", label: "Skills" },
  { key: "usage", label: "Usage" },
  { key: "tools", label: "Tools", comingSoon: true },
  { key: "mcps", label: "MCPs", comingSoon: true },
  { key: "memory", label: "Memory", comingSoon: true },
];

export default function AgentDetailPanel({
  roomId,
  agent,
  onBack,
  instructionsRefreshSignal = 0,
  skillsRefreshSignal = 0,
}: {
  roomId: string;
  agent: RoomAgentOut;
  onBack: () => void;
  instructionsRefreshSignal?: number;
  skillsRefreshSignal?: number;
}) {
  const [tab, setTab] = useState<Tab>("instructions");

  return (
    <div className="agent-detail-panel">
      <div className="agent-detail-header">
        <button className="btn-icon" onClick={onBack} aria-label="Back to agent list">
          ←
        </button>
        <span className={`agent-chip agent-chip-${agent.agent_key}`}>{agent.display_name}</span>
      </div>

      <nav className="agent-detail-tabs">
        {TABS.map((t) => (
          <button
            key={t.key}
            className={`nav-link ${tab === t.key ? "nav-active" : ""} ${
              t.comingSoon ? "agent-detail-tab-soon" : ""
            }`}
            onClick={() => !t.comingSoon && setTab(t.key)}
            disabled={t.comingSoon}
            title={t.comingSoon ? "Coming soon" : undefined}
          >
            {t.label}
            {t.comingSoon && <span className="agent-detail-tab-badge">soon</span>}
          </button>
        ))}
      </nav>

      <div className="agent-detail-tab-content">
        {tab === "instructions" && (
          <AgentInstructionsTab
            roomId={roomId}
            agentKey={agent.agent_key}
            refreshSignal={instructionsRefreshSignal}
          />
        )}
        {tab === "skills" && (
          <AgentSkillsTab
            roomId={roomId}
            agentKey={agent.agent_key}
            refreshSignal={skillsRefreshSignal}
          />
        )}
        {tab === "usage" && <AgentUsageTab roomId={roomId} agentKey={agent.agent_key} />}
      </div>
    </div>
  );
}
