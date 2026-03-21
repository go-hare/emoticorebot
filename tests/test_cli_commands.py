from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
import asyncio
from pathlib import Path

from emoticorebot.cli import commands
from emoticorebot.cli.commands import (
    _are_one_shot_tasks_settled,
    _is_one_shot_task_settled,
    _pick_one_shot_task_id,
    _pick_one_shot_task_ids,
)
from emoticorebot.config.schema import Config
from emoticorebot.protocol.events import DeliveryTargetPayload
from emoticorebot.protocol.task_models import TaskRequestSpec
from emoticorebot.executor.state import ExecutorState
from emoticorebot.executor.store import ExecutorRecord, ExecutorStore
from emoticorebot.runtime.transport_bus import OutboundMessage


def _task(task_id: str, *, state: ExecutorState, updated_at: str = "2026-03-16T00:00:00Z", state_version: int = 1):
    return ExecutorRecord(
        task_id=task_id,
        session_id="cli:direct",
        turn_id="turn_1",
        job_id=f"job_{task_id}",
        request=TaskRequestSpec(request="test"),
        title=task_id,
        delivery_target=DeliveryTargetPayload(delivery_mode="push", channel="cli", chat_id="direct"),
        state=state,
        updated_at=updated_at,
        state_version=state_version,
    )


def test_pick_one_shot_task_id_prefers_fallback() -> None:
    store = ExecutorStore()
    store.add(_task("task_new", state=ExecutorState.RUNNING))
    agent_loop = SimpleNamespace(kernel=SimpleNamespace(task_store=store))

    assert _pick_one_shot_task_id(agent_loop, "cli:direct", set(), "task_resume") == "task_resume"


def test_pick_one_shot_task_id_returns_newest_new_task() -> None:
    store = ExecutorStore()
    store.add(_task("task_old", state=ExecutorState.DONE, updated_at="2026-03-16T00:00:00Z"))
    store.add(_task("task_newer", state=ExecutorState.RUNNING, updated_at="2026-03-16T00:00:02Z"))
    store.add(_task("task_newest", state=ExecutorState.RUNNING, updated_at="2026-03-16T00:00:03Z"))
    agent_loop = SimpleNamespace(kernel=SimpleNamespace(task_store=store))

    assert _pick_one_shot_task_id(agent_loop, "cli:direct", {"task_old"}, None) == "task_newest"


def test_is_one_shot_task_settled_accepts_terminal_only() -> None:
    done = _task("task_done", state=ExecutorState.DONE)
    running = _task("task_running", state=ExecutorState.RUNNING)
    tasks = {
        done.task_id: done,
        running.task_id: running,
    }
    agent_loop = SimpleNamespace(kernel=SimpleNamespace(get_task=tasks.get))

    assert _is_one_shot_task_settled(agent_loop, "task_done") is True
    assert _is_one_shot_task_settled(agent_loop, "task_running") is False


def test_pick_one_shot_task_ids_collects_all_new_tasks() -> None:
    store = ExecutorStore()
    store.add(_task("task_old", state=ExecutorState.DONE, updated_at="2026-03-16T00:00:00Z"))
    store.add(_task("task_newer", state=ExecutorState.RUNNING, updated_at="2026-03-16T00:00:02Z"))
    store.add(_task("task_newest", state=ExecutorState.RUNNING, updated_at="2026-03-16T00:00:03Z"))
    agent_loop = SimpleNamespace(kernel=SimpleNamespace(task_store=store))

    assert _pick_one_shot_task_ids(agent_loop, "cli:direct", {"task_old"}, None) == {
        "task_newer",
        "task_newest",
    }


def test_are_one_shot_tasks_settled_requires_all_terminal() -> None:
    done = _task("task_done", state=ExecutorState.DONE)
    running = _task("task_running", state=ExecutorState.RUNNING)
    tasks = {
        done.task_id: done,
        running.task_id: running,
    }
    agent_loop = SimpleNamespace(kernel=SimpleNamespace(get_task=tasks.get))

    assert _are_one_shot_tasks_settled(agent_loop, {"task_done"}) is True
    assert _are_one_shot_tasks_settled(agent_loop, {"task_done", "task_running"}) is False


