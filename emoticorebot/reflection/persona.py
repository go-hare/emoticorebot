"""Persona and user-model governance helpers."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

from loguru import logger

from emoticorebot.models.emotion_state import EmotionStateManager


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class GovernedWriteResult:
    applied: bool
    version: int | None = None
    conflict_detected: bool = False
    rollback_to_version: int | None = None
    snapshot_path: Path | None = None


class ManagedAnchorWriter:
    """Writes governed USER/SOUL anchor blocks into markdown files."""

    _GOVERNANCE_ROOT = ".governance/persona"
    _CONFIGS = {
        ("turn", "user_model"): {
            "filename": "USER.md",
            "marker_start": "<!-- TURN_REFLECTION_USER_START -->",
            "marker_end": "<!-- TURN_REFLECTION_USER_END -->",
            "intro": "以下条目沉淀当前轮高置信用户信息，由 `turn_reflection` 自动维护。",
            "section_title": "## 逐轮快写（自动维护）",
            "max_entries": 10,
        },
        ("turn", "persona"): {
            "filename": "SOUL.md",
            "marker_start": "<!-- TURN_REFLECTION_SOUL_START -->",
            "marker_end": "<!-- TURN_REFLECTION_SOUL_END -->",
            "intro": "以下条目沉淀当前轮高置信左脑风格修正，由 `turn_reflection` 自动维护。",
            "section_title": "## 逐轮快写（自动维护）",
            "max_entries": 10,
        },
        ("deep", "user_model"): {
            "filename": "USER.md",
            "marker_start": "<!-- DEEP_REFLECTION_USER_START -->",
            "marker_end": "<!-- DEEP_REFLECTION_USER_END -->",
            "intro": "以下条目沉淀用户的稳定画像，由 `deep_reflection` 自动维护。",
            "section_title": "## 深反思沉淀（自动维护）",
            "max_entries": None,
        },
        ("deep", "persona"): {
            "filename": "SOUL.md",
            "marker_start": "<!-- DEEP_REFLECTION_SOUL_START -->",
            "marker_end": "<!-- DEEP_REFLECTION_SOUL_END -->",
            "intro": "以下条目沉淀左脑的稳定风格与长期策略，由 `deep_reflection` 自动维护。",
            "section_title": "## 深反思沉淀（自动维护）",
            "max_entries": None,
        },
    }

    def __init__(self, workspace: Path) -> None:
        self._workspace = Path(workspace)

    def write(self, *, target: str, updates: Any, scope: str) -> GovernedWriteResult:
        normalized_updates = self.normalize_update_lines(updates)
        if not normalized_updates:
            return GovernedWriteResult(applied=False)

        config = dict(self._CONFIGS[(scope, target)])
        target_path = self._workspace / config["filename"]
        current = self._ensure_md_file(config["filename"])
        manifest = self._load_manifest(target=target, scope=scope)
        current_hash = self._content_hash(current)
        conflict_detected = bool(manifest.get("current_hash")) and manifest.get("current_hash") != current_hash

        existing_updates = self._extract_managed_updates(
            current,
            marker_start=config["marker_start"],
            marker_end=config["marker_end"],
        )
        merged_updates = self._merge_updates(existing_updates, normalized_updates)
        max_entries = config["max_entries"]
        if max_entries is not None and max_entries > 0:
            merged_updates = merged_updates[-max_entries:]
        block = self._render_managed_block(
            section_title=config["section_title"],
            marker_start=config["marker_start"],
            marker_end=config["marker_end"],
            intro=config["intro"],
            updates=merged_updates,
        )
        updated = self._replace_or_append_managed_block(
            current,
            marker_start=config["marker_start"],
            marker_end=config["marker_end"],
            block=block,
        )
        if updated == current and not conflict_detected:
            return GovernedWriteResult(applied=False)

        next_version = int(manifest.get("latest_version", 0) or 0) + 1
        if updated != current and not self._safe_write_text(target_path, updated):
            return GovernedWriteResult(applied=False, conflict_detected=conflict_detected)

        final_content = updated if updated != current else current
        snapshot_path = self._write_snapshot(
            target=target,
            scope=scope,
            version=next_version,
            content=final_content,
            filename=config["filename"],
        )
        self._append_history(
            target=target,
            scope=scope,
            record={
                "version": next_version,
                "action": "apply",
                "target": target,
                "scope": scope,
                "filename": config["filename"],
                "applied_at": _utc_now(),
                "conflict_detected": conflict_detected,
                "resolution": "accept_disk_then_merge",
                "source_hash": current_hash,
                "result_hash": self._content_hash(final_content),
                "updates": merged_updates,
                "snapshot_path": self._relative_path(snapshot_path),
            },
        )
        self._save_manifest(
            target=target,
            scope=scope,
            payload={
                "target": target,
                "scope": scope,
                "filename": config["filename"],
                "latest_version": next_version,
                "current_hash": self._content_hash(final_content),
                "current_snapshot": self._relative_path(snapshot_path),
                "updated_at": _utc_now(),
            },
        )
        return GovernedWriteResult(
            applied=True,
            version=next_version,
            conflict_detected=conflict_detected,
            snapshot_path=snapshot_path,
        )

    def rollback(self, *, target: str, scope: str, version: int | None = None) -> GovernedWriteResult:
        config = dict(self._CONFIGS[(scope, target)])
        manifest = self._load_manifest(target=target, scope=scope)
        latest_version = int(manifest.get("latest_version", 0) or 0)
        if latest_version <= 0:
            return GovernedWriteResult(applied=False)

        rollback_to = latest_version - 1 if version is None else int(version)
        if rollback_to <= 0:
            return GovernedWriteResult(applied=False)

        snapshot_path = self._snapshot_path(target=target, scope=scope, version=rollback_to, filename=config["filename"])
        if not snapshot_path.exists():
            return GovernedWriteResult(applied=False)

        target_path = self._workspace / config["filename"]
        current = self._ensure_md_file(config["filename"])
        current_hash = self._content_hash(current)
        conflict_detected = bool(manifest.get("current_hash")) and manifest.get("current_hash") != current_hash
        restored = snapshot_path.read_text(encoding="utf-8")
        if restored != current and not self._safe_write_text(target_path, restored):
            return GovernedWriteResult(applied=False, conflict_detected=conflict_detected)

        next_version = latest_version + 1
        new_snapshot_path = self._write_snapshot(
            target=target,
            scope=scope,
            version=next_version,
            content=restored,
            filename=config["filename"],
        )
        self._append_history(
            target=target,
            scope=scope,
            record={
                "version": next_version,
                "action": "rollback",
                "target": target,
                "scope": scope,
                "filename": config["filename"],
                "applied_at": _utc_now(),
                "conflict_detected": conflict_detected,
                "resolution": "restore_snapshot",
                "rollback_to_version": rollback_to,
                "source_hash": current_hash,
                "result_hash": self._content_hash(restored),
                "updates": self._extract_managed_updates(
                    restored,
                    marker_start=config["marker_start"],
                    marker_end=config["marker_end"],
                ),
                "snapshot_path": self._relative_path(new_snapshot_path),
            },
        )
        self._save_manifest(
            target=target,
            scope=scope,
            payload={
                "target": target,
                "scope": scope,
                "filename": config["filename"],
                "latest_version": next_version,
                "current_hash": self._content_hash(restored),
                "current_snapshot": self._relative_path(new_snapshot_path),
                "updated_at": _utc_now(),
            },
        )
        return GovernedWriteResult(
            applied=True,
            version=next_version,
            conflict_detected=conflict_detected,
            rollback_to_version=rollback_to,
            snapshot_path=new_snapshot_path,
        )

    def current_updates(self, *, target: str, scope: str) -> list[str]:
        config = dict(self._CONFIGS[(scope, target)])
        current = self._ensure_md_file(config["filename"])
        return self._extract_managed_updates(
            current,
            marker_start=config["marker_start"],
            marker_end=config["marker_end"],
        )

    @staticmethod
    def normalize_update_lines(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        items: list[str] = []
        for item in value:
            text = str(item or "").strip()
            text = re.sub(r"^[-*•]\s*", "", text)
            text = re.sub(r"^\d+[.)、]\s*", "", text)
            text = " ".join(text.split())
            if text and text not in items:
                items.append(text)
        return items[:8]

    def _ensure_md_file(self, filename: str) -> str:
        target = self._workspace / filename
        if target.exists():
            return target.read_text(encoding="utf-8")
        template = self._load_template(filename)
        if template:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(template, encoding="utf-8")
            return template
        return ""

    @staticmethod
    def _load_template(filename: str) -> str:
        try:
            return (files("emoticorebot") / "templates" / filename).read_text(encoding="utf-8")
        except Exception:
            return ""

    @staticmethod
    def _extract_managed_updates(
        text: str,
        *,
        marker_start: str,
        marker_end: str,
    ) -> list[str]:
        pattern = re.compile(rf"{re.escape(marker_start)}([\s\S]*?){re.escape(marker_end)}")
        match = pattern.search(text or "")
        if not match:
            return []
        items: list[str] = []
        for line in match.group(1).splitlines():
            stripped = line.strip()
            if not stripped.startswith("- "):
                continue
            value = stripped[2:].strip()
            if value and value not in items:
                items.append(value)
        return items

    @staticmethod
    def _merge_updates(existing: list[str], incoming: list[str]) -> list[str]:
        merged: list[str] = []
        for item in [*existing, *incoming]:
            value = str(item or "").strip()
            if value and value not in merged:
                merged.append(value)
        return merged

    @staticmethod
    def _render_managed_block(
        *,
        section_title: str,
        marker_start: str,
        marker_end: str,
        intro: str,
        updates: list[str],
    ) -> str:
        lines = [
            marker_start,
            section_title,
            f"> {intro}",
            "",
            *(f"- {item}" for item in updates),
            marker_end,
        ]
        return "\n".join(lines).strip() + "\n"

    @staticmethod
    def _replace_or_append_managed_block(
        current: str,
        *,
        marker_start: str,
        marker_end: str,
        block: str,
    ) -> str:
        pattern = re.compile(rf"{re.escape(marker_start)}[\s\S]*?{re.escape(marker_end)}")
        stripped_block = block.strip()
        if pattern.search(current or ""):
            updated = pattern.sub(stripped_block, current, count=1)
        else:
            base = (current or "").rstrip()
            updated = f"{base}\n\n{stripped_block}" if base else stripped_block
        return updated.rstrip() + "\n"

    def _governance_dir(self, *, target: str, scope: str) -> Path:
        return self._workspace / self._GOVERNANCE_ROOT / target / scope

    def _manifest_path(self, *, target: str, scope: str) -> Path:
        return self._governance_dir(target=target, scope=scope) / "manifest.json"

    def _history_path(self, *, target: str, scope: str) -> Path:
        return self._governance_dir(target=target, scope=scope) / "history.jsonl"

    def _snapshot_path(self, *, target: str, scope: str, version: int, filename: str) -> Path:
        suffix = Path(filename).suffix or ".md"
        return self._governance_dir(target=target, scope=scope) / "versions" / f"v{version:06d}{suffix}"

    def _load_manifest(self, *, target: str, scope: str) -> dict[str, Any]:
        path = self._manifest_path(target=target, scope=scope)
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("ManagedAnchorWriter failed to load manifest {}: {}", path.name, exc)
            return {}

    def _save_manifest(self, *, target: str, scope: str, payload: dict[str, Any]) -> None:
        path = self._manifest_path(target=target, scope=scope)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _append_history(self, *, target: str, scope: str, record: dict[str, Any]) -> None:
        path = self._history_path(target=target, scope=scope)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _write_snapshot(self, *, target: str, scope: str, version: int, content: str, filename: str) -> Path:
        path = self._snapshot_path(target=target, scope=scope, version=version, filename=filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def _relative_path(self, path: Path) -> str:
        return path.relative_to(self._workspace).as_posix()

    @staticmethod
    def _content_hash(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _safe_write_text(target: Path, content: str) -> bool:
        backup = target.with_suffix(target.suffix + ".bak")
        temp = target.with_suffix(target.suffix + ".tmp")
        previous = target.read_text(encoding="utf-8") if target.exists() else None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if previous is not None:
                backup.write_text(previous, encoding="utf-8")
            temp.write_text(content, encoding="utf-8")
            temp.replace(target)
            return True
        except Exception as exc:
            logger.warning("ManagedAnchorWriter safe write failed for {}: {}", target.name, exc)
            try:
                if previous is not None:
                    target.write_text(previous, encoding="utf-8")
            except Exception:
                pass
            return False
        finally:
            if temp.exists():
                try:
                    temp.unlink()
                except Exception:
                    pass


class PersonaManager:
    """Applies managed USER/SOUL updates and reflection-selected state changes."""

    def __init__(
        self,
        *,
        emotion_manager: EmotionStateManager,
        anchor_writer: ManagedAnchorWriter,
    ) -> None:
        self._emotion_mgr = emotion_manager
        self._writer = anchor_writer

    def apply_turn_reflection(
        self,
        turn_reflection: dict[str, Any] | None,
    ) -> tuple[bool, bool, bool, dict[str, Any] | None]:
        user_result, soul_result, updated_state, state_snapshot = self.apply_turn_reflection_results(turn_reflection)
        return user_result.applied, soul_result.applied, updated_state, state_snapshot

    def apply_turn_reflection_results(
        self,
        turn_reflection: dict[str, Any] | None,
    ) -> tuple[GovernedWriteResult, GovernedWriteResult, bool, dict[str, Any] | None]:
        payload = turn_reflection if isinstance(turn_reflection, dict) else {}
        updated_user = self.apply_updates_result("user_model", payload.get("user_updates"), scope="turn")
        updated_soul = self.apply_updates_result("persona", payload.get("soul_updates"), scope="turn")
        updated_state, state_snapshot = self.apply_state_update(payload.get("state_update"))
        return updated_user, updated_soul, updated_state, state_snapshot

    def apply_updates(self, target: str, updates: Any, *, scope: str) -> bool:
        return self.apply_updates_result(target, updates, scope=scope).applied

    def apply_updates_result(self, target: str, updates: Any, *, scope: str) -> GovernedWriteResult:
        return self._writer.write(target=target, updates=updates, scope=scope)

    def rollback_updates(self, target: str, *, scope: str, version: int | None = None) -> GovernedWriteResult:
        return self._writer.rollback(target=target, scope=scope, version=version)

    def current_updates(self, target: str, *, scope: str) -> list[str]:
        return self._writer.current_updates(target=target, scope=scope)

    def apply_state_update(self, payload: Any) -> tuple[bool, dict[str, Any] | None]:
        update = payload if isinstance(payload, dict) else {}
        if not bool(update.get("should_apply", False)):
            return False, None
        pad_state = self.normalize_state_value_map(
            update.get("pad_state"),
            allowed=("pleasure", "arousal", "dominance"),
            minimum=-1.0,
            maximum=1.0,
        )
        drives_state = self.normalize_state_value_map(
            update.get("drives_state"),
            allowed=("social", "energy"),
            minimum=0.0,
            maximum=100.0,
        )
        snapshot = self._emotion_mgr.apply_reflection_state_update(
            pad_state=pad_state,
            drives_state=drives_state,
        )
        return True, snapshot

    @staticmethod
    def normalize_update_lines(value: Any) -> list[str]:
        return ManagedAnchorWriter.normalize_update_lines(value)

    @staticmethod
    def normalize_state_value_map(
        payload: Any,
        *,
        allowed: tuple[str, ...],
        minimum: float,
        maximum: float,
    ) -> dict[str, float]:
        if not isinstance(payload, dict):
            return {}
        normalized: dict[str, float] = {}
        for key in allowed:
            if key not in payload:
                continue
            try:
                value = float(payload.get(key, 0.0) or 0.0)
            except Exception:
                continue
            value = max(minimum, min(maximum, value))
            precision = 3 if key in {"pleasure", "arousal", "dominance"} else 2
            normalized[key] = round(value, precision)
        return normalized

__all__ = ["GovernedWriteResult", "ManagedAnchorWriter", "PersonaManager"]

