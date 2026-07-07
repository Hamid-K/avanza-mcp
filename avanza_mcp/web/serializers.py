"""JSON serializers for the Web UI.

WS-push payloads are built strictly from kernel caches (no network calls, so
they are safe to build from any thread); REST endpoints may pass refresh
flags through to the kernel snapshot methods which own the caching rules.
"""

from typing import Any

from avanza_mcp.config import PROFIT_METRIC_MODES
from avanza_mcp.records import position_mcp_dict, stop_loss_mcp_dict
from avanza_mcp.rendering import (
    account_performance_window_summary,
    account_profit_summary_from_avanza,
    amount,
    market_clock_text,
    open_order_mcp_dict,
    portfolio_day_summary,
    profit_metric_label,
    realtime_status,
)


def account_summary(account: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(account, dict):
        return {}
    return {
        "id": str(account.get("id", "")),
        "name": str(account.get("name", "")),
        "type": str(account.get("type", "") or account.get("accountType", "")),
        "total_value": amount(account, "totalValue"),
        "buying_power": amount(account, "buyingPower"),
        "status": str(account.get("status", "")) or "-",
    }


def profit_metrics(
    account: dict[str, Any] | None,
    portfolio_data: dict[str, Any] | None,
    account_id: str | None,
) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}
    for mode in PROFIT_METRIC_MODES:
        profit_amount: float | None = None
        profit_percent: float | None = None
        unit = "SEK"
        if account and portfolio_data is not None:
            if mode == "total":
                profit_amount, profit_percent, unit = account_profit_summary_from_avanza(account)
            elif mode == "day":
                profit_amount, profit_percent, unit = account_performance_window_summary(account, mode)
                if profit_amount is None and profit_percent is None:
                    profit_amount, profit_percent, unit = portfolio_day_summary(portfolio_data, account_id, account)
            elif mode == "since_start":
                profit_amount, profit_percent, unit = account_performance_window_summary(account, mode)
                if profit_amount is None and profit_percent is None:
                    profit_amount, profit_percent, unit = account_profit_summary_from_avanza(account)
            else:
                profit_amount, profit_percent, unit = account_performance_window_summary(account, mode)
        metrics[mode] = {
            "label": profit_metric_label(mode),
            "amount": profit_amount,
            "percent": profit_percent,
            "unit": unit,
        }
    return metrics


def sessions_payload(kernel: Any) -> dict[str, Any]:
    sessions = []
    for session_id, context in kernel.tenant_sessions.items():
        sessions.append(
            {
                "session_id": session_id,
                "label": context.label,
                "color": context.color,
                "auth_valid": bool(context.auth_valid),
                "auth_error": str(context.auth_error or ""),
                "selected_account_id": context.selected_account_id,
                "accounts": [account_summary(a) for a in context.accounts],
            }
        )
    return {"sessions": sessions, "active_session_id": kernel.active_session_id}


def portfolio_payload(kernel: Any) -> dict[str, Any]:
    """Cache-only portfolio payload: rows, metrics, realtime status."""
    data = kernel.latest_portfolio_data
    account_id = kernel.selected_account_id
    rows: list[dict[str, Any]] = []
    if isinstance(data, dict):
        for section in ("withOrderbook", "withoutOrderbook"):
            for item in data.get(section, []):
                if not isinstance(item, dict):
                    continue
                item_account = str(
                    item.get("accountId", "")
                    or (item.get("account") or {}).get("id", "")
                )
                if account_id and item_account and item_account != account_id:
                    continue
                orderbook_id = str(((item.get("instrument") or {}).get("orderbook") or {}).get("id", ""))
                status = kernel.realtime_status_by_order_book.get(orderbook_id, "") or realtime_status(item) or "unknown"
                rows.append(position_mcp_dict(item, status))
    account = kernel.account_by_id(account_id) if account_id else None
    return {
        "account_id": account_id,
        "account": account_summary(account),
        "rows": rows,
        "metrics": profit_metrics(account, data if isinstance(data, dict) else None, account_id),
        "clock": market_clock_text(),
    }


def orders_payload(kernel: Any) -> dict[str, Any]:
    items = [open_order_mcp_dict(item) for item in kernel.latest_open_order_items if isinstance(item, dict)]
    return {"items": items}


def stoplosses_payload(kernel: Any) -> dict[str, Any]:
    live = [stop_loss_mcp_dict(item) for item in kernel.latest_stoploss_items if isinstance(item, dict)]
    paper_rows: list[dict[str, Any]] = []
    for item in kernel.paper_session.get("orders", []):
        if not isinstance(item, dict):
            continue
        state = str(item.get("state", "") or item.get("status", "")).lower()
        if state and state not in {"open", "active", "pending", "created"}:
            continue
        request = item.get("request") if isinstance(item.get("request"), dict) else {}
        paper_rows.append(
            {
                "mode": "Paper",
                "id": str(item.get("id", "")),
                "kind": str(item.get("kind", "Order")),
                "stock": str(item.get("instrument", "") or request.get("order_book_id", "")),
                "side": str(request.get("order_type", "") or request.get("side", "")).upper(),
                "volume": request.get("volume"),
                "trigger_or_price": request.get("trigger_value", request.get("price")),
                "valid_or_created": str(item.get("created_at", "") or request.get("valid_until", "")),
                "status": str(item.get("state", "") or item.get("status", "") or "open"),
            }
        )
    return {"items": live, "paper_items": paper_rows}


def mcp_status_web_payload(kernel: Any) -> dict[str, Any]:
    running = kernel.mcp_server is not None
    port = 0
    if running:
        try:
            port = int(kernel.mcp_server.server_address[1])
        except Exception:
            port = 0
    return {
        "running": running,
        "url": f"http://127.0.0.1:{port}" if running and port else "",
        "token": kernel.mcp_token or "",
        "read_write": bool(kernel.mcp_write_enabled),
        "live_trading": bool(kernel.live_trading_allowed_for_session),
        "paper_mode": bool(kernel.paper_mode_enabled),
        "proxy_command": "python avanza_cli.py mcp",
    }
