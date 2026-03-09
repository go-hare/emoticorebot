"""Memory node for orchestration finalization."""

from emoticorebot.core.state import OrchestrationState


async def memory_node(state: OrchestrationState, runtime) -> OrchestrationState:
    """Persist memory and state updates for the current turn."""
    await runtime.write_memory(state)
    return state
