// Room WebSocket client with auto-reconnect and exponential backoff.
import { API_BASE } from "./api";
import { getAccessToken, isEntraAuth } from "./auth";
import type { RoomWsEvent } from "./types";

async function wsUrl(roomId: string): Promise<string> {
  const base = new URL(API_BASE, window.location.origin);
  const proto = base.protocol === "https:" ? "wss:" : "ws:";
  const url = new URL(`${proto}//${base.host}/ws/rooms/${roomId}`);
  // Browsers cannot set an Authorization header on the WS handshake, so the
  // verified Entra ID access token travels as a query param instead; the
  // backend validates it the same way as the HTTP bearer token (ws.py).
  if (isEntraAuth) {
    url.searchParams.set("access_token", await getAccessToken());
  }
  return url.toString();
}

export type WsEventHandler = (event: RoomWsEvent) => void;

export class RoomSocket {
  private ws: WebSocket | null = null;
  private roomId: string | null = null;
  private onEvent: WsEventHandler | null = null;
  private closedByUser = false;
  private attempts = 0;
  private reconnectTimer: number | null = null;

  connect(roomId: string, onEvent: WsEventHandler): void {
    this.close();
    this.roomId = roomId;
    this.onEvent = onEvent;
    this.closedByUser = false;
    this.attempts = 0;
    this.open();
  }

  private open(): void {
    if (this.roomId === null || this.closedByUser) return;
    const roomId = this.roomId;
    wsUrl(roomId)
      .then((url) => this.openWithUrl(roomId, url))
      .catch(() => this.scheduleReconnect());
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
      this.attempts = 0;
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
    const delay = Math.min(30000, 1000 * 2 ** this.attempts);
    this.attempts += 1;
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
  }
}
