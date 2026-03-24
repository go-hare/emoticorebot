import type { AffectStateSnapshot } from "./protocol";

export async function readAffectStateSnapshot(
  affectStatePath: string,
): Promise<AffectStateSnapshot | null> {
  if (!affectStatePath) {
    return null;
  }

  if (!(window as { __TAURI__?: unknown }).__TAURI__) {
    return null;
  }

  try {
    const { readTextFile } = await import("@tauri-apps/api/fs");
    const raw = await readTextFile(affectStatePath);
    return JSON.parse(raw) as AffectStateSnapshot;
  } catch (_error) {
    return null;
  }
}
