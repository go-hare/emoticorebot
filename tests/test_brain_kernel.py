from __future__ import annotations

import asyncio
from pathlib import Path

from langchain_core.messages import AIMessage

from emoticorebot.brain_kernel import (
    BrainOutputType,
    BrainKernel,
    ClientTool,
    ChildToolRule,
    CognitiveEvent,
    FrontEvent,
    FunctionTool,
    InitToolRule,
    JsonlMemoryStore,
    MemoryPatch,
    MemoryView,
    RunStatus,
    SleepAgent,
    SleepEvent,
    SleepOutcome,
    TaskType,
    ToolCallNode,
)


def test_init_rule_restricts_first_tool() -> None:
    kernel = BrainKernel(tool_rules=[InitToolRule(tool_name="read_file")])
    context = kernel.build_turn_context(
        conversation_id="conv",
        input_kind="user",
        input_text="hello",
        memory=MemoryView(),
        tool_solver=kernel._make_tool_solver(),
        available_tools=["read_file", "write_file"],
    )
    assert context.allowed_tools == ["read_file"]


def test_run_store_tracks_lifecycle() -> None:
    kernel = BrainKernel(agent_id="alice")
    run = kernel.create_run(conversation_id="conv", goal="summarize")
    assert run.status == RunStatus.created
    assert run.agent_id == "alice"

    run = kernel.mark_run_running(run.id, current_tool="read_file")
    assert run.status == RunStatus.running
    assert run.current_tool == "read_file"

    run = kernel.finish_run(run.id, result_summary="done")
    assert run.status == RunStatus.completed
    assert run.result_summary == "done"


def test_child_tool_rule_prefills_after_parent_call() -> None:
    kernel = BrainKernel(
        tool_rules=[
            InitToolRule(tool_name="plan"),
            ChildToolRule(
                tool_name="plan",
                children=["execute"],
                child_arg_nodes=[ToolCallNode(name="execute", args={"mode": "fast"})],
            ),
        ]
    )
    solver = kernel._make_tool_solver()
    solver.register_tool_call("plan")
    context = kernel.build_turn_context(
        conversation_id="conv",
        input_kind="user",
        input_text="do it",
        memory=MemoryView(),
        tool_solver=solver,
        available_tools=["plan", "execute"],
    )
    assert context.allowed_tools == ["execute"]
    assert solver.get_prefilled_args("execute") == {"mode": "fast"}


def test_sleep_agent_writes_long_term_memory(tmp_path: Path) -> None:
    memory_store = JsonlMemoryStore(tmp_path)
    agent = SleepAgent(memory_store=memory_store)

    outcome = asyncio.run(
        agent.run_for_turn(
            agent_id="alice",
            conversation_id="conv",
            user_id="user",
            turn_id="turn",
            latest_user_text="我喜欢机器人，也想让它帮我整理文件",
            latest_front_reply="我会记住这个偏好",
        )
    )

    assert outcome.memory_candidates
    assert memory_store.long_term_path.exists()


def test_sleep_agent_builds_unified_digest(tmp_path: Path) -> None:
    memory_store = JsonlMemoryStore(tmp_path)
    agent = SleepAgent(memory_store=memory_store)
    memory_store.append_front_record(
        "conv",
        {
            "agent_id": "alice",
            "event_type": "dialogue",
            "emotion": "warm",
            "tags": ["relationship"],
            "content": "user=以后叫我阿青 | front=好呀，我记住了",
        },
    )
    memory_store.append_brain_record(
        "conv",
        {
            "agent_id": "alice",
            "role": "user",
            "turn_id": "turn_1",
            "content": "帮我整理文件",
        },
    )
    memory_store.append_tool_record(
        "conv",
        {
            "agent_id": "alice",
            "tool_name": "read_file",
            "content": "ok",
        },
    )

    digest = agent.build_digest(
        agent_id="alice",
        conversation_id="conv",
        user_id="user",
        turn_id="turn_1",
        latest_user_text="帮我整理文件",
        latest_front_reply="好呀，我来处理",
    )

    assert digest.front_events
    assert digest.kernel_events
    assert isinstance(digest.front_events[0], SleepEvent)
    assert digest.front_events[0].source == "front"
    assert digest.kernel_events[-1].source == "kernel"
    assert any(event.event_type == "tool" for event in digest.kernel_events)


