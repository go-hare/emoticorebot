from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from emoticorebot.bootstrap import RuntimeHost
from emoticorebot.runtime.transport_bus import TransportBus


def test_runtime_host_uses_configured_heartbeat_interval(tmp_path) -> None:
    host = RuntimeHost.__new__(RuntimeHost)
    host.workspace = Path(tmp_path)
    host.tool_manager = SimpleNamespace(cron_service=None)
    host.bus = TransportBus()
    host.subconscious = None
    host.heartbeat = None

    host.initialize_subconscious(
        enable_reflection=False,
        enable_heartbeat=True,
        heartbeat_interval_s=123,
    )

    assert host.heartbeat is not None
    assert host.heartbeat.interval_s == 123
