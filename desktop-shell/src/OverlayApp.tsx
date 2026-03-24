import { type FormEvent, type KeyboardEvent, useEffect, useRef, useState } from "react";
import OverlayPhaserWrapper from "./OverlayPhaserWrapper";
import { DesktopBridgeClient, type ConnectionState } from "./lib/bridgeClient";
import {
  OVERLAY_PET_ACTIVATE_EVENT,
  OVERLAY_PET_HOVER_EVENT,
  type OverlayPetUiState,
  dispatchOverlayInteractionLockState,
} from "./lib/overlayPetUi";
import { DEFAULT_PACKET, type DesktopPacket, type ServerEvent } from "./lib/protocol";
import { useAvatarPreset } from "./lib/useAvatarPreset";

const defaultBridgeUrl = import.meta.env.VITE_DESKTOP_WS_URL ?? "ws://127.0.0.1:8765";
const screenMargin = 12;
const bubbleHoldMs = 12000;
const composerHideDelayMs = 280;

function clamp(value: number, min: number, max: number): number {
  if (min > max) {
    return value;
  }
  return Math.min(Math.max(value, min), max);
}

export default function OverlayApp() {
  const { selectedPreset } = useAvatarPreset();
  const [packet, setPacket] = useState<DesktopPacket>(DEFAULT_PACKET);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [threadId, setThreadId] = useState(DEFAULT_PACKET.thread_id);
  const [replyText, setReplyText] = useState("");
  const [lastCompletedReply, setLastCompletedReply] = useState("");
  const [isReplyStreaming, setIsReplyStreaming] = useState(false);
  const [bubbleHoldUntil, setBubbleHoldUntil] = useState(0);
  const [petUiState, setPetUiState] = useState<OverlayPetUiState | null>(null);
  const [isPetHovered, setIsPetHovered] = useState(false);
  const [isComposerHovered, setIsComposerHovered] = useState(false);
  const [isComposerFocused, setIsComposerFocused] = useState(false);
  const [isComposerOpen, setIsComposerOpen] = useState(false);
  const [composerSuppressedUntilPetLeave, setComposerSuppressedUntilPetLeave] = useState(false);
  const [draft, setDraft] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [viewport, setViewport] = useState(() => ({
    width: window.innerWidth,
    height: window.innerHeight,
  }));
  const clientRef = useRef<DesktopBridgeClient | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const hideComposerTimerRef = useRef<number | null>(null);
  const focusComposerOnOpenRef = useRef(false);
  const isReplyStreamingRef = useRef(false);
  const phaseRef = useRef(DEFAULT_PACKET.phase);

  useEffect(() => {
    const client = new DesktopBridgeClient(defaultBridgeUrl, {
      onConnection: setConnection,
      onEvent: (event: ServerEvent) => {
        switch (event.type) {
          case "ready":
            setThreadId(event.payload.default_thread_id);
            break;
          case "surface_state":
            if (event.payload.phase === "listening" && phaseRef.current !== "listening") {
              setReplyText("");
              isReplyStreamingRef.current = false;
              setBubbleHoldUntil(0);
            }
            setPacket(event.payload);
            phaseRef.current = event.payload.phase;
            break;
          case "reply_chunk":
            setReplyText((current) =>
              isReplyStreamingRef.current ? current + event.payload.chunk : event.payload.chunk,
            );
            isReplyStreamingRef.current = true;
            setIsReplyStreaming(true);
            setBubbleHoldUntil(Date.now() + bubbleHoldMs);
            break;
          case "reply_done":
            setReplyText(event.payload.text);
            if (event.payload.text.trim()) {
              setLastCompletedReply(event.payload.text);
            }
            isReplyStreamingRef.current = false;
            setIsReplyStreaming(false);
            setIsSending(false);
            setBubbleHoldUntil(Date.now() + bubbleHoldMs);
            break;
          case "turn_error": {
            const message = `连接内核时出错：${event.payload.error}`;
            setReplyText(message);
            setLastCompletedReply(message);
            isReplyStreamingRef.current = false;
            setIsReplyStreaming(false);
            setIsSending(false);
            setBubbleHoldUntil(Date.now() + bubbleHoldMs);
            break;
          }
          case "error": {
            const message = `桌面桥接异常：${event.payload.message}`;
            setReplyText(message);
            setLastCompletedReply(message);
            isReplyStreamingRef.current = false;
            setIsReplyStreaming(false);
            setIsSending(false);
            setBubbleHoldUntil(Date.now() + bubbleHoldMs);
            break;
          }
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
    const handleResize = () => {
      setViewport({
        width: window.innerWidth,
        height: window.innerHeight,
      });
    };

    const handlePetHover = (event: Event) => {
      const hovered = Boolean((event as CustomEvent<boolean>).detail);
      setIsPetHovered(hovered);
      if (!hovered) {
        setComposerSuppressedUntilPetLeave(false);
      }
    };

    const handlePetActivate = () => {
      setComposerSuppressedUntilPetLeave(false);
      focusComposerOnOpenRef.current = true;
      setIsComposerOpen(true);
    };

    window.addEventListener("resize", handleResize);
    window.addEventListener(OVERLAY_PET_HOVER_EVENT, handlePetHover);
    window.addEventListener(OVERLAY_PET_ACTIVATE_EVENT, handlePetActivate);

    return () => {
      window.removeEventListener("resize", handleResize);
      window.removeEventListener(OVERLAY_PET_HOVER_EVENT, handlePetHover);
      window.removeEventListener(OVERLAY_PET_ACTIVATE_EVENT, handlePetActivate);
    };
  }, []);

  useEffect(() => {
    if (bubbleHoldUntil <= 0) {
      return;
    }

    const delay = bubbleHoldUntil - Date.now();
    if (delay <= 0) {
      setBubbleHoldUntil(0);
      return;
    }

    const timer = window.setTimeout(() => {
      setBubbleHoldUntil(0);
    }, delay);

    return () => {
      window.clearTimeout(timer);
    };
  }, [bubbleHoldUntil]);

  useEffect(() => {
    const shouldKeepComposerOpen =
      Boolean(petUiState) &&
      (
        (!composerSuppressedUntilPetLeave && isPetHovered) ||
        isComposerHovered ||
        isComposerFocused ||
        draft.trim().length > 0
      );

    if (shouldKeepComposerOpen) {
      if (hideComposerTimerRef.current !== null) {
        window.clearTimeout(hideComposerTimerRef.current);
        hideComposerTimerRef.current = null;
      }
      setIsComposerOpen(true);
      return;
    }

    hideComposerTimerRef.current = window.setTimeout(() => {
      setIsComposerOpen(false);
      hideComposerTimerRef.current = null;
    }, composerHideDelayMs);

    return () => {
      if (hideComposerTimerRef.current !== null) {
        window.clearTimeout(hideComposerTimerRef.current);
        hideComposerTimerRef.current = null;
      }
    };
  }, [draft, isComposerFocused, isComposerHovered, isSending, isPetHovered, petUiState]);

  useEffect(() => {
    dispatchOverlayInteractionLockState(isComposerOpen);
  }, [isComposerOpen]);

  useEffect(() => {
    return () => {
      dispatchOverlayInteractionLockState(false);
    };
  }, []);

  useEffect(() => {
    if (!isComposerOpen || !focusComposerOnOpenRef.current) {
      return;
    }

    const timer = window.setTimeout(() => {
      composerRef.current?.focus();
      focusComposerOnOpenRef.current = false;
    }, 0);

    return () => {
      window.clearTimeout(timer);
    };
  }, [isComposerOpen]);

  const submitDraft = () => {
    const text = draft.trim();
    if (!text || !clientRef.current) {
      return;
    }

    composerRef.current?.blur();
    setComposerSuppressedUntilPetLeave(true);
    setIsComposerHovered(false);
    setIsComposerFocused(false);
    setIsComposerOpen(false);
    setDraft("");
    setReplyText("");
    isReplyStreamingRef.current = false;
    setIsReplyStreaming(false);
    setIsSending(true);

    try {
      clientRef.current.sendUserInput({
        text,
        thread_id: threadId,
      });
    } catch (error) {
      const message = `桌面桥接未连接：${String(error)}`;
      setReplyText(message);
      setLastCompletedReply(message);
      setIsSending(false);
      setBubbleHoldUntil(Date.now() + bubbleHoldMs);
    }
  };

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    submitDraft();
  };

  const handleComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submitDraft();
      return;
    }

    if (event.key === "Escape") {
      event.preventDefault();
      composerRef.current?.blur();
      if (!draft.trim()) {
        setComposerSuppressedUntilPetLeave(true);
        setIsComposerHovered(false);
        setIsComposerOpen(false);
      }
    }
  };

  const bubbleText = replyText || (packet.phase === "idle" ? lastCompletedReply : "");
  const hasBubbleText = bubbleText.trim().length > 0;
  const shouldShowBubble = Boolean(
    petUiState &&
      hasBubbleText &&
      !isComposerOpen &&
      (isReplyStreaming || packet.bubble_visible || bubbleHoldUntil > Date.now()),
  );

  const bubbleMaxWidth = Math.max(140, Math.min(320, viewport.width - screenMargin * 2));
  const bubbleHalfWidth = bubbleMaxWidth / 2;
  const bubbleBelowPet = (petUiState?.petTop ?? 0) < 104;
  const bubbleLeft = petUiState
    ? clamp(
        petUiState.anchorX,
        screenMargin + bubbleHalfWidth,
        viewport.width - screenMargin - bubbleHalfWidth,
      )
    : screenMargin + bubbleHalfWidth;
  const bubbleTop = petUiState
    ? bubbleBelowPet
      ? clamp(petUiState.petBottom + 18, 20, viewport.height - 20)
      : clamp(petUiState.petTop - 18, 20, viewport.height - 20)
    : 20;

  const composerWidth = Math.max(220, Math.min(320, viewport.width - screenMargin * 2));
  const composerHalfWidth = composerWidth / 2;
  const composerBelowPet = (petUiState?.petTop ?? 0) < 168;
  const composerLeft = petUiState
    ? clamp(
        petUiState.anchorX,
        screenMargin + composerHalfWidth,
        viewport.width - screenMargin - composerHalfWidth,
      )
    : screenMargin + composerHalfWidth;
  const composerTop = petUiState
    ? composerBelowPet
      ? clamp(petUiState.petBottom + 14, 12, viewport.height - 12)
      : clamp(petUiState.petTop - 14, 12, viewport.height - 12)
    : 20;

  return (
    <main className={`overlay-root ${isComposerOpen ? "composer-visible" : ""}`}>
      <OverlayPhaserWrapper
        spriteConfig={selectedPreset.spriteConfig}
        onPetUiStateChange={setPetUiState}
      />

      <form
        className={`overlay-composer ${isComposerOpen ? "is-visible" : ""} ${composerBelowPet ? "is-below" : "is-above"}`}
        onMouseEnter={() => setIsComposerHovered(true)}
        onMouseLeave={() => setIsComposerHovered(false)}
        onSubmit={handleSubmit}
        style={{
          left: `${Math.round(composerLeft)}px`,
          top: `${Math.round(composerTop)}px`,
          maxWidth: `${Math.round(composerWidth)}px`,
        }}
      >
        <div className="overlay-composer-shell">
          <p className="overlay-composer-label">Front Input</p>
          <div className="overlay-composer-row">
            <textarea
              ref={composerRef}
              className="overlay-composer-input"
              placeholder={connection === "connected" ? "和它说句话..." : "桌面桥接连接中..."}
              rows={2}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onFocus={() => setIsComposerFocused(true)}
              onBlur={() => setIsComposerFocused(false)}
              onKeyDown={handleComposerKeyDown}
            />
            <button
              className="overlay-composer-button"
              type="submit"
              disabled={connection !== "connected" || isSending || !draft.trim()}
            >
              {isSending ? "发送中" : "发送"}
            </button>
          </div>
        </div>
      </form>

      <div
        aria-hidden={!shouldShowBubble}
        className={`overlay-dialog ${shouldShowBubble ? "is-visible" : ""} ${bubbleBelowPet ? "is-below" : "is-above"} ${petUiState?.facingLeft ? "is-facing-left" : "is-facing-right"}`}
        style={{
          left: `${Math.round(bubbleLeft)}px`,
          top: `${Math.round(bubbleTop)}px`,
          maxWidth: `${Math.round(bubbleMaxWidth)}px`,
        }}
      >
        <div className="overlay-dialog-shell">
          <p className="overlay-dialog-text">{bubbleText}</p>
        </div>
      </div>
    </main>
  );
}