class FakeSleepStructuredModel:
    async def ainvoke(self, messages):
        self.messages = messages
        return {
            "summary": "llm reflection summary",
            "memory_candidates": [
                {
                    "memory_type": "reflection",
                    "summary": "用户想要陪伴感机器人",
                    "detail": "front and kernel both point to companion preference",
                    "confidence": 0.82,
                    "stability": 0.71,
                    "tags": ["robot", "companion"],
                }
            ],
            "user_updates": ["我想要有陪伴感的机器人"],
            "soul_updates": ["对陪伴感需求保持高敏感"],
            "notes": "llm reflection",
        }


class FakeSleepModel:
    def with_structured_output(self, schema):
        self.schema = schema
        self.structured = FakeSleepStructuredModel()
        return self.structured


def test_sleep_agent_can_use_llm_model_for_reflection(tmp_path: Path) -> None:
    memory_store = JsonlMemoryStore(tmp_path)
    model = FakeSleepModel()
    agent = SleepAgent(memory_store=memory_store, model=model)

    outcome = asyncio.run(
        agent.run_for_turn(
            agent_id="alice",
            conversation_id="conv",
            user_id="user",
            turn_id="turn",
            latest_user_text="我想要有陪伴感的机器人",
            latest_front_reply="我会一直陪着你",
        )
    )

    assert outcome.summary == "llm reflection summary"
    assert outcome.memory_candidates
    assert outcome.memory_candidates[0].memory_type == "reflection"
    assert outcome.memory_candidates[0].metadata["source"] == "llm_reflection"
    assert outcome.user_updates == ["我想要有陪伴感的机器人"]
    assert outcome.soul_updates == ["对陪伴感需求保持高敏感"]
    assert memory_store.long_term_path.exists()
    assert "我想要有陪伴感的机器人" in (tmp_path / "USER.md").read_text(encoding="utf-8")
    assert "对陪伴感需求保持高敏感" in (tmp_path / "SOUL.md").read_text(encoding="utf-8")


def test_memory_store_builds_three_layer_memory_view(tmp_path: Path) -> None:
    memory_store = JsonlMemoryStore(tmp_path)
    memory_store.append_brain_record(
        "conv",
        {
            "agent_id": "alice",
            "role": "user",
            "content": "你好",
        },
    )
    memory_store.append_tool_record(
        "conv",
        {
            "agent_id": "alice",
            "tool_name": "read_file",
            "content": "ok",
        },
    )
    memory_store.append_front_record(
        "conv",
        {
            "agent_id": "alice",
            "event_type": "dialogue",
            "content": "user=以后叫我阿青 | front=好呀，我记住了",
        },
    )
    memory_store.append_patch(
        MemoryPatch(
            cognitive_append=[
                CognitiveEvent(
                    event_id="cog_1",
                    agent_id="alice",
                    conversation_id="conv",
                    turn_id="turn_1",
                    summary="用户在确认机器人主干架构",
                    outcome="direct_reply",
                    reason="完成本轮理解",
                )
            ]
        )
    )

    view = memory_store.build_memory_view("conv", "alice", "机器人架构")

    assert view.raw_layer["recent_dialogue"]
    assert view.raw_layer["recent_front_events"]
    assert view.raw_layer["recent_tools"]
    assert view.cognitive_layer
    assert view.cognitive_layer[0]["summary"] == "用户在确认机器人主干架构"


