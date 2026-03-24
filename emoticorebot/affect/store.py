"""Filesystem store for the shared affect state."""

from __future__ import annotations

import json
from pathlib import Path

from emoticorebot.affect.models import AffectState
from emoticorebot.utils.helpers import ensure_dir


class AffectStateStore:
    """Persist affect state under workspace/memony/affect_state.json."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.path = self.workspace / "memony" / "affect_state.json"

    def ensure(self) -> AffectState:
        state = self.load()
        if not self.path.exists():
            self.save(state)
        return state

    def load(self) -> AffectState:
        if not self.path.exists():
            return AffectState.default()

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return AffectState.default()
        return AffectState.from_dict(payload)

    def save(self, state: AffectState) -> None:
        ensure_dir(self.path.parent)
        self.path.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
