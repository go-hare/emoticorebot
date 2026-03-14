from __future__ import annotations

import asyncio
from types import SimpleNamespace

from emoticorebot.brain.companion_brain import CompanionBrain


class _FakeTaskSystem:
    def __init__(self, *, active_snapshots=None, recent_snapshots=None, waiting_task=None) -> None:
        self.created_task_spec = None
        self.answered = None
        self._active_snapshots = list(active_snapshots or [])
        self._recent_snapshots = list(recent_snapshots or [])
        self._waiting_task = waiting_task

    async def create_central_task(self, task_spec):
        self.created_task_spec = dict(task_spec)

    def waiting_task(self):
        return self._waiting_task

    async def answer(self, content, task_id=None, *, origin_message_id=""):
        self.answered = {
            "content": content,
            "task_id": task_id,
            "origin_message_id": origin_message_id,
        }
        if self._waiting_task is not None:
            self._waiting_task.params["origin_message_id"] = origin_message_id
        return self._waiting_task is not None

    def active_tasks(self):
        return [SimpleNamespace(snapshot=lambda snapshot=snapshot: dict(snapshot)) for snapshot in self._active_snapshots]

    def recent_task_snapshots(self):
        return [dict(snapshot) for snapshot in self._recent_snapshots]

    def latest_task_snapshot(self):
        if self._active_snapshots:
            return dict(self._active_snapshots[-1])
        if self._recent_snapshots:
            return dict(self._recent_snapshots[-1])
        return None

    def get_tasks_summary(self):
        if not self._active_snapshots:
            return "当前没有正在执行的任务。"
        return "\n".join(
            f"- {snapshot.get('title', snapshot.get('task_id', '任务'))}: {snapshot.get('stage_info', '执行中')}"
            for snapshot in self._active_snapshots
        )


def test_build_tools_succeeds_with_langchain_tool_wrapper() -> None:
    brain = CompanionBrain(brain_llm=None, context_builder=None)

    tools = brain._build_tools(
        task_system=None,
        current_context={},
        channel="cli",
        chat_id="direct",
        session_id="cli:direct",
    )

    assert len(tools) == 4


def test_fast_dispatch_detects_explicit_code_task() -> None:
    fake_task_system = _FakeTaskSystem()

    assert CompanionBrain._should_fast_dispatch_task(
        "修改 sub.py 新增 subOne(a,b,c) 返回 a+b+c",
        fake_task_system,
    )


def test_fast_dispatch_skips_non_task_chat() -> None:
    fake_task_system = _FakeTaskSystem()

    assert not CompanionBrain._should_fast_dispatch_task(
        "你好呀",
        fake_task_system,
    )


def test_handle_user_message_fast_dispatch_creates_async_task() -> None:
    fake_task_system = _FakeTaskSystem()
    brain = CompanionBrain(brain_llm=None, context_builder=None)

    result = asyncio.run(
        brain.handle_user_message(
            user_input="修改 sub.py 新增 subOne(a,b,c) 返回 a+b+c",
            history=[],
            internal_history=[],
            emotion="平静",
            pad={"pleasure": 0.0, "arousal": 0.5, "dominance": 0.5},
            task_system=fake_task_system,
            message_id="msg_test",
            channel="cli",
            chat_id="direct",
            session_id="cli:direct",
            media=None,
        )
    )

    assert result["task_action"] == "create_task"
    assert result["final_decision"] == "continue"
    assert fake_task_system.created_task_spec is not None
    assert fake_task_system.created_task_spec["request"] == "修改 sub.py 新增 subOne(a,b,c) 返回 a+b+c"


def test_handle_user_message_task_status_query_uses_existing_task() -> None:
    fake_task_system = _FakeTaskSystem(
        active_snapshots=[
            {
                "task_id": "task_add",
                "title": "创建 add.py",
                "status": "running",
                "stage_info": "正在执行内部任务",
                "params": {"request": "请新增 add.py 新增方法 add(a,b) 返回 a+b"},
            }
        ]
    )
    brain = CompanionBrain(brain_llm=None, context_builder=None)

    result = asyncio.run(
        brain.handle_user_message(
            user_input="add 文件创建好了吗",
            history=[],
            internal_history=[],
            emotion="平静",
            pad={"pleasure": 0.0, "arousal": 0.5, "dominance": 0.5},
            task_system=fake_task_system,
            message_id="msg_status",
            channel="cli",
            chat_id="direct",
            session_id="cli:direct",
            media=None,
        )
    )

    assert result["task_action"] == "none"
    assert "创建 add.py" in result["final_message"]
    assert "还在处理中" in result["final_message"]


