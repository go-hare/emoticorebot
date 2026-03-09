"""Orchestration graph definition and execution helpers."""

from __future__ import annotations

from pathlib import Path

from langgraph.graph import END, StateGraph
from loguru import logger

from emoticorebot.core.nodes.executor_node import executor_node
from emoticorebot.core.nodes.main_brain_node import main_brain_node
from emoticorebot.core.nodes.memory_node import memory_node
from emoticorebot.core.router import OrchestrationRouter
from emoticorebot.core.state import OrchestrationState, create_initial_state


def create_orchestration_agent(workspace: Path, runtime):
    """Compile the orchestration graph once per runtime."""
    router = OrchestrationRouter(max_executor_attempts=3)
    graph = StateGraph(OrchestrationState)

    async def _main_brain(state: OrchestrationState) -> OrchestrationState:
        return await main_brain_node(state, runtime)

    async def _executor(state: OrchestrationState) -> OrchestrationState:
        return await executor_node(state, runtime)

    async def _memory(state: OrchestrationState) -> OrchestrationState:
        return await memory_node(state, runtime)

    graph.add_node("main_brain", _main_brain)
    graph.add_node("executor", _executor)
    graph.add_node("memory", _memory)
    graph.set_entry_point("main_brain")

    def route_next(state: OrchestrationState) -> str:
        return router.route_next(state)

    graph.add_conditional_edges(
        "main_brain",
        route_next,
        {"main_brain": "main_brain", "executor": "executor", "memory": "memory"},
    )
    graph.add_conditional_edges(
        "executor",
        route_next,
        {"main_brain": "main_brain", "executor": "executor", "memory": "memory"},
    )
    graph.add_edge("memory", END)
    return graph.compile()


async def run_orchestration_agent(
    user_input: str,
    workspace: Path,
    runtime,
    dialogue_history: list[dict] | None = None,
    internal_history: list[dict] | None = None,
    metadata: dict | None = None,
    channel: str = "",
    chat_id: str = "",
    session_id: str = "",
    on_progress=None,
    agent=None,
) -> tuple[str, dict]:
    """Run one orchestration turn and return output plus final state."""
    if agent is None:
        agent = create_orchestration_agent(workspace, runtime=runtime)

    initial_state = create_initial_state(
        user_input=user_input,
        workspace=workspace,
        dialogue_history=dialogue_history or [],
        internal_history=internal_history or [],
        channel=channel,
        chat_id=chat_id,
        session_id=session_id,
    )

    if on_progress:
        initial_state["on_progress"] = on_progress
    if metadata:
        initial_state["metadata"] = metadata

    try:
        result = await agent.ainvoke(initial_state)
        return result.get("output", ""), result
    except Exception as exc:
        logger.error("Orchestration agent error: {}", exc)
        return f"Sorry, something went wrong: {exc}", initial_state


__all__ = ["create_orchestration_agent", "run_orchestration_agent"]
