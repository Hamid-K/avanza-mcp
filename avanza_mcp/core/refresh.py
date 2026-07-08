"""Live-refresh, heartbeat, and update-check workers (headless halves).

Thread-spawning fetch workers and their state-application logic live here;
interval timers and table rendering stay with the widget hosts. The kernel's
``_apply_live_refresh_payload`` updates caches only; the TUI overrides it to
also render its DataTables.
"""

import threading
import time
from typing import Any

from avanza_mcp import update_check
from avanza_mcp.config import APP_VERSION, AUTH_ERROR_LOG_THROTTLE_SECONDS
from avanza_mcp.rendering import open_order_items
from avanza_mcp.update_check import is_version_outdated
from avanza_mcp.utils import is_unauthorized_http_error


class CoreRefreshMixin:
    """Background data-refresh workers shared by the TUI and Web front-ends."""

    def refresh_selected_account_live(self) -> None:
        if self.shutdown_event.is_set():
            return
        if self.mcp_scope_depth > 0:
            self.live_refresh_deferred_by_mcp_scope = True
            return
        if not self.avanza or not self.selected_account_id:
            return
        active_session_id = str(self.active_session_id or "").strip()
        if active_session_id and active_session_id in self.live_refresh_auth_blocked_sessions:
            now = time.monotonic()
            last_notice = self.live_refresh_auth_last_notice_at.get(active_session_id, 0.0)
            if now - last_notice >= AUTH_ERROR_LOG_THROTTLE_SECONDS:
                self.live_refresh_auth_last_notice_at[active_session_id] = now
                label = self.tenant_session_label(active_session_id)
                self.write_log(
                    f"[yellow]Live refresh still paused:[/yellow] {label} is unauthorized. "
                    "Re-login it via [bold]Extra Account Login[/bold]."
                )
            return
        with self.live_refresh_lock:
            if self.live_refresh_inflight:
                self.live_refresh_pending = True
                return
            self.live_refresh_inflight = True
        self.live_refresh_thread = threading.Thread(
            target=self._refresh_selected_account_live_worker,
            daemon=True,
            name="avanza-live-refresh",
        )
        self.live_refresh_thread.start()

    def _refresh_selected_account_live_worker(self) -> None:
        started = time.perf_counter()
        if self.mcp_scope_depth > 0:
            self.live_refresh_deferred_by_mcp_scope = True
            self._finish_live_refresh_cycle()
            return
        active_session_id = self.active_session_id
        if not self.avanza or not self.selected_account_id:
            self._finish_live_refresh_cycle()
            return

        try:
            avanza = self.require_connection()
            selected_account_id = self.selected_account_id
            data = avanza.get_accounts_positions()
            if not isinstance(data, dict):
                raise RuntimeError(f"Unexpected portfolio response type: {type(data).__name__}")
            quote_payloads, realtime_statuses = self.prefetch_quote_and_status_by_order_book(
                data,
                selected_account_id,
                allow_status_lookup=False,
            )
            stoplosses = avanza.get_all_stop_losses()
            try:
                orders = avanza.get_orders()
            except Exception:
                orders = []
            elapsed = time.perf_counter() - started
            if not self.safe_call_from_thread(
                self._apply_live_refresh_payload,
                data,
                quote_payloads,
                realtime_statuses,
                stoplosses,
                orders,
                elapsed,
                active_session_id,
            ):
                self._finish_live_refresh_cycle()
        except Exception as exc:
            if is_unauthorized_http_error(exc):
                self.safe_call_from_thread(self.mark_tenant_session_auth_expired, active_session_id, exc)
            else:
                self.safe_call_from_thread(self.write_log, f"[red]Live refresh failed:[/red] {exc}")
            self._finish_live_refresh_cycle()

    def _finish_live_refresh_cycle(self) -> None:
        with self.live_refresh_lock:
            had_pending = self.live_refresh_pending
            self.live_refresh_inflight = False
            self.live_refresh_pending = False
        if had_pending and not self.shutdown_event.is_set():
            self.refresh_selected_account_live()

    def _background_session_heartbeat_worker(self) -> None:
        try:
            active_session_id = str(self.active_session_id or "").strip()
            session_snapshot = list(self.tenant_sessions.items())
            for session_id, context in session_snapshot:
                if self.shutdown_event.is_set():
                    break
                if session_id == active_session_id:
                    continue
                try:
                    overview = context.avanza.get_overview()
                    portfolio = context.avanza.get_accounts_positions()
                    stoplosses = context.avanza.get_all_stop_losses()
                    try:
                        orders = context.avanza.get_orders()
                    except Exception as exc:
                        self.safe_call_from_thread(
                            self.debug_log,
                            f"background_session_refresh({session_id}) orders fetch failed: {exc}",
                        )
                        orders = []
                except Exception as exc:
                    if is_unauthorized_http_error(exc):
                        self.safe_call_from_thread(self.mark_tenant_session_auth_expired, session_id, exc)
                    else:
                        self.safe_call_from_thread(
                            self.debug_log,
                            f"background_session_refresh({session_id}) failed: {exc}",
                        )
                    continue
                if isinstance(portfolio, dict):
                    self.safe_call_from_thread(
                        self.update_tenant_session_data_cache,
                        session_id,
                        overview if isinstance(overview, dict) else None,
                        portfolio,
                        stoplosses,
                        orders,
                    )
        finally:
            with self.background_session_heartbeat_lock:
                self.background_session_heartbeat_inflight = False

    def refresh_background_sessions(self) -> None:
        if self.shutdown_event.is_set():
            return
        if len(self.tenant_sessions) < 2:
            return
        with self.background_session_heartbeat_lock:
            if self.background_session_heartbeat_inflight:
                return
            self.background_session_heartbeat_inflight = True
        self.background_session_heartbeat_thread = threading.Thread(
            target=self._background_session_heartbeat_worker,
            daemon=True,
            name="avanza-background-session-heartbeat",
        )
        self.background_session_heartbeat_thread.start()

    def schedule_update_check(self) -> None:
        with self.update_check_lock:
            if self.update_check_inflight:
                return
            self.update_check_inflight = True
        self.update_status_text = "Update: checking..."
        self.render_update_status()
        self.update_check_thread = threading.Thread(
            target=self._update_check_worker,
            daemon=True,
            name="avanza-update-check",
        )
        self.update_check_thread.start()

    def _update_check_worker(self) -> None:
        info: dict[str, str] | None = None
        error = ""
        try:
            info = update_check.github_latest_version_info(self.update_status_repo)
        except Exception as exc:
            error = str(exc)
        if not self.safe_call_from_thread(self.apply_update_check_result, info, error):
            with self.update_check_lock:
                self.update_check_inflight = False

    def apply_update_check_result(self, info: dict[str, str] | None, error: str) -> None:
        self.update_status_error = error
        self.update_status_blink_on = True
        if error:
            self.update_status_latest = ""
            self.update_status_outdated = False
            self.update_status_text = "Update: check failed"
            self.render_update_status()
            with self.update_check_lock:
                self.update_check_inflight = False
            return

        latest = str((info or {}).get("version", "") or "")
        self.update_status_latest = latest
        self.update_status_outdated = is_version_outdated(APP_VERSION, latest)
        if self.update_status_outdated and latest:
            self.update_status_text = f"Update: v{latest} available"
        else:
            self.update_status_text = f"Update: latest ({APP_VERSION})"
        self.render_update_status()
        with self.update_check_lock:
            self.update_check_inflight = False

    def _apply_live_refresh_payload(
        self,
        data: dict[str, Any],
        quote_payloads: dict[str, dict[str, Any] | None],
        realtime_statuses: dict[str, str],
        stoplosses: Any,
        orders: Any,
        elapsed: float,
        session_id: str | None,
    ) -> None:
        """Headless default: update caches and notify; widget hosts override to render."""
        if self.mcp_scope_depth > 0:
            self.live_refresh_deferred_by_mcp_scope = True
            self._finish_live_refresh_cycle()
            return
        if session_id and self.active_session_id and session_id != self.active_session_id:
            self._finish_live_refresh_cycle()
            return
        if session_id:
            self.update_tenant_session_data_cache(session_id, None, data, stoplosses, orders)
        self.mark_tenant_session_auth_ok(session_id)
        self.latest_portfolio_data = data if isinstance(data, dict) else None
        self.latest_stoploss_items = [item for item in stoplosses if isinstance(item, dict)] if isinstance(stoplosses, list) else []
        self.latest_open_order_items = [item for item in open_order_items(orders) if isinstance(item, dict)]
        if self.debug_mode:
            self.debug_log(f"refresh_selected_account_live(background): {elapsed:.3f}s")
        self.on_state_changed("portfolio")
        self.on_state_changed("orders")
        self.on_state_changed("stoplosses")
        self._finish_live_refresh_cycle()
