"""Data refresh: tables, overlays, live loop, heartbeat, clock, update checker."""

import threading

from avanza.constants import TransactionsDetailsType
from avanza_mcp import utils
from avanza_mcp.config import (
    BACKGROUND_SESSION_HEARTBEAT_SECONDS,
    LIVE_REFRESH_SECONDS,
    TRADINGVIEW_WATCHLIST_ROW_LIMIT,
    UPDATE_BLINK_INTERVAL_SECONDS,
    UPDATE_CHECK_INTERVAL_SECONDS,
)
from avanza_mcp.external import tradingview_data as tv_data
from avanza_mcp.paper import paper_orders
from avanza_mcp.records import (
    transaction_activity_row,
    transaction_matches_filters,
    transaction_order_history_row,
    transactions_items,
)
from avanza_mcp.rendering import (
    account_metric_values,
    account_rows_from_overview,
    active_paper_order_row,
    active_stop_loss_row,
    changed_position_row,
    default_account,
    market_clock_text,
    matches_account,
    open_order_activity_row,
    open_order_items,
    position_order_book_id,
    position_state_row,
    position_state_row_with_quote,
    position_trade_target,
    stoploss_holding_options,
    stoploss_volume_by_order_book,
    trade_action_badge,
)
from avanza_mcp.tui.layout import restore_table_row_selection, selected_table_row_key
from avanza_mcp.update_check import update_check_enabled
from rich.text import Text
from textual.widgets import Button, DataTable, Input, Select, Static
from typing import Any


