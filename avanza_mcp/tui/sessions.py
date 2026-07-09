"""Tenant-session registry, activation, snapshots, and auth state."""


from avanza_mcp.config import APP_NAME, APP_VERSION
from avanza_mcp.models import AvanzaTenantSession
from avanza_mcp.rendering import account_display_name, compact_account_type, compact_single_line
from rich.text import Text
from textual.widgets import DataTable, Select, Static
from typing import Any


class SessionsMixin:
    """Tenant-session registry, activation, snapshots, and auth state."""

    def is_blank_select_value(self, value: Any) -> bool:
        if value is None:
            return True
        for sentinel_name in ("BLANK", "NULL"):
            sentinel = getattr(Select, sentinel_name, None)
            if sentinel is not None and (value is sentinel or value == sentinel):
                return True
        text = str(value).strip()
        return text in {"", "Select.BLANK", "Select.NULL"}














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






