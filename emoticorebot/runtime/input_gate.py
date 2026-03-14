"""Runtime policy for tasks waiting on user input."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class InputGate:
    """Ensure only one task is actively awaiting user input at a time."""

    waiting_task_id: str | None = None
    blocked_task_ids: list[str] = field(default_factory=list)

    def activate_or_block(self, task_id: str) -> bool:
        wanted = str(task_id or "").strip()
        if not wanted:
            return False
        if self.waiting_task_id and self.waiting_task_id != wanted:
            if wanted not in self.blocked_task_ids:
                self.blocked_task_ids.append(wanted)
            return False
        self.blocked_task_ids = [item for item in self.blocked_task_ids if item != wanted]
        self.waiting_task_id = wanted
        return True

    def release(self, task_id: str) -> str | None:
        wanted = str(task_id or "").strip()
        if not wanted:
            return None
        if self.waiting_task_id == wanted:
            self.waiting_task_id = None
            while self.blocked_task_ids:
                promoted = self.blocked_task_ids.pop(0)
                if promoted:
                    self.waiting_task_id = promoted
                    return promoted
            return None
        self.remove(wanted)
        return None

    def remove(self, task_id: str) -> None:
        wanted = str(task_id or "").strip()
        if not wanted:
            return
        if self.waiting_task_id == wanted:
            self.waiting_task_id = None
        self.blocked_task_ids = [item for item in self.blocked_task_ids if item != wanted]

    def current_waiting(self) -> str | None:
        return self.waiting_task_id

    def current_blocked(self) -> str | None:
        return self.blocked_task_ids[0] if self.blocked_task_ids else None


__all__ = ["InputGate"]
