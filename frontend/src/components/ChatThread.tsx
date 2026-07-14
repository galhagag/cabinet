import { useEffect, useRef, useState } from "react";
import { getUserEmail } from "../api";
import { getActiveAccount, isEntraAuth } from "../auth";
import type { MessageOut } from "../types";
import { Avatar } from "./Avatar";
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";

function bubbleClass(msg: MessageOut, outgoing: boolean): string {
  let base: string;
  if (msg.sender_type === "system") base = "msg msg-system";
  else if (outgoing) base = "msg msg-outgoing";
  else if (msg.agent_key === "data_expert") base = "msg msg-incoming msg-data_expert";
  else if (msg.agent_key === "fce") base = "msg msg-incoming msg-fce";
  else base = "msg msg-incoming";
  return msg.superseded_at ? `${base} msg-superseded` : base;
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
  onEditMessage,
}: {
  messages: MessageOut[];
  thinkingAgents: Record<string, string>;
  onEditMessage: (messageId: string, content: string) => Promise<boolean>;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const editTextareaRef = useRef<HTMLTextAreaElement | null>(null);
  const shouldStickToBottomRef = useRef(true);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [editSaving, setEditSaving] = useState(false);
  const thinking = Object.entries(thinkingAgents);
  const currentUser = (
    isEntraAuth ? getActiveAccount()?.username ?? "" : getUserEmail()
  )
    .trim()
    .toLowerCase();
  const latestEditableHumanId = [...messages]
    .reverse()
    .find((msg) => msg.sender_type === "human" && !msg.superseded_at)?.id ?? null;

  const stopEditing = () => {
    setEditingId(null);
    setEditValue("");
    setEditSaving(false);
  };

  const syncScrollState = () => {
    const container = containerRef.current;
    if (!container) return;
    const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
    const nearBottom = distanceFromBottom < 72;
    shouldStickToBottomRef.current = nearBottom;
    setShowJumpToLatest(!nearBottom);
  };

  const jumpToLatest = () => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    shouldStickToBottomRef.current = true;
    setShowJumpToLatest(false);
  };

  useEffect(() => {
    syncScrollState();
  }, []);

  useEffect(() => {
    if (!shouldStickToBottomRef.current) {
      setShowJumpToLatest(true);
      return;
    }
    jumpToLatest();
  }, [messages.length, thinking.length]);

  useEffect(() => {
    if (!editingId) return;
    const editedMessage = messages.find((msg) => msg.id === editingId);
    if (!editedMessage || editedMessage.superseded_at || editingId !== latestEditableHumanId) {
      stopEditing();
    }
  }, [editingId, latestEditableHumanId, messages]);

  useEffect(() => {
    if (!editingId) return;
    editTextareaRef.current?.focus();
    editTextareaRef.current?.setSelectionRange(editValue.length, editValue.length);
  }, [editingId]);

  const startEditing = (msg: MessageOut) => {
    setEditingId(msg.id);
    setEditValue(msg.content);
    setEditSaving(false);
  };

  const saveEdit = async (messageId: string) => {
    const content = editValue.trim();
    if (!content || editSaving) return;
    setEditSaving(true);
    const saved = await onEditMessage(messageId, content);
    if (saved) {
      stopEditing();
      return;
    }
    setEditSaving(false);
  };

  return (
    <div className="chat-thread-wrap">
      <div className="chat-thread" ref={containerRef} onScroll={syncScrollState}>
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
        const canEdit = outgoing && msg.id === latestEditableHumanId && !msg.superseded_at;
        const isEditing = editingId === msg.id;
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
                {msg.edit_of_id && <span className="msg-badge">Edited</span>}
                <span className="msg-time">{formatTime(msg.created_at)}</span>
                {canEdit && !isEditing && (
                  <button
                    className="msg-edit-btn"
                    type="button"
                    onClick={() => startEditing(msg)}
                    aria-label="Edit message"
                  >
                    Edit
                  </button>
                )}
              </div>
              {isEditing ? (
                <div className="msg-edit-form">
                  <textarea
                    ref={editTextareaRef}
                    className="msg-edit-textarea"
                    value={editValue}
                    rows={4}
                    disabled={editSaving}
                    onChange={(event) => setEditValue(event.target.value)}
                  />
                  <div className="msg-edit-actions">
                    <button
                      className="btn btn-small"
                      type="button"
                      onClick={stopEditing}
                      disabled={editSaving}
                    >
                      Cancel
                    </button>
                    <button
                      className="btn btn-primary btn-small"
                      type="button"
                      onClick={() => void saveEdit(msg.id)}
                      disabled={editSaving || !editValue.trim()}
                    >
                      {editSaving ? "Saving…" : "Save"}
                    </button>
                  </div>
                </div>
              ) : (
                <div className="msg-content">
                  <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={markdownComponents}>
                    {msg.content}
                  </ReactMarkdown>
                </div>
              )}
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
      {showJumpToLatest && (
        <button className="chat-jump-to-latest" onClick={jumpToLatest}>
          Jump to latest
        </button>
      )}
    </div>
  );
}
