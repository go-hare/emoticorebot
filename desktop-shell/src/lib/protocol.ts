export interface DesktopPacket {
  thread_id: string;
  phase: string;
  avatar_phase: string;
  animation: string;
  bubble_mode: string;
  bubble_visible: boolean;
  hold_ms: number;
  companion_mode: string;
  mood: string;
  presence: string;
  body_state: string;
  breathing_hint: string;
  metadata: Record<string, unknown>;
}

export interface AffectStateSnapshot {
  current_pad?: {
    pleasure?: number;
    arousal?: number;
    dominance?: number;
  };
  vitality?: number;
  pressure?: number;
  turn_count?: number;
  updated_at?: string;
}

export interface ReadyPayload {
  default_thread_id: string;
  affect_state_path: string;
}

export type ServerEvent =
  | { type: "ready"; payload: ReadyPayload }
  | { type: "surface_state"; payload: DesktopPacket }
  | { type: "reply_chunk"; payload: { thread_id: string; chunk: string } }
  | { type: "reply_done"; payload: { thread_id: string; text: string } }
  | { type: "turn_error"; payload: { thread_id: string; error: string } }
  | { type: "affect_state"; payload: AffectStateSnapshot }
  | { type: "error"; payload: { message: string } };

export interface UserInputPayload {
  text: string;
  thread_id?: string;
  user_id?: string;
}

export const DEFAULT_PACKET: DesktopPacket = {
  thread_id: "desktop:main",
  phase: "idle",
  avatar_phase: "idle",
  animation: "idle_breathe_soft",
  bubble_mode: "hidden",
  bubble_visible: false,
  hold_ms: 0,
  companion_mode: "quiet_company",
  mood: "calm",
  presence: "beside",
  body_state: "resting_beside",
  breathing_hint: "soft_slow",
  metadata: {},
};
