"""Memory node for turn finalization."""

from emoticorebot.core.state import TurnState


async def memory_node(state: TurnState, runtime) -> TurnState:
    """Persist memory and state updates for the current turn."""
    await runtime.write_memory(state)
    return state