def test_append_patch_handles_existing_jsonl_without_trailing_newline(tmp_path: Path) -> None:
    memory_store = JsonlMemoryStore(tmp_path)
    memory_store.long_term_path.write_text(
        '{"record_id":"mem_old","summary":"old","memory_candidates":[],"user_updates":[],"soul_updates":[]}',
        encoding="utf-8",
    )

    memory_store.append_patch(
        MemoryPatch(
            long_term_append=[
                {
                    "record_id": "mem_new",
                    "summary": "new",
                    "memory_candidates": [],
                    "user_updates": ["喜欢极简设计"],
                    "soul_updates": [],
                }
            ]
        )
    )

    rows = memory_store.query_long_term(query="极简设计", agent_id="", limit=10)
    text = memory_store.long_term_path.read_text(encoding="utf-8")

    assert '\n{"record_id": "mem_new"' in text
    assert "喜欢极简设计" in (tmp_path / "USER.md").read_text(encoding="utf-8")
    assert rows == []


def test_refresh_projections_keeps_existing_soul_without_updates(tmp_path: Path) -> None:
    memory_store = JsonlMemoryStore(tmp_path)
    soul_path = tmp_path / "SOUL.md"
    soul_original = "# SOUL\n\n- baseline persona\n"
    soul_path.write_text(soul_original, encoding="utf-8")

    memory_store.append_patch(
        MemoryPatch(
            long_term_append=[
                {
                    "record_id": "mem_new",
                    "summary": "new",
                    "memory_candidates": [],
                    "user_updates": ["喜欢极简设计"],
                    "soul_updates": [],
                }
            ]
        )
    )

    assert soul_path.read_text(encoding="utf-8") == soul_original


def test_refresh_projections_merges_updates_without_overwrite(tmp_path: Path) -> None:
    memory_store = JsonlMemoryStore(tmp_path)
    soul_path = tmp_path / "SOUL.md"
    soul_original = "# SOUL\n\n> keep this note\n\n- baseline persona\n"
    soul_path.write_text(soul_original, encoding="utf-8")

    memory_store.append_patch(
        MemoryPatch(
            long_term_append=[
                {
                    "record_id": "mem_new_1",
                    "summary": "new",
                    "memory_candidates": [],
                    "user_updates": [],
                    "soul_updates": ["对边界感保持高敏感"],
                }
            ]
        )
    )

    first = soul_path.read_text(encoding="utf-8")
    assert "> keep this note" in first
    assert "- baseline persona" in first
    assert "- 对边界感保持高敏感" in first

    memory_store.append_patch(
        MemoryPatch(
            long_term_append=[
                {
                    "record_id": "mem_new_2",
                    "summary": "new",
                    "memory_candidates": [],
                    "user_updates": [],
                    "soul_updates": ["对边界感保持高敏感"],
                }
            ]
        )
    )

    second = soul_path.read_text(encoding="utf-8")
    assert second.count("- 对边界感保持高敏感") == 1


def test_brain_kernel_can_store_front_event(tmp_path: Path) -> None:
    memory_store = JsonlMemoryStore(tmp_path)
    kernel = BrainKernel(agent_id="alice", memory_store=memory_store)

    event = asyncio.run(
        kernel.handle_front_event(
            conversation_id="conv",
            user_id="user",
            turn_id="front_turn",
            front_event=FrontEvent(
                event_type="dialogue",
                user_text="以后叫我阿青",
                front_reply="好呀，我记住了",
                emotion="warm",
                tags=["relationship"],
            ),
        )
    )

    assert event.event_type == "dialogue"
    rows = memory_store.recent_front_records("conv", 10)
    assert rows
    assert rows[0]["user_text"] == "以后叫我阿青"
    assert rows[0]["front_reply"] == "好呀，我记住了"
    assert rows[0]["emotion"] == "warm"
    view = memory_store.build_memory_view("conv", "alice", "阿青")
    assert view.raw_layer["recent_front_events"]


