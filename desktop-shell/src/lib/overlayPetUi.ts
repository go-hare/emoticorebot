export const OVERLAY_PET_UI_EVENT = "overlay:pet-ui-state";
export const OVERLAY_PET_HOVER_EVENT = "overlay:pet-hover-state";
export const OVERLAY_PET_ACTIVATE_EVENT = "overlay:pet-activate";
export const OVERLAY_INTERACTION_LOCK_EVENT = "overlay:interaction-lock";

export interface OverlayPetUiState {
  anchorX: number;
  petTop: number;
  petBottom: number;
  petLeft: number;
  petRight: number;
  petWidth: number;
  petHeight: number;
  facingLeft: boolean;
}

export function dispatchOverlayPetUiState(detail: OverlayPetUiState): void {
  window.dispatchEvent(new CustomEvent<OverlayPetUiState>(OVERLAY_PET_UI_EVENT, { detail }));
}

export function dispatchOverlayPetHoverState(hovered: boolean): void {
  window.dispatchEvent(new CustomEvent<boolean>(OVERLAY_PET_HOVER_EVENT, { detail: hovered }));
}

export function dispatchOverlayPetActivate(): void {
  window.dispatchEvent(new CustomEvent(OVERLAY_PET_ACTIVATE_EVENT));
}

export function dispatchOverlayInteractionLockState(locked: boolean): void {
  window.dispatchEvent(new CustomEvent<boolean>(OVERLAY_INTERACTION_LOCK_EVENT, { detail: locked }));
}
