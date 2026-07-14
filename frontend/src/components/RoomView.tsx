import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  editMessage,
  getRoom,
  getUserEmail,
  listMembers,
  listMessages,
  postMessage,
  resumeRoom,
} from "../api";
import type {
  MessageOut,
  RoomConnectionState,
  RoomMemberOut,
  RoomOut,
  RoomWsEvent,
} from "../types";
import { RoomSocket } from "../ws";
import { pushToast, toastError } from "../toast";
import { getActiveAccount, isEntraAuth } from "../auth";
import ChatThread from "./ChatThread";
import Composer from "./Composer";
import PausedBanner from "./PausedBanner";
import DrivePanel from "./DrivePanel";
import InviteDialog from "./InviteDialog";
import { AvatarCluster, type AvatarClusterItem } from "./Avatar";
import AgentsSkillsView from "./AgentsSkillsView";

function agentDisplayName(room: RoomOut | null, agentKey: string): string {
  const found = room?.agents.find((a) => a.agent_key === agentKey);
  if (found) return found.display_name;
  return agentKey === "fce" ? "Financial Crime Expert" : agentKey === "data_expert" ? "Data Expert" : agentKey;
}

const ROOM_LOAD_RETRY_DELAY_MS = 300;
const ROOM_LOAD_ATTEMPTS = 2;

function isRetryableRoomLoadError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 0;
}

async function withRoomLoadRetry<T>(load: () => Promise<T>): Promise<T> {
  let lastError: unknown;
  for (let attemptIndex = 0; attemptIndex < ROOM_LOAD_ATTEMPTS; attemptIndex += 1) {
    try {
      return await load();
    } catch (error) {
      lastError = error;
      if (!isRetryableRoomLoadError(error) || attemptIndex === ROOM_LOAD_ATTEMPTS - 1) {
        throw error;
      }
      await new Promise<void>((resolve) => {
        window.setTimeout(resolve, ROOM_LOAD_RETRY_DELAY_MS * (attemptIndex + 1));
      });
    }
  }
  throw lastError;
}

