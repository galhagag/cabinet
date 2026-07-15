import { useEffect, useRef, useState } from "react";
import { API_BASE, uploadRoomLogo } from "../api";
import { getAccessToken, isEntraAuth } from "../auth";
import { toastError } from "../toast";
import { Avatar } from "./Avatar";
import type { RoomLogoOut, RoomOut } from "../types";

export function RoomLogo({
  room,
  size = 40,
  editable = false,
  onUpdated,
}: {
  room: Pick<RoomOut, "id" | "customer_name" | "logo_url">;
  size?: number;
  editable?: boolean;
  onUpdated?: (patch: RoomLogoOut) => void;
}) {
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [uploading, setUploading] = useState(false);
  const [src, setSrc] = useState<string | null>(null);

  // A plain <img src> cannot carry the Authorization header Entra auth
  // needs, so in that mode the token is resolved once here and appended as
  // a query param — see docs/superpowers/specs/2026-07-14-room-logo-design.md.
  useEffect(() => {
    if (!room.logo_url) {
      setSrc(null);
      return;
    }
    if (!isEntraAuth) {
      setSrc(`${API_BASE}${room.logo_url}`);
      return;
    }
    let cancelled = false;
    getAccessToken()
      .then((token) => {
        if (!cancelled) {
          setSrc(`${API_BASE}${room.logo_url}?access_token=${encodeURIComponent(token)}`);
        }
      })
      .catch(() => {
        if (!cancelled) setSrc(null);
      });
    return () => {
      cancelled = true;
    };
  }, [room.logo_url]);

  const upload = async (file: File) => {
    setUploading(true);
    try {
      const result = await uploadRoomLogo(room.id, file);
      onUpdated?.(result);
    } catch (err) {
      toastError(err, "Failed to upload logo");
    } finally {
      setUploading(false);
    }
  };

  return (
    <span className="room-logo" style={{ width: size, height: size }}>
      {src ? (
        <img className="room-logo-img" src={src} alt="" style={{ width: size, height: size }} />
      ) : (
        <Avatar name={room.customer_name} size={size} />
      )}
      {editable && (
        <>
          <button
            type="button"
            className="room-logo-edit"
            onClick={() => fileRef.current?.click()}
            disabled={uploading}
            aria-label="Change room logo"
            title="Change room logo"
          >
            {uploading ? "…" : "✎"}
          </button>
          <input
            ref={fileRef}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            className="room-logo-file-input"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void upload(file);
              e.target.value = "";
            }}
          />
        </>
      )}
    </span>
  );
}
