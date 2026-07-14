import { useEffect, useRef } from "react";
import { getUserEmail } from "../api";
import { getActiveAccount, isEntraAuth } from "../auth";
import type { MessageOut } from "../types";
import { Avatar } from "./Avatar";
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";

function bubbleClass(msg: MessageOut, outgoing: boolean): string {
  if (msg.sender_type === "system") return "msg msg-system";
  if (outgoing) return "msg msg-outgoing";
  if (msg.agent_key === "data_expert") return "msg msg-incoming msg-data_expert";
  if (msg.agent_key === "fce") return "msg msg-incoming msg-fce";
  return "msg msg-incoming";
}

function rowClass(msg: MessageOut, outgoing: boolean): string {
  if (msg.sender_type === "system") return "msg-row msg-row-system";
  return `msg-row ${outgoing ? "msg-row-outgoing" : "msg-row-incoming"}`;
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  return isNaN(d.getTime())
    ? iso
    : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatTokenCount(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

function TokenUsage({ input, output }: { input: number; output: number }) {
  const total = input + output;
  return (
    <div className="msg-usage" title={`${input} input tokens · ${output} output tokens`}>
      <span className="msg-usage-icon">⚡</span>
      {formatTokenCount(total)} tokens
      <span className="msg-usage-split">
        ({formatTokenCount(input)} in · {formatTokenCount(output)} out)
      </span>
    </div>
  );
}

const markdownComponents: Components = {
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ),
  table: ({ children }) => (
    <div className="md-table-wrap">
      <table>{children}</table>
    </div>
  ),
};

export default function ChatThread({
  messages,
  thinkingAgents,
}: {
  messages: MessageOut[];
  thinkingAgents: Record<string, string>;
}) {
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const thinking = Object.entries(thinkingAgents);
  const currentUser = (
    isEntraAuth ? getActiveAccount()?.username ?? "" : getUserEmail()
  )
    .trim()
    .toLowerCase();

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
      {messages.map((msg) => {
        const outgoing =
          msg.sender_type === "human" &&
          msg.sender_name.trim().toLowerCase() === currentUser;
        const showAvatar = msg.sender_type !== "system" && !outgoing;
        return (
          <div key={msg.id} className={rowClass(msg, outgoing)}>
            {showAvatar && (
              <Avatar
                name={msg.sender_name}
                agentKey={msg.agent_key}
                size={30}
                className="msg-avatar"
              />
            )}
            <div className={bubbleClass(msg, outgoing)}>
              <div className="msg-header">
                <span className="msg-sender">{msg.sender_name}</span>
                <span className="msg-time">{formatTime(msg.created_at)}</span>
              </div>
              <div className="msg-content">
                <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={markdownComponents}>
                  {msg.content}
                </ReactMarkdown>
              </div>
              {msg.sender_type === "agent" &&
                (msg.input_tokens !== null || msg.output_tokens !== null) && (
                  <TokenUsage input={msg.input_tokens ?? 0} output={msg.output_tokens ?? 0} />
                )}
            </div>
          </div>
        );
      })}
      {thinking.map(([key, name]) => (
        <div key={`thinking-${key}`} className="msg-row msg-row-incoming">
          <Avatar name={name} agentKey={key} size={30} className="msg-avatar" />
          <div className={`msg msg-incoming msg-${key} msg-thinking`}>
            <div className="msg-header">
              <span className="msg-sender">{name}</span>
            </div>
            <div className="msg-content thinking-dots">
              is thinking<span>.</span>
              <span>.</span>
              <span>.</span>
            </div>
          </div>
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
