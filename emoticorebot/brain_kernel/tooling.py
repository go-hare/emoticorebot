"""Tool schemas and rule evaluation for the brain kernel."""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class FunctionTool:
    """Server-side tool executed directly inside the brain."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        parameters: dict[str, Any],
        func: Callable[..., Any],
    ) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters
        self.func = func

    def to_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        result = self.func(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False)


class ClientTool:
    """Client-side tool exposed by runtime and resumed via tool results."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        parameters: dict[str, Any],
    ) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters

    def to_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolExecutionRecord(BaseModel):
    tool_call_id: str = ""
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    result: str = ""
    success: bool = True


class ToolRuleType(StrEnum):
    run_first = "run_first"
    exit_loop = "exit_loop"
    continue_loop = "continue_loop"
    conditional = "conditional"
    constrain_child_tools = "constrain_child_tools"
    max_count_per_step = "max_count_per_step"
    parent_last_tool = "parent_last_tool"
    required_before_exit = "required_before_exit"
    requires_approval = "requires_approval"


class ToolCallNode(BaseModel):
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class BaseToolRule(BaseModel):
    tool_name: str
    type: ToolRuleType
    prompt_template: str | None = None

    def get_valid_tools(
        self,
        tool_call_history: list[str],
        available_tools: set[str],
        last_function_response: str | None,
    ) -> set[str]:
        raise NotImplementedError

    def render_prompt(self) -> str | None:
        return None

    @property
    def requires_force_tool_call(self) -> bool:
        return False


class InitToolRule(BaseToolRule):
    type: ToolRuleType = ToolRuleType.run_first
    args: dict[str, Any] = Field(default_factory=dict)

    def get_valid_tools(
        self,
        tool_call_history: list[str],
        available_tools: set[str],
        last_function_response: str | None,
    ) -> set[str]:
        _ = last_function_response
        if tool_call_history:
            return available_tools
        return {self.tool_name}

    def render_prompt(self) -> str | None:
        return f"<tool_rule>\nThe first tool must be {self.tool_name}\n</tool_rule>"

    @property
    def requires_force_tool_call(self) -> bool:
        return True


class ChildToolRule(BaseToolRule):
    type: ToolRuleType = ToolRuleType.constrain_child_tools
    children: list[str] = Field(default_factory=list)
    child_arg_nodes: list[ToolCallNode] = Field(default_factory=list)

    def get_valid_tools(
        self,
        tool_call_history: list[str],
        available_tools: set[str],
        last_function_response: str | None,
    ) -> set[str]:
        _ = last_function_response
        if tool_call_history and tool_call_history[-1] == self.tool_name:
            return set(self.children)
        return available_tools

    def get_child_args_map(self) -> dict[str, dict[str, Any]]:
        return {node.name: dict(node.args) for node in self.child_arg_nodes if node.args}

    def render_prompt(self) -> str | None:
        child_names = ", ".join(self.children)
        return f"<tool_rule>\nAfter using {self.tool_name}, use one of: {child_names}\n</tool_rule>"

    @property
    def requires_force_tool_call(self) -> bool:
        return True

    @model_validator(mode="after")
    def validate_child_nodes(self) -> "ChildToolRule":
        children = set(self.children)
        for node in self.child_arg_nodes:
            if node.name not in children:
                raise ValueError(f"{node.name} is not a declared child of {self.tool_name}")
        return self


class ParentToolRule(BaseToolRule):
    type: ToolRuleType = ToolRuleType.parent_last_tool
    children: list[str] = Field(default_factory=list)

    def get_valid_tools(
        self,
        tool_call_history: list[str],
        available_tools: set[str],
        last_function_response: str | None,
    ) -> set[str]:
        _ = last_function_response
        if tool_call_history and tool_call_history[-1] == self.tool_name:
            return set(self.children)
        return available_tools - set(self.children)

    def render_prompt(self) -> str | None:
        child_names = ", ".join(self.children)
        return f"<tool_rule>\n{child_names} can only be used after {self.tool_name}\n</tool_rule>"

    @property
    def requires_force_tool_call(self) -> bool:
        return True


class ConditionalToolRule(BaseToolRule):
    type: ToolRuleType = ToolRuleType.conditional
    default_child: str = ""
    child_output_mapping: dict[str, str] = Field(default_factory=dict)
    require_output_mapping: bool = False

    def get_valid_tools(
        self,
        tool_call_history: list[str],
        available_tools: set[str],
        last_function_response: str | None,
    ) -> set[str]:
        if not tool_call_history or tool_call_history[-1] != self.tool_name:
            return available_tools
        if not last_function_response:
            return {self.default_child} if self.default_child else available_tools

        try:
            payload = json.loads(last_function_response)
            function_output = str(payload.get("message", "") or "")
        except json.JSONDecodeError:
            return {self.default_child} if self.default_child else available_tools

        mapped_tool = self.child_output_mapping.get(function_output)
        if mapped_tool:
            return {mapped_tool}
        if self.require_output_mapping:
            return set()
        return {self.default_child} if self.default_child else available_tools

    def render_prompt(self) -> str | None:
        return f"<tool_rule>\n{self.tool_name} selects the next tool from its output.\n</tool_rule>"

    @property
    def requires_force_tool_call(self) -> bool:
        return True


