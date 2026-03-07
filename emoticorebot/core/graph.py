"""Fusion graph - LangGraph 图定义与编译。

define + compile LangGraph:
  entry → eq_node
  eq_node →（路由）→ iq_node | memory_node
  iq_node →（路由）→ eq_node | memory_node
  memory_node → END
"""

from pathlib import Path
from langgraph.graph import StateGraph, END
from loguru import logger

from emoticorebot.core.state import FusionState, create_initial_state
from emoticorebot.core.nodes.eq_node import eq_node
from emoticorebot.core.nodes.iq_node import iq_node
from emoticorebot.core.nodes.memory_node import memory_node
from emoticorebot.core.router import FusionRouter


def create_fusion_agent(workspace: Path, runtime):
    """编译 LangGraph fusion 图（一次性，结果应被 runtime 缓存）。"""
    router = FusionRouter(max_iq_attempts=3)
    graph = StateGraph(FusionState)

    async def _eq(s: FusionState) -> FusionState:
        return await eq_node(s, runtime)

    async def _iq(s: FusionState) -> FusionState:
        return await iq_node(s, runtime)

    async def _memory(s: FusionState) -> FusionState:
        return await memory_node(s, runtime)

    graph.add_node("eq", _eq)
    graph.add_node("iq", _iq)
    graph.add_node("memory", _memory)
    graph.set_entry_point("eq")

    def route_next(state: FusionState) -> str:
        return router.route_next(state)

    graph.add_conditional_edges("eq", route_next, {"iq": "iq", "eq": "eq", "memory": "memory"})
    graph.add_conditional_edges("iq", route_next, {"eq": "eq", "iq": "iq", "memory": "memory"})
    graph.add_edge("memory", END)
    return graph.compile()


async def run_fusion_agent(
    user_input: str,
    workspace: Path,
    runtime,
    history: list[dict] | None = None,
    metadata: dict | None = None,
    channel: str = "",
    chat_id: str = "",
    session_id: str = "",
    on_progress=None,
    agent=None,
) -> tuple[str, dict]:
    """运行一轮 fusion 对话。

    :param agent: 预编译的 LangGraph agent（建议由 runtime 注入，避免重复编译）
    :return: (output_text, final_state)
    """
    if agent is None:
        agent = create_fusion_agent(workspace, runtime=runtime)

    initial_state = create_initial_state(
        user_input=user_input,
        workspace=workspace,
        history=history or [],
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
    except Exception as e:
        logger.error("Fusion agent error: {}", e)
        return f"抱歉，出了点问题: {e}", initial_state


__all__ = ["create_fusion_agent", "run_fusion_agent"]
