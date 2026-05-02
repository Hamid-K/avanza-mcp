#!/usr/bin/env python3
import argparse
import cProfile
import getpass
import io
import json
import os
import pstats
import re
import secrets
import subprocess
import sys
import threading
import time
import textwrap
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from avanza import Avanza
from avanza.constants import Condition, InstrumentType, OrderType, StopLossPriceType, StopLossTriggerType, TransactionsDetailsType
from avanza.entities import StopLossOrderEvent, StopLossTrigger
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Footer, Header, Input, RichLog, Select, Static, Switch, TabbedContent, TabPane


console = Console()
HELP_FORMATTER = argparse.RawDescriptionHelpFormatter
MCP_SESSION_FILE = Path(__file__).with_name(".avanza_mcp_session.json")
PAPER_SESSION_FILE = Path(__file__).with_name(".avanza_paper_session.json")
LOG_DIR = Path(__file__).with_name("avanza-cli") / "logs"
MCP_PROTOCOL_VERSION = "2024-11-05"
VALID_UNTIL_MAX_DAYS = int(os.getenv("AVANZA_VALID_UNTIL_MAX_DAYS", "90"))
STOPLOSS_ORDER_VALID_DAYS_DEFAULT = int(os.getenv("AVANZA_STOPLOSS_ORDER_VALID_DAYS_DEFAULT", "8"))

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
ORDER_CONDITION_CHOICES = ["normal", "fill-or-kill", "fill-and-kill"]
TRANSACTION_TYPE_CHOICES = [item.value for item in TransactionsDetailsType]
LIVE_REFRESH_SECONDS = 5.0
REALTIME_STATUS_REFRESH_SECONDS = 300.0
QUOTE_CACHE_SECONDS = 8.0
MCP_HEALTH_CHECK_SECONDS = 5.0
LOGIN_PROGRESS_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
LOGIN_PROGRESS_ROTATE_TICKS = 10
DEBUG_PROFILE_TOP_DEFAULT = 25
CHANGED_CELL_STYLE = "#d7ba7d"
POSITIVE_CELL_STYLE = "#7fbf8f"
NEGATIVE_CELL_STYLE = "#d98f8f"
POSITIVE_PERCENT_STYLE = "#a9dcb8"
NEGATIVE_PERCENT_STYLE = "#ebb0b0"
BUY_SIDE_STYLE = "bold white on #1f6f43"
SELL_SIDE_STYLE = "bold white on #8f2438"
POSITION_CHANGE_COLUMNS = {2, 3, 4, 5, 6, 7, 8}
MIN_PANE_WEIGHT = 1
MAX_PANE_WEIGHT = 8
PANE_RESIZE_STEP = 0.10
MIN_ACTIVE_TRADES_WIDTH = 30
MAX_ACTIVE_TRADES_WIDTH = 110
MIN_TICKET_PANE_WIDTH = 52
MAX_TICKET_PANE_WIDTH = 110
PROFIT_METRIC_MODES = ("day", "week", "month", "year", "total")
PAPER_ORDER_ACTIVE_STATES = {"ACTIVE", "PENDING"}
WINDOW_PERFORMANCE_KEYS = {
    "week": ("lastTradingWeekPerformance", "weekPerformance", "oneWeekPerformance", "lastWeekPerformance"),
    "month": ("lastTradingMonthPerformance", "monthPerformance", "oneMonthPerformance", "lastMonthPerformance"),
    "year": ("lastTradingYearPerformance", "yearPerformance", "oneYearPerformance", "lastYearPerformance"),
}
OVERVIEW_PERFORMANCE_KEYS = {
    "day": ("TODAY", "DAY", "ONE_DAY"),
    "week": ("ONE_WEEK", "WEEK", "LAST_TRADING_WEEK"),
    "month": ("ONE_MONTH", "MONTH", "LAST_TRADING_MONTH"),
    "year": ("ONE_YEAR", "YEAR", "THIS_YEAR", "LAST_TRADING_YEAR"),
}
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
LOG_CATEGORY_FILES = {
    "app": "app.jsonl",
    "mcp": "mcp.jsonl",
    "trading": "trading.jsonl",
}


def max_valid_until_date(reference: date | None = None) -> date:
    base = reference or date.today()
    return base + timedelta(days=max(1, VALID_UNTIL_MAX_DAYS))


def validate_valid_until(value: date, label: str = "Valid until", reference: date | None = None) -> date:
    today = reference or date.today()
    max_date = max_valid_until_date(today)
    if value < today:
        raise ValueError(f"{label} cannot be before {today.isoformat()}.")
    if value > max_date:
        raise ValueError(
            f"{label} exceeds Avanza limit ({VALID_UNTIL_MAX_DAYS} days). Max: {max_date.isoformat()}."
        )
    return value


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


