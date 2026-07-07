"""Order/stop-loss ticket building, dry runs, live placement, cancel flow."""


from avanza.constants import Condition, OrderType, StopLossPriceType, StopLossTriggerType
from avanza.entities import StopLossOrderEvent, StopLossTrigger
from avanza_mcp.config import STOPLOSS_ORDER_VALID_DAYS_DEFAULT
from avanza_mcp.market_data import order_account_id, order_stock_name
from avanza_mcp.paper import append_paper_event, cancel_paper_order, create_paper_order, create_paper_stop_loss_order
from avanza_mcp.records import flattened_search_hits, search_hit_label, search_hit_order_book_id
from avanza_mcp.rendering import (
    build_order_preview,
    enum_value,
    holding_search_options,
    money_text,
    order_request_log_lines,
    parse_price_type,
    stop_loss_request_log_lines,
    stoploss_holding_options,
)
from avanza_mcp.stoploss_rules import (
    enforce_live_stoploss_order_valid_days,
    max_valid_until_date,
    normalize_stoploss_order_valid_days,
    stoploss_triggered_order_expiry,
    validate_valid_until,
)
from avanza_mcp.tui.layout import selected_table_row_key
from datetime import date
from textual.widgets import Button, DataTable, Input, Select, Static, Switch
from typing import Any


