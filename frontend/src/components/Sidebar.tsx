import { useMemo, useState, type FormEvent, type MouseEvent } from "react";
import { archiveRoom, createRoom, deleteRoom, unarchiveRoom } from "../api";
import type { RoomOut } from "../types";
import { toastError } from "../toast";
import Modal from "./Modal";
import RoomLogo from "./RoomLogo";
import RoomContextMenu from "./RoomContextMenu";

const NEW_ROOM_DIALOG_TITLE_ID = "new-room-dialog-title";
const DELETE_ROOM_DIALOG_TITLE_ID = "delete-room-dialog-title";

function formatRelativeTime(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const diffMs = Date.now() - d.getTime();
  const minute = 60_000;
  const hour = 60 * minute;
  const day = 24 * hour;
  if (diffMs < minute) return "now";
  if (diffMs < hour) return `${Math.floor(diffMs / minute)}m`;
  if (diffMs < day) return `${Math.floor(diffMs / hour)}h`;
  if (diffMs < 7 * day) return `${Math.floor(diffMs / day)}d`;
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

function previewText(room: RoomOut): string {
  const last = room.last_message;
  if (!last) return "No messages yet — say hello";
  const who = last.sender_type === "human" ? last.sender_name.split("@")[0] : last.sender_name;
  const body = last.content.replace(/\s+/g, " ").trim();
  return `${who}: ${body}`;
}

function NewRoomModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (room: RoomOut) => void;
}) {
  const [customerName, setCustomerName] = useState("");
  const [enrichment, setEnrichment] = useState("");
  const [creating, setCreating] = useState(false);

  const handleCreate = async (e: FormEvent) => {
    e.preventDefault();
    const name = customerName.trim();
    if (!name || creating) return;
    setCreating(true);
    try {
      const room = await createRoom(name, enrichment.trim() || undefined);
      onCreated(room);
    } catch (err) {
      toastError(err, "Failed to create room");
    } finally {
      setCreating(false);
    }
  };

  return (
    <Modal title="New Cabinet Room" titleId={NEW_ROOM_DIALOG_TITLE_ID} onClose={onClose}>
        <form onSubmit={handleCreate} className="new-room-form">
          <label className="field">
            <span className="field-label">Customer name</span>
            <input
              value={customerName}
              onChange={(e) => setCustomerName(e.target.value)}
              placeholder="e.g. Meridian Bank"
              autoFocus
              required
            />
          </label>
          <label className="field">
            <span className="field-label">
              Context enrichment (appended to expert baselines — never overrides them)
            </span>
            <textarea
              value={enrichment}
              onChange={(e) => setEnrichment(e.target.value)}
              placeholder="Optional room-specific context for the Data Expert and FCE…"
              rows={4}
            />
          </label>
          <button className="btn btn-primary" type="submit" disabled={creating || !customerName.trim()}>
            {creating ? "Creating…" : "Create room"}
          </button>
        </form>
    </Modal>
  );
}

function ConfirmDeleteRoomModal({
  room,
  onClose,
  onConfirmed,
}: {
  room: RoomOut;
  onClose: () => void;
  onConfirmed: () => void;
}) {
  const [deleting, setDeleting] = useState(false);

  const handleDelete = async () => {
    if (deleting) return;
    setDeleting(true);
    try {
      await deleteRoom(room.id);
      onConfirmed();
    } catch (err) {
      toastError(err, "Failed to delete room");
      setDeleting(false);
    }
  };

  return (
    <Modal title="Delete room" titleId={DELETE_ROOM_DIALOG_TITLE_ID} onClose={onClose}>
      <p>
        Delete <strong>{room.customer_name}</strong>? This can&apos;t be undone from the sidebar.
      </p>
      <div className="modal-actions">
        <button className="btn" onClick={onClose} disabled={deleting} autoFocus>
          Cancel
        </button>
        <button className="btn btn-danger" onClick={handleDelete} disabled={deleting}>
          {deleting ? "Deleting…" : "Delete"}
        </button>
      </div>
    </Modal>
  );
}

type ContextMenuState = { room: RoomOut; x: number; y: number };

