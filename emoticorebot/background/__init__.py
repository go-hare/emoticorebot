"""Background package - 后台守护进程。

后台进程与主执行链路的区别：
- left/right/memory/tools → 跟随请求触发的主通路组件
- background/             → 独立运行的长生命周期守护进程（不依赖请求触发）

子模块：
- subconscious.py  潜意识守护进程（情绪衰减 / 主动对话）
- heartbeat.py     心跳服务（定时检查待办任务）
"""

from emoticorebot.background.subconscious import SubconsciousDaemon
from emoticorebot.background.heartbeat import HeartbeatService

__all__ = [
    "SubconsciousDaemon",
    "HeartbeatService",
]
