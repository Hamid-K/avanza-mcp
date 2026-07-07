"""Web-hosted trading kernel: seams mapped to the WebSocket event bus."""

from collections import deque
from typing import Any

from avanza_mcp.config import DEBUG_PROFILE_TOP_DEFAULT
from avanza_mcp.core.kernel import TradingKernel
from avanza_mcp.utils import strip_markup, timestamp
from avanza_mcp.web.events import EventBus


class WebTradingKernel(TradingKernel):
    """Kernel whose state-change hooks push serialized snapshots over the EventBus."""

    def __init__(self, event_bus: EventBus, debug: bool = False, debug_profile_top: int = DEBUG_PROFILE_TOP_DEFAULT) -> None:
        self.event_bus = event_bus
        self.web_mcp_log: deque = deque(maxlen=500)
        self.init_kernel_state(debug=debug, debug_profile_top=debug_profile_top, log_kind="web")

    def on_state_changed(self, channel: str, payload: Any = None) -> None:
        if payload is None:
            payload = self._payload_for_channel(channel)
        self.event_bus.publish(channel, payload)

    def _payload_for_channel(self, channel: str) -> Any:
        from avanza_mcp.web import serializers

        try:
            if channel == "portfolio":
                return serializers.portfolio_payload(self)
            if channel == "sessions":
                return serializers.sessions_payload(self)
            if channel == "orders":
                return serializers.orders_payload(self)
            if channel == "stoplosses":
                return serializers.stoplosses_payload(self)
            if channel == "paper":
                return None  # paper view pulls /api/paper/state on demand
            if channel == "mcp_status":
                return serializers.mcp_status_web_payload(self)
            if channel == "update_check":
                return {
                    "text": self.update_status_text,
                    "latest": self.update_status_latest,
                    "outdated": self.update_status_outdated,
                    "error": self.update_status_error,
                }
        except Exception as exc:
            self.debug_log(f"ws payload build failed for {channel}: {exc}")
        return None

    def write_log(self, message: str) -> None:
        super().write_log(message)
        self.event_bus.publish("notice", {"message": strip_markup(message), "timestamp": timestamp()})

    def write_mcp_log(self, message: str) -> None:
        super().write_mcp_log(message)
        entry = {"message": strip_markup(message), "timestamp": timestamp()}
        self.web_mcp_log.append(entry)
        self.event_bus.publish("mcp_log", entry)

    def notify_user(self, message: str, severity: str = "information") -> None:
        super().write_log(message)
        self.event_bus.publish("notice", {"message": strip_markup(message), "severity": severity, "timestamp": timestamp()})