def test_agent_one_shot_prints_task_result_after_stream(monkeypatch, tmp_path) -> None:
    store = ExecutorStore()
    streamed_chunks: list[str] = []
    printed_responses: list[str] = []

    fake_config = SimpleNamespace(
        workspace_path=tmp_path,
        agents=SimpleNamespace(
            defaults=SimpleNamespace(
                executor_mode=SimpleNamespace(memory_window=0),
                brain_mode=SimpleNamespace(memory_window=0),
            )
        ),
        providers=None,
        memory=None,
        tools=SimpleNamespace(
            web=SimpleNamespace(search=SimpleNamespace(api_key=None)),
            exec=None,
            restrict_to_workspace=False,
            mcp_servers={},
        ),
        channels=None,
    )

    class _FakeCronService:
        def __init__(self, _path) -> None:
            self.on_job = None

    class _FakeRuntimeHost:
        def __init__(self, *, bus, **_kwargs) -> None:
            self._bus = bus
            self.kernel = SimpleNamespace(task_store=store, get_task=store.get)
            self.channels_config = None

        async def process_direct(
            self,
            _content: str,
            *,
            session_key: str,
            channel: str,
            chat_id: str,
            deliver: bool,
            message_id: str | None,
        ) -> str:
            assert deliver is True
            task = _task("task_streamed", state=ExecutorState.RUNNING)
            task.session_id = session_key
            store.add(task)
            await self._bus.publish_outbound(
                OutboundMessage(
                    channel=channel,
                    chat_id=chat_id,
                    content="好的，我来处理。",
                    reply_to=message_id,
                    metadata={
                        "reply_kind": "answer",
                        "_stream": True,
                        "_stream_id": "stream_1",
                        "_stream_state": "open",
                    },
                )
            )
            await self._bus.publish_outbound(
                OutboundMessage(
                    channel=channel,
                    chat_id=chat_id,
                    content="好的，我来处理。",
                    reply_to=message_id,
                    metadata={
                        "reply_kind": "status",
                        "_stream": True,
                        "_stream_id": "stream_1",
                        "_stream_state": "close",
                    },
                )
            )
            await asyncio.sleep(0)
            task.state = ExecutorState.DONE
            task.touch()
            await self._bus.publish_outbound(
                OutboundMessage(
                    channel=channel,
                    chat_id=chat_id,
                    content="任务已完成。",
                    reply_to=message_id,
                    metadata={
                        "reply_kind": "answer",
                        "task_id": task.task_id,
                    },
                )
            )
            return "好的，我来处理。"

        def stop(self) -> None:
            return None

        async def close_mcp(self) -> None:
            return None

    monkeypatch.setattr("emoticorebot.config.loader.load_config", lambda: fake_config)
    monkeypatch.setattr("emoticorebot.config.loader.get_data_dir", lambda: tmp_path)
    monkeypatch.setattr("emoticorebot.bootstrap.RuntimeHost", _FakeRuntimeHost)
    monkeypatch.setattr("emoticorebot.cron.service.CronService", _FakeCronService)
    monkeypatch.setattr(commands.console, "status", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(
        commands,
        "_write_stream_chunk",
        lambda *, content, render_markdown, stream_started: streamed_chunks.append(content),
    )
    monkeypatch.setattr(commands, "_finish_stream_output", lambda: streamed_chunks.append("<finish>"))
    monkeypatch.setattr(
        commands,
        "_print_agent_response",
        lambda response, render_markdown: printed_responses.append(response),
    )

    commands.agent(
        message="创建文件",
        session_id="cli:test-stream",
        markdown=False,
        logs=False,
    )

    assert streamed_chunks == ["好的，我来处理。", "<finish>"]
    assert printed_responses == ["任务已完成。"]


def test_agent_one_shot_waits_for_all_parallel_task_results(monkeypatch, tmp_path) -> None:
    store = ExecutorStore()
    printed_responses: list[str] = []

    fake_config = SimpleNamespace(
        workspace_path=tmp_path,
        agents=SimpleNamespace(
            defaults=SimpleNamespace(
                executor_mode=SimpleNamespace(memory_window=0),
                brain_mode=SimpleNamespace(memory_window=0),
            )
        ),
        providers=None,
        memory=None,
        tools=SimpleNamespace(
            web=SimpleNamespace(search=SimpleNamespace(api_key=None)),
            exec=None,
            restrict_to_workspace=False,
            mcp_servers={},
        ),
        channels=None,
    )

    class _FakeCronService:
        def __init__(self, _path) -> None:
            self.on_job = None

    class _FakeRuntimeHost:
        def __init__(self, *, bus, **_kwargs) -> None:
            self._bus = bus
            self.kernel = SimpleNamespace(task_store=store, get_task=store.get)
            self.channels_config = None

        async def process_direct(
            self,
            _content: str,
            *,
            session_key: str,
            channel: str,
            chat_id: str,
            deliver: bool,
            message_id: str | None,
        ) -> str:
            assert deliver is True
            task_a = _task("task_parallel_a", state=ExecutorState.RUNNING)
            task_a.session_id = session_key
            task_b = _task("task_parallel_b", state=ExecutorState.RUNNING, updated_at="2026-03-16T00:00:01Z")
            task_b.session_id = session_key
            store.add(task_a)
            store.add(task_b)
            await self._bus.publish_outbound(
                OutboundMessage(
                    channel=channel,
                    chat_id=chat_id,
                    content="开始并行处理。",
                    reply_to=message_id,
                    metadata={"reply_kind": "status"},
                )
            )
            await asyncio.sleep(0)
            task_a.state = ExecutorState.DONE
            task_a.touch()
            await self._bus.publish_outbound(
                OutboundMessage(
                    channel=channel,
                    chat_id=chat_id,
                    content="任务 A 完成。",
                    reply_to=message_id,
                    metadata={
                        "reply_kind": "answer",
                        "task_id": task_a.task_id,
                    },
                )
            )
            await asyncio.sleep(0)
            task_b.state = ExecutorState.DONE
            task_b.touch()
            await self._bus.publish_outbound(
                OutboundMessage(
                    channel=channel,
                    chat_id=chat_id,
                    content="任务 B 完成。",
                    reply_to=message_id,
                    metadata={
                        "reply_kind": "answer",
                        "task_id": task_b.task_id,
                    },
                )
            )
            return "开始并行处理。"

        def stop(self) -> None:
            return None

        async def close_mcp(self) -> None:
            return None

    monkeypatch.setattr("emoticorebot.config.loader.load_config", lambda: fake_config)
    monkeypatch.setattr("emoticorebot.config.loader.get_data_dir", lambda: tmp_path)
    monkeypatch.setattr("emoticorebot.bootstrap.RuntimeHost", _FakeRuntimeHost)
    monkeypatch.setattr("emoticorebot.cron.service.CronService", _FakeCronService)
    monkeypatch.setattr(commands.console, "status", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(
        commands,
        "_print_agent_response",
        lambda response, render_markdown: printed_responses.append(response),
    )

    commands.agent(
        message="并行创建两个文件",
        session_id="cli:test-parallel",
        markdown=False,
        logs=False,
    )

    assert printed_responses == ["任务 B 完成。"]


def test_status_prints_brain_and_executor_models(monkeypatch, tmp_path) -> None:
    printed: list[str] = []
    config_path = Path(tmp_path) / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr("emoticorebot.config.loader.get_config_path", lambda: config_path)
    monkeypatch.setattr("emoticorebot.config.loader.load_config", lambda: Config())
    monkeypatch.setattr(commands.console, "print", lambda *args, **kwargs: printed.append(" ".join(str(arg) for arg in args)))

    commands.status()

    assert any("Brain Model: anthropic/claude-opus-4-5" in line for line in printed)
    assert any("Executor Model: anthropic/claude-opus-4-5" in line for line in printed)


def test_interactive_console_disables_ansi_sequences() -> None:
    interactive_console = commands._interactive_console()

    assert interactive_console.color_system is None
    assert interactive_console.no_color is True
