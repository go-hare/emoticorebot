"""Services package - 每次请求都会参与的核心服务。

与 background/ 的区别：
- services/    → 请求驱动的有状态服务（EQ 响应 / IQ 执行 / 记忆管理 / 工具管理）
- background/  → 独立运行的长生命周期守护进程

服务列表：
- EQService:    EQ 情感响应（LLM 调用 + 情绪上下文注入）
- IQService:    IQ 任务执行（工具调用循环）
- MemoryService:记忆管理（写入 / 压缩 / 技能生成）
- ToolManager:  工具管理（注册 / 上下文 / MCP 连接）
"""

from emoticorebot.services.eq_service import EQService
from emoticorebot.services.iq_service import IQService
from emoticorebot.services.memory_service import MemoryService
from emoticorebot.services.tool_manager import ToolManager

__all__ = [
    "EQService",
    "IQService",
    "MemoryService",
    "ToolManager",
]
