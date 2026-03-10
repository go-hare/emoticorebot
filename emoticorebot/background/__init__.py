"""Background package - 后台守护进程。

后台进程与 services/ 的区别：
- services/  → 每次请求都会调用的、有状态的服务（main_brain / executor / memory）
- background/ → 独立运行的长生命周期守护进程（不依赖请求触发）

子模块：
- subconscious.py  潜意识守护进程（情绪衰减 / 主动对话）
- reflection.py    元认知反思引擎（更新 SOUL.md / USER.md / 策略）
- heartbeat.py     心跳服务（定时检查待办任务）
"""

from emoticorebot.background.subconscious import SubconsciousDaemon
from emoticorebot.background.reflection import ReflectionEngine, ReflectionResult
from emoticorebot.background.heartbeat import HeartbeatService

__all__ = [
    "SubconsciousDaemon",
    "ReflectionEngine",
    "ReflectionResult",
    "HeartbeatService",
]
