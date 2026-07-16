// Typed REST client for the Cabinet backend.
import { getAccessToken, isEntraAuth } from "./auth";
import type {
  AgentConfigOut,
  AgentUsageOut,
  CompiledPromptOut,
  GDriveAuthorizeOut,
  GDriveStatusOut,
  InstructionsHistoryEntryOut,
  InviteCreateOut,
  MessageOut,
  PostMessageResult,
  RealtimeTokenOut,
  RoomAgentDetailOut,
  RoomLogoOut,
  RoomMemberOut,
  RoomOut,
  SkillOut,
} from "./types";

export const API_BASE: string = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

const EMAIL_KEY = "cabinet_user_email";
const DEFAULT_EMAIL = "dev@thetaray.com";

export function getUserEmail(): string {
  return localStorage.getItem(EMAIL_KEY) || DEFAULT_EMAIL;
}

export function setUserEmail(email: string): void {
  localStorage.setItem(EMAIL_KEY, email.trim() || DEFAULT_EMAIL);
}

export class ApiError extends Error {
  status: number;
  retryAfter?: number;
  constructor(status: number, message: string, retryAfter?: number) {
    super(message);
    this.status = status;
    this.retryAfter = retryAfter;
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (isEntraAuth) {
    headers.set("Authorization", `Bearer ${await getAccessToken()}`);
  } else {
    headers.set("X-User-Email", getUserEmail());
  }
  if (init.body !== undefined && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  } catch (err) {
    throw new ApiError(0, `Network error: ${err instanceof Error ? err.message : String(err)}`);
  }

  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    const retryAfterHeader = res.headers.get("Retry-After");
    const retryAfter = retryAfterHeader ? Number.parseInt(retryAfterHeader, 10) : undefined;
    try {
      const body: unknown = await res.json();
      if (body && typeof body === "object" && "detail" in body) {
        const d = (body as { detail: unknown }).detail;
        detail = typeof d === "string" ? d : JSON.stringify(d);
      }
    } catch {
      // no JSON body; keep the status text
    }
    throw new ApiError(
      res.status,
      detail,
      retryAfter !== undefined && Number.isFinite(retryAfter) ? retryAfter : undefined,
    );
  }

  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}

// --- Health -----------------------------------------------------------------
export const getHealth = () => request<{ status: string }>("/api/health");

// --- Admin ------------------------------------------------------------------
export const listAgentConfigs = () => request<AgentConfigOut[]>("/api/admin/agents");

export const getAgentConfig = (agentKey: string) =>
  request<AgentConfigOut>(`/api/admin/agents/${agentKey}`);

export const updateAgentConfig = (agentKey: string, systemPrompt: string) =>
  request<AgentConfigOut>(`/api/admin/agents/${agentKey}`, {
    method: "PUT",
    body: JSON.stringify({ system_prompt: systemPrompt }),
  });

export const uploadGlobalSkill = (agentKey: string, file: File) => {
  const form = new FormData();
  form.append("file", file);
  return request<SkillOut>(`/api/admin/agents/${agentKey}/skills`, {
    method: "POST",
    body: form,
  });
};

export const listGlobalSkills = (agentKey: string) =>
  request<SkillOut[]>(`/api/admin/agents/${agentKey}/skills`);

export const deleteGlobalSkill = (agentKey: string, skillId: string) =>
  request<void>(`/api/admin/agents/${agentKey}/skills/${skillId}`, {
    method: "DELETE",
  });

// --- Rooms --------------------------------------------------------------------
export const createRoom = (customerName: string, enrichmentPrompt?: string) =>
  request<RoomOut>("/api/rooms", {
    method: "POST",
    body: JSON.stringify({
      customer_name: customerName,
      enrichment_prompt: enrichmentPrompt || null,
    }),
  });

export const listRooms = () => request<RoomOut[]>("/api/rooms");

export const getRoom = (roomId: string) => request<RoomOut>(`/api/rooms/${roomId}`);

export const uploadRoomLogo = (roomId: string, file: File) => {
  const form = new FormData();
  form.append("file", file);
  return request<RoomLogoOut>(`/api/rooms/${roomId}/logo`, {
    method: "POST",
    body: form,
  });
};

