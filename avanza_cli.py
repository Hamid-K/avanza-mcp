#!/usr/bin/env python3
import argparse
import getpass
import sys
import textwrap
from datetime import date
from typing import Any

from avanza import Avanza
from avanza.constants import OrderType, StopLossPriceType, StopLossTriggerType
from avanza.entities import StopLossOrderEvent, StopLossTrigger
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Footer, Header, Input, RichLog, Select, Static, Switch


console = Console()
HELP_FORMATTER = argparse.RawDescriptionHelpFormatter

TRIGGER_TYPE_CHOICES = [
    "less-or-equal",
    "more-or-equal",
    "follow-upwards",
    "follow-downwards",
]
PRICE_TYPE_ALIASES = {
    "monetary": "monetary",
    "sek": "monetary",
    "currency": "monetary",
    "percentage": "percentage",
    "percent": "percentage",
    "%": "percentage",
}
PRICE_TYPE_SELECT_OPTIONS = [("SEK", "monetary"), ("%", "percentage")]
ORDER_TYPE_CHOICES = ["buy", "sell"]
LIVE_REFRESH_SECONDS = 5.0
CHANGED_CELL_STYLE = "#d7ba7d"
POSITIVE_CELL_STYLE = "#7fbf8f"
NEGATIVE_CELL_STYLE = "#d98f8f"
POSITION_CHANGE_COLUMNS = {2, 3, 4, 5, 6, 7, 8}
MIN_PANE_WEIGHT = 1
MAX_PANE_WEIGHT = 8
REALTIME_KEYS = {
    "isRealTime",
    "isRealtime",
    "realTime",
    "realtime",
    "realTimeQuotes",
    "realtimeQuotes",
}
DELAYED_KEYS = {
    "delayed",
    "isDelayed",
    "delayedQuotes",
    "isDelayedQuote",
}


def prompt_credentials(username: str | None) -> dict[str, str]:
    if not username:
        username = input("Avanza username: ").strip()

    password = getpass.getpass("Avanza password: ")
    totp_code = getpass.getpass("Avanza TOTP code: ").strip()

    if not username:
        raise ValueError("Username is required.")
    if not password:
        raise ValueError("Password is required.")
    if not totp_code:
        raise ValueError("TOTP code is required.")

    return {
        "username": username,
        "password": password,
        "totpToken": totp_code,
    }


def connect(args: argparse.Namespace) -> Avanza:
    return Avanza(prompt_credentials(args.username))


def clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


def pane_weights_after_drag(
    start_positions_weight: int,
    start_activity_weight: int,
    delta_rows: int,
) -> tuple[int, int]:
    positions_weight = clamp(start_positions_weight + delta_rows, MIN_PANE_WEIGHT, MAX_PANE_WEIGHT)
    activity_weight = clamp(start_activity_weight - delta_rows, MIN_PANE_WEIGHT, MAX_PANE_WEIGHT)
    return positions_weight, activity_weight


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


def render_table(title: str, columns: list[str], rows: list[tuple[Any, ...]]) -> None:
    table = Table(title=title, show_lines=False)
    for column in columns:
        table.add_column(column, overflow="fold")

    for row in rows:
        table.add_row(*(str(value) for value in row))

    console.print(table)


def render_message(title: str, lines: list[str]) -> None:
    console.print(Panel("\n".join(lines), title=title, expand=False))


def format_stop_loss_request(preview: dict[str, Any]) -> list[str]:
    trigger = preview["stop_loss_trigger"]
    order_event = preview["stop_loss_order_event"]
    return [
        f"Account: {preview['account_id']}",
        f"Order book: {preview['order_book_id']}",
        f"Trigger: {trigger['type']} {formatted_typed_value(trigger['value'], trigger['value_type'])}",
        f"Trigger valid until: {trigger['valid_until']}",
        f"Order: {order_event['type']} {order_event['volume']} @ {formatted_typed_value(order_event['price'], order_event['price_type'])}",
        f"Order valid days after trigger: {order_event['valid_days']}",
    ]


def render_stop_loss_request(title: str, preview: dict[str, Any]) -> None:
    render_message(title, format_stop_loss_request(preview))


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
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use YYYY-MM-DD format.") from exc


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


def nested_value(data: dict[str, Any], *path: str) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return ""
        current = current.get(key, "")
    return current


def amount(data: dict[str, Any], *path: str) -> str:
    value = nested_value(data, *path)
    if isinstance(value, dict):
        raw = value.get("value", "")
        unit = value.get("unit", "")
        return f"{raw} {unit}".strip()
    return str(value)


def account_display_name(account: dict[str, Any]) -> str:
    name = account.get("name", "")
    if isinstance(name, dict):
        return str(name.get("userDefinedName") or name.get("defaultName") or "")
    return str(name)


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
    return str(nested_value(item, "account", "id"))


def matches_account(item: dict[str, Any], account_id: str | None) -> bool:
    return not account_id or account_id_for_item(item) == account_id


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


def value_number(data: dict[str, Any], *path: str) -> float | None:
    value = nested_value(data, *path)
    if isinstance(value, dict):
        value = value.get("value")
    if isinstance(value, (int, float)):
        return float(value)
    return None


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
            acquired_value = value_number(item, "acquiredValue")
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


