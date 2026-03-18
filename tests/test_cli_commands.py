from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
import asyncio
from pathlib import Path

from emoticorebot.cli import commands
from emoticorebot.cli.commands import _is_one_shot_task_settled, _pick_one_shot_task_id
from emoticorebot.config.schema import Config
from emoticorebot.protocol.task_models import TaskRequestSpec
from emoticorebot.runtime.state_machine import TaskState
from emoticorebot.runtime.task_store import RuntimeTaskRecord, TaskStore
from emoticorebot.runtime.transport_bus import OutboundMessage


def _task(task_id: str, *, state: TaskState, updated_at: str = "2026-03-16T00:00:00Z", state_version: int = 1):
    return RuntimeTaskRecord(
        task_id=task_id,
        session_id="cli:direct",
        turn_id="turn_1",
        request=TaskRequestSpec(request="test"),
        origin_message=None,
        title=task_id,
        state=state,
        updated_at=updated_at,
        state_version=state_version,
    )


def test_pick_one_shot_task_id_prefers_fallback() -> None:
    store = TaskStore()
    store.add(_task("task_new", state=TaskState.RUNNING))
    agent_loop = SimpleNamespace(kernel=SimpleNamespace(task_store=store))

    assert _pick_one_shot_task_id(agent_loop, "cli:direct", set(), "task_resume") == "task_resume"


def test_pick_one_shot_task_id_returns_newest_new_task() -> None:
    store = TaskStore()
    store.add(_task("task_old", state=TaskState.DONE, updated_at="2026-03-16T00:00:00Z"))
    store.add(_task("task_newer", state=TaskState.RUNNING, updated_at="2026-03-16T00:00:02Z"))
    store.add(_task("task_newest", state=TaskState.RUNNING, updated_at="2026-03-16T00:00:03Z"))
    agent_loop = SimpleNamespace(kernel=SimpleNamespace(task_store=store))

    assert _pick_one_shot_task_id(agent_loop, "cli:direct", {"task_old"}, None) == "task_newest"


def test_is_one_shot_task_settled_accepts_waiting_input_and_terminal() -> None:
    waiting = _task("task_waiting", state=TaskState.WAITING)
    done = _task("task_done", state=TaskState.DONE)
    running = _task("task_running", state=TaskState.RUNNING)
    tasks = {
        waiting.task_id: waiting,
        done.task_id: done,
        running.task_id: running,
    }
    agent_loop = SimpleNamespace(kernel=SimpleNamespace(get_task=tasks.get))

    assert _is_one_shot_task_settled(agent_loop, "task_waiting") is True
    assert _is_one_shot_task_settled(agent_loop, "task_done") is True
    assert _is_one_shot_task_settled(agent_loop, "task_running") is False


def test_agent_one_shot_prints_task_result_after_stream(monkeypatch, tmp_path) -> None:
    store = TaskStore()
    streamed_chunks: list[str] = []
    printed_responses: list[str] = []

    fake_config = SimpleNamespace(
        workspace_path=tmp_path,
        agents=SimpleNamespace(
            defaults=SimpleNamespace(
                worker_mode=SimpleNamespace(memory_window=0),
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
            task = _task("task_streamed", state=TaskState.RUNNING)
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
            task.state = TaskState.DONE
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


def test_status_prints_brain_and_worker_models(monkeypatch, tmp_path) -> None:
    printed: list[str] = []
    config_path = Path(tmp_path) / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr("emoticorebot.config.loader.get_config_path", lambda: config_path)
    monkeypatch.setattr("emoticorebot.config.loader.load_config", lambda: Config())
    monkeypatch.setattr(commands.console, "print", lambda *args, **kwargs: printed.append(" ".join(str(arg) for arg in args)))

    commands.status()

    assert any("Brain Model: anthropic/claude-opus-4-5" in line for line in printed)
    assert any("Worker Model: anthropic/claude-opus-4-5" in line for line in printed)