class TradingMixin:
    """Order/stop-loss ticket building, dry runs, live placement, cancel flow."""
    def open_order_modal_for_portfolio_action(self, side: str, target: dict[str, str]) -> None:
        order_book_id = target.get("order_book_id", "")
        if not order_book_id:
            raise ValueError("Selected stock row has no order book id.")

        self.query_one("#order-search", Input).value = ""
        if self.latest_portfolio_data is not None:
            self.restore_order_holding_options()

        select = self.query_one("#order-instrument-select", Select)
        self.query_one("#regular-order-type", Select).value = side
        if order_book_id not in self.holding_labels_by_order_book:
            stock = target.get("stock") or order_book_id
            volume = target.get("volume", "")
            owned = f" - owned {volume}" if volume else ""
            select.set_options([(f"{stock}{owned} ({order_book_id})", order_book_id)])
            self.holding_labels_by_order_book[order_book_id] = stock
            self.holding_volumes_by_order_book[order_book_id] = volume

        select.value = order_book_id
        volume_input = self.query_one("#regular-order-volume", Input)
        volume_input.value = target.get("volume", "") if side == "sell" else ""
        self.update_regular_order_value()
        stock_name = target.get("stock") or order_book_id
        self.query_one("#order-search-status", Static).update(f"{side.upper()} ticket opened for {stock_name}.")
        self.query_one("#order-modal").display = True

    def update_regular_order_value(self) -> None:
        try:
            volume_text = self.input_value("regular-order-volume")
            price_text = self.input_value("regular-order-price")
            if not volume_text or not price_text:
                self.query_one("#regular-order-value", Static).update("Order value: -")
                return
            volume = int(volume_text)
            price = float(price_text)
            self.query_one("#regular-order-value", Static).update(f"Order value: {money_text(volume * price, 'SEK')}")
        except Exception:
            self.query_one("#regular-order-value", Static).update("Order value: -")

    def input_value(self, widget_id: str) -> str:
        widget = self.query_one(f"#{widget_id}")
        if isinstance(widget, Input):
            return widget.value.strip()
        if isinstance(widget, Select):
            if self.is_blank_select_value(widget.value):
                return ""
            return str(widget.value)
        raise TypeError(f"Unsupported input widget: {widget_id}")

    def required_input_value(self, widget_id: str, label: str) -> str:
        value = self.input_value(widget_id)
        if not value:
            raise ValueError(f"{label} is required.")
        return value

    def input_float_value(self, widget_id: str, label: str) -> float:
        value = self.required_input_value(widget_id, label)
        try:
            return float(value)
        except ValueError as exc:
            raise ValueError(f"{label} must be a number.") from exc

    def input_int_value(self, widget_id: str, label: str) -> int:
        value = self.required_input_value(widget_id, label)
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"{label} must be a whole number.") from exc

    def input_date_value(self, widget_id: str, label: str) -> date:
        value = self.required_input_value(widget_id, label)
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{label} must be an ISO date, for example {date.today().isoformat()}.") from exc
        return validate_valid_until(parsed, label)

    def switch_value(self, widget_id: str) -> bool:
        return bool(self.query_one(f"#{widget_id}", Switch).value)

    def live_cancel_target(self, kind: str, item: dict[str, Any]) -> dict[str, str]:
        identifier = str(item.get("id", "") or item.get("orderId", ""))
        return {
            "mode": "Live",
            "kind": kind,
            "id": identifier,
            "account_id": order_account_id(item, self.selected_account_id),
            "stock": order_stock_name(item),
        }

    def paper_cancel_target(self, item: dict[str, Any]) -> dict[str, str]:
        request = item.get("request") if isinstance(item.get("request"), dict) else {}
        return {
            "mode": "Paper",
            "kind": str(item.get("kind", "Order")),
            "id": str(item.get("id", "")),
            "account_id": str(request.get("account_id") or self.selected_account_id or ""),
            "stock": str(item.get("instrument") or request.get("order_book_id") or ""),
        }

    def apply_stoploss_valid_days_safety(self, preview: dict[str, Any], *, live: bool) -> list[str]:
        order_book_id = str(preview.get("order_book_id") or "").strip()
        order_event = preview.get("stop_loss_order_event")
        if not isinstance(order_event, dict):
            return []
        valid_days = normalize_stoploss_order_valid_days(order_event.get("valid_days"), "order_valid_days")
        metadata = self.stoploss_metadata_for_orderbook(order_book_id) if order_book_id else {}
        warnings = enforce_live_stoploss_order_valid_days(valid_days, metadata, live=live)
        order_event["valid_days"] = valid_days
        order_event["derived_expiry_if_triggered_today"] = stoploss_triggered_order_expiry(valid_days)
        preview["warnings"] = warnings
        return warnings

    def build_stop_loss_request(self) -> tuple[StopLossTrigger, StopLossOrderEvent, dict[str, Any]]:
        selected_account_id = self.require_selected_account_id()
        order_book_id = self.input_value("instrument-select")
        if not order_book_id:
            raise ValueError("Select a portfolio holding first.")
        valid_until = self.input_date_value("valid-until", "Stop-loss valid until")
        trigger = StopLossTrigger(
            type=enum_value(StopLossTriggerType, self.input_value("trigger-type")),
            value=self.input_float_value("trigger-value", "Trigger value"),
            valid_until=valid_until,
            value_type=enum_value(StopLossPriceType, self.input_value("trigger-value-type")),
            trigger_on_market_maker_quote=self.switch_value("trigger-on-market-maker-quote"),
        )
        order_event = StopLossOrderEvent(
            type=enum_value(OrderType, self.input_value("order-type")),
            price=self.input_float_value("order-price", "Order price"),
            volume=self.input_float_value("volume", "Volume"),
            valid_days=normalize_stoploss_order_valid_days(
                self.input_int_value("order-valid-days", "Order valid days"),
                "Order valid days",
            ),
            price_type=enum_value(StopLossPriceType, self.input_value("order-price-type")),
            short_selling_allowed=self.switch_value("short-selling-allowed"),
        )
        preview = {
            "account_id": selected_account_id,
            "order_book_id": order_book_id,
            "parent_stop_loss_id": "0",
            "stop_loss_trigger": {
                "type": trigger.type.value,
                "value": trigger.value,
                "valid_until": trigger.valid_until.isoformat(),
                "value_type": trigger.value_type.value,
                "trigger_on_market_maker_quote": trigger.trigger_on_market_maker_quote,
            },
            "stop_loss_order_event": {
                "type": order_event.type.value,
                "price": order_event.price,
                "volume": order_event.volume,
                "valid_days": order_event.valid_days,
                "derived_expiry_if_triggered_today": stoploss_triggered_order_expiry(order_event.valid_days),
                "price_type": order_event.price_type.value,
                "short_selling_allowed": order_event.short_selling_allowed,
            },
            "warnings": [],
        }
        return trigger, order_event, preview

    def build_regular_order_request(self) -> tuple[OrderType, Condition, dict[str, Any]]:
        selected_account_id = self.require_selected_account_id()
        order_book_id = self.input_value("order-instrument-select")
        if not order_book_id:
            raise ValueError("Select a stock/order book first.")
        return build_order_preview(
            {
                "account_id": selected_account_id,
                "order_book_id": order_book_id,
                "order_type": self.input_value("regular-order-type"),
                "price": self.input_float_value("regular-order-price", "Limit price"),
                "valid_until": self.input_date_value("regular-order-valid-until", "Valid until"),
                "volume": self.input_int_value("regular-order-volume", "Volume"),
                "condition": self.input_value("regular-order-condition"),
            }
        )

    def reset_stoploss_modal_for_new(self) -> None:
        self.pending_stoploss_edit_id = None
        self.query_one("#stoploss-modal-title", Static).update("New Stop-Loss")
        self.query_one("#place-confirm", Input).value = ""
        self.query_one("#place-live", Button).label = "Create Paper Stop-Loss" if self.paper_mode_enabled else "Submit Live Stop-Loss"

    def selected_stoploss_item(self) -> dict[str, Any]:
        table = self.query_one("#active-trades-table", DataTable)
        row_key = selected_table_row_key(table)
        if row_key is None:
            raise ValueError("Select a stop-loss row in Active Stop-Losses first.")
        row_key_value = str(getattr(row_key, "value", row_key))
        target = self.cancel_targets_by_row_key.get(row_key_value, {})
        if str(target.get("kind", "")).lower() != "stop-loss":
            raise ValueError("Selected active row is not a stop-loss entry.")
        target_id = str(target.get("id", ""))
        item = next((entry for entry in self.latest_stoploss_items if str(entry.get("id", "")) == target_id), None)
        if item is None:
            raise ValueError("Could not resolve selected stop-loss entry.")
        return item

    def open_stoploss_edit_modal(self) -> None:
        item = self.selected_stoploss_item()
        orderbook = item.get("orderbook") if isinstance(item.get("orderbook"), dict) else {}
        trigger = item.get("trigger") if isinstance(item.get("trigger"), dict) else {}
        order = item.get("order") if isinstance(item.get("order"), dict) else {}
        order_book_id = str(orderbook.get("id", ""))
        if not order_book_id:
            raise ValueError("Selected stop-loss is missing order book id.")

        self.pending_stoploss_edit_id = str(item.get("id", ""))
        if not self.pending_stoploss_edit_id:
            raise ValueError("Selected stop-loss is missing id.")

        trigger_value_type = str(trigger.get("valueType", "") or "SEK")
        order_price_type = str(order.get("priceType", "") or "SEK")
        try:
            trigger_value_type = parse_price_type(trigger_value_type)
        except Exception:
            trigger_value_type = "monetary"
        try:
            order_price_type = parse_price_type(order_price_type)
        except Exception:
            order_price_type = "monetary"

        self.query_one("#stoploss-modal-title", Static).update("Edit Stop-Loss")
        self.query_one("#instrument-select", Select).value = order_book_id
        self.query_one("#volume", Input).value = str(order.get("volume", "") or "")
        self.query_one("#trigger-type", Select).value = str(trigger.get("type", "") or "follow-upwards").lower().replace("_", "-")
        self.query_one("#trigger-value", Input).value = str(trigger.get("value", "") or "")
        self.query_one("#trigger-value-type", Select).value = trigger_value_type
        self.query_one("#valid-until", Input).value = str(trigger.get("validUntil", "") or max_valid_until_date().isoformat())
        self.query_one("#order-type", Select).value = str(order.get("type", "") or "sell").lower()
        self.query_one("#order-price", Input).value = str(order.get("price", "") or "")
        self.query_one("#order-price-type", Select).value = order_price_type
        existing_valid_days_raw = order.get("validDays", STOPLOSS_ORDER_VALID_DAYS_DEFAULT)
        try:
            existing_valid_days = normalize_stoploss_order_valid_days(existing_valid_days_raw, "Order valid days")
        except Exception:
            existing_valid_days = STOPLOSS_ORDER_VALID_DAYS_DEFAULT
        safe_valid_days = 1 if existing_valid_days > 1 else existing_valid_days
        self.query_one("#order-valid-days", Input).value = str(safe_valid_days)
        if existing_valid_days > 1:
            self.write_log(
                "[yellow]Safety default applied:[/yellow] "
                f"edited stop-loss order-valid-days reset from {existing_valid_days} to 1."
            )
        self.query_one("#trigger-on-market-maker-quote", Switch).value = bool(trigger.get("triggerOnMarketMakerQuote", False))
        self.query_one("#short-selling-allowed", Switch).value = bool(order.get("shortSellingAllowed", False))
        self.query_one("#place-confirm", Input).value = ""
        self.query_one("#place-live", Button).label = "Update Paper Stop-Loss" if self.paper_mode_enabled else "Update Live Stop-Loss"
        self.query_one("#stoploss-modal").display = True
        self.write_log(f"Editing stop-loss for {orderbook.get('name', order_book_id)}.")

    def handle_dry_run(self) -> None:
        _, _, preview = self.build_stop_loss_request()
        self.apply_stoploss_valid_days_safety(preview, live=False)
        self.write_log("[yellow]Review-only stop-loss request. No paper or live order is created:[/yellow]")
        for line in stop_loss_request_log_lines(preview):
            self.write_log(line)

    def handle_order_dry_run(self) -> None:
        _, _, preview = self.build_regular_order_request()
        self.write_log("[yellow]Review-only buy/sell order request. No paper or live order is created:[/yellow]")
        for line in order_request_log_lines(preview):
            self.write_log(line)

    def cancel_summary_text(self, target: dict[str, str]) -> str:
        stock = f" {target['stock']}" if target.get("stock") else ""
        return f"{target.get('mode', '')} {target.get('kind', '')}{stock}\nAccount {target.get('account_id', '')}"

    def open_cancel_modal(self, target: dict[str, str]) -> None:
        self.pending_cancel_target = target
        self.query_one("#cancel-summary", Static).update(self.cancel_summary_text(target))
        self.query_one("#cancel-confirm", Input).value = ""
        button = self.query_one("#cancel-confirm-button", Button)
        if target.get("mode") == "Paper":
            self.query_one("#cancel-instructions", Static).update("Cancels the local paper order only. Avanza is not touched.")
            button.label = "Cancel Paper Order"
            button.variant = "warning"
        else:
            self.query_one("#cancel-instructions", Static).update('Type "CANCEL" to cancel this live Avanza order.')
            button.label = "Cancel Live Order"
            button.variant = "error"
        self.query_one("#cancel-modal").display = True

    def close_cancel_modal(self) -> None:
        self.query_one("#cancel-modal").display = False
        self.query_one("#cancel-confirm", Input).value = ""
        self.pending_cancel_target = None

    def handle_cancel_review(self) -> None:
        target = self.pending_cancel_target
        if not target:
            raise ValueError("Select an order to cancel first.")
        self.write_log("[yellow]Review-only cancel request. No order is cancelled:[/yellow]")
        self.write_log(self.cancel_summary_text(target).replace("[", "\\[").replace("]", "\\]"))

    def handle_cancel_confirm(self) -> None:
        target = self.pending_cancel_target
        if not target:
            raise ValueError("Select an order to cancel first.")
        identifier = target.get("id", "")
        if not identifier:
            raise ValueError("Selected order has no id.")

        if target.get("mode") == "Paper":
            paper_order = cancel_paper_order(self.paper_session, identifier)
            self.save_paper_state()
            self.record_event("trading", "paper_order_cancel_from_tui", {"order": paper_order})
            self.write_log(f"[green]Paper order cancelled:[/green] {identifier}")
            self.close_cancel_modal()
            return

        if self.input_value("cancel-confirm") != "CANCEL":
            raise ValueError('Type "CANCEL" before live cancellation.')
        account_id = target.get("account_id") or self.require_selected_account_id()
        avanza = self.require_connection()
        kind = target.get("kind", "")
        if kind == "Stop-loss":
            result = avanza.delete_stop_loss_order(account_id, identifier)
            event_name = "live_stoploss_cancel_from_tui"
        else:
            result = avanza.delete_order(account_id, identifier)
            event_name = "live_order_cancel_from_tui"
        self.record_event("trading", event_name, {"target": target, "result": result})
        self.write_log(f"[green]Live {kind.lower()} cancellation sent:[/green] {identifier}")
        self.close_cancel_modal()
        self.refresh_stoplosses()

    def restore_order_holding_options(self) -> None:
        if self.latest_portfolio_data is None:
            return
        holding_options = stoploss_holding_options(self.latest_portfolio_data, self.selected_account_id)
        select = self.query_one("#order-instrument-select", Select)
        previous_value = self.input_value("order-instrument-select")
        select.set_options(holding_options)
        values = {value for _, value in holding_options}
        if previous_value in values:
            select.value = previous_value
        elif holding_options:
            select.value = holding_options[0][1]
        self.order_search_labels_by_order_book = {}

    def stop_order_search_timer(self) -> None:
        if self.order_search_timer is not None:
            self.order_search_timer.stop()
            self.order_search_timer = None

    def handle_order_search_from_timer(self) -> None:
        self.stop_order_search_timer()
        try:
            self.handle_order_search(automatic=True)
        except Exception as exc:
            try:
                self.write_log(f"[yellow]Order search failed:[/yellow] {exc}")
            except Exception:
                pass

    def handle_order_search(self, automatic: bool = False) -> None:
        self.stop_order_search_timer()
        query = self.input_value("order-search")
        if len(query) < 2:
            self.query_one("#order-search-status", Static).update("Type at least 2 characters to search stocks.")
            raise ValueError("Type at least 2 characters to search stocks.")

        options: list[tuple[str, str]] = []
        labels_by_order_book: dict[str, str] = {}
        seen: set[str] = set()

        def add_option(label: str, order_book_id: str, stock_name: str | None = None) -> None:
            if not order_book_id or order_book_id in seen:
                return
            options.append((label, order_book_id))
            labels_by_order_book[order_book_id] = stock_name or label.split(" - owned", 1)[0]
            seen.add(order_book_id)

        if self.latest_portfolio_data is not None:
            for label, order_book_id in holding_search_options(self.latest_portfolio_data, self.selected_account_id, query):
                add_option(label, order_book_id)

        remote_error: Exception | None = None
        try:
            hits = flattened_search_hits(self.require_connection().search_for_stock(query, 20))
        except Exception as exc:
            remote_error = exc
            hits = []

        for hit in hits:
            order_book_id = search_hit_order_book_id(hit)
            add_option(search_hit_label(hit), order_book_id, str(hit.get("name") or ""))

        select = self.query_one("#order-instrument-select", Select)
        select.set_options(options)
        self.order_search_labels_by_order_book = labels_by_order_book
        if options:
            select.value = options[0][1]
            if remote_error is not None:
                self.query_one("#order-search-status", Static).update(
                    f"{len(options)} portfolio result(s). Remote search failed: {remote_error}"
                )
            else:
                self.query_one("#order-search-status", Static).update(f"{len(options)} result(s). First result selected.")
            if not automatic:
                self.write_log(f"Found {len(options)} stock/order book result(s) for '{query}'.")
        elif remote_error is not None:
            self.query_one("#order-search-status", Static).update(f"Search failed: {remote_error}")
            raise remote_error
        else:
            self.query_one("#order-search-status", Static).update(f"No stock/order book results for '{query}'.")
            if not automatic:
                self.write_log(f"[yellow]No stock/order book results for '{query}'.[/yellow]")

    def handle_place_live(self) -> None:
        is_edit = bool(self.pending_stoploss_edit_id)
        action_label = "updated" if is_edit else "created"
        if self.paper_mode_enabled:
            _, _, preview = self.build_stop_loss_request()
            warnings = self.apply_stoploss_valid_days_safety(preview, live=False)
            for warning in warnings:
                self.write_log(f"[yellow]Warning:[/yellow] {warning}")
            order_book_id = self.input_value("instrument-select")
            instrument = self.holding_labels_by_order_book.get(order_book_id, order_book_id)
            paper_order = create_paper_stop_loss_order(
                {
                    **preview,
                    "account_id": preview["account_id"],
                    "order_book_id": preview["order_book_id"],
                    "trigger_type": preview["stop_loss_trigger"]["type"],
                    "trigger_value": preview["stop_loss_trigger"]["value"],
                    "trigger_value_type": preview["stop_loss_trigger"]["value_type"],
                    "valid_until": preview["stop_loss_trigger"]["valid_until"],
                    "order_type": preview["stop_loss_order_event"]["type"],
                    "order_price": preview["stop_loss_order_event"]["price"],
                    "order_price_type": preview["stop_loss_order_event"]["price_type"],
                    "volume": preview["stop_loss_order_event"]["volume"],
                    "order_valid_days": preview["stop_loss_order_event"]["valid_days"],
                    "trigger_on_market_maker_quote": preview["stop_loss_trigger"]["trigger_on_market_maker_quote"],
                    "short_selling_allowed": preview["stop_loss_order_event"]["short_selling_allowed"],
                },
                instrument=str(instrument),
            )
            self.paper_session.setdefault("orders", []).append(paper_order)
            append_paper_event(self.paper_session, "paper_stoploss_set_from_tui", {"id": paper_order["id"], "request": paper_order["request"]})
            self.save_paper_state()
            self.record_event("trading", "paper_stoploss_set_from_tui", {"order": paper_order})
            self.write_log(f"[green]Paper stop-loss {action_label}:[/green] {paper_order['id']}")
            self.reset_stoploss_modal_for_new()
            self.query_one("#stoploss-modal").display = False
            return

        if self.input_value("place-confirm") != "PLACE":
            raise ValueError('Type "PLACE" in the confirmation field before live placement.')

        avanza = self.require_connection()
        trigger, order_event, preview = self.build_stop_loss_request()
        warnings = self.apply_stoploss_valid_days_safety(preview, live=True)
        for warning in warnings:
            self.write_log(f"[yellow]Warning:[/yellow] {warning}")
        self.write_log("[red]Placing live stop-loss request:[/red]")
        for line in stop_loss_request_log_lines(preview):
            self.write_log(line)

        if is_edit:
            edit_id = str(self.pending_stoploss_edit_id)
            account_id = self.require_selected_account_id()
            delete_result = avanza.delete_stop_loss_order(account_id, edit_id)
            result = avanza.place_stop_loss_order(
                parent_stop_loss_id="0",
                account_id=account_id,
                order_book_id=self.input_value("instrument-select"),
                stop_loss_trigger=trigger,
                stop_loss_order_event=order_event,
            )
            self.record_event(
                "trading",
                "live_stoploss_replace_from_tui",
                {"stop_loss_id": edit_id, "delete_result": delete_result, "request": preview, "result": result},
            )
        else:
            result = avanza.place_stop_loss_order(
                parent_stop_loss_id="0",
                account_id=self.require_selected_account_id(),
                order_book_id=self.input_value("instrument-select"),
                stop_loss_trigger=trigger,
                stop_loss_order_event=order_event,
            )
            self.record_event("trading", "live_stoploss_set_from_tui", {"request": preview, "result": result})
        if isinstance(result, dict):
            status = result.get("status") or result.get("orderRequestStatus") or "response received"
            identifier = result.get("stoplossOrderId") or result.get("orderId") or ""
            suffix = f" ({identifier})" if identifier else ""
            self.write_log(f"[green]Avanza status:[/green] {status}{suffix}")
        else:
            self.write_log("[green]Avanza accepted the request.[/green]")
        self.reset_stoploss_modal_for_new()
        self.query_one("#stoploss-modal").display = False
        self.refresh_stoplosses()

    def handle_order_place_live(self) -> None:
        order_type, condition, preview = self.build_regular_order_request()
        order_book_id = self.input_value("order-instrument-select")
        instrument = (
            self.order_search_labels_by_order_book.get(order_book_id)
            or self.holding_labels_by_order_book.get(order_book_id, order_book_id)
        )

        if self.paper_mode_enabled:
            paper_order = create_paper_order(preview, instrument=instrument)
            self.paper_session.setdefault("orders", []).append(paper_order)
            append_paper_event(self.paper_session, "paper_order_set_from_tui", {"id": paper_order["id"], "request": paper_order["request"]})
            self.save_paper_state()
            self.record_event("trading", "paper_order_set_from_tui", {"order": paper_order})
            self.write_log(f"[green]Paper order created:[/green] {paper_order['id']}")
            self.query_one("#order-modal").display = False
            return

        if self.input_value("regular-order-confirm") != "PLACE":
            raise ValueError('Type "PLACE" in the confirmation field before live placement.')

        avanza = self.require_connection()
        self.write_log("[red]Placing live buy/sell order request:[/red]")
        for line in order_request_log_lines(preview):
            self.write_log(line)

        result = avanza.place_order(
            account_id=preview["account_id"],
            order_book_id=preview["order_book_id"],
            order_type=order_type,
            price=preview["price"],
            valid_until=date.fromisoformat(preview["valid_until"]),
            volume=preview["volume"],
            condition=condition,
        )
        self.record_event("trading", "live_order_set_from_tui", {"request": preview, "result": result})
        if isinstance(result, dict):
            status = result.get("orderRequestStatus") or result.get("status") or "response received"
            identifier = result.get("orderId") or ""
            suffix = f" ({identifier})" if identifier else ""
            self.write_log(f"[green]Avanza status:[/green] {status}{suffix}")
        else:
            self.write_log("[green]Avanza accepted the order request.[/green]")
        self.query_one("#order-modal").display = False
        self.refresh_stoplosses()