def account_stats_text(
    account: dict[str, Any],
    portfolio_data: dict[str, Any] | None = None,
    account_id: str | None = None,
) -> Text:
    text = Text()
    name = account_display_name(account)
    account_type = str(account.get("type", ""))
    account_status = str(account.get("status", ""))
    label = f"{name} ({account_type})" if account_type else name
    text.append(label or "Selected account", style="bold")

    total = amount(account, "totalValue") or "-"
    buying_power = amount(account, "buyingPower") or "-"
    text.append("  Total ", style="dim")
    text.append(total, style="bold")
    text.append("  Buying ", style="dim")
    text.append(buying_power)

    if portfolio_data is not None:
        profit_amount, profit_percent, value_unit = portfolio_profit_summary(portfolio_data, account_id)
        if profit_amount is not None:
            style = metric_style(profit_amount)
            text.append("  Profit ", style="dim")
            text.append(money_text(profit_amount, value_unit), style=style)
            if profit_percent is not None:
                text.append(" ")
                text.append(f"({percent_text(profit_percent)})", style=style)

    if account_status:
        text.append("  ")
        text.append(account_status, style="dim")
    return text


def position_state_row(item: dict[str, Any]) -> tuple[str, ...]:
    instrument = item.get("instrument") or {}
    orderbook = instrument.get("orderbook") or {}
    performance = item.get("lastTradingDayPerformance") or {}
    current_value = value_number(item, "value")
    acquired_value = value_number(item, "acquiredValue")
    profit_amount = None
    profit_percent = None
    if current_value is not None and acquired_value not in (None, 0):
        profit_amount = current_value - acquired_value
        profit_percent = (profit_amount / acquired_value) * 100

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
        money_text(profit_amount, str(value_unit)),
        realtime_status(item),
    )


def position_order_book_id(item: dict[str, Any]) -> str:
    return str(nested_value(item, "instrument", "orderbook", "id"))


def position_holding_label(item: dict[str, Any]) -> str:
    instrument_name = str(nested_value(item, "instrument", "name"))
    order_book_id = position_order_book_id(item)
    owned_volume = amount(item, "volume")
    return f"{instrument_name} - owned {owned_volume} ({order_book_id})"


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


def stoploss_volume_by_order_book(positions: dict[str, Any], account_id: str | None) -> dict[str, str]:
    volumes: dict[str, str] = {}
    for section in ("withOrderbook", "withoutOrderbook"):
        for item in positions.get(section, []):
            if not isinstance(item, dict) or not matches_account(item, account_id):
                continue
            order_book_id = position_order_book_id(item)
            if order_book_id:
                volumes[order_book_id] = str(value_number(item, "volume") or "")
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


def open_order_row(item: dict[str, Any]) -> tuple[str, ...]:
    orderbook = item.get("orderbook") or item.get("instrument") or {}
    price = item.get("price", "")
    price_type = item.get("priceType", "") or item.get("price_type", "")
    return (
        "Order",
        str(item.get("id", "") or item.get("orderId", "")),
        str(item.get("status", "")),
        str(orderbook.get("name", "")),
        str(orderbook.get("id", "") or item.get("orderbookId", "")),
        str(item.get("type", "") or item.get("orderType", "")),
        str(item.get("volume", "")),
        formatted_typed_value(price, price_type) if price_type else str(price),
        str(item.get("validUntil", "")),
    )


def stop_loss_row(item: dict[str, Any]) -> tuple[str, ...]:
    account = item.get("account") or {}
    orderbook = item.get("orderbook") or {}
    trigger = item.get("trigger") or {}
    order = item.get("order") or {}

    return (
        str(item.get("id", "")),
        str(item.get("status", "")),
        str(account.get("name", "")),
        str(account.get("id", "")),
        str(orderbook.get("name", "")),
        str(orderbook.get("id", "")),
        f"{trigger.get('type', '')} {formatted_typed_value(trigger.get('value', ''), trigger.get('valueType', ''))}",
        f"{order.get('type', '')} {order.get('volume', '')} @ {formatted_typed_value(order.get('price', ''), order.get('priceType', ''))}",
        str(trigger.get("validUntil", "")),
    )


def stop_loss_activity_row(item: dict[str, Any]) -> tuple[str, ...]:
    orderbook = item.get("orderbook") or {}
    trigger = item.get("trigger") or {}
    order = item.get("order") or {}

    return (
        "Stop-loss",
        str(item.get("id", "")),
        str(item.get("status", "")),
        str(orderbook.get("name", "")),
        str(orderbook.get("id", "")),
        f"{trigger.get('type', '')} {formatted_typed_value(trigger.get('value', ''), trigger.get('valueType', ''))}",
        str(order.get("volume", "")),
        formatted_typed_value(order.get("price", ""), order.get("priceType", "")),
        str(trigger.get("validUntil", "")),
    )


def stop_loss_request_log_lines(preview: dict[str, Any]) -> list[str]:
    return [line.replace("[", "\\[").replace("]", "\\]") for line in format_stop_loss_request(preview)]


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
                "Instrument",
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
        render_message("Portfolio Positions", ["No instrument positions found."])

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


