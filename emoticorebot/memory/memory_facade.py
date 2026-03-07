from __future__ import annotations

from pathlib import Path

from emoticorebot.memory.stateful_stores import AffectiveStore, RelationalStore, SemanticStore
from emoticorebot.memory.structured_stores import EpisodicStore, EventStore, PlanStore, ReflectiveStore


class MemoryFacade:
    """Unified access to structured and stateful memory stores."""

    def __init__(self, workspace: Path):
        self.events = EventStore(workspace)
        self.episodic = EpisodicStore(workspace)
        self.semantic = SemanticStore(workspace)
        self.relational = RelationalStore(workspace)
        self.affective = AffectiveStore(workspace)
        self.reflective = ReflectiveStore(workspace)
        self.plans = PlanStore(workspace)
