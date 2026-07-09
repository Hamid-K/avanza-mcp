"""Web runtime: owns the kernel, background refresh threads, and lifecycle."""

import threading
import time

from avanza_mcp.config import (
    BACKGROUND_SESSION_HEARTBEAT_SECONDS,
    LIVE_REFRESH_SECONDS,
    MCP_HEALTH_CHECK_SECONDS,
    UPDATE_CHECK_INTERVAL_SECONDS,
    WEB_DEFAULT_PORT,
)
from avanza_mcp.update_check import update_check_enabled
from avanza_mcp.web.auth import WebAuth
from avanza_mcp.web.events import EventBus
from avanza_mcp.web.kernel import WebTradingKernel


class WebRuntime:
    def __init__(self, port: int = WEB_DEFAULT_PORT, debug: bool = False) -> None:
        self.event_bus = EventBus()
        self.kernel = WebTradingKernel(self.event_bus, debug=debug)
        self.auth = WebAuth(port)
        self.port = int(port)
        self._loop_threads: list[threading.Thread] = []
        self._started = False

    # ------------------------------------------------------------------

    def _interval_loop(self, interval: float, callback, name: str) -> None:
        def loop() -> None:
            while not self.kernel.shutdown_event.wait(interval):
                try:
                    callback()
                except Exception as exc:
                    self.kernel.debug_log(f"{name} tick failed: {exc}")

        thread = threading.Thread(target=loop, daemon=True, name=name)
        self._loop_threads.append(thread)
        thread.start()

    def start_background_loops(self) -> None:
        if self._started:
            return
        self._started = True
        self._interval_loop(LIVE_REFRESH_SECONDS, self.kernel.refresh_selected_account_live, "avanza-web-live-refresh")
        self._interval_loop(BACKGROUND_SESSION_HEARTBEAT_SECONDS, self.kernel.refresh_background_sessions, "avanza-web-heartbeat")
        self._interval_loop(MCP_HEALTH_CHECK_SECONDS, self.kernel.ensure_mcp_bridge_health, "avanza-web-mcp-health")
        if update_check_enabled():
            self.kernel.schedule_update_check()
            self._interval_loop(UPDATE_CHECK_INTERVAL_SECONDS, self.kernel.schedule_update_check, "avanza-web-update-check")

    def stop(self) -> None:
        self.kernel.shutdown_event.set()
        deadline = time.monotonic() + 3.0
        for thread in self._loop_threads:
            thread.join(timeout=max(0.1, deadline - time.monotonic()))
        try:
            self.kernel.stop_mcp_bridge(announce=False, wait=False)
        except Exception:
            pass
        self.auth.remove_session_file()
        self.event_bus.detach_loop()