def flattened_search_hits(results: Any) -> list[dict[str, Any]]:
    if not isinstance(results, dict):
        return []

    rows: list[dict[str, Any]] = []
    for hit_group in results.get("hits", []):
        if not isinstance(hit_group, dict):
            continue
        group_type = hit_group.get("instrumentType", "")
        top_hits = hit_group.get("topHits") or []
        for hit in top_hits:
            if isinstance(hit, dict):
                row = dict(hit)
                row.setdefault("instrumentType", group_type)
                rows.append(row)
    return rows


def render_search_results(results: Any) -> None:
    hits = flattened_search_hits(results)
    if not hits:
        render_message("Search Results", ["No matching stocks found."])
        return

    rows = []
    for hit in hits:
        rows.append(
            (
                hit.get("name", ""),
                hit.get("tickerSymbol", ""),
                hit.get("instrumentType", ""),
                hit.get("id", "") or hit.get("orderbookId", ""),
                hit.get("isin", ""),
                hit.get("currency", ""),
            )
        )

    render_table(
        "Search Results",
        ["Name", "Ticker", "Type", "Order Book ID", "ISIN", "Currency"],
        rows,
    )


def render_stoplosses(stoplosses: Any) -> None:
    if not isinstance(stoplosses, list):
        render_message("Stop-Loss Orders", ["Unexpected response shape from Avanza."])
        return

    rows = [stop_loss_row(item) for item in stoplosses if isinstance(item, dict)]
    if not rows:
        render_message("Stop-Loss Orders", ["No open stop-loss orders found."])
        return

    render_table(
        "Stop-Loss Orders",
        [
            "ID",
            "Status",
            "Account",
            "Account ID",
            "Instrument",
            "Order Book ID",
            "Trigger",
            "Order",
            "Valid Until",
        ],
        rows,
    )