def test_brain_kernel_can_wait_for_front_reply_for_same_turn(tmp_path: Path) -> None:
    memory_store = JsonlMemoryStore(tmp_path)
    kernel = BrainKernel(agent_id="alice", memory_store=memory_store)

    async def _exercise() -> str:
        kernel._front_reply_events[("conv", "turn_1")] = asyncio.Event()

        async def _publish() -> None:
            await asyncio.sleep(0.05)
            await kernel.handle_front_event(
                conversation_id="conv",
                user_id="user",
                turn_id="turn_1",
                front_event=FrontEvent(
                    event_type="dialogue",
                    user_text="以后叫我阿青",
                    front_reply="好呀，我记住了",
                    emotion="warm",
                    tags=["relationship"],
                ),
            )

        task = asyncio.create_task(_publish())
        try:
            return await kernel._resolve_sleep_front_reply(
                conversation_id="conv",
                turn_id="turn_1",
                latest_front_reply="",
                timeout=1.0,
            )
        finally:
            await task

    reply = asyncio.run(_exercise())

    assert reply == "好呀，我记住了"


def test_brain_kernel_assigns_default_model_to_sleep_agent(tmp_path: Path) -> None:
    memory_store = JsonlMemoryStore(tmp_path)
    model = FakeSleepModel()
    sleep_agent = SleepAgent(memory_store=memory_store)

    _kernel = BrainKernel(agent_id="alice", model=model, memory_store=memory_store, sleep_agent=sleep_agent)

    assert sleep_agent.model is model


class FakeLocalToolModel:
    def bind_tools(self, tools):
        self.tools = tools
        return self

    async def ainvoke(self, messages):
        has_tool_result = any(getattr(message, "type", "") == "tool" for message in messages)
        if not has_tool_result:
            return AIMessage(
                content="",
                tool_calls=[{"id": "call_1", "name": "remember_note", "args": {"note": "整理文件偏好"}}],
            )
        return AIMessage(content="已经记住，我之后会按这个偏好行动。")


async def _remember_note(note: str) -> str:
    return f"saved:{note}"


def test_brain_kernel_can_execute_local_tool_loop(tmp_path: Path) -> None:
    memory_store = JsonlMemoryStore(tmp_path)
    sleep_agent = SleepAgent(memory_store=memory_store)
    model = FakeLocalToolModel()
    tool = FunctionTool(
        name="remember_note",
        description="Store a user preference note.",
        parameters={
            "type": "object",
            "properties": {"note": {"type": "string"}},
            "required": ["note"],
        },
        func=_remember_note,
    )
    kernel = BrainKernel(agent_id="alice", model=model, memory_store=memory_store, sleep_agent=sleep_agent)

    result = asyncio.run(
        kernel.handle_user_input(
            conversation_id="conv",
            user_id="user",
            turn_id="turn",
            text="帮我记住我喜欢把文件整理整齐",
            tools=[tool],
        )
    )

    assert result.reply == "已经记住，我之后会按这个偏好行动。"
    assert result.tool_trace[0].tool_name == "remember_note"
    assert result.run.status == RunStatus.completed
    assert memory_store.recent_tool_records("conv", 10)
    assert memory_store.recent_cognitive_events("conv", 10)


class FakeClientToolModel:
    def bind_tools(self, tools):
        self.tools = tools
        return self

    async def ainvoke(self, messages):
        has_tool_result = any(getattr(message, "type", "") == "tool" for message in messages)
        if not has_tool_result:
            return AIMessage(
                content="",
                tool_calls=[{"id": "client_1", "name": "robot_wave", "args": {"speed": "slow"}}],
            )
        return AIMessage(content="机器人已经挥手了。")


class BlockingConversationModel:
    def __init__(self) -> None:
        self.slow_started = asyncio.Event()
        self.release_slow = asyncio.Event()

    def bind_tools(self, tools):
        self.tools = tools
        return self

    async def ainvoke(self, messages):
        prompt = str(messages[-1].content)
        if "slow turn" in prompt:
            self.slow_started.set()
            await self.release_slow.wait()
            return AIMessage(content="slow done")
        return AIMessage(content="fast done")


