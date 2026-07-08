import { useCallback, useEffect, useState } from "react";
import { getUserEmail, joinRoom, setUserEmail } from "./api";
import { dismissToast, pushToast, subscribeToasts, toastError, type Toast } from "./toast";
import RoomList from "./components/RoomList";
import RoomView from "./components/RoomView";
import AdminPanel from "./components/AdminPanel";

type View = { name: "lobby" } | { name: "admin" } | { name: "room"; roomId: string };

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

export default function App() {
  const [view, setView] = useState<View>({ name: "lobby" });
  const [joining, setJoining] = useState(false);

  const openRoom = useCallback((roomId: string) => setView({ name: "room", roomId }), []);

  // Handle ?token= invite links on load.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const token = params.get("token");
    if (!token) return;
    setJoining(true);
    joinRoom(token, getUserEmail())
      .then((room) => {
        pushToast("info", `Joined room "${room.customer_name}"`);
        setView({ name: "room", roomId: room.id });
      })
      .catch((err) => toastError(err, "Failed to join room"))
      .finally(() => {
        setJoining(false);
        // Remove the token from the URL so refreshes do not re-join.
        window.history.replaceState({}, "", window.location.pathname);
      });
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-left">
          <span className="app-title" onClick={() => setView({ name: "lobby" })}>
            Cabinet of Experts <span className="app-subtitle">· ThetaRay Onboarding</span>
          </span>
        </div>
        <nav className="topbar-nav">
          <button
            className={`nav-link ${view.name !== "admin" ? "nav-active" : ""}`}
            onClick={() => setView({ name: "lobby" })}
          >
            Lobby
          </button>
          <button
            className={`nav-link ${view.name === "admin" ? "nav-active" : ""}`}
            onClick={() => setView({ name: "admin" })}
          >
            Admin
          </button>
        </nav>
        <div className="topbar-right">
          <UserEmailEditor />
        </div>
      </header>

      <main className="main">
        {joining && <div className="joining-note">Joining room from invite link…</div>}
        {view.name === "lobby" && <RoomList onOpenRoom={openRoom} />}
        {view.name === "admin" && <AdminPanel />}
        {view.name === "room" && (
          <RoomView roomId={view.roomId} onBack={() => setView({ name: "lobby" })} />
        )}
      </main>

      <Toasts />
    </div>
  );
}