class AvanzaTradingTui(App):
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

    #workspace {
        display: none;
        height: 1fr;
    }

    #topbar {
        height: 3;
        padding: 0 1;
        background: $panel;
        border-bottom: solid $primary;
        align: left middle;
    }

    #app-title {
        width: 10;
        text-style: bold;
    }

    #account-select {
        width: 56;
        margin-right: 1;
    }

    #selected-account {
        width: 1fr;
    }

    #live-status {
        width: 10;
        color: $success;
    }

    #main {
        height: 1fr;
        padding: 1;
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

    #activity-panel {
        height: 1fr;
    }

    #portfolio-table {
        height: 1fr;
    }

    #stoploss-table {
        height: 1fr;
    }

    #stoploss-modal {
        display: none;
        dock: right;
        width: 58;
        height: 100%;
        margin: 1;
        padding: 1 2;
        border: tall $warning;
        background: $panel;
    }

    #stoploss-modal Select,
    #stoploss-modal Input {
        margin-bottom: 1;
    }

    #log {
        height: 6;
        border: solid $primary;
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

    #place-live {
        min-width: 11;
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

    def __init__(self) -> None:
        super().__init__()
        self.avanza: Avanza | None = None
        self.accounts: list[dict[str, Any]] = []
        self.selected_account_id: str | None = None
        self.live_refresh_timer = None
        self.last_resize: tuple[int, int] | None = None
        self.position_row_cache: dict[str, tuple[str, ...]] = {}
        self.holding_volumes_by_order_book: dict[str, str] = {}
        self.positions_pane_weight = 2
        self.activity_pane_weight = 1
        self.is_resizing_panes = False
        self.resize_start_y = 0
        self.resize_start_positions_weight = self.positions_pane_weight
        self.resize_start_activity_weight = self.activity_pane_weight

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="login-screen"):
            with Vertical(id="login-card"):
                yield Static("Avanza Trading Console", id="login-title")
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

        with Vertical(id="workspace"):
            with Horizontal(id="topbar"):
                yield Static("Avanza", id="app-title")
                yield Select([], prompt="Select account", allow_blank=True, id="account-select")
                yield Static("Selected account: none", id="selected-account")
                yield Static(f"Live {LIVE_REFRESH_SECONDS:g}s", id="live-status")
                yield Button("Refresh", id="refresh-all", variant="primary")
                yield Button("Add Stop-Loss", id="open-stoploss-modal", variant="warning")
            with Vertical(id="main"):
                with Vertical(id="positions-panel"):
                    yield Static("Selected Account Positions", classes="panel")
                    yield DataTable(id="portfolio-table")
                yield Static("drag to resize", id="pane-resizer")
                with Vertical(id="activity-panel"):
                    yield Static("Stop-Losses and Open Orders", classes="panel")
                    yield DataTable(id="stoploss-table")
                    with Horizontal():
                        yield Button("Refresh Account", id="refresh-account", variant="primary")
                        yield Button("Clear Log", id="clear-log")
                    yield RichLog(id="log", highlight=True, markup=True)
            with Vertical(id="stoploss-modal"):
                yield Static("New Stop-Loss", classes="panel")
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
                yield Input(placeholder=f"Valid until ({date.today().isoformat()})", id="valid-until")
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
                yield Input(value="1", placeholder="Order valid days", id="order-valid-days", type="integer")
                yield Switch(value=False, id="trigger-on-market-maker-quote")
                yield Static("Trigger on market-maker quote")
                yield Switch(value=False, id="short-selling-allowed")
                yield Static("Allow short selling")
                yield Input(placeholder='Type "PLACE" to enable live placement', id="place-confirm")
                with Horizontal():
                    yield Button("Dry Run", id="dry-run", variant="default")
                    yield Button("Place Live", id="place-live", variant="error")
                    yield Button("Close", id="close-stoploss-modal")
        yield Footer()

    def on_mount(self) -> None:
        stoploss_table = self.query_one("#stoploss-table", DataTable)
        stoploss_table.add_columns(
            "Kind",
            "ID",
            "Status",
            "Instrument",
            "Order Book ID",
            "Trigger/Side",
            "Volume",
            "Price",
            "Valid Until",
        )
        stoploss_table.cursor_type = "row"
        stoploss_table.zebra_stripes = True

        portfolio_table = self.query_one("#portfolio-table", DataTable)
        portfolio_table.add_columns(
            "Instrument",
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
        portfolio_table.cursor_type = "row"
        portfolio_table.zebra_stripes = True
        self.write_log("Ready. Log in, then refresh portfolio or stop-losses.")

    def on_resize(self, event: events.Resize) -> None:
        self.last_resize = (event.size.width, event.size.height)
        self.refresh(layout=True)
        for selector in ("#workspace", "#main", "#portfolio-table", "#stoploss-table"):
            try:
                self.query_one(selector).refresh(layout=True)
            except Exception:
                pass
        if self.avanza and self.selected_account_id:
            self.call_after_refresh(self.refresh_selected_account_live)

    def apply_pane_weights(self, positions_weight: int, activity_weight: int) -> None:
        self.positions_pane_weight = positions_weight
        self.activity_pane_weight = activity_weight
        self.query_one("#positions-panel").styles.height = f"{positions_weight}fr"
        self.query_one("#activity-panel").styles.height = f"{activity_weight}fr"
        self.query_one("#main").refresh(layout=True)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if getattr(event.widget, "id", None) != "pane-resizer":
            return
        self.is_resizing_panes = True
        self.resize_start_y = int(event.screen_y if event.screen_y is not None else event.y)
        self.resize_start_positions_weight = self.positions_pane_weight
        self.resize_start_activity_weight = self.activity_pane_weight
        self.capture_mouse(event.widget)
        event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if not self.is_resizing_panes:
            return
        current_y = int(event.screen_y if event.screen_y is not None else event.y)
        delta_rows = current_y - self.resize_start_y
        weights = pane_weights_after_drag(
            self.resize_start_positions_weight,
            self.resize_start_activity_weight,
            delta_rows,
        )
        self.apply_pane_weights(*weights)
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if not self.is_resizing_panes:
            return
        self.is_resizing_panes = False
        self.capture_mouse(None)
        event.stop()

    def input_value(self, widget_id: str) -> str:
        widget = self.query_one(f"#{widget_id}")
        if isinstance(widget, Input):
            return widget.value.strip()
        if isinstance(widget, Select):
            if widget.value == Select.BLANK:
                return ""
            return str(widget.value)
        raise TypeError(f"Unsupported input widget: {widget_id}")

    def switch_value(self, widget_id: str) -> bool:
        return bool(self.query_one(f"#{widget_id}", Switch).value)

    def clear_secret_inputs(self) -> None:
        self.query_one("#password", Input).value = ""
        self.query_one("#totp", Input).value = ""

    def write_log(self, message: str) -> None:
        self.query_one("#log", RichLog).write(message)

    def require_connection(self) -> Avanza:
        if self.avanza is None:
            raise RuntimeError("Log in first.")
        return self.avanza

    def require_selected_account_id(self) -> str:
        if not self.selected_account_id:
            raise RuntimeError("Select an account first.")
        return self.selected_account_id

    def update_selected_account_summary(self, portfolio_data: dict[str, Any] | None = None) -> None:
        account = self.account_by_id(self.selected_account_id) if self.selected_account_id else None
        if account:
            self.query_one("#selected-account", Static).update(
                account_stats_text(account, portfolio_data, self.selected_account_id)
            )
        else:
            self.query_one("#selected-account", Static).update("Selected account: none")

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

    def build_stop_loss_request(self) -> tuple[StopLossTrigger, StopLossOrderEvent, dict[str, Any]]:
        selected_account_id = self.require_selected_account_id()
        order_book_id = self.input_value("instrument-select")
        if not order_book_id:
            raise ValueError("Select a portfolio holding first.")
        valid_until = date.fromisoformat(self.input_value("valid-until"))
        trigger = StopLossTrigger(
            type=enum_value(StopLossTriggerType, self.input_value("trigger-type")),
            value=float(self.input_value("trigger-value")),
            valid_until=valid_until,
            value_type=enum_value(StopLossPriceType, self.input_value("trigger-value-type")),
            trigger_on_market_maker_quote=self.switch_value("trigger-on-market-maker-quote"),
        )
        order_event = StopLossOrderEvent(
            type=enum_value(OrderType, self.input_value("order-type")),
            price=float(self.input_value("order-price")),
            volume=float(self.input_value("volume")),
            valid_days=int(self.input_value("order-valid-days")),
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
                "price_type": order_event.price_type.value,
                "short_selling_allowed": order_event.short_selling_allowed,
            },
        }
        return trigger, order_event, preview

    def refresh_stoplosses(self) -> None:
        avanza = self.require_connection()
        table = self.query_one("#stoploss-table", DataTable)
        table.clear()

        visible_count = 0
        data = avanza.get_all_stop_losses()
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    if not matches_account(item, self.selected_account_id):
                        continue
                    table.add_row(*stop_loss_activity_row(item), key=f"stoploss-{item.get('id', visible_count)}")
                    visible_count += 1
        else:
            self.write_log(f"[yellow]Unexpected stop-loss response type:[/yellow] {type(data).__name__}")

        order_count = 0
        try:
            orders = avanza.get_orders()
        except Exception as exc:
            self.write_log(f"[yellow]Could not load open orders:[/yellow] {exc}")
            orders = []

        if isinstance(orders, dict):
            order_items = orders.get("orders") or orders.get("items") or []
        elif isinstance(orders, list):
            order_items = orders
        else:
            order_items = []

        for item in order_items:
            if isinstance(item, dict):
                if not matches_account(item, self.selected_account_id):
                    continue
                table.add_row(*open_order_row(item), key=f"order-{item.get('id', order_count)}")
                order_count += 1

        suffix = f" for account {self.selected_account_id}" if self.selected_account_id else ""
        self.write_log(f"Loaded {visible_count} stop-loss order(s) and {order_count} open order(s){suffix}.")

    def refresh_accounts(self) -> None:
        avanza = self.require_connection()
        overview = avanza.get_overview()
        if not isinstance(overview, dict):
            self.write_log(f"[yellow]Unexpected account overview response type:[/yellow] {type(overview).__name__}")
            return

        self.accounts = account_rows_from_overview(overview)
        account_options = [
            (
                f"{account_display_name(account)} ({account.get('type', '')}) - {amount(account, 'totalValue')}",
                str(account.get("id", "")),
            )
            for account in self.accounts
        ]
        account_select = self.query_one("#account-select", Select)
        account_select.set_options(account_options)

        self.write_log(f"Loaded {len(self.accounts)} account(s).")
        if self.accounts and not self.selected_account_id:
            selected_account = default_account(self.accounts)
            if selected_account is not None:
                self.set_selected_account(selected_account)
            account_select.value = self.selected_account_id

    def refresh_portfolio(self) -> None:
        avanza = self.require_connection()
        table = self.query_one("#portfolio-table", DataTable)
        table.clear()

        data = avanza.get_accounts_positions()
        if not isinstance(data, dict):
            self.write_log(f"[yellow]Unexpected portfolio response type:[/yellow] {type(data).__name__}")
            return
        self.update_selected_account_summary(data)

        holding_options = stoploss_holding_options(data, self.selected_account_id)
        holding_select = self.query_one("#instrument-select", Select)
        previous_holding = self.input_value("instrument-select")
        holding_select.set_options(holding_options)
        if previous_holding and previous_holding in {value for _, value in holding_options}:
            holding_select.value = previous_holding
        elif holding_options:
            holding_select.value = holding_options[0][1]
        self.holding_volumes_by_order_book = stoploss_volume_by_order_book(data, self.selected_account_id)
        selected_holding = self.input_value("instrument-select")
        volume_input = self.query_one("#volume", Input)
        if selected_holding and not volume_input.value.strip():
            volume_input.value = self.holding_volumes_by_order_book.get(selected_holding, "")

        count = 0
        next_cache: dict[str, tuple[str, ...]] = {}
        for section in ("withOrderbook", "withoutOrderbook"):
            for item in data.get(section, []):
                if isinstance(item, dict):
                    if not matches_account(item, self.selected_account_id):
                        continue
                    row_key = str(item.get("id", f"{section}-{count}"))
                    current_row = position_state_row(item)
                    previous_row = self.position_row_cache.get(row_key)
                    table.add_row(*changed_position_row(current_row, previous_row), key=row_key)
                    next_cache[row_key] = current_row
                    count += 1

        self.position_row_cache = next_cache
        suffix = f" for account {self.selected_account_id}" if self.selected_account_id else ""
        self.write_log(f"Loaded {count} portfolio row(s){suffix}.")

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

    def refresh_selected_account_live(self) -> None:
        if not self.avanza or not self.selected_account_id:
            return
        try:
            self.refresh_portfolio()
            self.refresh_stoplosses()
        except Exception as exc:
            self.write_log(f"[red]Live refresh failed:[/red] {exc}")

    def start_live_refresh(self) -> None:
        if self.live_refresh_timer is None:
            self.live_refresh_timer = self.set_interval(
                LIVE_REFRESH_SECONDS,
                self.refresh_selected_account_live,
                pause=False,
            )
            self.write_log(f"Live refresh enabled every {LIVE_REFRESH_SECONDS:g}s.")

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
        self.refresh_portfolio()
        self.refresh_stoplosses()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "account-select" and event.value:
            try:
                self.select_account(str(event.value))
            except Exception as exc:
                self.write_log(f"[red]Account switch failed:[/red] {exc}")
        elif event.select.id == "instrument-select" and event.value and event.value != Select.BLANK:
            volume_input = self.query_one("#volume", Input)
            if not volume_input.value.strip():
                volume_input.value = self.holding_volumes_by_order_book.get(str(event.value), "")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        try:
            if button_id == "login":
                self.handle_login()
            elif button_id == "refresh-all":
                self.refresh_accounts()
                self.refresh_portfolio()
                self.refresh_stoplosses()
            elif button_id in {"refresh", "refresh-account"}:
                self.refresh_portfolio()
                self.refresh_stoplosses()
            elif button_id == "open-stoploss-modal":
                self.query_one("#stoploss-modal").display = True
            elif button_id == "close-stoploss-modal":
                self.query_one("#stoploss-modal").display = False
            elif button_id == "clear-log":
                self.query_one("#log", RichLog).clear()
            elif button_id == "dry-run":
                self.handle_dry_run()
            elif button_id == "place-live":
                self.handle_place_live()
        except Exception as exc:
            self.write_log(f"[red]Error:[/red] {exc}")

    def handle_login(self) -> None:
        username = self.input_value("username")
        password = self.input_value("password")
        totp = self.input_value("totp")
        if not username or not password or not totp:
            raise ValueError("Username, password, and TOTP are required.")

        self.write_log("Logging in...")
        self.avanza = Avanza({"username": username, "password": password, "totpToken": totp})
        self.clear_secret_inputs()
        self.query_one("#login-screen").display = False
        self.query_one("#workspace").display = True
        self.write_log("[green]Logged in. Secret fields cleared.[/green]")
        self.refresh_accounts()
        self.refresh_portfolio()
        self.refresh_stoplosses()
        self.start_live_refresh()

    def handle_dry_run(self) -> None:
        _, _, preview = self.build_stop_loss_request()
        self.write_log("[yellow]Dry-run stop-loss request:[/yellow]")
        for line in stop_loss_request_log_lines(preview):
            self.write_log(line)

    def handle_place_live(self) -> None:
        if self.input_value("place-confirm") != "PLACE":
            raise ValueError('Type "PLACE" in the confirmation field before live placement.')

        avanza = self.require_connection()
        trigger, order_event, preview = self.build_stop_loss_request()
        self.write_log("[red]Placing live stop-loss request:[/red]")
        for line in stop_loss_request_log_lines(preview):
            self.write_log(line)

        result = avanza.place_stop_loss_order(
            parent_stop_loss_id="0",
            account_id=self.require_selected_account_id(),
            order_book_id=self.input_value("instrument-select"),
            stop_loss_trigger=trigger,
            stop_loss_order_event=order_event,
        )
        if isinstance(result, dict):
            status = result.get("status") or result.get("orderRequestStatus") or "response received"
            identifier = result.get("stoplossOrderId") or result.get("orderId") or ""
            suffix = f" ({identifier})" if identifier else ""
            self.write_log(f"[green]Avanza status:[/green] {status}{suffix}")
        else:
            self.write_log("[green]Avanza accepted the request.[/green]")
        self.query_one("#stoploss-modal").display = False
        self.refresh_stoplosses()


