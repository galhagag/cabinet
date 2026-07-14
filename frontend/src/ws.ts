// Room WebSocket client with auto-reconnect and exponential backoff.
import { getRealtimeToken, getUserEmail } from "./api";
import { getAccessToken, isEntraAuth } from "./auth";
import type {
  RealtimeTokenOut,
  RoomConnectionState,
  RoomConnectionStatus,
  RoomWsEvent,
} from "./types";

async function socketUrl(roomId: string): Promise<string> {
  const realtime = await getRealtimeToken(roomId);
  if (realtime.mode === "webpubsub") {
    return realtime.url;
  }
  return buildInProcessWsUrl(realtime);
}

async function buildInProcessWsUrl(realtime: RealtimeTokenOut): Promise<string> {
  const url = new URL(realtime.url, window.location.origin);
  url.protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  // Browsers cannot set custom headers on the WS handshake, so the in-process
  // backend auth data has to travel as query params instead.
  if (isEntraAuth) {
    url.searchParams.set("access_token", await getAccessToken());
  } else {
    url.searchParams.set("user_email", getUserEmail());
  }
  return url.toString();
}

export type WsEventHandler = (event: RoomWsEvent) => void;
export type WsReconnectHandler = () => void;
export type WsStatusHandler = (status: RoomConnectionStatus) => void;

const MAX_RECONNECT_ATTEMPTS = 5;
const STABLE_CONNECTION_MS = 5000;

export class RoomSocket {
  private ws: WebSocket | null = null;
  private roomId: string | null = null;
  private onEvent: WsEventHandler | null = null;
  private onReconnect: WsReconnectHandler | null = null;
  private onStatusChange: WsStatusHandler | null = null;
  private closedByUser = false;
  private attempts = 0;
  private reconnectTimer: number | null = null;
  private connectedOnce = false;
  private stableConnectionTimer: number | null = null;
  private needsResyncOnNextOpen = false;

  connect(
    roomId: string,
    onEvent: WsEventHandler,
    onReconnect?: WsReconnectHandler,
    onStatusChange?: WsStatusHandler,
  ): void {
    this.close();
    this.roomId = roomId;
    this.onEvent = onEvent;
    this.onReconnect = onReconnect ?? null;
    this.onStatusChange = onStatusChange ?? null;
    this.closedByUser = false;
    this.attempts = 0;
    this.connectedOnce = false;
    this.needsResyncOnNextOpen = false;
    this.emitStatus("connecting", "Connecting to live room updates…");
    this.open();
  }

  private open(): void {
    if (this.roomId === null || this.closedByUser) return;
    const roomId = this.roomId;
    socketUrl(roomId)
      .then((url) => this.openWithUrl(roomId, url))
      .catch((err) => this.scheduleReconnect(this.describeOpenError(err)));
  }

  private emitStatus(state: RoomConnectionState, detail: string): void {
    this.onStatusChange?.({
      state,
      attempts: this.attempts,
      maxAttempts: MAX_RECONNECT_ATTEMPTS,
      detail,
    });
  }

  private describeOpenError(err: unknown): string {
    const message = err instanceof Error ? err.message : String(err);
    return message && message !== "undefined"
      ? `Couldn't start live updates: ${message}`
      : "Couldn't start live updates.";
  }

  private describeCloseEvent(event: CloseEvent): string {
    if (event.reason) {
      return `Live updates disconnected: ${event.reason}`;
    }
    if (event.code === 4403 || event.code === 1008) {
      return "Live updates were rejected. Sign in again or rejoin the room.";
    }
    if (event.code === 1006) {
      return "Live updates dropped unexpectedly.";
    }
    return "Live updates disconnected.";
  }

