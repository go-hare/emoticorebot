import asyncio
import json
import time

from emoticorebot.bootstrap import RuntimeHost
from emoticorebot.config.loader import load_config
from emoticorebot.protocol.envelope import BusEnvelope
from emoticorebot.protocol.task_models import ProtocolModel
from emoticorebot.protocol.topics import EventType
from emoticorebot.runtime.transport_bus import TransportBus

WATCH_EVENTS = [
    EventType.OUTPUT_REPLY_APPROVED,
    EventType.TASK_EVENT_PROGRESS,
    EventType.TASK_EVENT_RESULT,
]

async def main() -> None:
    config = load_config()
    transport = TransportBus()
    host = RuntimeHost(
        bus=transport,
        workspace=config.workspace_path,
        worker_mode=config.agents.defaults.worker_mode,
        brain_mode=config.agents.defaults.brain_mode,
        providers_config=config.providers,
        memory_config=config.memory,
        brave_api_key=config.tools.web.search.api_key or None,
        exec_config=config.tools.exec,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        mcp_servers=config.tools.mcp_servers,
        channels_config=config.channels,
    )

    probe_rel = f'.timing_probe/create_agent_add_{int(time.time())}.py'
    session_id = f'cli:create_agent_probe:{int(time.time())}'
    message_id = f'msg_create_agent_probe_{int(time.time())}'
    prompt = f'创建一个 {probe_rel} 文件 add(a,b) 返回 a+b'
    host.tool_manager.set_context('cli', 'direct', message_id, session_id)

    timeline = []
    t0 = time.perf_counter()

    def now_ms() -> int:
        return int((time.perf_counter() - t0) * 1000)

    async def capture(event: BusEnvelope[ProtocolModel]) -> None:
        payload = event.payload
        summary = '"''"'
        if hasattr(payload, 'summary') and getattr(payload, 'summary'):
            summary = str(getattr(payload, 'summary'))
        elif hasattr(payload, 'reply') and getattr(payload, 'reply', None) is not None:
            summary = str(getattr(getattr(payload, 'reply'), 'plain_text', '"''"') or '"''"')
        timeline.append({'t_ms': now_ms(), 'event': str(event.event_type), 'summary': summary[:180], 'task_id': event.task_id})

    for idx, event_type in enumerate(WATCH_EVENTS):
        host.kernel._bus.subscribe(consumer=f'probe:{idx}', event_type=event_type, handler=capture)

    async def collect_outbound() -> list[dict[str, object]]:
        outputs = []
        deadline = time.perf_counter() + 180
        while time.perf_counter() < deadline and len(outputs) < 4:
            try:
                msg = await asyncio.wait_for(transport.consume_outbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            outputs.append({'t_ms': now_ms(), 'content': msg.content})
        return outputs

    outbound_task = asyncio.create_task(collect_outbound())
    first = await host.kernel.handle_user_message(
        session_id=session_id,
        channel='cli',
        chat_id='direct',
        sender_id='user',
        message_id=message_id,
        content=prompt,
        timeout_s=120.0,
    )
    outputs = await outbound_task

    print(json.dumps({'first_reply': first.content, 'outbound': outputs, 'timeline': timeline}, ensure_ascii=False, indent=2))

    await host.close_mcp()
    await host.kernel.stop()

asyncio.run(main())