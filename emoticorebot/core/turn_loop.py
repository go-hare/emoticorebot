"""Explicit turn scheduler for the main_brain -> executor loop."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from emoticorebot.core.nodes.executor_node import executor_node
from emoticorebot.core.nodes.main_brain_node import main_brain_node
from emoticorebot.core.state import TurnState, create_turn_state

MAX_TURN_STEPS = 8


def _executor_request_ready(state: TurnState) -> bool:
    executor = state.get("executor")
    if executor is None:
        return False
    request = str(getattr(executor, "request", "") or "").strip()
    control_state = str(getattr(executor, "control_state", "") or "").strip()
    return bool(request or state.get("metadata", {}).get("execution")) and control_state == "running"


async def run_turn_loop(
    user_input: str,
    workspace: Path,
    runtime,
    dialogue_history: list[dict[str, Any]] | None = None,
    internal_history: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
    channel: str = "",
    chat_id: str = "",
    session_id: str = "",
    media: list[str] | None = None,
    on_progress=None,
) -> tuple[str, TurnState]:
    """Run one explicit main_brain -> executor scheduling loop."""
    state = create_turn_state(
        user_input=user_input,
        workspace=workspace,
        dialogue_history=dialogue_history or [],
        internal_history=internal_history or [],
        channel=channel,
        chat_id=chat_id,
        session_id=session_id,
    )
    state["metadata"] = dict(metadata or {})
    state["media"] = list(media or [])
    if on_progress is not None:
        state["on_progress"] = on_progress

    for _ in range(MAX_TURN_STEPS):
        state = await main_brain_node(state, runtime)
        if state.get("done"):
            return str(state.get("output", "") or ""), state

        if _executor_request_ready(state):
            state = await executor_node(state, runtime)
            if state.get("done"):
                return str(state.get("output", "") or ""), state
            continue

        logger.warning("Turn loop stopped without a finalized main_brain decision")
        break

    if not state.get("done"):
        main_brain = state.get("main_brain")
        executor = state.get("executor")
        fallback = ""
        if main_brain is not None:
            fallback = str(getattr(main_brain, "final_message", "") or "").strip()
        if not fallback and executor is not None:
            fallback = str(getattr(executor, "analysis", "") or "").strip()
        state["output"] = fallback or "我先给你一个当前能确认的结论，我们可以继续往下推进。"
        state["done"] = True

    return str(state.get("output", "") or ""), state


__all__ = ["run_turn_loop"]
