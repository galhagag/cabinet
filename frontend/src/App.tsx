import { useCallback, useEffect, useState } from "react";
import { getUserEmail, joinRoom, listRooms, setUserEmail } from "./api";
import { dismissToast, pushToast, subscribeToasts, toastError, type Toast } from "./toast";
import Sidebar from "./components/Sidebar";
import RoomView from "./components/RoomView";
import AdminPanel from "./components/AdminPanel";
import type { RoomOut } from "./types";
import { getActiveAccount, isEntraAuth } from "./auth";

type View = { name: "empty" } | { name: "admin" } | { name: "room"; roomId: string };

function Toasts() {
  const [toasts, setToasts] = useState<Toast[]>([]);
  useEffect(() => subscribeToasts(setToasts), []);
  if (toasts.length === 0) return null;
  return (
    <div className="toast-stack" role="status">
      {toasts.map((t) => (
        <div key={t.id} className={`toast toast-${t.kind}`}>
          <span>{t.text}</span>
          <button className="toast-close" onClick={() => dismissToast(t.id)} aria-label="Dismiss">
            ×
          </button>
        </div>
      ))}
    </div>
  );
}

function UserEmailEditor() {
  const [editing, setEditing] = useState(false);
  const [email, setEmail] = useState(getUserEmail());
  const [draft, setDraft] = useState(email);

  const save = () => {
    setUserEmail(draft);
    setEmail(getUserEmail());
    setEditing(false);
  };

  if (!editing) {
    return (
      <button
        className="user-email"
        title="Click to change your identity"
        onClick={() => {
          setDraft(email);
          setEditing(true);
        }}
      >
        {email}
      </button>
    );
  }
  return (
    <span className="user-email-edit">
      <input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") save();
          if (e.key === "Escape") setEditing(false);
        }}
        autoFocus
        placeholder="you@thetaray.com"
      />
      <button className="btn btn-small" onClick={save}>
        Save
      </button>
    </span>
  );
}

function EntraAccountBadge() {
  const account = getActiveAccount();
  if (!account) return null;
  return (
    <span className="user-email-edit">
      <span className="user-email" title={account.username}>
        {account.name || account.username}
      </span>
      <button className="btn btn-small" onClick={() => void signOut()}>
        Sign out
      </button>
    </span>
  );
}

function SignInScreen() {
  return (
    <div className="app">
      <div className="joining-note">
        <p>Sign in with your Microsoft account to continue.</p>
        <button className="btn" onClick={() => void signIn()}>
          Sign in with Microsoft
        </button>
      </div>
    </div>
  );
}

export default function App() {
  const [view, setView] = useState<View>({ name: "empty" });
  const [joining, setJoining] = useState(false);
  const [rooms, setRooms] = useState<RoomOut[] | null>(null);
  const [roomsError, setRoomsError] = useState<string | null>(null);

  const refreshRooms = useCallback(() => {
    listRooms()
      .then((r) => {
        setRooms(r);
        setRoomsError(null);
      })
      .catch((err) => {
        setRoomsError(err instanceof Error ? err.message : String(err));
      });
  }, []);

  useEffect(refreshRooms, [refreshRooms]);

  const patchRoom = useCallback((roomId: string, patch: Partial<RoomOut>) => {
    setRooms((prev) => (prev ? prev.map((r) => (r.id === roomId ? { ...r, ...patch } : r)) : prev));
  }, []);

  const openRoom = useCallback((roomId: string) => setView({ name: "room", roomId }), []);

  // Handle ?token= invite links on load.
  useEffect(() => {
    if (isEntraAuth && !getActiveAccount()) return;
    const params = new URLSearchParams(window.location.search);
    const token = params.get("token");
    if (!token) return;
    setJoining(true);
    const identity = isEntraAuth
      ? getActiveAccount()?.username ?? "unknown"
      : getUserEmail();
    joinRoom(token, identity)
      .then((room) => {
        pushToast("info", `Joined room "${room.customer_name}"`);
        refreshRooms();
        setView({ name: "room", roomId: room.id });
      })
      .catch((err) => toastError(err, "Failed to join room"))
      .finally(() => {
        setJoining(false);
        // Remove the token from the URL so refreshes do not re-join.
        window.history.replaceState({}, "", window.location.pathname);
      });
  }, [refreshRooms]);

  if (isEntraAuth && !getActiveAccount()) {
    return <SignInScreen />;
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-left">
          <span className="app-title" onClick={() => setView({ name: "empty" })}>
            Cabinet of Experts <span className="app-subtitle">· ThetaRay Onboarding</span>
          </span>
        </div>
        <nav className="topbar-nav">
          <button
            className={`nav-link ${view.name !== "admin" ? "nav-active" : ""}`}
            onClick={() => setView({ name: "empty" })}
          >
            Chats
          </button>
          <button
            className={`nav-link ${view.name === "admin" ? "nav-active" : ""}`}
            onClick={() => setView({ name: "admin" })}
          >
            Admin
          </button>
        </nav>
        <div className="topbar-right">
          {isEntraAuth ? <EntraAccountBadge /> : <UserEmailEditor />}
        </div>
      </header>

      <div className={`app-body ${view.name !== "empty" ? "app-body-detail" : ""}`}>
        <Sidebar
          rooms={rooms}
          error={roomsError}
          selectedRoomId={view.name === "room" ? view.roomId : null}
          onSelectRoom={openRoom}
          onCreated={(room) => {
            refreshRooms();
            openRoom(room.id);
          }}
        />
        <main className="main-pane">
          {joining && <div className="joining-note">Joining room from invite link…</div>}
          {!joining && view.name === "admin" && <AdminPanel />}
          {!joining && view.name === "room" && (
            <RoomView key={view.roomId} roomId={view.roomId} onClose={() => setView({ name: "empty" })} onActivity={patchRoom} />
          )}
          {!joining && view.name === "empty" && (
            <div className="empty-state">
              <div className="empty-state-icon">💬</div>
              <h3>Select a Cabinet Room</h3>
              <p className="muted">Pick a room from the list on the left, or start a new one.</p>
            </div>
          )}
        </main>
      </div>

      <Toasts />
    </div>
  );
}
