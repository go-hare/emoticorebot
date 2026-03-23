import Phaser from "phaser";
import type { ISpriteConfig } from "../types/ISpriteConfig";
import { DEFAULT_PACKET, type DesktopPacket } from "../lib/protocol";

export default class SurfaceAvatarScene extends Phaser.Scene {
  private spriteConfig: ISpriteConfig | null = null;
  private avatar: Phaser.GameObjects.Sprite | null = null;
  private packet: DesktopPacket = DEFAULT_PACKET;

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
        frameRate: 8,
        repeat: -1,
      });
    }

    this.avatar = this.add.sprite(160, 176, this.spriteConfig.name);
    this.avatar.setScale(this.spriteConfig.scale ?? 1);
    this.applyPacket(this.packet);
  }

  shutdown(): void {
    this.game.events.off("surface:update", this.applyPacket, this);
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
  }

  private resolveAnimation(packet: DesktopPacket): string | null {
    if (!this.spriteConfig) {
      return null;
    }

    const candidates = [
      packet.animation,
      packet.avatar_phase,
      packet.phase,
      "idle",
      "idle_breathe_soft",
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
}
