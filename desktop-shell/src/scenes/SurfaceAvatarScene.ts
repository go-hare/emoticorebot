import Phaser from "phaser";
import type { ISpriteConfig } from "../types/ISpriteConfig";
import { DEFAULT_PACKET, type DesktopPacket } from "../lib/protocol";

export default class SurfaceAvatarScene extends Phaser.Scene {
  private spriteConfig: ISpriteConfig | null = null;
  private avatar: Phaser.GameObjects.Sprite | null = null;
  private packet: DesktopPacket = DEFAULT_PACKET;
  private postureTween: Phaser.Tweens.Tween | null = null;
  private speechPulseTween: Phaser.Tweens.Tween | null = null;

  constructor() {
    super({ key: "surface-avatar" });
  }

  preload(): void {
    this.spriteConfig = this.game.registry.get("spriteConfig") ?? null;
    this.packet = this.game.registry.get("surfacePacket") ?? DEFAULT_PACKET;

    if (!this.spriteConfig) {
      return;
    }

    this.load.spritesheet(this.spriteConfig.name, this.spriteConfig.imageSrc, {
      frameWidth: this.spriteConfig.frameSize,
      frameHeight: this.spriteConfig.frameSize,
    });
  }

  create(): void {
    this.cameras.main.setBackgroundColor("rgba(0, 0, 0, 0)");
    this.game.events.on("surface:update", this.applyPacket, this);
    this.game.events.on("speech:pulse", this.handleSpeechPulse, this);

    if (!this.spriteConfig) {
      return;
    }

    for (const [stateName, range] of Object.entries(this.spriteConfig.states)) {
      const key = this.keyFor(stateName);
      if (this.anims.exists(key)) {
        continue;
      }
      this.anims.create({
        key,
        frames: this.anims.generateFrameNumbers(this.spriteConfig.name, {
          start: range.start,
          end: range.end,
        }),
        frameRate: this.spriteConfig.frameRates?.[stateName] ?? 8,
        repeat: -1,
      });
    }

    this.avatar = this.add.sprite(160, 176, this.spriteConfig.name);
    this.avatar.setScale(this.spriteConfig.scale ?? 1);
    this.applyPacket(this.packet);
  }

  shutdown(): void {
    this.postureTween?.remove();
    this.speechPulseTween?.remove();
    this.postureTween = null;
    this.speechPulseTween = null;
    this.game.events.off("surface:update", this.applyPacket, this);
    this.game.events.off("speech:pulse", this.handleSpeechPulse, this);
  }

  private applyPacket(packet: DesktopPacket): void {
    this.packet = packet;
    if (!this.avatar || !this.spriteConfig) {
      return;
    }

    const animationKey = this.resolveAnimation(packet);
    if (animationKey) {
      this.avatar.play(animationKey, true);
    }
    this.applyPosture(packet);
  }

  private resolveAnimation(packet: DesktopPacket): string | null {
    if (!this.spriteConfig) {
      return null;
    }

    const candidates = [
      ...(this.spriteConfig.resolveStateCandidates?.(packet) ?? []),
      packet.animation,
      packet.avatar_phase,
      packet.phase,
      "idle",
      "sit",
      "stand",
    ];

    for (const candidate of candidates) {
      if (candidate in this.spriteConfig.states) {
        return this.keyFor(candidate);
      }
    }

    const [fallbackState] = Object.keys(this.spriteConfig.states);
    return fallbackState ? this.keyFor(fallbackState) : null;
  }

  private keyFor(stateName: string): string {
    return `${this.spriteConfig?.name ?? "avatar"}:${stateName}`;
  }

  private handleSpeechPulse(event: {
    phase?: string;
    mood?: string;
    tick?: number;
  }): void {
    if (!this.avatar || !this.spriteConfig) {
      return;
    }
    if (event.phase !== "replying" || !event.tick) {
      return;
    }

    const basePosture = this.resolvePosture(this.packet);
    const pulseLift = event.mood === "playful" ? -8 : -5;
    const pulseScale = event.mood === "soothing" ? 1.015 : 1.028;

    this.speechPulseTween?.remove();
    this.speechPulseTween = this.tweens.add({
      targets: this.avatar,
      y: 176 + basePosture.yOffset + pulseLift,
      scaleX: basePosture.scale * pulseScale,
      scaleY: basePosture.scale * pulseScale,
      duration: 90,
      yoyo: true,
      ease: "Sine.easeInOut",
    });
  }

  private applyPosture(packet: DesktopPacket): void {
    if (!this.avatar || !this.spriteConfig) {
      return;
    }

    const posture = this.resolvePosture(packet);
    this.postureTween?.remove();
    this.postureTween = this.tweens.add({
      targets: this.avatar,
      x: 160 + posture.xOffset,
      y: 176 + posture.yOffset,
      scaleX: posture.scale,
      scaleY: posture.scale,
      angle: posture.angle,
      alpha: posture.alpha,
      duration: 260,
      ease: "Sine.easeOut",
    });
  }

  private resolvePosture(packet: DesktopPacket): {
    xOffset: number;
    yOffset: number;
    scale: number;
    angle: number;
    alpha: number;
  } {
    const baseScale = this.spriteConfig?.scale ?? 1;

    if (packet.phase === "replying") {
      if (packet.mood === "playful") {
        return { xOffset: 8, yOffset: -12, scale: baseScale * 1.08, angle: 6, alpha: 1 };
      }
      if (packet.mood === "soothing") {
        return { xOffset: -4, yOffset: -8, scale: baseScale * 1.04, angle: -4, alpha: 1 };
      }
      return { xOffset: 2, yOffset: -10, scale: baseScale * 1.06, angle: 2, alpha: 1 };
    }

    if (packet.phase === "listening") {
      return {
        xOffset: 0,
        yOffset: -4,
        scale: baseScale * 1.03,
        angle: packet.animation === "listen_nod" ? -5 : -2,
        alpha: 1,
      };
    }

    if (packet.phase === "settling") {
      return {
        xOffset: packet.mood === "playful" ? 4 : 0,
        yOffset: 6,
        scale: baseScale * 0.99,
        angle: packet.mood === "soothing" ? -2 : 0,
        alpha: 0.94,
      };
    }

    if (packet.mood === "playful") {
      return { xOffset: 6, yOffset: -2, scale: baseScale * 1.02, angle: 2, alpha: 0.98 };
    }
    if (packet.mood === "soothing") {
      return { xOffset: -2, yOffset: 8, scale: baseScale * 0.98, angle: -1, alpha: 0.92 };
    }
    return { xOffset: 0, yOffset: 2, scale: baseScale, angle: 0, alpha: 0.96 };
  }
}
