"""Central prompt construction helpers."""

from __future__ import annotations

import json
from typing import Any

from emoticorebot.agent.central.result import extract_missing


def build_request_prompt(
    *,
    request: str,
    history: list[dict[str, Any]],
    task_context: dict[str, Any] | None,
    media: list[str] | None,
) -> str:
    task = task_context or {}
    delegation = normalize_delegation(task.get("delegation"), fallback_request=request)
    parts = build_delegation_prompt_sections(delegation=delegation)
    resume_payload = task.get("resume_payload")
    missing = extract_missing(task)
    thread_id = str(task.get("thread_id", "") or "").strip()
    run_id = str(task.get("run_id", "") or "").strip()

    if thread_id:
        parts.append(f"当前执行线程：{thread_id}")
    if run_id:
        parts.append(f"当前执行轮次：{run_id}")
    if resume_payload not in (None, "", [], {}):
        payload_text = resume_payload if isinstance(resume_payload, str) else json.dumps(resume_payload, ensure_ascii=False)
        parts.append(f"恢复载荷：{payload_text}")
    if missing:
        parts.append(f"需优先确认缺参：{json.dumps(missing, ensure_ascii=False)}")
    if media:
        parts.append(f"关联媒体数量：{len(media)}")

    compact = compact_history(history)
    if compact:
        parts.append("最近内部上下文：")
        parts.extend(compact)

    parts.append("brain 已提供相关执行经验、工具经验和 skill 提示；不要自行检索长期 memory。")
    parts.append(
        "你必须只返回一个 JSON 对象，不能输出解释、前言、Markdown、代码块、补充说明。\n"
        "\n"
        "字段说明：\n"
        "- `status`：执行整体状态，只能是 `completed`、`needs_input`、`uncertain`、`failed`。\n"
        "- `analysis`：给 brain 的紧凑结论，说明你做了什么、得出了什么结果。\n"
        "- `risks`：风险、不确定性、边界提醒，没有就返回空数组 `[]`。\n"
        "- `missing`：继续执行所缺少的信息，没有就返回空数组 `[]`。\n"
        "- `recommended_action`：建议 brain 下一步做什么，只能是 `answer`、`ask_user`、`continue_task`。\n"
        "- `confidence`：0 到 1 之间的小数。\n"
        "- `pending_review`：只有确实存在审批/编辑/确认动作时才填写对象，否则返回空对象 `{{}}`。\n"
        "\n"
        "填写规则：\n"
        "1. 如果任务已经完成且可交付，`status` 应为 `completed`，通常 `recommended_action` 应为 `answer`。\n"
        "2. 如果必须向用户索取信息，`status` 应为 `needs_input`，`recommended_action` 应为 `ask_user`。\n"
        "3. 如果还有必要继续内部执行，`recommended_action` 才能是 `continue_task`。\n"
        "4. `analysis` 必须非空。\n"
        "5. 不要遗漏任何字段。\n"
        "\n"
        "标准结构：\n"
        "{{\n"
        '  "status": "completed|needs_input|uncertain|failed",\n'
        '  "analysis": "...",\n'
        '  "risks": ["..."],\n'
        '  "missing": ["..."],\n'
        '  "recommended_action": "answer|ask_user|continue_task",\n'
        '  "confidence": 0.0,\n'
        '  "pending_review": {{}}\n'
        "}}\n"
        "\n"
        "已完成示例：\n"
        "{{\n"
        '  "status": "completed",\n'
        '  "analysis": "已完成检查，并得到可直接交付的最终结果。",\n'
        '  "risks": [],\n'
        '  "missing": [],\n'
        '  "recommended_action": "answer",\n'
        '  "confidence": 0.91,\n'
        '  "pending_review": {{}}\n'
        "}}\n"
        "\n"
        "缺少信息示例：\n"
        "{{\n"
        '  "status": "needs_input",\n'
        '  "analysis": "已经明确下一步需要的参数，但当前输入不足，无法继续执行。",\n'
        '  "risks": ["缺少关键参数会导致结果不准确"],\n'
        '  "missing": ["时间范围", "目标地址"],\n'
        '  "recommended_action": "ask_user",\n'
        '  "confidence": 0.84,\n'
        '  "pending_review": {{}}\n'
        "}}"
    )
    return "\n".join(parts)


