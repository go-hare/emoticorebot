import { useEffect, useRef, useState, type FormEvent } from "react";
import PhaserWrapper from "./PhaserWrapper";
import { DesktopBridgeClient, type ConnectionState } from "./lib/bridgeClient";
import { readAffectStateSnapshot } from "./lib/affectState";
import { avatarSpriteConfig } from "./lib/avatarConfig";
import {
  DEFAULT_PACKET,
  type AffectStateSnapshot,
  type DesktopPacket,
  type ServerEvent,
} from "./lib/protocol";

const defaultBridgeUrl = import.meta.env.VITE_DESKTOP_WS_URL ?? "ws://127.0.0.1:8765";

function prettify(value: string): string {
  return value
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

export default function App() {
  const [packet, setPacket] = useState<DesktopPacket>(DEFAULT_PACKET);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [draft, setDraft] = useState("");
  const [replyText, setReplyText] = useState("");
  const [lastCompletedReply, setLastCompletedReply] = useState("");
  const [speechPulseTick, setSpeechPulseTick] = useState(0);
  const [lastUserText, setLastUserText] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [threadId, setThreadId] = useState(DEFAULT_PACKET.thread_id);
  const [affectPath, setAffectPath] = useState(
    import.meta.env.VITE_AFFECT_STATE_PATH ?? "",
  );
  const [affectState, setAffectState] = useState<AffectStateSnapshot | null>(null);
  const clientRef = useRef<DesktopBridgeClient | null>(null);
  const isReplyStreamingRef = useRef(false);

  useEffect(() => {
    const client = new DesktopBridgeClient(defaultBridgeUrl, {
      onConnection: setConnection,
      onEvent: (event: ServerEvent) => {
        switch (event.type) {
          case "ready":
            setThreadId(event.payload.default_thread_id);
            setAffectPath(event.payload.affect_state_path);
            break;
          case "surface_state":
            setPacket(event.payload);
            break;
          case "reply_chunk":
            setReplyText((current) =>
              isReplyStreamingRef.current ? current + event.payload.chunk : event.payload.chunk,
            );
            isReplyStreamingRef.current = true;
            setSpeechPulseTick((current) => current + 1);
            break;
          case "reply_done":
            setReplyText(event.payload.text);
            if (event.payload.text.trim()) {
              setLastCompletedReply(event.payload.text);
            }
            isReplyStreamingRef.current = false;
            setIsSending(false);
            break;
          case "turn_error":
            setReplyText(`连接内核时出错：${event.payload.error}`);
            setLastCompletedReply(`连接内核时出错：${event.payload.error}`);
            isReplyStreamingRef.current = false;
            setIsSending(false);
            break;
          case "affect_state":
            setAffectState(event.payload);
            break;
          case "error":
            setReplyText(`桌面桥接异常：${event.payload.message}`);
            setLastCompletedReply(`桌面桥接异常：${event.payload.message}`);
            isReplyStreamingRef.current = false;
            setIsSending(false);
            break;
        }
      },
    });

    clientRef.current = client;
    return () => {
      client.close();
      clientRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!affectPath || packet.phase !== "idle") {
      return;
    }

    void readAffectStateSnapshot(affectPath).then((snapshot) => {
      if (snapshot) {
        setAffectState(snapshot);
      }
    });
  }, [affectPath, packet.phase]);

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text || !clientRef.current) {
      return;
    }

    setDraft("");
    setLastUserText(text);
    setReplyText("");
    isReplyStreamingRef.current = false;
    setIsSending(true);
    try {
      clientRef.current.sendUserInput({
        text,
        thread_id: threadId,
      });
    } catch (error) {
      setReplyText(`桌面桥接未连接：${String(error)}`);
      setIsSending(false);
    }
  };

  const bubbleText = replyText || lastCompletedReply || "";
  const bubbleLabel = bubbleText ? "当前回应" : "";
  const vitality = affectState?.vitality;
  const pressure = affectState?.pressure;

  return (
    <main className="shell-root">
      <section className="shell-panel">
        <header className="shell-header" data-tauri-drag-region>
          <div>
            <p className="eyebrow">EmotiCore Desktop Shell</p>
            <h1>陪伴壳</h1>
          </div>
          <div className={`status-pill is-${connection}`}>{connection}</div>
        </header>

        <section className="stage-card">
          <div className="stage-copy">
            <span className="phase-tag">{prettify(packet.phase)}</span>
            <span className="mood-tag">{prettify(packet.mood)}</span>
          </div>
          <PhaserWrapper
            packet={packet}
            speechPulseTick={speechPulseTick}
            spriteConfig={avatarSpriteConfig}
          />
          <div className="stage-footer">
            <span>{prettify(packet.animation)}</span>
            <span>{prettify(packet.body_state)}</span>
          </div>
        </section>

        <section
          className={`bubble-card ${packet.bubble_visible ? "is-live" : ""} phase-${packet.phase} mood-${packet.mood} pulse-${speechPulseTick % 2}`}
        >
          <p className="bubble-label">{bubbleLabel}</p>
          <p className={`bubble-text pulse-${speechPulseTick % 2}`}>{bubbleText}</p>
          {lastUserText ? <p className="bubble-user">你: {lastUserText}</p> : null}
        </section>

        <section className="affect-card">
          <div>
            <p className="eyebrow">Idle Baseline</p>
            <strong>{affectState?.updated_at ?? "waiting for affect snapshot"}</strong>
          </div>
          <div className="affect-metrics">
            <div>
              <span>Vitality</span>
              <strong>{typeof vitality === "number" ? vitality.toFixed(2) : "--"}</strong>
            </div>
            <div>
              <span>Pressure</span>
              <strong>{typeof pressure === "number" ? pressure.toFixed(2) : "--"}</strong>
            </div>
          </div>
        </section>

        <form className="composer" onSubmit={handleSubmit}>
          <textarea
            className="composer-input"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="把前台输入直接丢给内核..."
            rows={3}
          />
          <button
            className="composer-button"
            type="submit"
            disabled={connection !== "connected" || isSending || !draft.trim()}
          >
            {isSending ? "发送中" : "投递"}
          </button>
        </form>
      </section>
    </main>
  );
}
