"""Current state file access."""

from __future__ import annotations

from pathlib import Path

from emoticorebot.state.io import read_text, write_text


class CurrentStateStore:
    """Read and write current_state.md."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.path = self.workspace / "current_state.md"

    def ensure(self, content: str) -> None:
        if self.path.exists():
            return
        write_text(self.path, content)

    def read(self) -> str:
        return read_text(self.path)

    def write(self, content: str) -> None:
        write_text(self.path, content)
