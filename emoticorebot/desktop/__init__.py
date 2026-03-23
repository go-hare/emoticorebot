"""Desktop adapter exports."""

from emoticorebot.desktop.adapter import (
    DesktopStateAdapter,
    DesktopStatePacket,
    build_desktop_state_packet,
)
from emoticorebot.desktop.server import DesktopBridgeServer, load_affect_state_snapshot

__all__ = [
    "DesktopStateAdapter",
    "DesktopStatePacket",
    "DesktopBridgeServer",
    "build_desktop_state_packet",
    "load_affect_state_snapshot",
]