class ImmediateReplyModel:
    def bind_tools(self, tools):
        self.tools = tools
        return self

    async def ainvoke(self, messages):
        return AIMessage(content="前台回复先返回。")


class FakeTaskRouterModel:
    def __init__(self, task_type: TaskType) -> None:
        self.task_type = task_type

    def with_structured_output(self, schema):
        task_type = self.task_type

        class _Structured:
            async def ainvoke(self, messages):
                _ = messages
                return schema(task_type=task_type)

        return _Structured()


class FrontAwareTaskRouterModel:
    def with_structured_output(self, schema):
        class _Structured:
            async def ainvoke(self, messages):
                merged = "\n".join(str(getattr(msg, "content", msg)) for msg in messages)
                if "## Latest Front Reply" in merged and "今天是3月25号" in merged:
                    return schema(task_type=TaskType.none)
                return schema(task_type=TaskType.simple)

        return _Structured()


def test_brain_kernel_can_pause_for_client_tool_and_resume(tmp_path: Path) -> None:
    memory_store = JsonlMemoryStore(tmp_path)
    model = FakeClientToolModel()
    kernel = BrainKernel(agent_id="alice", model=model, memory_store=memory_store)
    tool = ClientTool(
        name="robot_wave",
        description="Wave the robot arm.",
        parameters={
            "type": "object",
            "properties": {"speed": {"type": "string"}},
            "required": ["speed"],
        },
    )

    first = asyncio.run(
        kernel.handle_user_input(
            conversation_id="conv",
            text="跟我挥挥手",
            tools=[tool],
        )
    )

    assert not first.reply
    assert first.run.status == RunStatus.running
    assert first.pending_tool_calls[0].tool_name == "robot_wave"

    second = asyncio.run(
        kernel.handle_tool_results(
            run_id=first.run.id,
            tool_results={
                "tool_call_id": first.pending_tool_calls[0].tool_call_id,
                "tool_name": "robot_wave",
                "result": "ok",
                "success": True,
            },
        )
    )

    assert second.reply == "机器人已经挥手了。"
    assert second.run.status == RunStatus.completed
    assert second.tool_trace[0].tool_name == "robot_wave"


def test_brain_kernel_can_promote_new_task_and_demote_previous_foreground(tmp_path: Path) -> None:
    memory_store = JsonlMemoryStore(tmp_path)
    model = FakeClientToolModel()
    kernel = BrainKernel(agent_id="alice", model=model, memory_store=memory_store)
    tool = ClientTool(
        name="robot_wave",
        description="Wave the robot arm.",
        parameters={
            "type": "object",
            "properties": {"speed": {"type": "string"}},
            "required": ["speed"],
        },
    )

    first = asyncio.run(
        kernel.handle_user_input(
            conversation_id="conv",
            text="帮我整理下载目录",
            tools=[tool],
        )
    )
    assert first.conversation is not None
    assert first.conversation.foreground_run_id == first.run.id
    assert first.run.background is False

    second = asyncio.run(
        kernel.handle_user_input(
            conversation_id="conv",
            text="另外帮我总结会议纪要",
            tools=[tool],
        )
    )

    assert second.run.id != first.run.id
    assert second.conversation is not None
    assert second.conversation.foreground_run_id == second.run.id
    assert first.run.id in second.conversation.background_run_ids
    assert second.run.background is False
    previous = kernel.get_run(first.run.id)
    assert previous is not None
    assert previous.background is True
    assert second.route is not None
    assert second.route.kind == "start_foreground"


