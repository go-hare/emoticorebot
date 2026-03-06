from __future__ import annotations

from pathlib import Path

from emoticorebot.memory.affective_store import AffectiveStore
from emoticorebot.memory.relational_store import RelationalStore
from emoticorebot.memory.semantic_store import SemanticStore


class MemoryFacade:
    """Unified access to semantic/relational/affective stores."""

    def __init__(self, workspace: Path):
        self.semantic = SemanticStore(workspace)
        self.relational = RelationalStore(workspace)
        self.affective = AffectiveStore(workspace)