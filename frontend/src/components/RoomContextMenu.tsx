import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { RoomOut } from "../types";

export default function RoomContextMenu({
  room,
  x,
  y,
  onClose,
  onArchive,
  onUnarchive,
  onDeleteRequest,
}: {
  room: RoomOut;
  x: number;
  y: number;
  onClose: () => void;
  onArchive: () => void;
  onUnarchive: () => void;
  onDeleteRequest: () => void;
}) {
  const menuRef = useRef<HTMLDivElement | null>(null);
  const [pos, setPos] = useState({ top: y, left: x });

  // Keep the menu on-screen even when the right-click lands near an edge.
  useLayoutEffect(() => {
    const menu = menuRef.current;
    if (!menu) return;
    const { width, height } = menu.getBoundingClientRect();
    setPos({
      top: Math.max(8, Math.min(y, window.innerHeight - height - 8)),
      left: Math.max(8, Math.min(x, window.innerWidth - width - 8)),
    });
  }, [x, y]);

  useEffect(() => {
    const handlePointerDown = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        onClose();
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [onClose]);

  const archived = room.archived_at !== null;

  return (
    <div
      ref={menuRef}
      className="context-menu"
      role="menu"
      aria-label={`Actions for ${room.customer_name}`}
      style={{ top: pos.top, left: pos.left }}
    >
      {archived ? (
        <button
          role="menuitem"
          className="context-menu-item"
          onClick={() => {
            onUnarchive();
            onClose();
          }}
        >
          Unarchive
        </button>
      ) : (
        <button
          role="menuitem"
          className="context-menu-item"
          onClick={() => {
            onArchive();
            onClose();
          }}
        >
          Archive
        </button>
      )}
      <button
        role="menuitem"
        className="context-menu-item context-menu-item-danger"
        onClick={() => {
          onDeleteRequest();
          onClose();
        }}
      >
        Delete
      </button>
    </div>
  );
}
