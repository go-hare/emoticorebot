"""Memory Node - 写入记忆与状态文件（收尾节点）。"""

from emoticorebot.core.state import FusionState


async def memory_node(state: FusionState, runtime) -> FusionState:
    """将本轮对话写入记忆，更新情绪状态文件。"""
    await runtime.write_memory(state)
    return state
