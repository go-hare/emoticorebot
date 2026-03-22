"""Execution runtime using direct tool-calling loops."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from emoticorebot.core.parser import parse_json_model
from emoticorebot.execution.schemas import JobResult, JobSpec
from emoticorebot.execution.skills import SkillLibrary
from emoticorebot.execution.toolkit import build_tool_registry
from emoticorebot.state.schemas import now_iso
from emoticorebot.tools.base import ToolRegistry
from emoticorebot.utils.llm_utils import extract_message_text


class ExecutionRuntime:
    """Run checks against a tool-calling model and return terminal results."""

    def __init__(
        self,
        workspace: Path,
        model: Any,
        tools_config: Any,
        result_handler: Callable[[dict[str, Any]], Awaitable[None]],
    ):
        self.workspace = workspace
        self.model = model
        self.result_handler = result_handler
        self.registry = build_tool_registry(workspace, tools_config)
        self.skills = SkillLibrary(workspace)

    async def run_checks(self, checks: list[dict[str, Any]]) -> None:
        for check in checks:
            job = JobSpec.model_validate(check)
            await self.run_check(job)

    async def run_check(self, job: JobSpec | dict[str, Any]) -> None:
        job = JobSpec.model_validate(job if isinstance(job, dict) else job.model_dump())
        bound_model = self.bind_model()
        messages = self.build_messages(job)
        trace: list[dict[str, Any]] = []
        max_iterations = 24
        for _ in range(max_iterations):
            if hasattr(bound_model, "ainvoke"):
                response = await bound_model.ainvoke(messages)
            elif hasattr(bound_model, "invoke"):
                response = bound_model.invoke(messages)
            else:
                raise RuntimeError("Bound execution model does not support invoke")
            text = extract_message_text(response)
            tool_calls = list(getattr(response, "tool_calls", []) or [])
            trace.append(
                {
                    "role": "assistant",
                    "content": text,
                    "tool_calls": tool_calls,
                    "created_at": now_iso(),
                    "job_id": job.job_id,
                    "task_id": job.task_id,
                    "check_id": job.check_id,
                }
            )
            messages.append(response)
            if not tool_calls:
                result = parse_json_model(text, JobResult)
                payload = result.model_dump()
                payload["thread_id"] = job.thread_id
                payload["trace"] = trace
                await self.result_handler(payload)
                return

            for tool_call in tool_calls:
                tool_name = str(tool_call.get("name", "") or "").strip()
                tool_args = dict(tool_call.get("args", {}) or {})
                tool_result = await self.run_tool(tool_name, tool_args)
                trace.append(
                    {
                        "role": "tool",
                        "tool_name": tool_name,
                        "content": tool_result,
                        "created_at": now_iso(),
                        "job_id": job.job_id,
                        "task_id": job.task_id,
                        "check_id": job.check_id,
                    }
                )
                messages.append(ToolMessage(content=tool_result, tool_call_id=str(tool_call.get("id", "") or "")))

        raise RuntimeError(f"Execution exceeded tool iteration limit for job {job.job_id}")

    def bind_model(self) -> Any:
        if not hasattr(self.model, "bind_tools"):
            raise RuntimeError("Execution model does not support tool calling")
        return self.model.bind_tools(self.registry.get_definitions())

    def build_messages(self, job: JobSpec) -> list[SystemMessage | HumanMessage]:
        system_text = (self.workspace / "templates" / "EXECUTION.md").read_text(encoding="utf-8")
        payload = {
            "job_id": job.job_id,
            "task_id": job.task_id,
            "check_id": job.check_id,
            "goal": job.goal,
            "instructions": job.instructions,
            "workspace": job.workspace,
            "skills": self.skills.build_summary(),
        }
        return [
            SystemMessage(content=system_text),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False, indent=2)),
        ]

    async def run_tool(self, name: str, params: dict[str, Any]) -> str:
        tool = self.registry.get(name)
        if tool is None:
            return f"Error: Tool '{name}' not found"
        errors = tool.validate_params(params)
        if errors:
            return f"Error: Invalid parameters for tool '{name}': {'; '.join(errors)}"
        return await tool.execute(**params)
