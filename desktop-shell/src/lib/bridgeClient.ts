import type { ServerEvent, UserInputPayload } from "./protocol";

export type ConnectionState = "connecting" | "connected" | "disconnected";

interface DesktopBridgeHandlers {
  onConnection: (state: ConnectionState) => void;
  onEvent: (event: ServerEvent) => void;
}

export class DesktopBridgeClient {
  private readonly url: string;
  private readonly handlers: DesktopBridgeHandlers;
  private socket: WebSocket | null = null;
  private reconnectTimer: number | null = null;
  private closed = false;

  constructor(url: string, handlers: DesktopBridgeHandlers) {
    this.url = url;
    this.handlers = handlers;
    this.connect();
  }

  sendUserInput(payload: UserInputPayload): void {
    if (this.socket?.readyState !== WebSocket.OPEN) {
      throw new Error("desktop bridge is not connected");
    }
    this.socket.send(
      JSON.stringify({
        type: "user_input",
        payload,
      }),
    );
  }

  close(): void {
    this.closed = true;
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.socket?.close();
  }

  private connect(): void {
    this.handlers.onConnection("connecting");
    const socket = new WebSocket(this.url);
    this.socket = socket;

    socket.addEventListener("open", () => {
      this.handlers.onConnection("connected");
    });

    socket.addEventListener("message", (message) => {
      try {
        const event = JSON.parse(message.data) as ServerEvent;
        this.handlers.onEvent(event);
      } catch (_error) {
        this.handlers.onEvent({
          type: "error",
          payload: { message: "desktop bridge returned invalid JSON" },
        });
      }
    });

    socket.addEventListener("close", () => {
      if (this.closed) {
        return;
      }
      this.handlers.onConnection("disconnected");
      this.reconnectTimer = window.setTimeout(() => {
        this.connect();
      }, 1200);
    });

    socket.addEventListener("error", () => {
      if (!this.closed) {
        this.handlers.onConnection("disconnected");
      }
    });
  }
}
