import type { DesktopPacket } from "../lib/protocol";

export interface SpriteStateRange {
  start: number;
  end: number;
}

export interface ISpriteConfig {
  name: string;
  imageSrc: string;
  frameSize: number;
  sheetColumns?: number;
  scale?: number;
  states: Record<string, SpriteStateRange>;
  frameRates?: Record<string, number>;
  resolveStateCandidates?: (packet: DesktopPacket) => string[];
}