def cmd_tui(_args: argparse.Namespace) -> None:
    AvanzaTradingTui().run()


def cmd_accounts(args: argparse.Namespace) -> None:
    avanza = connect(args)
    render_accounts_overview(avanza.get_overview())


def cmd_portfolio_positions(args: argparse.Namespace) -> None:
    avanza = connect(args)
    render_portfolio_positions(avanza.get_accounts_positions())


def cmd_portfolio_summary(args: argparse.Namespace) -> None:
    avanza = connect(args)
    render_portfolio_summary(avanza.get_accounts_positions())


def cmd_search(args: argparse.Namespace) -> None:
    avanza = connect(args)
    render_search_results(avanza.search_for_stock(args.query, args.limit))


def cmd_stoploss_list(args: argparse.Namespace) -> None:
    avanza = connect(args)
    render_stoplosses(avanza.get_all_stop_losses())


def cmd_stoploss_delete(args: argparse.Namespace) -> None:
    if not args.confirm:
        render_message(
            "Dry Run",
            [
                "Add --confirm to delete this stop-loss order.",
                f"Account: {args.account_id}",
                f"Stop-loss ID: {args.stop_loss_id}",
            ],
        )
        return

    avanza = connect(args)
    result = avanza.delete_stop_loss_order(args.account_id, args.stop_loss_id)
    render_result("Delete Stop-Loss Result", {"deleted": True, "result": result})


