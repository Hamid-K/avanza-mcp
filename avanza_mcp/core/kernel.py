"""The UI-agnostic trading kernel.

`TradingKernel` owns every piece of state and behavior that is independent of
a particular front-end: authenticated Avanza tenant sessions, account and
quote caches, the paper-trading ledger, MCP bridge lifecycle and tool
dispatch, and the live-trading authorization gates. The Textual TUI and the
Web UI are both thin views over this kernel (one at a time — the two UI modes
are mutually exclusive per session).

Design notes:
- The kernel deliberately defines NO ``__init__``. Hosts call
  ``init_kernel_state()`` explicitly, which keeps ``AvanzaTradingTui``'s
  cooperative ``super().__init__()`` chain (into ``textual.App``) untouched.
- Hosts customize behavior through the seam methods (``write_log``,
  ``write_mcp_log``, ``safe_call_from_thread``, ``notify_user``,
  ``on_state_changed``); the kernel defaults are file-backed or no-ops so the
  kernel is fully functional headless.
- This module (and everything under ``avanza_mcp.core``) must never import
  ``textual`` — a guard test enforces it.
"""

import cProfile
import io
import pstats
import re
import threading
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable

from avanza import Avanza

from avanza_mcp import config
from avanza_mcp.config import (
    DEBUG_PROFILE_TOP_DEFAULT,
    GITHUB_RELEASE_REPO,
    LOG_CATEGORY_FILES,
)
from avanza_mcp.core.bridge import CoreBridgeMixin
from avanza_mcp.core.refresh import CoreRefreshMixin
from avanza_mcp.core.sessions import CoreSessionsMixin
from avanza_mcp.core.snapshots import CoreSnapshotsMixin
from avanza_mcp.core.trading import CoreTradingMixin
from avanza_mcp.models import AccountDataSnapshot, AvanzaTenantSession
from avanza_mcp.paper import load_paper_session, save_paper_session
from avanza_mcp.rendering import account_display_name, position_order_book_id
from avanza_mcp.utils import append_jsonl, create_session_log_path, nested_value, strip_markup, timestamp

if TYPE_CHECKING:
    from avanza_mcp.mcp.server import AvanzaMcpHttpServer


