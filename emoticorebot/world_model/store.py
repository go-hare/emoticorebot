"""Persistence for per-session world-model snapshots."""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from emoticorebot.utils.helpers import ensure_dir, safe_filename
from emoticorebot.world_model.schema import WorldModel


class WorldModelStore:
    def __init__(self, workspace: Path):
        self.root = ensure_dir(workspace / "session")

    @staticmethod
    def safe_session_id(session_id: str) -> str:
        return safe_filename(str(session_id or "").replace(":", "_"))

    def session_dir(self, session_id: str) -> Path:
        return self.root / self.safe_session_id(session_id)

    def ensure_session_dir(self, session_id: str) -> Path:
        return ensure_dir(self.session_dir(session_id))

    def path_for(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "world_model.json"

    def load(self, session_id: str) -> WorldModel:
        path = self.path_for(session_id)
        if not path.exists():
            return WorldModel(session_id=str(session_id or "").strip())
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("world model payload must be an object")
            return WorldModel.from_dict(data, session_id=session_id)
        except Exception as exc:
            logger.warning("Failed to load world model for {}: {}", session_id, exc)
            return WorldModel(session_id=str(session_id or "").strip())

    def save(self, model: WorldModel) -> None:
        session_id = str(model.session_id or "").strip()
        if not session_id:
            raise ValueError("world model save requires session_id")
        self.ensure_session_dir(session_id)
        path = self.path_for(session_id)
        path.write_text(
            json.dumps(model.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def clear(self, session_id: str) -> None:
        path = self.path_for(session_id)
        if path.exists():
            path.unlink()


__all__ = ["WorldModelStore"]
