"""Rich rendering, row builders, badges, parsers, and order previews."""

import argparse
import re
from datetime import date, datetime, timedelta
from typing import Any

from avanza.constants import Condition, InstrumentType, OrderType, StopLossPriceType, StopLossTriggerType
from avanza.entities import StopLossOrderEvent, StopLossTrigger
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from avanza_mcp.config import (
    BUY_SIDE_STYLE,
    CHANGED_CELL_STYLE,
    DELAYED_KEYS,
    NEGATIVE_CELL_STYLE,
    NEGATIVE_PERCENT_STYLE,
    OVERVIEW_PERFORMANCE_KEYS,
    POSITION_CHANGE_COLUMNS,
    POSITIVE_CELL_STYLE,
    POSITIVE_PERCENT_STYLE,
    PRICE_TYPE_ALIASES,
    REALTIME_KEYS,
    SELL_SIDE_STYLE,
    STOPLOSS_ORDER_VALID_DAYS_DEFAULT,
    WINDOW_PERFORMANCE_KEYS,
    console,
)
from avanza_mcp.stoploss_rules import (
    max_valid_until_date,
    normalize_stoploss_order_valid_days,
    stoploss_triggered_order_expiry,
    validate_valid_until,
)
from avanza_mcp.utils import first_unit_text, first_value_number, nested_value, value_number

def next_weekday_start(day: date) -> datetime:
    current = day
    while current.weekday() >= 5:
        current = current + timedelta(days=1)
    return datetime.combine(current, datetime.min.time()).replace(hour=9)


def market_clock_text(now: datetime | None = None) -> str:
    now = now or datetime.now()
    open_time = now.replace(hour=9, minute=0, second=0, microsecond=0)
    close_time = now.replace(hour=17, minute=30, second=0, microsecond=0)

    if now.weekday() < 5 and open_time <= now < close_time:
        target = close_time
        label = "OMXS closes"
    else:
        if now.weekday() < 5 and now < open_time:
            target = open_time
        else:
            target = next_weekday_start(now.date() + timedelta(days=1))
        label = "OMXS opens"

    remaining = max(int((target - now).total_seconds()), 0)
    hours, remainder = divmod(remaining, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{now:%H:%M:%S}  {label} in {hours:02d}:{minutes:02d}:{seconds:02d}"


def price_type_label(value: Any) -> str:
    normalized = str(value).lower()
    if normalized == "percentage":
        return "%"
    if normalized == "monetary":
        return "SEK"
    return str(value)


def formatted_typed_value(value: Any, value_type: Any) -> str:
    label = price_type_label(value_type)
    if label == "%":
        return f"{value}%"
    if label == "SEK":
        return f"{value} SEK"
    return f"{value} {label}".strip()


def plain_cell_value(value: Any) -> str:
    if isinstance(value, Text):
        return value.plain
    return str(value)


def sortable_cell_value(value: Any) -> tuple[int, Any]:
    text = plain_cell_value(value).strip()
    if text == "●" and isinstance(value, Text):
        style = str(value.style)
        if POSITIVE_CELL_STYLE in style:
            return (1, 1)
        if CHANGED_CELL_STYLE in style:
            return (1, 0)
    normalized = text.lower().lstrip("●○ ").strip()
    if normalized in {"", "-", "none", "unknown"}:
        return (0, "")
    if normalized in {"no", "false"}:
        return (1, 0)
    if normalized in {"yes", "true"}:
        return (1, 1)

    if re.match(r"^[+-]?\d", text):
        number_text = re.sub(r"[^0-9,+.\\-]", "", text).replace(",", "")
        try:
            return (2, float(number_text))
        except ValueError:
            pass

    return (3, normalized)


def realtime_status_badge(status: str) -> Text:
    normalized = status.strip().lower()
    if normalized == "yes":
        return Text("●", style=POSITIVE_CELL_STYLE)
    return Text("●", style=CHANGED_CELL_STYLE)


def render_table(title: str, columns: list[str], rows: list[tuple[Any, ...]]) -> None:
    table = Table(title=title, show_lines=False)
    for column in columns:
        table.add_column(column, overflow="fold")

    for row in rows:
        table.add_row(*(str(value) for value in row))

    console.print(table)


def plain_row(row: tuple[Any, ...]) -> tuple[str, ...]:
    return tuple(plain_cell_value(value) for value in row)


def rows_as_dicts(columns: list[str], rows: list[tuple[Any, ...]]) -> list[dict[str, str]]:
    return [dict(zip(columns, plain_row(row))) for row in rows]


def render_message(title: str, lines: list[str]) -> None:
    console.print(Panel("\n".join(lines), title=title, expand=False))


def format_stop_loss_request(preview: dict[str, Any]) -> list[str]:
    trigger = preview["stop_loss_trigger"]
    order_event = preview["stop_loss_order_event"]
    lines = [
        f"Account: {preview['account_id']}",
        f"Order book: {preview['order_book_id']}",
        f"Trigger: {trigger['type']} {formatted_typed_value(trigger['value'], trigger['value_type'])}",
        f"Trigger valid until: {trigger['valid_until']}",
        f"Order: {order_event['type']} {order_event['volume']} @ {formatted_typed_value(order_event['price'], order_event['price_type'])}",
        f"Order valid days after trigger: {order_event['valid_days']}",
    ]
    derived_expiry = str(
        order_event.get("derived_expiry_if_triggered_today")
        or stoploss_triggered_order_expiry(int(order_event["valid_days"]))
    )
    lines.append(f"Derived order expiry (if triggered today): {derived_expiry}")
    warnings = preview.get("warnings")
    if isinstance(warnings, list):
        for warning in warnings:
            text = str(warning).strip()
            if text:
                lines.append(f"Warning: {text}")
    return lines


def format_order_request(preview: dict[str, Any]) -> list[str]:
    return [
        f"Account: {preview['account_id']}",
        f"Order book: {preview['order_book_id']}",
        f"Side: {preview['order_type']}",
        f"Volume: {preview['volume']}",
        f"Price: {preview['price']}{' ' + str(preview['currency']) if preview.get('currency') else ''}",
        f"Condition: {preview['condition']}",
        f"Valid until: {preview['valid_until']}",
    ]


def render_stop_loss_request(title: str, preview: dict[str, Any]) -> None:
    render_message(title, format_stop_loss_request(preview))


def render_order_request(title: str, preview: dict[str, Any]) -> None:
    render_message(title, format_order_request(preview))


def render_result(title: str, result: Any) -> None:
    if isinstance(result, dict):
        scalar_rows = [
            (key, value)
            for key, value in result.items()
            if not isinstance(value, (dict, list))
        ]
        if scalar_rows:
            render_table(title, ["Field", "Value"], scalar_rows)
            return

    render_message(title, ["Avanza accepted the request, but returned no concise status fields."])


def parse_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use YYYY-MM-DD format.") from exc
    try:
        return validate_valid_until(parsed)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def parse_price_type(value: str) -> str:
    normalized = value.strip().lower()
    try:
        return PRICE_TYPE_ALIASES[normalized]
    except KeyError as exc:
        raise argparse.ArgumentTypeError("Use SEK for an absolute value or % for a relative value.") from exc


def enum_value(enum_class: Any, value: str) -> Any:
    normalized = value.strip().upper().replace("-", "_")
    try:
        return enum_class[normalized]
    except KeyError as exc:
        choices = ", ".join(item.name.lower().replace("_", "-") for item in enum_class)
        raise argparse.ArgumentTypeError(f"Invalid value '{value}'. Choices: {choices}") from exc


def amount(data: dict[str, Any], *path: str) -> str:
    value = nested_value(data, *path)
    if isinstance(value, dict):
        raw = value.get("value", "")
        unit = value.get("unit", "")
        return f"{raw} {unit}".strip()
    return str(value)


def quantity_text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("value")
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)):
        number = float(value)
        return str(int(number)) if number.is_integer() else f"{number:g}"
    text = str(value).strip()
    try:
        number = float(text)
    except ValueError:
        return text
    return str(int(number)) if number.is_integer() else f"{number:g}"