def normalize_delegation(value: Any, *, fallback_request: str) -> dict[str, Any]:
    delegation = dict(value) if isinstance(value, dict) else {}

    goal = str(delegation.get("goal", "") or "").strip() or str(fallback_request or "").strip()
    request = str(delegation.get("request", "") or "").strip() or goal
    constraints = [str(item).strip() for item in list(delegation.get("constraints", []) or []) if str(item).strip()]
    relevant_task_memories = [
        item
        for item in list(delegation.get("relevant_task_memories", []) or [])
        if isinstance(item, dict)
    ]
    relevant_tool_memories = [
        item
        for item in list(delegation.get("relevant_tool_memories", []) or [])
        if isinstance(item, dict)
    ]
    skill_hints = [
        item
        for item in list(delegation.get("skill_hints", []) or [])
        if isinstance(item, dict)
    ]
    success_criteria = [
        str(item).strip()
        for item in list(delegation.get("success_criteria", []) or [])
        if str(item).strip()
    ]
    return_contract = dict(delegation.get("return_contract", {}) or {})

    normalized = {
        "goal": goal,
        "request": request,
        "constraints": constraints,
        "relevant_task_memories": relevant_task_memories,
        "relevant_tool_memories": relevant_tool_memories,
        "skill_hints": skill_hints,
        "success_criteria": success_criteria,
        "return_contract": return_contract,
    }
    resume_payload = delegation.get("resume_payload")
    if resume_payload not in (None, "", [], {}):
        normalized["resume_payload"] = resume_payload
    return normalized


def build_delegation_prompt_sections(*, delegation: dict[str, Any]) -> list[str]:
    goal = str(delegation.get("goal", "") or "").strip()
    parts = [f"内部目标：{goal}"] if goal else []

    request = str(delegation.get("request", "") or "").strip()
    if request:
        parts.append(f"主脑内部请求：{request}")

    constraints = [str(item).strip() for item in list(delegation.get("constraints", []) or []) if str(item).strip()]
    if constraints:
        parts.append("执行约束：")
        parts.extend(f"- {item}" for item in constraints[:6])

    task_memories = [
        item
        for item in list(delegation.get("relevant_task_memories", []) or [])
        if isinstance(item, dict)
    ]
    if task_memories:
        parts.append("相关执行经验：")
        parts.extend(
            f"- [{str(item.get('type', '') or 'memory')}] {str(item.get('summary', '') or item.get('content', '')).strip()}"
            for item in task_memories[:3]
            if str(item.get("summary", "") or item.get("content", "")).strip()
        )

    tool_memories = [
        item
        for item in list(delegation.get("relevant_tool_memories", []) or [])
        if isinstance(item, dict)
    ]
    if tool_memories:
        parts.append("相关工具经验：")
        parts.extend(
            f"- [{str(item.get('type', '') or 'memory')}] {str(item.get('summary', '') or item.get('content', '')).strip()}"
            for item in tool_memories[:3]
            if str(item.get("summary", "") or item.get("content", "")).strip()
        )

    skill_hints = [
        item
        for item in list(delegation.get("skill_hints", []) or [])
        if isinstance(item, dict)
    ]
    if skill_hints:
        parts.append("Skill 提示：")
        parts.extend(
            f"- {str(item.get('summary', '') or item.get('content', '')).strip()}"
            for item in skill_hints[:3]
            if str(item.get("summary", "") or item.get("content", "")).strip()
        )

    success_criteria = [
        str(item).strip()
        for item in list(delegation.get("success_criteria", []) or [])
        if str(item).strip()
    ]
    if success_criteria:
        parts.append("成功标准：")
        parts.extend(f"- {item}" for item in success_criteria[:5])

    return_contract = (
        delegation.get("return_contract")
        if isinstance(delegation.get("return_contract"), dict)
        else {}
    )
    if return_contract:
        parts.append(f"返回契约：{json.dumps(return_contract, ensure_ascii=False)}")

    resume_payload = delegation.get("resume_payload")
    if resume_payload not in (None, "", [], {}):
        payload_text = resume_payload if isinstance(resume_payload, str) else json.dumps(resume_payload, ensure_ascii=False)
        parts.append(f"委托恢复载荷：{payload_text}")

    return parts


def compact_history(history: list[dict[str, Any]] | None, *, limit: int = 6) -> list[str]:
    compact: list[str] = []
    filtered: list[dict[str, Any]] = []
    for item in reversed(history or []):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "") or "").strip()
        if role == "tool":
            continue
        content = " ".join(str(item.get("content", "") or "").split()).strip()
        if role == "assistant" and not content:
            tool_calls = item.get("tool_calls")
            if isinstance(tool_calls, list) and tool_calls:
                continue
        filtered.append(item)
        if len(filtered) >= limit:
            break

    for item in reversed(filtered):
        role = str(item.get("role", "") or "").strip()
        content = " ".join(str(item.get("content", "") or "").split()).strip()
        if role and content:
            compact.append(f"- {role}: {content[:200]}")
    return compact


__all__ = [
    "build_delegation_prompt_sections",
    "build_request_prompt",
    "compact_history",
    "normalize_delegation",
]