def onepassword_command(args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["op", *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("1Password CLI 'op' is not installed or is not on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("1Password CLI timed out waiting for authorization.") from exc
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or "").strip()
        raise RuntimeError(f"1Password CLI failed: {message or exc}") from exc
    return result.stdout.strip()


def onepassword_item_json(item: str, vault: str | None = None) -> dict[str, Any]:
    if not item.strip():
        raise ValueError("1Password item name or ID is required.")
    args = ["item", "get", item, "--format", "json"]
    if vault:
        args.extend(["--vault", vault])
    raw = onepassword_command(args)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("1Password CLI returned invalid JSON.") from exc
    if not isinstance(data, dict):
        raise RuntimeError("1Password CLI returned an unexpected item shape.")
    return data


def onepassword_field_value(item: dict[str, Any], labels: set[str], purposes: set[str]) -> str:
    for field in item.get("fields", []):
        if not isinstance(field, dict):
            continue
        label = str(field.get("label") or field.get("id") or "").strip().lower()
        purpose = str(field.get("purpose") or "").strip().lower()
        value = field.get("value")
        if value is None:
            continue
        if label in labels or purpose in purposes:
            return str(value)
    return ""


def onepassword_credentials(item: str, vault: str | None = None) -> dict[str, str]:
    item_data = onepassword_item_json(item, vault)
    username = onepassword_field_value(
        item_data,
        {"username", "user name", "email", "e-mail"},
        {"username"},
    )
    password = onepassword_field_value(
        item_data,
        {"password"},
        {"password"},
    )

    otp_args = ["item", "get", item, "--otp"]
    if vault:
        otp_args.extend(["--vault", vault])
    totp_code = onepassword_command(otp_args).strip()

    if not username:
        raise ValueError("Could not find a username field in the 1Password item.")
    if not password:
        raise ValueError("Could not find a password field in the 1Password item.")
    if not totp_code:
        raise ValueError("Could not get a TOTP code from the 1Password item.")

    return {
        "username": username,
        "password": password,
        "totpToken": totp_code,
    }


def connect(args: argparse.Namespace) -> Avanza:
    if getattr(args, "username", None) and getattr(args, "onepassword_item", None):
        raise ValueError("Use either --username or --onepassword-item, not both.")
    onepassword_item = getattr(args, "onepassword_item", None)
    onepassword_vault = getattr(args, "onepassword_vault", None)
    if onepassword_item:
        return Avanza(onepassword_credentials(onepassword_item, onepassword_vault))
    return Avanza(prompt_credentials(args.username))


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def timestamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def create_session_log_path(kind: str) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return LOG_DIR / f"session-{kind}-{stamp}.jsonl"


def strip_markup(value: str) -> str:
    return (
        value.replace("[green]", "").replace("[/green]", "")
        .replace("[yellow]", "").replace("[/yellow]", "")
        .replace("[red]", "").replace("[/red]", "")
        .replace("[cyan]", "").replace("[/cyan]", "")
        .replace("[magenta]", "").replace("[/magenta]", "")
        .replace("[blue]", "").replace("[/blue]", "")
        .replace("[bold]", "").replace("[/bold]", "")
    )


def summarize_mcp_result(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        summary: dict[str, Any] = {}
        for key, value in result.items():
            if isinstance(value, list):
                summary[key] = {"count": len(value)}
            elif isinstance(value, dict):
                summary[key] = summarize_mcp_result(value)
            else:
                summary[key] = value
        return summary
    if isinstance(result, list):
        return {"count": len(result)}
    return {"value": result}


def mcp_stock_marker(arguments: dict[str, Any]) -> str:
    for key in ("instrument", "stock", "ticker", "symbol"):
        value = str(arguments.get(key, "")).strip()
        if value:
            return value
    order_book_id = str(arguments.get("order_book_id", "")).strip()
    if order_book_id:
        return f"OB {order_book_id}"
    query = str(arguments.get("query", "")).strip()
    if query:
        return query
    return ""


def mcp_side_badge(value: Any) -> str:
    side = str(value or "").strip().lower()
    if side == "buy":
        return "[green]BUY[/green]"
    if side == "sell":
        return "[red]SELL[/red]"
    return side.upper() if side else "-"


def mcp_trade_detail(tool: str, arguments: dict[str, Any]) -> str:
    if tool in {"avanza_order_set", "avanza_paper_order_set"}:
        side = mcp_side_badge(arguments.get("order_type", "buy"))
        volume = arguments.get("volume", "-")
        price = arguments.get("price", "-")
        return f"{side} [bold]{volume}[/bold] @ {price} SEK"

    if tool in {"avanza_order_edit", "avanza_open_order_edit"}:
        volume = arguments.get("volume", "-")
        price = arguments.get("price", "-")
        return f"[magenta]EDIT[/magenta] [bold]{volume}[/bold] @ {price} SEK"

    if tool in {"avanza_order_delete", "avanza_open_order_cancel"}:
        return f"[red]DELETE[/red] {arguments.get('order_id', '-')}"

    if tool in {"avanza_stoploss_set", "avanza_paper_stoploss_set", "avanza_stoploss_edit", "avanza_stoploss_replace"}:
        side = mcp_side_badge(arguments.get("order_type", "sell"))
        volume = arguments.get("volume", "-")
        trigger_value = arguments.get("trigger_value", "-")
        trigger_value_type = str(arguments.get("trigger_value_type", "%")).strip()
        trigger_suffix = "%" if trigger_value_type in {"%", "percentage"} else " SEK"
        return f"{side} [bold]{volume}[/bold] SL {trigger_value}{trigger_suffix}"

    if tool == "avanza_stoploss_delete":
        return f"[red]DELETE[/red] {arguments.get('stop_loss_id', '-')}"

    if tool == "avanza_transactions":
        from_date = str(arguments.get("transactions_from", "")).strip() or "..."
        to_date = str(arguments.get("transactions_to", "")).strip() or "today"
        return f"[blue]HISTORY[/blue] {from_date}→{to_date}"

    return ""


def mcp_call_log_line(tool: str, arguments: dict[str, Any], marker_override: str | None = None) -> str:
    parts = [f"← {tool}"]
    marker = marker_override or mcp_stock_marker(arguments)
    if marker:
        parts.append(f"[cyan]{marker}[/cyan]")
    detail = mcp_trade_detail(tool, arguments)
    if detail:
        parts.append(detail)
    return "  ".join(parts)


def mcp_result_log_suffix(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    if payload.get("paper"):
        return " [blue]PAPER[/blue]"
    if payload.get("dry_run") is True:
        return " [yellow]DRY[/yellow]"
    if payload.get("dry_run") is False:
        return " [green]LIVE[/green]"
    return ""


def mcp_result_log_detail(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""

    counts: list[str] = []
    for key, label in (
        ("positions", "pos"),
        ("stoplosses", "sl"),
        ("orders", "ord"),
        ("open_orders", "ord"),
        ("paper_orders", "paper"),
        ("transactions", "txn"),
        ("quotes", "qt"),
        ("events", "evt"),
    ):
        value = payload.get(key)
        if isinstance(value, list):
            counts.append(f"{label}:{len(value)}")
    if counts:
        return " [dim]" + " ".join(counts) + "[/dim]"

    request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
    order = payload.get("order") if isinstance(payload.get("order"), dict) else {}
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}

    if order:
        identifier = str(order.get("id", "")).strip()
        if identifier:
            return f" [dim]id:{identifier}[/dim]"

    for candidate in (
        result.get("orderId"),
        result.get("stoplossOrderId"),
        result.get("stopLossOrderId"),
        result.get("id"),
    ):
        token = str(candidate or "").strip()
        if token:
            return f" [dim]id:{token}[/dim]"

    for candidate in (request.get("order_id"), request.get("stop_loss_id")):
        token = str(candidate or "").strip()
        if token:
            return f" [dim]id:{token}[/dim]"
    return ""


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


def pane_weights_after_drag(
    start_positions_weight: float,
    start_activity_weight: float,
    delta_rows: int,
) -> tuple[float, float]:
    delta_weight = delta_rows * PANE_RESIZE_STEP
    positions_weight = clamp(start_positions_weight + delta_weight, MIN_PANE_WEIGHT, MAX_PANE_WEIGHT)
    activity_weight = clamp(start_activity_weight - delta_weight, MIN_PANE_WEIGHT, MAX_PANE_WEIGHT)
    return positions_weight, activity_weight


def side_panel_width_after_drag(start_width: int, delta_columns: int) -> int:
    return clamp(start_width - delta_columns, MIN_ACTIVE_TRADES_WIDTH, MAX_ACTIVE_TRADES_WIDTH)


def ticket_pane_width_after_drag(start_width: int, delta_columns: int) -> int:
    return clamp(start_width - delta_columns, MIN_TICKET_PANE_WIDTH, MAX_TICKET_PANE_WIDTH)


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


def selected_table_row_key(table: DataTable) -> Any | None:
    if table.row_count == 0:
        return None
    try:
        return table.ordered_rows[table.cursor_row].key
    except Exception:
        return None


def restore_table_row_selection(table: DataTable, row_key: Any | None) -> None:
    if row_key is None:
        return
    try:
        table.move_cursor(row=table.get_row_index(row_key), animate=False, scroll=False)
    except Exception:
        return


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
    return [
        f"Account: {preview['account_id']}",
        f"Order book: {preview['order_book_id']}",
        f"Trigger: {trigger['type']} {formatted_typed_value(trigger['value'], trigger['value_type'])}",
        f"Trigger valid until: {trigger['valid_until']}",
        f"Order: {order_event['type']} {order_event['volume']} @ {formatted_typed_value(order_event['price'], order_event['price_type'])}",
        f"Order valid days after trigger: {order_event['valid_days']}",
    ]


def format_order_request(preview: dict[str, Any]) -> list[str]:
    return [
        f"Account: {preview['account_id']}",
        f"Order book: {preview['order_book_id']}",
        f"Side: {preview['order_type']}",
        f"Volume: {preview['volume']}",
        f"Price: {preview['price']} SEK",
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


def open_order_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if looks_like_open_order(item)]

    if not isinstance(payload, dict):
        return []

    collected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def push(items: Any) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not looks_like_open_order(item):
                continue
            identifier = str(item.get("id") or item.get("orderId") or id(item))
            if identifier in seen_ids:
                continue
            seen_ids.add(identifier)
            collected.append(item)

    # Common shapes.
    for key in ("orders", "items", "openOrders", "buyOrders", "sellOrders"):
        push(payload.get(key))

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
                for item in entries:
                    if not isinstance(item, dict) or not looks_like_open_order(item):
                        continue
                    if account_id and not account_id_for_item(item):
                        item = dict(item)
                        item["accountId"] = account_id
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
        "total": "Total P/L",
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
            profit_amount, profit_percent, value_unit = portfolio_profit_summary(portfolio_data, account_id)
        elif profit_mode in WINDOW_PERFORMANCE_KEYS:
            profit_amount, profit_percent, value_unit = portfolio_window_summary(
                portfolio_data,
                account_id,
                profit_mode,
                account,
            )
            if profit_amount is None and profit_percent is None:
                profit_amount, profit_percent, value_unit = account_performance_window_summary(account, profit_mode)
        else:
            profit_amount, profit_percent, value_unit = portfolio_day_summary(portfolio_data, account_id, account)
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
        realtime_status_badge(realtime_override or realtime_status(item)),
    )


def position_state_row_with_quote(
    item: dict[str, Any],
    quote: dict[str, Any] | None,
    realtime_override: str | None = None,
) -> tuple[str, ...]:
    base = list(position_state_row(item, realtime_override))
    if not quote:
        return tuple(base)

    last_price = market_quote_last(quote)
    change_percent = market_quote_change_percent(quote)
    volume = value_number(item, "volume")
    acquired_value = value_number(item, "acquiredValue")
    value_unit = str(nested_value(item, "value", "unit") or "SEK")
    if last_price is None or volume is None:
        return tuple(base)

    current_value = last_price * volume
    day_absolute = current_value * (change_percent / 100.0) if change_percent is not None else None
    profit_amount = (current_value - acquired_value) if acquired_value is not None else None
    profit_percent = ((profit_amount / acquired_value) * 100.0) if (profit_amount is not None and acquired_value) else None

    base[3] = money_text(current_value, value_unit)
    base[5] = percent_text(change_percent)
    base[6] = money_text(day_absolute, value_unit)
    base[7] = percent_text(profit_percent)
    base[8] = money_text(profit_amount, value_unit)
    return tuple(base)


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
    orderbook = item.get("orderbook") or item.get("instrument") or {}
    price = item.get("price", "")
    price_type = item.get("priceType", "") or item.get("price_type", "")
    return (
        "Order",
        str(item.get("status", "")),
        str(orderbook.get("name", "")),
        side_badge(item.get("type", "") or item.get("orderType", "")),
        str(item.get("volume", "")),
        formatted_typed_value(price, price_type) if price_type else str(price),
        str(item.get("validUntil", "")),
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
    orderbook = item.get("orderbook") or item.get("instrument") or {}
    price = item.get("price", "")
    price_type = item.get("priceType", "") or item.get("price_type", "")
    return (
        "Live",
        "Order",
        str(orderbook.get("name", "")),
        side_badge(item.get("type", "") or item.get("orderType", "")),
        str(item.get("volume", "")),
        formatted_typed_value(price, price_type) if price_type else str(price),
        str(item.get("validUntil", "")),
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
    order_event = StopLossOrderEvent(
        type=enum_value(OrderType, str(args.get("order_type", "sell"))),
        price=float(args["order_price"]),
        volume=float(args["volume"]),
        valid_days=int(args.get("order_valid_days", STOPLOSS_ORDER_VALID_DAYS_DEFAULT)),
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
            "price_type": order_event.price_type.value,
            "short_selling_allowed": order_event.short_selling_allowed,
        },
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


def flattened_search_hits(results: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    if isinstance(results, list):
        source = results
    elif isinstance(results, dict):
        if "hits" in results:
            source = results.get("hits") or []
        elif "topHits" in results:
            source = results.get("topHits") or []
        else:
            source = [results]
    else:
        return []

    for hit_group in source:
        if not isinstance(hit_group, dict):
            continue
        group_type = hit_group.get("instrumentType", "")
        top_hits = hit_group.get("topHits") or []
        if not top_hits:
            row = dict(hit_group)
            if group_type:
                row.setdefault("instrumentType", group_type)
            rows.append(row)
            continue
        for hit in top_hits:
            if isinstance(hit, dict):
                row = dict(hit)
                row.setdefault("instrumentType", group_type)
                rows.append(row)
    return rows


def search_hit_order_book_id(hit: dict[str, Any]) -> str:
    orderbook = hit.get("orderbook") if isinstance(hit.get("orderbook"), dict) else {}
    return str(hit.get("id") or hit.get("orderbookId") or orderbook.get("id") or "")


def search_hit_label(hit: dict[str, Any]) -> str:
    name = str(hit.get("name") or hit.get("shortName") or "").strip()
    ticker = str(hit.get("tickerSymbol") or hit.get("symbol") or "").strip()
    instrument_type = str(hit.get("instrumentType") or "").strip()
    currency = str(hit.get("currency") or "").strip()
    order_book_id = search_hit_order_book_id(hit)

    parts = [name or order_book_id]
    meta = [value for value in (ticker, instrument_type, currency) if value]
    if meta:
        parts.append(f"({' / '.join(meta)})")
    return " ".join(parts)


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
                hit.get("isin", ""),
                hit.get("currency", ""),
            )
        )

    render_table(
        "Search Results",
        ["Name", "Ticker", "Type", "ISIN", "Currency"],
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
            "Status",
            "Account",
            "Stock",
            "Trigger",
            "Order",
            "Valid Until",
        ],
        rows,
    )


def render_orders(orders: Any) -> None:
    items = open_order_items(orders)
    rows = [open_order_row(item) for item in items if isinstance(item, dict)]
    if not rows:
        render_message("Open Orders", ["No open orders found."])
        return
    render_table(
        "Open Orders",
        ["Kind", "Status", "Stock", "Side", "Volume", "Price", "Valid Until"],
        rows,
    )


def parse_transaction_types(values: Any) -> list[TransactionsDetailsType]:
    if values in (None, "", []):
        return [TransactionsDetailsType.BUY, TransactionsDetailsType.SELL]

    raw_values: list[str]
    if isinstance(values, str):
        raw_values = [chunk.strip() for chunk in values.split(",") if chunk.strip()]
    elif isinstance(values, (list, tuple, set)):
        raw_values = [str(value).strip() for value in values if str(value).strip()]
    else:
        raise ValueError("transaction types must be a comma-separated string or a list.")

    if not raw_values:
        return [TransactionsDetailsType.BUY, TransactionsDetailsType.SELL]
    return [enum_value(TransactionsDetailsType, value) for value in raw_values]


def transactions_items(payload: Any) -> tuple[list[dict[str, Any]], str | None]:
    if hasattr(payload, "model_dump"):
        try:
            payload = payload.model_dump()
        except Exception:
            pass
    if isinstance(payload, dict):
        raw_items = payload.get("transactions") or payload.get("items") or []
        first_date = str(payload.get("firstTransactionDate") or "") or None
    elif isinstance(payload, list):
        raw_items = payload
        first_date = None
    else:
        raw_items = []
        first_date = None
    return [item for item in raw_items if isinstance(item, dict)], first_date


def transaction_matches_filters(item: dict[str, Any], account_id: str | None, executed_only: bool) -> bool:
    if account_id and order_account_id(item, None) != account_id:
        return False
    if not executed_only:
        return True
    return str(item.get("type", "")).upper() in {"BUY", "SELL"}


def transaction_history_row(item: dict[str, Any]) -> tuple[Any, ...]:
    orderbook = item.get("orderbook") if isinstance(item.get("orderbook"), dict) else {}
    side = str(item.get("type", "")).upper()
    side_cell = side_badge(side.lower()) if side in {"BUY", "SELL"} else side
    return (
        str(item.get("tradeDate") or item.get("date") or ""),
        str(nested_value(item, "account", "name") or ""),
        str(item.get("instrumentName") or orderbook.get("name") or item.get("description") or ""),
        side_cell,
        amount(item, "volume"),
        amount(item, "priceInTransactionCurrency") or amount(item, "priceInTradedCurrency"),
        amount(item, "amount"),
        amount(item, "commission"),
        amount(item, "result"),
    )


def transaction_order_history_row(item: dict[str, Any]) -> tuple[Any, ...]:
    orderbook = item.get("orderbook") if isinstance(item.get("orderbook"), dict) else {}
    account = item.get("account") if isinstance(item.get("account"), dict) else {}
    return (
        str(item.get("tradeDate", "")),
        side_badge(str(item.get("type", "")).lower()),
        str(item.get("instrumentName", "") or orderbook.get("name", "")),
        quantity_text(nested_value(item, "volume", "value")),
        amount(item, "priceInTransactionCurrency"),
        amount(item, "amount"),
        amount(item, "result"),
        str(account.get("name", "")),
    )


def transaction_activity_row(item: dict[str, Any]) -> tuple[Any, ...]:
    account = item.get("account") if isinstance(item.get("account"), dict) else {}
    return (
        str(item.get("tradeDate", "")),
        str(account.get("name", "")),
        str(item.get("type", "")),
        str(item.get("instrumentName", "") or item.get("description", "")),
        quantity_text(nested_value(item, "volume", "value")),
        amount(item, "priceInTransactionCurrency"),
        amount(item, "amount"),
        amount(item, "result"),
        str(item.get("isin", "")),
    )


def transaction_history_dict_row(item: dict[str, Any]) -> dict[str, Any]:
    orderbook = item.get("orderbook") if isinstance(item.get("orderbook"), dict) else {}
    return {
        "Trade Date": str(item.get("tradeDate") or item.get("date") or ""),
        "Account": str(nested_value(item, "account", "name") or ""),
        "Stock": str(item.get("instrumentName") or orderbook.get("name") or item.get("description") or ""),
        "Type": str(item.get("type", "")).upper(),
        "Volume": amount(item, "volume"),
        "Price": amount(item, "priceInTransactionCurrency") or amount(item, "priceInTradedCurrency"),
        "Amount": amount(item, "amount"),
        "Commission": amount(item, "commission"),
        "Result": amount(item, "result"),
        "ISIN": str(item.get("isin") or orderbook.get("isin") or ""),
        "Description": str(item.get("description") or ""),
    }


def render_transactions_history(
    payload: Any,
    account_id: str | None = None,
    executed_only: bool = True,
) -> None:
    items, first_date = transactions_items(payload)
    rows = [
        transaction_history_row(item)
        for item in items
        if transaction_matches_filters(item, account_id, executed_only)
    ]
    if not rows:
        render_message("Transaction History", ["No matching transactions found."])
        return

    heading = "Executed Orders History" if executed_only else "Transaction History"
    if first_date:
        heading = f"{heading} (first available: {first_date})"

    render_table(
        heading,
        ["Trade Date", "Account", "Stock", "Type", "Volume", "Price", "Amount", "Commission", "Result"],
        rows,
    )


def empty_paper_session() -> dict[str, Any]:
    now = datetime.now().isoformat(timespec="seconds")
    return {
        "version": 1,
        "created_at": now,
        "updated_at": now,
        "orders": [],
        "events": [],
    }


def load_paper_session(path: Path | None = None) -> dict[str, Any]:
    path = path or PAPER_SESSION_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty_paper_session()
    if not isinstance(data, dict):
        return empty_paper_session()
    data.setdefault("version", 1)
    data.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
    data.setdefault("updated_at", data["created_at"])
    data.setdefault("orders", [])
    data.setdefault("events", [])
    if not isinstance(data["orders"], list):
        data["orders"] = []
    if not isinstance(data["events"], list):
        data["events"] = []
    return data


def save_paper_session(session: dict[str, Any], path: Path | None = None) -> None:
    path = path or PAPER_SESSION_FILE
    session["updated_at"] = datetime.now().isoformat(timespec="seconds")
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(session, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(temp_path, path)


def append_paper_event(session: dict[str, Any], event_type: str, payload: dict[str, Any]) -> None:
    events = session.setdefault("events", [])
    events.append(
        {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "type": event_type,
            "payload": payload,
        }
    )


def paper_orders(session: dict[str, Any], account_id: str | None = None, active_only: bool = False) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in session.get("orders", []):
        if not isinstance(item, dict):
            continue
        request = item.get("request") if isinstance(item.get("request"), dict) else {}
        if account_id and str(request.get("account_id", "")) != account_id:
            continue
        if active_only and str(item.get("status", "")).upper() not in {"ACTIVE", "PENDING"}:
            continue
        rows.append(item)
    return rows


def create_paper_stop_loss_order(args: dict[str, Any], instrument: str = "") -> dict[str, Any]:
    _, _, preview = build_stop_loss_preview(args)
    timestamp = datetime.now().isoformat(timespec="seconds")
    return {
        "id": f"paper-{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
        "kind": "Stop-loss",
        "status": "ACTIVE",
        "created_at": timestamp,
        "updated_at": timestamp,
        "instrument": instrument,
        "request": preview,
    }


def create_paper_order(args: dict[str, Any], instrument: str = "") -> dict[str, Any]:
    _, _, preview = build_order_preview(args)
    timestamp = datetime.now().isoformat(timespec="seconds")
    return {
        "id": f"paper-order-{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
        "kind": "Order",
        "status": "ACTIVE",
        "created_at": timestamp,
        "updated_at": timestamp,
        "instrument": instrument,
        "request": preview,
    }


def cancel_paper_order(session: dict[str, Any], paper_order_id: str) -> dict[str, Any]:
    for item in session.get("orders", []):
        if isinstance(item, dict) and str(item.get("id", "")) == paper_order_id:
            item["status"] = "CANCELLED"
            item["updated_at"] = datetime.now().isoformat(timespec="seconds")
            append_paper_event(session, "paper_cancel", {"id": paper_order_id})
            return item
    raise ValueError(f"Unknown paper order id: {paper_order_id}")


def scalar_number(value: Any) -> float | None:
    if isinstance(value, dict):
        nested = value.get("value")
        if isinstance(nested, (int, float)):
            return float(nested)
        if isinstance(nested, str):
            try:
                return float(nested.replace(",", ""))
            except ValueError:
                return None
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", ""))
        except ValueError:
            return None
    return None


def first_numeric(payload: Any, paths: tuple[tuple[str, ...], ...]) -> float | None:
    for path in paths:
        current = payload
        for key in path:
            if isinstance(current, dict):
                current = current.get(key)
            else:
                current = None
            if current is None:
                break
        value = scalar_number(current)
        if value is not None:
            return value
    return None


def market_quote_last(payload: dict[str, Any]) -> float | None:
    return first_numeric(
        payload,
        (
            ("quote", "last"),
            ("quote", "lastPrice"),
            ("lastPrice",),
            ("last",),
            ("price",),
            ("orderBook", "quote", "last"),
            ("orderbook", "quote", "last"),
        ),
    )


def market_quote_change_percent(payload: dict[str, Any]) -> float | None:
    return first_numeric(
        payload,
        (
            ("quote", "changePercent"),
            ("changePercent",),
            ("quote", "change", "percent"),
            ("change", "percent"),
        ),
    )


def paper_order_request(item: dict[str, Any]) -> dict[str, Any]:
    request = item.get("request")
    return request if isinstance(request, dict) else {}


def paper_order_book_id(item: dict[str, Any]) -> str:
    request = paper_order_request(item)
    return str(request.get("order_book_id", ""))


def paper_order_is_active(item: dict[str, Any]) -> bool:
    return str(item.get("status", "")).upper() in PAPER_ORDER_ACTIVE_STATES


def paper_order_side(item: dict[str, Any]) -> str:
    request = paper_order_request(item)
    kind = str(item.get("kind", ""))
    if kind == "Order":
        return str(request.get("order_type", "")).lower()
    order_event = request.get("stop_loss_order_event") if isinstance(request.get("stop_loss_order_event"), dict) else {}
    return str(order_event.get("type", "")).lower()


def paper_order_volume(item: dict[str, Any]) -> float:
    request = paper_order_request(item)
    kind = str(item.get("kind", ""))
    if kind == "Order":
        return float(request.get("volume", 0) or 0)
    order_event = request.get("stop_loss_order_event") if isinstance(request.get("stop_loss_order_event"), dict) else {}
    return float(order_event.get("volume", 0) or 0)


def order_account_id(item: dict[str, Any], fallback: str | None = None) -> str:
    account = item.get("account") if isinstance(item.get("account"), dict) else {}
    return str(
        account.get("id")
        or item.get("accountId")
        or item.get("account_id")
        or fallback
        or ""
    )


def order_stock_name(item: dict[str, Any]) -> str:
    orderbook = item.get("orderbook") or item.get("instrument") or {}
    if isinstance(orderbook, dict):
        return str(orderbook.get("name") or "")
    return ""


class PaneResizer(Static):
    def __init__(self) -> None:
        super().__init__("─", id="pane-resizer")

    @staticmethod
    def event_y(event: events.MouseDown | events.MouseMove | events.MouseUp) -> int:
        return int(event.screen_y if event.screen_y is not None else event.y)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self.capture_mouse(True)
        self.add_class("dragging")
        self.app.start_pane_resize(self.event_y(event))
        event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        self.app.update_pane_resize(self.event_y(event))
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        self.release_mouse()
        self.remove_class("dragging")
        self.app.finish_pane_resize()
        event.stop()


class ActivityPaneResizer(Static):
    def __init__(self) -> None:
        super().__init__("─", id="activity-resizer")

    @staticmethod
    def event_y(event: events.MouseDown | events.MouseMove | events.MouseUp) -> int:
        return int(event.screen_y if event.screen_y is not None else event.y)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self.capture_mouse(True)
        self.add_class("dragging")
        self.app.start_activity_resize(self.event_y(event))
        event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        self.app.update_activity_resize(self.event_y(event))
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        self.release_mouse()
        self.remove_class("dragging")
        self.app.finish_activity_resize()
        event.stop()


class SidePaneResizer(Static):
    def __init__(self) -> None:
        super().__init__("│", id="side-pane-resizer")

    @staticmethod
    def event_x(event: events.MouseDown | events.MouseMove | events.MouseUp) -> int:
        return int(event.screen_x if event.screen_x is not None else event.x)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self.capture_mouse(True)
        self.add_class("dragging")
        self.app.start_side_pane_resize(self.event_x(event))
        event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        self.app.update_side_pane_resize(self.event_x(event))
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        self.release_mouse()
        self.remove_class("dragging")
        self.app.finish_side_pane_resize()
        event.stop()


class TicketPaneResizer(Static):
    def __init__(self, ticket: str) -> None:
        super().__init__("│", id=f"{ticket}-ticket-resizer", classes="ticket-resizer")

    @staticmethod
    def event_x(event: events.MouseDown | events.MouseMove | events.MouseUp) -> int:
        return int(event.screen_x if event.screen_x is not None else event.x)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self.capture_mouse(True)
        self.add_class("dragging")
        self.app.start_ticket_pane_resize(self.event_x(event))
        event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        self.app.update_ticket_pane_resize(self.event_x(event))
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        self.release_mouse()
        self.remove_class("dragging")
        self.app.finish_ticket_pane_resize()
        event.stop()


MCP_TOOLS = [
    {
        "name": "avanza_status",
        "description": "Show TUI MCP bridge status, selected account, and current safety mode.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "avanza_accounts",
        "description": "List Avanza accounts currently visible to the authenticated TUI session.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "avanza_portfolio",
        "description": "List portfolio positions for the selected account, or a supplied account_id.",
        "inputSchema": {
            "type": "object",
            "properties": {"account_id": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_stoplosses",
        "description": "List stop-loss orders for the selected account, or a supplied account_id.",
        "inputSchema": {
            "type": "object",
            "properties": {"account_id": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_open_orders",
        "description": "List live open/pending regular orders for the selected account, or a supplied account_id.",
        "inputSchema": {
            "type": "object",
            "properties": {"account_id": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_ongoing_orders",
        "description": "List ongoing orders for the selected account: live stop-losses + live open orders, with optional paper active orders.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "include_paper": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_transactions",
        "description": "List executed orders/history (BUY/SELL by default) with optional account/date/type filters.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "transactions_from": {"type": "string"},
                "transactions_to": {"type": "string"},
                "types": {
                    "type": "array",
                    "items": {"type": "string", "enum": TRANSACTION_TYPE_CHOICES},
                },
                "isin": {"type": "string"},
                "max_elements": {"type": "integer", "minimum": 1, "maximum": 20000, "default": 1000},
                "executed_only": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_live_snapshot",
        "description": "Read a decision-ready snapshot for polling loops: positions, live stop-losses/orders, paper orders, and safety mode.",
        "inputSchema": {
            "type": "object",
            "properties": {"account_id": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_realtime_quotes",
        "description": "Fetch real-time quote snapshot for selected account holdings (best with a 5s polling loop).",
        "inputSchema": {
            "type": "object",
            "properties": {"account_id": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_search_stock",
        "description": "Search Avanza stock/order book data by name, ticker, or ISIN.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_paper_stoploss_set",
        "description": "Create a local paper stop-loss order. This never places an Avanza order and is allowed in MCP read-only mode.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "order_book_id": {"type": "string"},
                "instrument": {"type": "string"},
                "trigger_type": {"type": "string"},
                "trigger_value": {"type": "number"},
                "trigger_value_type": {"type": "string", "default": "%"},
                "valid_until": {"type": "string"},
                "order_type": {"type": "string", "default": "sell"},
                "order_price": {"type": "number"},
                "order_price_type": {"type": "string", "default": "%"},
                "volume": {"type": "number"},
                "order_valid_days": {"type": "integer", "default": STOPLOSS_ORDER_VALID_DAYS_DEFAULT},
                "trigger_on_market_maker_quote": {"type": "boolean", "default": False},
                "short_selling_allowed": {"type": "boolean", "default": False},
            },
            "required": [
                "account_id",
                "order_book_id",
                "trigger_value",
                "order_price",
                "volume",
            ],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_paper_orders",
        "description": "List local paper-trading orders and events for the selected account, or a supplied account_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "active_only": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_paper_order_set",
        "description": "Create a local paper buy/sell order. This never places an Avanza order and is allowed in MCP read-only mode.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "order_book_id": {"type": "string"},
                "instrument": {"type": "string"},
                "order_type": {"type": "string", "default": "buy"},
                "price": {"type": "number"},
                "valid_until": {"type": "string"},
                "volume": {"type": "integer"},
                "condition": {"type": "string", "default": "normal"},
            },
            "required": ["account_id", "order_book_id", "price", "valid_until", "volume"],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_paper_cancel",
        "description": "Cancel a local paper order. This never changes Avanza and is allowed in MCP read-only mode.",
        "inputSchema": {
            "type": "object",
            "properties": {"paper_order_id": {"type": "string"}},
            "required": ["paper_order_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_stoploss_set",
        "description": "Dry-run or place a stop-loss order. Live placement requires TUI R/W mode and confirm=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "order_book_id": {"type": "string"},
                "trigger_type": {"type": "string"},
                "trigger_value": {"type": "number"},
                "trigger_value_type": {"type": "string", "default": "%"},
                "valid_until": {"type": "string"},
                "order_type": {"type": "string", "default": "sell"},
                "order_price": {"type": "number"},
                "order_price_type": {"type": "string", "default": "%"},
                "volume": {"type": "number"},
                "order_valid_days": {"type": "integer", "default": STOPLOSS_ORDER_VALID_DAYS_DEFAULT},
                "trigger_on_market_maker_quote": {"type": "boolean", "default": False},
                "short_selling_allowed": {"type": "boolean", "default": False},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": [
                "account_id",
                "order_book_id",
                "trigger_value",
                "order_price",
                "volume",
            ],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_order_set",
        "description": "Dry-run or place a regular buy/sell order. Live placement requires TUI R/W mode and confirm=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "order_book_id": {"type": "string"},
                "order_type": {"type": "string", "default": "buy"},
                "price": {"type": "number"},
                "valid_until": {"type": "string"},
                "volume": {"type": "integer"},
                "condition": {"type": "string", "default": "normal"},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["account_id", "order_book_id", "price", "valid_until", "volume"],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_order_edit",
        "description": "Dry-run or update an existing open order (price/volume/valid_until). Live update requires TUI R/W mode and confirm=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "order_id": {"type": "string"},
                "price": {"type": "number"},
                "valid_until": {"type": "string"},
                "volume": {"type": "integer"},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["account_id", "order_id", "price", "valid_until", "volume"],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_open_order_edit",
        "description": "Dry-run or update an existing open/pending regular order (alias of avanza_order_edit). Live update requires TUI R/W mode and confirm=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "order_id": {"type": "string"},
                "price": {"type": "number"},
                "valid_until": {"type": "string"},
                "volume": {"type": "integer"},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["account_id", "order_id", "price", "valid_until", "volume"],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_order_delete",
        "description": "Dry-run or delete a regular open order. Live deletion requires TUI R/W mode and confirm=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "order_id": {"type": "string"},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["account_id", "order_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_open_order_cancel",
        "description": "Dry-run or cancel an existing open/pending regular order (alias of avanza_order_delete). Live cancellation requires TUI R/W mode and confirm=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "order_id": {"type": "string"},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["account_id", "order_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_stoploss_delete",
        "description": "Dry-run or delete a stop-loss order. Live deletion requires TUI R/W mode and confirm=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "stop_loss_id": {"type": "string"},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["account_id", "stop_loss_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_stoploss_edit",
        "description": "Dry-run or edit an existing stop-loss (delete old + place new). Supports gliding triggers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "stop_loss_id": {"type": "string"},
                "order_book_id": {"type": "string"},
                "trigger_type": {"type": "string"},
                "trigger_value": {"type": "number"},
                "trigger_value_type": {"type": "string", "default": "%"},
                "valid_until": {"type": "string"},
                "order_type": {"type": "string", "default": "sell"},
                "order_price": {"type": "number"},
                "order_price_type": {"type": "string", "default": "%"},
                "volume": {"type": "number"},
                "order_valid_days": {"type": "integer", "default": STOPLOSS_ORDER_VALID_DAYS_DEFAULT},
                "trigger_on_market_maker_quote": {"type": "boolean", "default": False},
                "short_selling_allowed": {"type": "boolean", "default": False},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": [
                "account_id",
                "stop_loss_id",
                "order_book_id",
                "trigger_value",
                "order_price",
                "volume",
            ],
            "additionalProperties": False,
        },
    },
]


class AvanzaMcpHttpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], handler_class: type[BaseHTTPRequestHandler], app: "AvanzaTradingTui", token: str) -> None:
        super().__init__(server_address, handler_class)
        self.app = app
        self.token = token


class AvanzaMcpRequestHandler(BaseHTTPRequestHandler):
    server: AvanzaMcpHttpServer

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def send_json(self, status_code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self) -> bool:
        expected = self.server.token
        auth = self.headers.get("Authorization", "")
        header_token = self.headers.get("X-Avanza-MCP-Token", "")
        return auth == f"Bearer {expected}" or header_token == expected

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Expected JSON object.")
        return data

    def do_GET(self) -> None:
        if self.path != "/status":
            self.send_json(404, {"error": "not found"})
            return
        if not self.authorized():
            self.send_json(401, {"error": "unauthorized"})
            return
        try:
            payload = self.server.app.call_from_thread(self.server.app.mcp_status_payload)
            self.send_json(200, payload)
        except Exception as exc:
            self.send_json(500, {"ok": False, "error": str(exc)})

    def do_POST(self) -> None:
        if self.path != "/call":
            self.send_json(404, {"error": "not found"})
            return
        if not self.authorized():
            self.send_json(401, {"error": "unauthorized"})
            return
        try:
            request = self.read_json_body()
            tool = str(request.get("tool", ""))
            arguments = request.get("arguments") or {}
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be an object.")
            payload = self.server.app.call_from_thread(self.server.app.handle_mcp_tool_call, tool, arguments)
            self.send_json(200, payload)
        except Exception as exc:
            self.send_json(500, {"ok": False, "error": str(exc)})


def mcp_session_payload(host: str, port: int, token: str, read_write: bool) -> dict[str, Any]:
    return {
        "url": f"http://{host}:{port}",
        "token": token,
        "read_write": read_write,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "proxy_command": f"python {Path(__file__).name} mcp",
    }


def write_mcp_session_file(path: Path, payload: dict[str, Any]) -> None:
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(temp_path, path)


def remove_mcp_session_file(path: Path | None = None) -> None:
    path = path or MCP_SESSION_FILE
    try:
        path.unlink()
    except FileNotFoundError:
        pass


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
        padding: 0 1;
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
    }

    #account-row {
        height: 4;
        align: left middle;
    }

    #app-title {
        width: 9;
        text-style: bold;
    }

    #account-select {
        width: 48;
        margin-right: 1;
    }

    #metric-grid {
        height: 4;
    }

    .metric-card {
        width: 1fr;
        height: 3;
        margin: 0 1 0 0;
        padding: 0 1;
        background: $boost;
        border-left: solid $primary;
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
    }

    #metric-profit-value {
        height: 2;
        padding: 0 1;
        content-align: left middle;
    }

    #clock-status {
        height: 2;
        content-align: right middle;
        color: $accent;
        text-style: bold;
    }

    #button-controls {
        height: 2;
        align: right middle;
    }

    #view-controls {
        height: 2;
        align: right middle;
    }

    #view-label {
        width: auto;
        margin-right: 1;
        color: $text-muted;
        text-style: bold;
    }

    #controls-separator {
        height: 1;
        border-top: solid $primary-darken-3;
    }

    .view-tab {
        background: $primary-darken-3;
        color: $text;
        border: solid $primary;
        border-bottom: solid $primary;
        text-style: bold;
    }

    .view-tab:hover {
        background: $accent-darken-2;
        color: $text;
    }

    #open-orders-overlay {
        min-width: 9;
    }

    #open-transactions-overlay {
        min-width: 15;
    }

    #toggle-controls {
        height: 2;
        align: right middle;
    }

    .toggle-control {
        width: auto;
        height: 1;
        margin-left: 1;
        align: left middle;
    }

    #live-status {
        width: 9;
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

    #orders-overlay,
    #transactions-overlay {
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
    #transactions-overlay-note {
        height: 1;
        margin-bottom: 1;
        color: $text-muted;
    }

    #orders-history-table,
    #transactions-history-table {
        height: 1fr;
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
        self.debug_mode = bool(debug)
        self.debug_profile_top = max(5, int(debug_profile_top))
        self.debug_profile_depth = 0
        self.debug_session_log_path = create_session_log_path("debug") if self.debug_mode else None
        self.avanza: Avanza | None = None
        self.accounts: list[dict[str, Any]] = []
        self.selected_account_id: str | None = None
        self.live_refresh_timer = None
        self.clock_timer = None
        self.mcp_health_timer = None
        self.order_search_timer = None
        self.login_progress_timer = None
        self.login_thread: threading.Thread | None = None
        self.login_busy = False
        self.login_spinner_index = 0
        self.login_progress_tick = 0
        self.login_progress_messages: tuple[str, ...] = ()
        self.login_progress_index = 0
        self.login_stage_message = ""
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
        self.live_refresh_thread: threading.Thread | None = None
        self.live_refresh_inflight = False
        self.live_refresh_pending = False
        self.live_refresh_lock = threading.Lock()
        self.mcp_server: AvanzaMcpHttpServer | None = None
        self.mcp_thread: threading.Thread | None = None
        self.mcp_token: str | None = None
        self.mcp_write_enabled = False
        self.paper_mode_enabled = True
        self.paper_session_path = PAPER_SESSION_FILE
        self.paper_session = load_paper_session(self.paper_session_path)
        self.session_log_path = create_session_log_path("tui")
        self.latest_portfolio_data: dict[str, Any] | None = None
        self.latest_stoploss_items: list[dict[str, Any]] = []
        self.latest_open_order_items: list[dict[str, Any]] = []
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
                "session_log": str(self.session_log_path),
                "paper_session_file": str(self.paper_session_path),
                "debug_mode": self.debug_mode,
                "debug_session_log": str(self.debug_session_log_path) if self.debug_session_log_path else "",
            },
        )
        if self.debug_mode:
            self.debug_log("Debug mode enabled.")

    def compose(self) -> ComposeResult:
        default_valid_until = max_valid_until_date().isoformat()
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
                        yield Static("Avanza", id="app-title")
                        yield Select([], prompt="Select account", allow_blank=True, id="account-select")
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
                        yield Button("Order", id="open-order-modal", variant="primary")
                        yield Button("Stop-Loss", id="open-stoploss-modal", variant="warning")
                    yield Static("", id="controls-separator")
                    with Horizontal(id="view-controls"):
                        yield Static("Views", id="view-label")
                        yield Button("Orders", id="open-orders-overlay", classes="view-tab")
                        yield Button("Transactions", id="open-transactions-overlay", classes="view-tab")
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

        self.write_log("Ready. Log in, then refresh portfolio or stop-losses.")
        if self.debug_mode and self.debug_session_log_path is not None:
            self.write_log(f"[yellow]Debug profiling enabled:[/yellow] {self.debug_session_log_path}")
        self.write_mcp_log("MCP disabled. Log in, then enable MCP mode.")
        self.update_clock_status()
        self.start_clock()
        if self.mcp_health_timer is None:
            self.mcp_health_timer = self.set_interval(MCP_HEALTH_CHECK_SECONDS, self.ensure_mcp_bridge_health, pause=False)
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
            if widget.value == Select.BLANK:
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

    def clear_secret_inputs(self) -> None:
        self.query_one("#password", Input).value = ""
        self.query_one("#totp", Input).value = ""

    def set_login_controls_enabled(self, enabled: bool) -> None:
        for widget_id in ("username", "password", "totp", "onepassword-item", "onepassword-vault"):
            try:
                self.query_one(f"#{widget_id}", Input).disabled = not enabled
            except Exception:
                pass
        for widget_id in ("login", "onepassword-login"):
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
        self.login_stage_message = ""
        self.login_progress_messages = ()
        try:
            self.query_one("#login-progress", Static).display = False
            self.query_one("#login-progress-detail", Static).display = False
        except Exception:
            pass
        self.set_login_controls_enabled(True)

    def apply_accounts_overview(self, overview: dict[str, Any], announce: bool = True) -> None:
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
        if announce:
            self.write_log(f"Loaded {len(self.accounts)} account(s).")
        if self.accounts:
            selected = next((a for a in self.accounts if str(a.get("id", "")) == self.selected_account_id), None)
            if selected is None:
                selected = default_account(self.accounts)
            if selected is not None:
                self.set_selected_account(selected)
                account_select.value = self.selected_account_id

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

    def complete_login(self, avanza: Avanza, overview: dict[str, Any], portfolio: dict[str, Any], stoplosses: Any, orders: Any) -> None:
        self.avanza = avanza
        self.clear_secret_inputs()
        self.query_one("#login-screen").display = False
        self.query_one("#workspace").display = True
        self.write_log("[green]Logged in. Secret fields cleared.[/green]")
        self.apply_accounts_overview(overview, announce=True)
        self.apply_portfolio_data(portfolio, fetch_quotes=False)
        self.apply_stoploss_orders_data(stoplosses, orders)
        self.start_live_refresh()

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
            append_jsonl(LOG_DIR / category_file, record)

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

    def ensure_mcp_bridge_health(self) -> None:
        if self.mcp_server is None:
            return

        # If the bridge thread died, restart transparently.
        if self.mcp_thread is None or not self.mcp_thread.is_alive():
            self.record_event("mcp", "bridge_thread_dead", {"action": "restart"})
            self.write_mcp_log("[yellow]MCP bridge thread stopped; restarting.[/yellow]")
            self.stop_mcp_bridge(announce=False)
            try:
                self.start_mcp_bridge()
                self.update_mode_toggles()
            except Exception as exc:
                self.record_event("mcp", "bridge_restart_failed", {"error": str(exc)})
                self.write_mcp_log(f"[red]MCP bridge restart failed:[/red] {exc}")
            return

        # If the session file was removed or became stale, restore it.
        try:
            host, port = self.mcp_server.server_address
            expected_url = f"http://{host}:{port}"
            session = load_mcp_session(MCP_SESSION_FILE)
            token = str(session.get("token", ""))
            read_write = bool(session.get("read_write", False))
            if session.get("url") != expected_url or token != (self.mcp_token or "") or read_write != self.mcp_write_enabled:
                self.update_mcp_session_file()
        except Exception:
            self.update_mcp_session_file()

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

    def mcp_stock_marker_for_call(self, arguments: dict[str, Any]) -> str:
        marker = mcp_stock_marker(arguments)
        if marker and not marker.startswith("OB "):
            return marker

        order_book_id = str(arguments.get("order_book_id", "")).strip()
        if order_book_id:
            return self.stock_name_for_order_book(order_book_id) or f"OB {order_book_id}"

        stop_loss_id = str(arguments.get("stop_loss_id", "")).strip()
        if stop_loss_id:
            for item in self.latest_stoploss_items:
                if str(item.get("id", "")).strip() == stop_loss_id:
                    return order_stock_name(item) or marker

        order_id = str(arguments.get("order_id", "")).strip()
        if order_id:
            for item in self.latest_open_order_items:
                current = str(item.get("id", "") or item.get("orderId", "")).strip()
                if current == order_id:
                    return order_stock_name(item) or marker
        return marker

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

    def mcp_status_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "enabled": self.mcp_server is not None,
            "read_write": self.mcp_write_enabled,
            "paper_trading": True,
            "selected_account_id": self.selected_account_id,
            "accounts_loaded": len(self.accounts),
            "poll_interval_seconds": LIVE_REFRESH_SECONDS,
            "paper_session_file": str(self.paper_session_path),
        }

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

    def portfolio_snapshot(self, avanza: Any, account_id: str) -> dict[str, Any]:
        data = avanza.get_accounts_positions()
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected portfolio response type: {type(data).__name__}")
        rows = []
        for section in ("withOrderbook", "withoutOrderbook"):
            for item in data.get(section, []):
                if isinstance(item, dict) and matches_account(item, account_id or None):
                    status = self.realtime_status_for_position(item)
                    row = list(position_state_row(item, status))
                    row[-1] = status
                    rows.append(tuple(row))
        return {
            "account_id": account_id or None,
            "positions": rows_as_dicts(
                ["Stock", "Order Book ID", "Volume", "Value", "Avg Price", "Day %", "Day SEK", "Profit %", "Profit", "Real-time"],
                rows,
            ),
        }

    def stoploss_snapshot(self, avanza: Any, account_id: str) -> dict[str, Any]:
        data = avanza.get_all_stop_losses()
        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected stop-loss response type: {type(data).__name__}")
        rows = [stop_loss_row(item) for item in data if isinstance(item, dict) and matches_account(item, account_id or None)]
        return {
            "account_id": account_id or None,
            "stoplosses": rows_as_dicts(
                ["Status", "Account", "Stock", "Trigger", "Order", "Valid Until"],
                rows,
            ),
        }

    def open_orders_snapshot(self, avanza: Any, account_id: str) -> dict[str, Any]:
        try:
            data = avanza.get_orders()
        except Exception:
            data = []
        items = open_order_items(data)
        rows = [open_order_row(item) for item in items if isinstance(item, dict) and matches_account(item, account_id or None)]
        return {
            "account_id": account_id or None,
            "orders": rows_as_dicts(
                ["Kind", "Status", "Stock", "Side", "Volume", "Price", "Valid Until"],
                rows,
            ),
        }

    def update_mcp_session_file(self) -> None:
        if self.mcp_server is None or self.mcp_token is None:
            return
        host, port = self.mcp_server.server_address
        write_mcp_session_file(
            MCP_SESSION_FILE,
            mcp_session_payload(str(host), int(port), self.mcp_token, self.mcp_write_enabled),
        )

    def start_mcp_bridge(self) -> None:
        self.require_connection()
        if self.mcp_server is not None:
            return
        self.mcp_token = secrets.token_urlsafe(24)
        server = AvanzaMcpHttpServer(("127.0.0.1", 0), AvanzaMcpRequestHandler, self, self.mcp_token)
        self.mcp_server = server
        self.mcp_thread = threading.Thread(target=server.serve_forever, name="avanza-mcp-bridge", daemon=True)
        self.mcp_thread.start()
        self.update_mcp_session_file()
        host, port = server.server_address
        self.write_mcp_log(f"[green]MCP enabled[/green] at http://{host}:{port}.")
        self.write_mcp_log(f"Proxy command: python {Path(__file__).name} mcp")

    def stop_mcp_bridge(self, announce: bool = True) -> None:
        if self.mcp_server is None:
            remove_mcp_session_file()
            return
        server = self.mcp_server
        thread = self.mcp_thread
        self.mcp_server = None
        self.mcp_thread = None
        self.mcp_token = None
        try:
            server.shutdown()
        except Exception:
            pass
        try:
            server.server_close()
        except Exception:
            pass
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=0.5)
        remove_mcp_session_file()
        if announce:
            self.write_mcp_log("[yellow]MCP disabled.[/yellow]")

    def on_unmount(self) -> None:
        self.stop_login_progress()
        if self.order_search_timer is not None:
            self.order_search_timer.stop()
            self.order_search_timer = None
        if self.live_refresh_timer is not None:
            self.live_refresh_timer.stop()
            self.live_refresh_timer = None
        if self.clock_timer is not None:
            self.clock_timer.stop()
            self.clock_timer = None
        if self.mcp_health_timer is not None:
            self.mcp_health_timer.stop()
            self.mcp_health_timer = None
        self.stop_mcp_bridge(announce=False)

    def require_mcp_write(self, confirmed: bool) -> None:
        if not confirmed:
            return
        if not self.mcp_write_enabled:
            raise PermissionError("TUI MCP mode is read-only. Enable R/W in the TUI for live mutations.")

    def handle_mcp_tool_call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        marker = self.mcp_stock_marker_for_call(arguments)
        self.write_mcp_log(mcp_call_log_line(tool, arguments, marker_override=marker))
        self.record_event("mcp", "tool_call", {"tool": tool, "arguments": arguments})
        try:
            result = self.execute_mcp_tool(tool, arguments)
            self.write_mcp_log(f"[green]✓[/green] {tool}{mcp_result_log_suffix(result)}{mcp_result_log_detail(result)}")
            self.record_event(
                "mcp",
                "tool_result",
                {"tool": tool, "ok": True, "summary": summarize_mcp_result(result)},
            )
            return {
                "ok": True,
                "tool": tool,
                "read_write": self.mcp_write_enabled,
                "result": result,
            }
        except Exception as exc:
            self.write_mcp_log(f"[red]✗ {tool}:[/red] {exc}")
            self.record_event("mcp", "tool_error", {"tool": tool, "error": str(exc)})
            return {
                "ok": False,
                "tool": tool,
                "read_write": self.mcp_write_enabled,
                "error": str(exc),
            }

    def execute_mcp_tool(self, tool: str, arguments: dict[str, Any]) -> Any:
        avanza = self.require_connection()
        account_id = str(arguments.get("account_id") or self.selected_account_id or "")

        if tool == "avanza_status":
            return self.mcp_status_payload()

        if tool == "avanza_accounts":
            overview = avanza.get_overview()
            accounts = account_rows_from_overview(overview) if isinstance(overview, dict) else []
            return rows_as_dicts(["ID", "Name", "Type", "Total Value", "Buying Power", "Status"], [account_row(account) for account in accounts])

        if tool == "avanza_portfolio":
            return self.portfolio_snapshot(avanza, account_id)

        if tool == "avanza_stoplosses":
            return self.stoploss_snapshot(avanza, account_id)

        if tool == "avanza_open_orders":
            return self.open_orders_snapshot(avanza, account_id)

        if tool == "avanza_ongoing_orders":
            include_paper = bool(arguments.get("include_paper", True))
            return {
                "account_id": account_id or None,
                "stoplosses": self.stoploss_snapshot(avanza, account_id)["stoplosses"],
                "open_orders": self.open_orders_snapshot(avanza, account_id)["orders"],
                "paper_orders": (
                    paper_orders(self.paper_session, account_id or None, active_only=True)
                    if include_paper
                    else []
                ),
            }

        if tool == "avanza_transactions":
            transactions_from_raw = arguments.get("transactions_from")
            transactions_to_raw = arguments.get("transactions_to")
            transactions_from = date.fromisoformat(str(transactions_from_raw)) if transactions_from_raw else None
            transactions_to = date.fromisoformat(str(transactions_to_raw)) if transactions_to_raw else None
            transaction_types = parse_transaction_types(arguments.get("types"))
            isin = str(arguments.get("isin", "") or "") or None
            max_elements = int(arguments.get("max_elements", 1000))
            executed_only = bool(arguments.get("executed_only", True))
            payload = avanza.get_transactions_details(
                transaction_details_types=transaction_types,
                transactions_from=transactions_from,
                transactions_to=transactions_to,
                isin=isin,
                max_elements=max_elements,
            )
            items, first_date = transactions_items(payload)
            rows = [
                transaction_history_dict_row(item)
                for item in items
                if transaction_matches_filters(item, account_id or None, executed_only)
            ]
            return {
                "account_id": account_id or None,
                "executed_only": executed_only,
                "types": [item.value for item in transaction_types],
                "transactions_from": transactions_from.isoformat() if transactions_from else None,
                "transactions_to": transactions_to.isoformat() if transactions_to else None,
                "first_available_date": first_date,
                "transactions": rows,
            }

        if tool == "avanza_live_snapshot":
            account_id = account_id or self.require_selected_account_id()
            realtime_quotes = self.realtime_quotes_snapshot(account_id)
            return {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "account_id": account_id,
                "read_write": self.mcp_write_enabled,
                "paper_trading": True,
                "poll_interval_seconds": LIVE_REFRESH_SECONDS,
                "portfolio": self.portfolio_snapshot(avanza, account_id),
                "stoplosses": self.stoploss_snapshot(avanza, account_id),
                "open_orders": self.open_orders_snapshot(avanza, account_id),
                "realtime_quotes": realtime_quotes,
                "paper_orders": paper_orders(self.paper_session, account_id),
            }

        if tool == "avanza_realtime_quotes":
            account_id = account_id or self.require_selected_account_id()
            return {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "account_id": account_id,
                "poll_interval_seconds": LIVE_REFRESH_SECONDS,
                "quotes": self.realtime_quotes_snapshot(account_id),
            }

        if tool == "avanza_search_stock":
            query = str(arguments["query"])
            limit = int(arguments.get("limit", 10))
            hits = flattened_search_hits(avanza.search_for_stock(query, limit))
            return [
                {
                    "name": hit.get("name", ""),
                    "ticker": hit.get("tickerSymbol", ""),
                    "instrument_type": hit.get("instrumentType", ""),
                    "order_book_id": search_hit_order_book_id(hit),
                    "isin": hit.get("isin", ""),
                    "currency": hit.get("currency", ""),
                }
                for hit in hits
            ]

        if tool == "avanza_stoploss_set":
            confirmed = bool(arguments.get("confirm", False))
            self.require_mcp_write(confirmed)
            trigger, order_event, preview = build_stop_loss_preview(arguments)
            if not confirmed:
                return {"dry_run": True, "summary": format_stop_loss_request(preview), "request": preview}
            result = avanza.place_stop_loss_order(
                parent_stop_loss_id=preview["parent_stop_loss_id"],
                account_id=preview["account_id"],
                order_book_id=preview["order_book_id"],
                stop_loss_trigger=trigger,
                stop_loss_order_event=order_event,
            )
            self.record_event("trading", "live_stoploss_set", {"request": preview, "result": result})
            return {"dry_run": False, "request": preview, "result": result}

        if tool == "avanza_order_set":
            confirmed = bool(arguments.get("confirm", False))
            self.require_mcp_write(confirmed)
            order_type, condition, preview = build_order_preview(arguments)
            if not confirmed:
                return {"dry_run": True, "summary": format_order_request(preview), "request": preview}
            result = avanza.place_order(
                account_id=preview["account_id"],
                order_book_id=preview["order_book_id"],
                order_type=order_type,
                price=preview["price"],
                valid_until=date.fromisoformat(preview["valid_until"]),
                volume=preview["volume"],
                condition=condition,
            )
            self.record_event("trading", "live_order_set", {"request": preview, "result": result})
            return {"dry_run": False, "request": preview, "result": result}

        if tool in {"avanza_order_edit", "avanza_open_order_edit"}:
            confirmed = bool(arguments.get("confirm", False))
            self.require_mcp_write(confirmed)
            valid_until = arguments.get("valid_until")
            if isinstance(valid_until, str):
                valid_until = date.fromisoformat(valid_until)
            if not isinstance(valid_until, date):
                raise ValueError("valid_until must be an ISO date string.")
            valid_until = validate_valid_until(valid_until, "valid_until")
            request = {
                "account_id": str(arguments["account_id"]),
                "order_id": str(arguments["order_id"]),
                "price": float(arguments["price"]),
                "valid_until": valid_until.isoformat(),
                "volume": int(arguments["volume"]),
            }
            if not confirmed:
                return {"dry_run": True, "request": request}
            result = avanza.edit_order(
                order_id=request["order_id"],
                account_id=request["account_id"],
                price=request["price"],
                valid_until=valid_until,
                volume=request["volume"],
            )
            self.record_event("trading", "live_order_edit", {"request": request, "result": result})
            return {"dry_run": False, "request": request, "result": result}

        if tool == "avanza_paper_stoploss_set":
            paper_order = create_paper_stop_loss_order(arguments, instrument=str(arguments.get("instrument", "")))
            self.paper_session.setdefault("orders", []).append(paper_order)
            append_paper_event(self.paper_session, "paper_stoploss_set", {"id": paper_order["id"], "request": paper_order["request"]})
            self.save_paper_state()
            self.record_event("trading", "paper_stoploss_set", {"order": paper_order})
            return {"paper": True, "order": paper_order}

        if tool == "avanza_paper_order_set":
            paper_order = create_paper_order(arguments, instrument=str(arguments.get("instrument", "")))
            self.paper_session.setdefault("orders", []).append(paper_order)
            append_paper_event(self.paper_session, "paper_order_set", {"id": paper_order["id"], "request": paper_order["request"]})
            self.save_paper_state()
            self.record_event("trading", "paper_order_set", {"order": paper_order})
            return {"paper": True, "order": paper_order}

        if tool == "avanza_paper_orders":
            requested_account_id = str(arguments.get("account_id") or self.selected_account_id or "")
            active_only = bool(arguments.get("active_only", False))
            return {
                "paper": True,
                "account_id": requested_account_id or None,
                "orders": paper_orders(self.paper_session, requested_account_id or None, active_only),
                "events": self.paper_session.get("events", []),
            }

        if tool == "avanza_paper_cancel":
            paper_order = cancel_paper_order(self.paper_session, str(arguments["paper_order_id"]))
            self.save_paper_state()
            self.record_event("trading", "paper_order_cancel", {"order": paper_order})
            return {"paper": True, "order": paper_order}

        if tool in {"avanza_order_delete", "avanza_open_order_cancel"}:
            confirmed = bool(arguments.get("confirm", False))
            self.require_mcp_write(confirmed)
            request = {
                "account_id": str(arguments["account_id"]),
                "order_id": str(arguments["order_id"]),
            }
            if not confirmed:
                return {"dry_run": True, "request": request}
            result = avanza.delete_order(request["account_id"], request["order_id"])
            self.record_event("trading", "live_order_delete", {"request": request, "result": result})
            return {"dry_run": False, "request": request, "result": result}

        if tool == "avanza_stoploss_delete":
            confirmed = bool(arguments.get("confirm", False))
            self.require_mcp_write(confirmed)
            request = {
                "account_id": str(arguments["account_id"]),
                "stop_loss_id": str(arguments["stop_loss_id"]),
            }
            if not confirmed:
                return {"dry_run": True, "request": request}
            result = avanza.delete_stop_loss_order(request["account_id"], request["stop_loss_id"])
            self.record_event("trading", "live_stoploss_delete", {"request": request, "result": result})
            return {
                "dry_run": False,
                "request": request,
                "result": result,
            }

        if tool in {"avanza_stoploss_replace", "avanza_stoploss_edit"}:
            confirmed = bool(arguments.get("confirm", False))
            self.require_mcp_write(confirmed)
            stop_loss_id = str(arguments["stop_loss_id"])
            trigger, order_event, preview = build_stop_loss_preview(arguments)
            deprecated_alias = tool == "avanza_stoploss_replace"
            request = {
                "stop_loss_id": stop_loss_id,
                "replacement": preview,
            }
            if not confirmed:
                payload = {"dry_run": True, "summary": format_stop_loss_request(preview), "request": request}
                if deprecated_alias:
                    payload["warning"] = "avanza_stoploss_replace is deprecated; use avanza_stoploss_edit."
                return payload
            delete_result = avanza.delete_stop_loss_order(preview["account_id"], stop_loss_id)
            place_result = avanza.place_stop_loss_order(
                parent_stop_loss_id=preview["parent_stop_loss_id"],
                account_id=preview["account_id"],
                order_book_id=preview["order_book_id"],
                stop_loss_trigger=trigger,
                stop_loss_order_event=order_event,
            )
            result = {"delete": delete_result, "place": place_result}
            self.record_event(
                "trading",
                "live_stoploss_edit",
                {"request": request, "result": result, "used_deprecated_alias": deprecated_alias},
            )
            payload = {"dry_run": False, "request": request, "result": result}
            if deprecated_alias:
                payload["warning"] = "avanza_stoploss_replace is deprecated; use avanza_stoploss_edit."
            return payload

        raise ValueError(f"Unknown MCP tool: {tool}")

    def quote_payload_for_order_book(self, order_book_id: str, refresh: bool = True) -> dict[str, Any] | None:
        if not order_book_id:
            return None
        cached = self.quote_payload_by_order_book.get(order_book_id)
        cached_at = self.quote_payload_checked_at.get(order_book_id)
        if (
            not refresh
            and cached is not None
            and cached_at is not None
            and datetime.now() - cached_at < timedelta(seconds=QUOTE_CACHE_SECONDS)
        ):
            return cached
        avanza = self.require_connection()
        try:
            payload = avanza.get_market_data(order_book_id)
        except Exception:
            return None
        if isinstance(payload, dict):
            self.quote_payload_by_order_book[order_book_id] = payload
            self.quote_payload_checked_at[order_book_id] = datetime.now()
            return payload
        if hasattr(payload, "model_dump"):
            try:
                dumped = payload.model_dump()
            except Exception:
                dumped = None
            if isinstance(dumped, dict):
                self.quote_payload_by_order_book[order_book_id] = dumped
                self.quote_payload_checked_at[order_book_id] = datetime.now()
                return dumped
        if hasattr(payload, "dict"):
            try:
                dumped = payload.dict()
            except Exception:
                dumped = None
            if isinstance(dumped, dict):
                self.quote_payload_by_order_book[order_book_id] = dumped
                self.quote_payload_checked_at[order_book_id] = datetime.now()
                return dumped
        return None

    def realtime_quotes_snapshot(self, account_id: str) -> list[dict[str, Any]]:
        avanza = self.require_connection()
        positions_data = self.latest_portfolio_data
        if not isinstance(positions_data, dict):
            positions_data = avanza.get_accounts_positions()
            if isinstance(positions_data, dict):
                self.latest_portfolio_data = positions_data
        if not isinstance(positions_data, dict):
            return []

        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for section in ("withOrderbook", "withoutOrderbook"):
            for item in positions_data.get(section, []):
                if not isinstance(item, dict) or not matches_account(item, account_id or None):
                    continue
                order_book_id = position_order_book_id(item)
                if not order_book_id or order_book_id in seen:
                    continue
                seen.add(order_book_id)
                quote_payload = self.quote_payload_for_order_book(order_book_id) or {}
                rows.append(
                    {
                        "stock": str(nested_value(item, "instrument", "name")),
                        "order_book_id": order_book_id,
                        "last": market_quote_last(quote_payload),
                        "change_percent": market_quote_change_percent(quote_payload),
                        "realtime_status": self.realtime_status_for_position(item, quote_payload),
                        "updated_at": datetime.now().isoformat(timespec="seconds"),
                    }
                )
        return rows

    def realtime_status_for_position(
        self,
        item: dict[str, Any],
        quote_payload: dict[str, Any] | None = None,
        allow_lookup: bool = True,
    ) -> str:
        direct_status = first_known_realtime_status(item, quote_payload or {})
        order_book_id = position_order_book_id(item)
        if not order_book_id:
            return direct_status

        if direct_status != "Unknown":
            self.realtime_status_by_order_book[order_book_id] = direct_status
            self.realtime_status_checked_at[order_book_id] = datetime.now()
            return direct_status

        checked_at = self.realtime_status_checked_at.get(order_book_id)
        cached_status = self.realtime_status_by_order_book.get(order_book_id)
        if cached_status and checked_at and datetime.now() - checked_at < timedelta(seconds=REALTIME_STATUS_REFRESH_SECONDS):
            return cached_status

        if not allow_lookup:
            return cached_status or "Unknown"

        try:
            status = lookup_realtime_status(self.require_connection(), item)
        except Exception:
            status = "Unknown"
        self.realtime_status_by_order_book[order_book_id] = status
        self.realtime_status_checked_at[order_book_id] = datetime.now()
        return status

    def prefetch_quote_and_status_by_order_book(
        self,
        data: dict[str, Any],
        account_id: str | None,
        allow_status_lookup: bool = False,
    ) -> tuple[dict[str, dict[str, Any] | None], dict[str, str]]:
        quote_payloads: dict[str, dict[str, Any] | None] = {}
        realtime_statuses: dict[str, str] = {}
        seen: set[str] = set()
        for section in ("withOrderbook", "withoutOrderbook"):
            for item in data.get(section, []):
                if not isinstance(item, dict) or not matches_account(item, account_id):
                    continue
                order_book_id = position_order_book_id(item)
                if not order_book_id or order_book_id in seen:
                    continue
                seen.add(order_book_id)
                quote_payload = self.quote_payload_for_order_book(order_book_id, refresh=True)
                quote_payloads[order_book_id] = quote_payload
                realtime_statuses[order_book_id] = self.realtime_status_for_position(
                    item,
                    quote_payload,
                    allow_lookup=allow_status_lookup,
                )
        return quote_payloads, realtime_statuses

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
            valid_days=self.input_int_value("order-valid-days", "Order valid days"),
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
        self.query_one("#order-valid-days", Input).value = str(order.get("validDays", STOPLOSS_ORDER_VALID_DAYS_DEFAULT) or STOPLOSS_ORDER_VALID_DAYS_DEFAULT)
        self.query_one("#trigger-on-market-maker-quote", Switch).value = bool(trigger.get("triggerOnMarketMakerQuote", False))
        self.query_one("#short-selling-allowed", Switch).value = bool(order.get("shortSellingAllowed", False))
        self.query_one("#place-confirm", Input).value = ""
        self.query_one("#place-live", Button).label = "Update Paper Stop-Loss" if self.paper_mode_enabled else "Update Live Stop-Loss"
        self.query_one("#stoploss-modal").display = True
        self.write_log(f"Editing stop-loss for {orderbook.get('name', order_book_id)}.")

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

    def _refresh_selected_account_live_worker(self) -> None:
        started = time.perf_counter()
        if not self.avanza or not self.selected_account_id:
            self.call_from_thread(self._finish_live_refresh_cycle)
            return

        try:
            avanza = self.require_connection()
            selected_account_id = self.selected_account_id
            data = avanza.get_accounts_positions()
            if not isinstance(data, dict):
                raise RuntimeError(f"Unexpected portfolio response type: {type(data).__name__}")
            quote_payloads, realtime_statuses = self.prefetch_quote_and_status_by_order_book(
                data,
                selected_account_id,
                allow_status_lookup=False,
            )
            stoplosses = avanza.get_all_stop_losses()
            try:
                orders = avanza.get_orders()
            except Exception:
                orders = []
            elapsed = time.perf_counter() - started
            self.call_from_thread(
                self._apply_live_refresh_payload,
                data,
                quote_payloads,
                realtime_statuses,
                stoplosses,
                orders,
                elapsed,
            )
        except Exception as exc:
            self.call_from_thread(self.write_log, f"[red]Live refresh failed:[/red] {exc}")
            self.call_from_thread(self._finish_live_refresh_cycle)

    def _apply_live_refresh_payload(
        self,
        data: dict[str, Any],
        quote_payloads: dict[str, dict[str, Any] | None],
        realtime_statuses: dict[str, str],
        stoplosses: Any,
        orders: Any,
        elapsed: float,
    ) -> None:
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

    def _finish_live_refresh_cycle(self) -> None:
        with self.live_refresh_lock:
            had_pending = self.live_refresh_pending
            self.live_refresh_inflight = False
            self.live_refresh_pending = False
        if had_pending:
            self.refresh_selected_account_live()

    def refresh_selected_account_live(self) -> None:
        if not self.avanza or not self.selected_account_id:
            return
        with self.live_refresh_lock:
            if self.live_refresh_inflight:
                self.live_refresh_pending = True
                return
            self.live_refresh_inflight = True
        self.live_refresh_thread = threading.Thread(
            target=self._refresh_selected_account_live_worker,
            daemon=True,
            name="avanza-live-refresh",
        )
        self.live_refresh_thread.start()

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
        self.refresh_selected_account_live()

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
        elif event.select.id == "order-instrument-select" and event.value and event.value != Select.BLANK:
            volume_input = self.query_one("#regular-order-volume", Input)
            if self.input_value("regular-order-type") == "sell" and not volume_input.value.strip():
                volume_input.value = self.holding_volumes_by_order_book.get(str(event.value), "")
            self.update_regular_order_value()
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
                self.update_mcp_session_file()
                self.update_mode_toggles()
                mode = "read/write" if self.mcp_write_enabled else "read-only"
                self.write_mcp_log(f"MCP mode: {mode}.")
            elif button_id == "refresh-all":
                self.refresh_accounts()
                self.refresh_portfolio()
                self.refresh_stoplosses()
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
        )

    def start_login_worker(
        self,
        target: Callable[..., None],
        args: tuple[Any, ...],
        progress_messages: tuple[str, ...],
        initial_message: str,
    ) -> None:
        if self.login_busy:
            self.write_log("[yellow]Login already in progress...[/yellow]")
            return
        self.start_login_progress(progress_messages, initial_message)
        worker = threading.Thread(target=target, args=args, daemon=True, name="avanza-login-worker")
        self.login_thread = worker
        worker.start()

    def login_worker_with_credentials(self, credentials: dict[str, str]) -> None:
        self.perform_login(credentials, connect_stage_index=0)

    def login_worker_with_1password(self, item: str, vault: str | None) -> None:
        self.call_from_thread(self.set_login_stage, "Reading credentials from 1Password...", 1)
        credentials = onepassword_credentials(item, vault)
        self.perform_login(credentials, connect_stage_index=2)

    def perform_login(self, credentials: dict[str, str], connect_stage_index: int) -> None:
        try:
            self.call_from_thread(self.set_login_stage, "Connecting to Avanza...", connect_stage_index)
            avanza = Avanza(credentials)

            self.call_from_thread(self.set_login_stage, "Loading account overview...", connect_stage_index + 1)
            overview = avanza.get_overview()
            if not isinstance(overview, dict):
                raise RuntimeError(f"Unexpected account overview response type: {type(overview).__name__}")

            self.call_from_thread(self.set_login_stage, "Loading portfolio...", connect_stage_index + 2)
            portfolio = avanza.get_accounts_positions()
            if not isinstance(portfolio, dict):
                raise RuntimeError(f"Unexpected portfolio response type: {type(portfolio).__name__}")

            self.call_from_thread(self.set_login_stage, "Loading stop-losses and open orders...", connect_stage_index + 3)
            stoplosses = avanza.get_all_stop_losses()
            try:
                orders = avanza.get_orders()
            except Exception:
                orders = []

            self.call_from_thread(self.set_login_stage, "Building workspace...", connect_stage_index + 4)
            self.call_from_thread(self.complete_login, avanza, overview, portfolio, stoplosses, orders)
            self.call_from_thread(self.stop_login_progress)
        except Exception as exc:
            self.call_from_thread(self.stop_login_progress)
            self.call_from_thread(self.write_log, f"[red]Login failed:[/red] {exc}")

    def handle_dry_run(self) -> None:
        _, _, preview = self.build_stop_loss_request()
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


