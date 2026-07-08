import { useCallback, useEffect, useRef, useState } from "react";
import { gdriveAuthorize, gdriveLinkFolder, gdriveStatus } from "../api";
import type { GDriveStatusOut } from "../types";
import { pushToast, toastError } from "../toast";

export default function DrivePanel({
  roomId,
  refreshSignal = 0,
}: {
  roomId: string;
  refreshSignal?: number;
}) {
  const [status, setStatus] = useState<GDriveStatusOut | null>(null);
  const [folderId, setFolderId] = useState("");
  const [folderName, setFolderName] = useState("");
  const [busy, setBusy] = useState(false);
  const pollTimer = useRef<number | null>(null);

  const refresh = useCallback(() => {
    gdriveStatus(roomId)
      .then(setStatus)
      .catch(() => {
        // Status polling failures stay quiet; the connect action reports errors.
      });
  }, [roomId]);

  useEffect(() => {
    refresh();
    return () => {
      if (pollTimer.current !== null) window.clearInterval(pollTimer.current);
    };
  }, [refresh, refreshSignal]);

  // Poll while a consent flow may be in progress.
  useEffect(() => {
    const s = status?.status;
    const shouldPoll = s === "pending";
    if (shouldPoll && pollTimer.current === null) {
      pollTimer.current = window.setInterval(refresh, 3000);
    } else if (!shouldPoll && pollTimer.current !== null) {
      window.clearInterval(pollTimer.current);
      pollTimer.current = null;
    }
  }, [status, refresh]);

  const connect = async () => {
    setBusy(true);
    try {
      const { authorize_url } = await gdriveAuthorize(roomId);
      window.open(authorize_url, "_blank", "noopener");
      // Start polling for the callback to complete.
      if (pollTimer.current === null) {
        pollTimer.current = window.setInterval(refresh, 3000);
      }
    } catch (err) {
      toastError(err, "Google Drive authorization failed");
    } finally {
      setBusy(false);
    }
  };

  const linkFolder = async () => {
    if (!folderId.trim() || busy) return;
    setBusy(true);
    try {
      const next = await gdriveLinkFolder(roomId, folderId.trim(), folderName.trim());
      setStatus(next);
      pushToast("info", "Google Drive folder linked");
    } catch (err) {
      toastError(err, "Failed to link folder");
    } finally {
      setBusy(false);
    }
  };

  const s = status?.status ?? "none";

  return (
    <div className="drive-panel">
      <span className="drive-label">Drive:</span>
      {s === "none" || s === "revoked" ? (
        <>
          {s === "revoked" && <span className="badge badge-paused">Revoked</span>}
          <button className="btn btn-small" onClick={connect} disabled={busy}>
            {busy ? "Opening…" : "Connect Google Drive"}
          </button>
        </>
      ) : s === "pending" ? (
        <span className="badge badge-pending" title="Waiting for Google consent to complete">
          Authorizing…
        </span>
      ) : s === "connected" ? (
        <span className="drive-link-form">
          <span className="badge badge-active">Connected</span>
          <input
            className="drive-input"
            placeholder="Folder ID"
            value={folderId}
            onChange={(e) => setFolderId(e.target.value)}
          />
          <input
            className="drive-input"
            placeholder="Folder name (optional)"
            value={folderName}
            onChange={(e) => setFolderName(e.target.value)}
          />
          <button className="btn btn-small" onClick={linkFolder} disabled={busy || !folderId.trim()}>
            Link folder
          </button>
        </span>
      ) : (
        <span className="badge badge-linked" title={status?.google_folder_id ?? ""}>
          Linked: {status?.google_folder_name || status?.google_folder_id || "folder"}
        </span>
      )}
    </div>
  );
}