def account_display_name(account: dict[str, Any]) -> str:
    name = account.get("name", "")
    if isinstance(name, dict):
        return str(name.get("userDefinedName") or name.get("defaultName") or "")
    return str(name)


ACCOUNT_TYPE_SHORT_LABELS = {
    "KAPITALFORSAKRING": "KF",
    "INVESTERINGSSPARKONTO": "ISK",
    "INVESTMENT_SAVINGS_ACCOUNT": "ISK",
    "AKTIE_&_FONDKONTO": "AF",
    "AKTIE_OCH_FONDKONTO": "AF",
    "AKTIE_FONDKONTO": "AF",
    "DEPA": "DEPA",
}


def compact_single_line(value: Any, max_len: int = 48) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_len:
        return text
    if max_len <= 1:
        return "…"
    return text[: max_len - 1].rstrip() + "…"


def compact_account_type(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    token = (
        text.upper()
        .replace("Ä", "A")
        .replace("Å", "A")
        .replace("Ö", "O")
        .replace("&", "_")
        .replace("/", "_")
        .replace("-", "_")
        .replace(" ", "_")
    )
    token = re.sub(r"_+", "_", token).strip("_")
    return ACCOUNT_TYPE_SHORT_LABELS.get(token, compact_single_line(text, max_len=12))


def account_row(account: dict[str, Any]) -> tuple[str, ...]:
    return (
        str(account.get("id", "")),
        account_display_name(account),
        str(account.get("type", "")),
        amount(account, "totalValue"),
        amount(account, "buyingPower"),
        str(account.get("status", "")),
    )


def account_rows_from_overview(overview: dict[str, Any]) -> list[dict[str, Any]]:
    accounts = overview.get("accounts", [])
    return [account for account in accounts if isinstance(account, dict) and account.get("id")]


def account_sort_value(account: dict[str, Any]) -> float:
    return value_number(account, "totalValue") or 0.0


def default_account(accounts: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not accounts:
        return None
    return max(accounts, key=account_sort_value)


def account_id_for_item(item: dict[str, Any]) -> str:
    return str(
        nested_value(item, "account", "id")
        or nested_value(item, "accountId")
        or nested_value(item, "account_id")
        or nested_value(item, "account", "accountId")
        or ""
    )


def matches_account(item: dict[str, Any], account_id: str | None) -> bool:
    return not account_id or account_id_for_item(item) == account_id


def looks_like_open_order(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    if not item:
        return False
    keys = {
        "status",
        "type",
        "orderType",
        "orderbook",
        "instrument",
        "validUntil",
        "orderId",
        "price",
        "volume",
        "account",
        "accountId",
        "account_id",
    }
    return any(key in item for key in keys)


def normalize_order_side(value: Any) -> str:
    text = str(value or "").strip().upper().replace("-", "_")
    if not text:
        return ""
    if "BUY" in text:
        return "BUY"
    if "SELL" in text:
        return "SELL"
    return ""


def open_order_side_value(item: dict[str, Any]) -> str:
    for key in ("side", "type", "orderType", "order_type"):
        side = normalize_order_side(item.get(key))
        if side:
            return side
    return normalize_order_side(item.get("__bucket_side"))


def open_order_id(item: dict[str, Any]) -> str:
    return str(item.get("orderId") or item.get("id") or "")


def open_order_account_id(item: dict[str, Any]) -> str:
    return str(
        item.get("accountId")
        or item.get("account_id")
        or nested_value(item, "account", "id")
        or nested_value(item, "account", "accountId")
        or ""
    )


def open_order_account_name(item: dict[str, Any]) -> str:
    return str(
        nested_value(item, "account", "name")
        or item.get("accountName")
        or nested_value(item, "account", "accountName")
        or ""
    )


def open_order_order_book_id(item: dict[str, Any]) -> str:
    return str(
        nested_value(item, "orderbook", "id")
        or nested_value(item, "instrument", "orderbook", "id")
        or item.get("orderBookId")
        or item.get("order_book_id")
        or nested_value(item, "instrument", "id")
        or ""
    )


def open_order_stock_name(item: dict[str, Any]) -> str:
    return str(
        nested_value(item, "orderbook", "name")
        or nested_value(item, "instrument", "name")
        or item.get("instrumentName")
        or item.get("name")
        or ""
    )


def open_order_value_scalar(value: Any) -> Any:
    if isinstance(value, dict):
        nested = value.get("value")
        if nested is not None:
            return nested
    return value


def open_order_volume_value(item: dict[str, Any]) -> Any:
    return open_order_value_scalar(item.get("volume"))


def open_order_price_value(item: dict[str, Any]) -> Any:
    return open_order_value_scalar(item.get("price"))


def open_order_valid_until(item: dict[str, Any]) -> str:
    return str(item.get("validUntil") or item.get("valid_until") or "")


def open_order_mcp_dict(item: dict[str, Any]) -> dict[str, Any]:
    order_id = open_order_id(item)
    account_id = open_order_account_id(item)
    account_name = open_order_account_name(item)
    order_book_id = open_order_order_book_id(item)
    stock = open_order_stock_name(item)
    side = open_order_side_value(item)
    volume = open_order_volume_value(item)
    price = open_order_price_value(item)
    return {
        "Order ID": order_id,
        "Account ID": account_id,
        "Account Name": account_name,
        "Order Book ID": order_book_id,
        "Stock": stock,
        "Side": side or "-",
        "Volume": volume,
        "Price": price,
        "Valid Until": open_order_valid_until(item),
        "Status": str(item.get("status", "") or ""),
        # Machine-friendly aliases for direct tool chaining.
        "order_id": order_id,
        "id": str(item.get("id", "") or ""),
        "orderId": str(item.get("orderId", "") or ""),
        "account_id": account_id,
        "order_book_id": order_book_id,
        "side": side or "",
    }


def fund_order_items(payload: Any) -> list[dict[str, Any]]:
    """Collect fund orders, which live outside the equity open-order buckets.

    Fund orders cannot be normalized into the equity order shape (no
    orderbook trading semantics) but hiding them entirely understates open
    order exposure, so they are surfaced as their own collection.
    """
    if not isinstance(payload, dict):
        return []
    collected: list[dict[str, Any]] = []
    for key in ("fundOrders", "fund_orders"):
        entries = payload.get(key)
        if isinstance(entries, list):
            collected.extend(item for item in entries if isinstance(item, dict))
    accounts = payload.get("accounts")
    if isinstance(accounts, list):
        for account in accounts:
            if not isinstance(account, dict):
                continue
            for key in ("fundOrders", "fund_orders"):
                entries = account.get(key)
                if isinstance(entries, list):
                    collected.extend(item for item in entries if isinstance(item, dict))
    return collected


def open_order_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if looks_like_open_order(item)]

    if not isinstance(payload, dict):
        return []

    collected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def push(items: Any, bucket_side: str = "") -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not looks_like_open_order(item):
                continue
            if bucket_side and not open_order_side_value(item):
                item = dict(item)
                item["__bucket_side"] = bucket_side
            identifier = str(item.get("id") or item.get("orderId") or id(item))
            if identifier in seen_ids:
                continue
            seen_ids.add(identifier)
            collected.append(item)

    # Common shapes.
    for key in ("orders", "items", "openOrders", "buyOrders", "sellOrders"):
        bucket_side = "BUY" if key == "buyOrders" else "SELL" if key == "sellOrders" else ""
        push(payload.get(key), bucket_side=bucket_side)

    # Account-grouped shapes: {"accounts":[{"id":"...","orders":[...]}]}
    accounts = payload.get("accounts")
    if isinstance(accounts, list):
        for account in accounts:
            if not isinstance(account, dict):
                continue
            account_id = str(account.get("id", ""))
            for key in ("orders", "items", "openOrders", "buyOrders", "sellOrders"):
                entries = account.get(key)
                if not isinstance(entries, list):
                    continue
                bucket_side = "BUY" if key == "buyOrders" else "SELL" if key == "sellOrders" else ""
                for item in entries:
                    if not isinstance(item, dict) or not looks_like_open_order(item):
                        continue
                    side_missing = not open_order_side_value(item)
                    if account_id and not account_id_for_item(item):
                        item = dict(item)
                        item["accountId"] = account_id
                    elif side_missing and bucket_side:
                        item = dict(item)
                    if bucket_side and side_missing:
                        item["__bucket_side"] = bucket_side
                    identifier = str(item.get("id") or item.get("orderId") or id(item))
                    if identifier in seen_ids:
                        continue
                    seen_ids.add(identifier)
                    collected.append(item)

    return collected


def recursive_flag(data: Any, keys: set[str]) -> bool | None:
    if isinstance(data, dict):
        for key, value in data.items():
            if key in keys and isinstance(value, bool):
                return value
        for value in data.values():
            nested = recursive_flag(value, keys)
            if nested is not None:
                return nested
    elif isinstance(data, list):
        for value in data:
            nested = recursive_flag(value, keys)
            if nested is not None:
                return nested
    return None


def realtime_status(item: dict[str, Any]) -> str:
    realtime = recursive_flag(item, REALTIME_KEYS)
    if realtime is not None:
        return "Yes" if realtime else "No"

    delayed = recursive_flag(item, DELAYED_KEYS)
    if delayed is not None:
        return "No" if delayed else "Yes"

    return "Unknown"


def instrument_type_enum(value: Any) -> InstrumentType | None:
    normalized = str(value or "").strip().lower().replace("-", "_")
    mapping = {
        "stock": InstrumentType.STOCK,
        "stocks": InstrumentType.STOCK,
        "aktie": InstrumentType.STOCK,
        "fund": InstrumentType.FUND,
        "certificate": InstrumentType.CERTIFICATE,
        "warrant": InstrumentType.WARRANT,
        "exchange_traded_fund": InstrumentType.EXCHANGE_TRADED_FUND,
        "exchange_traded_funds": InstrumentType.EXCHANGE_TRADED_FUND,
        "etf": InstrumentType.EXCHANGE_TRADED_FUND,
    }
    return mapping.get(normalized)


def unique_values(*values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            result.append(text)
            seen.add(text)
    return result


def first_known_realtime_status(*payloads: Any) -> str:
    for payload in payloads:
        if isinstance(payload, dict):
            status = realtime_status(payload)
            if status != "Unknown":
                return status
    return "Unknown"


def lookup_realtime_status(avanza: Any, item: dict[str, Any]) -> str:
    order_book_id = position_order_book_id(item)
    if not order_book_id:
        return realtime_status(item)

    market_data: dict[str, Any] | None = None
    try:
        market_data = avanza.get_market_data(order_book_id)
    except Exception:
        market_data = None

    order_book: dict[str, Any] | None = None
    try:
        order_book = avanza.get_order_book(order_book_id)
    except Exception:
        order_book = None

    status = first_known_realtime_status(item, market_data, order_book)
    if status != "Unknown":
        return status

    instrument = item.get("instrument") or {}
    orderbook = instrument.get("orderbook") or {}
    instrument_ids = unique_values(
        order_book.get("instrumentId") if isinstance(order_book, dict) else "",
        instrument.get("instrumentId"),
        orderbook.get("instrumentId"),
        order_book_id,
    )
    mapped_type = instrument_type_enum(
        order_book.get("instrumentType") if isinstance(order_book, dict) else ""
    ) or instrument_type_enum(instrument.get("type")) or instrument_type_enum(orderbook.get("type"))
    instrument_types = [mapped_type] if mapped_type else [
        InstrumentType.STOCK,
        InstrumentType.EXCHANGE_TRADED_FUND,
        InstrumentType.CERTIFICATE,
        InstrumentType.WARRANT,
    ]

    for instrument_id in instrument_ids:
        for instrument_type in instrument_types:
            if instrument_type is None:
                continue
            try:
                details = avanza.get_instrument_details(instrument_type, instrument_id)
            except Exception:
                details = None
            status = first_known_realtime_status(details)
            if status != "Unknown":
                return status

            try:
                summary = avanza.get_instrument(instrument_type, instrument_id)
            except Exception:
                summary = None
            status = first_known_realtime_status(summary)
            if status != "Unknown":
                return status

    return "Unknown"


def position_row(item: dict[str, Any]) -> tuple[str, ...]:
    instrument = item.get("instrument") or {}
    orderbook = instrument.get("orderbook") or {}
    performance = item.get("lastTradingDayPerformance") or {}

    return (
        str(nested_value(item, "account", "name")),
        str(nested_value(item, "account", "id")),
        str(instrument.get("name", "")),
        str(orderbook.get("id", "")),
        str(instrument.get("isin", "")),
        amount(item, "volume"),
        amount(item, "value"),
        amount(item, "averageAcquiredPrice"),
        amount(item, "acquiredValue"),
        amount(performance, "relative"),
    )


def percent_text(value: float | None) -> str:
    if value is None:
        return ""
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def money_text(value: float | None, unit: str = "SEK") -> str:
    if value is None:
        return ""
    sign = "+" if value > 0 else ""
    return f"{sign}{value:,.2f} {unit}"


def acquired_cost_basis(value: float | None) -> float | None:
    if value is None:
        return None
    return abs(value)


def account_profit_summary_from_avanza(account: dict[str, Any] | None) -> tuple[float | None, float | None, str]:
    if not isinstance(account, dict):
        return None, None, "SEK"
    amount_value = value_number(account, "profit", "absolute")
    percent_value = value_number(account, "profit", "relative")
    value_unit = str(nested_value(account, "profit", "absolute", "unit") or "SEK")
    return amount_value, percent_value, value_unit


def position_profit_summary_from_avanza(item: dict[str, Any]) -> tuple[float | None, float | None, str]:
    amount_paths = (
        ("profit", "absolute"),
        ("development", "absolute"),
        ("performanceSincePurchase", "absolute"),
        ("performanceSinceAcquired", "absolute"),
        ("outcome", "development"),
    )
    percent_paths = (
        ("profit", "relative"),
        ("development", "relative"),
        ("performanceSincePurchase", "relative"),
        ("performanceSinceAcquired", "relative"),
        ("outcome", "totalDevelopmentInPercent"),
        ("totalDevelopmentInPercent",),
    )
    profit_amount = first_value_number(item, amount_paths)
    profit_percent = first_value_number(item, percent_paths)
    value_unit = first_unit_text(item, amount_paths, str(nested_value(item, "value", "unit") or "SEK"))
    if profit_amount is None and profit_percent is None:
        current_value = value_number(item, "value")
        acquired_value = acquired_cost_basis(value_number(item, "acquiredValue"))
        if current_value is not None and acquired_value not in (None, 0):
            profit_amount = current_value - acquired_value
            profit_percent = (profit_amount / acquired_value) * 100
            value_unit = str(nested_value(item, "value", "unit") or value_unit)
    return profit_amount, profit_percent, value_unit


def metric_style(value: float | None) -> str:
    if value is None:
        return "dim"
    if value > 0:
        return POSITIVE_CELL_STYLE
    if value < 0:
        return NEGATIVE_CELL_STYLE
    return "dim"


def portfolio_profit_summary(data: dict[str, Any], account_id: str | None) -> tuple[float | None, float | None, str]:
    current_total = 0.0
    acquired_total = 0.0
    value_unit = "SEK"
    found = False

    for section in ("withOrderbook", "withoutOrderbook"):
        for item in data.get(section, []):
            if not isinstance(item, dict) or not matches_account(item, account_id):
                continue
            current_value = value_number(item, "value")
            acquired_value = acquired_cost_basis(value_number(item, "acquiredValue"))
            if current_value is None or acquired_value is None:
                continue
            current_total += current_value
            acquired_total += acquired_value
            value_unit = str(nested_value(item, "value", "unit") or value_unit)
            found = True

    if not found:
        return None, None, value_unit

    profit_amount = current_total - acquired_total
    profit_percent = (profit_amount / acquired_total) * 100 if acquired_total else None
    return profit_amount, profit_percent, value_unit


def portfolio_day_summary(
    data: dict[str, Any],
    account_id: str | None,
    account: dict[str, Any] | None = None,
) -> tuple[float | None, float | None, str]:
    day_total = 0.0
    position_total = 0.0
    value_unit = "SEK"
    found = False

    for section in ("withOrderbook", "withoutOrderbook"):
        for item in data.get(section, []):
            if not isinstance(item, dict) or not matches_account(item, account_id):
                continue
            performance = item.get("lastTradingDayPerformance") or {}
            day_value = value_number(performance, "absolute")
            current_value = value_number(item, "value")
            if current_value is not None:
                position_total += current_value
                value_unit = str(nested_value(item, "value", "unit") or value_unit)
            if day_value is None:
                continue
            day_total += day_value
            value_unit = str(nested_value(performance, "absolute", "unit") or value_unit)
            found = True

    if not found:
        return None, None, value_unit

    denominator = value_number(account or {}, "totalValue") or position_total
    day_percent = (day_total / denominator) * 100 if denominator else None
    return day_total, day_percent, value_unit


def portfolio_window_summary(
    data: dict[str, Any],
    account_id: str | None,
    mode: str,
    account: dict[str, Any] | None = None,
) -> tuple[float | None, float | None, str]:
    performance_keys = WINDOW_PERFORMANCE_KEYS.get(mode, ())
    if not performance_keys:
        return None, None, "SEK"

    absolute_total = 0.0
    weighted_relative_total = 0.0
    weighted_base_total = 0.0
    position_total = 0.0
    value_unit = "SEK"
    found_absolute = False
    found_relative = False

    for section in ("withOrderbook", "withoutOrderbook"):
        for item in data.get(section, []):
            if not isinstance(item, dict) or not matches_account(item, account_id):
                continue

            current_value = value_number(item, "value")
            if current_value is not None:
                position_total += current_value
                value_unit = str(nested_value(item, "value", "unit") or value_unit)

            performance: dict[str, Any] | None = None
            for key in performance_keys:
                candidate = item.get(key)
                if isinstance(candidate, dict):
                    performance = candidate
                    break
            if performance is None:
                continue

            absolute_value = value_number(performance, "absolute")
            if absolute_value is not None:
                absolute_total += absolute_value
                value_unit = str(nested_value(performance, "absolute", "unit") or value_unit)
                found_absolute = True

            relative_value = value_number(performance, "relative")
            if relative_value is not None and current_value is not None:
                weighted_relative_total += relative_value * current_value
                weighted_base_total += current_value
                found_relative = True

    amount_value = absolute_total if found_absolute else None
    percent_value: float | None = None
    if found_relative and weighted_base_total:
        percent_value = weighted_relative_total / weighted_base_total
    elif found_absolute:
        denominator = value_number(account or {}, "totalValue") or position_total
        percent_value = (absolute_total / denominator) * 100 if denominator else None

    return amount_value, percent_value, value_unit


def account_stats_text(
    account: dict[str, Any],
    portfolio_data: dict[str, Any] | None = None,
    account_id: str | None = None,
    include_label: bool = True,
) -> Text:
    text = Text()
    name = account_display_name(account)
    account_type = str(account.get("type", ""))
    account_status = str(account.get("status", ""))
    label = f"{name} ({account_type})" if account_type else name
    if include_label:
        text.append(label or "Selected account", style="bold")

    total = amount(account, "totalValue") or "-"
    buying_power = amount(account, "buyingPower") or "-"
    if include_label:
        text.append("  ", style="dim")
    text.append("Total ", style="dim")
    text.append(total, style="bold")
    text.append("  Buying ", style="dim")
    text.append(buying_power)

    if portfolio_data is not None:
        profit_amount, profit_percent, value_unit = account_profit_summary_from_avanza(account)
        if profit_amount is not None or profit_percent is not None:
            style = metric_style(profit_amount if profit_amount is not None else profit_percent)
            text.append("  Profit ", style="dim")
            if profit_amount is not None:
                text.append(money_text(profit_amount, value_unit), style=style)
            if profit_percent is not None:
                if profit_amount is not None:
                    text.append(" ")
                text.append(f"({percent_text(profit_percent)})", style=style)

    if account_status:
        text.append("  ")
        text.append(account_status, style="dim")
    return text


def account_metric_text(label: str, value: str, style: str = "bold") -> Text:
    text = Text()
    text.append(label, style="dim")
    text.append("\n")
    text.append(value or "-", style=style)
    return text


def profit_metric_label(mode: str) -> str:
    labels = {
        "day": "1D P/L",
        "week": "1W P/L",
        "month": "1M P/L",
        "year": "1Y P/L",
        "since_start": "Since Start P/L",
        "total": "Unrealized P/L",
    }
    return labels.get(mode, "1D P/L")


def profit_metric_value_text(amount_value: float | None, percent_value: float | None, unit: str = "SEK") -> Text:
    text = Text()
    if amount_value is None and percent_value is None:
        text.append("-", style="dim")
        return text

    metric_basis = amount_value if amount_value is not None else percent_value
    amount_style = metric_style(metric_basis)
    percent_style = (
        POSITIVE_PERCENT_STYLE if (metric_basis or 0) > 0
        else NEGATIVE_PERCENT_STYLE if (metric_basis or 0) < 0
        else "dim"
    )

    if amount_value is not None:
        text.append(money_text(amount_value, unit), style=amount_style)
    if percent_value is not None:
        if amount_value is not None:
            text.append("  ")
        text.append(percent_text(percent_value), style=percent_style)
    return text


def account_performance_window_summary(
    account: dict[str, Any] | None,
    mode: str,
) -> tuple[float | None, float | None, str]:
    if not account:
        return None, None, "SEK"

    performance_map = account.get("performance")
    if not isinstance(performance_map, dict):
        return None, None, "SEK"

    candidates = OVERVIEW_PERFORMANCE_KEYS.get(mode, ())
    key_lookup = {str(key).upper(): key for key in performance_map.keys()}
    selected = None
    for candidate in candidates:
        resolved_key = key_lookup.get(candidate.upper())
        if resolved_key is not None:
            value = performance_map.get(resolved_key)
            if isinstance(value, dict):
                selected = value
                break
    if not isinstance(selected, dict):
        return None, None, "SEK"

    amount_value = value_number(selected, "absolute")
    percent_value = value_number(selected, "relative")
    value_unit = str(nested_value(selected, "absolute", "unit") or nested_value(selected, "relative", "unit") or "SEK")
    return amount_value, percent_value, value_unit


def account_metric_values(
    account: dict[str, Any] | None,
    portfolio_data: dict[str, Any] | None = None,
    account_id: str | None = None,
    profit_mode: str = "day",
) -> dict[str, Text]:
    profit_label = profit_metric_label(profit_mode)
    if not account:
        return {
            "total": account_metric_text("Total", "-"),
            "buying": account_metric_text("Buying", "-"),
            "profit": profit_metric_value_text(None, None),
            "profit_label": Text(profit_label),
            "status": account_metric_text("Status", "-"),
        }
    total = amount(account, "totalValue") or "-"
    buying_power = amount(account, "buyingPower") or "-"
    status = str(account.get("status", "")) or "-"
    profit_amount: float | None = None
    profit_percent: float | None = None
    value_unit = "SEK"
    if portfolio_data is not None:
        if profit_mode == "total":
            profit_amount, profit_percent, value_unit = account_profit_summary_from_avanza(account)
        elif profit_mode == "day":
            profit_amount, profit_percent, value_unit = account_performance_window_summary(account, profit_mode)
            if profit_amount is None and profit_percent is None:
                profit_amount, profit_percent, value_unit = portfolio_day_summary(portfolio_data, account_id, account)
        elif profit_mode == "since_start":
            profit_amount, profit_percent, value_unit = account_performance_window_summary(account, profit_mode)
            if profit_amount is None and profit_percent is None:
                profit_amount, profit_percent, value_unit = account_profit_summary_from_avanza(account)
        else:
            profit_amount, profit_percent, value_unit = account_performance_window_summary(account, profit_mode)
    return {
        "total": account_metric_text("Total", total, "bold"),
        "buying": account_metric_text("Buying", buying_power, "bold"),
        "profit": profit_metric_value_text(profit_amount, profit_percent, value_unit),
        "profit_label": Text(profit_label),
        "status": account_metric_text("Status", status, "bold"),
    }


def position_state_row(item: dict[str, Any], realtime_override: str | None = None) -> tuple[str, ...]:
    instrument = item.get("instrument") or {}
    orderbook = instrument.get("orderbook") or {}
    performance = item.get("lastTradingDayPerformance") or {}
    profit_amount, profit_percent, profit_unit = position_profit_summary_from_avanza(item)
    value_unit = nested_value(item, "value", "unit") or "SEK"
    return (
        str(instrument.get("name", "")),
        str(orderbook.get("id", "")),
        amount(item, "volume"),
        amount(item, "value"),
        amount(item, "averageAcquiredPrice"),
        percent_text(value_number(performance, "relative")),
        money_text(value_number(performance, "absolute"), str(value_unit)),
        percent_text(profit_percent),
        money_text(profit_amount, profit_unit),
        realtime_status_badge(realtime_override or realtime_status(item)),
    )


def position_state_row_with_quote(
    item: dict[str, Any],
    quote: dict[str, Any] | None,
    realtime_override: str | None = None,
) -> tuple[str, ...]:
    # Keep financial values from account-position payload to avoid currency/FX
    # mismatches when quote feeds return instrument-currency prices.
    return position_state_row(item, realtime_override)


def position_trade_action_row(item: dict[str, Any], realtime_override: str | None = None) -> tuple[Any, ...]:
    row = position_state_row(item, realtime_override)
    return (row[0], trade_action_badge("buy"), trade_action_badge("sell"), *row[1:])


def position_order_book_id(item: dict[str, Any]) -> str:
    return str(nested_value(item, "instrument", "orderbook", "id"))


def position_holding_label(item: dict[str, Any]) -> str:
    instrument_name = str(nested_value(item, "instrument", "name"))
    owned_volume = amount(item, "volume")
    return f"{instrument_name} - owned {owned_volume}"


def position_trade_target(item: dict[str, Any]) -> dict[str, str]:
    return {
        "stock": str(nested_value(item, "instrument", "name")),
        "order_book_id": position_order_book_id(item),
        "volume": quantity_text(nested_value(item, "volume")),
    }


def stoploss_holding_options(positions: dict[str, Any], account_id: str | None) -> list[tuple[str, str]]:
    options: list[tuple[str, str]] = []
    seen: set[str] = set()
    for section in ("withOrderbook", "withoutOrderbook"):
        for item in positions.get(section, []):
            if not isinstance(item, dict) or not matches_account(item, account_id):
                continue
            order_book_id = position_order_book_id(item)
            if not order_book_id or order_book_id in seen:
                continue
            seen.add(order_book_id)
            options.append((position_holding_label(item), order_book_id))
    return options


def holding_search_options(positions: dict[str, Any], account_id: str | None, query: str) -> list[tuple[str, str]]:
    normalized_query = query.casefold().strip()
    if not normalized_query:
        return []
    return [
        (label, order_book_id)
        for label, order_book_id in stoploss_holding_options(positions, account_id)
        if normalized_query in label.casefold()
    ]


def stoploss_volume_by_order_book(positions: dict[str, Any], account_id: str | None) -> dict[str, str]:
    volumes: dict[str, str] = {}
    for section in ("withOrderbook", "withoutOrderbook"):
        for item in positions.get(section, []):
            if not isinstance(item, dict) or not matches_account(item, account_id):
                continue
            order_book_id = position_order_book_id(item)
            if order_book_id:
                volumes[order_book_id] = quantity_text(nested_value(item, "volume"))
    return volumes


def changed_position_row(
    current: tuple[str, ...],
    previous: tuple[str, ...] | None,
) -> tuple[Any, ...]:
    if previous is None:
        return current

    cells: list[Any] = []
    for index, value in enumerate(current):
        if index in POSITION_CHANGE_COLUMNS and value and index < len(previous) and value != previous[index]:
            cells.append(Text(value, style=change_style(value)))
        else:
            cells.append(value)
    return tuple(cells)


def change_style(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("+"):
        return POSITIVE_CELL_STYLE
    if stripped.startswith("-"):
        return NEGATIVE_CELL_STYLE
    return CHANGED_CELL_STYLE


def side_badge(value: Any) -> Text:
    label = str(value or "").upper()
    normalized = label.replace("_", "-").lower()
    if normalized == "buy":
        return Text(" BUY ", style=BUY_SIDE_STYLE)
    if normalized == "sell":
        return Text(" SELL ", style=SELL_SIDE_STYLE)
    return Text(label or "-", style="dim")


def cancel_badge() -> Text:
    return Text(" × ", style=SELL_SIDE_STYLE)


def trade_action_badge(side: str) -> Text:
    normalized = side.lower()
    if normalized == "buy":
        return Text(" B ", style=BUY_SIDE_STYLE)
    if normalized == "sell":
        return Text(" S ", style=SELL_SIDE_STYLE)
    return Text(str(side or "-"), style="dim")


def trade_action_from_cell(value: Any) -> str:
    text = plain_cell_value(value).strip().lower()
    if text in {"buy", "b"}:
        return "buy"
    if text in {"sell", "s"}:
        return "sell"
    return ""


def cash_row(item: dict[str, Any]) -> tuple[str, ...]:
    return (
        str(nested_value(item, "account", "name")),
        str(nested_value(item, "account", "id")),
        "Cash",
        "",
        "",
        "",
        amount(item, "totalBalance"),
        "",
        "",
        "",
    )


def open_order_row(item: dict[str, Any]) -> tuple[Any, ...]:
    price = open_order_price_value(item)
    price_type = item.get("priceType", "") or item.get("price_type", "")
    return (
        "Order",
        str(item.get("status", "")),
        open_order_stock_name(item),
        side_badge(open_order_side_value(item)),
        str(open_order_volume_value(item)),
        formatted_typed_value(price, price_type) if price_type else str(price),
        open_order_valid_until(item),
    )


def open_order_activity_row(item: dict[str, Any]) -> tuple[Any, ...]:
    row = open_order_row(item)
    return (*row, cancel_badge())


def stop_loss_row(item: dict[str, Any]) -> tuple[str, ...]:
    account = item.get("account") or {}
    orderbook = item.get("orderbook") or {}
    trigger = item.get("trigger") or {}
    order = item.get("order") or {}

    return (
        str(item.get("status", "")),
        str(account.get("name", "")),
        str(orderbook.get("name", "")),
        f"{trigger.get('type', '')} {formatted_typed_value(trigger.get('value', ''), trigger.get('valueType', ''))}",
        f"{order.get('type', '')} {order.get('volume', '')} @ {formatted_typed_value(order.get('price', ''), order.get('priceType', ''))}",
        str(trigger.get("validUntil", "")),
    )


def stop_loss_mcp_row(item: dict[str, Any]) -> tuple[str, ...]:
    account = item.get("account") or {}
    orderbook = item.get("orderbook") or {}
    trigger = item.get("trigger") or {}
    order = item.get("order") or {}
    return (
        str(item.get("id", "")),
        str(item.get("status", "")),
        str(account.get("name", "")),
        str(orderbook.get("name", "")),
        str(orderbook.get("id", "")),
        f"{trigger.get('type', '')} {formatted_typed_value(trigger.get('value', ''), trigger.get('valueType', ''))}",
        f"{order.get('type', '')} {order.get('volume', '')} @ {formatted_typed_value(order.get('price', ''), order.get('priceType', ''))}",
        str(trigger.get("validUntil", "")),
    )


def stop_loss_activity_row(item: dict[str, Any]) -> tuple[Any, ...]:
    orderbook = item.get("orderbook") or {}
    trigger = item.get("trigger") or {}
    order = item.get("order") or {}

    return (
        "Stop-loss",
        str(item.get("status", "")),
        str(orderbook.get("name", "")),
        f"{trigger.get('type', '')} {formatted_typed_value(trigger.get('value', ''), trigger.get('valueType', ''))}",
        side_badge(order.get("type", "")),
        str(order.get("volume", "")),
        formatted_typed_value(order.get("price", ""), order.get("priceType", "")),
        str(trigger.get("validUntil", "")),
        cancel_badge(),
    )


def active_stop_loss_row(item: dict[str, Any]) -> tuple[Any, ...]:
    orderbook = item.get("orderbook") or {}
    trigger = item.get("trigger") or {}
    order = item.get("order") or {}
    return (
        "Live",
        "Stop-loss",
        str(orderbook.get("name", "")),
        side_badge(order.get("type", "")),
        str(order.get("volume", "")),
        f"{trigger.get('type', '')} {formatted_typed_value(trigger.get('value', ''), trigger.get('valueType', ''))}",
        str(trigger.get("validUntil", "")),
        str(item.get("status", "")),
        cancel_badge(),
    )


def active_open_order_row(item: dict[str, Any]) -> tuple[Any, ...]:
    price = open_order_price_value(item)
    price_type = item.get("priceType", "") or item.get("price_type", "")
    return (
        "Live",
        "Order",
        open_order_stock_name(item),
        side_badge(open_order_side_value(item)),
        str(open_order_volume_value(item)),
        formatted_typed_value(price, price_type) if price_type else str(price),
        open_order_valid_until(item),
        str(item.get("status", "")),
        cancel_badge(),
    )


def active_paper_order_row(item: dict[str, Any]) -> tuple[Any, ...]:
    request = item.get("request") or {}
    if item.get("kind") == "Order":
        return (
            "Paper",
            "Order",
            str(item.get("instrument", "") or request.get("order_book_id", "")),
            side_badge(request.get("order_type", "")),
            str(request.get("volume", "")),
            f"{request.get('price', '')} SEK {request.get('condition', '')}".strip(),
            str(request.get("valid_until", "") or item.get("created_at", "")),
            str(item.get("status", "")),
            cancel_badge(),
        )
    trigger = request.get("stop_loss_trigger") or {}
    order = request.get("stop_loss_order_event") or {}
    return (
        "Paper",
        str(item.get("kind", "Stop-loss")),
        str(item.get("instrument", "") or request.get("order_book_id", "")),
        side_badge(order.get("type", "")),
        str(order.get("volume", "")),
        f"{trigger.get('type', '')} {formatted_typed_value(trigger.get('value', ''), trigger.get('value_type', ''))}",
        str(trigger.get("valid_until", "") or item.get("created_at", "")),
        str(item.get("status", "")),
        cancel_badge(),
    )


def stop_loss_request_log_lines(preview: dict[str, Any]) -> list[str]:
    return [line.replace("[", "\\[").replace("]", "\\]") for line in format_stop_loss_request(preview)]


def order_request_log_lines(preview: dict[str, Any]) -> list[str]:
    return [line.replace("[", "\\[").replace("]", "\\]") for line in format_order_request(preview)]


def build_stop_loss_preview(args: dict[str, Any]) -> tuple[StopLossTrigger, StopLossOrderEvent, dict[str, Any]]:
    valid_until = args.get("valid_until")
    if valid_until in (None, ""):
        valid_until = max_valid_until_date()
    elif isinstance(valid_until, str):
        try:
            valid_until = date.fromisoformat(valid_until)
        except ValueError as exc:
            raise ValueError("valid_until must be an ISO date string.") from exc
    if not isinstance(valid_until, date):
        raise ValueError("valid_until must be an ISO date string.")
    valid_until = validate_valid_until(valid_until, "valid_until")

    trigger = StopLossTrigger(
        type=enum_value(StopLossTriggerType, str(args.get("trigger_type", "follow-upwards"))),
        value=float(args["trigger_value"]),
        valid_until=valid_until,
        value_type=enum_value(StopLossPriceType, parse_price_type(str(args.get("trigger_value_type", "SEK")))),
        trigger_on_market_maker_quote=bool(args.get("trigger_on_market_maker_quote", False)),
    )
    valid_days = normalize_stoploss_order_valid_days(
        args.get("order_valid_days", STOPLOSS_ORDER_VALID_DAYS_DEFAULT),
        "order_valid_days",
    )
    order_event = StopLossOrderEvent(
        type=enum_value(OrderType, str(args.get("order_type", "sell"))),
        price=float(args["order_price"]),
        volume=float(args["volume"]),
        valid_days=valid_days,
        price_type=enum_value(StopLossPriceType, parse_price_type(str(args.get("order_price_type", "SEK")))),
        short_selling_allowed=bool(args.get("short_selling_allowed", False)),
    )
    preview = {
        "parent_stop_loss_id": str(args.get("parent_stop_loss_id", "0")),
        "account_id": str(args["account_id"]),
        "order_book_id": str(args["order_book_id"]),
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


def build_order_preview(args: dict[str, Any]) -> tuple[OrderType, Condition, dict[str, Any]]:
    valid_until = args.get("valid_until")
    if isinstance(valid_until, str):
        valid_until = date.fromisoformat(valid_until)
    if not isinstance(valid_until, date):
        raise ValueError("valid_until must be an ISO date string.")
    valid_until = validate_valid_until(valid_until, "valid_until")

    order_type = enum_value(OrderType, str(args.get("order_type", "buy")))
    condition = enum_value(Condition, str(args.get("condition", "normal")))
    preview = {
        "account_id": str(args["account_id"]),
        "order_book_id": str(args["order_book_id"]),
        "order_type": order_type.value,
        "price": float(args["price"]),
        "valid_until": valid_until.isoformat(),
        "volume": int(args["volume"]),
        "condition": condition.value,
    }
    return order_type, condition, preview


def render_accounts_overview(overview: dict[str, Any]) -> None:
    accounts = account_rows_from_overview(overview)
    if not accounts:
        render_message("Accounts", ["No accounts found."])
        return

    render_table(
        "Accounts",
        ["Account ID", "Name", "Type", "Total Value", "Buying Power", "Status"],
        [account_row(account) for account in accounts],
    )


def render_portfolio_positions(positions: dict[str, Any]) -> None:
    position_rows: list[tuple[Any, ...]] = []
    for section in ("withOrderbook", "withoutOrderbook"):
        for item in positions.get(section, []):
            if isinstance(item, dict):
                position_rows.append(position_row(item))

    cash_rows = [
        cash_row(item)
        for item in positions.get("cashPositions", [])
        if isinstance(item, dict)
    ]

    if position_rows:
        render_table(
            "Portfolio Positions",
            [
                "Account",
                "Account ID",
                "Stock",
                "Order Book ID",
                "ISIN",
                "Volume",
                "Value",
                "Avg Price",
                "Acquired",
                "Day %",
            ],
            position_rows,
        )
    else:
        render_message("Portfolio Positions", ["No stock positions found."])

    if cash_rows:
        render_table(
            "Cash Positions",
            [
                "Account",
                "Account ID",
                "Type",
                "Order Book ID",
                "ISIN",
                "Volume",
                "Balance",
                "Avg Price",
                "Acquired",
                "Day %",
            ],
            cash_rows,
        )


def render_portfolio_summary(positions: dict[str, Any]) -> None:
    render_message(
        "Portfolio Summary",
        [
            f"Listed positions: {len(positions.get('withOrderbook', []))}",
            f"Unlisted positions: {len(positions.get('withoutOrderbook', []))}",
            f"Cash positions: {len(positions.get('cashPositions', []))}",
        ],
    )
    cash_rows = [
        cash_row(item)
        for item in positions.get("cashPositions", [])
        if isinstance(item, dict)
    ]
    if cash_rows:
        render_table(
            "Cash Positions",
            [
                "Account",
                "Account ID",
                "Type",
                "Order Book ID",
                "ISIN",
                "Volume",
                "Balance",
                "Avg Price",
                "Acquired",
                "Day %",
            ],
            cash_rows,
        )
