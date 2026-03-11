"""Tool base classes and registry.

工具系统的核心抽象：
- Tool: 工具抽象基类
- ToolRegistry: 工具注册表
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable


class Tool(ABC):
    """工具抽象基类"""
    
    _TYPE_MAP = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    
    @property
    @abstractmethod
    def name(self) -> str:
        """工具名称"""
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """工具描述"""
        pass
    
    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """工具参数 schema（JSON Schema 格式）"""
        pass
    
    @abstractmethod
    async def execute(self, **kwargs: Any) -> str:
        """执行工具"""
        pass
    
    def validate_params(self, params: dict[str, Any]) -> list[str]:
        """验证参数"""
        schema = self.parameters or {}
        if schema.get("type", "object") != "object":
            raise ValueError(f"Schema must be object type, got {schema.get('type')!r}")
        return self._validate(params, {**schema, "type": "object"}, "")
    
    def _validate(self, val: Any, schema: dict[str, Any], path: str) -> list[str]:
        """递归验证参数"""
        t, label = schema.get("type"), path or "parameter"
        if t in self._TYPE_MAP and not isinstance(val, self._TYPE_MAP[t]):
            return [f"{label} should be {t}"]
        errors = []
        if "enum" in schema and val not in schema["enum"]:
            errors.append(f"{label} must be one of {schema['enum']}")
        if t in ("integer", "number"):
            if "minimum" in schema and val < schema["minimum"]:
                errors.append(f"{label} must be >= {schema['minimum']}")
            if "maximum" in schema and val > schema["maximum"]:
                errors.append(f"{label} must be <= {schema['maximum']}")
        if t == "string":
            if "minLength" in schema and len(val) < schema["minLength"]:
                errors.append(f"{label} must be at least {schema['minLength']} chars")
            if "maxLength" in schema and len(val) > schema["maxLength"]:
                errors.append(f"{label} must be at most {schema['maxLength']} chars")
        if t == "object":
            props = schema.get("properties", {})
            for k in schema.get("required", []):
                if k not in val:
                    errors.append(f"missing required {path + '.' + k if path else k}")
            for k, v in val.items():
                if k in props:
                    errors.extend(self._validate(v, props[k], path + '.' + k if path else k))
        if t == "array" and "items" in schema:
            for i, item in enumerate(val):
                errors.extend(self._validate(item, schema["items"], f"{path}[{i}]" if path else f"[{i}]"))
        return errors
    
    def to_schema(self) -> dict[str, Any]:
        """转换为 LangChain 工具 schema"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """工具注册表"""
    
    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._execution_observer: Callable[[dict[str, Any]], Awaitable[None]] | None = None
        self._execution_context: dict[str, Any] = {}
    
    def register(self, tool: Tool) -> None:
        """注册工具"""
        self._tools[tool.name] = tool
    
    def get(self, name: str) -> Tool | None:
        """获取工具"""
        return self._tools.get(name)
    
    def get_definitions(self) -> list[dict[str, Any]]:
        """获取所有工具的定义"""
        return [tool.to_schema() for tool in self._tools.values()]
    
    def set_execution_observer(
        self,
        observer: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> None:
        self._execution_observer = observer

    def set_execution_context(
        self,
        *,
        channel: str = "",
        chat_id: str = "",
        message_id: str | None = None,
        session_key: str | None = None,
        source: str = "task",
    ) -> None:
        self._execution_context = {
            "channel": str(channel or ""),
            "chat_id": str(chat_id or ""),
            "message_id": str(message_id or ""),
            "session_key": str(session_key or ""),
            "source": str(source or "task"),
        }

    async def execute(self, name: str, params: dict[str, Any]) -> str:
        """执行工具"""
        hint = "\n\n[Analyze the error above and try a different approach.]"
        tool = self._tools.get(name)
        if not tool:
            result = f"Error: Tool '{name}' not found. Available: {', '.join(self.tool_names)}"
            await self._notify_execution(name=name, params=params, raw_result=result, final_result=result, success=False)
            return result
        try:
            errors = tool.validate_params(params)
            if errors:
                raw_result = f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors)
                final_result = raw_result + hint
                await self._notify_execution(
                    name=name,
                    params=params,
                    raw_result=raw_result,
                    final_result=final_result,
                    success=False,
                )
                return final_result
            result = await tool.execute(**params)
            if isinstance(result, str) and result.startswith("Error"):
                final_result = result + hint
                await self._notify_execution(
                    name=name,
                    params=params,
                    raw_result=result,
                    final_result=final_result,
                    success=False,
                )
                return final_result
            await self._notify_execution(
                name=name,
                params=params,
                raw_result=result,
                final_result=str(result),
                success=True,
            )
            return result
        except Exception as e:
            raw_result = f"Error executing {name}: {e}"
            final_result = raw_result + hint
            await self._notify_execution(
                name=name,
                params=params,
                raw_result=raw_result,
                final_result=final_result,
                success=False,
            )
            return final_result

    async def _notify_execution(
        self,
        *,
        name: str,
        params: dict[str, Any],
        raw_result: Any,
        final_result: str,
        success: bool,
    ) -> None:
        if self._execution_observer is None:
            return
        try:
            await self._execution_observer(
                {
                    "tool_name": name,
                    "params": dict(params or {}),
                    "raw_result": str(raw_result or ""),
                    "final_result": final_result,
                    "success": success,
                    "context": dict(self._execution_context),
                }
            )
        except Exception:
            pass
    
    @property
    def tool_names(self) -> list[str]:
        """获取所有工具名称"""
        return list(self._tools.keys())


__all__ = ["Tool", "ToolRegistry"]
