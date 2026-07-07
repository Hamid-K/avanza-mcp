"""The Avanza trading TUI application."""

import cProfile
import io
import pstats
import re
import threading
import time

from avanza import Avanza
from avanza_mcp import config
from avanza_mcp.config import (
    APP_NAME,
    APP_VERSION,
    DEBUG_PROFILE_TOP_DEFAULT,
    GITHUB_RELEASE_REPO,
    LIVE_REFRESH_SECONDS,
    LOG_CATEGORY_FILES,
    MCP_HEALTH_CHECK_SECONDS,
    ORDER_CONDITION_CHOICES,
    ORDER_TYPE_CHOICES,
    PRICE_TYPE_SELECT_OPTIONS,
    PROFIT_METRIC_MODES,
    STOPLOSS_ORDER_VALID_DAYS_DEFAULT,
    TRADINGVIEW_TUI_REFRESH_SECONDS,
    TRIGGER_TYPE_CHOICES,
    TUI_TITLE,
)
from avanza_mcp.models import AccountDataSnapshot, AvanzaTenantSession
from avanza_mcp.paper import load_paper_session, save_paper_session
from avanza_mcp.rendering import (
    account_display_name,
    market_clock_text,
    plain_cell_value,
    position_order_book_id,
    profit_metric_label,
    sortable_cell_value,
    trade_action_from_cell,
)
from avanza_mcp.stoploss_rules import max_valid_until_date
from avanza_mcp.tui.layout import (
    ActivityPaneResizer,
    PaneResizer,
    SidePaneResizer,
    TicketPaneResizer,
    pane_weights_after_drag,
    side_panel_width_after_drag,
    ticket_pane_width_after_drag,
)
from avanza_mcp.tui.login import LoginMixin
from avanza_mcp.tui.mcp_bridge import McpBridgeMixin
from avanza_mcp.tui.mcp_snapshots import McpSnapshotsMixin
from avanza_mcp.tui.refresh import RefreshMixin
from avanza_mcp.tui.sessions import SessionsMixin
from avanza_mcp.tui.trading import TradingMixin
from avanza_mcp.utils import append_jsonl, create_session_log_path, nested_value, strip_markup, timestamp
from datetime import datetime
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Footer, Header, Input, RichLog, Select, Static, Switch
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from avanza_mcp.mcp.server import AvanzaMcpHttpServer


