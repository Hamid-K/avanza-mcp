"""Generic utilities: logging, formatting of MCP log lines, thread helpers."""

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError

from avanza_mcp import config

def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def timestamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def create_session_log_path(kind: str) -> Path:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return config.LOG_DIR / f"session-{kind}-{stamp}.jsonl"


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



def run_blocking_in_thread(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    result_holder: dict[str, Any] = {}
    error_holder: dict[str, BaseException] = {}

    def target() -> None:
        try:
            result_holder["value"] = func(*args, **kwargs)
        except BaseException as exc:  # pragma: no cover - passthrough
            error_holder["error"] = exc

    thread = threading.Thread(target=target, daemon=True, name="avanza-blocking-worker")
    thread.start()
    thread.join()
    if "error" in error_holder:
        raise error_holder["error"]
    return result_holder.get("value")



def http_status_code_from_exception(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    if isinstance(exc, HTTPError):
        try:
            return int(exc.code)
        except Exception:
            return None
    message = str(exc).lower()
    if "401" in message and "unauthorized" in message:
        return 401
    if "403" in message and "forbidden" in message:
        return 403
    return None


def is_unauthorized_http_error(exc: Exception) -> bool:
    status_code = http_status_code_from_exception(exc)
    if status_code in {401, 403}:
        return True
    message = str(exc).lower()
    return "unauthorized" in message or "forbidden" in message


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


def nested_value(data: dict[str, Any], *path: str) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return ""
        current = current.get(key, "")
    return current


def value_number(data: dict[str, Any], *path: str) -> float | None:
    value = nested_value(data, *path)
    if isinstance(value, dict):
        value = value.get("value")
    if isinstance(value, (int, float)):
        return float(value)
    return None


def first_value_number(data: dict[str, Any], paths: tuple[tuple[str, ...], ...]) -> float | None:
    for path in paths:
        candidate = value_number(data, *path)
        if candidate is not None:
            return candidate
    return None


def first_unit_text(data: dict[str, Any], paths: tuple[tuple[str, ...], ...], default: str = "SEK") -> str:
    for path in paths:
        unit_value = nested_value(data, *path, "unit")
        if unit_value:
            return str(unit_value)
    return default