class TradingKernel(
    CoreBridgeMixin,
    CoreSnapshotsMixin,
    CoreSessionsMixin,
    CoreTradingMixin,
    CoreRefreshMixin,
):
    """Headless trading core shared by the TUI and Web front-ends."""

    def init_kernel_state(
        self,
        debug: bool = False,
        debug_profile_top: int = DEBUG_PROFILE_TOP_DEFAULT,
        log_kind: str = "kernel",
    ) -> None:
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
        self.shutdown_event = threading.Event()
        self.state_lock = threading.RLock()
        self.mcp_scope_original_session_id: str | None = None
        self.mcp_scope_depth = 0
        self.live_refresh_deferred_by_mcp_scope = False
        self.position_row_cache: dict[str, tuple[str, ...]] = {}
        self.holding_volumes_by_order_book: dict[str, str] = {}
        self.holding_labels_by_order_book: dict[str, str] = {}
        self.order_search_labels_by_order_book: dict[str, str] = {}
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
        self.live_trading_authorized_session_ids: set[str] = set()
        self.live_trading_allowed_for_session = False
        self.paper_mode_enabled = True
        self.paper_session_path = config.PAPER_SESSION_FILE
        self.paper_session = load_paper_session(self.paper_session_path)
        self.session_log_path = create_session_log_path(log_kind)
        self.latest_portfolio_data: dict[str, Any] | None = None
        self.latest_stoploss_items: list[dict[str, Any]] = []
        self.latest_open_order_items: list[dict[str, Any]] = []
        self.latest_tv_lists: list[dict[str, Any]] = []
        self.latest_tv_list_items: list[dict[str, Any]] = []
        self.account_snapshot_cache: dict[str, AccountDataSnapshot] = {}
        self.paper_quote_cache: dict[str, dict[str, Any]] = {}
        self.update_check_thread: threading.Thread | None = None
        self.update_check_inflight = False
        self.update_check_lock = threading.Lock()
        self.update_status_repo = GITHUB_RELEASE_REPO
        self.update_status_text = "Update: checking..."
        self.update_status_latest = ""
        self.update_status_outdated = False
        self.update_status_error = ""

    # ------------------------------------------------------------------
    # Seams — hosts override these to surface kernel activity in their UI.
    # Kernel defaults are file-backed (JSONL) or inert so headless use works.
    # ------------------------------------------------------------------

    def write_log(self, message: str) -> None:
        self.record_event("app", "console", {"message": strip_markup(message)})

    def write_mcp_log(self, message: str) -> None:
        self.record_event("mcp", "console", {"message": strip_markup(message)})

    def call_from_thread(self, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Headless marshaling seam: serialize cross-thread calls via the state lock.

        The TUI inherits textual.App.call_from_thread instead (UI-thread dispatch);
        the MCP bridge handler relies on this method on whichever host owns it.
        """
        with self.state_lock:
            return callback(*args, **kwargs)

    def safe_call_from_thread(self, callback: Callable[..., Any], *args: Any) -> bool:
        if self.shutdown_event.is_set():
            return False
        try:
            callback(*args)
        except Exception:
            return False
        return True

    def notify_user(self, message: str, severity: str = "information") -> None:
        self.write_log(message)

    def on_state_changed(self, channel: str, payload: Any = None) -> None:
        """Called after kernel state mutations. Hosts push UI updates here."""

    # ------------------------------------------------------------------
    # View-refresh hooks that kernel flows invoke; widget hosts override.
    # ------------------------------------------------------------------

    def update_mode_toggles(self) -> None:
        self.on_state_changed("mcp_status")

    def update_selected_account_summary(self) -> None:
        self.on_state_changed("portfolio")

    def update_active_trades_table(self) -> None:
        self.on_state_changed("stoplosses")

    def render_update_status(self) -> None:
        self.on_state_changed("update_check")

    def refresh_session_select_options(self) -> None:
        self.on_state_changed("sessions")

    def update_session_auth_badge(self) -> None:
        self.on_state_changed("sessions")

    # ------------------------------------------------------------------
    # Trading gates and account selection (headless defaults).
    # ------------------------------------------------------------------

    @property
    def live_trading_allowed_for_session(self) -> bool:
        """True when the ACTIVE tenant session has been explicitly armed for live trading.

        Authorization is per session id; entering ``temporary_tenant_scope``
        switches the active session, so MCP mutations scoped to another
        tenant are gated on that tenant's own authorization, never on a
        process-global flag.
        """
        active = str(self.active_session_id or "")
        return bool(active) and active in self.live_trading_authorized_session_ids

    @live_trading_allowed_for_session.setter
    def live_trading_allowed_for_session(self, value: bool) -> None:
        authorized = getattr(self, "live_trading_authorized_session_ids", None)
        if authorized is None:
            authorized = set()
            self.live_trading_authorized_session_ids = authorized
        if value:
            active = str(self.active_session_id or "")
            if active:
                authorized.add(active)
            else:
                self.write_log("[yellow]Live-trading authorization ignored: no active session.[/yellow]")
        else:
            # Revoke is deliberately global: turning authorization off (or
            # disabling R/W) disarms every session, never just the active one.
            authorized.clear()

    def live_mutations_allowed(self) -> bool:
        return (
            not self.paper_mode_enabled
            and self.mcp_write_enabled
            and self.live_trading_allowed_for_session
        )

    def save_paper_state(self) -> None:
        save_paper_session(self.paper_session, self.paper_session_path)
        self.on_state_changed("paper")

    def select_account(self, account_id: str) -> None:
        account = self.account_by_id(account_id)
        if not account:
            raise ValueError(f"Unknown account id: {account_id}")
        self.selected_account_id = str(account.get("id", ""))
        self.write_log(f"Selected account {account_display_name(account)} ({self.selected_account_id}).")
        self.sync_active_state_to_tenant()
        self.position_row_cache = {}
        self.on_state_changed("portfolio")

    def activate_tenant_session(self, session_id: str) -> None:
        self.activate_tenant_session_state(session_id)
        self.on_state_changed("sessions")

    def activate_tenant_session_state(self, session_id: str) -> None:
        session = self.tenant_session_by_id(session_id)
        if session is None:
            raise ValueError(f"Unknown session id: {session_id}")
        if self.active_session_id == session_id:
            return
        self.sync_active_state_to_tenant()
        self.load_active_state_from_tenant(session)

    # ------------------------------------------------------------------
    # File-backed logging / profiling (moved verbatim from the TUI shell).
    # ------------------------------------------------------------------

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

    def account_by_id(self, account_id: str) -> dict[str, Any] | None:
        for account in self.accounts:
            if str(account.get("id", "")) == account_id:
                return account
        return None