class AvanzaTradingTui(
    McpBridgeMixin,
    McpSnapshotsMixin,
    LoginMixin,
    SessionsMixin,
    TradingMixin,
    RefreshMixin,
    App,
):
    CSS = """
    Screen {
        layout: vertical;
        background: $surface;
    }

    #login-screen {
        height: 1fr;
        align: center middle;
        padding: 2 4;
    }

    #login-card {
        width: 50;
        height: auto;
        border: tall $primary;
        padding: 1 3;
        background: $panel;
    }

    #login-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #login-subtitle {
        color: $text-muted;
        margin-bottom: 1;
    }

    #login-progress {
        display: none;
        height: 1;
        margin-top: 1;
        color: $accent;
        text-style: bold;
    }

    #login-progress-detail {
        display: none;
        height: 1;
        color: $text-muted;
    }

    #workspace {
        display: none;
        height: 1fr;
    }

    #topbar {
        height: 9;
        padding: 0 3;
        background: $panel;
        border-bottom: solid $primary;
    }

    #topbar-grid {
        height: 8;
    }

    #left-info {
        width: 1fr;
        height: 8;
    }

    #right-controls {
        width: 74;
        min-width: 74;
        height: 8;
        padding: 0 1;
    }

    #account-row {
        height: 3;
        margin-bottom: 0;
        align: left top;
    }

    #app-title {
        width: 13;
        min-width: 13;
        height: 3;
        margin-right: 1;
        content-align: left middle;
        text-style: bold;
    }

    .account-select-block {
        height: 3;
        margin-right: 1;
    }

    .account-select-label {
        height: 1;
        margin-left: 0;
        color: $text-muted;
    }

    #session-select-block {
        width: 23;
        min-width: 21;
    }

    #account-select-block {
        width: 43;
        min-width: 36;
        max-width: 46;
    }

    #extra-login-block {
        width: 26;
        min-width: 24;
        max-width: 28;
        padding-top: 1;
        margin-right: 0;
    }

    #session-select,
    #account-select {
        height: 2;
        text-wrap: nowrap;
        content-align: left middle;
    }

    #open-extra-login {
        min-width: 0;
        width: 100%;
        height: 2;
        text-wrap: nowrap;
        text-overflow: ellipsis;
        text-align: center;
        content-align: center middle;
        margin-top: 0;
    }

    #logout-selected-session {
        min-width: 16;
    }

    #refresh-selected-session {
        min-width: 19;
    }

    #session-auth-badge {
        min-width: 20;
        content-align: left middle;
        text-wrap: nowrap;
    }

    #metric-grid {
        height: 4;
        margin-top: 1;
    }

    .metric-card {
        width: 1fr;
        height: 4;
        margin: 0 1 0 0;
        padding: 0 1;
        background: $boost;
        border-left: solid $primary;
        text-align: center;
        content-align: center middle;
    }

    #metric-total {
        border-left: solid $accent;
    }

    #metric-buying {
        border-left: solid $warning;
    }

    #metric-profit {
        border-left: solid $success;
    }

    #metric-status {
        border-left: solid $secondary;
    }

    #metric-profit {
        padding: 0;
    }

    #profit-cycle {
        min-width: 12;
        width: 100%;
        height: 1;
        margin: 0;
        padding: 0 1;
        background: $boost;
        color: $text-muted;
        text-style: bold;
        text-align: center;
        content-align: center middle;
    }

    #metric-profit-value {
        height: 3;
        padding: 0 1;
        text-align: center;
        content-align: center middle;
    }

    #clock-status {
        height: 2;
        content-align: center middle;
        color: $accent;
        text-style: bold;
    }

    #button-controls {
        height: 2;
        align: center middle;
        margin-bottom: 0;
    }

    #view-controls {
        height: 2;
        align: center middle;
        margin-top: 0;
    }

    #view-label {
        width: auto;
        margin-right: 2;
        color: $text-muted;
        text-style: bold;
    }

    #controls-separator {
        height: 1;
        margin: 0 1;
        border-top: solid $primary-darken-3;
    }

    .view-tab {
        min-width: 10;
        height: 1;
        padding: 0 1;
        background: #0f3f73;
        color: #ffffff;
        border: none;
        text-style: bold;
    }

    .view-tab:hover {
        background: #1662ad;
        color: #ffffff;
    }

    #open-orders-overlay {
        min-width: 10;
    }

    #open-transactions-overlay {
        min-width: 14;
    }

    #open-tv-lists-overlay {
        min-width: 18;
    }

    #toggle-controls {
        height: 2;
        align: center middle;
        margin-top: 0;
    }

    .toggle-control {
        width: auto;
        height: 1;
        margin-left: 1;
        align: left middle;
    }

    #live-status {
        width: 9;
        margin-right: 2;
        color: $success;
    }

    .mode-toggle-box {
        min-width: 3;
        width: 3;
        height: 1;
        margin: 0;
        padding: 0;
        text-style: bold;
    }

    .mode-toggle-box.enabled {
        background: $success-darken-2;
        color: $success-lighten-3;
    }

    .mode-toggle-box.disabled {
        background: $error-darken-3;
        color: $error-lighten-2;
    }

    .mode-toggle-label {
        width: auto;
        min-width: 5;
        height: 1;
        margin-left: 1;
        margin-right: 1;
        color: $text;
    }

    #main {
        height: 1fr;
        width: 1fr;
        padding: 1;
    }

    #workspace-tabs {
        height: 1fr;
    }

    #workspace-tabs Tabs {
        height: 1;
    }

    #workspace-tabs TabPane {
        padding: 0;
        height: 1fr;
    }

    #body {
        height: 1fr;
    }

    #active-trades-panel {
        width: 42;
        height: 1fr;
        padding: 1 1 1 0;
    }

    #side-pane-resizer {
        width: 1;
        height: 1fr;
        content-align: center middle;
        color: $text-muted;
        background: $boost;
    }

    #side-pane-resizer:hover {
        color: $text;
        background: $primary-darken-3;
    }

    #side-pane-resizer.dragging {
        color: $text;
        background: $accent;
    }

    #active-trades-table {
        height: 1fr;
    }

    #paper-tab-layout {
        height: 1fr;
        padding: 1;
    }

    #paper-summary {
        height: 3;
        border: solid $primary;
        padding: 0 1;
        background: $boost;
        margin-bottom: 1;
    }

    #paper-positions-table,
    #paper-orders-table {
        height: 1fr;
    }

    .panel {
        border: solid $primary;
        padding: 0 1;
        height: auto;
    }

    DataTable {
        height: 1fr;
        background: $panel;
        color: $text;
    }

    DataTable > .datatable--header {
        background: $primary-darken-3;
        color: $primary-lighten-3;
        text-style: bold;
    }

    DataTable > .datatable--header-hover {
        background: $accent;
        color: $text;
    }

    DataTable > .datatable--even-row {
        background: $surface-lighten-1 35%;
    }

    #positions-panel {
        height: 2fr;
    }

    #pane-resizer {
        height: 1;
        content-align: center middle;
        color: $text-muted;
        background: $boost;
    }

    #pane-resizer:hover {
        color: $text;
        background: $primary-darken-3;
    }

    #pane-resizer.dragging {
        color: $text;
        background: $accent;
    }

    #activity-panel {
        height: 1fr;
    }

    #activity-table-section {
        height: 3fr;
    }

    #activity-logs-section {
        height: 1fr;
        layout: vertical;
    }

    #activity-controls {
        height: 1;
        margin: 0;
        padding: 0;
    }

    #activity-resizer {
        height: 1;
        content-align: center middle;
        color: $text-muted;
        background: $boost;
    }

    #activity-resizer:hover {
        color: $text;
        background: $primary-darken-3;
    }

    #activity-resizer.dragging {
        color: $text;
        background: $accent;
    }

    #portfolio-table {
        height: 1fr;
    }

    #stoploss-table {
        height: 1fr;
    }

    #stoploss-modal,
    #order-modal,
    #cancel-modal {
        display: none;
        dock: right;
        width: 64;
        height: 1fr;
        margin: 0;
        padding: 1 2;
        border: tall $warning;
        background: $panel;
    }

    #extra-login-modal {
        display: none;
        layer: overlay;
        align: center middle;
        width: 1fr;
        height: 1fr;
        background: rgba(0, 0, 0, 0.45);
    }

    #extra-login-card {
        width: 62;
        min-width: 58;
        max-width: 68;
        height: auto;
        border: tall $primary;
        padding: 1 2;
        background: $panel;
    }

    #extra-login-title {
        text-style: bold;
        margin-bottom: 0;
    }

    #extra-login-subtitle {
        color: $text-muted;
        margin-bottom: 0;
    }

    #extra-login-card Input {
        margin-bottom: 0;
    }

    .extra-login-actions {
        height: 1;
        margin: 0;
        align: left middle;
    }

    #extra-onepassword-title {
        margin-top: 0;
    }

    #orders-overlay,
    #transactions-overlay,
    #tv-lists-overlay {
        display: none;
        dock: top;
        width: 1fr;
        height: 1fr;
        margin: 0;
        padding: 1;
        border: tall $primary;
        background: $surface;
    }

    #orders-overlay-note,
    #transactions-overlay-note,
    #tv-lists-overlay-note {
        height: 1;
        margin-bottom: 1;
        color: $text-muted;
    }

    #orders-history-table,
    #transactions-history-table,
    #tv-lists-table {
        height: 1fr;
    }

    #tv-lists-controls {
        height: 3;
        align: left middle;
        margin-bottom: 1;
    }

    #tv-lists-select {
        width: 1fr;
        margin-right: 1;
    }

    #status-bar {
        height: 1;
        dock: bottom;
        padding: 0 1;
        background: $surface-darken-1;
    }

    #update-status {
        width: 1fr;
        content-align: right middle;
        color: $text-muted;
    }

    .ticket-resizer {
        width: 1;
        height: 1fr;
        content-align: center middle;
        color: $text-muted;
        background: $boost;
    }

    .ticket-resizer:hover {
        color: $text;
        background: $primary-darken-3;
    }

    .ticket-resizer.dragging {
        color: $text;
        background: $accent;
    }

    .ticket-content {
        width: 1fr;
        height: 100%;
        padding: 0 1;
    }

    .modal-header {
        height: 3;
        align: left middle;
    }

    .modal-title {
        width: 1fr;
        text-style: bold;
        content-align: left middle;
    }

    .modal-close {
        min-width: 3;
        width: 3;
        margin-right: 1;
        background: $error-darken-3;
        color: $error-lighten-2;
        text-style: bold;
    }

    #order-search-row {
        height: 3;
    }

    #order-search {
        width: 1fr;
    }

    #order-search-status {
        height: 1;
        color: $text-muted;
    }

    #stoploss-modal Select,
    #stoploss-modal Input,
    #order-modal Select,
    #order-modal Input {
        margin-bottom: 1;
    }

    #console-row {
        width: 1fr;
        height: 1fr;
        min-height: 6;
    }

    #log {
        width: 1fr;
        height: 100%;
        border: solid $primary;
    }

    #mcp-log {
        width: 1fr;
        height: 100%;
        border: solid $warning;
    }

    Button {
        min-width: 8;
        height: 1;
        margin: 0 1 0 0;
        padding: 0 1;
        text-style: none;
        border: none;
        background: $boost;
        color: $text;
    }

    Button:hover {
        background: $accent;
        color: $text;
    }

    Button.-primary {
        background: $primary-darken-3;
        color: $primary-lighten-3;
        border: none;
    }

    Button.-warning {
        background: $warning-darken-3;
        color: $warning-lighten-2;
        border: none;
    }

    Button.-error {
        background: $error-darken-3;
        color: $error-lighten-2;
        border: none;
        text-style: bold;
    }

    #login {
        width: 100%;
        margin-top: 1;
    }

    #place-live,
    #order-place-live,
    #cancel-confirm-button {
        min-width: 18;
    }

    #dry-run,
    #order-dry-run,
    #cancel-review {
        min-width: 12;
    }

    Input {
        margin-bottom: 1;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh_stoplosses", "Refresh Stop-Losses"),
        ("p", "refresh_portfolio", "Refresh Portfolio"),
    ]

    def __init__(self, debug: bool = False, debug_profile_top: int = DEBUG_PROFILE_TOP_DEFAULT) -> None:
        super().__init__()
        self.title = TUI_TITLE
        self.debug_mode = bool(debug)
        self.debug_profile_top = max(5, int(debug_profile_top))
        self.debug_profile_depth = 0
        self.debug_session_log_path = create_session_log_path("debug") if self.debug_mode else None
        self.avanza: Avanza | None = None
        self.tenant_sessions: dict[str, AvanzaTenantSession] = {}
        self.active_session_id: str | None = None
        self.session_label_counter = 1
        self.accounts: list[dict[str, Any]] = []
        self.selected_account_id: str | None = None
        self.live_refresh_timer = None
        self.background_session_heartbeat_timer = None
        self.clock_timer = None
        self.mcp_health_timer = None
        self.order_search_timer = None
        self.tv_lists_refresh_timer = None
        self.update_check_timer = None
        self.update_blink_timer = None
        self.login_progress_timer = None
        self.login_thread: threading.Thread | None = None
        self.shutdown_event = threading.Event()
        self.login_busy = False
        self.login_spinner_index = 0
        self.login_progress_tick = 0
        self.login_progress_messages: tuple[str, ...] = ()
        self.login_progress_index = 0
        self.login_stage_message = ""
        self.login_target_mode = "initial"
        self.login_target_session_id: str | None = None
        self.login_target_session_label: str | None = None
        self.mcp_scope_original_session_id: str | None = None
        self.mcp_scope_depth = 0
        self.live_refresh_deferred_by_mcp_scope = False
        self.last_resize: tuple[int, int] | None = None
        self.position_row_cache: dict[str, tuple[str, ...]] = {}
        self.holding_volumes_by_order_book: dict[str, str] = {}
        self.holding_labels_by_order_book: dict[str, str] = {}
        self.order_search_labels_by_order_book: dict[str, str] = {}
        self.table_sort_state: dict[str, tuple[Any, bool]] = {}
        self.realtime_status_by_order_book: dict[str, str] = {}
        self.realtime_status_checked_at: dict[str, datetime] = {}
        self.quote_payload_by_order_book: dict[str, dict[str, Any]] = {}
        self.quote_payload_checked_at: dict[str, datetime] = {}
        self.orderbook_metadata_by_id: dict[str, dict[str, Any]] = {}
        self.orderbook_metadata_checked_at: dict[str, datetime] = {}
        self.live_refresh_thread: threading.Thread | None = None
        self.live_refresh_inflight = False
        self.live_refresh_pending = False
        self.live_refresh_lock = threading.Lock()
        self.background_session_heartbeat_thread: threading.Thread | None = None
        self.background_session_heartbeat_inflight = False
        self.background_session_heartbeat_lock = threading.Lock()
        self.live_refresh_auth_blocked_sessions: set[str] = set()
        self.live_refresh_auth_last_notice_at: dict[str, float] = {}
        self.mcp_server: "AvanzaMcpHttpServer | None" = None
        self.mcp_thread: threading.Thread | None = None
        self.mcp_token: str | None = None
        self.mcp_write_enabled = False
        self.live_trading_allowed_for_session = False
        self.paper_mode_enabled = True
        self.paper_session_path = config.PAPER_SESSION_FILE
        self.paper_session = load_paper_session(self.paper_session_path)
        self.session_log_path = create_session_log_path("tui")
        self.latest_portfolio_data: dict[str, Any] | None = None
        self.latest_stoploss_items: list[dict[str, Any]] = []
        self.latest_open_order_items: list[dict[str, Any]] = []
        self.latest_tv_lists: list[dict[str, Any]] = []
        self.latest_tv_list_items: list[dict[str, Any]] = []
        self.account_snapshot_cache: dict[str, AccountDataSnapshot] = {}
        self.tv_list_option_refs: dict[str, dict[str, str]] = {}
        self.session_select_updating = False
        self.account_select_updating = False
        self.tv_lists_loaded_value = ""
        self.tv_lists_select_updating = False
        self.tv_lists_refresh_thread: threading.Thread | None = None
        self.tv_lists_refresh_inflight = False
        self.tv_lists_refresh_pending_value: str | None = None
        self.tv_lists_refresh_lock = threading.Lock()
        self.update_check_thread: threading.Thread | None = None
        self.update_check_inflight = False
        self.update_check_lock = threading.Lock()
        self.update_status_repo = GITHUB_RELEASE_REPO
        self.update_status_text = "Update: checking..."
        self.update_status_latest = ""
        self.update_status_outdated = False
        self.update_status_error = ""
        self.update_status_blink_on = True
        self.portfolio_trade_targets_by_row_key: dict[str, dict[str, str]] = {}
        self.paper_trade_targets_by_row_key: dict[str, dict[str, str]] = {}
        self.cancel_targets_by_row_key: dict[str, dict[str, str]] = {}
        self.stoploss_items_by_row_key: dict[str, dict[str, Any]] = {}
        self.pending_cancel_target: dict[str, str] | None = None
        self.pending_stoploss_edit_id: str | None = None
        self.paper_quote_cache: dict[str, dict[str, Any]] = {}
        self.positions_pane_weight = 2
        self.activity_pane_weight = 1
        self.activity_table_weight = 3
        self.activity_logs_weight = 1
        self.active_trades_width = 42
        self.ticket_pane_width = 64
        self.profit_metric_mode = "day"
        self.is_resizing_panes = False
        self.is_resizing_activity = False
        self.is_resizing_side_pane = False
        self.is_resizing_ticket_pane = False
        self.resize_start_y = 0
        self.activity_resize_start_y = 0
        self.resize_start_x = 0
        self.resize_start_positions_weight = self.positions_pane_weight
        self.resize_start_activity_weight = self.activity_pane_weight
        self.activity_resize_start_table_weight = self.activity_table_weight
        self.activity_resize_start_logs_weight = self.activity_logs_weight
        self.resize_start_active_trades_width = self.active_trades_width
        self.resize_start_ticket_pane_width = self.ticket_pane_width
        self.record_event(
            "app",
            "tui_start",
            {
                "app_version": APP_VERSION,
                "session_log": str(self.session_log_path),
                "paper_session_file": str(self.paper_session_path),
                "debug_mode": self.debug_mode,
                "debug_session_log": str(self.debug_session_log_path) if self.debug_session_log_path else "",
            },
        )
        if self.debug_mode:
            self.debug_log("Debug mode enabled.")

    def safe_call_from_thread(self, callback: Callable[..., Any], *args: Any) -> bool:
        if self.shutdown_event.is_set():
            return False
        try:
            self.call_from_thread(callback, *args)
        except RuntimeError:
            return False
        except Exception:
            return False
        return True

    def compose(self) -> ComposeResult:
        default_valid_until = max_valid_until_date().isoformat()
        yield Header()
        with Vertical(id="login-screen"):
            with Vertical(id="login-card"):
                yield Static(f"{APP_NAME} Trading Console", id="login-title")
                yield Static("Sign in once. Credentials disappear after login.", id="login-subtitle")
                yield Input(placeholder="Username", id="username")
                yield Input(placeholder="Password", id="password", password=True)
                yield Input(
                    placeholder="Current TOTP code",
                    id="totp",
                    password=True,
                    restrict=r"[0-9]*",
                    max_length=8,
                )
                yield Button("Login", id="login", variant="primary")
                yield Static("Or use 1Password CLI", id="onepassword-title")
                yield Input(placeholder="1Password item name or ID", id="onepassword-item")
                yield Input(placeholder="1Password vault (optional)", id="onepassword-vault")
                yield Button("Login with 1Password", id="onepassword-login", variant="primary")
                yield Static("", id="login-progress")
                yield Static("", id="login-progress-detail")

        with Vertical(id="workspace"):
            with Horizontal(id="topbar"):
                with Vertical(id="left-info"):
                    with Horizontal(id="account-row"):
                        yield Static(f"{APP_NAME}\nv{APP_VERSION}", id="app-title")
                        with Vertical(id="session-select-block", classes="account-select-block"):
                            yield Static("Session", classes="account-select-label")
                            yield Select([], prompt="Session", allow_blank=True, id="session-select")
                        with Vertical(id="account-select-block", classes="account-select-block"):
                            yield Static("Account", classes="account-select-label")
                            yield Select([], prompt="Select account", allow_blank=True, id="account-select")
                        with Vertical(id="extra-login-block", classes="account-select-block"):
                            yield Button("Extra Account Login", id="open-extra-login", variant="primary")
                    with Horizontal(id="metric-grid"):
                        yield Static("Total\n-", id="metric-total", classes="metric-card")
                        yield Static("Buying\n-", id="metric-buying", classes="metric-card")
                        with Vertical(id="metric-profit", classes="metric-card"):
                            yield Button("1D P/L", id="profit-cycle", classes="metric-cycle")
                            yield Static("-", id="metric-profit-value")
                        yield Static("Status\n-", id="metric-status", classes="metric-card")
                with Vertical(id="right-controls"):
                    yield Static(market_clock_text(), id="clock-status")
                    with Horizontal(id="button-controls"):
                        yield Static(f"Live {LIVE_REFRESH_SECONDS:g}s", id="live-status")
                        yield Button("Refresh", id="refresh-all", variant="primary")
                        yield Button("Logout Session", id="logout-selected-session", variant="error")
                        yield Button("Refresh Session Auth", id="refresh-selected-session", variant="default")
                        yield Static("Session auth: -", id="session-auth-badge")
                        yield Button("Reload TUI", id="reload-tui", variant="default")
                        yield Button("Order", id="open-order-modal", variant="primary")
                        yield Button("Stop-Loss", id="open-stoploss-modal", variant="warning")
                    yield Static("", id="controls-separator")
                    with Horizontal(id="view-controls"):
                        yield Static("Views", id="view-label")
                        yield Button("Orders", id="open-orders-overlay", classes="view-tab")
                        yield Button("Transactions", id="open-transactions-overlay", classes="view-tab")
                        yield Button("TradingView Lists", id="open-tv-lists-overlay", classes="view-tab")
                    with Horizontal(id="toggle-controls"):
                        with Horizontal(classes="toggle-control"):
                            yield Button("✓", id="paper-mode-toggle", classes="mode-toggle-box enabled")
                            yield Static("Paper", id="paper-mode-label", classes="mode-toggle-label")
                        with Horizontal(classes="toggle-control"):
                            yield Button("×", id="mcp-toggle", classes="mode-toggle-box disabled")
                            yield Static("MCP", id="mcp-label", classes="mode-toggle-label")
                        with Horizontal(classes="toggle-control"):
                            yield Button("×", id="mcp-write-toggle", classes="mode-toggle-box disabled")
                            yield Static("R/W", id="mcp-write-label", classes="mode-toggle-label")
            with Horizontal(id="body"):
                with Vertical(id="main"):
                    with Vertical(id="positions-panel"):
                        yield Static("Selected Account Stocks", classes="panel")
                        yield DataTable(id="portfolio-table")
                    yield PaneResizer()
                    with Vertical(id="activity-panel"):
                        yield Static("Ongoing Orders", classes="panel")
                        with Vertical(id="activity-table-section"):
                            yield DataTable(id="stoploss-table")
                        yield ActivityPaneResizer()
                        with Vertical(id="activity-logs-section"):
                            with Horizontal(id="activity-controls"):
                                yield Button("Refresh Account", id="refresh-account", variant="primary")
                                yield Button("Edit Stop-Loss", id="edit-stoploss", variant="primary")
                                yield Button("Clear Log", id="clear-log")
                            with Horizontal(id="console-row"):
                                yield RichLog(id="log", highlight=True, markup=True)
                                yield RichLog(id="mcp-log", highlight=True, markup=True)
                yield SidePaneResizer()
                with Vertical(id="active-trades-panel"):
                    yield Static("Active Stop-Losses", classes="panel")
                    yield DataTable(id="active-trades-table")
                with Horizontal(id="stoploss-modal"):
                    yield TicketPaneResizer("stoploss")
                    with Vertical(classes="ticket-content"):
                        with Horizontal(classes="modal-header"):
                            yield Button("X", id="close-stoploss-modal", classes="modal-close")
                            yield Static("New Stop-Loss", id="stoploss-modal-title", classes="modal-title")
                        yield Static("Uses the selected account.", id="stoploss-account-note")
                        yield Select([], prompt="Select portfolio holding", allow_blank=True, id="instrument-select")
                        yield Input(placeholder="Volume", id="volume", type="number")
                        yield Select(
                            [(label, label) for label in TRIGGER_TYPE_CHOICES],
                            value="follow-upwards",
                            allow_blank=False,
                            id="trigger-type",
                        )
                        yield Input(placeholder="Trigger value", id="trigger-value", type="number")
                        yield Select(
                            PRICE_TYPE_SELECT_OPTIONS,
                            value="percentage",
                            allow_blank=False,
                            id="trigger-value-type",
                        )
                        yield Input(
                            value=default_valid_until,
                            placeholder=f"Valid until ({default_valid_until})",
                            id="valid-until",
                        )
                        yield Select(
                            [(label, label) for label in ORDER_TYPE_CHOICES],
                            value="sell",
                            allow_blank=False,
                            id="order-type",
                        )
                        yield Input(placeholder="Order price", id="order-price", type="number")
                        yield Select(
                            PRICE_TYPE_SELECT_OPTIONS,
                            value="percentage",
                            allow_blank=False,
                            id="order-price-type",
                        )
                        yield Input(
                            value=str(STOPLOSS_ORDER_VALID_DAYS_DEFAULT),
                            placeholder="Order valid days",
                            id="order-valid-days",
                            type="integer",
                        )
                        yield Switch(value=False, id="trigger-on-market-maker-quote")
                        yield Static("Trigger on market-maker quote")
                        yield Switch(value=False, id="short-selling-allowed")
                        yield Static("Allow short selling")
                        yield Input(placeholder='Type "PLACE" to enable live placement', id="place-confirm")
                        with Horizontal():
                            yield Button("Review Only", id="dry-run", variant="default")
                            yield Button("Create Paper Stop-Loss", id="place-live", variant="warning")
                with Horizontal(id="order-modal"):
                    yield TicketPaneResizer("order")
                    with Vertical(classes="ticket-content"):
                        with Horizontal(classes="modal-header"):
                            yield Button("X", id="close-order-modal", classes="modal-close")
                            yield Static("New Buy/Sell Order", classes="modal-title")
                        yield Static("Uses the selected account.", id="order-account-note")
                        with Horizontal(id="order-search-row"):
                            yield Input(placeholder="Search stock, ticker, or ISIN", id="order-search")
                            yield Button("Search", id="order-search-button", variant="primary")
                        yield Static("Type at least 2 characters to search stocks.", id="order-search-status")
                        yield Select([], prompt="Select stock/order book", allow_blank=True, id="order-instrument-select")
                        yield Select(
                            [(label, label) for label in ORDER_TYPE_CHOICES],
                            value="buy",
                            allow_blank=False,
                            id="regular-order-type",
                        )
                        yield Input(placeholder="Volume", id="regular-order-volume", type="integer")
                        yield Input(placeholder="Limit price (SEK)", id="regular-order-price", type="number")
                        yield Static("Order value: -", id="regular-order-value")
                        yield Select(
                            [(label, label) for label in ORDER_CONDITION_CHOICES],
                            value="normal",
                            allow_blank=False,
                            id="regular-order-condition",
                        )
                        yield Input(
                            value=default_valid_until,
                            placeholder=f"Valid until ({default_valid_until})",
                            id="regular-order-valid-until",
                        )
                        yield Input(placeholder='Type "PLACE" to enable live placement', id="regular-order-confirm")
                        with Horizontal():
                            yield Button("Review Only", id="order-dry-run", variant="default")
                            yield Button("Create Paper Order", id="order-place-live", variant="warning")
                with Vertical(id="cancel-modal"):
                    with Horizontal(classes="modal-header"):
                        yield Button("X", id="close-cancel-modal", classes="modal-close")
                        yield Static("Cancel Order", classes="modal-title")
                    yield Static("-", id="cancel-summary")
                    yield Static('Type "CANCEL" for live Avanza cancellation.', id="cancel-instructions")
                    yield Input(placeholder='Type "CANCEL" for live cancellation', id="cancel-confirm")
                    with Horizontal():
                        yield Button("Review Only", id="cancel-review", variant="default")
                        yield Button("Cancel Order", id="cancel-confirm-button", variant="error")
            with Vertical(id="orders-overlay"):
                with Horizontal(classes="modal-header"):
                    yield Button("X", id="close-orders-overlay", classes="modal-close")
                    yield Static("Orders", classes="modal-title")
                    yield Button("Refresh", id="refresh-orders-overlay", variant="primary")
                yield Static("Completed buy/sell orders for the selected account.", id="orders-overlay-note")
                yield DataTable(id="orders-history-table")
            with Vertical(id="transactions-overlay"):
                with Horizontal(classes="modal-header"):
                    yield Button("X", id="close-transactions-overlay", classes="modal-close")
                    yield Static("Transactions", classes="modal-title")
                    yield Button("Refresh", id="refresh-transactions-overlay", variant="primary")
                yield Static("Executed orders and account transactions for the selected account.", id="transactions-overlay-note")
                yield DataTable(id="transactions-history-table")
            with Vertical(id="tv-lists-overlay"):
                with Horizontal(classes="modal-header"):
                    yield Button("X", id="close-tv-lists-overlay", classes="modal-close")
                    yield Static("TradingView Custom Lists", classes="modal-title")
                    yield Button("Refresh", id="refresh-tv-lists-overlay", variant="primary")
                yield Static(
                    f"Authenticated profile watchlists. Auto-refresh every {TRADINGVIEW_TUI_REFRESH_SECONDS:g}s while open.",
                    id="tv-lists-overlay-note",
                )
                with Horizontal(id="tv-lists-controls"):
                    yield Select([], prompt="Select TradingView list", allow_blank=True, id="tv-lists-select")
                    yield Button("Reload Lists", id="reload-tv-lists", variant="default")
                yield DataTable(id="tv-lists-table")
            with Vertical(id="extra-login-modal"):
                with Vertical(id="extra-login-card"):
                    yield Static("Login to extra accounts", id="extra-login-title")
                    yield Static("Add another tenant session without leaving the TUI.", id="extra-login-subtitle")
                    yield Input(placeholder="Session label (e.g. Personal, DarkCell AB)", id="extra-session-label")
                    yield Input(placeholder="Username", id="extra-username")
                    yield Input(placeholder="Password", id="extra-password", password=True)
                    yield Input(
                        placeholder="Current TOTP code",
                        id="extra-totp",
                        password=True,
                        restrict=r"[0-9]*",
                        max_length=8,
                    )
                    with Horizontal(classes="extra-login-actions"):
                        yield Button("Login extra account", id="extra-login-submit", variant="primary")
                        yield Button("Cancel", id="extra-login-cancel", variant="default")
                    yield Static("Or use 1Password CLI", id="extra-onepassword-title")
                    yield Input(placeholder="1Password item name or ID", id="extra-onepassword-item")
                    yield Input(placeholder="1Password vault (optional)", id="extra-onepassword-vault")
                    with Horizontal(classes="extra-login-actions"):
                        yield Button("Login with 1Password", id="extra-onepassword-login", variant="primary")
                        yield Button("Cancel", id="extra-login-cancel-2", variant="default")
        with Horizontal(id="status-bar"):
            yield Static("Update: checking...", id="update-status")
        yield Footer()

    def on_mount(self) -> None:
        stoploss_table = self.query_one("#stoploss-table", DataTable)
        stoploss_table.add_columns(
            "Kind",
            "Status",
            "Stock",
            "Side",
            "Volume",
            "Price",
            "Valid Until",
            "Cancel",
        )
        stoploss_table.cursor_type = "cell"
        stoploss_table.zebra_stripes = True

        portfolio_table = self.query_one("#portfolio-table", DataTable)
        portfolio_table.add_columns(
            "Stock",
            "B",
            "S",
            "Order Book ID",
            "Volume",
            "Value",
            "Avg Price",
            "Day %",
            "Day SEK",
            "Profit %",
            "Profit",
            "Real-time",
        )
        portfolio_table.cursor_type = "cell"
        portfolio_table.zebra_stripes = True

        active_table = self.query_one("#active-trades-table", DataTable)
        active_table.add_columns(
            "Mode",
            "Kind",
            "Stock",
            "Side",
            "Volume",
            "Trigger/Price",
            "Valid/Created",
            "Status",
            "Cancel",
        )
        active_table.cursor_type = "cell"
        active_table.zebra_stripes = True

        orders_history_table = self.query_one("#orders-history-table", DataTable)
        orders_history_table.add_columns(
            "Date",
            "Side",
            "Stock",
            "Qty",
            "Price",
            "Amount",
            "Result",
            "Account",
        )
        orders_history_table.cursor_type = "row"
        orders_history_table.zebra_stripes = True

        transactions_history_table = self.query_one("#transactions-history-table", DataTable)
        transactions_history_table.add_columns(
            "Date",
            "Account",
            "Type",
            "Description",
            "Qty",
            "Price",
            "Amount",
            "Result",
            "ISIN",
        )
        transactions_history_table.cursor_type = "row"
        transactions_history_table.zebra_stripes = True

        tv_lists_table = self.query_one("#tv-lists-table", DataTable)
        tv_lists_table.add_columns(
            "Symbol",
            "Last",
            "Chg",
            "Chg%",
            "Volume",
            "Status",
        )
        tv_lists_table.cursor_type = "row"
        tv_lists_table.zebra_stripes = True

        self.write_log("Ready. Log in, then refresh portfolio or stop-losses.")
        if self.debug_mode and self.debug_session_log_path is not None:
            self.write_log(f"[yellow]Debug profiling enabled:[/yellow] {self.debug_session_log_path}")
        self.write_mcp_log("MCP disabled. Log in, then enable MCP mode.")
        self.update_clock_status()
        self.start_clock()
        self.start_update_checker()
        self.start_background_session_heartbeat()
        if self.mcp_health_timer is None:
            self.mcp_health_timer = self.set_interval(MCP_HEALTH_CHECK_SECONDS, self.ensure_mcp_bridge_health, pause=False)
        if self.tv_lists_refresh_timer is None:
            self.tv_lists_refresh_timer = self.set_interval(
                TRADINGVIEW_TUI_REFRESH_SECONDS,
                self.refresh_tv_lists_if_visible,
                pause=False,
            )
        self.apply_ticket_pane_width(self.ticket_pane_width)
        self.apply_activity_subpane_weights(self.activity_table_weight, self.activity_logs_weight)
        self.update_mode_toggles()

    def on_resize(self, event: events.Resize) -> None:
        self.last_resize = (event.size.width, event.size.height)
        self.refresh(layout=True)
        for selector in ("#workspace", "#main", "#portfolio-table", "#stoploss-table", "#active-trades-table"):
            try:
                self.query_one(selector).refresh(layout=True)
            except Exception:
                pass
        if self.avanza and self.selected_account_id:
            self.call_after_refresh(self.refresh_selected_account_live)

    def apply_pane_weights(self, positions_weight: float, activity_weight: float) -> None:
        self.positions_pane_weight = positions_weight
        self.activity_pane_weight = activity_weight
        self.query_one("#positions-panel").styles.height = f"{positions_weight}fr"
        self.query_one("#activity-panel").styles.height = f"{activity_weight}fr"
        self.query_one("#main").refresh(layout=True)

    def apply_active_trades_width(self, width: int) -> None:
        self.active_trades_width = width
        self.query_one("#active-trades-panel").styles.width = width
        self.query_one("#body").refresh(layout=True)

    def apply_activity_subpane_weights(self, table_weight: float, logs_weight: float) -> None:
        self.activity_table_weight = table_weight
        self.activity_logs_weight = logs_weight
        self.query_one("#activity-table-section").styles.height = f"{table_weight}fr"
        self.query_one("#activity-logs-section").styles.height = f"{logs_weight}fr"
        self.query_one("#console-row").styles.height = "1fr"
        self.query_one("#activity-panel").refresh(layout=True)
        self.query_one("#activity-logs-section").refresh(layout=True)
        self.query_one("#console-row").refresh(layout=True)
        self.query_one("#log").refresh(layout=True)
        self.query_one("#mcp-log").refresh(layout=True)

    def apply_ticket_pane_width(self, width: int) -> None:
        self.ticket_pane_width = width
        for selector in ("#order-modal", "#stoploss-modal"):
            self.query_one(selector).styles.width = width
        self.refresh(layout=True)

    def start_pane_resize(self, screen_y: int) -> None:
        self.is_resizing_panes = True
        self.resize_start_y = screen_y
        self.resize_start_positions_weight = self.positions_pane_weight
        self.resize_start_activity_weight = self.activity_pane_weight

    def update_pane_resize(self, screen_y: int) -> None:
        if not self.is_resizing_panes:
            return
        delta_rows = screen_y - self.resize_start_y
        weights = pane_weights_after_drag(
            self.resize_start_positions_weight,
            self.resize_start_activity_weight,
            delta_rows,
        )
        self.apply_pane_weights(*weights)

    def finish_pane_resize(self) -> None:
        self.is_resizing_panes = False

    def start_activity_resize(self, screen_y: int) -> None:
        self.is_resizing_activity = True
        self.activity_resize_start_y = screen_y
        self.activity_resize_start_table_weight = self.activity_table_weight
        self.activity_resize_start_logs_weight = self.activity_logs_weight

    def update_activity_resize(self, screen_y: int) -> None:
        if not self.is_resizing_activity:
            return
        delta_rows = screen_y - self.activity_resize_start_y
        table_weight, logs_weight = pane_weights_after_drag(
            self.activity_resize_start_table_weight,
            self.activity_resize_start_logs_weight,
            delta_rows,
        )
        self.apply_activity_subpane_weights(table_weight, logs_weight)

    def finish_activity_resize(self) -> None:
        self.is_resizing_activity = False

    def start_side_pane_resize(self, screen_x: int) -> None:
        self.is_resizing_side_pane = True
        self.resize_start_x = screen_x
        self.resize_start_active_trades_width = self.active_trades_width

    def update_side_pane_resize(self, screen_x: int) -> None:
        if not self.is_resizing_side_pane:
            return
        delta_columns = screen_x - self.resize_start_x
        self.apply_active_trades_width(
            side_panel_width_after_drag(self.resize_start_active_trades_width, delta_columns)
        )

    def finish_side_pane_resize(self) -> None:
        self.is_resizing_side_pane = False

    def start_ticket_pane_resize(self, screen_x: int) -> None:
        self.is_resizing_ticket_pane = True
        self.resize_start_x = screen_x
        self.resize_start_ticket_pane_width = self.ticket_pane_width

    def update_ticket_pane_resize(self, screen_x: int) -> None:
        if not self.is_resizing_ticket_pane:
            return
        delta_columns = screen_x - self.resize_start_x
        self.apply_ticket_pane_width(
            ticket_pane_width_after_drag(self.resize_start_ticket_pane_width, delta_columns)
        )

    def finish_ticket_pane_resize(self) -> None:
        self.is_resizing_ticket_pane = False

    def sort_table(self, table: DataTable, column_key: Any, reverse: bool) -> None:
        table.sort(column_key, key=sortable_cell_value, reverse=reverse)
        if table.id:
            self.table_sort_state[table.id] = (column_key, reverse)

    def reapply_table_sort(self, table: DataTable) -> None:
        if not table.id:
            return
        state = self.table_sort_state.get(table.id)
        if not state:
            return
        column_key, reverse = state
        self.sort_table(table, column_key, reverse)

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        table = event.data_table
        previous_column, previous_reverse = self.table_sort_state.get(table.id or "", (None, False))
        reverse = not previous_reverse if previous_column == event.column_key else False
        self.sort_table(table, event.column_key, reverse)
        direction = "descending" if reverse else "ascending"
        self.write_log(f"Sorted {table.id or 'table'} by {event.label.plain} ({direction}).")
        event.stop()

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        if event.data_table.id == "portfolio-table":
            action = trade_action_from_cell(event.value)
            if action not in {"buy", "sell"}:
                return
            row_key = str(getattr(event.cell_key.row_key, "value", ""))
            target = self.portfolio_trade_targets_by_row_key.get(row_key)
            if not target:
                self.write_log("[yellow]Could not resolve stock row for order ticket.[/yellow]")
                return
            self.open_order_modal_for_portfolio_action(action, target)
            event.stop()
            return

        if event.data_table.id not in {"stoploss-table", "active-trades-table"}:
            return
        if plain_cell_value(event.value).strip() != "×":
            return
        row_key = str(getattr(event.cell_key.row_key, "value", ""))
        target = self.cancel_targets_by_row_key.get(row_key)
        if not target:
            self.write_log("[yellow]Could not resolve cancellation target for this row.[/yellow]")
            return
        self.open_cancel_modal(target)
        event.stop()


















































    def record_event(self, category: str, event: str, details: dict[str, Any] | None = None) -> None:
        record = {
            "timestamp": timestamp(),
            "category": category,
            "event": event,
            "details": details or {},
        }
        append_jsonl(self.session_log_path, record)
        category_file = LOG_CATEGORY_FILES.get(category)
        if category_file:
            append_jsonl(config.LOG_DIR / category_file, record)

    def debug_log(self, message: str) -> None:
        if not self.debug_mode or self.debug_session_log_path is None:
            return
        append_jsonl(
            self.debug_session_log_path,
            {
                "timestamp": timestamp(),
                "kind": "debug",
                "message": message,
            },
        )

    def run_profiled(self, label: str, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if not self.debug_mode or self.debug_profile_depth > 0:
            return callback(*args, **kwargs)

        profiler = cProfile.Profile()
        started = time.perf_counter()
        self.debug_profile_depth += 1
        try:
            profiler.enable()
            return callback(*args, **kwargs)
        finally:
            profiler.disable()
            elapsed = time.perf_counter() - started
            self.debug_profile_depth = max(0, self.debug_profile_depth - 1)
            if self.debug_session_log_path is None:
                return

            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            safe_label = re.sub(r"[^a-zA-Z0-9_.-]+", "_", label)
            prof_path = self.debug_session_log_path.with_name(f"profile-{safe_label}-{stamp}.prof")
            profiler.dump_stats(str(prof_path))
            self.debug_log(f"{label}: {elapsed:.3f}s -> {prof_path.name}")

            stream = io.StringIO()
            stats = pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats("cumtime")
            stats.print_stats(self.debug_profile_top)
            for line in stream.getvalue().splitlines():
                line = line.rstrip()
                if line:
                    self.debug_log(f"{label}: {line}")

    def write_log(self, message: str) -> None:
        stamped = f"{timestamp()} {message}"
        self.record_event("app", "console", {"message": strip_markup(message)})
        try:
            self.query_one("#log", RichLog).write(stamped)
        except Exception:
            # During shutdown the DOM may already be unmounted.
            pass

    def write_mcp_log(self, message: str) -> None:
        stamped = f"{timestamp()} {message}"
        self.record_event("mcp", "console", {"message": strip_markup(message)})
        try:
            self.query_one("#mcp-log", RichLog).write(stamped)
        except Exception:
            try:
                self.query_one("#log", RichLog).write(stamped)
            except Exception:
                # During shutdown the DOM may already be unmounted.
                pass


    def require_connection(self) -> Avanza:
        if self.avanza is None:
            raise RuntimeError("Log in first.")
        return self.avanza

    def require_selected_account_id(self) -> str:
        if not self.selected_account_id:
            raise RuntimeError("Select an account first.")
        return self.selected_account_id

    def stock_name_for_order_book(self, order_book_id: str) -> str:
        token = str(order_book_id or "").strip()
        if not token:
            return ""
        cached = str(self.holding_labels_by_order_book.get(token, "")).strip()
        if cached:
            return cached
        cached = str(self.order_search_labels_by_order_book.get(token, "")).strip()
        if cached:
            return cached
        data = self.latest_portfolio_data
        if isinstance(data, dict):
            for section in ("withOrderbook", "withoutOrderbook"):
                for item in data.get(section, []):
                    if not isinstance(item, dict):
                        continue
                    if position_order_book_id(item) == token:
                        return str(nested_value(item, "instrument", "name") or "").strip()
        return ""














    def save_paper_state(self) -> None:
        save_paper_session(self.paper_session, self.paper_session_path)
        self.update_active_trades_table()

    def set_mode_toggle(self, button_id: str, label_id: str, enabled: bool, text: str) -> None:
        button = self.query_one(f"#{button_id}", Button)
        button.label = "✓" if enabled else "×"
        button.remove_class("enabled")
        button.remove_class("disabled")
        button.add_class("enabled" if enabled else "disabled")
        self.query_one(f"#{label_id}", Static).update(text)

    def update_mode_toggles(self) -> None:
        self.set_mode_toggle("paper-mode-toggle", "paper-mode-label", self.paper_mode_enabled, "Paper")
        self.set_mode_toggle("mcp-toggle", "mcp-label", self.mcp_server is not None, "MCP")
        self.set_mode_toggle("mcp-write-toggle", "mcp-write-label", self.mcp_write_enabled, "Live R/W")

    def update_paper_mode_ui(self) -> None:
        stoploss_button = self.query_one("#place-live", Button)
        editing = bool(self.pending_stoploss_edit_id)
        if self.paper_mode_enabled:
            stoploss_button.label = "Update Paper Stop-Loss" if editing else "Create Paper Stop-Loss"
            stoploss_button.variant = "warning"
        else:
            stoploss_button.label = "Update Live Stop-Loss" if editing else "Submit Live Stop-Loss"
            stoploss_button.variant = "error"

        order_button = self.query_one("#order-place-live", Button)
        if self.paper_mode_enabled:
            order_button.label = "Create Paper Order"
            order_button.variant = "warning"
        else:
            order_button.label = "Submit Live Order"
            order_button.variant = "error"
        self.update_mode_toggles()

    def cycle_profit_metric(self) -> None:
        current_index = PROFIT_METRIC_MODES.index(self.profit_metric_mode)
        self.profit_metric_mode = PROFIT_METRIC_MODES[(current_index + 1) % len(PROFIT_METRIC_MODES)]
        self.update_selected_account_summary()
        self.write_log(f"Account P/L metric: {profit_metric_label(self.profit_metric_mode)}.")





































    def on_unmount(self) -> None:
        self.shutdown_event.set()
        self.stop_login_progress()
        if self.order_search_timer is not None:
            self.order_search_timer.stop()
            self.order_search_timer = None
        if self.live_refresh_timer is not None:
            self.live_refresh_timer.stop()
            self.live_refresh_timer = None
        if self.background_session_heartbeat_timer is not None:
            self.background_session_heartbeat_timer.stop()
            self.background_session_heartbeat_timer = None
        if self.clock_timer is not None:
            self.clock_timer.stop()
            self.clock_timer = None
        if self.mcp_health_timer is not None:
            self.mcp_health_timer.stop()
            self.mcp_health_timer = None
        if self.update_check_timer is not None:
            self.update_check_timer.stop()
            self.update_check_timer = None
        if self.update_blink_timer is not None:
            self.update_blink_timer.stop()
            self.update_blink_timer = None
        if self.tv_lists_refresh_timer is not None:
            self.tv_lists_refresh_timer.stop()
            self.tv_lists_refresh_timer = None
        for worker in (
            self.live_refresh_thread,
            self.background_session_heartbeat_thread,
            self.tv_lists_refresh_thread,
            self.update_check_thread,
        ):
            if worker is not None and worker.is_alive() and worker is not threading.current_thread():
                worker.join(timeout=0.5)
        self.live_refresh_thread = None
        self.background_session_heartbeat_thread = None
        self.tv_lists_refresh_thread = None
        self.update_check_thread = None
        self.stop_mcp_bridge(announce=False, wait=False)
















    def set_selected_account(self, account: dict[str, Any]) -> None:
        account_id = str(account.get("id", ""))
        if not account_id:
            raise ValueError("Selected account has no id.")

        self.selected_account_id = account_id
        self.update_selected_account_summary()
        account_select = self.query_one("#account-select", Select)
        if account_select.value != account_id:
            account_select.value = account_id
        self.write_log(f"Selected account {account_display_name(account)} ({account_id}).")
        self.sync_active_state_to_tenant()
        self.refresh_session_select_options()

    def restore_account_select_to_current(self) -> None:
        try:
            account_select = self.query_one("#account-select", Select)
        except Exception:
            return
        if not self.selected_account_id:
            return
        self.account_select_updating = True
        try:
            if account_select.value != self.selected_account_id:
                account_select.value = self.selected_account_id
        finally:
            self.account_select_updating = False










































    def account_by_id(self, account_id: str) -> dict[str, Any] | None:
        for account in self.accounts:
            if str(account.get("id", "")) == account_id:
                return account
        return None

    def select_account(self, account_id: str) -> None:
        account = self.account_by_id(account_id)
        if not account:
            raise ValueError(f"Unknown account id: {account_id}")
        self.set_selected_account(account)
        self.position_row_cache = {}
        self.apply_cached_account_snapshot(account_id)
        self.refresh_selected_account_live()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "session-select" and not self.is_blank_select_value(event.value):
            if self.session_select_updating:
                return
            next_session_id = str(event.value)
            if next_session_id == self.active_session_id:
                return
            try:
                self.activate_tenant_session(next_session_id)
            except Exception as exc:
                self.write_log(f"[red]Session switch failed:[/red] {exc}")
        elif event.select.id == "account-select" and not self.is_blank_select_value(event.value):
            if self.account_select_updating:
                return
            next_account_id = str(event.value)
            if next_account_id == self.selected_account_id:
                return
            if self.account_by_id(next_account_id) is None:
                self.debug_log(
                    f"Ignored stale account-select value {next_account_id!r} for session {self.active_session_id!r}."
                )
                self.restore_account_select_to_current()
                return
            try:
                self.select_account(next_account_id)
            except Exception as exc:
                self.write_log(f"[red]Account switch failed:[/red] {exc}")
        elif event.select.id == "instrument-select" and not self.is_blank_select_value(event.value):
            try:
                volume_input = self.query_one("#volume", Input)
            except Exception:
                return
            if not volume_input.value.strip():
                volume_input.value = self.holding_volumes_by_order_book.get(str(event.value), "")
        elif event.select.id == "order-instrument-select" and not self.is_blank_select_value(event.value):
            try:
                volume_input = self.query_one("#regular-order-volume", Input)
            except Exception:
                return
            if self.input_value("regular-order-type") == "sell" and not volume_input.value.strip():
                volume_input.value = self.holding_volumes_by_order_book.get(str(event.value), "")
            self.update_regular_order_value()
        elif event.select.id == "tv-lists-select":
            if self.tv_lists_select_updating:
                return
            if not self.is_blank_select_value(event.value):
                if str(event.value) == self.tv_lists_loaded_value:
                    return
                self.refresh_tv_lists(str(event.value))
        elif event.select.id == "regular-order-type":
            if str(event.value) == "sell":
                order_book_id = self.input_value("order-instrument-select")
                volume_input = self.query_one("#regular-order-volume", Input)
                if order_book_id and not volume_input.value.strip():
                    volume_input.value = self.holding_volumes_by_order_book.get(order_book_id, "")
            self.update_regular_order_value()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id in {"regular-order-volume", "regular-order-price"}:
            self.update_regular_order_value()
            return
        if event.input.id != "order-search":
            return
        query = event.value.strip()
        self.stop_order_search_timer()
        if len(query) < 2:
            self.query_one("#order-search-status", Static).update("Type at least 2 characters to search stocks.")
            if not query:
                self.restore_order_holding_options()
            return
        self.query_one("#order-search-status", Static).update(f"Searching '{query}'...")
        self.order_search_timer = self.set_timer(0.35, self.handle_order_search_from_timer)

    def on_switch_changed(self, event: Switch.Changed) -> None:
        # No action required for switch state changes; values are read on submit.
        return

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        try:
            if button_id == "login":
                self.handle_login()
            elif button_id == "onepassword-login":
                self.handle_1password_login()
            elif button_id == "open-extra-login":
                self.open_extra_login_modal()
            elif button_id == "logout-selected-session":
                self.logout_selected_session()
            elif button_id == "refresh-selected-session":
                self.handle_refresh_selected_session()
            elif button_id in {"extra-login-cancel", "extra-login-cancel-2"}:
                self.close_extra_login_modal()
            elif button_id == "extra-login-submit":
                self.handle_extra_login()
            elif button_id == "extra-onepassword-login":
                self.handle_extra_1password_login()
            elif button_id == "profit-cycle":
                self.cycle_profit_metric()
            elif button_id == "paper-mode-toggle":
                self.paper_mode_enabled = not self.paper_mode_enabled
                self.update_paper_mode_ui()
                mode = "paper" if self.paper_mode_enabled else "live"
                self.write_log(f"Order placement mode: {mode}.")
                self.record_event("trading", "paper_mode_changed", {"enabled": self.paper_mode_enabled})
            elif button_id == "mcp-toggle":
                if self.mcp_server is None:
                    self.start_mcp_bridge()
                else:
                    self.stop_mcp_bridge()
                self.update_mode_toggles()
            elif button_id == "mcp-write-toggle":
                self.mcp_write_enabled = not self.mcp_write_enabled
                if not self.mcp_write_enabled:
                    self.live_trading_allowed_for_session = False
                self.update_mcp_session_file()
                self.update_mode_toggles()
                mode = "read/write" if self.mcp_write_enabled else "read-only"
                self.write_mcp_log(f"MCP mode: {mode}.")
            elif button_id == "refresh-all":
                self.refresh_accounts()
                self.refresh_portfolio()
                self.refresh_stoplosses()
            elif button_id == "reload-tui":
                self.write_log("[yellow]Reloading TUI process to apply latest code changes...[/yellow]")
                self.exit({"reload_tui": True})
            elif button_id in {"refresh", "refresh-account"}:
                self.refresh_portfolio()
                self.refresh_stoplosses()
            elif button_id == "open-stoploss-modal":
                self.reset_stoploss_modal_for_new()
                self.query_one("#stoploss-modal").display = True
            elif button_id == "edit-stoploss":
                self.open_stoploss_edit_modal()
            elif button_id == "open-order-modal":
                self.query_one("#order-modal").display = True
            elif button_id == "open-orders-overlay":
                self.open_orders_overlay()
            elif button_id == "open-transactions-overlay":
                self.open_transactions_overlay()
            elif button_id == "open-tv-lists-overlay":
                self.open_tv_lists_overlay()
            elif button_id == "close-stoploss-modal":
                self.reset_stoploss_modal_for_new()
                self.query_one("#stoploss-modal").display = False
            elif button_id == "close-order-modal":
                self.query_one("#order-modal").display = False
            elif button_id == "close-orders-overlay":
                self.close_orders_overlay()
            elif button_id == "refresh-orders-overlay":
                self.refresh_orders_overlay()
            elif button_id == "close-transactions-overlay":
                self.close_transactions_overlay()
            elif button_id == "refresh-transactions-overlay":
                self.refresh_transactions_overlay()
            elif button_id == "close-tv-lists-overlay":
                self.close_tv_lists_overlay()
            elif button_id in {"refresh-tv-lists-overlay", "reload-tv-lists"}:
                self.refresh_tv_lists()
            elif button_id == "close-cancel-modal":
                self.close_cancel_modal()
            elif button_id == "clear-log":
                self.query_one("#log", RichLog).clear()
            elif button_id == "dry-run":
                self.handle_dry_run()
            elif button_id == "place-live":
                self.handle_place_live()
            elif button_id == "order-search-button":
                self.handle_order_search()
            elif button_id == "order-dry-run":
                self.handle_order_dry_run()
            elif button_id == "order-place-live":
                self.handle_order_place_live()
            elif button_id == "cancel-review":
                self.handle_cancel_review()
            elif button_id == "cancel-confirm-button":
                self.handle_cancel_confirm()
        except Exception as exc:
            self.write_log(f"[red]Error:[/red] {exc}")