  reconnectNow(): void {
    if (this.closedByUser || this.roomId === null) return;
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.stableConnectionTimer !== null) {
      window.clearTimeout(this.stableConnectionTimer);
      this.stableConnectionTimer = null;
    }
    if (this.ws) {
      const ws = this.ws;
      this.ws = null;
      ws.onopen = null;
      ws.onclose = null;
      ws.onerror = null;
      ws.onmessage = null;
      try {
        ws.close();
      } catch {
        // already closed
      }
    }
    this.attempts = 0;
    this.needsResyncOnNextOpen = true;
    this.emitStatus(
      this.connectedOnce ? "reconnecting" : "connecting",
      "Retrying live updates now…",
    );
    this.open();
  }

  private openWithUrl(roomId: string, url: string): void {
    // The room/token-acquiring await above may have outlived a close() or a
    // switch to a different room; drop a now-stale connect attempt.
    if (this.roomId !== roomId || this.closedByUser) return;
    let ws: WebSocket;
    try {
      ws = new WebSocket(url);
    } catch (err) {
      this.scheduleReconnect(this.describeOpenError(err));
      return;
    }
    this.ws = ws;

    ws.onopen = () => {
      const shouldResync = this.connectedOnce || this.needsResyncOnNextOpen;
      this.connectedOnce = true;
      this.needsResyncOnNextOpen = false;
      if (this.stableConnectionTimer !== null) {
        window.clearTimeout(this.stableConnectionTimer);
      }
      this.stableConnectionTimer = window.setTimeout(() => {
        this.stableConnectionTimer = null;
        this.attempts = 0;
      }, STABLE_CONNECTION_MS);
      this.emitStatus(
        "live",
        shouldResync ? "Live updates restored." : "Live updates connected.",
      );
      if (shouldResync) {
        this.onReconnect?.();
      }
    };

    ws.onmessage = (ev: MessageEvent) => {
      if (typeof ev.data !== "string") return;
      let parsed: unknown;
      try {
        parsed = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (
        parsed &&
        typeof parsed === "object" &&
        typeof (parsed as { type?: unknown }).type === "string"
      ) {
        this.onEvent?.(parsed as RoomWsEvent);
      }
    };

    ws.onclose = (event) => {
      if (this.ws === ws) this.ws = null;
      if (this.stableConnectionTimer !== null) {
        window.clearTimeout(this.stableConnectionTimer);
        this.stableConnectionTimer = null;
      }
      this.scheduleReconnect(this.describeCloseEvent(event));
    };

    ws.onerror = () => {
      // onclose follows; reconnection handled there.
    };
  }

  private scheduleReconnect(detail: string): void {
    if (this.closedByUser || this.reconnectTimer !== null) return;
    this.needsResyncOnNextOpen = true;
    const nextAttempt = this.attempts + 1;
    if (nextAttempt >= MAX_RECONNECT_ATTEMPTS) {
      this.attempts = nextAttempt;
      this.emitStatus(
        "offline",
        `${detail} Automatic retries stopped after ${MAX_RECONNECT_ATTEMPTS} attempts.`,
      );
      return;
    }
    this.attempts = nextAttempt;
    const retryState = this.connectedOnce ? "reconnecting" : "connecting";
    this.emitStatus(
      retryState,
      `${detail} Retrying automatically (${this.attempts}/${MAX_RECONNECT_ATTEMPTS - 1}).`,
    );
    const delay = Math.min(30000, 1000 * 2 ** (this.attempts - 1));
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.open();
    }, delay);
  }

  close(): void {
    this.closedByUser = true;
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.stableConnectionTimer !== null) {
      window.clearTimeout(this.stableConnectionTimer);
      this.stableConnectionTimer = null;
    }
    if (this.ws) {
      const ws = this.ws;
      this.ws = null;
      ws.onopen = null;
      ws.onclose = null;
      ws.onerror = null;
      ws.onmessage = null;
      try {
        ws.close();
      } catch {
        // already closed
      }
    }
    this.roomId = null;
    this.onEvent = null;
    this.onReconnect = null;
    this.onStatusChange = null;
    this.connectedOnce = false;
    this.needsResyncOnNextOpen = false;
  }
}
