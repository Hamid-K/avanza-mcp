"""Login flow: credential entry, 1Password, staged progress, extra sessions."""

import threading
import time

from avanza import Avanza

from avanza_mcp import auth
from avanza_mcp.core import login as core_login
from avanza_mcp.config import LOGIN_PROGRESS_FRAMES, LOGIN_PROGRESS_ROTATE_TICKS
from avanza_mcp.rendering import account_rows_from_overview, default_account, open_order_items
from avanza_mcp.utils import clamp
from textual.widgets import Button, Input, Static
from typing import Any, Callable


class LoginMixin:
    """Login flow: credential entry, 1Password, staged progress, extra sessions."""
    def clear_secret_inputs(self) -> None:
        self.query_one("#password", Input).value = ""
        self.query_one("#totp", Input).value = ""

    def set_login_controls_enabled(self, enabled: bool) -> None:
        for widget_id in (
            "username",
            "password",
            "totp",
            "onepassword-item",
            "onepassword-vault",
            "extra-session-label",
            "extra-username",
            "extra-password",
            "extra-totp",
            "extra-onepassword-item",
            "extra-onepassword-vault",
        ):
            try:
                self.query_one(f"#{widget_id}", Input).disabled = not enabled
            except Exception:
                pass
        for widget_id in (
            "login",
            "onepassword-login",
            "extra-login-submit",
            "extra-onepassword-login",
            "extra-login-cancel",
            "extra-login-cancel-2",
        ):
            try:
                self.query_one(f"#{widget_id}", Button).disabled = not enabled
            except Exception:
                pass

    def render_login_progress(self) -> None:
        spinner = LOGIN_PROGRESS_FRAMES[self.login_spinner_index % len(LOGIN_PROGRESS_FRAMES)]
        if self.login_stage_message:
            message = self.login_stage_message
        elif self.login_progress_messages:
            message = self.login_progress_messages[self.login_progress_index % len(self.login_progress_messages)]
        else:
            message = "Working..."
        detail = f"Step {self.login_progress_index + 1}/{max(1, len(self.login_progress_messages))}" if self.login_progress_messages else ""
        try:
            self.query_one("#login-progress", Static).update(f"{spinner} {message}")
            self.query_one("#login-progress-detail", Static).update(detail)
        except Exception:
            pass

    def advance_login_progress(self) -> None:
        if not self.login_busy:
            return
        self.login_spinner_index = (self.login_spinner_index + 1) % len(LOGIN_PROGRESS_FRAMES)
        self.login_progress_tick += 1
        if not self.login_stage_message and self.login_progress_messages and self.login_progress_tick % LOGIN_PROGRESS_ROTATE_TICKS == 0:
            self.login_progress_index = (self.login_progress_index + 1) % len(self.login_progress_messages)
        self.render_login_progress()

    def set_login_stage(self, message: str, index: int | None = None) -> None:
        if index is not None:
            if self.login_progress_messages:
                self.login_progress_index = int(clamp(index, 0, len(self.login_progress_messages) - 1))
            else:
                self.login_progress_index = max(0, index)
        self.login_stage_message = message
        self.render_login_progress()

    def start_login_progress(self, messages: tuple[str, ...], initial_message: str) -> None:
        self.login_busy = True
        self.login_spinner_index = 0
        self.login_progress_tick = 0
        self.login_progress_messages = messages
        self.login_progress_index = 0
        self.login_stage_message = initial_message
        self.set_login_controls_enabled(False)
        try:
            progress = self.query_one("#login-progress", Static)
            detail = self.query_one("#login-progress-detail", Static)
            progress.display = True
            detail.display = True
        except Exception:
            pass
        if self.login_progress_timer is not None:
            self.login_progress_timer.stop()
        self.login_progress_timer = self.set_interval(0.12, self.advance_login_progress, pause=False)
        self.render_login_progress()

    def stop_login_progress(self) -> None:
        self.login_busy = False
        if self.login_progress_timer is not None:
            self.login_progress_timer.stop()
            self.login_progress_timer = None
        self.login_target_mode = "initial"
        self.login_target_session_id = None
        self.login_target_session_label = None
        self.login_stage_message = ""
        self.login_progress_messages = ()
        try:
            self.query_one("#login-progress", Static).display = False
            self.query_one("#login-progress-detail", Static).display = False
        except Exception:
            pass
        self.set_login_controls_enabled(True)

    def clear_extra_secret_inputs(self) -> None:
        for widget_id in ("extra-password", "extra-totp"):
            try:
                self.query_one(f"#{widget_id}", Input).value = ""
            except Exception:
                pass

    def complete_login(
        self,
        avanza: Avanza,
        overview: dict[str, Any],
        portfolio: dict[str, Any],
        stoplosses: Any,
        orders: Any,
        target_mode: str = "initial",
        target_session_id: str | None = None,
        session_label: str | None = None,
    ) -> None:
        is_extra = str(target_mode or "").strip().lower() == "extra"
        refresh_token = str(target_session_id or "").strip()
        is_refresh = is_extra and bool(refresh_token) and refresh_token in self.tenant_sessions
        if is_refresh:
            context = self.tenant_sessions[refresh_token]
            accounts = account_rows_from_overview(overview)
            context.avanza = avanza
            if session_label:
                context.label = str(session_label)
            context.accounts = accounts
            selected = str(context.selected_account_id or "").strip()
            if not selected or not any(str(item.get("id", "")) == selected for item in accounts):
                default = default_account(accounts)
                context.selected_account_id = str(default.get("id", "")) if default else None
            context.latest_portfolio_data = portfolio if isinstance(portfolio, dict) else None
            context.latest_stoploss_items = [item for item in stoplosses if isinstance(item, dict)] if isinstance(stoplosses, list) else []
            context.latest_open_order_items = [item for item in open_order_items(orders) if isinstance(item, dict)]
            context.auth_valid = True
            context.auth_error = ""
            self.live_refresh_auth_blocked_sessions.discard(context.session_id)
            self.live_refresh_auth_last_notice_at.pop(context.session_id, None)
        else:
            context = self.register_tenant_session(
                avanza,
                overview,
                portfolio,
                stoplosses,
                orders,
                label=session_label,
            )
        self.update_tenant_session_data_cache(
            context.session_id,
            overview,
            portfolio if isinstance(portfolio, dict) else None,
            stoplosses,
            orders,
        )
        self.load_active_state_from_tenant(context)
        self.position_row_cache = {}
        self.query_one("#login-screen").display = False
        self.query_one("#workspace").display = True
        self.query_one("#extra-login-modal").display = False
        self.clear_secret_inputs()
        self.clear_extra_secret_inputs()
        self.refresh_session_select_options()
        self.apply_active_session_header()
        self.write_log(
            (
                f"[green]Session re-authenticated:[/green] {context.label} ({context.session_id})."
                if is_refresh
                else f"[green]Extra session logged in:[/green] {context.label} ({context.session_id})."
                if is_extra
                else "[green]Logged in. Secret fields cleared.[/green]"
            )
        )
        self.apply_accounts_overview(overview, announce=not is_extra)
        self.apply_portfolio_data(portfolio, fetch_quotes=False)
        self.apply_stoploss_orders_data(stoplosses, orders)
        self.start_live_refresh()
        self.update_session_auth_badge()

    def open_extra_login_modal(self, *, refresh_session_id: str | None = None) -> None:
        if not self.tenant_sessions:
            raise ValueError("Log in to your first account first.")
        refresh_token = str(refresh_session_id or "").strip()
        refresh_context = self.tenant_sessions.get(refresh_token) if refresh_token else None
        self.query_one("#extra-login-modal").display = True
        self.query_one("#extra-login-title", Static).update(
            "Refresh selected session login" if refresh_context is not None else "Login to extra accounts"
        )
        self.query_one("#extra-login-subtitle", Static).update(
            (
                f"Re-authenticate {refresh_context.label} to restore MCP/TUI access."
                if refresh_context is not None
                else "Add another tenant session without leaving the TUI."
            )
        )
        self.query_one("#extra-session-label", Input).value = refresh_context.label if refresh_context is not None else ""
        self.query_one("#extra-username", Input).value = ""
        self.clear_extra_secret_inputs()
        self.query_one("#extra-onepassword-item", Input).value = ""
        self.query_one("#extra-onepassword-vault", Input).value = ""
        self.login_target_session_id = refresh_context.session_id if refresh_context is not None else None

    def close_extra_login_modal(self) -> None:
        self.query_one("#extra-login-modal").display = False
        self.query_one("#extra-login-title", Static).update("Login to extra accounts")
        self.query_one("#extra-login-subtitle", Static).update("Add another tenant session without leaving the TUI.")
        self.query_one("#extra-session-label", Input).value = ""
        self.query_one("#extra-username", Input).value = ""
        self.query_one("#extra-onepassword-item", Input).value = ""
        self.query_one("#extra-onepassword-vault", Input).value = ""
        self.clear_extra_secret_inputs()
        self.login_target_session_id = None

    def handle_refresh_selected_session(self) -> None:
        session_id = self.selected_session_id()
        if not session_id:
            raise ValueError("No session is selected.")
        _ = self.tenant_session_by_id(session_id)
        self.open_extra_login_modal(refresh_session_id=session_id)

    def handle_extra_login(self) -> None:
        username = self.input_value("extra-username")
        password = self.input_value("extra-password")
        totp = self.input_value("extra-totp")
        if not username or not password or not totp:
            raise ValueError("Username, password, and TOTP are required.")

        label = self.extra_session_label()
        self.write_log(f"Logging in extra session '{label}'...")
        self.start_login_worker(
            self.login_worker_with_credentials,
            (
                {"username": username, "password": password, "totpToken": totp},
            ),
            (
                "Connecting to Avanza...",
                "Loading account overview...",
                "Loading portfolio...",
                "Loading stop-losses and open orders...",
                "Building workspace...",
            ),
            "Connecting to Avanza...",
            target_mode="extra",
            session_label=label,
            session_id=self.login_target_session_id,
        )

    def handle_extra_1password_login(self) -> None:
        item = self.input_value("extra-onepassword-item")
        vault = self.input_value("extra-onepassword-vault") or None
        if not item:
            raise ValueError("1Password item name or ID is required.")
        label = self.extra_session_label()
        self.write_log(f"Requesting extra-session credentials from 1Password ({label})...")
        self.start_login_worker(
            self.login_worker_with_1password,
            (item, vault),
            (
                "Waiting for 1Password approval...",
                "Reading credentials from 1Password...",
                "Connecting to Avanza...",
                "Loading account overview...",
                "Loading portfolio...",
                "Loading stop-losses and open orders...",
                "Building workspace...",
            ),
            "Waiting for 1Password approval...",
            target_mode="extra",
            session_label=label,
            session_id=self.login_target_session_id,
        )

    def handle_login(self) -> None:
        username = self.input_value("username")
        password = self.input_value("password")
        totp = self.input_value("totp")
        if not username or not password or not totp:
            raise ValueError("Username, password, and TOTP are required.")

        self.write_log("Logging in...")
        self.start_login_worker(
            self.login_worker_with_credentials,
            (
                {"username": username, "password": password, "totpToken": totp},
            ),
            (
                "Connecting to Avanza...",
                "Loading account overview...",
                "Loading portfolio...",
                "Loading stop-losses and open orders...",
                "Building workspace...",
            ),
            "Connecting to Avanza...",
            target_mode="initial",
        )

    def handle_1password_login(self) -> None:
        item = self.input_value("onepassword-item")
        vault = self.input_value("onepassword-vault") or None
        if not item:
            raise ValueError("1Password item name or ID is required.")

        self.write_log("Requesting Avanza credentials from 1Password CLI...")
        self.start_login_worker(
            self.login_worker_with_1password,
            (item, vault),
            (
                "Waiting for 1Password approval...",
                "Reading credentials from 1Password...",
                "Connecting to Avanza...",
                "Loading account overview...",
                "Loading portfolio...",
                "Loading stop-losses and open orders...",
                "Building workspace...",
            ),
            "Waiting for 1Password approval...",
            target_mode="initial",
        )

    def start_login_worker(
        self,
        target: Callable[..., None],
        args: tuple[Any, ...],
        progress_messages: tuple[str, ...],
        initial_message: str,
        *,
        target_mode: str = "initial",
        session_label: str | None = None,
        session_id: str | None = None,
    ) -> None:
        if self.login_busy:
            self.write_log("[yellow]Login already in progress...[/yellow]")
            return
        self.login_target_mode = target_mode
        self.login_target_session_id = str(session_id or "").strip() or None
        self.login_target_session_label = session_label
        self.start_login_progress(progress_messages, initial_message)
        worker = threading.Thread(target=target, args=args, daemon=True, name="avanza-login-worker")
        self.login_thread = worker
        worker.start()

    def run_login_stage_call(self, stage_message: str, stage_index: int, callback: Callable[..., Any], *args: Any) -> Any:
        self.call_from_thread(self.set_login_stage, stage_message, stage_index)
        done = threading.Event()
        started = time.perf_counter()

        def pulse() -> None:
            while not done.wait(1.0):
                elapsed = int(time.perf_counter() - started)
                try:
                    self.call_from_thread(
                        self.set_login_stage,
                        f"{stage_message} ({elapsed}s)",
                        stage_index,
                    )
                except RuntimeError:
                    break

        pulse_thread = threading.Thread(target=pulse, daemon=True, name="avanza-login-stage-pulse")
        pulse_thread.start()
        try:
            return callback(*args)
        finally:
            done.set()
            pulse_thread.join(timeout=0.2)

    def login_worker_with_credentials(self, credentials: dict[str, str]) -> None:
        self.perform_login(credentials, connect_stage_index=0)

    def login_worker_with_1password(self, item: str, vault: str | None) -> None:
        credentials = self.run_login_stage_call(
            "Reading credentials from 1Password...",
            1,
            auth.onepassword_credentials,
            item,
            vault,
        )
        self.perform_login(credentials, connect_stage_index=2)

    def perform_login(self, credentials: dict[str, str], connect_stage_index: int) -> None:
        try:
            result = core_login.perform_login_headless(
                credentials,
                connect_stage_index=connect_stage_index,
                run_stage=self.run_login_stage_call,
            )

            self.call_from_thread(self.set_login_stage, "Building workspace...", connect_stage_index + 4)
            self.call_from_thread(
                self.complete_login,
                result.avanza,
                result.overview,
                result.portfolio,
                result.stoplosses,
                result.orders,
                self.login_target_mode,
                self.login_target_session_id,
                self.login_target_session_label,
            )
            self.call_from_thread(self.stop_login_progress)
        except Exception as exc:
            self.call_from_thread(self.stop_login_progress)
            self.call_from_thread(self.write_log, f"[red]Login failed:[/red] {exc}")
