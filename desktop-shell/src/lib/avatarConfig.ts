import canekoSprite from "../assets/avatar/shimeji_Caneko.png";
import nekojapanSprite from "../assets/avatar/shimeji_nekojapan.png";
import koreaCatSprite from "../assets/avatar/shimeji_skoreacat.png";
import turkatSprite from "../assets/avatar/shimeji_Turkat.png";
import type { ISpriteConfig } from "../types/ISpriteConfig";
import type { DesktopPacket } from "./protocol";

export interface AvatarPreset {
  id: string;
  label: string;
  description: string;
  spriteConfig: ISpriteConfig;
}

const desktopPetScale = 0.9;

type LineStateSpec = {
  spriteLine: number;
  frameMax: number;
};

function buildStateRanges(highestFrameMax: number, specs: Record<string, LineStateSpec>): ISpriteConfig["states"] {
  return Object.fromEntries(
    Object.entries(specs).map(([state, spec]) => {
      const start = (spec.spriteLine - 1) * highestFrameMax;
      const end = start + spec.frameMax - 1;
      return [state, { start, end }];
    }),
  );
}

function resolveShimejiCatState(packet: DesktopPacket): string[] {
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

function createShimejiCatConfig(
  name: string,
  imageSrc: string,
  highestFrameMax: number,
  specs: Record<string, LineStateSpec>,
): ISpriteConfig {
  return {
    name,
    imageSrc,
    frameSize: 128,
    sheetColumns: highestFrameMax,
    scale: desktopPetScale,
    resolveStateCandidates: (packet: DesktopPacket) => resolveShimejiCatState(packet),
    states: buildStateRanges(highestFrameMax, specs),
  };
}

export const avatarPresets: AvatarPreset[] = [
  {
    id: "korea-cat",
    label: "韩国猫",
    description: "现在这只默认陪伴猫，动作和桌宠层完全同步。",
    spriteConfig: createShimejiCatConfig("shimeji-skoreacat", koreaCatSprite, 8, {
      stand: { spriteLine: 1, frameMax: 1 },
      walk: { spriteLine: 2, frameMax: 4 },
      sit: { spriteLine: 3, frameMax: 1 },
      greet: { spriteLine: 4, frameMax: 8 },
      jump: { spriteLine: 5, frameMax: 1 },
      fall: { spriteLine: 6, frameMax: 3 },
      drag: { spriteLine: 7, frameMax: 1 },
      crawl: { spriteLine: 8, frameMax: 8 },
      climb: { spriteLine: 9, frameMax: 8 },
    }),
  },
  {
    id: "caneko",
    label: "加拿大猫",
    description: "同系 shimeji 预设，切过去就能直接接管主框和桌面层。",
    spriteConfig: createShimejiCatConfig("shimeji-caneko", canekoSprite, 8, {
      stand: { spriteLine: 1, frameMax: 1 },
      walk: { spriteLine: 2, frameMax: 4 },
      sit: { spriteLine: 3, frameMax: 1 },
      greet: { spriteLine: 4, frameMax: 8 },
      jump: { spriteLine: 5, frameMax: 1 },
      fall: { spriteLine: 6, frameMax: 3 },
      drag: { spriteLine: 7, frameMax: 1 },
      crawl: { spriteLine: 8, frameMax: 8 },
      climb: { spriteLine: 9, frameMax: 8 },
    }),
  },
  {
    id: "nekojapan",
    label: "日本猫",
    description: "保留同一套交互逻辑，只更换形象，不改行为。",
    spriteConfig: createShimejiCatConfig("shimeji-nekojapan", nekojapanSprite, 9, {
      stand: { spriteLine: 1, frameMax: 9 },
      walk: { spriteLine: 2, frameMax: 4 },
      sit: { spriteLine: 3, frameMax: 5 },
      greet: { spriteLine: 4, frameMax: 8 },
      jump: { spriteLine: 5, frameMax: 1 },
      fall: { spriteLine: 6, frameMax: 3 },
      drag: { spriteLine: 7, frameMax: 1 },
      crawl: { spriteLine: 8, frameMax: 8 },
      climb: { spriteLine: 9, frameMax: 8 },
    }),
  },
  {
    id: "turkat",
    label: "土耳其猫",
    description: "同规格素材，方便后面继续扩自己的 pet 列表。",
    spriteConfig: createShimejiCatConfig("shimeji-turkat", turkatSprite, 8, {
      stand: { spriteLine: 1, frameMax: 1 },
      walk: { spriteLine: 2, frameMax: 4 },
      sit: { spriteLine: 3, frameMax: 1 },
      greet: { spriteLine: 4, frameMax: 8 },
      jump: { spriteLine: 5, frameMax: 1 },
      fall: { spriteLine: 6, frameMax: 3 },
      drag: { spriteLine: 7, frameMax: 1 },
      crawl: { spriteLine: 8, frameMax: 8 },
      climb: { spriteLine: 9, frameMax: 8 },
    }),
  },
];

export const defaultAvatarPresetId = avatarPresets[0].id;

export function getAvatarPresetById(id?: string | null): AvatarPreset | null {
  if (!id) {
    return null;
  }
  return avatarPresets.find((preset) => preset.id === id) ?? null;
}

export const avatarSpriteConfig: ISpriteConfig = avatarPresets[0].spriteConfig;
