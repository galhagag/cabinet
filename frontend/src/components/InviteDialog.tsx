import { useState } from "react";
import { createInvite } from "../api";
import type { InviteCreateOut } from "../types";
import { pushToast, toastError } from "../toast";

export default function InviteDialog({ roomId }: { roomId: string }) {
  const [open, setOpen] = useState(false);
  const [invite, setInvite] = useState<InviteCreateOut | null>(null);
  const [creating, setCreating] = useState(false);

  const create = async () => {
    setCreating(true);
    try {
      setInvite(await createInvite(roomId));
    } catch (err) {
      toastError(err, "Failed to create invite");
    } finally {
      setCreating(false);
    }
  };

  const joinLink = invite ? `${window.location.origin}/?token=${invite.token}` : "";

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(joinLink);
      pushToast("info", "Invite link copied to clipboard");
    } catch {
      pushToast("error", "Could not copy — select the link text manually");
    }
  };

  return (
    <>
      <button
        className="btn"
        onClick={() => {
          setOpen(true);
          if (!invite) void create();
        }}
      >
        Invite stakeholders
      </button>
      {open && (
        <div className="modal-overlay" onClick={() => setOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Invite stakeholders</h3>
              <button className="modal-close" onClick={() => setOpen(false)} aria-label="Close">
                ×
              </button>
            </div>
            {creating && <div className="muted">Generating secure invite link…</div>}
            {!creating && invite && (
              <>
                <p className="muted">
                  Share this link — anyone with it can join this Cabinet Room until it expires.
                </p>
                <div className="invite-link-row">
                  <input className="invite-link" readOnly value={joinLink} onFocus={(e) => e.target.select()} />
                  <button className="btn btn-primary" onClick={copy}>
                    Copy
                  </button>
                </div>
                <p className="muted">Expires: {new Date(invite.expires_at).toLocaleString()}</p>
                <button className="btn btn-small" onClick={create} disabled={creating}>
                  Generate new link
                </button>
              </>
            )}
            {!creating && !invite && (
              <button className="btn btn-primary" onClick={create}>
                Generate invite link
              </button>
            )}
          </div>
        </div>
      )}
    </>
  );
}