def cmd_stoploss_set(args: argparse.Namespace) -> None:
    trigger_type = enum_value(StopLossTriggerType, args.trigger_type)
    trigger_value_type = enum_value(StopLossPriceType, args.trigger_value_type)
    order_type = enum_value(OrderType, args.order_type)
    order_price_type = enum_value(StopLossPriceType, args.order_price_type)

    trigger = StopLossTrigger(
        type=trigger_type,
        value=args.trigger_value,
        valid_until=args.valid_until,
        value_type=trigger_value_type,
        trigger_on_market_maker_quote=args.trigger_on_market_maker_quote,
    )
    order_event = StopLossOrderEvent(
        type=order_type,
        price=args.order_price,
        volume=args.volume,
        valid_days=args.order_valid_days,
        price_type=order_price_type,
        short_selling_allowed=args.short_selling_allowed,
    )

    request_preview = {
        "parent_stop_loss_id": args.parent_stop_loss_id,
        "account_id": args.account_id,
        "order_book_id": args.order_book_id,
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
            "price_type": order_event.price_type.value,
            "short_selling_allowed": order_event.short_selling_allowed,
        },
    }

    if not args.confirm:
        render_stop_loss_request(
            "Dry Run: add --confirm to place this stop-loss order.",
            request_preview,
        )
        return

    avanza = connect(args)
    result = avanza.place_stop_loss_order(
        parent_stop_loss_id=args.parent_stop_loss_id,
        account_id=args.account_id,
        order_book_id=args.order_book_id,
        stop_loss_trigger=trigger,
        stop_loss_order_event=order_event,
    )
    render_result("Place Stop-Loss Result", result)