class RefreshMixin:
    """Data refresh: tables, overlays, live loop, heartbeat, clock, update checker."""
    def apply_accounts_overview(self, overview: dict[str, Any], announce: bool = True) -> None:
        self.accounts = account_rows_from_overview(overview)
        account_options = [self.session_account_option(account) for account in self.accounts]
        account_select = self.query_one("#account-select", Select)
        self.account_select_updating = True
        try:
            account_select.set_options(account_options)
            if announce:
                self.write_log(f"Loaded {len(self.accounts)} account(s).")
            if self.accounts:
                selected = next((a for a in self.accounts if str(a.get("id", "")) == self.selected_account_id), None)
                if selected is None:
                    selected = default_account(self.accounts)
                if selected is not None:
                    self.set_selected_account(selected)
                    if account_select.value != self.selected_account_id:
                        account_select.value = self.selected_account_id
        finally:
            self.account_select_updating = False
        self.sync_active_state_to_tenant()
        self.refresh_session_select_options()
        self.apply_active_session_header()

    def apply_portfolio_data(
        self,
        data: dict[str, Any],
        fetch_quotes: bool = True,
        quote_payloads: dict[str, dict[str, Any] | None] | None = None,
        realtime_statuses: dict[str, str] | None = None,
        allow_status_lookup: bool = True,
    ) -> None:
        table = self.query_one("#portfolio-table", DataTable)
        selected_row_key = selected_table_row_key(table)
        table.clear()
        self.latest_portfolio_data = data
        self.update_selected_account_summary(data)

        holding_options = stoploss_holding_options(data, self.selected_account_id)
        holding_select = self.query_one("#instrument-select", Select)
        order_holding_select = self.query_one("#order-instrument-select", Select)
        previous_holding = self.input_value("instrument-select")
        previous_order_holding = self.input_value("order-instrument-select")
        order_search_query = self.input_value("order-search")
        holding_select.set_options(holding_options)
        if not order_search_query:
            order_holding_select.set_options(holding_options)
        if previous_holding and previous_holding in {value for _, value in holding_options}:
            holding_select.value = previous_holding
        elif holding_options:
            holding_select.value = holding_options[0][1]
        if not order_search_query:
            if previous_order_holding and previous_order_holding in {value for _, value in holding_options}:
                order_holding_select.value = previous_order_holding
            elif holding_options:
                order_holding_select.value = holding_options[0][1]
        self.holding_volumes_by_order_book = stoploss_volume_by_order_book(data, self.selected_account_id)
        self.holding_labels_by_order_book = {
            value: label.split(" - owned", 1)[0]
            for label, value in holding_options
        }
        selected_holding = self.input_value("instrument-select")
        volume_input = self.query_one("#volume", Input)
        if selected_holding and not volume_input.value.strip():
            volume_input.value = self.holding_volumes_by_order_book.get(selected_holding, "")

        count = 0
        next_cache: dict[str, tuple[str, ...]] = {}
        self.portfolio_trade_targets_by_row_key = {}
        for section in ("withOrderbook", "withoutOrderbook"):
            for item in data.get(section, []):
                if isinstance(item, dict):
                    if not matches_account(item, self.selected_account_id):
                        continue
                    row_key = str(item.get("id", f"{section}-{count}"))
                    if fetch_quotes:
                        order_book_id = position_order_book_id(item)
                        quote_payload = None
                        if quote_payloads is not None:
                            quote_payload = quote_payloads.get(order_book_id)
                        if quote_payload is None:
                            quote_payload = self.quote_payload_for_order_book(order_book_id)
                        realtime_status = None
                        if realtime_statuses is not None and order_book_id:
                            realtime_status = realtime_statuses.get(order_book_id)
                        if realtime_status is None:
                            realtime_status = self.realtime_status_for_position(
                                item,
                                quote_payload,
                                allow_lookup=allow_status_lookup,
                            )
                        current_row = position_state_row_with_quote(
                            item,
                            quote_payload,
                            realtime_status,
                        )
                    else:
                        current_row = position_state_row(item, "Unknown")
                    previous_row = self.position_row_cache.get(row_key)
                    changed_row = changed_position_row(current_row, previous_row)
                    table.add_row(
                        changed_row[0],
                        trade_action_badge("buy"),
                        trade_action_badge("sell"),
                        *changed_row[1:],
                        key=row_key,
                    )
                    next_cache[row_key] = current_row
                    self.portfolio_trade_targets_by_row_key[row_key] = position_trade_target(item)
                    count += 1

        self.position_row_cache = next_cache
        self.reapply_table_sort(table)
        restore_table_row_selection(table, selected_row_key)
        suffix = f" for account {self.selected_account_id}" if self.selected_account_id else ""
        self.write_log(f"Loaded {count} portfolio row(s){suffix}.")
        self.sync_active_state_to_tenant()

    def apply_stoploss_orders_data(self, stoplosses: Any, orders: Any) -> None:
        table = self.query_one("#stoploss-table", DataTable)
        selected_row_key = selected_table_row_key(table)
        table.clear()
        self.stoploss_items_by_row_key = {}
        self.cancel_targets_by_row_key = {
            key: value
            for key, value in self.cancel_targets_by_row_key.items()
            if not key.startswith(("stoploss-", "order-", "active-"))
        }
        self.latest_stoploss_items = []
        if isinstance(stoplosses, list):
            for item in stoplosses:
                if isinstance(item, dict):
                    if not matches_account(item, self.selected_account_id):
                        continue
                    self.latest_stoploss_items.append(item)
        else:
            self.write_log(f"[yellow]Unexpected stop-loss response type:[/yellow] {type(stoplosses).__name__}")

        order_items = open_order_items(orders)
        order_count = 0
        self.latest_open_order_items = []
        for item in order_items:
            if isinstance(item, dict):
                if not matches_account(item, self.selected_account_id):
                    continue
                self.latest_open_order_items.append(item)
                row_key = f"order-{item.get('id', '') or item.get('orderId', '') or order_count}"
                table.add_row(*open_order_activity_row(item), key=row_key)
                self.cancel_targets_by_row_key[row_key] = self.live_cancel_target("Order", item)
                order_count += 1

        self.reapply_table_sort(table)
        restore_table_row_selection(table, selected_row_key)
        self.update_active_trades_table()
        suffix = f" for account {self.selected_account_id}" if self.selected_account_id else ""
        self.write_log(f"Loaded {len(self.latest_stoploss_items)} active stop-loss order(s) and {order_count} ongoing open order(s){suffix}.")
        self.sync_active_state_to_tenant()

    def apply_cached_account_snapshot(self, account_id: str) -> bool:
        context = self.active_tenant_session()
        if context is None:
            return False
        snapshot = context.account_snapshots.get(str(account_id or "").strip())
        if snapshot is None:
            return False
        applied = False
        if isinstance(snapshot.portfolio_data, dict):
            self.position_row_cache = {}
            self.apply_portfolio_data(snapshot.portfolio_data, fetch_quotes=False, allow_status_lookup=False)
            applied = True
        self.apply_stoploss_orders_data(snapshot.stoploss_items, snapshot.open_order_items)
        return applied

    def update_selected_account_summary(self, portfolio_data: dict[str, Any] | None = None) -> None:
        portfolio_data = portfolio_data or self.latest_portfolio_data
        account = self.account_by_id(self.selected_account_id) if self.selected_account_id else None
        metrics = account_metric_values(account, portfolio_data, self.selected_account_id, self.profit_metric_mode)
        self.query_one("#metric-total", Static).update(metrics["total"])
        self.query_one("#metric-buying", Static).update(metrics["buying"])
        self.query_one("#profit-cycle", Button).label = metrics["profit_label"].plain
        self.query_one("#metric-profit-value", Static).update(metrics["profit"])
        self.query_one("#metric-status", Static).update(metrics["status"])

    def update_clock_status(self) -> None:
        self.query_one("#clock-status", Static).update(market_clock_text())

    def start_clock(self) -> None:
        if self.clock_timer is None:
            self.clock_timer = self.set_interval(1.0, self.update_clock_status, pause=False)

    def render_update_status(self) -> None:
        text = self.update_status_text
        style = "dim"
        if self.update_check_inflight:
            style = "cyan"
        elif self.update_status_error:
            style = "red"
        elif self.update_status_outdated:
            style = "bold yellow" if self.update_status_blink_on else "yellow"
        elif self.update_status_latest:
            style = "green"
        try:
            self.query_one("#update-status", Static).update(Text(text, style=style))
        except Exception:
            pass

    def start_update_checker(self) -> None:
        if not update_check_enabled():
            self.update_status_text = "Update: disabled"
            self.render_update_status()
            return
        self.render_update_status()
        if self.update_check_timer is None:
            self.update_check_timer = self.set_interval(
                UPDATE_CHECK_INTERVAL_SECONDS,
                self.schedule_update_check,
                pause=False,
            )
        if self.update_blink_timer is None:
            self.update_blink_timer = self.set_interval(
                UPDATE_BLINK_INTERVAL_SECONDS,
                self.toggle_update_blink,
                pause=False,
            )
        self.schedule_update_check()

    def toggle_update_blink(self) -> None:
        if not self.update_status_outdated:
            return
        self.update_status_blink_on = not self.update_status_blink_on
        self.render_update_status()




    def active_trade_rows(self) -> list[tuple[Any, ...]]:
        rows: list[tuple[Any, ...]] = []
        rows.extend(active_stop_loss_row(item) for item in self.latest_stoploss_items)
        rows.extend(
            active_paper_order_row(item)
            for item in paper_orders(self.paper_session, self.selected_account_id, active_only=True)
            if str(item.get("kind", "")) == "Stop-loss"
        )
        return rows

    def active_trade_entries(self) -> list[tuple[tuple[Any, ...], dict[str, str]]]:
        entries: list[tuple[tuple[Any, ...], dict[str, str]]] = []
        entries.extend((active_stop_loss_row(item), self.live_cancel_target("Stop-loss", item)) for item in self.latest_stoploss_items)
        entries.extend(
            (active_paper_order_row(item), self.paper_cancel_target(item))
            for item in paper_orders(self.paper_session, self.selected_account_id, active_only=True)
            if str(item.get("kind", "")) == "Stop-loss"
        )
        return entries

    def update_active_trades_table(self) -> None:
        try:
            table = self.query_one("#active-trades-table", DataTable)
        except Exception:
            return
        selected_row_key = selected_table_row_key(table)
        table.clear()
        self.cancel_targets_by_row_key = {
            key: value
            for key, value in self.cancel_targets_by_row_key.items()
            if not key.startswith("active-")
        }
        for index, (row, target) in enumerate(self.active_trade_entries()):
            row_key = f"active-{index}-{row[0]}-{row[1]}-{row[2]}"
            table.add_row(*row, key=row_key)
            self.cancel_targets_by_row_key[row_key] = target
        restore_table_row_selection(table, selected_row_key)

    def _refresh_stoplosses_impl(self) -> None:
        avanza = self.require_connection()
        try:
            orders = avanza.get_orders()
        except Exception as exc:
            self.write_log(f"[yellow]Could not load open orders:[/yellow] {exc}")
            orders = []
        self.apply_stoploss_orders_data(avanza.get_all_stop_losses(), orders)

    def refresh_stoplosses(self) -> None:
        self.run_profiled("refresh_stoplosses", self._refresh_stoplosses_impl)

    def _refresh_orders_overlay_impl(self) -> None:
        avanza = self.require_connection()
        table = self.query_one("#orders-history-table", DataTable)
        selected_row_key = selected_table_row_key(table)
        table.clear()

        payload = avanza.get_transactions_details(
            transaction_details_types=[TransactionsDetailsType.BUY, TransactionsDetailsType.SELL],
            max_elements=5000,
        )
        items, _first_date = transactions_items(payload)
        rows = [
            transaction_order_history_row(item)
            for item in items
            if transaction_matches_filters(item, self.selected_account_id, executed_only=True)
        ]
        for index, row in enumerate(rows):
            table.add_row(*row, key=f"orders-history-{index}")
        restore_table_row_selection(table, selected_row_key)
        suffix = f" for account {self.selected_account_id}" if self.selected_account_id else ""
        self.write_log(f"Loaded {len(rows)} completed order row(s){suffix}.")

    def refresh_orders_overlay(self) -> None:
        self.run_profiled("refresh_orders_overlay", self._refresh_orders_overlay_impl)

    def open_orders_overlay(self) -> None:
        self.refresh_orders_overlay()
        self.query_one("#orders-overlay").display = True

    def close_orders_overlay(self) -> None:
        self.query_one("#orders-overlay").display = False

    def _refresh_transactions_overlay_impl(self) -> None:
        avanza = self.require_connection()
        table = self.query_one("#transactions-history-table", DataTable)
        selected_row_key = selected_table_row_key(table)
        table.clear()

        payload = avanza.get_transactions_details(
            transaction_details_types=list(TransactionsDetailsType),
            max_elements=5000,
        )
        items, _first_date = transactions_items(payload)
        rows = [
            transaction_activity_row(item)
            for item in items
            if transaction_matches_filters(item, self.selected_account_id, executed_only=False)
        ]
        for index, row in enumerate(rows):
            table.add_row(*row, key=f"transactions-history-{index}")
        restore_table_row_selection(table, selected_row_key)
        suffix = f" for account {self.selected_account_id}" if self.selected_account_id else ""
        self.write_log(f"Loaded {len(rows)} transaction row(s){suffix}.")

    def refresh_transactions_overlay(self) -> None:
        self.run_profiled("refresh_transactions_overlay", self._refresh_transactions_overlay_impl)

    def open_transactions_overlay(self) -> None:
        self.refresh_transactions_overlay()
        self.query_one("#transactions-overlay").display = True

    def close_transactions_overlay(self) -> None:
        self.query_one("#transactions-overlay").display = False

    def tv_list_selection(self, value: str | None = None) -> dict[str, str | None]:
        selection = value
        if selection is None:
            widget_value = self.query_one("#tv-lists-select", Select).value
            if not self.is_blank_select_value(widget_value):
                selection = str(widget_value)
        ref = self.tv_list_option_refs.get(str(selection or ""), {})
        list_id = str(ref.get("id", "") or "").strip() or None
        list_name = str(ref.get("name", "") or "").strip() or None
        return {"value": str(selection or ""), "list_id": list_id, "list_name": list_name}

    def refresh_tv_lists_if_visible(self) -> None:
        if self.query_one("#tv-lists-overlay").display:
            self.refresh_tv_lists()

    def refresh_tv_lists(self, selection_value: str | None = None) -> None:
        selection = self.tv_list_selection(selection_value)
        with self.tv_lists_refresh_lock:
            if self.tv_lists_refresh_inflight:
                self.tv_lists_refresh_pending_value = selection.get("value") or self.tv_lists_refresh_pending_value
                return
            self.tv_lists_refresh_inflight = True
        self.query_one("#tv-lists-overlay-note", Static).update("Loading TradingView custom lists...")
        self.tv_lists_refresh_thread = threading.Thread(
            target=self._refresh_tv_lists_worker,
            args=(selection.get("list_id"), selection.get("list_name")),
            daemon=True,
            name="avanza-tv-lists-refresh",
        )
        self.tv_lists_refresh_thread.start()

    def _refresh_tv_lists_worker(self, list_id: str | None, list_name: str | None) -> None:
        try:
            snapshot = tv_data.tradingview_custom_watchlists_from_profile(
                list_id=list_id,
                list_name=list_name,
                limit=TRADINGVIEW_WATCHLIST_ROW_LIMIT,
            )
            self.safe_call_from_thread(self.apply_tv_lists_snapshot, snapshot)
        except Exception as exc:
            self.safe_call_from_thread(self.write_log, f"[yellow]TradingView list refresh failed:[/yellow] {exc}")
            self.safe_call_from_thread(self.set_tv_lists_overlay_note, f"TradingView list refresh failed: {exc}")
        finally:
            if not self.safe_call_from_thread(self.finish_tv_lists_refresh):
                with self.tv_lists_refresh_lock:
                    self.tv_lists_refresh_pending_value = None
                    self.tv_lists_refresh_inflight = False

    def finish_tv_lists_refresh(self) -> None:
        pending_value = None
        with self.tv_lists_refresh_lock:
            pending_value = self.tv_lists_refresh_pending_value
            self.tv_lists_refresh_pending_value = None
            self.tv_lists_refresh_inflight = False
        if pending_value:
            self.refresh_tv_lists(pending_value)

    def apply_tv_lists_snapshot(self, snapshot: dict[str, Any]) -> None:
        list_entries = [entry for entry in snapshot.get("lists", []) if isinstance(entry, dict)]
        rows = [item for item in snapshot.get("items", []) if isinstance(item, dict)]
        self.latest_tv_lists = list_entries
        self.latest_tv_list_items = rows

        select = self.query_one("#tv-lists-select", Select)
        prior = select.value
        options: list[tuple[str, str]] = []
        refs: dict[str, dict[str, str]] = {}
        for index, entry in enumerate(list_entries):
            entry_id = str(entry.get("id", "") or "").strip()
            entry_name = str(entry.get("name", "") or entry.get("raw_label", "") or f"List {index + 1}").strip()
            count = utils.scalar_number(entry.get("count"))
            count_suffix = f" ({int(count)})" if count is not None else ""
            option_value = entry_id or f"list-{index}"
            options.append((f"{entry_name}{count_suffix}", option_value))
            refs[option_value] = {"id": entry_id, "name": entry_name}
        self.tv_list_option_refs = refs
        self.tv_lists_select_updating = True
        select.set_options(options)

        selected_entry = snapshot.get("selected_list") if isinstance(snapshot.get("selected_list"), dict) else {}
        selected_id = str(selected_entry.get("id", "") or "").strip()
        selected_name = str(selected_entry.get("name", "") or "").strip().lower()

        selected_value = None
        if not self.is_blank_select_value(prior) and str(prior) in refs:
            selected_value = str(prior)
        if selected_id:
            selected_value = next((value for value, ref in refs.items() if ref.get("id") == selected_id), selected_value)
        if selected_value is None and selected_name:
            selected_value = next(
                (value for value, ref in refs.items() if str(ref.get("name", "")).strip().lower() == selected_name),
                None,
            )
        if selected_value is None and options:
            selected_value = options[0][1]
        self.tv_lists_loaded_value = str(selected_value or "")
        if selected_value is not None:
            select.value = selected_value
        self.tv_lists_select_updating = False

        table = self.query_one("#tv-lists-table", DataTable)
        selected_row = selected_table_row_key(table)
        table.clear()
        for index, item in enumerate(rows):
            row = (
                str(item.get("symbol", "") or "-"),
                str(item.get("last", "") or "-"),
                str(item.get("change", "") or "-"),
                str(item.get("change_percent", "") or "-"),
                str(item.get("volume", "") or "-"),
                str(item.get("market_status", "") or "-"),
            )
            table.add_row(*row, key=f"tv-list-row-{index}")
        restore_table_row_selection(table, selected_row)

        active_name = str(snapshot.get("active_list_name", "") or "")
        as_of = str(snapshot.get("as_of", "") or "")
        self.set_tv_lists_overlay_note(f"Loaded {len(rows)} row(s) from {active_name or 'TradingView list'} at {as_of}.")
        if self.query_one("#tv-lists-overlay").display:
            self.write_log(f"Loaded {len(rows)} TradingView list row(s) from {active_name or 'selected list'}.")

    def set_tv_lists_overlay_note(self, message: str) -> None:
        self.query_one("#tv-lists-overlay-note", Static).update(message)

    def open_tv_lists_overlay(self) -> None:
        self.query_one("#tv-lists-overlay").display = True
        self.refresh_tv_lists()

    def close_tv_lists_overlay(self) -> None:
        self.query_one("#tv-lists-overlay").display = False

    def refresh_accounts(self) -> None:
        avanza = self.require_connection()
        overview = avanza.get_overview()
        if not isinstance(overview, dict):
            self.write_log(f"[yellow]Unexpected account overview response type:[/yellow] {type(overview).__name__}")
            return
        self.apply_accounts_overview(overview, announce=True)

    def _refresh_portfolio_impl(self) -> None:
        avanza = self.require_connection()
        data = avanza.get_accounts_positions()
        if not isinstance(data, dict):
            self.write_log(f"[yellow]Unexpected portfolio response type:[/yellow] {type(data).__name__}")
            return
        self.apply_portfolio_data(data, fetch_quotes=True, allow_status_lookup=False)

    def refresh_portfolio(self) -> None:
        self.run_profiled("refresh_portfolio", self._refresh_portfolio_impl)

    def action_refresh_stoplosses(self) -> None:
        try:
            self.refresh_stoplosses()
        except Exception as exc:
            self.write_log(f"[red]Refresh failed:[/red] {exc}")

    def action_refresh_portfolio(self) -> None:
        try:
            self.refresh_portfolio()
        except Exception as exc:
            self.write_log(f"[red]Portfolio refresh failed:[/red] {exc}")


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
        self.apply_portfolio_data(
            data,
            fetch_quotes=True,
            quote_payloads=quote_payloads,
            realtime_statuses=realtime_statuses,
            allow_status_lookup=False,
        )
        self.apply_stoploss_orders_data(stoplosses, orders)
        if self.debug_mode:
            self.debug_log(f"refresh_selected_account_live(background): {elapsed:.3f}s")
        self._finish_live_refresh_cycle()




    def start_background_session_heartbeat(self) -> None:
        if self.background_session_heartbeat_timer is None:
            self.background_session_heartbeat_timer = self.set_interval(
                BACKGROUND_SESSION_HEARTBEAT_SECONDS,
                self.refresh_background_sessions,
                pause=False,
            )


    def start_live_refresh(self) -> None:
        if self.live_refresh_timer is None:
            self.live_refresh_timer = self.set_interval(
                LIVE_REFRESH_SECONDS,
                self.refresh_selected_account_live,
                pause=False,
            )
            self.write_log(f"Live refresh enabled every {LIVE_REFRESH_SECONDS:g}s.")
