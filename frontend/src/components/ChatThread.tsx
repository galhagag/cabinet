import { useEffect, useRef } from "react";
import type { MessageOut } from "../types";

function bubbleClass(msg: MessageOut): string {
  if (msg.sender_type === "system") return "msg msg-system";
  if (msg.sender_type === "human") return "msg msg-human";
  if (msg.agent_key === "data_expert") return "msg msg-agent msg-data_expert";
  if (msg.agent_key === "fce") return "msg msg-agent msg-fce";
  return "msg msg-agent";
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  return isNaN(d.getTime())
    ? iso
    : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export default function ChatThread({
  messages,
  thinkingAgents,
}: {
  messages: MessageOut[];
  thinkingAgents: Record<string, string>;
}) {
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const thinking = Object.entries(thinkingAgents);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, thinking.length]);

  return (
    <div className="chat-thread">
      {messages.length === 0 && (
        <div className="muted chat-empty">
          No messages yet. Say hello to the Cabinet — try mentioning @DataExpert or @FCE.
        </div>
      )}
      {messages.map((msg) => (
        <div key={msg.id} className={bubbleClass(msg)}>
          <div className="msg-header">
            <span className="msg-sender">{msg.sender_name}</span>
            {msg.cycle_number !== null && (
              <span className="cycle-chip" title="Autonomous cycle number">
                cycle {msg.cycle_number}
              </span>
            )}
            <span className="msg-time">{formatTime(msg.created_at)}</span>
          </div>
          <div className="msg-content">{msg.content}</div>
        </div>
      ))}
      {thinking.map(([key, name]) => (
        <div key={`thinking-${key}`} className={`msg msg-agent msg-${key} msg-thinking`}>
          <div className="msg-header">
            <span className="msg-sender">{name}</span>
          </div>
          <div className="msg-content thinking-dots">
            is thinking<span>.</span>
            <span>.</span>
            <span>.</span>
          </div>
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