export default function RoomView({
  roomId,
  onClose,
  onActivity,
}: {
  roomId: string;
  onClose: () => void;
  onActivity: (roomId: string, patch: Partial<RoomOut>) => void;
}) {
  const [room, setRoom] = useState<RoomOut | null>(null);
  const [messages, setMessages] = useState<MessageOut[]>([]);
  const [members, setMembers] = useState<RoomMemberOut[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [thinkingAgents, setThinkingAgents] = useState<Record<string, string>>({});
  const [driveRefreshSignal, setDriveRefreshSignal] = useState(0);
  const [instructionsRefreshSignal, setInstructionsRefreshSignal] = useState(0);
  const [skillsRefreshSignal, setSkillsRefreshSignal] = useState(0);
  const [reloadToken, setReloadToken] = useState(0);
  const [activeTab, setActiveTab] = useState<"chat" | "agents">("chat");
  const [connectionState, setConnectionState] = useState<RoomConnectionState>("connecting");
  const roomRef = useRef<RoomOut | null>(null);
  roomRef.current = room;

  const mergeMessages = useCallback((incoming: MessageOut[]) => {
    setMessages((prev) => {
      if (incoming.length === 0) return prev;
      const merged = new Map(prev.map((message) => [message.id, message]));
      let changed = false;
      for (const message of incoming) {
        const existing = merged.get(message.id);
        if (!existing || JSON.stringify(existing) !== JSON.stringify(message)) {
          merged.set(message.id, message);
          changed = true;
        }
      }
      if (!changed) return prev;
      return [...merged.values()].sort(
        (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
      );
    });
  }, []);

  const markSupersededMessages = useCallback((ids: string[]) => {
    if (ids.length === 0) return;
    const idSet = new Set(ids);
    const supersededAt = new Date().toISOString();
    setMessages((prev) =>
      prev.map((message) =>
        idSet.has(message.id) && !message.superseded_at
          ? { ...message, superseded_at: supersededAt }
          : message,
      ),
    );
  }, []);

  const refreshRoom = useCallback(() => {
    getRoom(roomId)
      .then(setRoom)
      .catch(() => {
        // header refresh is best-effort
      });
  }, [roomId]);

  const resyncRoomState = useCallback(() => {
    listMessages(roomId)
      .then(mergeMessages)
      .catch(() => {
        // message resync is best-effort
      });
    refreshRoom();
  }, [mergeMessages, refreshRoom, roomId]);

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
        case "message_edited":
          markSupersededMessages(event.superseded_message_ids);
          break;
        case "skill_added":
          pushToast("info", `Skill added${event.skill_name ? `: ${event.skill_name}` : ""}`);
          break;
        case "agent_instructions_updated": {
          const currentIdentity = (isEntraAuth ? getActiveAccount()?.username : getUserEmail())?.toLowerCase();
          if (event.actor?.toLowerCase() !== currentIdentity) {
            pushToast(
              "info",
              `Instructions updated for ${agentDisplayName(roomRef.current, event.agent_key)}`,
            );
          }
          setInstructionsRefreshSignal((n) => n + 1);
          break;
        }
        case "agent_skill_toggled":
          pushToast(
            "info",
            `Skill ${event.enabled ? "enabled" : "disabled"} for ${agentDisplayName(
              roomRef.current,
              event.agent_key,
            )}`,
          );
          setSkillsRefreshSignal((n) => n + 1);
          break;
        case "drive_linked":
        case "drive_connected":
          setDriveRefreshSignal((n) => n + 1);
          break;
        case "desync":
          void listMessages(roomId)
            .then(mergeMessages)
            .catch(() => {
              // message resync is best-effort
            });
          refreshRoom();
          break;
      }
    },
    [markSupersededMessages, mergeMessages, refreshRoom, roomId],
  );

  // Initial load.
  useEffect(() => {
    let cancelled = false;
    setRoom(null);
    setMessages([]);
    setMembers([]);
    setLoadError(null);
    setThinkingAgents({});
    void withRoomLoadRetry(() => getRoom(roomId))
      .then((nextRoom) => {
        if (!cancelled) {
          setRoom(nextRoom);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setLoadError(err instanceof Error ? err.message : String(err));
        }
      });
    void withRoomLoadRetry(() => listMessages(roomId))
      .then((nextMessages) => {
        if (!cancelled) {
          mergeMessages(nextMessages);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          toastError(err, "Failed to load message history");
        }
      });
    listMembers(roomId)
      .then((m) => {
        if (!cancelled) setMembers(m);
      })
      .catch(() => {
        // header avatar cluster degrades gracefully without member details
      });
    return () => {
      cancelled = true;
    };
  }, [mergeMessages, reloadToken, roomId]);

  // Live socket.
  useEffect(() => {
    const socket = new RoomSocket();
    socket.connect(roomId, handleWsEvent, resyncRoomState, setConnectionState);
    return () => socket.close();
  }, [roomId, handleWsEvent, resyncRoomState]);

  // Mirror status + last message up to the sidebar so the chat list stays live.
  useEffect(() => {
    if (!room) return;
    const last = [...messages].reverse().find((message) => !message.superseded_at);
    onActivity(roomId, {
      status: room.status,
      cycles_used: room.cycles_used,
      cycle_limit: room.cycle_limit,
      last_message: last
        ? {
            sender_type: last.sender_type,
            sender_name: last.sender_name,
            agent_key: last.agent_key,
            content: last.content,
            created_at: last.created_at,
          }
        : null,
    });
  }, [room, messages, roomId, onActivity]);

  const send = async (content: string): Promise<boolean> => {
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
      return true;
    } catch (err) {
      toastError(err, "Failed to send message");
      return false;
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

  const handleEditMessage = async (messageId: string, content: string): Promise<boolean> => {
    try {
      const result = await editMessage(roomId, messageId, content);
      markSupersededMessages(result.superseded_message_ids);
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
      setThinkingAgents({});
      return true;
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        pushToast("error", "That message is no longer the latest editable turn.");
        void listMessages(roomId).then(mergeMessages).catch(() => undefined);
        refreshRoom();
      } else {
        toastError(err, "Failed to edit message");
      }
      return false;
    }
  };

  if (loadError) {
    return (
      <div className="room-view">
        <button className="btn btn-small" onClick={onClose}>
          ← Back
        </button>
        <div className="inline-error">Could not load room: {loadError}</div>
        <button className="btn btn-small" onClick={() => setReloadToken((value) => value + 1)}>
          Retry
        </button>
      </div>
    );
  }

  const clusterItems: AvatarClusterItem[] = [
    ...(room?.agents.map((a) => ({ name: a.display_name, agentKey: a.agent_key })) ?? []),
    ...members.map((m) => ({ name: m.display_name || m.user_email, agentKey: null })),
  ];
  const subtitle = room
    ? [...room.agents.map((a) => a.display_name), ...members.map((m) => m.display_name || m.user_email)].join(
        " · ",
      )
    : "";
  const connectionLabel =
    connectionState === "live"
      ? "Live"
      : connectionState === "reconnecting"
        ? "Reconnecting"
        : connectionState === "offline"
          ? "Offline"
          : "Connecting";

  return (
    <div className="room-view">
      <header className="room-header">
        <div className="room-header-left">
          <button className="btn-icon room-back" onClick={onClose} aria-label="Close chat">
            ←
          </button>
          <AvatarCluster items={clusterItems} size={40} max={5} />
          <div className="room-header-text">
            <h2 className="room-title">{room ? room.customer_name : "Loading…"}</h2>
            {room && (
              <span className="room-subtitle" title={subtitle}>
                {room.status === "paused_awaiting_human" ? (
                  <span className="room-status-paused">Paused — awaiting human</span>
                ) : (
                  <span className="room-status-active">{subtitle || "Active"}</span>
                )}
              </span>
            )}
          </div>
        </div>
        <div className="room-header-actions">
          <span className={`connection-pill connection-pill-${connectionState}`}>{connectionLabel}</span>
          <DrivePanel roomId={roomId} refreshSignal={driveRefreshSignal} />
          <InviteDialog roomId={roomId} />
        </div>
      </header>

      <nav className="room-tabs">
        <button
          className={`nav-link ${activeTab === "chat" ? "nav-active" : ""}`}
          onClick={() => setActiveTab("chat")}
        >
          Chat
        </button>
        <button
          className={`nav-link ${activeTab === "agents" ? "nav-active" : ""}`}
          onClick={() => setActiveTab("agents")}
        >
          Agents Skills
        </button>
      </nav>

      <div className="room-chat-pane" style={{ display: activeTab === "chat" ? "contents" : "none" }}>
        {room && <PausedBanner status={room.status} onResume={resume} resuming={resuming} />}
        <ChatThread
          messages={messages}
          thinkingAgents={thinkingAgents}
          onEditMessage={handleEditMessage}
        />
        <Composer
          onSend={send}
          sending={sending}
          disabled={!room}
          disabledHint={!room ? "Loading room…" : undefined}
        />
      </div>

      {activeTab === "agents" && room && (
        <AgentsSkillsView
          roomId={roomId}
          agents={room.agents}
          instructionsRefreshSignal={instructionsRefreshSignal}
          skillsRefreshSignal={skillsRefreshSignal}
        />
      )}
    </div>
  );
}