def load_mcp_session(path: Path | None = None) -> dict[str, Any]:
    path = path or MCP_SESSION_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"MCP session file not found: {path}. Enable MCP mode in the TUI first.") from exc
    if not isinstance(data, dict) or not data.get("url") or not data.get("token"):
        raise RuntimeError(f"Invalid MCP session file: {path}")
    return data


def call_mcp_bridge(session: dict[str, Any], tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    url = str(session["url"]).rstrip("/") + "/call"
    body = json.dumps({"tool": tool, "arguments": arguments}).encode("utf-8")
    request = Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {session['token']}",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body or "{}")
        except json.JSONDecodeError:
            payload = {"error": body or f"HTTP {exc.code}"}
        payload.setdefault("ok", False)
        payload.setdefault("error", f"HTTP {exc.code}")
    except URLError as exc:
        raise RuntimeError(f"Could not reach TUI MCP bridge at {url}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("MCP bridge returned a non-object response.")
    return payload


def mcp_tool_response(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, indent=2, ensure_ascii=False, default=str),
            }
        ],
        "isError": not bool(payload.get("ok", True)),
    }


def read_mcp_message(stream: Any) -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = stream.readline()
        if line == b"":
            return None
        if line in (b"\r\n", b"\n"):
            break
        key, _, value = line.decode("utf-8").partition(":")
        headers[key.lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    return json.loads(stream.read(length).decode("utf-8"))


def write_mcp_message(stream: Any, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    stream.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
    stream.flush()


def mcp_success(message_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def mcp_error(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def run_mcp_stdio_proxy(session_file: Path | None = None) -> None:
    input_stream = sys.stdin.buffer
    output_stream = sys.stdout.buffer

    while True:
        message = read_mcp_message(input_stream)
        if message is None:
            return
        method = message.get("method")
        message_id = message.get("id")
        params = message.get("params") or {}
        if message_id is None and str(method).startswith("notifications/"):
            continue

        try:
            if method == "initialize":
                write_mcp_message(
                    output_stream,
                    mcp_success(
                        message_id,
                        {
                            "protocolVersion": MCP_PROTOCOL_VERSION,
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "avanza_cli", "version": "0.1.0"},
                        },
                    ),
                )
            elif method == "notifications/initialized":
                continue
            elif method == "ping":
                write_mcp_message(output_stream, mcp_success(message_id, {}))
            elif method == "tools/list":
                write_mcp_message(output_stream, mcp_success(message_id, {"tools": MCP_TOOLS}))
            elif method == "tools/call":
                tool_name = str(params.get("name", ""))
                arguments = params.get("arguments") or {}
                if not isinstance(arguments, dict):
                    raise ValueError("arguments must be an object.")
                session = load_mcp_session(session_file)
                payload = call_mcp_bridge(session, tool_name, arguments)
                write_mcp_message(output_stream, mcp_success(message_id, mcp_tool_response(payload)))
            else:
                write_mcp_message(output_stream, mcp_error(message_id, -32601, f"Unknown method: {method}"))
        except Exception as exc:
            write_mcp_message(output_stream, mcp_error(message_id, -32000, str(exc)))


def cmd_tui(args: argparse.Namespace) -> None:
    AvanzaTradingTui(
        debug=bool(getattr(args, "debug", False)),
        debug_profile_top=int(getattr(args, "debug_profile_top", DEBUG_PROFILE_TOP_DEFAULT)),
    ).run()


def cmd_mcp(args: argparse.Namespace) -> None:
    run_mcp_stdio_proxy(Path(args.session_file))


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


def cmd_stoploss_edit(args: argparse.Namespace) -> None:
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
        "stop_loss_id": args.stop_loss_id,
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
        render_message(
            "Dry Run: add --confirm to update this stop-loss (delete + place replacement).",
            [
                f"Existing stop-loss ID: {args.stop_loss_id}",
                *format_stop_loss_request(request_preview),
            ],
        )
        return

    avanza = connect(args)
    delete_result = avanza.delete_stop_loss_order(args.account_id, args.stop_loss_id)
    place_result = avanza.place_stop_loss_order(
        parent_stop_loss_id=args.parent_stop_loss_id,
        account_id=args.account_id,
        order_book_id=args.order_book_id,
        stop_loss_trigger=trigger,
        stop_loss_order_event=order_event,
    )
    render_result(
        "Update Stop-Loss Result",
        {"updated": True, "deleted": delete_result, "placed": place_result},
    )


def cmd_orders_list(args: argparse.Namespace) -> None:
    avanza = connect(args)
    render_orders(avanza.get_orders())


def cmd_transactions_list(args: argparse.Namespace) -> None:
    avanza = connect(args)
    if args.max_elements < 1:
        raise ValueError("--max-elements must be >= 1.")
    transaction_types = parse_transaction_types(args.types)
    transactions_from = None if args.all else args.transactions_from
    transactions_to = None if args.all else args.transactions_to
    if transactions_from and transactions_to and transactions_from > transactions_to:
        raise ValueError("--from cannot be after --to.")
    payload = avanza.get_transactions_details(
        transaction_details_types=transaction_types,
        transactions_from=transactions_from,
        transactions_to=transactions_to,
        isin=args.isin,
        max_elements=args.max_elements,
    )
    render_transactions_history(
        payload,
        account_id=args.account_id,
        executed_only=not args.include_non_executed,
    )


def cmd_order_delete(args: argparse.Namespace) -> None:
    if not args.confirm:
        render_message(
            "Dry Run",
            [
                "Add --confirm to delete this regular order.",
                f"Account: {args.account_id}",
                f"Order ID: {args.order_id}",
            ],
        )
        return

    avanza = connect(args)
    result = avanza.delete_order(args.account_id, args.order_id)
    render_result("Delete Order Result", {"deleted": True, "result": result})


def cmd_order_set(args: argparse.Namespace) -> None:
    order_type, condition, preview = build_order_preview(
        {
            "account_id": args.account_id,
            "order_book_id": args.order_book_id,
            "order_type": args.order_type,
            "price": args.price,
            "valid_until": args.valid_until,
            "volume": args.volume,
            "condition": args.condition,
        }
    )

    if not args.confirm:
        render_order_request(
            "Dry Run: add --confirm to place this buy/sell order.",
            preview,
        )
        return

    avanza = connect(args)
    result = avanza.place_order(
        account_id=args.account_id,
        order_book_id=args.order_book_id,
        order_type=order_type,
        price=args.price,
        valid_until=args.valid_until,
        volume=args.volume,
        condition=condition,
    )
    render_result("Place Order Result", result)


def add_common_auth(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--username",
        metavar="USER",
        help="Avanza username. If omitted, you are prompted interactively.",
    )
    parser.add_argument(
        "--onepassword-item",
        metavar="ITEM",
        help="Read Avanza username, password, and TOTP from a 1Password item via the op CLI.",
    )
    parser.add_argument(
        "--onepassword-vault",
        metavar="VAULT",
        help="Optional 1Password vault name or ID for --onepassword-item.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="avanza_cli.py",
        formatter_class=HELP_FORMATTER,
        description="Human-readable Avanza account, portfolio, search, order, and stop-loss tools.",
        epilog=textwrap.dedent(
            """\
            Common examples:
              python avanza_cli.py tui
              python avanza_cli.py accounts
              python avanza_cli.py portfolio summary
              python avanza_cli.py portfolio positions
              python avanza_cli.py search-stock "VOLV B"
              python avanza_cli.py transactions list
              python avanza_cli.py orders list
              python avanza_cli.py stoploss list

            Credentials:
              Password and current TOTP code are prompted interactively and masked.
              Or use --onepassword-item ITEM with the 1Password CLI.

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
    tui.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug profiling mode. Writes timing/profile logs under avanza-cli/logs/.",
    )
    tui.add_argument(
        "--debug-profile-top",
        metavar="N",
        type=int,
        default=DEBUG_PROFILE_TOP_DEFAULT,
        help=f"How many top functions to include per profile sample in --debug mode. Default: {DEBUG_PROFILE_TOP_DEFAULT}.",
    )
    tui.set_defaults(func=cmd_tui)

    mcp = subparsers.add_parser(
        "mcp",
        formatter_class=HELP_FORMATTER,
        help="Run the stdio MCP proxy for a TUI-managed authenticated session.",
        description=textwrap.dedent(
            """\
            Run a stdio MCP server proxy that forwards tool calls to the currently
            authenticated TUI MCP bridge. Start `python avanza_cli.py tui`, log in,
            enable MCP mode in the TUI, then configure Codex/desktop clients to run
            this command.
            """
        ),
        epilog=textwrap.dedent(
            """\
            Example MCP server command:
              python avanza_cli.py mcp
            """
        ),
    )
    mcp.add_argument(
        "--session-file",
        default=str(MCP_SESSION_FILE),
        help="Path to the TUI-written MCP session file. Default: .avanza_mcp_session.json next to avanza_cli.py.",
    )
    mcp.set_defaults(func=cmd_mcp)

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
        description="Show all portfolio stock positions and cash balances in tables.",
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

    transactions = subparsers.add_parser(
        "transactions",
        formatter_class=HELP_FORMATTER,
        help="View transaction history / executed orders.",
        description="List transaction history. Defaults to executed orders (BUY/SELL).",
        epilog=textwrap.dedent(
            """\
            Examples:
              python avanza_cli.py transactions list
              python avanza_cli.py transactions list --account-id ACCOUNT_ID --max-elements 5000
              python avanza_cli.py transactions list --all
              python avanza_cli.py transactions list --types BUY,SELL,DIVIDEND --from 2026-01-01 --to 2026-05-01
            """
        ),
    )
    transactions_subparsers = transactions.add_subparsers(dest="transactions_command", required=True)

    transactions_list = transactions_subparsers.add_parser(
        "list",
        formatter_class=HELP_FORMATTER,
        help="List transaction history entries.",
        description="List transaction history entries with account/date/type filters.",
    )
    add_common_auth(transactions_list)
    transactions_list.add_argument("--account-id", metavar="ID", help="Optional Avanza account id filter.")
    transactions_list.add_argument(
        "--from",
        dest="transactions_from",
        metavar="YYYY-MM-DD",
        type=parse_date,
        help="Start date filter (inclusive).",
    )
    transactions_list.add_argument(
        "--to",
        dest="transactions_to",
        metavar="YYYY-MM-DD",
        type=parse_date,
        help="End date filter (inclusive).",
    )
    transactions_list.add_argument(
        "--types",
        metavar="CSV",
        default="BUY,SELL",
        help="Comma-separated transaction types. Default: BUY,SELL.",
    )
    transactions_list.add_argument("--isin", metavar="ISIN", help="Optional ISIN filter.")
    transactions_list.add_argument(
        "--max-elements",
        metavar="N",
        type=int,
        default=1000,
        help="Maximum number of transactions to request. Default: 1000.",
    )
    transactions_list.add_argument(
        "--include-non-executed",
        action="store_true",
        help="Include non-executed types (deposits/dividends/withdrawals) in output.",
    )
    transactions_list.add_argument(
        "--all",
        action="store_true",
        help="Request practically all available history by removing date filters.",
    )
    transactions_list.set_defaults(func=cmd_transactions_list)

    orders = subparsers.add_parser(
        "orders",
        formatter_class=HELP_FORMATTER,
        help="List, create, and delete regular buy/sell orders.",
        description="Manage regular Avanza buy/sell orders. Placement and deletion dry-run unless --confirm is passed.",
        epilog=textwrap.dedent(
            """\
            Examples:
              python avanza_cli.py orders list
              python avanza_cli.py orders set --help
              python avanza_cli.py orders delete --help
            """
        ),
    )
    orders_subparsers = orders.add_subparsers(dest="orders_command", required=True)

    orders_list = orders_subparsers.add_parser(
        "list",
        formatter_class=HELP_FORMATTER,
        help="List open regular orders.",
        description="List open regular buy/sell orders in a readable table.",
        epilog="Example:\n  python avanza_cli.py orders list",
    )
    add_common_auth(orders_list)
    orders_list.set_defaults(func=cmd_orders_list)

    orders_delete = orders_subparsers.add_parser(
        "delete",
        formatter_class=HELP_FORMATTER,
        help="Delete a regular order.",
        description="Delete a regular order. Without --confirm this only prints the intended deletion.",
        epilog=textwrap.dedent(
            """\
            Dry-run:
              python avanza_cli.py orders delete --account-id ACCOUNT_ID --order-id ORDER_ID

            Live deletion:
              python avanza_cli.py orders delete --account-id ACCOUNT_ID --order-id ORDER_ID --confirm
            """
        ),
    )
    add_common_auth(orders_delete)
    orders_delete.add_argument("--account-id", metavar="ID", required=True, help="Avanza account id that owns the order.")
    orders_delete.add_argument("--order-id", metavar="ID", required=True, help="Order id to delete.")
    orders_delete.add_argument("--confirm", action="store_true", help="Actually delete the order. Omit for dry-run.")
    orders_delete.set_defaults(func=cmd_order_delete)

    orders_set = orders_subparsers.add_parser(
        "set",
        formatter_class=HELP_FORMATTER,
        help="Create a regular buy/sell order.",
        description=textwrap.dedent(
            """\
            Create a regular buy/sell order.

            Without --confirm, this command prints a readable dry-run summary and does not log in.

            Conditions:
              normal         normal limit order
              fill-or-kill   fill entire order immediately or cancel
              fill-and-kill  fill available volume immediately and cancel remainder
            """
        ),
        epilog=textwrap.dedent(
            """\
            Buy order dry-run:
              python avanza_cli.py orders set \\
                --account-id ACCOUNT_ID \\
                --order-book-id ORDER_BOOK_ID \\
                --order-type buy \\
                --price 100 \\
                --valid-until 2026-05-28 \\
                --volume 10 \\
                --condition normal

            Add --confirm only after reviewing the dry-run summary.
            """
        ),
    )
    add_common_auth(orders_set)
    orders_set.add_argument("--account-id", metavar="ID", required=True, help="Avanza account id to place the order on.")
    orders_set.add_argument("--order-book-id", metavar="ID", required=True, help="Avanza order book id for the instrument.")
    orders_set.add_argument("--order-type", choices=ORDER_TYPE_CHOICES, default="buy", help="Order side. Default: buy.")
    orders_set.add_argument("--price", metavar="SEK", required=True, type=float, help="Limit price in SEK.")
    orders_set.add_argument("--valid-until", metavar="YYYY-MM-DD", required=True, type=parse_date, help="Last date the order remains valid.")
    orders_set.add_argument("--volume", metavar="QTY", required=True, type=int, help="Number of shares/contracts to order.")
    orders_set.add_argument("--condition", choices=ORDER_CONDITION_CHOICES, default="normal", help="Order condition. Default: normal.")
    orders_set.add_argument("--confirm", action="store_true", help="Actually place the order. Omit for dry-run.")
    orders_set.set_defaults(func=cmd_order_set)

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
              python avanza_cli.py stoploss edit --help
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

            If --valid-until is omitted, avanza_cli auto-sets it to the longest allowed date.
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
    stoploss_set.add_argument(
        "--valid-until",
        metavar="YYYY-MM-DD",
        default=max_valid_until_date().isoformat(),
        type=parse_date,
        help=f"Last date the trigger remains valid. Default: max allowed ({VALID_UNTIL_MAX_DAYS} days from today).",
    )
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
    stoploss_set.add_argument(
        "--order-valid-days",
        metavar="DAYS",
        default=STOPLOSS_ORDER_VALID_DAYS_DEFAULT,
        type=int,
        help=f"Triggered order validity in days. Default: {STOPLOSS_ORDER_VALID_DAYS_DEFAULT}.",
    )
    stoploss_set.add_argument("--short-selling-allowed", action="store_true", help="Allow short selling for the triggered order.")
    stoploss_set.add_argument("--confirm", action="store_true", help="Actually place the stop-loss. Omit for dry-run.")
    stoploss_set.set_defaults(func=cmd_stoploss_set)

    stoploss_edit = stoploss_subparsers.add_parser(
        "edit",
        formatter_class=HELP_FORMATTER,
        help="Update an existing stop-loss (replace workflow).",
        description=textwrap.dedent(
            """\
            Update an existing stop-loss by deleting the old one and placing a replacement.

            This command uses the same trigger/order fields as `stoploss set`, plus --stop-loss-id.
            Without --confirm, it prints a dry-run summary.
            """
        ),
    )
    add_common_auth(stoploss_edit)
    stoploss_edit.add_argument("--stop-loss-id", metavar="ID", required=True, help="Existing stop-loss id to update.")
    stoploss_edit.add_argument("--account-id", metavar="ID", required=True, help="Avanza account id that owns the stop-loss.")
    stoploss_edit.add_argument("--order-book-id", metavar="ID", required=True, help="Avanza order book id for the instrument.")
    stoploss_edit.add_argument("--parent-stop-loss-id", metavar="ID", default="0", help="Parent stop-loss id. Default: 0.")
    stoploss_edit.add_argument("--trigger-type", choices=TRIGGER_TYPE_CHOICES, required=True, help="Stop-loss trigger behavior.")
    stoploss_edit.add_argument("--trigger-value", metavar="VALUE", required=True, type=float, help="Trigger value, interpreted with --trigger-value-type.")
    stoploss_edit.add_argument(
        "--trigger-value-type",
        metavar="{SEK,%}",
        type=parse_price_type,
        default="monetary",
        help="How to interpret --trigger-value. Use SEK or %%. Default: SEK.",
    )
    stoploss_edit.add_argument(
        "--valid-until",
        metavar="YYYY-MM-DD",
        default=max_valid_until_date().isoformat(),
        type=parse_date,
        help=f"Last date the trigger remains valid. Default: max allowed ({VALID_UNTIL_MAX_DAYS} days from today).",
    )
    stoploss_edit.add_argument("--trigger-on-market-maker-quote", action="store_true", help="Allow market-maker quote to trigger the stop-loss.")
    stoploss_edit.add_argument("--order-type", choices=ORDER_TYPE_CHOICES, default="sell", help="Order side after trigger. Default: sell.")
    stoploss_edit.add_argument("--order-price", metavar="VALUE", required=True, type=float, help="Order price or offset, interpreted with --order-price-type.")
    stoploss_edit.add_argument(
        "--order-price-type",
        metavar="{SEK,%}",
        type=parse_price_type,
        default="monetary",
        help="How to interpret --order-price. Use SEK or %%. Default: SEK.",
    )
    stoploss_edit.add_argument("--volume", metavar="QTY", required=True, type=float, help="Number of shares/contracts to include in the triggered order.")
    stoploss_edit.add_argument(
        "--order-valid-days",
        metavar="DAYS",
        default=STOPLOSS_ORDER_VALID_DAYS_DEFAULT,
        type=int,
        help=f"Triggered order validity in days. Default: {STOPLOSS_ORDER_VALID_DAYS_DEFAULT}.",
    )
    stoploss_edit.add_argument("--short-selling-allowed", action="store_true", help="Allow short selling for the triggered order.")
    stoploss_edit.add_argument("--confirm", action="store_true", help="Actually update the stop-loss (delete + place replacement).")
    stoploss_edit.set_defaults(func=cmd_stoploss_edit)

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
