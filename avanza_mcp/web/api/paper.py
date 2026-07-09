"""Paper-trading state and TradingView list endpoints."""

import asyncio
import os
import re
import threading
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from avanza_mcp.paper import paper_orders, paper_positions, paper_session_summary, paper_trades

router = APIRouter()

ZACKS_TV_LIST_LIMIT_DEFAULT = int(os.getenv("AVANZA_WEB_TV_LIST_ZACKS_LIMIT", "12"))
ZACKS_TV_LIST_LIMIT_MAX = 50
ZACKS_TV_LIST_CACHE_SECONDS = float(os.getenv("AVANZA_WEB_TV_LIST_ZACKS_CACHE_SECONDS", "3600"))
ZACKS_TV_LIST_ERROR_CACHE_SECONDS = float(os.getenv("AVANZA_WEB_TV_LIST_ZACKS_ERROR_CACHE_SECONDS", "600"))
ZACKS_US_EXCHANGES = {"NASDAQ", "NYSE", "AMEX", "NYSEARCA", "NYSEAMERICAN"}
_ZACKS_TV_LIST_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_ZACKS_TV_LIST_CACHE_LOCK = threading.RLock()


def _kernel(request: Request):
    return request.app.state.runtime.kernel


def _tv_first_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return ""


def _short_text(value: Any, max_chars: int = 220) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "…"


def _zacks_symbol_from_tv_row(row: dict[str, Any]) -> str:
    exchange = str(_tv_first_value(row, "exchange", "market") or "").strip().upper()
    if exchange and exchange not in ZACKS_US_EXCHANGES:
        return ""
    symbol = str(_tv_first_value(row, "symbol", "name", "ticker") or "").strip().upper()
    if ":" in symbol:
        symbol = symbol.split(":", 1)[1]
    symbol = re.sub(r"\s+", "", symbol)
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", symbol):
        return ""
    return symbol


def _format_zacks_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    rank = snapshot.get("rank") if isinstance(snapshot.get("rank"), dict) else {}
    value = rank.get("value")
    label = str(rank.get("label") or "").strip()
    if value:
        rank_text = f"#{value}"
        if label:
            rank_text += f" {label}"
    else:
        rank_text = "n/a"
    summary = snapshot.get("analysis_summary") if isinstance(snapshot.get("analysis_summary"), dict) else {}
    return {
        "zacks_rank": rank_text,
        "zacks_rank_value": value,
        "zacks_esp": str(snapshot.get("earnings_esp") or ""),
        "zacks_note": _short_text(summary.get("summary"), 260),
        "zacks_blocked": bool(snapshot.get("blocked")),
        "zacks_error": str(snapshot.get("analysis_error") or ""),
    }


def _cached_zacks_snapshot(kernel: Any, symbol: str) -> tuple[dict[str, Any] | None, str]:
    now = time.monotonic()
    with _ZACKS_TV_LIST_CACHE_LOCK:
        cached = _ZACKS_TV_LIST_CACHE.get(symbol)
        if cached is not None:
            expires_at, payload = cached
            if expires_at > now:
                return (payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else None, str(payload.get("error") or ""))
    try:
        snapshot = kernel.execute_mcp_tool("zacks_scrape_symbol", {"symbol": symbol})
        if not isinstance(snapshot, dict):
            raise RuntimeError("Zacks returned an invalid payload.")
        payload = {"snapshot": snapshot, "error": ""}
        ttl = ZACKS_TV_LIST_CACHE_SECONDS
    except Exception as exc:
        payload = {"snapshot": None, "error": str(exc)}
        ttl = ZACKS_TV_LIST_ERROR_CACHE_SECONDS
    with _ZACKS_TV_LIST_CACHE_LOCK:
        _ZACKS_TV_LIST_CACHE[symbol] = (now + ttl, payload)
    return (payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else None, str(payload.get("error") or ""))


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
        "zacks_symbol": _tv_first_value(row, "zacks_symbol"),
        "zacks_rank": _tv_first_value(row, "zacks_rank") or "n/a",
        "zacks_rank_value": _tv_first_value(row, "zacks_rank_value"),
        "zacks_esp": _tv_first_value(row, "zacks_esp"),
        "zacks_note": _tv_first_value(row, "zacks_note"),
        "zacks_blocked": bool(row.get("zacks_blocked")),
        "zacks_error": _tv_first_value(row, "zacks_error"),
    }


def _enrich_tv_rows_with_zacks(kernel: Any, rows: list[dict[str, Any]], zacks_limit: int) -> tuple[int, list[str]]:
    warnings: list[str] = []
    attempts = 0
    failures = 0
    bounded_limit = max(0, min(int(zacks_limit), ZACKS_TV_LIST_LIMIT_MAX))
    if bounded_limit <= 0:
        return 0, warnings
    for row in rows:
        symbol = _zacks_symbol_from_tv_row(row)
        if not symbol:
            continue
        row["zacks_symbol"] = symbol
        attempts += 1
        snapshot, error = _cached_zacks_snapshot(kernel, symbol)
        if snapshot is not None:
            row.update(_format_zacks_snapshot(snapshot))
        else:
            failures += 1
            row["zacks_rank"] = "n/a"
            row["zacks_error"] = error
        if attempts >= bounded_limit:
            break
    if failures:
        warnings.append(f"Zacks enrichment failed for {failures} hot-list row(s); hover row values for details.")
    return attempts - failures, warnings


def _normalize_tv_lists_payload(payload: Any, *, kernel: Any | None = None, fallback_reason: str = "", zacks_limit: int = ZACKS_TV_LIST_LIMIT_DEFAULT) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    rows = payload.get("rows") or payload.get("items") or payload.get("symbols") or []
    normalized_rows = [_normalize_tv_row(row) for row in rows if isinstance(row, dict)]
    zacks_warnings: list[str] = []
    zacks_enriched_count = 0
    if kernel is not None:
        zacks_enriched_count, zacks_warnings = _enrich_tv_rows_with_zacks(kernel, normalized_rows, zacks_limit)
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
        "zacks_enriched_count": zacks_enriched_count,
        "zacks_limit": max(0, min(int(zacks_limit), ZACKS_TV_LIST_LIMIT_MAX)),
        "warnings": [*(payload.get("warnings") if isinstance(payload.get("warnings"), list) else []), *zacks_warnings],
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
async def tv_lists(request: Request, list_id: str = "", limit: int = 200, zacks_limit: int = ZACKS_TV_LIST_LIMIT_DEFAULT):
    kernel = _kernel(request)

    def work():
        bounded_limit = max(1, min(int(limit), 200))
        arguments = {"limit": bounded_limit}
        if list_id and list_id != "public-heatmap":
            arguments["list_id"] = list_id
        try:
            return _normalize_tv_lists_payload(kernel.execute_mcp_tool("tv_auth_custom_lists", arguments), kernel=kernel, zacks_limit=zacks_limit)
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
            return _normalize_tv_lists_payload(fallback, kernel=kernel, fallback_reason=str(custom_exc), zacks_limit=zacks_limit)

    try:
        return await asyncio.to_thread(work)
    except PermissionError as exc:
        return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)
    except Exception as exc:
        return JSONResponse({"error": "tv_failed", "detail": str(exc)}, status_code=502)