def test_brain_kernel_can_queue_background_task_without_stealing_foreground(tmp_path: Path) -> None:
    memory_store = JsonlMemoryStore(tmp_path)
    model = FakeClientToolModel()
    kernel = BrainKernel(agent_id="alice", model=model, memory_store=memory_store)
    tool = ClientTool(
        name="robot_wave",
        description="Wave the robot arm.",
        parameters={
            "type": "object",
            "properties": {"speed": {"type": "string"}},
            "required": ["speed"],
        },
    )

    first = asyncio.run(
        kernel.handle_user_input(
            conversation_id="conv",
            text="帮我整理下载目录",
            tools=[tool],
        )
    )
    second = asyncio.run(
        kernel.handle_user_input(
            conversation_id="conv",
            text="顺便再帮我总结会议纪要",
            tools=[tool],
            metadata={"new_task": True, "queue_only": True},
        )
    )

    assert second.run.id != first.run.id
    assert second.conversation is not None
    assert second.conversation.foreground_run_id == first.run.id
    assert second.run.background is True
    assert second.run.id in second.conversation.background_run_ids
    assert second.route is not None
    assert second.route.kind == "start_background"


def test_brain_kernel_does_not_create_run_for_none_task_type(tmp_path: Path) -> None:
    memory_store = JsonlMemoryStore(tmp_path)
    kernel = BrainKernel(
        agent_id="alice",
        model=ImmediateReplyModel(),
        task_router_model=FakeTaskRouterModel(TaskType.none),
        memory_store=memory_store,
    )

    result = asyncio.run(
        kernel.handle_user_input(
            conversation_id="conv",
            text="以后叫我阿青",
        )
    )

    assert result.task_type == TaskType.none
    assert result.run is None
    assert result.context is not None
    assert result.context.input_text == "以后叫我阿青"
    assert result.conversation is not None
    assert result.conversation.active_run_ids == []
    assert kernel.list_runs("conv") == []


def test_brain_kernel_can_skip_kernel_when_front_already_answered(tmp_path: Path) -> None:
    memory_store = JsonlMemoryStore(tmp_path)
    kernel = BrainKernel(
        agent_id="alice",
        model=ImmediateReplyModel(),
        task_router_model=FrontAwareTaskRouterModel(),
        memory_store=memory_store,
    )

    result = asyncio.run(
        kernel.handle_user_input(
            conversation_id="conv",
            text="今天几号",
            latest_front_reply="今天是3月25号。",
        )
    )

    assert result.task_type == TaskType.none
    assert result.run is None
    assert kernel.list_runs("conv") == []


def test_brain_kernel_can_switch_foreground_run(tmp_path: Path) -> None:
    memory_store = JsonlMemoryStore(tmp_path)
    model = FakeClientToolModel()
    kernel = BrainKernel(agent_id="alice", model=model, memory_store=memory_store)
    tool = ClientTool(
        name="robot_wave",
        description="Wave the robot arm.",
        parameters={
            "type": "object",
            "properties": {"speed": {"type": "string"}},
            "required": ["speed"],
        },
    )

    first = asyncio.run(
        kernel.handle_user_input(
            conversation_id="conv",
            text="帮我整理下载目录",
            tools=[tool],
        )
    )
    second = asyncio.run(
        kernel.handle_user_input(
            conversation_id="conv",
            text="另外帮我总结会议纪要",
            tools=[tool],
        )
    )

    switched = asyncio.run(
        kernel.handle_user_input(
            conversation_id="conv",
            text="切回刚才那个",
            tools=[tool],
            target_run_id=first.run.id,
            metadata={"control": "switch"},
        )
    )

    assert switched.route is not None
    assert switched.route.kind == "switch_run"
    assert switched.run.id == first.run.id
    assert switched.conversation is not None
    assert switched.conversation.foreground_run_id == first.run.id
    assert second.run.id in switched.conversation.background_run_ids
    latest_second = kernel.get_run(second.run.id)
    assert latest_second is not None
    assert latest_second.background is True


