import { useEffect, useRef, useState } from "react";
import Phaser from "phaser";
import { appWindow } from "@tauri-apps/api/window";
import type { ISpriteConfig } from "./types/ISpriteConfig";
import DesktopPetOverlayScene from "./scenes/DesktopPetOverlayScene";
import { OVERLAY_PET_UI_EVENT, type OverlayPetUiState } from "./lib/overlayPetUi";

interface OverlayPhaserWrapperProps {
  spriteConfig: ISpriteConfig | null;
  onPetUiStateChange?: (state: OverlayPetUiState) => void;
}

export default function OverlayPhaserWrapper({
  spriteConfig,
  onPetUiStateChange,
}: OverlayPhaserWrapperProps) {
  const phaserDom = useRef<HTMLDivElement>(null);
  const gameRef = useRef<Phaser.Game | null>(null);
  const [screenWidth, setScreenWidth] = useState(window.screen.width);
  const [screenHeight, setScreenHeight] = useState(window.screen.height);

  useEffect(() => {
    if (!onPetUiStateChange) {
      return;
    }

    const handlePetUiState = (event: Event) => {
      const detail = (event as CustomEvent<OverlayPetUiState>).detail;
      if (detail) {
        onPetUiStateChange(detail);
      }
    };

    window.addEventListener(OVERLAY_PET_UI_EVENT, handlePetUiState);
    return () => {
      window.removeEventListener(OVERLAY_PET_UI_EVENT, handlePetUiState);
    };
  }, [onPetUiStateChange]);

  useEffect(() => {
    if (!phaserDom.current || !spriteConfig) {
      return;
    }

    const handleResize = () => {
      setScreenWidth(window.screen.width);
      setScreenHeight(window.screen.height);
    };

    window.addEventListener("resize", handleResize);
    void appWindow.setIgnoreCursorEvents(true);

    const game = new Phaser.Game({
      type: Phaser.AUTO,
      parent: phaserDom.current,
      backgroundColor: "#00000000",
      transparent: true,
      roundPixels: true,
      antialias: true,
      scale: {
        mode: Phaser.Scale.ScaleModes.RESIZE,
        width: screenWidth,
        height: screenHeight,
      },
      physics: {
        default: "arcade",
        arcade: {
          debug: false,
          gravity: { y: 200, x: 0 },
        },
      },
      fps: {
        target: 30,
        min: 30,
        smoothStep: true,
      },
      scene: [DesktopPetOverlayScene],
      audio: {
        noAudio: true,
      },
      callbacks: {
        preBoot: (instance) => {
          instance.registry.set("spriteConfig", spriteConfig);
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
      window.removeEventListener("resize", handleResize);
    };
  }, [screenHeight, screenWidth, spriteConfig]);

  return <div className="overlay-canvas" ref={phaserDom} />;
}
