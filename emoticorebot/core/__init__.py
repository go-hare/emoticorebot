"""Core package - Agent 决策核心。

定义 AI 的推理结构、决策流程和路由策略。

与 services/ 的区别：
- core/     → 定义"AI 如何决策"（图结构、策略、信号、状态）
- services/ → 定义"如何执行"（LLM 调用、工具执行、记忆管理）

子模块：
- state.py           FusionState / IQState / EQState 数据结构
- graph.py           LangGraph 图定义与编译
- router.py          节点路由决策（FusionRouter）
- context.py         System prompt 构建器
- model.py           LLM 模型配置与工厂
- skills.py          技能（Skill）加载器
- mcp.py             MCP 服务器连接适配
- nodes/             各图节点实现
"""

from emoticorebot.core.state import (
    FusionState,
    IQState,
    EQState,
    Metadata,
    create_initial_state,
    load_pad_from_workspace,
    get_emotion_label,
)
from emoticorebot.core.graph import create_fusion_agent, run_fusion_agent
from emoticorebot.core.router import FusionRouter

__all__ = [
    "FusionState",
    "IQState",
    "EQState",
    "Metadata",
    "create_initial_state",
    "load_pad_from_workspace",
    "get_emotion_label",
    "create_fusion_agent",
    "run_fusion_agent",
    "FusionRouter",
]
