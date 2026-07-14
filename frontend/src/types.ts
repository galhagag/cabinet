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

export interface RoomAgentDetailOut {
  agent_key: AgentKey;
  display_name: string;
  system_prompt: string;
  instructions: string;
}

export interface InstructionsUpdate {
  instructions: string;
}

export interface InstructionsHistoryEntryOut {
  actor: string;
  old_instructions: string;
  new_instructions: string;
  created_at: string;
}

export interface AgentUsageOut {
  agent_key: AgentKey;
  message_count: number;
  total_input_tokens: number;
  total_output_tokens: number;
}

export interface RoomLastMessageOut {
  sender_type: SenderType;
  sender_name: string;
  agent_key: string | null;
  content: string;
  created_at: string;
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
  member_count: number;
  last_message: RoomLastMessageOut | null;
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

export interface MessageEdit {
  content: string;
}

export interface MessageOut {
  id: string;
  room_id: string;
  sender_type: SenderType;
  sender_name: string;
  agent_key: string | null;
  mention_target: string | null;
  edit_of_id: string | null;
  cycle_number: number | null;
  content: string;
  input_tokens: number | null;
  output_tokens: number | null;
  created_at: string;
  superseded_at: string | null;
}

export interface PostMessageResult {
  messages: MessageOut[];
  room_status: RoomStatus;
  cycles_used: number;
  cycle_limit: number;
}

export interface MessageEditResult extends PostMessageResult {
  superseded_message_ids: string[];
}

// --- Google Drive -------------------------------------------------------------
export type GDriveStatus =
  | "none"
  | "pending"
  | "connected"
  | "linked"
  | "error"
  | "revoked";

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
  enabled: boolean;
}

export interface SkillToggleUpdate {
  enabled: boolean;
}

// --- Compiled prompt -----------------------------------------------------------
export interface CompiledPromptOut {
  agent_key: string;
  compiled_prompt: string;
}

// --- Realtime -----------------------------------------------------------------
export interface RealtimeTokenOut {
  mode: string;
  url: string;
}

export type RoomConnectionState = "connecting" | "live" | "reconnecting" | "offline";

// --- WebSocket events ------------------------------------------------------------
export interface WsMessageCreated {
  type: "message_created";
  message: MessageOut;
}

export interface WsMessageEdited {
  type: "message_edited";
  room_id: string;
  message_id: string;
  replacement_message_id: string | null;
  superseded_message_ids: string[];
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

export interface WsAgentInstructionsUpdated {
  type: "agent_instructions_updated";
  room_id: string;
  agent_key: string;
  actor: string;
}

export interface WsAgentSkillToggled {
  type: "agent_skill_toggled";
  room_id: string;
  agent_key: string;
  skill_id: string;
  enabled: boolean;
}

export interface WsDriveLinked {
  type: "drive_linked";
  google_folder_id?: string;
  google_folder_name?: string;
}

export interface WsDriveConnected {
  type: "drive_connected";
}

export interface WsDesync {
  type: "desync";
  reason: string;
}

export type RoomWsEvent =
  | WsMessageCreated
  | WsMessageEdited
  | WsAgentThinking
  | WsRoomPaused
  | WsRoomResumed
  | WsSkillAdded
  | WsAgentInstructionsUpdated
  | WsAgentSkillToggled
  | WsDriveLinked
  | WsDriveConnected
  | WsDesync;