class ContinueToolRule(BaseToolRule):
    type: ToolRuleType = ToolRuleType.continue_loop

    def get_valid_tools(
        self,
        tool_call_history: list[str],
        available_tools: set[str],
        last_function_response: str | None,
    ) -> set[str]:
        _ = tool_call_history
        _ = last_function_response
        return available_tools

    def render_prompt(self) -> str | None:
        return f"<tool_rule>\nAfter {self.tool_name}, continue the tool loop.\n</tool_rule>"


class TerminalToolRule(BaseToolRule):
    type: ToolRuleType = ToolRuleType.exit_loop

    def get_valid_tools(
        self,
        tool_call_history: list[str],
        available_tools: set[str],
        last_function_response: str | None,
    ) -> set[str]:
        _ = tool_call_history
        _ = last_function_response
        return available_tools

    def render_prompt(self) -> str | None:
        return f"<tool_rule>\n{self.tool_name} ends the current tool loop.\n</tool_rule>"


class RequiredBeforeExitToolRule(BaseToolRule):
    type: ToolRuleType = ToolRuleType.required_before_exit

    def get_valid_tools(
        self,
        tool_call_history: list[str],
        available_tools: set[str],
        last_function_response: str | None,
    ) -> set[str]:
        _ = tool_call_history
        _ = last_function_response
        return available_tools


class RequiresApprovalToolRule(BaseToolRule):
    type: ToolRuleType = ToolRuleType.requires_approval

    def get_valid_tools(
        self,
        tool_call_history: list[str],
        available_tools: set[str],
        last_function_response: str | None,
    ) -> set[str]:
        _ = tool_call_history
        _ = last_function_response
        return available_tools


class MaxCountPerStepToolRule(BaseToolRule):
    type: ToolRuleType = ToolRuleType.max_count_per_step
    max_count_limit: int = 1

    def get_valid_tools(
        self,
        tool_call_history: list[str],
        available_tools: set[str],
        last_function_response: str | None,
    ) -> set[str]:
        _ = last_function_response
        count = tool_call_history.count(self.tool_name)
        if count >= self.max_count_limit:
            return available_tools - {self.tool_name}
        return available_tools

    def render_prompt(self) -> str | None:
        return f"<tool_rule>\n{self.tool_name}: at most {self.max_count_limit} use(s) per response\n</tool_rule>"


class ToolRulesSolver(BaseModel):
    tool_rules: list[BaseToolRule] = Field(default_factory=list)
    tool_call_history: list[str] = Field(default_factory=list)
    last_prefilled_args_by_tool: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def register_tool_call(self, tool_name: str) -> None:
        self.tool_call_history.append(tool_name)

    def get_allowed_tool_names(
        self,
        available_tools: set[str],
        *,
        error_on_empty: bool = True,
        last_function_response: str | None = None,
    ) -> list[str]:
        init_rules = [rule for rule in self.tool_rules if isinstance(rule, InitToolRule)]
        child_rules = [
            rule
            for rule in self.tool_rules
            if isinstance(rule, (ChildToolRule, ParentToolRule, ConditionalToolRule, MaxCountPerStepToolRule))
        ]

        if not self.tool_call_history and init_rules:
            allowed = {rule.tool_name for rule in init_rules}
        else:
            valid_sets = [
                rule.get_valid_tools(self.tool_call_history, available_tools, last_function_response)
                for rule in child_rules
            ]
            allowed = set.intersection(*valid_sets) if valid_sets else set(available_tools)
            allowed &= available_tools

        if error_on_empty and not allowed:
            raise ValueError("No valid tools found based on tool rules.")

        self._cache_prefilled_args(allowed=allowed, available_tools=available_tools)
        return sorted(allowed)

    def _cache_prefilled_args(self, *, allowed: set[str], available_tools: set[str]) -> None:
        self.last_prefilled_args_by_tool.clear()
        last_tool = self.tool_call_history[-1] if self.tool_call_history else ""
        allowed &= available_tools

        for rule in self.tool_rules:
            if isinstance(rule, InitToolRule) and not self.tool_call_history and rule.args and rule.tool_name in allowed:
                self.last_prefilled_args_by_tool[rule.tool_name] = dict(rule.args)
            if isinstance(rule, ChildToolRule) and last_tool == rule.tool_name:
                for child_name, child_args in rule.get_child_args_map().items():
                    if child_name in allowed:
                        self.last_prefilled_args_by_tool[child_name] = dict(child_args)

    def get_prefilled_args(self, tool_name: str) -> dict[str, Any]:
        return dict(self.last_prefilled_args_by_tool.get(tool_name, {}))

    def compile_rule_prompt(self) -> str:
        prompts = [rule.render_prompt() for rule in self.tool_rules if rule.render_prompt()]
        return "\n".join(prompts)
