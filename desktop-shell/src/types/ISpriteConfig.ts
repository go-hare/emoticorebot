export interface SpriteStateRange {
  start: number;
  end: number;
}

export interface ISpriteConfig {
  name: string;
  imageSrc: string;
  frameSize: number;
  scale?: number;
  states: Record<string, SpriteStateRange>;
}
