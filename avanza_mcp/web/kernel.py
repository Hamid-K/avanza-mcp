"""Web-hosted trading kernel: seams mapped to the WebSocket event bus."""

from typing import Any

from avanza_mcp.config import DEBUG_PROFILE_TOP_DEFAULT
from avanza_mcp.core.kernel import TradingKernel
from avanza_mcp.utils import strip_markup, timestamp
from avanza_mcp.web.events import EventBus


class WebTradingKernel(TradingKernel):
    def __init__(self, event_bus: EventBus, debug: bool = False, debug_profile_top: int = DEBUG_PROFILE_TOP_DEFAULT) -> None:
        self.event_bus = event_bus
        self.init_kernel_state(debug=debug, debug_profile_top=debug_profile_top, log_kind="web")

    def on_state_changed(self, channel: str, payload: Any = None) -> None:
        self.event_bus.publish(channel, payload)

    def write_log(self, message: str) -> None:
        super().write_log(message)
        self.event_bus.publish("notice", {"message": strip_markup(message), "timestamp": timestamp()})

    def write_mcp_log(self, message: str) -> None:
        super().write_mcp_log(message)
        self.event_bus.publish("mcp_log", {"message": strip_markup(message), "timestamp": timestamp()})

    def notify_user(self, message: str, severity: str = "information") -> None:
        super().write_log(message)
        self.event_bus.publish("notice", {"message": strip_markup(message), "severity": severity, "timestamp": timestamp()})
