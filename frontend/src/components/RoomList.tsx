import { useEffect, useState, type FormEvent } from "react";
import { createRoom, listRooms } from "../api";
import type { RoomOut } from "../types";
import { toastError } from "../toast";

function StatusBadge({ status }: { status: string }) {
  const paused = status === "paused_awaiting_human";
  return (
    <span className={`badge ${paused ? "badge-paused" : "badge-active"}`}>
      {paused ? "Paused — awaiting human" : "Active"}
    </span>
  );
}

export default function RoomList({ onOpenRoom }: { onOpenRoom: (roomId: string) => void }) {
  const [rooms, setRooms] = useState<RoomOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [customerName, setCustomerName] = useState("");
  const [enrichment, setEnrichment] = useState("");
  const [creating, setCreating] = useState(false);

  const refresh = () => {
    listRooms()
      .then((r) => {
        setRooms(r);
        setError(null);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : String(err));
      });
  };

  useEffect(refresh, []);

  const handleCreate = async (e: FormEvent) => {
    e.preventDefault();
    const name = customerName.trim();
    if (!name || creating) return;
    setCreating(true);
    try {
      const room = await createRoom(name, enrichment.trim() || undefined);
      setCustomerName("");
      setEnrichment("");
      onOpenRoom(room.id);
    } catch (err) {
      toastError(err, "Failed to create room");
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="lobby">
      <section className="panel new-room-panel">
        <h2>New Cabinet Room</h2>
        <form onSubmit={handleCreate} className="new-room-form">
          <label className="field">
            <span className="field-label">Customer name</span>
            <input
              value={customerName}
              onChange={(e) => setCustomerName(e.target.value)}
              placeholder="e.g. Meridian Bank"
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
      </section>

      <section className="rooms-section">
        <div className="rooms-header">
          <h2>Cabinet Rooms</h2>
          <button className="btn btn-small" onClick={refresh}>
            Refresh
          </button>
        </div>
        {error && <div className="inline-error">Could not load rooms: {error}</div>}
        {rooms === null && !error && <div className="muted">Loading rooms…</div>}
        {rooms !== null && rooms.length === 0 && (
          <div className="muted">No rooms yet. Create the first Cabinet Room above.</div>
        )}
        <div className="room-grid">
          {(rooms ?? []).map((room) => (
            <button key={room.id} className="room-card" onClick={() => onOpenRoom(room.id)}>
              <div className="room-card-top">
                <span className="room-name">{room.customer_name}</span>
                <StatusBadge status={room.status} />
              </div>
              <div className="room-card-meta">
                <span className="muted">
                  Cycles {room.cycles_used}/{room.cycle_limit}
                </span>
                <span className="muted">
                  {new Date(room.created_at).toLocaleDateString()}
                </span>
              </div>
              <div className="room-card-agents">
                {room.agents.map((a) => (
                  <span key={a.agent_key} className={`agent-chip agent-chip-${a.agent_key}`}>
                    {a.display_name}
                  </span>
                ))}
              </div>
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}
