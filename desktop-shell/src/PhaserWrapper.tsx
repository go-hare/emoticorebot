import { useEffect, useRef } from "react";
import Phaser from "phaser";
import type { DesktopPacket } from "./lib/protocol";
import type { ISpriteConfig } from "./types/ISpriteConfig";
import SurfaceAvatarScene from "./scenes/SurfaceAvatarScene";

interface PhaserWrapperProps {
  packet: DesktopPacket;
  speechPulseTick: number;
  spriteConfig: ISpriteConfig | null;
}

function FallbackAvatar({
  packet,
  speechPulseTick,
}: {
  packet: DesktopPacket;
  speechPulseTick: number;
}) {
  return (
    <div className={`fallback-avatar mood-${packet.mood} phase-${packet.phase}`}>
      <div className={`fallback-core animation-${packet.animation} pulse-${speechPulseTick % 2}`} />
      <div className="fallback-shadow" />
    </div>
  );
}

export default function PhaserWrapper({
  packet,
  speechPulseTick,
  spriteConfig,
}: PhaserWrapperProps) {
  const phaserDom = useRef<HTMLDivElement>(null);
  const gameRef = useRef<Phaser.Game | null>(null);

  useEffect(() => {
    if (!phaserDom.current || !spriteConfig) {
      return;
    }

    const game = new Phaser.Game({
      type: Phaser.AUTO,
      parent: phaserDom.current,
      backgroundColor: "#00000000",
      transparent: true,
      width: 320,
      height: 320,
      audio: { noAudio: true },
      scene: [SurfaceAvatarScene],
      callbacks: {
        preBoot: (instance) => {
          instance.registry.set("spriteConfig", spriteConfig);
          instance.registry.set("surfacePacket", packet);
        },
      },
    });

    gameRef.current = game;
    return () => {
      gameRef.current = null;
      game.destroy(true);
      if (phaserDom.current) {
        phaserDom.current.innerHTML = "";
      }
    };
  }, [spriteConfig]);

  useEffect(() => {
    if (!spriteConfig || !gameRef.current) {
      return;
    }
    gameRef.current.events.emit("surface:update", packet);
  }, [packet, spriteConfig]);

  useEffect(() => {
    if (!gameRef.current) {
      return;
    }
    gameRef.current.events.emit("speech:pulse", {
      phase: packet.phase,
      mood: packet.mood,
      tick: speechPulseTick,
    });
  }, [packet.mood, packet.phase, speechPulseTick]);

  if (!spriteConfig) {
    return <FallbackAvatar packet={packet} speechPulseTick={speechPulseTick} />;
  }

  return <div className="avatar-canvas" ref={phaserDom} />;
}
