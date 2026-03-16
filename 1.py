import asyncio
import json
import time
from pathlib import Path

from emoticorebot.bootstrap import RuntimeHost
from emoticorebot.config.loader import load_config
from emoticorebot.runtime.transport_bus import TransportBus


def compact_trace_item(item: dict) -> dict:
    out = {
        'role': item.get('role', '"''"'),
        'timestamp': item.get('timestamp', '"''"'),
    }
    if item.get('tool_calls'):
        out['tool_calls'] = [call.get('name', '"''"') for call in item.get('tool_calls', [])]
    if item.get('name'):
        out['name'] = item.get('name')
    content = item.get('content')
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict):
                text = str(block.get('text', '"''"') or block.get('content', '"''"') or '"''"').strip()
                if text:
                    texts.append(text)
        out['content'] = ' | '.join(texts)[:200]
    else:
        out['content'] = str(content or '"''"')[:200]
    return out

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

    probe_rel = f'.timing_probe/trace_add_{int(time.time())}.py'
    session_id = f'cli:timing_trace:{int(time.time())}'
    message_id = f'msg_trace_{int(time.time())}'
    prompt = f'创建一个 {probe_rel} 文件 add(a,b) 返回 a+b'
    host.tool_manager.set_context('cli', 'direct', message_id, session_id)

    async def collect_outbound() -> list[str]:
        outputs = []
        deadline = time.perf_counter() + 180
        while time.perf_counter() < deadline and len(outputs) < 2:
            try:
                msg = await asyncio.wait_for(transport.consume_outbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            outputs.append(msg.content)
        return outputs

    outbound_task = asyncio.create_task(collect_outbound())
    await host.kernel.handle_user_message(
        session_id=session_id,
        channel='cli',
        chat_id='direct',
        sender_id='user',
        message_id=message_id,
        content=prompt,
        timeout_s=120.0,
    )
    outputs = await outbound_task

    executor = host.kernel._team._worker._executor
    trace = [] if executor is None else list(getattr(executor, '_trace_log', []))
    print(json.dumps({
        'probe_rel': probe_rel,
        'outputs': outputs,
        'trace_count': len(trace),
        'trace_tail': [compact_trace_item(item) for item in trace[-12:]],
    }, ensure_ascii=False, indent=2))

    await host.close_mcp()
    await host.kernel.stop()

asyncio.run(main())