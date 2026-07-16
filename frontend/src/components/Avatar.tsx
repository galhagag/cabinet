import type { CSSProperties } from "react";

const AGENT_META: Record<string, { initials: string; className: string }> = {
  data_expert: { initials: "DE", className: "avatar-data_expert" },
  fce: { initials: "FC", className: "avatar-fce" },
};

const PALETTE = [
  "#223a75",
  "#9b4090",
  "#3888ff",
  "#f5013e",
  "#ff25ad",
  "#6b3fa0",
  "#c8860b",
  "#1f9d55",
];

function hash(str: string): number {
  let h = 0;
  for (let i = 0; i < str.length; i++) {
    h = (Math.imul(31, h) + str.charCodeAt(i)) | 0;
  }
  return Math.abs(h);
}

export function initialsFor(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) {
    // A single token is usually an email address — one clear initial reads
    // better than two and avoids collisions with the agents' fixed two-letter
    // badges (DE / FC).
    const letters = parts[0].replace(/[^a-zA-Z]/g, "");
    return (letters[0] ?? "?").toUpperCase();
  }
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export function avatarBackgroundFor(name: string): string {
  return PALETTE[hash(name) % PALETTE.length];
}

export function Avatar({
  name,
  agentKey,
  size = 36,
  title,
  className = "",
}: {
  name: string;
  agentKey?: string | null;
  size?: number;
  title?: string;
  className?: string;
}) {
  const agent = agentKey ? AGENT_META[agentKey] : undefined;
  const initials = agent ? agent.initials : initialsFor(name);
  const style: CSSProperties = {
    width: size,
    height: size,
    fontSize: Math.max(10, Math.round(size * 0.38)),
    ...(agent ? {} : { background: avatarBackgroundFor(name) }),
  };
  return (
    <span
      className={`avatar ${agent ? agent.className : ""} ${className}`}
      style={style}
      title={title ?? name}
    >
      {initials}
    </span>
  );
}

export interface AvatarClusterItem {
  name: string;
  agentKey?: string | null;
}

export function AvatarCluster({
  items,
  size = 24,
  max = 4,
}: {
  items: AvatarClusterItem[];
  size?: number;
  max?: number;
}) {
  const shown = items.slice(0, max);
  const overflow = items.length - shown.length;
  return (
    <span className="avatar-cluster" style={{ height: size }}>
      {shown.map((it, i) => (
        <Avatar
          key={`${it.agentKey ?? ""}-${it.name}-${i}`}
          name={it.name}
          agentKey={it.agentKey}
          size={size}
          className="avatar-clustered"
        />
      ))}
      {overflow > 0 && (
        <span
          className="avatar avatar-clustered avatar-overflow"
          style={{ width: size, height: size, fontSize: Math.max(9, Math.round(size * 0.32)) }}
        >
          +{overflow}
        </span>
      )}
    </span>
  );
}
