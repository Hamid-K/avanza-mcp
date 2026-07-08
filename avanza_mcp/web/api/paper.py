"""Paper-trading state and TradingView list endpoints."""

import asyncio
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from avanza_mcp.paper import paper_orders, paper_positions, paper_session_summary, paper_trades

router = APIRouter()


def _kernel(request: Request):
    return request.app.state.runtime.kernel


def _tv_first_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return ""


def _normalize_tv_row(row: dict[str, Any]) -> dict[str, Any]:
    symbol = _tv_first_value(row, "symbol", "name")
    exchange = _tv_first_value(row, "exchange")
    symbol_full = _tv_first_value(row, "symbol_full")
    if not symbol_full and exchange and symbol:
        symbol_full = f"{exchange}:{symbol}"
    return {
        **row,
        "symbol": symbol,
        "symbol_full": symbol_full,
        "name": _tv_first_value(row, "description", "name", "symbol"),
        "last": _tv_first_value(row, "last", "close", "premarket_close", "postmarket_close"),
        "change": _tv_first_value(row, "change_abs", "change"),
        "change_percent": _tv_first_value(row, "change_percent", "change"),
        "volume": _tv_first_value(row, "volume"),
        "market_state": _tv_first_value(row, "market_state", "market_status", "update_mode"),
        "source": _tv_first_value(row, "source") or "tradingview",
    }


def _normalize_tv_lists_payload(payload: Any, *, fallback_reason: str = "") -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    rows = payload.get("rows") or payload.get("items") or payload.get("symbols") or []
    normalized_rows = [_normalize_tv_row(row) for row in rows if isinstance(row, dict)]
    lists = payload.get("lists") or payload.get("watchlists") or []
    lists = [item for item in lists if isinstance(item, dict)]
    if not lists:
        lists = [
            {
                "id": "public-heatmap",
                "list_id": "public-heatmap",
                "name": "Public TradingView movers",
                "title": "Public TradingView movers",
                "count": len(normalized_rows),
            }
        ]
    selected = payload.get("selected_list") if isinstance(payload.get("selected_list"), dict) else None
    selected = selected or lists[0]
    result = {
        **payload,
        "lists": lists,
        "items": normalized_rows,
        "rows": normalized_rows,
        "selected_list": selected,
        "fallback": bool(fallback_reason),
        "fallback_reason": fallback_reason,
    }
    if fallback_reason:
        result["warning"] = f"Showing public TradingView scanner data because authenticated custom lists are unavailable: {fallback_reason}"
        result["mode"] = "public_scanner_fallback"
        result["source"] = payload.get("source") or "tradingview-scanner"
    return result


@router.get("/api/paper/state")
async def paper_state(request: Request):
    kernel = _kernel(request)

    def work():
        with kernel.state_lock:
            session = kernel.paper_session
            account_id = kernel.selected_account_id
            payload = {
                "paper_mode": kernel.paper_mode_enabled,
                "positions": paper_positions(session, account_id=None),
                "open_positions": paper_positions(session, account_id=None, active_only=True),
                "orders": paper_orders(session, account_id=None),
                "active_orders": paper_orders(session, account_id=None, active_only=True),
                "trades": paper_trades(session, account_id=None),
                "summary": paper_session_summary(session),
                "risk": None,
            }
            if account_id and kernel.avanza is not None:
                try:
                    payload["risk"] = kernel.execute_mcp_tool("avanza_paper_risk_state", {"account_id": account_id})
                except Exception:
                    payload["risk"] = None
            return payload

    return await asyncio.to_thread(work)


@router.get("/api/tv/lists")
async def tv_lists(request: Request, list_id: str = "", limit: int = 200):
    kernel = _kernel(request)

    def work():
        bounded_limit = max(1, min(int(limit), 200))
        arguments = {"limit": bounded_limit}
        if list_id and list_id != "public-heatmap":
            arguments["list_id"] = list_id
        try:
            return _normalize_tv_lists_payload(kernel.execute_mcp_tool("tv_auth_custom_lists", arguments))
        except Exception as custom_exc:
            fallback = kernel.execute_mcp_tool(
                "tv_scrape_heatmap",
                {
                    "limit": bounded_limit,
                    "sort_by": "change",
                    "include_premarket": True,
                    "exclude_otc": True,
                },
            )
            return _normalize_tv_lists_payload(fallback, fallback_reason=str(custom_exc))

    try:
        return await asyncio.to_thread(work)
    except PermissionError as exc:
        return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)
    except Exception as exc:
        return JSONResponse({"error": "tv_failed", "detail": str(exc)}, status_code=502)
