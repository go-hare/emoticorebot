"""Central execution result packet structure."""

from __future__ import annotations

import json
import re
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


def _extract_json_block(text: str) -> dict[str, Any] | None:
    """尝试从文本中提取 JSON 块（```json ... ``` 或裸 JSON）"""
    # 尝试提取 ```json ... ``` 块
    json_block_pattern = r"```(?:json)?\s*(\{[\s\S]*?\})\s*```"
    match = re.search(json_block_pattern, text)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    
    # 尝试查找裸 JSON 对象
    brace_start = text.find("{")
    if brace_start != -1:
        depth = 0
        for i, ch in enumerate(text[brace_start:], start=brace_start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[brace_start : i + 1])
                    except json.JSONDecodeError:
                        break
    return None


def _extract_from_trace(trace_log: list[dict[str, Any]]) -> dict[str, Any]:
    """从 trace_log 中提取结构化信息"""
    extracted: dict[str, Any] = {
        "tool_calls": 0,
        "tool_errors": 0,
        "tools_used": [],
        "has_file_ops": False,
        "has_search_ops": False,
        "last_tool_result": None,
    }
    
    for entry in trace_log:
        entry_type = entry.get("type", "")
        
        if entry_type == "tool_call":
            extracted["tool_calls"] += 1
            tool_name = str(entry.get("tool", "") or entry.get("name", "")).strip()
            if tool_name and tool_name not in extracted["tools_used"]:
                extracted["tools_used"].append(tool_name)
            
            # 检查操作类型
            tool_lower = tool_name.lower()
            if any(kw in tool_lower for kw in ("file", "read", "write", "edit")):
                extracted["has_file_ops"] = True
            if any(kw in tool_lower for kw in ("search", "grep", "find", "glob")):
                extracted["has_search_ops"] = True
        
        elif entry_type == "tool_result":
            extracted["last_tool_result"] = entry.get("result")
            if entry.get("error") or entry.get("is_error"):
                extracted["tool_errors"] += 1
    
    return extracted


def _infer_missing_from_response(text: str) -> list[str]:
    """从响应文本中推断缺失信息"""
    missing: list[str] = []
    text_lower = text.lower()
    
    patterns = [
        (r"需要.*?(?:提供|告诉|说明).*?([^，。\n]+)", None),
        (r"缺少.*?([^，。\n]+)", None),
        (r"请.*?(?:提供|告诉|说明).*?([^，。\n]+)", None),
        (r"could you (?:provide|tell|specify)\s+(.+?)(?:\?|\.)", None),
        (r"i need (?:to know|more info about)\s+(.+?)(?:\?|\.)", None),
    ]
    
    for pattern, _ in patterns:
        for match in re.finditer(pattern, text_lower):
            item = match.group(1).strip()
            if item and len(item) < 50 and item not in missing:
                missing.append(item)
    
    return missing[:5]  # 最多返回 5 项


def _infer_recommended_action(
    text: str, trace_info: dict[str, Any], missing: list[str]
) -> str:
    """推断建议的下一步操作"""
    if missing:
        return "补充缺失信息后重试"
    
    if trace_info.get("tool_errors", 0) > 0:
        return "检查工具执行错误并修复"
    
    text_lower = text.lower()
    if any(kw in text_lower for kw in ("建议", "可以尝试", "recommend", "suggest")):
        # 尝试提取建议
        for pattern in [
            r"建议[：:]\s*([^。\n]+)",
            r"可以尝试[：:]\s*([^。\n]+)",
            r"recommend(?:ed)?[：:]\s*([^.\n]+)",
        ]:
            match = re.search(pattern, text_lower)
            if match:
                return match.group(1).strip()[:100]
    
    return ""


def parse_agent_response(
    raw_response: str, trace_log: list[dict[str, Any]] | None = None
) -> CentralResult:
    """Parse agent response and build structured result.
    
    从 trace_log 和响应文本中提取结构化信息，填充 CentralResult 的所有字段。
    """
    result = CentralResult()
    result.message = raw_response
    result.task_trace = list(trace_log or [])
    
    trace_info = _extract_from_trace(trace_log or [])
    
    # 尝试从响应中提取 JSON 结构化数据
    json_data = _extract_json_block(raw_response)
    if json_data:
        # 如果响应包含结构化 JSON，优先使用
        result.control_state = str(json_data.get("control_state", "completed")).strip()
        result.status = str(json_data.get("status", "success")).strip()
        result.analysis = str(json_data.get("analysis", "")).strip()
        
        # 提取 missing
        raw_missing = json_data.get("missing", [])
        if isinstance(raw_missing, list):
            result.missing = [str(m).strip() for m in raw_missing if str(m).strip()]
        
        # 提取 pending_review
        raw_pending = json_data.get("pending_review", [])
        if isinstance(raw_pending, list):
            result.pending_review = [
                item for item in raw_pending if isinstance(item, dict)
            ]
        
        result.recommended_action = str(
            json_data.get("recommended_action", "")
        ).strip()
        
        try:
            result.confidence = float(json_data.get("confidence", 0.8))
        except (TypeError, ValueError):
            result.confidence = 0.8
        
        try:
            result.attempt_count = int(json_data.get("attempt_count", 1))
        except (TypeError, ValueError):
            result.attempt_count = 1
        
        # 如果 JSON 中有 message 字段，使用它；否则移除 JSON 块后的文本作为 message
        if "message" in json_data:
            result.message = str(json_data["message"]).strip()
        else:
            # 移除 JSON 块，保留其他文本
            clean_text = re.sub(r"```(?:json)?\s*\{[\s\S]*?\}\s*```", "", raw_response)
            clean_text = clean_text.strip()
            if clean_text:
                result.message = clean_text
    else:
        # 没有结构化 JSON，从文本和 trace 中推断
        result.missing = _infer_missing_from_response(raw_response)
        result.recommended_action = _infer_recommended_action(
            raw_response, trace_info, result.missing
        )
        
        # 根据 trace 计算置信度
        tool_calls = trace_info.get("tool_calls", 0)
        tool_errors = trace_info.get("tool_errors", 0)
        
        if tool_calls > 0:
            base_confidence = 0.6 + (tool_calls * 0.08)
            error_penalty = tool_errors * 0.15
            result.confidence = max(0.3, min(1.0, base_confidence - error_penalty))
        else:
            result.confidence = 0.7  # 没有工具调用时的默认置信度
    
    # 设置 attempt_count（从 trace 中统计重试）
    if trace_log:
        retry_count = sum(
            1 for t in trace_log
            if t.get("type") == "retry" or "retry" in str(t.get("event", "")).lower()
        )
        result.attempt_count = max(1, retry_count + 1)
    
    # 推断控制状态
    if result.missing:
        result.control_state = "waiting_input"
        result.status = "pending"
        if not result.analysis:
            result.analysis = f"执行需要补充信息: {', '.join(result.missing[:3])}"
    elif raw_response:
        if result.control_state not in ("waiting_input", "failed"):
            result.control_state = "completed"
        if result.status not in ("partial", "failed", "pending"):
            result.status = "success"
        if not result.analysis:
            tools_desc = ""
            if trace_info.get("tools_used"):
                tools_desc = f"，使用工具: {', '.join(trace_info['tools_used'][:3])}"
            result.analysis = (
                f"执行完成，回复长度 {len(raw_response)} 字符"
                f"{tools_desc}"
            )
    else:
        result.control_state = "failed"
        result.status = "failed"
        result.analysis = "未生成有效回复"
        result.confidence = 0.3
    
    return result


__all__ = ["CentralResult", "parse_agent_response"]
