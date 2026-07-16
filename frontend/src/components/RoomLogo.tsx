import { useEffect, useRef, useState } from "react";
import { uploadRoomLogo } from "../api";
import { toastError } from "../toast";
import type { RoomLogoOut, RoomOut } from "../types";
import { avatarBackgroundFor, initialsFor } from "./Avatar";

type RoomLogoRoom = Pick<RoomOut, "id" | "customer_name" | "logo_url" | "logo_source">;

export default function RoomLogo({
  room,
  size = 40,
  editable = false,
  onUpdated,
}: {
  room: RoomLogoRoom | null;
  size?: number;
  editable?: boolean;
  onUpdated?: (patch: RoomLogoOut) => void;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [uploading, setUploading] = useState(false);
  const [imageFailed, setImageFailed] = useState(false);

  useEffect(() => {
    setImageFailed(false);
  }, [room?.logo_url]);

  const name = room?.customer_name ?? "Loading";
  const initials = initialsFor(name);
  const showImage = Boolean(room?.logo_url) && !imageFailed;

  const handlePick = () => {
    if (!room || uploading) return;
    inputRef.current?.click();
  };

  const handleFileChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!room || !file) return;
    setUploading(true);
    try {
      const patch = await uploadRoomLogo(room.id, file);
      setImageFailed(false);
      onUpdated?.(patch);
    } catch (err) {
      toastError(err, "Failed to upload room logo");
    } finally {
      setUploading(false);
    }
  };

  return (
    <div
      className={`room-logo ${editable ? "room-logo-editable" : ""}`}
      style={{ width: size, height: size }}
      title={name}
    >
      {showImage ? (
        <img
          src={room?.logo_url ?? undefined}
          alt={`${name} logo`}
          className="room-logo-image"
          onError={() => setImageFailed(true)}
        />
      ) : (
        <span
          className="room-logo-fallback"
          style={{
            width: size,
            height: size,
            fontSize: Math.max(10, Math.round(size * 0.36)),
            background: avatarBackgroundFor(name),
          }}
        >
          {initials}
        </span>
      )}

      {room?.logo_source === "pending" && !showImage && <span className="room-logo-badge">Auto</span>}

      {editable && room && (
        <>
          <button
            type="button"
            className="room-logo-edit"
            aria-label={uploading ? "Uploading room logo" : "Upload room logo"}
            title={uploading ? "Uploading room logo" : "Upload room logo"}
            onClick={handlePick}
            disabled={uploading}
          >
            {uploading ? "..." : "Edit"}
          </button>
          <input
            ref={inputRef}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            className="room-logo-input"
            onChange={handleFileChange}
          />
        </>
      )}
    </div>
  );
}