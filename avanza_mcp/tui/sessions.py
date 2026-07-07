"""Tenant-session registry, activation, snapshots, and auth state."""

import re
import time

from avanza import Avanza
from avanza_mcp.config import APP_NAME, APP_VERSION, AUTH_ERROR_LOG_THROTTLE_SECONDS, SESSION_ACCENT_COLORS
from avanza_mcp.models import AccountDataSnapshot, AvanzaTenantSession
from avanza_mcp.records import open_order_matches_filters, stop_loss_account_id, stop_loss_matches_filters
from avanza_mcp.rendering import (
    account_display_name,
    account_id_for_item,
    account_rows_from_overview,
    compact_account_type,
    compact_single_line,
    default_account,
    open_order_account_id,
    open_order_items,
)
from contextlib import contextmanager
from datetime import datetime, timezone
from rich.text import Text
from textual.widgets import DataTable, Select, Static
from typing import Any, Iterator


class SessionsMixin:
    """Tenant-session registry, activation, snapshots, and auth state."""
    def next_session_color(self) -> str:
        return SESSION_ACCENT_COLORS[len(self.tenant_sessions) % len(SESSION_ACCENT_COLORS)]

    def build_session_id(self, label: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", str(label or "").strip().lower()).strip("-")
        if not slug:
            slug = f"session-{self.session_label_counter}"
            self.session_label_counter += 1
        candidate = slug
        counter = 2
        while candidate in self.tenant_sessions:
            candidate = f"{slug}-{counter}"
            counter += 1
        return candidate

    def auto_session_label(self, accounts: list[dict[str, Any]], fallback: str = "Session") -> str:
        lead = default_account(accounts)
        if isinstance(lead, dict):
            name = account_display_name(lead).strip()
            if name:
                return name
        label = f"{fallback} {self.session_label_counter}"
        self.session_label_counter += 1
        return label

    def active_tenant_session(self) -> AvanzaTenantSession | None:
        if not self.active_session_id:
            return None
        return self.tenant_sessions.get(self.active_session_id)

    def tenant_session_by_id(self, session_id: str) -> AvanzaTenantSession:
        context = self.tenant_sessions.get(str(session_id or "").strip())
        if context is None:
            raise ValueError(f"Unknown session_id: {session_id}")
        return context

    def tenant_session_for_account(self, account_id: str) -> AvanzaTenantSession | None:
        token = str(account_id or "").strip()
        if not token:
            return None
        for context in self.tenant_sessions.values():
            for account in context.accounts:
                if str(account.get("id", "")) == token:
                    return context
        return None

    def account_ids_for_payload(
        self,
        context: AvanzaTenantSession,
        portfolio_data: dict[str, Any] | None,
        stoploss_items: list[dict[str, Any]],
        open_orders: list[dict[str, Any]],
    ) -> list[str]:
        account_ids = {str(account.get("id", "")) for account in context.accounts if account.get("id")}
        if context.selected_account_id:
            account_ids.add(str(context.selected_account_id))
        if isinstance(portfolio_data, dict):
            for section in ("withOrderbook", "withoutOrderbook"):
                for item in portfolio_data.get(section, []):
                    if isinstance(item, dict):
                        item_account_id = account_id_for_item(item)
                        if item_account_id:
                            account_ids.add(item_account_id)
        for item in stoploss_items:
            item_account_id = stop_loss_account_id(item)
            if item_account_id:
                account_ids.add(item_account_id)
        for item in open_orders:
            item_account_id = open_order_account_id(item)
            if item_account_id:
                account_ids.add(item_account_id)
        return sorted(account_ids)

    def update_tenant_account_snapshot(
        self,
        context: AvanzaTenantSession,
        account_id: str,
        *,
        portfolio_data: dict[str, Any] | None = None,
        stoploss_items: list[dict[str, Any]] | None = None,
        open_orders: list[dict[str, Any]] | None = None,
        refreshed_at: datetime | None = None,
    ) -> AccountDataSnapshot:
        token = str(account_id or "").strip()
        snapshot = context.account_snapshots.get(token)
        if snapshot is None:
            snapshot = AccountDataSnapshot(account_id=token)
            context.account_snapshots[token] = snapshot
        now = refreshed_at or datetime.now(timezone.utc)
        if portfolio_data is not None:
            snapshot.portfolio_data = portfolio_data
            snapshot.portfolio_refreshed_at = now
        if stoploss_items is not None:
            snapshot.stoploss_items = [
                item for item in stoploss_items if stop_loss_matches_filters(item, account_id=token)
            ]
            snapshot.stoploss_refreshed_at = now
            snapshot.orders_refreshed_at = now
        if open_orders is not None:
            snapshot.open_order_items = [
                item for item in open_orders if open_order_matches_filters(item, account_id=token)
            ]
            snapshot.open_orders_refreshed_at = now
            snapshot.orders_refreshed_at = now
        snapshot.refreshed_at = now
        snapshot.auth_valid = True
        snapshot.auth_error = ""
        return snapshot

    def update_tenant_session_data_cache(
        self,
        session_id: str,
        overview: dict[str, Any] | None,
        portfolio_data: dict[str, Any] | None,
        stoplosses: Any,
        orders: Any,
    ) -> None:
        context = self.tenant_sessions.get(str(session_id or "").strip())
        if context is None:
            return
        if isinstance(overview, dict):
            accounts = account_rows_from_overview(overview)
            if accounts:
                context.accounts = accounts
                selected = str(context.selected_account_id or "").strip()
                if not selected or not any(str(item.get("id", "")) == selected for item in accounts):
                    default = default_account(accounts)
                    context.selected_account_id = str(default.get("id", "")) if default else None
        stoploss_items = [item for item in stoplosses if isinstance(item, dict)] if isinstance(stoplosses, list) else []
        open_orders = [item for item in open_order_items(orders) if isinstance(item, dict)]
        refreshed_at = datetime.now(timezone.utc)
        account_ids = self.account_ids_for_payload(context, portfolio_data, stoploss_items, open_orders)
        for account_id in account_ids:
            self.update_tenant_account_snapshot(
                context,
                account_id,
                portfolio_data=portfolio_data if isinstance(portfolio_data, dict) else None,
                stoploss_items=stoploss_items,
                open_orders=open_orders,
                refreshed_at=refreshed_at,
            )
        selected = str(context.selected_account_id or "").strip()
        if selected:
            snapshot = context.account_snapshots.get(selected)
            if snapshot is not None:
                if isinstance(snapshot.portfolio_data, dict):
                    context.latest_portfolio_data = snapshot.portfolio_data
                context.latest_stoploss_items = list(snapshot.stoploss_items)
                context.latest_open_order_items = list(snapshot.open_order_items)
        context.auth_valid = True
        context.auth_error = ""
        self.live_refresh_auth_blocked_sessions.discard(context.session_id)
        self.live_refresh_auth_last_notice_at.pop(context.session_id, None)
        self.refresh_session_select_options()
        self.update_session_auth_badge()

    def update_active_selected_account_snapshot(self, context: AvanzaTenantSession) -> None:
        selected = str(context.selected_account_id or "").strip()
        if not selected:
            return
        snapshot = self.update_tenant_account_snapshot(
            context,
            selected,
            portfolio_data=context.latest_portfolio_data if isinstance(context.latest_portfolio_data, dict) else None,
            stoploss_items=list(context.latest_stoploss_items),
            open_orders=list(context.latest_open_order_items),
        )
        if not isinstance(snapshot.portfolio_data, dict) and isinstance(context.latest_portfolio_data, dict):
            snapshot.portfolio_data = context.latest_portfolio_data

    def sync_active_state_to_tenant(self) -> None:
        context = self.active_tenant_session()
        if context is None:
            return
        context.accounts = list(self.accounts)
        context.selected_account_id = self.selected_account_id
        context.latest_portfolio_data = self.latest_portfolio_data
        context.latest_stoploss_items = list(self.latest_stoploss_items)
        context.latest_open_order_items = list(self.latest_open_order_items)
        context.holding_volumes_by_order_book = dict(self.holding_volumes_by_order_book)
        context.holding_labels_by_order_book = dict(self.holding_labels_by_order_book)
        context.order_search_labels_by_order_book = dict(self.order_search_labels_by_order_book)
        self.update_active_selected_account_snapshot(context)

    def load_active_state_from_tenant(self, context: AvanzaTenantSession) -> None:
        self.active_session_id = context.session_id
        self.avanza = context.avanza
        self.accounts = list(context.accounts)
        self.selected_account_id = context.selected_account_id
        snapshot = context.account_snapshots.get(str(context.selected_account_id or "").strip())
        self.latest_portfolio_data = (
            snapshot.portfolio_data
            if snapshot is not None and isinstance(snapshot.portfolio_data, dict)
            else context.latest_portfolio_data
        )
        self.latest_stoploss_items = list(snapshot.stoploss_items if snapshot is not None else context.latest_stoploss_items)
        self.latest_open_order_items = list(
            snapshot.open_order_items if snapshot is not None else context.latest_open_order_items
        )
        self.holding_volumes_by_order_book = dict(context.holding_volumes_by_order_book)
        self.holding_labels_by_order_book = dict(context.holding_labels_by_order_book)
        self.order_search_labels_by_order_book = dict(context.order_search_labels_by_order_book)

    def capture_active_runtime_state(self) -> dict[str, Any]:
        return {
            "active_session_id": self.active_session_id,
            "avanza": self.avanza,
            "accounts": list(self.accounts),
            "selected_account_id": self.selected_account_id,
            "latest_portfolio_data": self.latest_portfolio_data,
            "latest_stoploss_items": list(self.latest_stoploss_items),
            "latest_open_order_items": list(self.latest_open_order_items),
            "holding_volumes_by_order_book": dict(self.holding_volumes_by_order_book),
            "holding_labels_by_order_book": dict(self.holding_labels_by_order_book),
            "order_search_labels_by_order_book": dict(self.order_search_labels_by_order_book),
        }

    def restore_active_runtime_state(self, state: dict[str, Any]) -> None:
        self.active_session_id = state.get("active_session_id")
        self.avanza = state.get("avanza")
        self.accounts = list(state.get("accounts") or [])
        self.selected_account_id = state.get("selected_account_id")
        portfolio_data = state.get("latest_portfolio_data")
        self.latest_portfolio_data = portfolio_data if isinstance(portfolio_data, dict) else None
        self.latest_stoploss_items = list(state.get("latest_stoploss_items") or [])
        self.latest_open_order_items = list(state.get("latest_open_order_items") or [])
        self.holding_volumes_by_order_book = dict(state.get("holding_volumes_by_order_book") or {})
        self.holding_labels_by_order_book = dict(state.get("holding_labels_by_order_book") or {})
        self.order_search_labels_by_order_book = dict(state.get("order_search_labels_by_order_book") or {})

    def session_summary_text(self, context: AvanzaTenantSession) -> Text:
        label = compact_single_line(context.label, max_len=24)
        styled = Text()
        styled.append("● ", style=context.color)
        styled.append(label)
        if not context.auth_valid:
            styled.append(" [EXPIRED]", style="bold red")
        return styled

    def refresh_session_select_options(self) -> None:
        try:
            session_select = self.query_one("#session-select", Select)
        except Exception:
            return
        options = [(self.session_summary_text(context), context.session_id) for context in self.tenant_sessions.values()]
        self.session_select_updating = True
        try:
            session_select.set_options(options)
            if self.active_session_id and self.active_session_id in self.tenant_sessions:
                if session_select.value != self.active_session_id:
                    session_select.value = self.active_session_id
        finally:
            self.session_select_updating = False
        self.update_session_auth_badge()

    def apply_active_session_header(self) -> None:
        title = Text(f"{APP_NAME} v{APP_VERSION}")
        try:
            self.query_one("#app-title", Static).update(title)
        except Exception:
            pass

    def workspace_widgets_ready(self) -> bool:
        try:
            self.query_one("#account-select", Select)
            self.query_one("#portfolio-table", DataTable)
            self.query_one("#stoploss-table", DataTable)
        except Exception:
            return False
        return True

    def is_blank_select_value(self, value: Any) -> bool:
        if value is None:
            return True
        for sentinel_name in ("BLANK", "NULL"):
            sentinel = getattr(Select, sentinel_name, None)
            if sentinel is not None and (value is sentinel or value == sentinel):
                return True
        text = str(value).strip()
        return text in {"", "Select.BLANK", "Select.NULL"}

    def selected_session_id(self) -> str | None:
        try:
            session_value = self.query_one("#session-select", Select).value
        except Exception:
            session_value = None
        if not self.is_blank_select_value(session_value):
            token = str(session_value).strip()
            if token:
                return token
        token = str(self.active_session_id or "").strip()
        return token or None

    def update_session_auth_badge(self) -> None:
        try:
            badge = self.query_one("#session-auth-badge", Static)
        except Exception:
            return
        session_id = self.selected_session_id() or self.active_session_id
        if not session_id:
            badge.update("Session auth: -")
            return
        context = self.tenant_sessions.get(str(session_id).strip())
        if context is None:
            badge.update("Session auth: -")
            return
        if context.auth_valid:
            badge.update("[green]Session auth: OK[/green]")
            return
        badge.update("[red]Session auth: EXPIRED[/red]")

    def session_account_option(self, account: dict[str, Any]) -> tuple[Text, str]:
        context = self.active_tenant_session()
        display = Text()
        if context is not None:
            display.append("● ", style=context.color)
        account_name = compact_single_line(account_display_name(account), max_len=32)
        account_type = compact_account_type(account.get("type", ""))
        if account_type:
            display.append(f"{account_name} [{account_type}]")
        else:
            display.append(account_name)
        return display, str(account.get("id", ""))

    def register_tenant_session(
        self,
        avanza: Avanza,
        overview: dict[str, Any],
        portfolio: dict[str, Any],
        stoplosses: Any,
        orders: Any,
        *,
        label: str | None = None,
    ) -> AvanzaTenantSession:
        accounts = account_rows_from_overview(overview)
        session_label = str(label or "").strip() or self.auto_session_label(accounts)
        session_id = self.build_session_id(session_label)
        context = AvanzaTenantSession(
            session_id=session_id,
            label=session_label,
            color=self.next_session_color(),
            avanza=avanza,
            accounts=accounts,
            selected_account_id=str(default_account(accounts).get("id", "")) if default_account(accounts) else None,
            latest_portfolio_data=portfolio if isinstance(portfolio, dict) else None,
            latest_stoploss_items=[item for item in stoplosses if isinstance(item, dict)] if isinstance(stoplosses, list) else [],
            latest_open_order_items=[item for item in open_order_items(orders) if isinstance(item, dict)],
        )
        self.tenant_sessions[session_id] = context
        self.update_tenant_session_data_cache(
            session_id,
            overview,
            portfolio if isinstance(portfolio, dict) else None,
            stoplosses,
            orders,
        )
        return context

    def activate_tenant_session(
        self,
        session_id: str,
        *,
        refresh_ui: bool = True,
        announce: bool = True,
        update_controls: bool = True,
    ) -> None:
        self.sync_active_state_to_tenant()
        context = self.tenant_session_by_id(session_id)
        self.load_active_state_from_tenant(context)
        widgets_ready = self.workspace_widgets_ready()
        if update_controls and widgets_ready:
            self.refresh_session_select_options()
            self.apply_active_session_header()
        if refresh_ui and widgets_ready:
            if isinstance(self.latest_portfolio_data, dict):
                self.position_row_cache = {}
                self.apply_accounts_overview({"accounts": self.accounts}, announce=False)
                self.apply_portfolio_data(self.latest_portfolio_data, fetch_quotes=False, allow_status_lookup=False)
            else:
                self.apply_accounts_overview({"accounts": self.accounts}, announce=False)
            self.apply_stoploss_orders_data(self.latest_stoploss_items, self.latest_open_order_items)
            self.refresh_selected_account_live()
        if announce:
            self.write_log(f"Switched session to {context.label} ({context.session_id}).")

    def clear_workspace_tables(self) -> None:
        for selector in (
            "#portfolio-table",
            "#stoploss-table",
            "#active-trades-table",
            "#orders-history-table",
            "#transactions-history-table",
            "#tv-lists-table",
        ):
            try:
                self.query_one(selector, DataTable).clear()
            except Exception:
                pass

    def logout_all_sessions(self) -> None:
        self.stop_mcp_bridge(announce=False)
        self.tenant_sessions.clear()
        self.active_session_id = None
        self.avanza = None
        self.accounts = []
        self.selected_account_id = None
        self.latest_portfolio_data = None
        self.latest_stoploss_items = []
        self.latest_open_order_items = []
        self.account_snapshot_cache.clear()
        self.position_row_cache = {}
        self.holding_volumes_by_order_book = {}
        self.holding_labels_by_order_book = {}
        self.order_search_labels_by_order_book = {}
        self.live_refresh_auth_blocked_sessions.clear()
        self.live_refresh_auth_last_notice_at.clear()
        if self.live_refresh_timer is not None:
            self.live_refresh_timer.stop()
            self.live_refresh_timer = None
        self.clear_workspace_tables()
        self.refresh_session_select_options()
        try:
            self.query_one("#account-select", Select).set_options([])
        except Exception:
            pass
        self.update_selected_account_summary(None)
        self.query_one("#workspace").display = False
        self.query_one("#login-screen").display = True
        self.clear_secret_inputs()
        self.clear_extra_secret_inputs()
        self.update_mode_toggles()
        self.write_mcp_log("[yellow]MCP disabled: no logged-in tenant sessions.[/yellow]")
        self.record_event("app", "tenant_sessions_cleared", {})

    def logout_selected_session(self) -> None:
        session_id = self.selected_session_id()
        if not session_id:
            raise ValueError("No session is selected.")
        context = self.tenant_sessions.get(session_id)
        if context is None:
            raise ValueError(f"Unknown session_id: {session_id}")
        was_active = session_id == self.active_session_id
        self.tenant_sessions.pop(session_id, None)
        self.live_refresh_auth_blocked_sessions.discard(session_id)
        self.live_refresh_auth_last_notice_at.pop(session_id, None)
        if self.mcp_scope_original_session_id == session_id:
            self.mcp_scope_original_session_id = None
        self.record_event(
            "app",
            "tenant_session_logged_out",
            {"session_id": session_id, "label": context.label, "was_active": was_active},
        )
        if not self.tenant_sessions:
            self.write_log(f"[yellow]Logged out session:[/yellow] {context.label}. No active sessions left.")
            self.logout_all_sessions()
            return

        if was_active:
            next_context = next(iter(self.tenant_sessions.values()))
            self.activate_tenant_session(next_context.session_id, refresh_ui=True, announce=False, update_controls=True)
            self.write_log(
                f"[yellow]Logged out session:[/yellow] {context.label}. "
                f"Switched to {next_context.label}."
            )
        else:
            self.refresh_session_select_options()
            self.write_log(f"[yellow]Logged out session:[/yellow] {context.label}.")

    @contextmanager
    def temporary_tenant_scope(self, session_id: str | None) -> Iterator[None]:
        target = str(session_id or "").strip()
        if not target or target == self.active_session_id:
            self.mcp_scope_original_session_id = self.active_session_id
            try:
                yield
            finally:
                self.mcp_scope_original_session_id = None
            return
        context = self.tenant_session_by_id(target)
        current = self.active_session_id
        previous_original = self.mcp_scope_original_session_id
        previous_state = self.capture_active_runtime_state()
        self.sync_active_state_to_tenant()
        self.mcp_scope_original_session_id = current
        self.mcp_scope_depth += 1
        self.load_active_state_from_tenant(context)
        try:
            yield
        finally:
            self.sync_active_state_to_tenant()
            self.restore_active_runtime_state(previous_state)
            self.mcp_scope_original_session_id = previous_original
            self.mcp_scope_depth = max(0, self.mcp_scope_depth - 1)
            if self.mcp_scope_depth == 0 and self.live_refresh_deferred_by_mcp_scope:
                self.live_refresh_deferred_by_mcp_scope = False
                self.safe_call_from_thread(self.refresh_selected_account_live)

    def tenant_session_label(self, session_id: str | None) -> str:
        token = str(session_id or "").strip()
        if not token:
            return "active session"
        context = self.tenant_sessions.get(token)
        return context.label if context else token

    def mark_tenant_session_auth_expired(self, session_id: str | None, exc: Exception) -> None:
        token = str(session_id or "").strip()
        if not token:
            self.write_log(f"[red]Live refresh failed:[/red] {exc}")
            return
        if token not in self.tenant_sessions:
            return
        self.live_refresh_auth_blocked_sessions.add(token)
        context = self.tenant_sessions.get(token)
        if context is not None:
            context.auth_valid = False
            context.auth_error = str(exc)
        now = time.monotonic()
        last_notice = self.live_refresh_auth_last_notice_at.get(token, 0.0)
        if now - last_notice >= AUTH_ERROR_LOG_THROTTLE_SECONDS:
            self.live_refresh_auth_last_notice_at[token] = now
            label = self.tenant_session_label(token)
            self.write_log(
                f"[yellow]Session auth expired:[/yellow] {label}. "
                "Live refresh paused for this session. Re-login it via [bold]Extra Account Login[/bold]."
            )
        self.record_event(
            "app",
            "session_auth_expired",
            {
                "session_id": token,
                "session_label": self.tenant_session_label(token),
                "error": str(exc),
            },
        )
        self.refresh_session_select_options()
        self.update_session_auth_badge()

    def mark_tenant_session_auth_ok(self, session_id: str | None) -> None:
        token = str(session_id or "").strip()
        if not token:
            return
        if token not in self.tenant_sessions:
            return
        self.live_refresh_auth_blocked_sessions.discard(token)
        context = self.tenant_sessions.get(token)
        if context is not None:
            context.auth_valid = True
            context.auth_error = ""
        self.refresh_session_select_options()
        self.update_session_auth_badge()

    def update_tenant_session_accounts(self, session_id: str, accounts: list[dict[str, Any]]) -> None:
        token = str(session_id or "").strip()
        context = self.tenant_sessions.get(token)
        if context is None:
            return
        context.accounts = list(accounts)
        selected = str(context.selected_account_id or "").strip()
        if selected and any(str(item.get("id", "")) == selected for item in context.accounts):
            return
        default = default_account(context.accounts)
        context.selected_account_id = str(default.get("id", "")) if default else None
        self.refresh_session_select_options()
        self.update_session_auth_badge()

    def extra_session_label(self) -> str:
        return str(self.input_value("extra-session-label") or "").strip() or self.auto_session_label([])
