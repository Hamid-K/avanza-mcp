"""MCP bridge lifecycle and tool-call dispatch."""

import secrets
import threading
import webbrowser

from avanza.entities import StopLossOrderEvent, StopLossTrigger
from avanza_mcp import avanza_ext, config, utils
from avanza_mcp.avanza_ext import estimate_avanza_fee
from avanza_mcp.config import (
    APP_VERSION,
    LIVE_REFRESH_SECONDS,
    TRADINGVIEW_DEFAULT_EXCHANGE,
    TRADINGVIEW_DEFAULT_MARKET,
    TRADINGVIEW_LOGIN_URL,
    TRADINGVIEW_WATCHLIST_ROW_LIMIT,
)
from avanza_mcp.external import feeds, tradingview_data as tv_data, zacks as zacks_feed
from avanza_mcp.external.tradingview_data import (
    tradingview_heatmap_snapshot,
    tradingview_preopen_batch_snapshot,
    tradingview_watchlist_id_from_input,
    tradingview_watchlist_snapshot,
)
from avanza_mcp.external.tradingview_session import (
    clear_tradingview_session,
    load_tradingview_session,
    save_tradingview_session,
    tradingview_auto_login_and_capture_session,
    tradingview_cookie_from_inputs,
    tradingview_session_status,
)
from avanza_mcp.market_data import (
    infer_currency_from_metadata,
    order_stock_name,
    orderbook_quote_row,
    payload_to_json_safe,
)
from avanza_mcp.mcp import server as mcp_server
from avanza_mcp.mcp.catalog import PAPER_SESSION_ID_TOOLS, TENANT_SESSION_SCOPED_TOOLS
from avanza_mcp.mcp.proxy import load_mcp_session
from avanza_mcp.mcp.server import (
    AvanzaMcpRequestHandler,
    mcp_session_payload,
    mcp_tools_catalog,
    remove_mcp_session_file,
    write_mcp_session_file,
)
from avanza_mcp.paper import (
    append_paper_event,
    cancel_paper_order,
    create_paper_order,
    create_paper_stop_loss_order,
    paper_exit_position,
    paper_open_position,
    paper_orders,
    paper_positions,
    paper_risk_state,
    paper_session_id,
    paper_session_summary,
    paper_trades,
)
from avanza_mcp.records import (
    filter_mover_rows,
    first_nested_text_for_keys,
    flattened_search_hits,
    index_constituent_row,
    mcp_orderbook_filter,
    movers_rows_from_payload,
    normalized_search_rows,
    parse_optional_iso_date,
    search_rows_with_market_data,
    stop_loss_mcp_dict,
)
from avanza_mcp.rendering import (
    account_display_name,
    account_row,
    account_rows_from_overview,
    build_order_preview,
    build_stop_loss_preview,
    format_order_request,
    format_stop_loss_request,
    normalize_order_side,
    rows_as_dicts,
)
from avanza_mcp.stoploss_rules import validate_valid_until
from avanza_mcp.utils import (
    is_unauthorized_http_error,
    mcp_call_log_line,
    mcp_result_log_detail,
    mcp_result_log_suffix,
    mcp_stock_marker,
    nested_value,
    summarize_mcp_result,
)
from datetime import date, datetime, timezone
from typing import Any


