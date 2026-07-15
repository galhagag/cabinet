// Room WebSocket client with auto-reconnect and exponential backoff.
import { getRealtimeToken, getUserEmail } from "./api";
import { getAccessToken, isEntraAuth } from "./auth";
import type { RealtimeTokenOut, RoomConnectionState, RoomWsEvent } from "./types";

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
export type WsStatusHandler = (state: RoomConnectionState) => void;

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
    this.emitStatus("connecting");
    this.open();
  }

  private open(): void {
    if (this.roomId === null || this.closedByUser) return;
    const roomId = this.roomId;
    socketUrl(roomId)
      .then((url) => this.openWithUrl(roomId, url))
      .catch(() => this.scheduleReconnect());
  }

  private emitStatus(state: RoomConnectionState): void {
    this.onStatusChange?.(state);
  }

  private openWithUrl(roomId: string, url: string): void {
    // The room/token-acquiring await above may have outlived a close() or a
    // switch to a different room; drop a now-stale connect attempt.
    if (this.roomId !== roomId || this.closedByUser) return;
    let ws: WebSocket;
    try {
      ws = new WebSocket(url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws = ws;

    ws.onopen = () => {
      const reconnected = this.connectedOnce;
      this.connectedOnce = true;
      this.attempts = 0;
      this.emitStatus("live");
      if (reconnected) {
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

    ws.onclose = () => {
      if (this.ws === ws) this.ws = null;
      this.scheduleReconnect();
    };

    ws.onerror = () => {
      // onclose follows; reconnection handled there.
    };
  }

  private scheduleReconnect(): void {
    if (this.closedByUser || this.reconnectTimer !== null) return;
    this.emitStatus(this.connectedOnce ? "reconnecting" : "connecting");
    const delay = Math.min(30000, 1000 * 2 ** this.attempts);
    this.attempts += 1;
    if (this.attempts >= 5) {
      this.emitStatus("offline");
    }
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
    if (this.ws) {
      const ws = this.ws;
      this.ws = null;
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
  }
}