export const listMembers = (roomId: string) =>
  request<RoomMemberOut[]>(`/api/rooms/${roomId}/members`);

export const createInvite = (roomId: string) =>
  request<InviteCreateOut>(`/api/rooms/${roomId}/invites`, { method: "POST" });

export const joinRoom = (token: string, displayName: string) =>
  request<RoomOut>("/api/rooms/join", {
    method: "POST",
    body: JSON.stringify({ token, display_name: displayName }),
  });

export const archiveRoom = (roomId: string) =>
  request<RoomOut>(`/api/rooms/${roomId}/archive`, { method: "POST" });

export const unarchiveRoom = (roomId: string) =>
  request<RoomOut>(`/api/rooms/${roomId}/unarchive`, { method: "POST" });

export const deleteRoom = (roomId: string) =>
  request<void>(`/api/rooms/${roomId}`, { method: "DELETE" });

// --- Room agents (Agents Skills) ----------------------------------------------
export const getRoomAgent = (roomId: string, agentKey: string) =>
  request<RoomAgentDetailOut>(`/api/rooms/${roomId}/agents/${agentKey}`);

export const updateRoomAgentInstructions = (
  roomId: string,
  agentKey: string,
  instructions: string,
) =>
  request<RoomAgentDetailOut>(`/api/rooms/${roomId}/agents/${agentKey}/instructions`, {
    method: "PUT",
    body: JSON.stringify({ instructions }),
  });

export const getInstructionsHistory = (roomId: string, agentKey: string) =>
  request<InstructionsHistoryEntryOut[]>(
    `/api/rooms/${roomId}/agents/${agentKey}/instructions/history`,
  );

export const getAgentUsage = (roomId: string, agentKey: string) =>
  request<AgentUsageOut>(`/api/rooms/${roomId}/agents/${agentKey}/usage`);

// --- Messages --------------------------------------------------------------------
export const listMessages = (roomId: string) =>
  request<MessageOut[]>(`/api/rooms/${roomId}/messages`);

export const postMessage = (roomId: string, content: string) =>
  request<PostMessageResult>(`/api/rooms/${roomId}/messages`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });

export const resumeRoom = (roomId: string) =>
  request<PostMessageResult>(`/api/rooms/${roomId}/resume`, { method: "POST" });

// --- Compiled prompt ----------------------------------------------------------------
export const getCompiledPrompt = (roomId: string, agentKey: string) =>
  request<CompiledPromptOut>(`/api/rooms/${roomId}/agents/${agentKey}/compiled-prompt`);

// --- Realtime ------------------------------------------------------------------------
export const getRealtimeToken = (roomId: string) =>
  request<RealtimeTokenOut>(`/api/rooms/${roomId}/realtime-token`);

// --- Google Drive -------------------------------------------------------------------
export const gdriveAuthorize = (roomId: string) =>
  request<GDriveAuthorizeOut>(`/api/rooms/${roomId}/gdrive/authorize`);

export const gdriveStatus = (roomId: string) =>
  request<GDriveStatusOut>(`/api/rooms/${roomId}/gdrive/status`);

export const gdriveLinkFolder = (roomId: string, folderId: string, folderName: string) =>
  request<GDriveStatusOut>(`/api/rooms/${roomId}/gdrive/folder`, {
    method: "POST",
    body: JSON.stringify({ folder_id: folderId, folder_name: folderName }),
  });

// --- Skills -----------------------------------------------------------------------
export const uploadSkill = (roomId: string, agentKey: string, file: File) => {
  const form = new FormData();
  form.append("file", file);
  return request<SkillOut>(`/api/rooms/${roomId}/agents/${agentKey}/skills`, {
    method: "POST",
    body: form,
  });
};

export const listSkills = (roomId: string, agentKey: string) =>
  request<SkillOut[]>(`/api/rooms/${roomId}/agents/${agentKey}/skills`);

export const toggleSkill = (
  roomId: string,
  agentKey: string,
  skillId: string,
  enabled: boolean,
) =>
  request<SkillOut>(`/api/rooms/${roomId}/agents/${agentKey}/skills/${skillId}`, {
    method: "PUT",
    body: JSON.stringify({ enabled }),
  });
