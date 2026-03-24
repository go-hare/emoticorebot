import { emit, listen } from "@tauri-apps/api/event";
import { useEffect, useState } from "react";
import {
  avatarPresets,
  defaultAvatarPresetId,
  getAvatarPresetById,
  type AvatarPreset,
} from "./avatarConfig";

const AVATAR_STORAGE_KEY = "emoticore.desktop.avatarPresetId";
const AVATAR_EVENT_NAME = "avatar-preset-changed";

function normalizeAvatarPresetId(id?: string | null): string {
  return getAvatarPresetById(id)?.id ?? defaultAvatarPresetId;
}

function readStoredAvatarPresetId(): string {
  try {
    return normalizeAvatarPresetId(window.localStorage.getItem(AVATAR_STORAGE_KEY));
  } catch {
    return defaultAvatarPresetId;
  }
}

async function broadcastAvatarPresetId(id: string): Promise<void> {
  const normalizedId = normalizeAvatarPresetId(id);
  try {
    window.localStorage.setItem(AVATAR_STORAGE_KEY, normalizedId);
  } catch {
    // Ignore localStorage failures and still notify other windows.
  }
  await emit(AVATAR_EVENT_NAME, { avatarId: normalizedId });
}

async function subscribeToAvatarPresetChanges(
  callback: (avatarId: string) => void,
): Promise<() => void> {
  const handleStorage = (event: StorageEvent) => {
    if (event.key !== AVATAR_STORAGE_KEY) {
      return;
    }
    callback(normalizeAvatarPresetId(event.newValue));
  };

  window.addEventListener("storage", handleStorage);

  const unlisten = await listen<{ avatarId?: string }>(AVATAR_EVENT_NAME, (event) => {
    callback(normalizeAvatarPresetId(event.payload?.avatarId));
  });

  return () => {
    window.removeEventListener("storage", handleStorage);
    void unlisten();
  };
}

export function useAvatarPreset(): {
  avatarPresets: AvatarPreset[];
  selectedPreset: AvatarPreset;
  selectedPresetId: string;
  setSelectedPresetId: (avatarId: string) => void;
} {
  const [selectedPresetId, setSelectedPresetIdState] = useState<string>(() => readStoredAvatarPresetId());

  useEffect(() => {
    let disposed = false;
    let cleanup: (() => void) | undefined;

    void subscribeToAvatarPresetChanges((nextId) => {
      if (!disposed) {
        setSelectedPresetIdState(nextId);
      }
    }).then((unsubscribe) => {
      if (disposed) {
        unsubscribe();
        return;
      }
      cleanup = unsubscribe;
    });

    return () => {
      disposed = true;
      cleanup?.();
    };
  }, []);

  const selectedPreset = getAvatarPresetById(selectedPresetId) ?? avatarPresets[0];

  return {
    avatarPresets,
    selectedPreset,
    selectedPresetId: selectedPreset.id,
    setSelectedPresetId: (avatarId: string) => {
      const normalizedId = normalizeAvatarPresetId(avatarId);
      setSelectedPresetIdState(normalizedId);
      void broadcastAvatarPresetId(normalizedId);
    },
  };
}