def test_handle_user_message_does_not_duplicate_existing_active_task() -> None:
    fake_task_system = _FakeTaskSystem(
        active_snapshots=[
            {
                "task_id": "task_add",
                "title": "创建 add.py",
                "status": "running",
                "stage_info": "正在执行内部任务",
                "params": {"request": "请新增 add.py 新增方法 add(a,b) 返回 a+b"},
            }
        ]
    )
    brain = CompanionBrain(brain_llm=None, context_builder=None)

    result = asyncio.run(
        brain.handle_user_message(
            user_input="请新增 add.py 新增方法 add(a,b) 返回 a+b",
            history=[],
            internal_history=[],
            emotion="平静",
            pad={"pleasure": 0.0, "arousal": 0.5, "dominance": 0.5},
            task_system=fake_task_system,
            message_id="msg_dup",
            channel="cli",
            chat_id="direct",
            session_id="cli:direct",
            media=None,
        )
    )

    assert fake_task_system.created_task_spec is None
    assert result["task_action"] == "none"
    assert "创建 add.py" in result["final_message"]


def test_handle_user_message_simple_greeting_fast_path() -> None:
    fake_task_system = _FakeTaskSystem()
    brain = CompanionBrain(brain_llm=None, context_builder=None)

    result = asyncio.run(
        brain.handle_user_message(
            user_input="你好",
            history=[],
            internal_history=[],
            emotion="平静",
            pad={"pleasure": 0.0, "arousal": 0.5, "dominance": 0.5},
            task_system=fake_task_system,
            message_id="msg_hello",
            channel="cli",
            chat_id="direct",
            session_id="cli:direct",
            media=None,
        )
    )

    assert result["task_action"] == "none"
    assert "你好呀" in result["final_message"]


def test_handle_user_message_simple_greeting_mentions_active_task() -> None:
    fake_task_system = _FakeTaskSystem(
        active_snapshots=[
            {
                "task_id": "task_add",
                "title": "创建 add.py",
                "status": "running",
                "stage_info": "正在执行内部任务",
                "params": {"request": "请新增 add.py 新增方法 add(a,b) 返回 a+b"},
            }
        ]
    )
    brain = CompanionBrain(brain_llm=None, context_builder=None)

    result = asyncio.run(
        brain.handle_user_message(
            user_input="你好",
            history=[],
            internal_history=[],
            emotion="平静",
            pad={"pleasure": 0.0, "arousal": 0.5, "dominance": 0.5},
            task_system=fake_task_system,
            message_id="msg_hello_task",
            channel="cli",
            chat_id="direct",
            session_id="cli:direct",
            media=None,
        )
    )

    assert "创建 add.py" in result["final_message"]


def test_handle_user_message_waiting_task_answer_uses_deterministic_fill_path() -> None:
    waiting_task = SimpleNamespace(
        task_id="task_weather",
        title="查询今日天气",
        params={
            "task_id": "task_weather",
            "title": "查询今日天气",
            "request": "查一下今天的天气",
            "history_context": "原始天气请求",
            "task_context": {},
        },
    )
    fake_task_system = _FakeTaskSystem(waiting_task=waiting_task)
    brain = CompanionBrain(brain_llm=None, context_builder=None)

    result = asyncio.run(
        brain.handle_user_message(
            user_input="杭州",
            history=[],
            internal_history=[],
            emotion="平静",
            pad={"pleasure": 0.0, "arousal": 0.5, "dominance": 0.5},
            task_system=fake_task_system,
            message_id="msg_fill",
            channel="cli",
            chat_id="direct",
            session_id="cli:direct",
            media=None,
        )
    )

    assert fake_task_system.answered is not None
    assert fake_task_system.answered["content"] == "杭州"
    assert fake_task_system.answered["task_id"] == "task_weather"
    assert fake_task_system.answered["origin_message_id"] == "msg_fill"
    assert result["task_action"] == "fill_task"
    assert result["final_decision"] == "continue"
    assert "查询今日天气" in result["final_message"]
