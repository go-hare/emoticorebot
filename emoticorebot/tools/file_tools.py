"""文件操作工具。

工具列表：
- ReadFileTool:    读取文件（支持行范围）
- WriteFileTool:   写入文件（覆盖）
- EditFileTool:    精准字符串替换
- ListDirTool:     列出目录内容
- SearchFilesTool: 按关键词搜索文件内容
- InsertLinesTool: 在指定行后插入内容
- DeleteLinesTool: 删除指定行范围
- ReplaceLinesTool: 替换指定行范围

所有工具均支持 allowed_dir 安全边界（可选）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from emoticorebot.tools.base import Tool


class WorkspaceTool(Tool):
    """带 workspace / allowed_dir 安全边界的文件工具基类。"""

    def __init__(self, workspace: str | Path, allowed_dir: str | Path | None = None):
        self.workspace = Path(workspace)
        self.allowed_dir = Path(allowed_dir) if allowed_dir else None

    def _resolve(self, file_path: str) -> Path | str:
        """解析路径，校验是否在允许目录内，返回绝对 Path 或错误字符串。"""
        raw = str(file_path or "").strip()
        relative = raw[1:] if raw.startswith("/") else raw
        path = (self.workspace / relative).resolve()
        if self.allowed_dir:
            try:
                path.relative_to(self.allowed_dir.resolve())
            except ValueError:
                return f"Error: Access denied: {file_path} is outside the allowed directory"
        return path


# ---------------------------------------------------------------------------
# 基础 CRUD 工具
# ---------------------------------------------------------------------------

class ReadFileTool(WorkspaceTool):
    """读取文件内容，支持行范围。"""

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Read file content. Returns entire file or specified line range."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Relative path to the file"},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
            },
            "required": ["file_path"],
        }

    async def execute(
        self,
        file_path: str,
        start_line: int | None = None,
        end_line: int | None = None,
        **kwargs: Any,
    ) -> str:
        result = self._resolve(file_path)
        if isinstance(result, str):
            return result
        path = result
        try:
            if not path.exists():
                return f"Error: File not found: {file_path}"
            text = path.read_text(encoding="utf-8")
            if start_line is None and end_line is None:
                return text
            lines = text.splitlines(keepends=True)
            start = (start_line or 1) - 1
            end = end_line if end_line else len(lines)
            if start < 0 or end > len(lines) or start >= end:
                return f"Error: Invalid line range {start + 1}-{end}"
            return "".join(lines[start:end])
        except Exception as e:
            return f"Error reading file: {e}"


class WriteFileTool(WorkspaceTool):
    """写入文件（覆盖），自动创建父目录。"""

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Write content to file. Creates parent directories if needed. Overwrites existing content."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["file_path", "content"],
        }

    async def execute(self, file_path: str, content: str, **kwargs: Any) -> str:
        result = self._resolve(file_path)
        if isinstance(result, str):
            return result
        path = result
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return f"Wrote {len(content)} characters to {file_path}"
        except Exception as e:
            return f"Error writing file: {e}"


class EditFileTool(WorkspaceTool):
    """通过 old_string / new_string 精准替换文件内容。"""

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "Edit a file by replacing an exact string. "
            "The old_string must match exactly (including whitespace/indentation). "
            "Use write_file to create new files."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "old_string": {"type": "string", "description": "Exact text to replace"},
                "new_string": {"type": "string", "description": "Replacement text"},
            },
            "required": ["file_path", "old_string", "new_string"],
        }

    async def execute(
        self, file_path: str, old_string: str, new_string: str, **kwargs: Any
    ) -> str:
        result = self._resolve(file_path)
        if isinstance(result, str):
            return result
        path = result
        try:
            if not path.exists():
                return f"Error: File not found: {file_path}"
            text = path.read_text(encoding="utf-8")
            count = text.count(old_string)
            if count == 0:
                return f"Error: old_string not found in {file_path}"
            if count > 1:
                return f"Error: old_string found {count} times in {file_path}; it must be unique"
            path.write_text(text.replace(old_string, new_string, 1), encoding="utf-8")
            return f"Edited {file_path}"
        except Exception as e:
            return f"Error editing file: {e}"


class ListDirTool(WorkspaceTool):
    """列出目录内容。"""

    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return "List files and directories at the given path (relative to workspace)."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "dir_path": {
                    "type": "string",
                    "description": "Relative path to list. Defaults to workspace root if empty.",
                },
            },
            "required": [],
        }

    async def execute(self, dir_path: str = "", **kwargs: Any) -> str:
        target = dir_path.strip() or "."
        result = self._resolve(target)
        if isinstance(result, str):
            return result
        path = result
        try:
            if not path.exists():
                return f"Error: Path not found: {dir_path}"
            if not path.is_dir():
                return f"Error: Not a directory: {dir_path}"
            entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
            lines = [f"{e.name}{'/' if e.is_dir() else ''}" for e in entries]
            return "\n".join(lines) if lines else "(empty directory)"
        except Exception as e:
            return f"Error listing directory: {e}"


# ---------------------------------------------------------------------------
# 精细化行操作工具
# ---------------------------------------------------------------------------

class SearchFilesTool(WorkspaceTool):
    """在工作区内搜索关键词，返回匹配行。"""

    @property
    def name(self) -> str:
        return "search_files"

    @property
    def description(self) -> str:
        return "Search for pattern in files within the workspace."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "file_pattern": {"type": "string"},
            },
            "required": ["pattern"],
        }

    async def execute(self, pattern: str, file_pattern: str = "*", **kwargs: Any) -> str:
        try:
            matches = []
            for path in self.workspace.rglob(file_pattern):
                if path.is_file():
                    try:
                        text = path.read_text(encoding="utf-8")
                        for i, line in enumerate(text.splitlines(), 1):
                            if pattern in line:
                                rel = path.relative_to(self.workspace)
                                matches.append(f"{rel}:{i}: {line.strip()}")
                    except Exception:
                        pass
            if not matches:
                return "No matches found"
            return "\n".join(matches[:100])
        except Exception as e:
            return f"Error searching: {e}"


class InsertLinesTool(WorkspaceTool):
    """在指定行后插入内容。"""

    @property
    def name(self) -> str:
        return "insert_lines"

    @property
    def description(self) -> str:
        return "Insert lines after specified line number."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "after_line": {"type": "integer", "minimum": 0},
                "content": {"type": "string"},
            },
            "required": ["file_path", "after_line", "content"],
        }

    async def execute(self, file_path: str, after_line: int, content: str, **kwargs: Any) -> str:
        result = self._resolve(file_path)
        if isinstance(result, str):
            return result
        path = result
        try:
            if not path.exists():
                return f"Error: File not found: {file_path}"
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            new_lines = [
                line + "\n" if not line.endswith("\n") else line
                for line in content.splitlines()
            ]
            if after_line < 0 or after_line > len(lines):
                return f"Error: Invalid line number {after_line}"
            lines[after_line:after_line] = new_lines
            path.write_text("".join(lines), encoding="utf-8")
            return f"Inserted {len(new_lines)} lines after line {after_line}"
        except Exception as e:
            return f"Error inserting lines: {e}"


class DeleteLinesTool(WorkspaceTool):
    """删除指定行范围（含边界）。"""

    @property
    def name(self) -> str:
        return "delete_lines"

    @property
    def description(self) -> str:
        return "Delete lines from start_line to end_line (inclusive)."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
            },
            "required": ["file_path", "start_line", "end_line"],
        }

    async def execute(self, file_path: str, start_line: int, end_line: int, **kwargs: Any) -> str:
        result = self._resolve(file_path)
        if isinstance(result, str):
            return result
        path = result
        try:
            if not path.exists():
                return f"Error: File not found: {file_path}"
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            start = start_line - 1
            end = end_line
            if start < 0 or end > len(lines) or start >= end:
                return f"Error: Invalid line range {start_line}-{end_line}"
            del lines[start:end]
            path.write_text("".join(lines), encoding="utf-8")
            return f"Deleted lines {start_line}-{end_line}"
        except Exception as e:
            return f"Error deleting lines: {e}"


class ReplaceLinesTool(WorkspaceTool):
    """替换指定行范围（含边界）。"""

    @property
    def name(self) -> str:
        return "replace_lines"

    @property
    def description(self) -> str:
        return "Replace lines from start_line to end_line (inclusive) with new content."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
                "content": {"type": "string"},
            },
            "required": ["file_path", "start_line", "end_line", "content"],
        }

    async def execute(
        self, file_path: str, start_line: int, end_line: int, content: str, **kwargs: Any
    ) -> str:
        result = self._resolve(file_path)
        if isinstance(result, str):
            return result
        path = result
        try:
            if not path.exists():
                return f"Error: File not found: {file_path}"
            old_lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            start = start_line - 1
            end = end_line
            if start < 0 or end > len(old_lines) or start >= end:
                return f"Error: Invalid line range {start_line}-{end_line}"
            new_lines = [
                line + "\n" if not line.endswith("\n") else line
                for line in content.splitlines()
            ]
            old_lines[start:end] = new_lines
            path.write_text("".join(old_lines), encoding="utf-8")
            return f"Replaced lines {start_line}-{end_line} with {len(new_lines)} new lines"
        except Exception as e:
            return f"Error replacing lines: {e}"


__all__ = [
    "WorkspaceTool",
    "ReadFileTool",
    "WriteFileTool",
    "EditFileTool",
    "ListDirTool",
    "SearchFilesTool",
    "InsertLinesTool",
    "DeleteLinesTool",
    "ReplaceLinesTool",
]
