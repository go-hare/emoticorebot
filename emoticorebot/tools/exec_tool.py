"""Command execution tool.

ExecTool: 执行 Shell 命令
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any

from emoticorebot.tools.base import Tool


class ExecTool(Tool):
    """执行 Shell 命令"""
    
    def __init__(
        self,
        timeout: int = 60,
        working_dir: str | None = None,
        deny_patterns: list[str] | None = None,
        allow_patterns: list[str] | None = None,
        restrict_to_workspace: bool = False,
        path_append: str = "",
    ):
        self.timeout = timeout
        self.working_dir = working_dir
        self.deny_patterns = deny_patterns or [
            r"\brm\s+-[rf]{1,2}\b",
            r"\bdel\s+/[fq]\b",
            r"\brmdir\s+/s\b",
            r"(?:^|[;&|]\s*)format\b",
            r"\b(mkfs|diskpart)\b",
            r"\bdd\s+if=",
            r">\s*/dev/sd",
            r"\b(shutdown|reboot|poweroff)\b",
            r":\(\)\s*\{.*\};\s*:",
        ]
        self.allow_patterns = allow_patterns or []
        self.restrict_to_workspace = restrict_to_workspace
        self.path_append = path_append
    
    @property
    def name(self) -> str:
        return "exec"
    
    @property
    def description(self) -> str:
        return "Execute a shell command and return output."
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "working_dir": {"type": "string"},
            },
            "required": ["command"],
        }
    
    async def execute(self, command: str, working_dir: str | None = None, **kwargs: Any) -> str:
        base_dir = Path(self.working_dir or os.getcwd()).resolve()
        raw_working_dir = str(working_dir or "").strip()
        if raw_working_dir:
            requested_dir = Path(raw_working_dir).expanduser()
            cwd_path = requested_dir.resolve() if requested_dir.is_absolute() else (base_dir / requested_dir).resolve()
        else:
            cwd_path = base_dir
        cwd = str(cwd_path)
        
        # 安全检查
        guard_error = self._guard_command(command, cwd)
        if guard_error:
            return guard_error
        
        env = os.environ.copy()
        if self.path_append:
            env["PATH"] = env.get("PATH", "") + os.pathsep + self.path_append
        
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout)
            except asyncio.TimeoutError:
                process.kill()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
                return f"Error: Command timed out after {self.timeout} seconds"
            
            output_parts = []
            if stdout:
                output_parts.append(stdout.decode("utf-8", errors="replace"))
            if stderr:
                stderr_text = stderr.decode("utf-8", errors="replace")
                output_parts.append(f"[stderr]\n{stderr_text}")
            
            output = "\n".join(output_parts) if output_parts else "(no output)"
            return f"Exit code: {process.returncode}\n{output}"
        
        except Exception as e:
            return f"Error executing command: {e}"
    
    def _guard_command(self, command: str, cwd: str) -> str | None:
        """安全检查命令"""
        # 白名单优先
        if self.allow_patterns:
            if any(re.search(pattern, command, re.I) for pattern in self.allow_patterns):
                return None
        
        # 黑名单检查
        for pattern in self.deny_patterns:
            if re.search(pattern, command, re.I):
                return f"Error: Dangerous command pattern detected: {pattern}"
        
        # 工作区限制检查
        if self.restrict_to_workspace:
            if not self.working_dir:
                return "Error: restrict_to_workspace enabled but no working_dir set"
            
            cwd_path = Path(cwd).resolve()
            workspace_path = Path(self.working_dir).resolve()
            try:
                cwd_path.relative_to(workspace_path)
            except ValueError:
                return f"Error: working_dir {cwd} is outside workspace {self.working_dir}"
        
        return None


__all__ = ["ExecTool"]
