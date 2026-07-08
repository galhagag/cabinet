import { useCallback, useEffect, useRef, useState } from "react";
import { getRoom, listMessages, postMessage, resumeRoom } from "../api";
import type { MessageOut, RoomOut, RoomWsEvent } from "../types";
import { RoomSocket } from "../ws";
import { pushToast, toastError } from "../toast";
import ChatThread from "./ChatThread";
import Composer from "./Composer";
import LoopBudgetBanner from "./LoopBudgetBanner";
import DrivePanel from "./DrivePanel";
import InviteDialog from "./InviteDialog";
import SkillUploadDialog from "./SkillUploadDialog";

function agentDisplayName(room: RoomOut | null, agentKey: string): string {
  const found = room?.agents.find((a) => a.agent_key === agentKey);
  if (found) return found.display_name;
  return agentKey === "fce" ? "Financial Crime Expert" : agentKey === "data_expert" ? "Data Expert" : agentKey;
}

export default function RoomView({ roomId, onBack }: { roomId: string; onBack: () => void }) {
  const [room, setRoom] = useState<RoomOut | null>(null);
  const [messages, setMessages] = useState<MessageOut[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [thinkingAgents, setThinkingAgents] = useState<Record<string, string>>({});
  const [driveRefreshSignal, setDriveRefreshSignal] = useState(0);
  const roomRef = useRef<RoomOut | null>(null);
  roomRef.current = room;

  const mergeMessages = useCallback((incoming: MessageOut[]) => {
    setMessages((prev) => {
      const seen = new Set(prev.map((m) => m.id));
      const fresh = incoming.filter((m) => !seen.has(m.id));
      if (fresh.length === 0) return prev;
      return [...prev, ...fresh].sort(
        (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
      );
    });
  }, []);

  const handleWsEvent = useCallback(
    (event: RoomWsEvent) => {
      switch (event.type) {
        case "message_created": {
          mergeMessages([event.message]);
          if (event.message.sender_type === "agent" && event.message.agent_key) {
            const key = event.message.agent_key;
            setThinkingAgents((prev) => {
              if (!(key in prev)) return prev;
              const next = { ...prev };
              delete next[key];
              return next;
            });
          }
          break;
        }
        case "agent_thinking": {
          const name = agentDisplayName(roomRef.current, event.agent_key);
          setThinkingAgents((prev) => ({ ...prev, [event.agent_key]: name }));
          break;
        }
        case "room_paused":
          setThinkingAgents({});
          setRoom((prev) =>
            prev
              ? {
                  ...prev,
                  status: "paused_awaiting_human",
                  cycles_used: event.cycles_used,
                  cycle_limit: event.cycle_limit,
                }
              : prev,
          );
          break;
        case "room_resumed":
          setRoom((prev) => (prev ? { ...prev, status: "active" } : prev));
          break;
        case "skill_added":
          pushToast("info", `Skill added${event.skill_name ? `: ${event.skill_name}` : ""}`);
          break;
        case "skill_toggled":
          pushToast(
            "info",
            `Skill ${event.enabled ? "enabled" : "disabled"}${
              event.skill_name ? `: ${event.skill_name}` : ""
            }`,
          );
          break;
        case "drive_linked":
        case "drive_connected":
          setDriveRefreshSignal((n) => n + 1);
          break;
      }
    },
    [mergeMessages],
  );

  // Initial load.
  useEffect(() => {
    let cancelled = false;
    setRoom(null);
    setMessages([]);
    setLoadError(null);
    Promise.all([getRoom(roomId), listMessages(roomId)])
      .then(([r, msgs]) => {
        if (cancelled) return;
        setRoom(r);
        setMessages(msgs);
      })
      .catch((err) => {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [roomId]);

  // Live socket.
  useEffect(() => {
    const socket = new RoomSocket();
    socket.connect(roomId, handleWsEvent);
    return () => socket.close();
  }, [roomId, handleWsEvent]);

  const refreshRoom = useCallback(() => {
    getRoom(roomId)
      .then(setRoom)
      .catch(() => {
        // header refresh is best-effort
      });
  }, [roomId]);

  const send = async (content: string) => {
    setSending(true);
    try {
      const result = await postMessage(roomId, content);
      mergeMessages(result.messages);
      setRoom((prev) =>
        prev
          ? {
              ...prev,
              status: result.room_status,
              cycles_used: result.cycles_used,
              cycle_limit: result.cycle_limit,
            }
          : prev,
      );
    } catch (err) {
      toastError(err, "Failed to send message");
    } finally {
      setSending(false);
      setThinkingAgents({});
    }
  };

  const resume = async () => {
    setResuming(true);
    try {
      const result = await resumeRoom(roomId);
      mergeMessages(result.messages);
      setRoom((prev) =>
        prev
          ? {
              ...prev,
              status: result.room_status,
              cycles_used: result.cycles_used,
              cycle_limit: result.cycle_limit,
            }
          : prev,
      );
    } catch (err) {
      toastError(err, "Failed to resume agents");
      refreshRoom();
    } finally {
      setResuming(false);
      setThinkingAgents({});
    }
  };

  if (loadError) {
    return (
      <div className="room-view">
        <button className="btn btn-small" onClick={onBack}>
          ← Back to lobby
        </button>
        <div className="inline-error">Could not load room: {loadError}</div>
      </div>
    );
  }

  return (
    <div className="room-view">
      <header className="room-header">
        <div className="room-header-left">
          <button className="btn btn-small" onClick={onBack}>
            ←
          </button>
          <div>
            <h2 className="room-title">{room ? room.customer_name : "Loading…"}</h2>
            {room && (
              <span
                className={`badge ${
                  room.status === "paused_awaiting_human" ? "badge-paused" : "badge-active"
                }`}
              >
                {room.status === "paused_awaiting_human" ? "Paused — awaiting human" : "Active"}
              </span>
            )}
          </div>
        </div>
        <div className="room-header-actions">
          <DrivePanel roomId={roomId} refreshSignal={driveRefreshSignal} />
          <InviteDialog roomId={roomId} />
          <SkillUploadDialog roomId={roomId} />
        </div>
      </header>

      {room && (
        <LoopBudgetBanner
          status={room.status}
          cyclesUsed={room.cycles_used}
          cycleLimit={room.cycle_limit}
          onResume={resume}
          resuming={resuming}
        />
      )}

      <ChatThread messages={messages} thinkingAgents={thinkingAgents} />

      <Composer
        onSend={(content) => void send(content)}
        sending={sending}
        disabled={!room}
        disabledHint={!room ? "Loading room…" : undefined}
      />
    </div>
  );
}
