"""World model storage."""

from __future__ import annotations

from pathlib import Path
from threading import Lock

from emoticorebot.state.io import read_json, write_json
from emoticorebot.state.schemas import WorldModel, WorldModelUpdate, now_iso


class WorldModelStore:
    """Owns state/world_model.json."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.path = self.workspace / "state" / "world_model.json"
        self.write_lock = Lock()

    def load(self) -> WorldModel:
        payload = read_json(self.path, WorldModel().model_dump())
        return WorldModel.model_validate(payload)

    def save(self, model: WorldModel) -> WorldModel:
        normalized = WorldModel.model_validate(model.model_dump())
        normalized.updated_at = now_iso()
        with self.write_lock:
            write_json(self.path, normalized.model_dump())
        return normalized

    def update(self, update: WorldModelUpdate) -> WorldModel:
        current = self.load()
        payload = current.model_dump()
        patch = update.model_dump(exclude_none=True)
        payload.update(patch)
        payload["open_threads"] = self.unique_threads(payload.get("open_threads", []))
        payload["updated_at"] = now_iso()
        model = WorldModel.model_validate(payload)
        with self.write_lock:
            write_json(self.path, model.model_dump())
        return model

    def replace(self, payload: dict[str, object]) -> WorldModel:
        model = WorldModel.model_validate(payload)
        return self.save(model)

    def render_block(self) -> str:
        model = self.load()
        lines = [
            f"- focus: {model.focus or '(empty)'}",
            f"- mode: {model.mode}",
            f"- recent_intent: {model.recent_intent or '(empty)'}",
            f"- last_tool_result: {model.last_tool_result or '(empty)'}",
        ]
        if model.open_threads:
            lines.append("- open_threads:")
            lines.extend(f"  - {item}" for item in model.open_threads)
        else:
            lines.append("- open_threads: []")
        return "\n".join(lines)

    def unique_threads(self, values: list[str]) -> list[str]:
        rows: list[str] = []
        for value in values:
            text = str(value or "").strip()
            if text and text not in rows:
                rows.append(text)
        return rows