def test_brain_kernel_can_cancel_run_and_promote_next_foreground(tmp_path: Path) -> None:
    memory_store = JsonlMemoryStore(tmp_path)
    model = FakeClientToolModel()
    kernel = BrainKernel(agent_id="alice", model=model, memory_store=memory_store)
    tool = ClientTool(
        name="robot_wave",
        description="Wave the robot arm.",
        parameters={
            "type": "object",
            "properties": {"speed": {"type": "string"}},
            "required": ["speed"],
        },
    )

    first = asyncio.run(
        kernel.handle_user_input(
            conversation_id="conv",
            text="帮我整理下载目录",
            tools=[tool],
        )
    )
    second = asyncio.run(
        kernel.handle_user_input(
            conversation_id="conv",
            text="另外帮我总结会议纪要",
            tools=[tool],
        )
    )

    cancelled = asyncio.run(
        kernel.handle_user_input(
            conversation_id="conv",
            text="这个别做了",
            tools=[tool],
            target_run_id=second.run.id,
            metadata={"control": "cancel"},
        )
    )

    assert cancelled.route is not None
    assert cancelled.route.kind == "cancel_run"
    assert cancelled.run.id == second.run.id
    assert cancelled.run.status == RunStatus.cancelled
    assert cancelled.conversation is not None
    assert cancelled.conversation.foreground_run_id == first.run.id
    latest_first = kernel.get_run(first.run.id)
    assert latest_first is not None
    assert latest_first.background is False


def test_brain_kernel_can_run_as_resident_service(tmp_path: Path) -> None:
    async def _exercise() -> None:
        memory_store = JsonlMemoryStore(tmp_path)
        model = FakeLocalToolModel()
        tool = FunctionTool(
            name="remember_note",
            description="Store a user preference note.",
            parameters={
                "type": "object",
                "properties": {"note": {"type": "string"}},
                "required": ["note"],
            },
            func=_remember_note,
        )
        kernel = BrainKernel(agent_id="alice", model=model, memory_store=memory_store, tools=[tool])

        await kernel.start()
        event_id = await kernel.publish_user_input(
            conversation_id="conv",
            user_id="user",
            turn_id="turn_resident",
            text="帮我记住我喜欢把文件整理整齐",
        )
        output = await kernel.recv_output()
        assert output.event_id == event_id
        assert output.type == BrainOutputType.response
        assert output.response is not None
        assert output.response.reply == "已经记住，我之后会按这个偏好行动。"

        await kernel.stop()
        stopped = await kernel.recv_output()
        assert stopped.type == BrainOutputType.stopped

    asyncio.run(_exercise())


def test_brain_kernel_resident_service_can_record_front_event(tmp_path: Path) -> None:
    async def _exercise() -> None:
        memory_store = JsonlMemoryStore(tmp_path)
        model = FakeLocalToolModel()
        kernel = BrainKernel(agent_id="alice", model=model, memory_store=memory_store)

        await kernel.start()
        event_id = await kernel.publish_front_event(
            conversation_id="conv",
            user_id="user",
            turn_id="front_turn",
            front_event={
                "event_type": "dialogue",
                "user_text": "以后叫我阿青",
                "front_reply": "好呀，我记住了",
                "emotion": "warm",
                "tags": ["relationship"],
            },
        )
        output = await kernel.recv_output()
        assert output.event_id == event_id
        assert output.type == BrainOutputType.recorded

        rows = memory_store.recent_front_records("conv", 10)
        assert rows
        assert rows[0]["content"]

        await kernel.stop()
        stopped = await kernel.recv_output()
        assert stopped.type == BrainOutputType.stopped

    asyncio.run(_exercise())


