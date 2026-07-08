// TypeScript interfaces mirroring backend/app/schemas.py

export type AgentKey = "data_expert" | "fce";

export type RoomStatus = "active" | "paused_awaiting_human";

export type SenderType = "human" | "agent" | "system";

// --- Admin -----------------------------------------------------------------
export interface AgentConfigOut {
  agent_key: AgentKey;
  display_name: string;
  system_prompt: string;
  updated_at: string;
}

export interface AgentConfigUpdate {
  system_prompt: string;
}

// --- Rooms -------------------------------------------------------------------
export interface RoomCreate {
  customer_name: string;
  enrichment_prompt?: string | null;
}

export interface RoomAgentOut {
  agent_key: AgentKey;
  display_name: string;
}

export interface RoomOut {
  id: string;
  customer_name: string;
  enrichment_prompt: string | null;
  status: RoomStatus;
  cycles_used: number;
  cycle_limit: number;
  created_at: string;
  agents: RoomAgentOut[];
}

export interface RoomMemberOut {
  user_email: string;
  display_name: string;
  role: string;
  joined_at: string;
}

export interface InviteCreateOut {
  token: string;
  room_id: string;
  expires_at: string;
  join_url: string;
}

export interface JoinRequest {
  token: string;
  display_name: string;
}

// --- Messages ---------------------------------------------------------------
export interface MessageCreate {
  content: string;
}

export interface MessageOut {
  id: string;
  room_id: string;
  sender_type: SenderType;
  sender_name: string;
  agent_key: string | null;
  mention_target: string | null;
  cycle_number: number | null;
  content: string;
  created_at: string;
}

export interface PostMessageResult {
  messages: MessageOut[];
  room_status: RoomStatus;
  cycles_used: number;
  cycle_limit: number;
}

// --- Google Drive -------------------------------------------------------------
export type GDriveStatus = "none" | "pending" | "connected" | "linked" | "revoked";

export interface GDriveAuthorizeOut {
  authorize_url: string;
  state: string;
}

export interface GDriveStatusOut {
  status: GDriveStatus;
  google_folder_id?: string | null;
  google_folder_name?: string | null;
  token_expiry?: string | null;
  scopes?: string;
}

export interface GDriveFolderLink {
  folder_id: string;
  folder_name: string;
}

// --- Skills -------------------------------------------------------------------
export interface SkillOut {
  id: string;
  room_id: string | null;
  agent_key: string;
  skill_name: string;
  skill_type: string;
  blob_path: string;
  created_at: string;
}

// --- Compiled prompt -----------------------------------------------------------
export interface CompiledPromptOut {
  agent_key: string;
  compiled_prompt: string;
}

// --- WebSocket events ------------------------------------------------------------
export interface WsMessageCreated {
  type: "message_created";
  message: MessageOut;
}

export interface WsAgentThinking {
  type: "agent_thinking";
  agent_key: string;
}

export interface WsRoomPaused {
  type: "room_paused";
  cycles_used: number;
  cycle_limit: number;
}

export interface WsRoomResumed {
  type: "room_resumed";
}

export interface WsSkillAdded {
  type: "skill_added";
  agent_key?: string;
  skill_name?: string;
}

export interface WsDriveLinked {
  type: "drive_linked";
  google_folder_id?: string;
  google_folder_name?: string;
}

export interface WsDriveConnected {
  type: "drive_connected";
}

export type RoomWsEvent =
  | WsMessageCreated
  | WsAgentThinking
  | WsRoomPaused
  | WsRoomResumed
  | WsSkillAdded
  | WsDriveLinked
  | WsDriveConnected;