class CoreBridgeMixin:
    """MCP bridge lifecycle and tool-call dispatch."""
    def ensure_mcp_bridge_health(self) -> None:
        if self.mcp_server is None:
            return

        # If the bridge thread died, restart transparently.
        if self.mcp_thread is None or not self.mcp_thread.is_alive():
            self.record_event("mcp", "bridge_thread_dead", {"action": "restart"})
            self.write_mcp_log("[yellow]MCP bridge thread stopped; restarting.[/yellow]")
            self.stop_mcp_bridge(announce=False)
            try:
                self.start_mcp_bridge()
                self.update_mode_toggles()
            except Exception as exc:
                self.record_event("mcp", "bridge_restart_failed", {"error": str(exc)})
                self.write_mcp_log(f"[red]MCP bridge restart failed:[/red] {exc}")
            return

        # If the session file was removed or became stale, restore it.
        try:
            host, port = self.mcp_server.server_address
            expected_url = f"http://{host}:{port}"
            session = load_mcp_session(config.MCP_SESSION_FILE)
            token = str(session.get("token", ""))
            read_write = bool(session.get("read_write", False))
            if session.get("url") != expected_url or token != (self.mcp_token or "") or read_write != self.mcp_write_enabled:
                self.update_mcp_session_file()
        except Exception:
            self.update_mcp_session_file()

    def mcp_stock_marker_for_call(self, arguments: dict[str, Any]) -> str:
        marker = mcp_stock_marker(arguments)
        if marker and not marker.startswith("OB "):
            return marker

        order_book_id = str(arguments.get("order_book_id", "")).strip()
        if order_book_id:
            return self.stock_name_for_order_book(order_book_id) or f"OB {order_book_id}"

        stop_loss_id = str(arguments.get("stop_loss_id", "")).strip()
        if stop_loss_id:
            for item in self.latest_stoploss_items:
                if str(item.get("id", "")).strip() == stop_loss_id:
                    return order_stock_name(item) or marker

        order_id = str(arguments.get("order_id", "")).strip()
        if order_id:
            for item in self.latest_open_order_items:
                current = str(item.get("id", "") or item.get("orderId", "")).strip()
                if current == order_id:
                    return order_stock_name(item) or marker
        return marker

    def mcp_status_payload(self) -> dict[str, Any]:
        account = self.account_by_id(self.selected_account_id or "") if self.selected_account_id else None
        available_tools = sorted(
            tool.get("name", "")
            for tool in mcp_tools_catalog()
            if isinstance(tool, dict) and tool.get("name")
        )
        sessions = [
            {
                "session_id": context.session_id,
                "label": context.label,
                "active": context.session_id == self.active_session_id,
                "live_trading_authorized": context.session_id in self.live_trading_authorized_session_ids,
                "accounts_loaded": len(context.accounts),
                "selected_account_id": context.selected_account_id,
                "selected_account_name": (
                    account_display_name(next((a for a in context.accounts if str(a.get("id", "")) == str(context.selected_account_id or "")), {}))
                    if context.selected_account_id
                    else None
                ),
                "auth_valid": bool(context.auth_valid),
                "auth_error": str(context.auth_error or ""),
            }
            for context in self.tenant_sessions.values()
        ]
        return {
            "ok": True,
            "app_version": APP_VERSION,
            "enabled": self.mcp_server is not None,
            "mcp_enabled": self.mcp_server is not None,
            "read_write": self.mcp_write_enabled,
            "read_write_enabled": self.mcp_write_enabled,
            "paper_trading": True,
            "paper_trading_enabled": self.paper_mode_enabled,
            "live_trading_allowed_for_this_session": self.live_trading_allowed_for_session,
            "selected_account_id": self.selected_account_id,
            "selected_account_name": account_display_name(account) if isinstance(account, dict) else None,
            "account_type": str(account.get("type", "") or "") if isinstance(account, dict) else None,
            "accounts_loaded": len(self.accounts),
            "poll_interval_seconds": LIVE_REFRESH_SECONDS,
            "available_tools": available_tools,
            "can_read_quotes": True,
            "can_place_paper_orders": True,
            "can_place_live_orders": bool(self.mcp_write_enabled and self.live_trading_allowed_for_session),
            "can_cancel_live_orders": bool(self.mcp_write_enabled and self.live_trading_allowed_for_session),
            "active_session_id": self.active_session_id,
            "sessions_loaded": len(self.tenant_sessions),
            "sessions": sessions,
            "warning": (
                "Live mutation tools are enabled for this session. Use with extreme caution."
                if (self.mcp_write_enabled and self.live_trading_allowed_for_session)
                else "MCP R/W is enabled but live mutation is still blocked until explicitly authorized."
                if self.mcp_write_enabled
                else ""
            ),
            "paper_session_file": str(self.paper_session_path),
            "update_available": self.update_status_outdated,
            "latest_version": self.update_status_latest or APP_VERSION,
        }

    def tenant_sessions_payload(self) -> dict[str, Any]:
        sessions: list[dict[str, Any]] = []
        for context in self.tenant_sessions.values():
            active_account = next(
                (item for item in context.accounts if str(item.get("id", "")) == str(context.selected_account_id or "")),
                None,
            )
            sessions.append(
                {
                    "session_id": context.session_id,
                    "label": context.label,
                    "color": context.color,
                    "active": context.session_id == self.active_session_id,
                    "accounts_loaded": len(context.accounts),
                    "selected_account_id": context.selected_account_id,
                    "selected_account_name": account_display_name(active_account or {}) if active_account else None,
                    "auth_valid": bool(context.auth_valid),
                    "auth_error": str(context.auth_error or ""),
                }
            )
        return {
            "active_session_id": self.active_session_id,
            "sessions_loaded": len(self.tenant_sessions),
            "sessions": sessions,
        }

    def resolve_session_id_for_mcp(self, tool: str, arguments: dict[str, Any]) -> str | None:
        requested_tenant_session_id = str(arguments.get("tenant_session_id", "") or "").strip() or None
        if requested_tenant_session_id:
            _ = self.tenant_session_by_id(requested_tenant_session_id)
            return requested_tenant_session_id

        # Paper-ledger tools reserve session_id for paper strategy grouping.
        requested_session_id = None
        if tool not in PAPER_SESSION_ID_TOOLS:
            requested_session_id = str(arguments.get("session_id", "") or "").strip() or None
        requested_account_id = str(arguments.get("account_id", "") or "").strip() or None
        if requested_session_id:
            _ = self.tenant_session_by_id(requested_session_id)
            return requested_session_id
        if requested_account_id:
            match = self.tenant_session_for_account(requested_account_id)
            if match is not None:
                return match.session_id
        return self.active_session_id

    def stoploss_readback_match(
        self,
        avanza: Any,
        preview: dict[str, Any],
        result: Any | None = None,
    ) -> dict[str, Any] | None:
        account_id = str(preview.get("account_id") or "")
        orderbook_id = str(preview.get("order_book_id") or "")
        order_event = preview.get("stop_loss_order_event") if isinstance(preview.get("stop_loss_order_event"), dict) else {}
        side = str(order_event.get("type") or "")
        result_id = first_nested_text_for_keys(
            result,
            {"stoplossOrderId", "stopLossOrderId", "stop_loss_id", "stopLossId", "id"},
        )
        try:
            items = self.filtered_stoploss_items(
                avanza,
                account_id,
                orderbook_id=orderbook_id,
                side=side,
                refresh=True,
            )
        except Exception:
            return None
        if result_id:
            for item in items:
                if str(item.get("id") or "") == result_id:
                    return stop_loss_mcp_dict(item)
        requested_volume = utils.scalar_number(order_event.get("volume"))
        requested_price = utils.scalar_number(order_event.get("price"))
        trigger = preview.get("stop_loss_trigger") if isinstance(preview.get("stop_loss_trigger"), dict) else {}
        requested_trigger = utils.scalar_number(trigger.get("value"))
        best: dict[str, Any] | None = None
        best_score = -1
        for item in items:
            score = 0
            if str(item.get("status", "")).upper() == "ACTIVE":
                score += 1
            if requested_volume is not None and utils.scalar_number(nested_value(item, "order", "volume")) == requested_volume:
                score += 2
            if requested_price is not None and utils.scalar_number(nested_value(item, "order", "price")) == requested_price:
                score += 2
            if requested_trigger is not None and utils.scalar_number(nested_value(item, "trigger", "value")) == requested_trigger:
                score += 2
            if score > best_score:
                best = item
                best_score = score
        return stop_loss_mcp_dict(best) if best else None

    def stoploss_mutation_response(
        self,
        *,
        dry_run: bool,
        action: str,
        preview: dict[str, Any],
        result: Any | None = None,
        warnings: list[str] | None = None,
        deleted_stop_loss_id: str = "",
        deprecated_alias: bool = False,
    ) -> dict[str, Any]:
        row = None if dry_run else self.stoploss_readback_match(self.require_connection(), preview, result)
        order_event = preview.get("stop_loss_order_event") if isinstance(preview.get("stop_loss_order_event"), dict) else {}
        trigger = preview.get("stop_loss_trigger") if isinstance(preview.get("stop_loss_trigger"), dict) else {}
        payload: dict[str, Any] = {
            "dry_run": dry_run,
            "action": action,
            "request": preview,
            "stop_loss_id": (row or {}).get("stop_loss_id") or first_nested_text_for_keys(result, {"stoplossOrderId", "stopLossOrderId", "stop_loss_id", "stopLossId", "id"}) or deleted_stop_loss_id or None,
            "status": (row or {}).get("status"),
            "account_id": preview.get("account_id"),
            "orderbook_id": preview.get("order_book_id"),
            "stock": (row or {}).get("stock"),
            "side": normalize_order_side(order_event.get("type")),
            "volume": utils.scalar_number(order_event.get("volume")),
            "trigger_type": trigger.get("type"),
            "trigger_value": utils.scalar_number(trigger.get("value")),
            "trigger_value_type": trigger.get("value_type"),
            "order_price": utils.scalar_number(order_event.get("price")),
            "order_price_type": order_event.get("price_type"),
            "valid_until": trigger.get("valid_until"),
            "order_valid_days": order_event.get("valid_days"),
            "readback": row,
        }
        if warnings:
            payload["warnings"] = warnings
        if result is not None:
            payload["result"] = result
        if deleted_stop_loss_id:
            payload["deleted_stop_loss_id"] = deleted_stop_loss_id
        if deprecated_alias:
            payload["warning"] = "avanza_stoploss_replace is deprecated; use avanza_stoploss_edit."
        return payload

    def update_mcp_session_file(self) -> None:
        if self.mcp_server is None or self.mcp_token is None:
            return
        host, port = self.mcp_server.server_address
        write_mcp_session_file(
            config.MCP_SESSION_FILE,
            mcp_session_payload(str(host), int(port), self.mcp_token, self.mcp_write_enabled),
        )

    def start_mcp_bridge(self) -> None:
        self.require_connection()
        if self.mcp_server is not None:
            return
        self.mcp_token = secrets.token_urlsafe(24)
        server = mcp_server.AvanzaMcpHttpServer(("127.0.0.1", 0), AvanzaMcpRequestHandler, self, self.mcp_token)
        self.mcp_server = server
        self.mcp_thread = threading.Thread(target=server.serve_forever, name="avanza-mcp-bridge", daemon=True)
        self.mcp_thread.start()
        self.update_mcp_session_file()
        host, port = server.server_address
        self.write_mcp_log(f"[green]MCP enabled[/green] at http://{host}:{port}.")
        self.write_mcp_log(f"Proxy command: python {config.SHIM_SCRIPT_NAME} mcp")

    def stop_mcp_bridge(self, announce: bool = True, wait: bool = True) -> None:
        if self.mcp_server is None:
            remove_mcp_session_file()
            return
        server = self.mcp_server
        thread = self.mcp_thread
        self.mcp_server = None
        self.mcp_thread = None
        self.mcp_token = None
        remove_mcp_session_file()

        def shutdown_server() -> None:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
            if thread is not None and thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=0.5 if wait else 0.05)

        if wait:
            shutdown_server()
        else:
            threading.Thread(target=shutdown_server, daemon=True, name="avanza-mcp-bridge-shutdown").start()

        if announce:
            self.write_mcp_log("[yellow]MCP disabled.[/yellow]")

    def require_mcp_write(self, confirmed: bool) -> None:
        if not confirmed:
            return
        if not self.mcp_write_enabled:
            raise PermissionError("TUI MCP mode is read-only. Enable R/W in the TUI for live mutations.")
        if not self.live_trading_allowed_for_session:
            raise PermissionError(
                "Live trading is blocked for this MCP session. Explicitly authorize live mode first."
            )

    def handle_mcp_tool_call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        marker = self.mcp_stock_marker_for_call(arguments)
        self.write_mcp_log(mcp_call_log_line(tool, arguments, marker_override=marker))
        self.record_event("mcp", "tool_call", {"tool": tool, "arguments": arguments})
        try:
            result = self.execute_mcp_tool(tool, arguments)
            verification_ok: bool | None = None
            if isinstance(result, dict) and "ok" in result:
                verification_ok = bool(result["ok"])
            if verification_ok is False:
                self.write_mcp_log(f"[yellow]⚠ {tool}: call succeeded but verification FAILED.[/yellow]")
            else:
                self.write_mcp_log(f"[green]✓[/green] {tool}{mcp_result_log_suffix(result)}{mcp_result_log_detail(result)}")
            self.record_event(
                "mcp",
                "tool_result",
                {"tool": tool, "ok": True, "verification_ok": verification_ok, "summary": summarize_mcp_result(result)},
            )
            response = {
                "ok": True,
                "tool": tool,
                "read_write": self.mcp_write_enabled,
                "result": result,
            }
            if verification_ok is not None:
                response["verification_ok"] = verification_ok
            return response
        except Exception as exc:
            self.write_mcp_log(f"[red]✗ {tool}:[/red] {exc}")
            self.record_event("mcp", "tool_error", {"tool": tool, "error": str(exc)})
            return {
                "ok": False,
                "tool": tool,
                "read_write": self.mcp_write_enabled,
                "error": str(exc),
            }

    def execute_mcp_tool(self, tool: str, arguments: dict[str, Any]) -> Any:
        if tool == "avanza_sessions":
            return self.tenant_sessions_payload()

        if tool == "avanza_select_session":
            requested_session_id = str(arguments["session_id"]).strip()
            if not requested_session_id:
                raise ValueError("session_id is required.")
            self.activate_tenant_session(requested_session_id)
            return {
                "ok": True,
                "active_session_id": self.active_session_id,
                "sessions": self.tenant_sessions_payload()["sessions"],
                "capabilities": self.mcp_status_payload(),
            }

        session_scope_id = (
            self.resolve_session_id_for_mcp(tool, arguments)
            if tool in TENANT_SESSION_SCOPED_TOOLS
            else None
        )
        with self.temporary_tenant_scope(session_scope_id):
            if tool in {"avanza_status", "avanza_capabilities"}:
                return self.mcp_status_payload()
            try:
                return self._execute_mcp_tool_inner(tool, arguments)
            except Exception as exc:
                if session_scope_id and is_unauthorized_http_error(exc):
                    self.mark_tenant_session_auth_expired_if_confirmed(session_scope_id, exc)
                raise

    def _execute_mcp_tool_inner(self, tool: str, arguments: dict[str, Any]) -> Any:
        avanza = self.require_connection()
        account_id = str(arguments.get("account_id") or self.selected_account_id or "")

        if tool == "avanza_live_session_authorize":
            acknowledge = bool(arguments.get("acknowledge", False))
            reason = str(arguments.get("reason", "") or "").strip() or None
            if not acknowledge:
                raise PermissionError("Set acknowledge=true to explicitly authorize live trading for this active session.")
            if not self.mcp_write_enabled:
                raise PermissionError("Enable MCP R/W mode in the TUI before authorizing live trading.")
            self.live_trading_allowed_for_session = True
            if not self.live_trading_allowed_for_session:
                raise PermissionError("No active tenant session to authorize. Log in / select a session first.")
            self.write_mcp_log("[yellow]Live mutation mode authorized for this session.[/yellow]")
            self.record_event(
                "mcp",
                "live_session_authorized",
                {"reason": reason, "session_id": self.active_session_id},
            )
            return {
                "ok": True,
                "live_trading_allowed_for_this_session": self.live_trading_allowed_for_session,
                "read_write_enabled": self.mcp_write_enabled,
                "warning": "Live mutation tools are now enabled for this active session.",
                "reason": reason,
            }

        if tool == "avanza_live_session_revoke":
            self.live_trading_allowed_for_session = False
            self.write_mcp_log("[green]Live mutation mode revoked. MCP is paper-safe again.[/green]")
            self.record_event("mcp", "live_session_revoked", {})
            return {
                "ok": True,
                "live_trading_allowed_for_this_session": False,
                "read_write_enabled": self.mcp_write_enabled,
            }

        if tool == "avanza_accounts":
            overview = avanza.get_overview()
            accounts = account_rows_from_overview(overview) if isinstance(overview, dict) else []
            return rows_as_dicts(["ID", "Name", "Type", "Total Value", "Buying Power", "Status"], [account_row(account) for account in accounts])

        if tool == "avanza_select_account":
            requested_account_id = str(arguments["account_id"]).strip()
            if not requested_account_id:
                raise ValueError("account_id is required.")
            if not self.accounts:
                overview = avanza.get_overview()
                if isinstance(overview, dict):
                    self.accounts = account_rows_from_overview(overview)
            account = self.account_by_id(requested_account_id)
            if not account:
                raise ValueError(f"Unknown account id: {requested_account_id}")
            restore_to = self.mcp_scope_original_session_id
            switching_foreign_session = bool(restore_to and restore_to != self.active_session_id)
            if switching_foreign_session:
                self.selected_account_id = requested_account_id
                self.sync_active_state_to_tenant()
            else:
                self.selected_account_id = requested_account_id
                try:
                    self.select_account(requested_account_id)
                except Exception:
                    # Keep MCP account context updated even if UI is not mounted.
                    self.selected_account_id = requested_account_id
                    self.sync_active_state_to_tenant()
            return {
                "ok": True,
                "session_id": self.active_session_id,
                "selected_account_id": requested_account_id,
                "selected_account_name": account_display_name(account),
                "account_type": str(account.get("type", "") or ""),
                "status": str(account.get("status", "") or ""),
                "capabilities": self.mcp_status_payload(),
            }

        if tool == "avanza_account_performance":
            requested_period = arguments.get("period", "SINCE_START")
            requested_account_id = str(arguments.get("account_id") or self.selected_account_id or "")
            return self.account_performance_snapshot(avanza, requested_account_id, requested_period)

        if tool == "tv_scrape_symbol_analytics":
            symbol = str(arguments["symbol"])
            exchange = str(arguments.get("exchange", TRADINGVIEW_DEFAULT_EXCHANGE))
            market = str(arguments.get("market", TRADINGVIEW_DEFAULT_MARKET))
            snapshot = tv_data.tradingview_symbol_snapshot(symbol, exchange=exchange, market=market, cookie="")
            snapshot["mode"] = "free_scrape"
            snapshot["experimental_scrape_mode"] = True
            return snapshot

        if tool == "tv_scrape_symbol_full":
            symbol = str(arguments["symbol"])
            exchange = str(arguments.get("exchange", TRADINGVIEW_DEFAULT_EXCHANGE))
            market = str(arguments.get("market", TRADINGVIEW_DEFAULT_MARKET))
            snapshot = tv_data.tradingview_symbol_full_snapshot(symbol, exchange=exchange, market=market, cookie="")
            snapshot["mode"] = "free_scrape"
            snapshot["experimental_scrape_mode"] = True
            return snapshot

        if tool == "tv_auth_session_start":
            open_browser = bool(arguments.get("open_browser", True))
            opened = False
            if open_browser:
                try:
                    opened = bool(webbrowser.open(TRADINGVIEW_LOGIN_URL, new=2, autoraise=True))
                except Exception:
                    opened = False
            return {
                "login_url": TRADINGVIEW_LOGIN_URL,
                "browser_opened": opened,
                "next_step": "After logging in via browser, call tv_auth_session_set with cookie or sessionid/sessionid_sign.",
                "session_file": str(config.TRADINGVIEW_SESSION_FILE),
                "status": tradingview_session_status(),
            }

        if tool == "tv_auth_session_set":
            source = str(arguments.get("source", "manual") or "manual")
            cookie = tradingview_cookie_from_inputs(arguments)
            if not cookie:
                raise ValueError("Provide cookie or sessionid/sessionid_sign to save TradingView session.")
            saved = save_tradingview_session(cookie, source=source)
            return {
                "saved": True,
                "status": tradingview_session_status(),
                "details": saved,
            }

        if tool == "tv_auth_session_login_auto":
            timeout_seconds = int(arguments.get("timeout_seconds", 300))
            return utils.run_blocking_in_thread(
                tradingview_auto_login_and_capture_session,
                timeout_seconds=timeout_seconds,
            )

        if tool == "tv_auth_session_status":
            return tradingview_session_status()

        if tool == "tv_auth_session_clear":
            deleted = clear_tradingview_session()
            return {
                "cleared": deleted,
                "status": tradingview_session_status(),
            }

        if tool == "tv_auth_symbol_analytics":
            symbol = str(arguments["symbol"])
            exchange = str(arguments.get("exchange", TRADINGVIEW_DEFAULT_EXCHANGE))
            market = str(arguments.get("market", TRADINGVIEW_DEFAULT_MARKET))
            cookie = tradingview_cookie_from_inputs(arguments, load_tradingview_session())
            if not cookie:
                raise ValueError("Authenticated mode requires cookie/sessionid input, saved session, or TRADINGVIEW_SESSIONID env.")
            snapshot = tv_data.tradingview_symbol_snapshot(symbol, exchange=exchange, market=market, cookie=cookie)
            snapshot["mode"] = "authenticated_scrape"
            snapshot["experimental_scrape_mode"] = True
            snapshot["unsafe_for_execution"] = False
            return snapshot

        if tool == "tv_auth_symbol_full":
            symbol = str(arguments["symbol"])
            exchange = str(arguments.get("exchange", TRADINGVIEW_DEFAULT_EXCHANGE))
            market = str(arguments.get("market", TRADINGVIEW_DEFAULT_MARKET))
            cookie = tradingview_cookie_from_inputs(arguments, load_tradingview_session())
            if not cookie:
                raise ValueError("Authenticated mode requires cookie/sessionid input, saved session, or TRADINGVIEW_SESSIONID env.")
            snapshot = tv_data.tradingview_symbol_full_snapshot(symbol, exchange=exchange, market=market, cookie=cookie)
            snapshot["mode"] = "authenticated_scrape"
            snapshot["experimental_scrape_mode"] = True
            snapshot["unsafe_for_execution"] = False
            return snapshot

        if tool == "tv_preopen_symbol_snapshot":
            symbol = str(arguments["symbol"])
            exchange = str(arguments.get("exchange", TRADINGVIEW_DEFAULT_EXCHANGE))
            market = str(arguments.get("market", TRADINGVIEW_DEFAULT_MARKET))
            authenticated = bool(arguments.get("authenticated", True))
            cookie = tradingview_cookie_from_inputs(arguments, load_tradingview_session()) if authenticated else ""
            if authenticated and not cookie:
                raise ValueError("Authenticated pre-open mode requires saved TradingView session or cookie/sessionid input.")
            snapshot = tv_data.tradingview_preopen_symbol_snapshot(
                symbol,
                exchange=exchange,
                market=market,
                authenticated=authenticated,
                cookie=cookie,
            )
            snapshot["experimental_scrape_mode"] = True
            return snapshot

        if tool == "tv_preopen_batch_snapshot":
            raw_symbols = arguments.get("symbols") or []
            if not isinstance(raw_symbols, list):
                raise ValueError("symbols must be a list.")
            exchange = str(arguments.get("exchange", TRADINGVIEW_DEFAULT_EXCHANGE))
            market = str(arguments.get("market", TRADINGVIEW_DEFAULT_MARKET))
            authenticated = bool(arguments.get("authenticated", True))
            cookie = tradingview_cookie_from_inputs(arguments, load_tradingview_session()) if authenticated else ""
            if authenticated and not cookie:
                raise ValueError("Authenticated pre-open mode requires saved TradingView session or cookie/sessionid input.")
            return tradingview_preopen_batch_snapshot(
                raw_symbols,
                exchange=exchange,
                market=market,
                authenticated=authenticated,
                compact=bool(arguments.get("compact", True)),
                max_concurrency=int(arguments.get("max_concurrency", 4)),
                cookie=cookie,
            )

        if tool == "tv_scrape_heatmap":
            market = str(arguments.get("market", TRADINGVIEW_DEFAULT_MARKET))
            limit = int(arguments.get("limit", 50))
            exchanges_raw = arguments.get("exchanges")
            exchanges = [str(item) for item in exchanges_raw] if isinstance(exchanges_raw, list) else None
            snapshot = tradingview_heatmap_snapshot(
                market=market,
                limit=limit,
                exchanges=exchanges,
                min_market_cap=utils.scalar_number(arguments.get("min_market_cap")),
                min_price=utils.scalar_number(arguments.get("min_price")),
                min_volume=utils.scalar_number(arguments.get("min_volume")),
                sector=str(arguments.get("sector") or "") or None,
                industry=str(arguments.get("industry") or "") or None,
                sort_by=str(arguments.get("sort_by", "change") or "change"),
                include_premarket=bool(arguments.get("include_premarket", True)),
                exclude_otc=bool(arguments.get("exclude_otc", True)),
                cookie="",
            )
            snapshot["mode"] = "free_scrape"
            snapshot["experimental_scrape_mode"] = True
            return snapshot

        if tool == "avanza_tv_preopen_portfolio_bundle":
            raw_symbols = arguments.get("include_symbols") or []
            include_symbols = raw_symbols if isinstance(raw_symbols, list) else []
            authenticated = bool(arguments.get("authenticated", True))
            cookie = tradingview_cookie_from_inputs(arguments, load_tradingview_session()) if authenticated else ""
            if authenticated and not cookie:
                raise ValueError("Authenticated pre-open mode requires saved TradingView session or cookie/sessionid input.")
            return self.avanza_tv_preopen_portfolio_bundle_snapshot(
                avanza,
                account_id,
                include_symbols=include_symbols,
                market=str(arguments.get("market", TRADINGVIEW_DEFAULT_MARKET)),
                authenticated=authenticated,
                compact=bool(arguments.get("compact", True)),
                cookie=cookie,
            )

        if tool == "tv_auth_watchlist":
            reference_symbol = str(arguments.get("reference_symbol", "AAPL"))
            exchange = str(arguments.get("exchange", TRADINGVIEW_DEFAULT_EXCHANGE))
            market = str(arguments.get("market", TRADINGVIEW_DEFAULT_MARKET))
            limit = int(arguments.get("limit", 25))
            cookie = tradingview_cookie_from_inputs(arguments, load_tradingview_session())
            if not cookie:
                raise ValueError("Authenticated watchlist mode requires cookie/sessionid input, saved session, or TRADINGVIEW_SESSIONID env.")
            snapshot = tradingview_watchlist_snapshot(
                reference_symbol=reference_symbol,
                exchange=exchange,
                market=market,
                limit=limit,
                cookie=cookie,
            )
            snapshot["mode"] = "authenticated_scrape"
            snapshot["experimental_scrape_mode"] = True
            return snapshot

        if tool == "tv_auth_custom_lists":
            list_id = tradingview_watchlist_id_from_input(arguments.get("list_id"))
            list_id = list_id or None
            list_name = str(arguments.get("list_name", "") or "").strip() or None
            limit = int(arguments.get("limit", TRADINGVIEW_WATCHLIST_ROW_LIMIT))
            snapshot = utils.run_blocking_in_thread(
                tv_data.tradingview_custom_watchlists_from_profile,
                list_id=list_id,
                list_name=list_name,
                limit=max(1, min(limit, TRADINGVIEW_WATCHLIST_ROW_LIMIT)),
            )
            snapshot["mode"] = "authenticated_scrape"
            snapshot["experimental_scrape_mode"] = True
            return snapshot

        if tool == "zacks_scrape_symbol":
            symbol = str(arguments["symbol"])
            cookie = str(arguments.get("cookie", "") or "")
            snapshot = zacks_feed.zacks_symbol_snapshot(symbol, cookie=cookie)
            snapshot["mode"] = "free_scrape"
            snapshot["experimental_scrape_mode"] = True
            return snapshot

        if tool == "fmp_analyst_recommendations":
            symbol = str(arguments["symbol"])
            limit = int(arguments.get("limit", 52))
            api_key = str(arguments.get("api_key", "") or "") or None
            snapshot = feeds.fmp_analyst_recommendations_snapshot(symbol, limit=limit, api_key=api_key)
            snapshot["mode"] = "api"
            snapshot["experimental_scrape_mode"] = True
            return snapshot

        if tool == "polygon_analyst_insights":
            symbol = str(arguments["symbol"])
            limit = int(arguments.get("limit", 50))
            date_value = str(arguments.get("date", "") or "") or None
            api_key = str(arguments.get("api_key", "") or "") or None
            snapshot = feeds.polygon_analyst_insights_snapshot(
                symbol,
                limit=limit,
                date_value=date_value,
                api_key=api_key,
            )
            snapshot["mode"] = "api"
            snapshot["experimental_scrape_mode"] = True
            return snapshot

        if tool == "sec_filings_recent":
            ticker = str(arguments.get("ticker", "") or "") or None
            cik = str(arguments.get("cik", "") or "") or None
            limit = int(arguments.get("limit", 20))
            return feeds.sec_recent_filings_snapshot(ticker=ticker, cik=cik, limit=limit)

        if tool == "fred_series":
            series_id = str(arguments["series_id"])
            api_key = str(arguments.get("api_key", "") or "") or None
            limit = int(arguments.get("limit", 120))
            sort_order = str(arguments.get("sort_order", "desc"))
            return feeds.fred_observations_snapshot(
                series_id=series_id,
                api_key=api_key,
                limit=limit,
                sort_order=sort_order,
            )

        if tool == "data_source_status":
            symbol = str(arguments.get("symbol", "AAPL"))
            exchange = str(arguments.get("exchange", TRADINGVIEW_DEFAULT_EXCHANGE))
            market = str(arguments.get("market", TRADINGVIEW_DEFAULT_MARKET))
            return self.data_source_status_snapshot(symbol=symbol, exchange=exchange, market=market)

        if tool == "signal_context_bundle":
            exchange = str(arguments.get("exchange", TRADINGVIEW_DEFAULT_EXCHANGE))
            market = str(arguments.get("market", TRADINGVIEW_DEFAULT_MARKET))
            symbols = arguments.get("symbols")
            include_tradingview = bool(arguments.get("include_tradingview", True))
            include_zacks = bool(arguments.get("include_zacks", True))
            include_fmp = bool(arguments.get("include_fmp", False))
            include_polygon = bool(arguments.get("include_polygon", False))
            include_sec = bool(arguments.get("include_sec", True))
            fred_series_id = str(arguments.get("fred_series_id", "") or "") or None
            fred_api_key = str(arguments.get("fred_api_key", "") or "") or None
            fmp_api_key = str(arguments.get("fmp_api_key", "") or "") or None
            polygon_api_key = str(arguments.get("polygon_api_key", "") or "") or None
            if isinstance(symbols, list) and symbols:
                return self.signal_context_bundle_batch_snapshot(
                    symbols=symbols,
                    exchange=exchange,
                    market=market,
                    include_tradingview=include_tradingview,
                    include_zacks=include_zacks,
                    include_fmp=include_fmp,
                    include_polygon=include_polygon,
                    include_sec=include_sec,
                    fred_series_id=fred_series_id,
                    fred_api_key=fred_api_key,
                    fmp_api_key=fmp_api_key,
                    polygon_api_key=polygon_api_key,
                    compact=bool(arguments.get("compact", False)),
                )
            symbol = str(arguments.get("symbol") or "").strip()
            if not symbol:
                raise ValueError("Provide symbol or symbols.")
            return self.signal_context_bundle_snapshot(
                symbol=symbol,
                exchange=exchange,
                market=market,
                include_tradingview=include_tradingview,
                include_zacks=include_zacks,
                include_fmp=include_fmp,
                include_polygon=include_polygon,
                include_sec=include_sec,
                fred_series_id=fred_series_id,
                fred_api_key=fred_api_key,
                fmp_api_key=fmp_api_key,
                polygon_api_key=polygon_api_key,
            )

        if tool == "avanza_portfolio":
            return self.portfolio_snapshot(
                avanza,
                account_id,
                orderbook_id=mcp_orderbook_filter(arguments),
                instrument_name=str(arguments.get("instrument_name") or "") or None,
                compact=bool(arguments.get("compact", False)),
                refresh=bool(arguments.get("refresh", False)),
            )

        if tool == "avanza_stoplosses":
            return self.stoploss_snapshot(
                avanza,
                account_id,
                orderbook_id=mcp_orderbook_filter(arguments),
                instrument_name=str(arguments.get("instrument_name") or "") or None,
                side=str(arguments.get("side") or "") or None,
                status=str(arguments.get("status") or "") or None,
                compact=bool(arguments.get("compact", False)),
                refresh=bool(arguments.get("refresh", False)),
            )

        if tool == "avanza_open_orders":
            return self.open_orders_snapshot(
                avanza,
                account_id,
                orderbook_id=mcp_orderbook_filter(arguments),
                instrument_name=str(arguments.get("instrument_name") or "") or None,
                side=str(arguments.get("side") or "") or None,
                status=str(arguments.get("status") or "") or None,
                compact=bool(arguments.get("compact", False)),
                refresh=bool(arguments.get("refresh", False)),
            )

        if tool == "avanza_open_orders_raw":
            include_raw = bool(arguments.get("include_raw", False))
            return self.open_orders_snapshot(
                avanza,
                account_id,
                include_raw=include_raw,
                orderbook_id=mcp_orderbook_filter(arguments),
                instrument_name=str(arguments.get("instrument_name") or "") or None,
                side=str(arguments.get("side") or "") or None,
                status=str(arguments.get("status") or "") or None,
                compact=bool(arguments.get("compact", False)),
                refresh=bool(arguments.get("refresh", False)),
            )

        if tool == "avanza_ongoing_orders":
            include_paper = bool(arguments.get("include_paper", True))
            orderbook_id = mcp_orderbook_filter(arguments)
            instrument_name = str(arguments.get("instrument_name") or "") or None
            side = str(arguments.get("side") or "") or None
            status = str(arguments.get("status") or "") or None
            compact = bool(arguments.get("compact", False))
            return {
                "account_id": account_id or None,
                "stoplosses": self.stoploss_snapshot(
                    avanza,
                    account_id,
                    orderbook_id=orderbook_id,
                    instrument_name=instrument_name,
                    side=side,
                    status=status,
                    compact=compact,
                )["stoplosses"],
                "open_orders": self.open_orders_snapshot(
                    avanza,
                    account_id,
                    orderbook_id=orderbook_id,
                    instrument_name=instrument_name,
                    side=side,
                    status=status,
                    compact=compact,
                )["orders"],
                "paper_orders": (
                    paper_orders(self.paper_session, account_id or None, active_only=True)
                    if include_paper
                    else []
                ),
            }

        if tool == "avanza_transactions":
            transactions_from = parse_optional_iso_date(
                arguments.get("transactions_from") or arguments.get("changed_since") or arguments.get("from"),
                label="transactions_from",
            )
            transactions_to = parse_optional_iso_date(arguments.get("transactions_to") or arguments.get("to"), label="transactions_to")
            return self.transactions_snapshot(
                avanza,
                account_id,
                orderbook_id=mcp_orderbook_filter(arguments),
                instrument_name=str(arguments.get("instrument_name") or "") or None,
                side=str(arguments.get("side") or "") or None,
                status=str(arguments.get("status") or "") or None,
                transactions_from=transactions_from,
                transactions_to=transactions_to,
                types=arguments.get("types"),
                isin=str(arguments.get("isin", "") or "") or None,
                max_elements=int(arguments.get("max_elements", 1000)),
                executed_only=bool(arguments.get("executed_only", True)),
                compact=bool(arguments.get("compact", False)),
            )

        if tool == "avanza_live_snapshot":
            account_id = account_id or self.require_selected_account_id()
            orderbook_id = mcp_orderbook_filter(arguments)
            instrument_name = str(arguments.get("instrument_name") or "") or None
            side = str(arguments.get("side") or "") or None
            status = str(arguments.get("status") or "") or None
            compact = bool(arguments.get("compact", False))
            realtime_quotes = self.realtime_quotes_snapshot(account_id)
            return {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "account_id": account_id,
                "read_write": self.mcp_write_enabled,
                "paper_trading": self.paper_mode_enabled,
                "live_trading_allowed_for_this_session": self.live_trading_allowed_for_session,
                "poll_interval_seconds": LIVE_REFRESH_SECONDS,
                "capabilities": self.mcp_status_payload(),
                "portfolio": self.portfolio_snapshot(
                    avanza,
                    account_id,
                    orderbook_id=orderbook_id,
                    instrument_name=instrument_name,
                    compact=compact,
                ),
                "stoplosses": self.stoploss_snapshot(
                    avanza,
                    account_id,
                    orderbook_id=orderbook_id,
                    instrument_name=instrument_name,
                    side=side,
                    status=status,
                    compact=compact,
                ),
                "open_orders": self.open_orders_snapshot(
                    avanza,
                    account_id,
                    orderbook_id=orderbook_id,
                    instrument_name=instrument_name,
                    side=side,
                    status=status,
                    compact=compact,
                ),
                "realtime_quotes": realtime_quotes,
                "paper_orders": paper_orders(self.paper_session, account_id),
                "paper_positions": paper_positions(self.paper_session, account_id=account_id, active_only=False),
                "paper_trades": paper_trades(self.paper_session, account_id=account_id),
            }

        if tool == "avanza_position":
            requested_orderbook = str(arguments["orderbook_id"])
            snapshot = self.portfolio_snapshot(
                avanza,
                account_id,
                orderbook_id=requested_orderbook,
                compact=bool(arguments.get("compact", False)),
            )
            return {
                "account_id": account_id or None,
                "orderbook_id": requested_orderbook,
                "position": snapshot["positions"][0] if snapshot["positions"] else None,
            }

        if tool == "avanza_instrument_stoplosses":
            return self.stoploss_snapshot(
                avanza,
                account_id,
                orderbook_id=str(arguments["orderbook_id"]),
                side=str(arguments.get("side") or "") or None,
                status=str(arguments.get("status") or "") or None,
                compact=bool(arguments.get("compact", False)),
            )

        if tool == "avanza_instrument_open_orders":
            return self.open_orders_snapshot(
                avanza,
                account_id,
                include_raw=bool(arguments.get("include_raw", True)),
                orderbook_id=str(arguments["orderbook_id"]),
                side=str(arguments.get("side") or "") or None,
                status=str(arguments.get("status") or "") or None,
                compact=bool(arguments.get("compact", False)),
            )

        if tool == "avanza_instrument_transactions":
            transactions_from = parse_optional_iso_date(
                arguments.get("transactions_from") or arguments.get("changed_since") or arguments.get("from") or arguments.get("date"),
                label="transactions_from",
            )
            transactions_to = parse_optional_iso_date(arguments.get("transactions_to") or arguments.get("to") or arguments.get("date"), label="transactions_to")
            return self.transactions_snapshot(
                avanza,
                account_id,
                orderbook_id=str(arguments["orderbook_id"]),
                side=str(arguments.get("side") or "") or None,
                status=str(arguments.get("status") or "") or None,
                transactions_from=transactions_from,
                transactions_to=transactions_to,
                max_elements=int(arguments.get("max_elements", 1000)),
                executed_only=bool(arguments.get("executed_only", True)),
                compact=bool(arguments.get("compact", False)),
            )

        if tool == "avanza_instrument_state":
            return self.instrument_state_snapshot(
                avanza,
                account_id,
                str(arguments["orderbook_id"]),
                transactions_from=parse_optional_iso_date(
                    arguments.get("transactions_from") or arguments.get("changed_since") or arguments.get("from") or arguments.get("date"),
                    label="transactions_from",
                ),
                transactions_to=parse_optional_iso_date(arguments.get("transactions_to") or arguments.get("to") or arguments.get("date"), label="transactions_to"),
                include_raw_orders=bool(arguments.get("include_raw", True)),
            )

        if tool == "avanza_protection_gaps":
            excludes_raw = arguments.get("exclude_orderbook_ids") or []
            excludes = [str(item) for item in excludes_raw] if isinstance(excludes_raw, list) else []
            return self.protection_gaps_snapshot(
                avanza,
                account_id,
                exclude_orderbook_ids=excludes,
                exclude_eth=bool(arguments.get("exclude_eth", False)),
            )

        if tool == "avanza_sold_today_buyback_state":
            return self.sold_today_buyback_state_snapshot(
                avanza,
                account_id,
                trade_date=parse_optional_iso_date(arguments.get("date"), label="date"),
                tight_trigger_percent_max=float(arguments.get("tight_trigger_percent_max", 8.0)),
            )

        if tool == "avanza_recent_fills_needing_protection":
            return self.recent_fills_needing_protection_snapshot(
                avanza,
                account_id,
                since=parse_optional_iso_date(arguments.get("since"), label="since"),
                exclude_eth=bool(arguments.get("exclude_eth", True)),
            )

        if tool == "avanza_verify_no_raw_failed_orders":
            ids_raw = arguments.get("orderbook_ids") or []
            ids = [str(item) for item in ids_raw] if isinstance(ids_raw, list) else []
            return self.verify_no_raw_failed_orders_snapshot(avanza, account_id, orderbook_ids=ids)

        if tool == "avanza_verify_protection":
            ids_raw = arguments.get("orderbook_ids") or []
            ids = [str(item) for item in ids_raw] if isinstance(ids_raw, list) else []
            return self.verify_protection_snapshot(
                avanza,
                account_id,
                orderbook_ids=ids,
                full_holding=bool(arguments.get("full_holding", True)),
                exclude_eth=bool(arguments.get("exclude_eth", True)),
                coverage_target_percent=float(arguments.get("coverage_target_percent", 100.0)),
                exclude_orderbook_ids=[str(item) for item in (arguments.get("exclude_orderbook_ids") or [])],
                exclude_non_stop_eligible=bool(arguments.get("exclude_non_stop_eligible", True)),
            )

        if tool == "avanza_realtime_quotes":
            account_id = account_id or self.require_selected_account_id()
            return {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "account_id": account_id,
                "poll_interval_seconds": LIVE_REFRESH_SECONDS,
                "quotes": self.realtime_quotes_snapshot(account_id),
            }

        if tool == "avanza_orderbook_quotes":
            raw_ids = arguments.get("orderbook_ids")
            if not isinstance(raw_ids, list) or not raw_ids:
                raise ValueError("orderbook_ids must be a non-empty array.")
            return self.orderbook_quotes_snapshot(
                [str(item).strip() for item in raw_ids if str(item).strip()],
                fields=arguments.get("fields") if isinstance(arguments.get("fields"), list) else None,
                refresh=bool(arguments.get("refresh", True)),
            )

        if tool == "avanza_market_movers":
            country_codes_raw = arguments.get("countryCodes", ["SE"])
            market_places_raw = arguments.get("marketPlaces")
            min_price = utils.scalar_number(arguments.get("min_price"))
            min_total_value_traded = utils.scalar_number(arguments.get("min_total_value_traded"))
            limit = int(arguments.get("limit", 30))
            country_codes = [str(item).strip().upper() for item in country_codes_raw if str(item).strip()] if isinstance(country_codes_raw, list) else ["SE"]
            market_places = [str(item).strip() for item in market_places_raw if str(item).strip()] if isinstance(market_places_raw, list) else []

            gainers_losers_payload = payload_to_json_safe(
                avanza_ext.avanza_private_post(
                    avanza,
                    "/_api/market-stock-filter/stocks/gainers-losers",
                    body={
                        "filter": {
                            "countryCodes": country_codes,
                            "marketPlaces": market_places,
                            "sectors": [],
                        }
                    },
                )
            )
            filter_options_payload = payload_to_json_safe(
                avanza_ext.avanza_private_get(
                    avanza,
                    "/_api/market-stock-filter/stocks/filter-options",
                    options={"countryCodes": country_codes},
                )
            )
            if not isinstance(gainers_losers_payload, dict):
                gainers_losers_payload = {}

            gainers_rows = movers_rows_from_payload(gainers_losers_payload.get("gainers"))
            losers_rows = movers_rows_from_payload(gainers_losers_payload.get("losers"))
            gainers_rows = filter_mover_rows(
                gainers_rows,
                min_price=min_price,
                min_total_value_traded=min_total_value_traded,
                limit=limit,
            )
            losers_rows = filter_mover_rows(
                losers_rows,
                min_price=min_price,
                min_total_value_traded=min_total_value_traded,
                limit=limit,
            )
            losers_rows.sort(key=lambda item: utils.scalar_number(item.get("one_day_change_percent")) or 0.0)
            for row in gainers_rows + losers_rows:
                orderbook_id = str(row.get("orderbook_id") or "").strip()
                if not orderbook_id:
                    continue
                self._cache_orderbook_metadata(
                    orderbook_id,
                    {
                        "orderbook_id": orderbook_id,
                        "name": row.get("name"),
                        "currency": row.get("currency"),
                        "country_code": row.get("country"),
                    },
                )
            return {
                "countryCodes": country_codes,
                "marketPlaces": market_places,
                "min_price": min_price,
                "min_total_value_traded": min_total_value_traded,
                "numberOfGainers": int(gainers_losers_payload.get("numberOfGainers") or len(gainers_rows)),
                "numberOfLosers": int(gainers_losers_payload.get("numberOfLosers") or len(losers_rows)),
                "numberOfNeutrals": int(gainers_losers_payload.get("numberOfNeutrals") or 0),
                "gainers": gainers_rows,
                "losers": losers_rows,
                "filter_options": filter_options_payload,
                "raw": gainers_losers_payload,
            }

        if tool == "avanza_index_constituents":
            index_id = str(arguments.get("index_id", "19002") or "19002").strip()
            index_name = str(arguments.get("index_name", "OMXS30") or "OMXS30").strip()
            include_quotes = bool(arguments.get("include_quotes", False))
            include_spread = bool(arguments.get("include_spread", False))
            if not index_id:
                raise ValueError("index_id is required.")

            raw_payload = payload_to_json_safe(
                avanza_ext.avanza_private_get(
                    avanza,
                    f"/_api/market-index/{index_id}/constituents",
                    options={},
                )
            )
            items: list[dict[str, Any]] = []
            if isinstance(raw_payload, list):
                items = [item for item in raw_payload if isinstance(item, dict)]
            elif isinstance(raw_payload, dict):
                for key in ("constituents", "items", "stocks", "results"):
                    candidate = raw_payload.get(key)
                    if isinstance(candidate, list):
                        items = [item for item in candidate if isinstance(item, dict)]
                        break
            rows = [index_constituent_row(item) for item in items]
            rows = [row for row in rows if row.get("orderbook_id")]
            for row in rows:
                orderbook_id = str(row.get("orderbook_id") or "").strip()
                if not orderbook_id:
                    continue
                self._cache_orderbook_metadata(
                    orderbook_id,
                    {
                        "orderbook_id": orderbook_id,
                        "name": row.get("name"),
                        "ticker": row.get("ticker"),
                        "country_code": row.get("country_code"),
                    },
                )

            if include_quotes:
                enriched: list[dict[str, Any]] = []
                for row in rows:
                    orderbook_id = str(row.get("orderbook_id") or "")
                    quote_payload = self.quote_payload_for_order_book(orderbook_id, refresh=True)
                    quote_row = orderbook_quote_row(
                        orderbook_id,
                        quote_payload,
                        fallback_name=str(row.get("name") or ""),
                        fallback_ticker=str(row.get("ticker") or ""),
                        fallback_currency="SEK" if str(row.get("country_code") or "").upper() == "SE" else "",
                    )
                    merged = {
                        **row,
                        "last": quote_row.get("last"),
                        "bid": quote_row.get("bid"),
                        "ask": quote_row.get("ask"),
                    }
                    if include_spread:
                        merged["spread_absolute"] = quote_row.get("spread_absolute")
                        merged["spread_percent"] = quote_row.get("spread_percent")
                    enriched.append(merged)
                rows = enriched
            elif include_spread:
                enriched = []
                for row in rows:
                    orderbook_id = str(row.get("orderbook_id") or "")
                    quote_payload = self.quote_payload_for_order_book(orderbook_id, refresh=False)
                    quote_row = orderbook_quote_row(
                        orderbook_id,
                        quote_payload,
                        fallback_name=str(row.get("name") or ""),
                        fallback_ticker=str(row.get("ticker") or ""),
                    )
                    merged = {
                        **row,
                        "last": quote_row.get("last"),
                        "bid": quote_row.get("bid"),
                        "ask": quote_row.get("ask"),
                        "spread_absolute": quote_row.get("spread_absolute"),
                        "spread_percent": quote_row.get("spread_percent"),
                    }
                    enriched.append(merged)
                rows = enriched

            return {
                "index_id": index_id,
                "index_name": index_name,
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "constituent_count": len(rows),
                "constituents": rows,
                "include_quotes": include_quotes,
                "include_spread": include_spread,
                "raw": raw_payload,
            }

        if tool == "avanza_fee_estimate":
            side = str(arguments.get("side", "")).strip()
            if not side:
                raise ValueError("side is required.")
            orderbook_id = str(arguments.get("orderbook_id") or arguments.get("order_book_id") or "").strip()
            if not orderbook_id:
                raise ValueError("orderbook_id is required.")
            price = utils.scalar_number(arguments.get("price"))
            quantity = int(arguments.get("quantity", 0) or 0)
            if price is None or price <= 0:
                raise ValueError("price must be > 0.")
            if quantity <= 0:
                raise ValueError("quantity must be > 0.")
            market = str(arguments.get("market", "")).strip()
            metadata = self.orderbook_metadata_for_quote(orderbook_id, quote_payload=None, allow_remote_lookup=True)
            warnings: list[str] = []
            currency_input = str(arguments.get("currency", "")).strip().upper()
            resolved_currency = currency_input or infer_currency_from_metadata(
                {
                    "currency": metadata.get("currency"),
                    "country_code": metadata.get("country_code") or metadata.get("country"),
                    "market": market or metadata.get("market"),
                }
            )
            if not resolved_currency:
                market_lower = (market or str(metadata.get("market", ""))).lower()
                country_code = str(metadata.get("country_code") or metadata.get("country") or "").upper()
                fallback = infer_currency_from_metadata({"country_code": country_code, "market": market_lower})
                if fallback:
                    resolved_currency = fallback
                    warnings.append(f"Currency missing; inferred {fallback} from market/country metadata.")
                elif country_code == "SE" or "stockholm" in market_lower or "xsto" in market_lower:
                    resolved_currency = "SEK"
                    warnings.append("Currency missing; inferred SEK from Swedish market context.")
                else:
                    resolved_currency = "USD"
                    warnings.append("Currency unknown for non-Swedish context; using conservative USD + FX estimate.")
            estimate = estimate_avanza_fee(
                account_id=str(arguments["account_id"]),
                order_book_id=orderbook_id,
                side=side,
                price=price,
                quantity=quantity,
                currency=resolved_currency,
                market=market or str(metadata.get("market", "") or ""),
                brokerage_class=str(arguments.get("brokerage_class", "")),
            )
            if warnings:
                estimate.setdefault("warnings", [])
                if isinstance(estimate["warnings"], list):
                    estimate["warnings"].extend(warnings)
            estimate["resolved_currency"] = resolved_currency
            estimate["metadata"] = {
                "name": metadata.get("name"),
                "ticker": metadata.get("ticker"),
                "market": metadata.get("market"),
                "country": metadata.get("country") or metadata.get("country_code"),
                "instrument_type": metadata.get("instrument_type"),
            }
            return estimate

        if tool == "avanza_search_stock":
            query = str(arguments["query"])
            limit = int(arguments.get("limit", 10))
            hits = flattened_search_hits(avanza.search_for_stock(query, max(10, limit * 3)))
            rows = normalized_search_rows(hits, query=query)
            rows = search_rows_with_market_data(avanza, rows, include_market_data=True)
            trimmed: list[dict[str, Any]] = []
            for row in rows[: max(1, min(limit, 50))]:
                orderbook_id = str(row.get("orderbook_id") or "").strip()
                if orderbook_id:
                    self._cache_orderbook_metadata(
                        orderbook_id,
                        {
                            "orderbook_id": orderbook_id,
                            "name": row.get("name"),
                            "ticker": row.get("ticker"),
                            "display_symbol": row.get("display_symbol"),
                            "market": row.get("market_place"),
                            "currency": row.get("currency"),
                            "country_code": row.get("country"),
                            "instrument_type": row.get("instrument_type"),
                        },
                    )
                trimmed.append(
                    {
                        "name": row.get("name"),
                        "ticker": row.get("ticker"),
                        "symbol": row.get("symbol"),
                        "display_symbol": row.get("display_symbol"),
                        "orderbook_id": row.get("orderbook_id"),
                        "market_place": row.get("market_place"),
                        "country": row.get("country"),
                        "currency": row.get("currency"),
                        "instrument_type": row.get("instrument_type"),
                        "tradeable": row.get("tradeable"),
                        "buyable": row.get("buyable"),
                        "sellable": row.get("sellable"),
                        "last_price": row.get("last_price"),
                        "bid": row.get("bid"),
                        "ask": row.get("ask"),
                        "spread_absolute": row.get("spread_absolute"),
                        "spread_percent": row.get("spread_percent"),
                        "isin": row.get("isin"),
                    }
                )
            return {
                "query": query,
                "count": len(trimmed),
                "results": trimmed,
            }

        if tool == "avanza_stoploss_set":
            confirmed = bool(arguments.get("confirm", False))
            self.require_mcp_write(confirmed)
            trigger, order_event, preview = build_stop_loss_preview(arguments)
            warnings = self.apply_stoploss_valid_days_safety(preview, live=confirmed)
            if not confirmed:
                payload = self.stoploss_mutation_response(
                    dry_run=True,
                    action="set",
                    preview=preview,
                    warnings=warnings,
                )
                payload["summary"] = format_stop_loss_request(preview)
                return payload
            result = avanza.place_stop_loss_order(
                parent_stop_loss_id=preview["parent_stop_loss_id"],
                account_id=preview["account_id"],
                order_book_id=preview["order_book_id"],
                stop_loss_trigger=trigger,
                stop_loss_order_event=order_event,
            )
            self.record_event("trading", "live_stoploss_set", {"request": preview, "result": result})
            return self.stoploss_mutation_response(
                dry_run=False,
                action="set",
                preview=preview,
                result=result,
                warnings=warnings,
            )

        if tool == "avanza_stoploss_set_batch":
            confirmed = bool(arguments.get("confirm", False))
            self.require_mcp_write(confirmed)
            batch_items = arguments.get("items")
            if not isinstance(batch_items, list) or not batch_items:
                raise ValueError("items must be a non-empty array.")
            parent_account_id = str(arguments["account_id"])
            prepared: list[tuple[StopLossTrigger, StopLossOrderEvent, dict[str, Any], list[str]]] = []
            dry_run_results: list[dict[str, Any]] = []
            for index, item in enumerate(batch_items):
                if not isinstance(item, dict):
                    raise ValueError(f"items[{index}] must be an object.")
                item_args = {**item, "account_id": parent_account_id}
                trigger, order_event, preview = build_stop_loss_preview(item_args)
                warnings = self.apply_stoploss_valid_days_safety(preview, live=confirmed)
                prepared.append((trigger, order_event, preview, warnings))
                if not confirmed:
                    dry_run_results.append(
                        {
                            "index": index,
                            **self.stoploss_mutation_response(
                                dry_run=True,
                                action="set",
                                preview=preview,
                                warnings=warnings,
                            ),
                        }
                    )
            if not confirmed:
                return {
                    "dry_run": True,
                    "account_id": parent_account_id,
                    "count": len(dry_run_results),
                    "results": dry_run_results,
                }

            results: list[dict[str, Any]] = []
            for index, (trigger, order_event, preview, warnings) in enumerate(prepared):
                try:
                    result = avanza.place_stop_loss_order(
                        parent_stop_loss_id=preview["parent_stop_loss_id"],
                        account_id=preview["account_id"],
                        order_book_id=preview["order_book_id"],
                        stop_loss_trigger=trigger,
                        stop_loss_order_event=order_event,
                    )
                    payload = self.stoploss_mutation_response(
                        dry_run=False,
                        action="set",
                        preview=preview,
                        result=result,
                        warnings=warnings,
                    )
                    payload["index"] = index
                    readback = payload.get("readback") if isinstance(payload.get("readback"), dict) else {}
                    if readback and str(readback.get("orderbook_id") or "") != str(preview["order_book_id"]):
                        payload["ok"] = False
                        payload["error"] = "Readback orderbook_id mismatch; stopping batch."
                        results.append(payload)
                        break
                    payload["ok"] = True
                    results.append(payload)
                except Exception as exc:
                    results.append(
                        {
                            "index": index,
                            "ok": False,
                            "dry_run": False,
                            "account_id": parent_account_id,
                            "orderbook_id": preview.get("order_book_id"),
                            "error": str(exc),
                        }
                    )
                    break
            self.record_event("trading", "live_stoploss_set_batch", {"count": len(results), "results": results})
            touched_ids = [
                str(item.get("orderbook_id") or "")
                for item in results
                if isinstance(item, dict) and item.get("orderbook_id")
            ]
            return {
                "dry_run": False,
                "account_id": parent_account_id,
                "requested_count": len(prepared),
                "completed_count": len(results),
                "all_ok": all(bool(item.get("ok")) for item in results) and len(results) == len(prepared),
                "results": results,
                "verification": self.verify_protection_snapshot(
                    avanza,
                    parent_account_id,
                    orderbook_ids=touched_ids,
                    full_holding=False,
                    exclude_eth=False,
                ),
            }

        if tool == "avanza_order_set":
            confirmed = bool(arguments.get("confirm", False))
            self.require_mcp_write(confirmed)
            order_type, condition, preview = build_order_preview(arguments)
            if not confirmed:
                return {"dry_run": True, "summary": format_order_request(preview), "request": preview}
            result = avanza.place_order(
                account_id=preview["account_id"],
                order_book_id=preview["order_book_id"],
                order_type=order_type,
                price=preview["price"],
                valid_until=date.fromisoformat(preview["valid_until"]),
                volume=preview["volume"],
                condition=condition,
            )
            self.record_event("trading", "live_order_set", {"request": preview, "result": result})
            return {"dry_run": False, "request": preview, "result": result}

        if tool in {"avanza_order_edit", "avanza_open_order_edit"}:
            confirmed = bool(arguments.get("confirm", False))
            self.require_mcp_write(confirmed)
            valid_until = arguments.get("valid_until")
            if isinstance(valid_until, str):
                valid_until = date.fromisoformat(valid_until)
            if not isinstance(valid_until, date):
                raise ValueError("valid_until must be an ISO date string.")
            valid_until = validate_valid_until(valid_until, "valid_until")
            request = {
                "account_id": str(arguments["account_id"]),
                "order_id": str(arguments["order_id"]),
                "price": float(arguments["price"]),
                "valid_until": valid_until.isoformat(),
                "volume": int(arguments["volume"]),
            }
            if not confirmed:
                return {"dry_run": True, "request": request}
            result = avanza.edit_order(
                order_id=request["order_id"],
                account_id=request["account_id"],
                price=request["price"],
                valid_until=valid_until,
                volume=request["volume"],
            )
            self.record_event("trading", "live_order_edit", {"request": request, "result": result})
            return {"dry_run": False, "request": request, "result": result}

        if tool == "avanza_paper_stoploss_set":
            paper_order = create_paper_stop_loss_order(arguments, instrument=str(arguments.get("instrument", "")))
            self.paper_session.setdefault("orders", []).append(paper_order)
            append_paper_event(self.paper_session, "paper_stoploss_set", {"id": paper_order["id"], "request": paper_order["request"]})
            self.save_paper_state()
            self.record_event("trading", "paper_stoploss_set", {"order": paper_order})
            return {"paper": True, "order": paper_order}

        if tool == "avanza_paper_order_set":
            paper_order = create_paper_order(arguments, instrument=str(arguments.get("instrument", "")))
            self.paper_session.setdefault("orders", []).append(paper_order)
            append_paper_event(self.paper_session, "paper_order_set", {"id": paper_order["id"], "request": paper_order["request"]})
            if bool(arguments.get("fill_immediately", False)):
                request = paper_order.get("request", {}) if isinstance(paper_order.get("request"), dict) else {}
                session_id = paper_session_id(arguments.get("session_id"))
                price = utils.scalar_number(request.get("price")) or 0.0
                quantity = int(request.get("volume", 0) or 0)
                if quantity <= 0 or price <= 0:
                    raise ValueError("Paper fill_immediately requires price > 0 and volume > 0.")
                fee = estimate_avanza_fee(
                    account_id=str(request.get("account_id", "")),
                    order_book_id=str(request.get("order_book_id", "")),
                    side=str(request.get("order_type", "buy")),
                    price=price,
                    quantity=quantity,
                    currency="SEK",
                )
                position = paper_open_position(
                    self.paper_session,
                    session_id=session_id,
                    account_id=str(request.get("account_id", "")),
                    order_book_id=str(request.get("order_book_id", "")),
                    ticker=str(arguments.get("ticker", "") or ""),
                    name=str(paper_order.get("instrument", "") or arguments.get("instrument", "")),
                    side=str(request.get("order_type", "buy")),
                    entry_price=price,
                    quantity=quantity,
                    estimated_fees=float(fee.get("estimated_total_cost", 0.0) or 0.0),
                    entry_reason=str(arguments.get("entry_reason", "") or ""),
                    stop_price=utils.scalar_number(arguments.get("stop_price")),
                    target_price=utils.scalar_number(arguments.get("target_price")),
                )
                paper_order["status"] = "FILLED"
                paper_order["updated_at"] = datetime.now().isoformat(timespec="seconds")
                append_paper_event(
                    self.paper_session,
                    "paper_order_filled",
                    {"order_id": paper_order["id"], "position_id": position["position_id"]},
                )
            self.save_paper_state()
            self.record_event("trading", "paper_order_set", {"order": paper_order})
            return {"paper": True, "order": paper_order}

        if tool == "avanza_paper_order_exit":
            requested_account_id = str(arguments.get("account_id") or self.selected_account_id or "")
            if not requested_account_id:
                raise ValueError("account_id is required.")
            position_id = str(arguments.get("position_id", "") or "") or None
            orderbook_id = str(arguments.get("orderbook_id", "") or "") or None
            exit_price = utils.scalar_number(arguments.get("exit_price"))
            if exit_price is None or exit_price <= 0:
                raise ValueError("exit_price must be > 0.")
            session_id = paper_session_id(arguments.get("session_id"))
            candidate_positions = paper_positions(self.paper_session, account_id=requested_account_id, session_id=None, active_only=True)
            target_position = None
            if position_id:
                target_position = next((item for item in candidate_positions if str(item.get("position_id", "")) == position_id), None)
            elif orderbook_id:
                target_position = next((item for item in candidate_positions if str(item.get("orderbook_id", "")) == str(orderbook_id)), None)
            if target_position is None:
                raise ValueError("No matching open paper position found.")
            quantity = max(1, int(target_position.get("quantity", 0) or 1))
            exit_fee = estimate_avanza_fee(
                account_id=requested_account_id,
                order_book_id=str(target_position.get("orderbook_id", "") or orderbook_id or ""),
                side="sell",
                price=float(exit_price),
                quantity=quantity,
                currency="SEK",
            )
            trade = paper_exit_position(
                self.paper_session,
                account_id=requested_account_id,
                position_id=position_id,
                order_book_id=orderbook_id,
                exit_price=float(exit_price),
                estimated_exit_fees=float(exit_fee.get("estimated_total_cost", 0.0) or 0.0),
                exit_reason=str(arguments.get("exit_reason", "") or ""),
            )
            trade["session_id"] = trade.get("session_id") or session_id
            self.save_paper_state()
            self.record_event("trading", "paper_order_exit", {"trade": trade})
            return {"paper": True, "trade": trade}

        if tool == "avanza_paper_orders":
            requested_account_id = str(arguments.get("account_id") or self.selected_account_id or "")
            active_only = bool(arguments.get("active_only", False))
            return {
                "paper": True,
                "account_id": requested_account_id or None,
                "orders": paper_orders(self.paper_session, requested_account_id or None, active_only),
                "events": self.paper_session.get("events", []),
            }

        if tool == "avanza_paper_positions":
            requested_account_id = str(arguments.get("account_id") or self.selected_account_id or "")
            if not requested_account_id:
                raise ValueError("account_id is required.")
            session_id = str(arguments.get("session_id", "") or "") or None
            active_only = bool(arguments.get("active_only", False))
            return {
                "paper": True,
                "account_id": requested_account_id,
                "session_id": session_id,
                "positions": paper_positions(
                    self.paper_session,
                    account_id=requested_account_id,
                    session_id=session_id,
                    active_only=active_only,
                ),
            }

        if tool == "avanza_paper_trades":
            requested_account_id = str(arguments.get("account_id") or self.selected_account_id or "")
            if not requested_account_id:
                raise ValueError("account_id is required.")
            session_id = str(arguments.get("session_id", "") or "") or None
            return {
                "paper": True,
                "account_id": requested_account_id,
                "session_id": session_id,
                "trades": paper_trades(self.paper_session, account_id=requested_account_id, session_id=session_id),
            }

        if tool == "avanza_paper_session_summary":
            requested_account_id = str(arguments.get("account_id") or self.selected_account_id or "")
            if not requested_account_id:
                raise ValueError("account_id is required.")
            session_id = str(arguments.get("session_id", "") or "") or None
            summary = paper_session_summary(self.paper_session, session_id=session_id, account_id=requested_account_id)
            summary["paper"] = True
            return summary

        if tool == "avanza_paper_risk_state":
            requested_account_id = str(arguments.get("account_id") or self.selected_account_id or "")
            if not requested_account_id:
                raise ValueError("account_id is required.")
            session_id = paper_session_id(arguments.get("session_id"))
            return {
                "paper": True,
                **paper_risk_state(
                    self.paper_session,
                    session_id=session_id,
                    account_id=requested_account_id,
                    max_open_trades=max(1, int(arguments.get("max_open_trades", 3))),
                    max_trade_notional_sek=max(0.0, float(arguments.get("max_trade_notional_sek", 5000) or 0.0)),
                    max_loss_per_trade_sek=max(0.0, float(arguments.get("max_loss_per_trade_sek", 250) or 0.0)),
                    max_session_loss_sek=max(0.0, float(arguments.get("max_session_loss_sek", 800) or 0.0)),
                    stop_after_consecutive_losses=max(0, int(arguments.get("stop_after_consecutive_losses", 3))),
                ),
            }

        if tool == "avanza_scalp_watchlist_set":
            watchlist_id = str(arguments.get("watchlist_id", "")).strip()
            if not watchlist_id:
                raise ValueError("watchlist_id is required.")
            items = arguments.get("items")
            if not isinstance(items, list) or not items:
                raise ValueError("items must be a non-empty array.")
            normalized: list[dict[str, Any]] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                orderbook_id = str(item.get("orderbook_id", "")).strip()
                if not orderbook_id:
                    continue
                normalized.append(
                    {
                        "orderbook_id": orderbook_id,
                        "label": str(item.get("label", "") or "").strip() or None,
                    }
                )
            if not normalized:
                raise ValueError("No valid watchlist items.")
            watchlists = self.paper_session.setdefault("watchlists", {})
            watchlists[watchlist_id] = normalized
            append_paper_event(self.paper_session, "watchlist_set", {"watchlist_id": watchlist_id, "count": len(normalized)})
            self.save_paper_state()
            return {"paper": True, "watchlist_id": watchlist_id, "count": len(normalized), "items": normalized}

        if tool == "avanza_scalp_watchlist_get":
            watchlist_id = str(arguments.get("watchlist_id", "")).strip()
            if not watchlist_id:
                raise ValueError("watchlist_id is required.")
            include_quotes = bool(arguments.get("include_quotes", True))
            watchlists = self.paper_session.setdefault("watchlists", {})
            items = watchlists.get(watchlist_id)
            if not isinstance(items, list):
                raise ValueError(f"Unknown watchlist_id: {watchlist_id}")
            payload: dict[str, Any] = {
                "paper": True,
                "watchlist_id": watchlist_id,
                "count": len(items),
                "items": items,
            }
            if include_quotes:
                orderbook_ids = [str(item.get("orderbook_id", "")).strip() for item in items if str(item.get("orderbook_id", "")).strip()]
                quotes: list[dict[str, Any]] = []
                for orderbook_id in orderbook_ids:
                    quote_payload = self.quote_payload_for_order_book(orderbook_id, refresh=True)
                    fallback_name = ""
                    for item in items:
                        if str(item.get("orderbook_id", "")).strip() == orderbook_id:
                            fallback_name = str(item.get("label", "") or "")
                            break
                    quotes.append(orderbook_quote_row(orderbook_id, quote_payload, fallback_name=fallback_name))
                payload["quotes"] = quotes
            return payload

        if tool == "avanza_paper_cancel":
            paper_order = cancel_paper_order(self.paper_session, str(arguments["paper_order_id"]))
            self.save_paper_state()
            self.record_event("trading", "paper_order_cancel", {"order": paper_order})
            return {"paper": True, "order": paper_order}

        if tool in {"avanza_order_delete", "avanza_open_order_cancel"}:
            confirmed = bool(arguments.get("confirm", False))
            self.require_mcp_write(confirmed)
            request = {
                "account_id": str(arguments["account_id"]),
                "order_id": str(arguments["order_id"]),
            }
            if not confirmed:
                return {"dry_run": True, "request": request}
            result = avanza.delete_order(request["account_id"], request["order_id"])
            self.record_event("trading", "live_order_delete", {"request": request, "result": result})
            return {"dry_run": False, "request": request, "result": result}

        if tool == "avanza_stoploss_delete":
            confirmed = bool(arguments.get("confirm", False))
            self.require_mcp_write(confirmed)
            request = {
                "account_id": str(arguments["account_id"]),
                "stop_loss_id": str(arguments["stop_loss_id"]),
            }
            if not confirmed:
                return {"dry_run": True, "request": request}
            result = avanza.delete_stop_loss_order(request["account_id"], request["stop_loss_id"])
            self.record_event("trading", "live_stoploss_delete", {"request": request, "result": result})
            return {
                "dry_run": False,
                "action": "delete",
                "stop_loss_id": request["stop_loss_id"],
                "account_id": request["account_id"],
                "status": "DELETED",
                "request": request,
                "result": result,
            }

        if tool in {"avanza_stoploss_replace", "avanza_stoploss_edit"}:
            confirmed = bool(arguments.get("confirm", False))
            self.require_mcp_write(confirmed)
            stop_loss_id = str(arguments["stop_loss_id"])
            trigger, order_event, preview = build_stop_loss_preview(arguments)
            warnings = self.apply_stoploss_valid_days_safety(preview, live=confirmed)
            deprecated_alias = tool == "avanza_stoploss_replace"
            request = {
                "stop_loss_id": stop_loss_id,
                "replacement": preview,
            }
            if not confirmed:
                payload = self.stoploss_mutation_response(
                    dry_run=True,
                    action="edit",
                    preview=preview,
                    warnings=warnings,
                    deleted_stop_loss_id=stop_loss_id,
                    deprecated_alias=deprecated_alias,
                )
                payload["summary"] = format_stop_loss_request(preview)
                payload["request"] = request
                if deprecated_alias:
                    payload["warning"] = "avanza_stoploss_replace is deprecated; use avanza_stoploss_edit."
                return payload
            # Place the replacement BEFORE deleting the old stop so a failed
            # placement can never leave the position unprotected.
            place_result = avanza.place_stop_loss_order(
                parent_stop_loss_id=preview["parent_stop_loss_id"],
                account_id=preview["account_id"],
                order_book_id=preview["order_book_id"],
                stop_loss_trigger=trigger,
                stop_loss_order_event=order_event,
            )
            try:
                delete_result = avanza.delete_stop_loss_order(preview["account_id"], stop_loss_id)
                protection_state = "replaced"
            except Exception as exc:
                delete_result = {"error": str(exc)}
                protection_state = "duplicate_protection"
                warnings = [
                    *warnings,
                    f"Replacement placed, but deleting old stop-loss {stop_loss_id} failed: {exc}. "
                    "BOTH stops may be active — review and delete manually.",
                ]
            result = {"delete": delete_result, "place": place_result, "protection_state": protection_state}
            self.record_event(
                "trading",
                "live_stoploss_edit",
                {"request": request, "result": result, "used_deprecated_alias": deprecated_alias},
            )
            payload = self.stoploss_mutation_response(
                dry_run=False,
                action="edit",
                preview=preview,
                result=result,
                warnings=warnings,
                deleted_stop_loss_id=stop_loss_id,
                deprecated_alias=deprecated_alias,
            )
            payload["request"] = request
            if deprecated_alias:
                payload["warning"] = "avanza_stoploss_replace is deprecated; use avanza_stoploss_edit."
            return payload

        raise ValueError(f"Unknown MCP tool: {tool}")