def test_brain_kernel_resident_service_runs_sleep_agent_in_background(tmp_path: Path) -> None:
    async def _exercise() -> None:
        memory_store = JsonlMemoryStore(tmp_path)
        sleep_started = asyncio.Event()
        sleep_release = asyncio.Event()

        async def _planner(digest) -> SleepOutcome:
            _ = digest
            sleep_started.set()
            await sleep_release.wait()
            return SleepOutcome(
                summary="background sleep completed",
                user_updates=["我想要陪伴感"],
            )

        sleep_agent = SleepAgent(
            memory_store=memory_store,
            planner=_planner,
        )
        model = ImmediateReplyModel()
        kernel = BrainKernel(agent_id="alice", model=model, memory_store=memory_store, sleep_agent=sleep_agent)

        await kernel.start()
        event_id = await kernel.publish_user_input(
            conversation_id="conv",
            user_id="user",
            turn_id="turn_background_sleep",
            text="帮我记住这次对话",
            latest_front_reply="我会记住这次对话。",
        )
        output = await asyncio.wait_for(kernel.recv_output(), timeout=1)
        assert output.event_id == event_id
        assert output.type == BrainOutputType.response
        assert output.response is not None
        assert output.response.reply == "前台回复先返回。"
        assert output.response.sleep_outcome is None

        await asyncio.wait_for(sleep_started.wait(), timeout=1)
        assert not memory_store.long_term_path.exists()

        sleep_release.set()
        for _ in range(50):
            if memory_store.long_term_path.exists():
                break
            await asyncio.sleep(0.02)
        assert memory_store.long_term_path.exists()

        await kernel.stop()
        stopped = await kernel.recv_output()
        assert stopped.type == BrainOutputType.stopped

    asyncio.run(_exercise())


def test_brain_kernel_resident_service_runs_conversations_in_parallel(tmp_path: Path) -> None:
    async def _exercise() -> None:
        memory_store = JsonlMemoryStore(tmp_path)
        model = BlockingConversationModel()
        kernel = BrainKernel(agent_id="alice", model=model, memory_store=memory_store)

        await kernel.start()
        slow_event_id = await kernel.publish_user_input(
            conversation_id="conv_slow",
            text="slow turn",
        )
        await asyncio.wait_for(model.slow_started.wait(), timeout=1)

        fast_event_id = await kernel.publish_user_input(
            conversation_id="conv_fast",
            text="fast turn",
        )
        first_output = await asyncio.wait_for(kernel.recv_output(), timeout=1)
        assert first_output.event_id == fast_event_id
        assert first_output.type == BrainOutputType.response
        assert first_output.response is not None
        assert first_output.response.reply == "fast done"

        model.release_slow.set()
        second_output = await asyncio.wait_for(kernel.recv_output(), timeout=1)
        assert second_output.event_id == slow_event_id
        assert second_output.type == BrainOutputType.response
        assert second_output.response is not None
        assert second_output.response.reply == "slow done"

        await kernel.stop()
        stopped = await kernel.recv_output()
        assert stopped.type == BrainOutputType.stopped

    asyncio.run(_exercise())


def test_brain_kernel_resident_service_can_resume_client_tool(tmp_path: Path) -> None:
    async def _exercise() -> None:
        memory_store = JsonlMemoryStore(tmp_path)
        model = FakeClientToolModel()
        tool = ClientTool(
            name="robot_wave",
            description="Wave the robot arm.",
            parameters={
                "type": "object",
                "properties": {"speed": {"type": "string"}},
                "required": ["speed"],
            },
        )
        kernel = BrainKernel(agent_id="alice", model=model, memory_store=memory_store, tools=[tool])

        await kernel.start()
        first_event_id = await kernel.publish_user_input(
            conversation_id="conv",
            text="跟我挥挥手",
        )
        first_output = await kernel.recv_output()
        assert first_output.event_id == first_event_id
        assert first_output.response is not None
        assert first_output.response.pending_tool_calls

        pending = first_output.response.pending_tool_calls[0]
        second_event_id = await kernel.publish_tool_results(
            run_id=first_output.response.run.id,
            tool_results={
                "tool_call_id": pending.tool_call_id,
                "tool_name": pending.tool_name,
                "result": "ok",
                "success": True,
            },
        )
        second_output = await kernel.recv_output()
        assert second_output.event_id == second_event_id
        assert second_output.type == BrainOutputType.response
        assert second_output.response is not None
        assert second_output.response.reply == "机器人已经挥手了。"

        await kernel.stop()
        await kernel.recv_output()

    asyncio.run(_exercise())
