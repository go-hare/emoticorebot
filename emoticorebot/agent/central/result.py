"""Central execution result packet structure."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CentralResult:
    """Structured result from Central execution."""
    
    # 核心状态
    control_state: str = "running"  # running, waiting_input, completed, failed
    status: str = "success"  # success, partial, failed, pending
    
    # 分析和内容
    analysis: str = ""  # 执行分析和推理过程
    message: str = ""  # 给用户的最终回复
    
    # 缺失信息
    missing: list[str] = field(default_factory=list)  # 缺失的字段列表
    pending_review: list[dict[str, Any]] = field(default_factory=list)  # 待审核项
    
    # 建议和置信度
    recommended_action: str = ""  # 建议的下一步操作
    confidence: float = 1.0  # 置信度 0-1
    
    # 元数据
    attempt_count: int = 1  # 尝试次数
    task_trace: list[dict[str, Any]] = field(default_factory=list)  # 执行追踪
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "control_state": self.control_state,
            "status": self.status,
            "analysis": self.analysis,
            "message": self.message,
            "missing": list(self.missing),
            "pending_review": list(self.pending_review),
            "recommended_action": self.recommended_action,
            "confidence": self.confidence,
            "attempt_count": self.attempt_count,
            "task_trace": list(self.task_trace),
        }


def parse_agent_response(raw_response: str, trace_log: list[dict[str, Any]] | None = None) -> CentralResult:
    """Parse agent response and build structured result."""
    result = CentralResult()
    result.message = raw_response
    result.task_trace = list(trace_log or [])
    
    # 从 trace 中提取信息
    if trace_log:
        # 计算工具调用次数作为置信度参考
        tool_calls = sum(1 for t in trace_log if t.get("type") == "tool_call")
        if tool_calls > 0:
            result.confidence = min(1.0, 0.6 + (tool_calls * 0.1))
    
    # 简单的状态推断
    if raw_response:
        result.control_state = "completed"
        result.status = "success"
        result.analysis = f"Central 执行完成，生成回复长度 {len(raw_response)} 字符"
    else:
        result.control_state = "failed"
        result.status = "failed"
        result.analysis = "Central 未生成有效回复"
        result.confidence = 0.3
    
    return result


__all__ = ["CentralResult", "parse_agent_response"]
