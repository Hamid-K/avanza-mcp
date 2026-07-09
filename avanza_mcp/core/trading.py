"""Order and stop-loss request building and submission, host-independent.

Hosts collect ticket fields their own way (TUI widgets, web JSON bodies),
normalize them into a plain ``fields`` dict, and call the builders here. The
``CoreTradingMixin`` submit methods hold the paper/live execution bodies the
TUI previously inlined; they report through the kernel logging seams and tag
ledger/audit events with the calling ``source`` ("tui", "web", ...).
"""

from datetime import date
from typing import Any

from avanza.constants import Condition, OrderType, StopLossPriceType, StopLossTriggerType
from avanza.entities import StopLossOrderEvent, StopLossTrigger

from avanza_mcp.market_data import order_account_id, order_stock_name
from avanza_mcp.paper import append_paper_event, cancel_paper_order, create_paper_order, create_paper_stop_loss_order
from avanza_mcp.rendering import (
    build_order_preview,
    enum_value,
    order_request_log_lines,
    stop_loss_request_log_lines,
)
from avanza_mcp.stoploss_rules import (
    enforce_live_stoploss_order_valid_days,
    normalize_stoploss_order_valid_days,
    stoploss_triggered_order_expiry,
)


def build_stop_loss_request_from_fields(fields: dict[str, Any]) -> tuple[StopLossTrigger, StopLossOrderEvent, dict[str, Any]]:
    account_id = str(fields.get("account_id") or "").strip()
    if not account_id:
        raise ValueError("Select an account first.")
    order_book_id = str(fields.get("order_book_id") or "").strip()
    if not order_book_id:
        raise ValueError("Select a portfolio holding first.")
    valid_until = fields["valid_until"]
    if not isinstance(valid_until, date):
        raise ValueError("Stop-loss valid until must be a date.")
    trigger = StopLossTrigger(
        type=enum_value(StopLossTriggerType, fields["trigger_type"]),
        value=float(fields["trigger_value"]),
        valid_until=valid_until,
        value_type=enum_value(StopLossPriceType, fields["trigger_value_type"]),
        trigger_on_market_maker_quote=bool(fields.get("trigger_on_market_maker_quote", False)),
    )
    order_event = StopLossOrderEvent(
        type=enum_value(OrderType, fields["order_type"]),
        price=float(fields["order_price"]),
        volume=float(fields["volume"]),
        valid_days=normalize_stoploss_order_valid_days(fields.get("order_valid_days"), "Order valid days"),
        price_type=enum_value(StopLossPriceType, fields["order_price_type"]),
        short_selling_allowed=bool(fields.get("short_selling_allowed", False)),
    )
    preview = {
        "account_id": account_id,
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


def build_regular_order_request_from_fields(fields: dict[str, Any]) -> tuple[OrderType, Condition, dict[str, Any]]:
    account_id = str(fields.get("account_id") or "").strip()
    if not account_id:
        raise ValueError("Select an account first.")
    order_book_id = str(fields.get("order_book_id") or "").strip()
    if not order_book_id:
        raise ValueError("Select a stock/order book first.")
    return build_order_preview(
        {
            "account_id": account_id,
            "order_book_id": order_book_id,
            "order_type": fields["order_type"],
            "price": fields["price"],
            "valid_until": fields["valid_until"],
            "volume": fields["volume"],
            "condition": fields["condition"],
        }
    )


class CoreTradingMixin:
    """Paper/live submission bodies shared by the TUI and Web front-ends."""

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

    def submit_paper_stop_loss(self, preview: dict[str, Any], instrument: str, *, source: str = "tui") -> dict[str, Any]:
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
        append_paper_event(self.paper_session, f"paper_stoploss_set_from_{source}", {"id": paper_order["id"], "request": paper_order["request"]})
        self.save_paper_state()
        self.record_event("trading", f"paper_stoploss_set_from_{source}", {"order": paper_order})
        return paper_order

    def submit_live_stop_loss(
        self,
        trigger: StopLossTrigger,
        order_event: StopLossOrderEvent,
        preview: dict[str, Any],
        *,
        replace_stoploss_id: str | None = None,
        source: str = "tui",
    ) -> Any:
        avanza = self.require_connection()
        self.write_log("[red]Placing live stop-loss request:[/red]")
        for line in stop_loss_request_log_lines(preview):
            self.write_log(line)

        account_id = str(preview.get("account_id") or "").strip() or self.require_selected_account_id()
        if replace_stoploss_id:
            # Place the replacement BEFORE deleting the old stop so a failed
            # placement can never leave the position unprotected. If Avanza
            # rejects a duplicate stop, the old one is still intact.
            result = avanza.place_stop_loss_order(
                parent_stop_loss_id="0",
                account_id=account_id,
                order_book_id=preview["order_book_id"],
                stop_loss_trigger=trigger,
                stop_loss_order_event=order_event,
            )
            try:
                delete_result = avanza.delete_stop_loss_order(account_id, replace_stoploss_id)
                protection_state = "replaced"
            except Exception as exc:
                delete_result = {"error": str(exc)}
                protection_state = "duplicate_protection"
                self.write_log(
                    f"[yellow]Warning:[/yellow] replacement stop-loss placed, but deleting the old one "
                    f"({replace_stoploss_id}) failed: {exc}. BOTH stops may now be active — review and delete manually."
                )
            self.record_event(
                "trading",
                f"live_stoploss_replace_from_{source}",
                {
                    "stop_loss_id": replace_stoploss_id,
                    "delete_result": delete_result,
                    "protection_state": protection_state,
                    "request": preview,
                    "result": result,
                },
            )
            if isinstance(result, dict):
                result = {**result, "replaced_stop_loss_id": replace_stoploss_id, "protection_state": protection_state}
        else:
            result = avanza.place_stop_loss_order(
                parent_stop_loss_id="0",
                account_id=account_id,
                order_book_id=preview["order_book_id"],
                stop_loss_trigger=trigger,
                stop_loss_order_event=order_event,
            )
            self.record_event("trading", f"live_stoploss_set_from_{source}", {"request": preview, "result": result})
        return result

    def submit_paper_order(self, preview: dict[str, Any], instrument: str, *, source: str = "tui") -> dict[str, Any]:
        paper_order = create_paper_order(preview, instrument=instrument)
        self.paper_session.setdefault("orders", []).append(paper_order)
        append_paper_event(self.paper_session, f"paper_order_set_from_{source}", {"id": paper_order["id"], "request": paper_order["request"]})
        self.save_paper_state()
        self.record_event("trading", f"paper_order_set_from_{source}", {"order": paper_order})
        return paper_order

    def submit_live_order(self, order_type: OrderType, condition: Condition, preview: dict[str, Any], *, source: str = "tui") -> Any:
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
        self.record_event("trading", f"live_order_set_from_{source}", {"request": preview, "result": result})
        return result

    def submit_cancel(self, target: dict[str, str], *, source: str = "tui") -> Any:
        identifier = str(target.get("id", ""))
        if not identifier:
            raise ValueError("Selected order has no id.")

        if target.get("mode") == "Paper":
            paper_order = cancel_paper_order(self.paper_session, identifier)
            self.save_paper_state()
            self.record_event("trading", f"paper_order_cancel_from_{source}", {"order": paper_order})
            self.write_log(f"[green]Paper order cancelled:[/green] {identifier}")
            return paper_order

        account_id = target.get("account_id") or self.require_selected_account_id()
        avanza = self.require_connection()
        kind = target.get("kind", "")
        if kind == "Stop-loss":
            result = avanza.delete_stop_loss_order(account_id, identifier)
            event_name = f"live_stoploss_cancel_from_{source}"
        else:
            result = avanza.delete_order(account_id, identifier)
            event_name = f"live_order_cancel_from_{source}"
        self.record_event("trading", event_name, {"target": target, "result": result})
        self.write_log(f"[green]Live {kind.lower()} cancellation sent:[/green] {identifier}")
        return result