def add_common_auth(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--username",
        metavar="USER",
        help="Avanza username. If omitted, you are prompted interactively.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="avanza_cli.py",
        formatter_class=HELP_FORMATTER,
        description="Human-readable Avanza account, portfolio, search, and stop-loss tools.",
        epilog=textwrap.dedent(
            """\
            Common examples:
              python avanza_cli.py tui
              python avanza_cli.py accounts
              python avanza_cli.py portfolio summary
              python avanza_cli.py portfolio positions
              python avanza_cli.py search-stock "VOLV B"
              python avanza_cli.py stoploss list

            Credentials:
              Password and current TOTP code are prompted interactively and masked.

            Safety:
              Mutating commands dry-run unless you pass --confirm.
            """
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    tui = subparsers.add_parser(
        "tui",
        formatter_class=HELP_FORMATTER,
        help="Launch the interactive Textual terminal UI.",
        description="Launch the interactive terminal UI for account switching, portfolio viewing, and stop-loss management.",
    )
    tui.set_defaults(func=cmd_tui)

    accounts = subparsers.add_parser(
        "accounts",
        formatter_class=HELP_FORMATTER,
        help="Show all accounts with balances and buying power.",
        description="Show all Avanza accounts in a readable table.",
        epilog="Example:\n  python avanza_cli.py accounts",
    )
    add_common_auth(accounts)
    accounts.set_defaults(func=cmd_accounts)

    portfolio = subparsers.add_parser(
        "portfolio",
        formatter_class=HELP_FORMATTER,
        help="View portfolio summaries and positions.",
        description="View portfolio data across accounts in readable terminal tables.",
        epilog=textwrap.dedent(
            """\
            Examples:
              python avanza_cli.py portfolio summary
              python avanza_cli.py portfolio positions
            """
        ),
    )
    portfolio_subparsers = portfolio.add_subparsers(dest="portfolio_command", required=True)

    portfolio_summary = portfolio_subparsers.add_parser(
        "summary",
        formatter_class=HELP_FORMATTER,
        help="Show position counts and cash balances.",
        description="Show portfolio position counts and cash positions.",
        epilog="Example:\n  python avanza_cli.py portfolio summary",
    )
    add_common_auth(portfolio_summary)
    portfolio_summary.set_defaults(func=cmd_portfolio_summary)

    portfolio_positions = portfolio_subparsers.add_parser(
        "positions",
        formatter_class=HELP_FORMATTER,
        help="Show instrument and cash positions.",
        description="Show all portfolio instrument positions and cash balances in tables.",
        epilog="Example:\n  python avanza_cli.py portfolio positions",
    )
    add_common_auth(portfolio_positions)
    portfolio_positions.set_defaults(func=cmd_portfolio_positions)

    search = subparsers.add_parser(
        "search-stock",
        formatter_class=HELP_FORMATTER,
        help="Search stocks by name, ticker, or ISIN.",
        description="Search Avanza stocks and show matching order book ids.",
        epilog='Example:\n  python avanza_cli.py search-stock "VOLV B" --limit 5',
    )
    add_common_auth(search)
    search.add_argument("query", help="Name, ticker, or ISIN to search for.")
    search.add_argument(
        "--limit",
        metavar="N",
        type=int,
        default=10,
        help="Maximum number of search results to request. Default: 10.",
    )
    search.set_defaults(func=cmd_search)

    stoploss = subparsers.add_parser(
        "stoploss",
        formatter_class=HELP_FORMATTER,
        help="List, create, and delete stop-loss orders.",
        description="Manage Avanza stop-loss orders. Placement and deletion dry-run unless --confirm is passed.",
        epilog=textwrap.dedent(
            """\
            Examples:
              python avanza_cli.py stoploss list
              python avanza_cli.py stoploss set --help
              python avanza_cli.py stoploss delete --help
            """
        ),
    )
    stoploss_subparsers = stoploss.add_subparsers(dest="stoploss_command", required=True)

    stoploss_list = stoploss_subparsers.add_parser(
        "list",
        formatter_class=HELP_FORMATTER,
        help="List open stop-loss orders.",
        description="List open stop-loss orders in a readable table.",
        epilog="Example:\n  python avanza_cli.py stoploss list",
    )
    add_common_auth(stoploss_list)
    stoploss_list.set_defaults(func=cmd_stoploss_list)

    stoploss_delete = stoploss_subparsers.add_parser(
        "delete",
        formatter_class=HELP_FORMATTER,
        help="Delete a stop-loss order.",
        description="Delete a stop-loss order. Without --confirm this only prints the intended deletion.",
        epilog=textwrap.dedent(
            """\
            Dry-run:
              python avanza_cli.py stoploss delete --account-id ACCOUNT_ID --stop-loss-id STOP_LOSS_ID

            Live deletion:
              python avanza_cli.py stoploss delete --account-id ACCOUNT_ID --stop-loss-id STOP_LOSS_ID --confirm
            """
        ),
    )
    add_common_auth(stoploss_delete)
    stoploss_delete.add_argument("--account-id", metavar="ID", required=True, help="Avanza account id that owns the stop-loss.")
    stoploss_delete.add_argument("--stop-loss-id", metavar="ID", required=True, help="Stop-loss id to delete.")
    stoploss_delete.add_argument("--confirm", action="store_true", help="Actually delete the stop-loss. Omit for dry-run.")
    stoploss_delete.set_defaults(func=cmd_stoploss_delete)

    stoploss_set = stoploss_subparsers.add_parser(
        "set",
        formatter_class=HELP_FORMATTER,
        help="Create a fixed or gliding stop-loss order.",
        description=textwrap.dedent(
            """\
            Create a stop-loss order.

            Without --confirm, this command prints a readable dry-run summary and does not log in.

            Trigger types:
              less-or-equal   fixed trigger at or below a price
              more-or-equal   fixed trigger at or above a price
              follow-upwards  gliding/trailing trigger for long positions
              follow-downwards gliding/trailing trigger for short/downward logic

            Price/value types:
              SEK             explicit currency value
              %               relative offset/value, interpreted by Avanza
            """
        ),
        epilog=textwrap.dedent(
            """\
            Gliding sell stop-loss dry-run:
              python avanza_cli.py stoploss set \\
                --account-id ACCOUNT_ID \\
                --order-book-id ORDER_BOOK_ID \\
                --trigger-type follow-upwards \\
                --trigger-value 5 \\
                --trigger-value-type % \\
                --valid-until 2026-05-28 \\
                --order-type sell \\
                --order-price 1 \\
                --order-price-type % \\
                --volume 10

            Add --confirm only after reviewing the dry-run summary.
            """
        ),
    )
    add_common_auth(stoploss_set)
    stoploss_set.add_argument("--account-id", metavar="ID", required=True, help="Avanza account id to place the stop-loss on.")
    stoploss_set.add_argument("--order-book-id", metavar="ID", required=True, help="Avanza order book id for the instrument.")
    stoploss_set.add_argument("--parent-stop-loss-id", metavar="ID", default="0", help="Parent stop-loss id. Default: 0.")
    stoploss_set.add_argument("--trigger-type", choices=TRIGGER_TYPE_CHOICES, required=True, help="Stop-loss trigger behavior.")
    stoploss_set.add_argument("--trigger-value", metavar="VALUE", required=True, type=float, help="Trigger value, interpreted with --trigger-value-type.")
    stoploss_set.add_argument(
        "--trigger-value-type",
        metavar="{SEK,%}",
        type=parse_price_type,
        default="monetary",
        help="How to interpret --trigger-value. Use SEK or %%. Default: SEK.",
    )
    stoploss_set.add_argument("--valid-until", metavar="YYYY-MM-DD", required=True, type=parse_date, help="Last date the trigger remains valid.")
    stoploss_set.add_argument("--trigger-on-market-maker-quote", action="store_true", help="Allow market-maker quote to trigger the stop-loss.")
    stoploss_set.add_argument("--order-type", choices=ORDER_TYPE_CHOICES, default="sell", help="Order side after trigger. Default: sell.")
    stoploss_set.add_argument("--order-price", metavar="VALUE", required=True, type=float, help="Order price or offset, interpreted with --order-price-type.")
    stoploss_set.add_argument(
        "--order-price-type",
        metavar="{SEK,%}",
        type=parse_price_type,
        default="monetary",
        help="How to interpret --order-price. Use SEK or %%. Default: SEK.",
    )
    stoploss_set.add_argument("--volume", metavar="QTY", required=True, type=float, help="Number of shares/contracts to include in the triggered order.")
    stoploss_set.add_argument("--order-valid-days", metavar="DAYS", default=1, type=int, help="Triggered order validity in days. Default: 1.")
    stoploss_set.add_argument("--short-selling-allowed", action="store_true", help="Allow short selling for the triggered order.")
    stoploss_set.add_argument("--confirm", action="store_true", help="Actually place the stop-loss. Omit for dry-run.")
    stoploss_set.set_defaults(func=cmd_stoploss_set)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        args.func(args)
        return 0
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