export default function Sidebar({
  rooms,
  error,
  selectedRoomId,
  onSelectRoom,
  onCreated,
  onRoomUpdated,
  onRoomDeleted,
}: {
  rooms: RoomOut[] | null;
  error: string | null;
  selectedRoomId: string | null;
  onSelectRoom: (roomId: string) => void;
  onCreated: (room: RoomOut) => void;
  onRoomUpdated: (room: RoomOut) => void;
  onRoomDeleted: (roomId: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [showNew, setShowNew] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<RoomOut | null>(null);

  const sortedBy = (list: RoomOut[]) =>
    [...list].sort((a, b) => {
      const ta = new Date(a.last_message?.created_at ?? a.created_at).getTime();
      const tb = new Date(b.last_message?.created_at ?? b.created_at).getTime();
      return tb - ta;
    });

  const { visible, archived } = useMemo(() => {
    const list = rooms ?? [];
    const q = query.trim().toLowerCase();
    const filtered = q ? list.filter((r) => r.customer_name.toLowerCase().includes(q)) : list;
    return {
      visible: sortedBy(filtered.filter((r) => r.archived_at === null)),
      archived: sortedBy(filtered.filter((r) => r.archived_at !== null)),
    };
  }, [rooms, query]);

  const handleContextMenu = (event: MouseEvent, room: RoomOut) => {
    event.preventDefault();
    if (room.role !== "owner") return;
    setContextMenu({ room, x: event.clientX, y: event.clientY });
  };

  const handleArchive = async (room: RoomOut) => {
    try {
      onRoomUpdated(await archiveRoom(room.id));
    } catch (err) {
      toastError(err, "Failed to archive room");
    }
  };

  const handleUnarchive = async (room: RoomOut) => {
    try {
      onRoomUpdated(await unarchiveRoom(room.id));
    } catch (err) {
      toastError(err, "Failed to unarchive room");
    }
  };

  const renderRoomItem = (room: RoomOut) => {
    const paused = room.status === "paused_awaiting_human";
    return (
      <button
        key={room.id}
        className={`chat-list-item ${selectedRoomId === room.id ? "chat-list-item-active" : ""}`}
        onClick={() => onSelectRoom(room.id)}
        onContextMenu={(e) => handleContextMenu(e, room)}
      >
        <RoomLogo room={room} size={38} />
        <div className="chat-list-body">
          <div className="chat-list-top">
            <span className="chat-list-name">{room.customer_name}</span>
            <span className="chat-list-time">
              {formatRelativeTime(room.last_message?.created_at ?? room.created_at)}
            </span>
          </div>
          <div className="chat-list-bottom">
            <span className="chat-list-preview">{previewText(room)}</span>
            {paused && <span className="status-dot status-dot-paused" title="Paused — awaiting human" />}
            {!paused && <span className="status-dot status-dot-active" title="Active" />}
          </div>
        </div>
      </button>
    );
  };

  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <h2 className="sidebar-title">Chats</h2>
        <button className="btn-icon" onClick={() => setShowNew(true)} title="New Cabinet Room" aria-label="New room">
          +
        </button>
      </div>
      <div className="sidebar-search">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search rooms…"
          aria-label="Search rooms"
        />
      </div>

      {error && <div className="inline-error sidebar-error">Could not load rooms: {error}</div>}
      {rooms === null && !error && <div className="muted sidebar-empty">Loading rooms…</div>}
      {rooms !== null && visible.length === 0 && (
        <div className="muted sidebar-empty">
          {rooms.length === 0
            ? "No rooms yet — start one with +"
            : query.trim()
              ? "No rooms match your search."
              : "All rooms are archived — see below."}
        </div>
      )}

      <div className="chat-list">
        {visible.map(renderRoomItem)}
        {archived.length > 0 && (
          <div className="chat-list-archived">
            <button
              className="chat-list-archived-toggle"
              onClick={() => setShowArchived((v) => !v)}
              aria-expanded={showArchived}
            >
              <span>{showArchived ? "▾" : "▸"} Archived ({archived.length})</span>
            </button>
            {showArchived && archived.map(renderRoomItem)}
          </div>
        )}
      </div>

      {showNew && (
        <NewRoomModal
          onClose={() => setShowNew(false)}
          onCreated={(room) => {
            setShowNew(false);
            onCreated(room);
          }}
        />
      )}

      {contextMenu && (
        <RoomContextMenu
          room={contextMenu.room}
          x={contextMenu.x}
          y={contextMenu.y}
          onClose={() => setContextMenu(null)}
          onArchive={() => handleArchive(contextMenu.room)}
          onUnarchive={() => handleUnarchive(contextMenu.room)}
          onDeleteRequest={() => setConfirmDelete(contextMenu.room)}
        />
      )}

      {confirmDelete && (
        <ConfirmDeleteRoomModal
          room={confirmDelete}
          onClose={() => setConfirmDelete(null)}
          onConfirmed={() => {
            onRoomDeleted(confirmDelete.id);
            setConfirmDelete(null);
          }}
        />
      )}
    </aside>
  );
}
