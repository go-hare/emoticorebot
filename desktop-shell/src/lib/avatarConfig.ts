import type { ISpriteConfig } from "../types/ISpriteConfig";
import type { DesktopPacket } from "./protocol";
import koreaCatSprite from "../assets/avatar/shimeji_skoreacat.png";

function resolveKoreaCatState(packet: DesktopPacket): string[] {
  const mood = packet.mood;
  const breathing = packet.breathing_hint;

  if (packet.phase === "replying") {
    if (mood === "playful") {
      return ["walk", "greet", "stand"];
    }
    if (mood === "bright") {
      return ["greet", "walk", "stand"];
    }
    if (mood === "soothing") {
      return ["sit", "greet", "stand"];
    }
    if (mood === "steady") {
      return ["stand", "greet", "sit"];
    }
    return ["greet", "stand", "sit"];
  }

  if (packet.phase === "listening") {
    if (mood === "playful") {
      return ["walk", "stand", "greet"];
    }
    if (mood === "bright") {
      return ["greet", "stand", "walk"];
    }
    if (mood === "soothing") {
      return ["sit", "stand", "greet"];
    }
    return ["stand", "sit"];
  }

  if (packet.phase === "settling") {
    if (mood === "playful") {
      return ["walk", "stand", "sit"];
    }
    if (mood === "bright" || mood === "steady") {
      return ["stand", "sit"];
    }
    return ["sit", "stand"];
  }

  if (breathing === "steady_even") {
    return ["stand", "sit"];
  }
  if (mood === "playful") {
    return ["walk", "stand", "sit"];
  }
  if (mood === "bright" || mood === "steady") {
    return ["stand", "sit"];
  }
  return ["sit", "stand"];
}

export const avatarSpriteConfig: ISpriteConfig = {
  name: "shimeji-skoreacat",
  imageSrc: koreaCatSprite,
  frameSize: 128,
  scale: 1.05,
  frameRates: {
    walk: 7,
    greet: 10,
    crawl: 8,
    climb: 8,
  },
  resolveStateCandidates: (packet: DesktopPacket) => resolveKoreaCatState(packet),
  states: {
    stand: { start: 0, end: 0 },
    walk: { start: 8, end: 11 },
    sit: { start: 16, end: 16 },
    greet: { start: 24, end: 31 },
    jump: { start: 32, end: 32 },
    fall: { start: 40, end: 42 },
    drag: { start: 48, end: 48 },
    crawl: { start: 56, end: 63 },
    climb: { start: 64, end: 71 },
  },
};
