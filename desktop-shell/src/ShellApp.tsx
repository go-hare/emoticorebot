import { appWindow } from "@tauri-apps/api/window";
import { useEffect, useRef, useState, type FormEvent } from "react";
import PhaserWrapper from "./PhaserWrapper";
import { readAffectStateSnapshot } from "./lib/affectState";
import { DesktopBridgeClient, type ConnectionState } from "./lib/bridgeClient";
import {
  DEFAULT_PACKET,
  type AffectStateSnapshot,
  type DesktopPacket,
  type ServerEvent,
} from "./lib/protocol";
import type { ISpriteConfig } from "./types/ISpriteConfig";
import { useAvatarPreset } from "./lib/useAvatarPreset";

const defaultBridgeUrl = import.meta.env.VITE_DESKTOP_WS_URL ?? "ws://127.0.0.1:8765";

function prettify(value: string): string {
  return value.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function coerceFiniteNumber(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return undefined;
}

function extractAffectSnapshotFromPacket(packet: DesktopPacket): AffectStateSnapshot | null {
  const metadata = packet.metadata ?? {};
  const vitality = coerceFiniteNumber(metadata["affect_vitality"]);
  const pressure = coerceFiniteNumber(metadata["affect_pressure"]);
  const updatedAtRaw = metadata["affect_updated_at"];
  const updatedAt = typeof updatedAtRaw === "string" ? updatedAtRaw.trim() : "";
  if (vitality === undefined && pressure === undefined && !updatedAt) {
    return null;
  }
  return {
    vitality,
    pressure,
    updated_at: updatedAt || undefined,
  };
}

function PetPresetPreview({
  spriteConfig,
  label,
}: {
  spriteConfig: ISpriteConfig;
  label: string;
}) {
  const previewSize = 96;
  const sheetColumns = spriteConfig.sheetColumns ?? 8;

  return (
    <div
      aria-label={label}
      className="pet-preset-preview"
      role="img"
      style={{
        backgroundImage: `url(${spriteConfig.imageSrc})`,
        backgroundPosition: "0 0",
        backgroundSize: `${previewSize * sheetColumns}px auto`,
      }}
    />
  );
}

export default function ShellApp() {
  const [packet, setPacket] = useState<DesktopPacket>(DEFAULT_PACKET);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [draft, setDraft] = useState("");
  const [replyText, setReplyText] = useState("");
  const [lastCompletedReply, setLastCompletedReply] = useState("");
  const [speechPulseTick, setSpeechPulseTick] = useState(0);
  const [lastUserText, setLastUserText] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [threadId, setThreadId] = useState(DEFAULT_PACKET.thread_id);
  const [affectPath, setAffectPath] = useState(import.meta.env.VITE_AFFECT_STATE_PATH ?? "");
  const [affectState, setAffectState] = useState<AffectStateSnapshot | null>(null);
  const clientRef = useRef<DesktopBridgeClient | null>(null);
  const isReplyStreamingRef = useRef(false);
  const phaseRef = useRef(DEFAULT_PACKET.phase);
  const { avatarPresets, selectedPreset, selectedPresetId, setSelectedPresetId } = useAvatarPreset();

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
            if (event.payload.phase === "listening" && phaseRef.current !== "listening") {
              setReplyText("");
              isReplyStreamingRef.current = false;
            }
            setPacket(event.payload);
            {
              const snapshot = extractAffectSnapshotFromPacket(event.payload);
              if (snapshot) {
                setAffectState((current) => ({ ...(current ?? {}), ...snapshot }));
              }
            }
            phaseRef.current = event.payload.phase;
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
    let disposed = false;
    let unlisten: (() => void) | undefined;

    void appWindow.onCloseRequested((event) => {
      event.preventDefault();
      void appWindow.hide();
    }).then((unsubscribe) => {
      if (disposed) {
        unsubscribe();
        return;
      }
      unlisten = unsubscribe;
    });

    return () => {
      disposed = true;
      unlisten?.();
    };
  }, []);

  useEffect(() => {
    if (!affectPath) {
      return;
    }
    let disposed = false;
    const refresh = () => {
      void readAffectStateSnapshot(affectPath).then((snapshot) => {
        if (!disposed && snapshot) {
          setAffectState(snapshot);
        }
      });
    };
    refresh();
    const timer = window.setInterval(refresh, 5000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [affectPath]);

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

  const bubbleText = replyText || (packet.phase === "idle" ? lastCompletedReply : "");
  const bubbleLabel = bubbleText ? "当前回应" : "";
  const vitality = affectState?.vitality;
  const pressure = affectState?.pressure;

  return (
    <main className="shell-root">
      <section className="shell-panel">
        <header className="shell-header">
          <div>
            <p className="eyebrow">EmotiCore Setting</p>
            <h1>陪伴设置</h1>
          </div>
          <div className="shell-header-actions">
            <div className={`status-pill is-${connection}`}>{connection}</div>
            <button className="utility-button" type="button" onClick={() => void appWindow.hide()}>
              隐藏
            </button>
          </div>
        </header>

        <section className="pet-settings-card">
          <div className="pet-settings-copy">
            <div>
              <p className="eyebrow">My Pets</p>
              <h2>更换桌宠</h2>
              <p className="section-copy">
                应用启动后默认只保留桌宠层，这里负责切换 pet 形象，同时保留你现在这套内核状态面板。
              </p>
            </div>
            <div className="pet-current-pill">{selectedPreset.label}</div>
          </div>
          <div className="pet-preset-grid">
            {avatarPresets.map((preset) => (
              <button
                key={preset.id}
                className={`pet-preset-card ${preset.id === selectedPresetId ? "is-active" : ""}`}
                type="button"
                onClick={() => setSelectedPresetId(preset.id)}
              >
                <div className="pet-preset-preview-frame">
                  <PetPresetPreview label={preset.label} spriteConfig={preset.spriteConfig} />
                </div>
                <strong>{preset.label}</strong>
                <span>{preset.description}</span>
              </button>
            ))}
          </div>
        </section>

        <section className="shell-grid">
          <div className="shell-grid-column">
            <section className="stage-card">
              <div className="stage-copy">
                <span className="phase-tag">{prettify(packet.phase)}</span>
                <span className="mood-tag">{prettify(packet.mood)}</span>
              </div>
              <PhaserWrapper
                packet={packet}
                speechPulseTick={speechPulseTick}
                spriteConfig={selectedPreset.spriteConfig}
              />
              <div className="stage-footer">
                <span>{selectedPreset.label}</span>
                <span>{`${prettify(packet.animation)} · ${prettify(packet.body_state)}`}</span>
              </div>
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
          </div>

          <div className="shell-grid-column">
            <section
              className={`bubble-card ${packet.bubble_visible ? "is-live" : ""} phase-${packet.phase} mood-${packet.mood} pulse-${speechPulseTick % 2}`}
            >
              <p className="bubble-label">{bubbleLabel}</p>
              <p className={`bubble-text pulse-${speechPulseTick % 2}`}>{bubbleText}</p>
              {lastUserText ? <p className="bubble-user">你: {lastUserText}</p> : null}
            </section>

            <form className="composer-panel" onSubmit={handleSubmit}>
              <div className="composer-panel-copy">
                <p className="eyebrow">Front Input</p>
                <h3>直接投递给内核</h3>
                <p className="section-copy">设置窗不用常驻前台，但打开时仍然可以直接把输入丢进你的 runtime。</p>
              </div>
              <div className="composer">
                <textarea
                  className="composer-input"
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  placeholder="把前台输入直接丢给内核..."
                  rows={4}
                />
                <button
                  className="composer-button"
                  type="submit"
                  disabled={connection !== "connected" || isSending || !draft.trim()}
                >
                  {isSending ? "发送中" : "投递"}
                </button>
              </div>
            </form>
          </div>
        </section>
      </section>
    </main>
  );
}
