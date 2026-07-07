"""Read-only data endpoints: accounts, portfolio, orders, history, search, quotes."""

import asyncio
from contextlib import nullcontext
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from avanza_mcp.records import flattened_search_hits, search_hit_label, search_hit_order_book_id
from avanza_mcp.rendering import holding_search_options, market_clock_text
from avanza_mcp.utils import is_unauthorized_http_error
from avanza_mcp.web.serializers import (
    account_summary,
    orders_payload,
    portfolio_payload,
    stoplosses_payload,
)

router = APIRouter()


def _kernel(request: Request):
    return request.app.state.runtime.kernel


def _scope(kernel, session_id: str | None):
    """Temporarily activate another tenant session for this call, if requested."""
    token = str(session_id or "").strip()
    if token and token != (kernel.active_session_id or ""):
        return kernel.temporary_tenant_scope(token)
    return nullcontext()


def _auth_expired_response(kernel, exc: Exception) -> JSONResponse | None:
    if is_unauthorized_http_error(exc):
        session_id = kernel.active_session_id
        kernel.mark_tenant_session_auth_expired(session_id, exc)
        return JSONResponse({"error": "auth_expired", "session_id": session_id}, status_code=401)
    return None


async def _run(kernel, fn, *args, **kwargs):
    def work():
        with kernel.state_lock:
            return fn(*args, **kwargs)

    return await asyncio.to_thread(work)


@router.get("/api/accounts")
async def accounts(request: Request):
    kernel = _kernel(request)
    return {
        "accounts": [account_summary(a) for a in kernel.accounts],
        "selected_account_id": kernel.selected_account_id,
    }


@router.post("/api/accounts/{account_id}/select")
async def select_account(account_id: str, request: Request):
    kernel = _kernel(request)
    try:
        await _run(kernel, kernel.select_account, account_id)
    except ValueError as exc:
        return JSONResponse({"error": "unknown_account", "detail": str(exc)}, status_code=404)
    kernel.refresh_selected_account_live()
    return {"ok": True, "selected_account_id": kernel.selected_account_id}


@router.get("/api/portfolio")
async def portfolio(request: Request, session_id: str | None = None):
    kernel = _kernel(request)

    def work():
        with kernel.state_lock, _scope(kernel, session_id):
            return portfolio_payload(kernel)

    return await asyncio.to_thread(work)


@router.get("/api/orders/open")
async def open_orders(request: Request, session_id: str | None = None):
    kernel = _kernel(request)

    def work():
        with kernel.state_lock, _scope(kernel, session_id):
            return orders_payload(kernel)

    return await asyncio.to_thread(work)


@router.get("/api/stoplosses")
async def stoplosses(request: Request, session_id: str | None = None):
    kernel = _kernel(request)

    def work():
        with kernel.state_lock, _scope(kernel, session_id):
            return stoplosses_payload(kernel)

    return await asyncio.to_thread(work)


@router.get("/api/transactions")
async def transactions(request: Request, from_date: str | None = None, to_date: str | None = None):
    kernel = _kernel(request)
    if kernel.avanza is None or not kernel.selected_account_id:
        return JSONResponse({"error": "no_session"}, status_code=409)
    try:
        from avanza_mcp.records import parse_optional_iso_date

        payload = await _run(
            kernel,
            kernel.transactions_snapshot,
            kernel.avanza,
            kernel.selected_account_id,
            transactions_from=parse_optional_iso_date(from_date, label="from_date"),
            transactions_to=parse_optional_iso_date(to_date, label="to_date"),
        )
    except Exception as exc:
        denial = _auth_expired_response(kernel, exc)
        if denial is not None:
            return denial
        return JSONResponse({"error": "upstream_failed", "detail": str(exc)}, status_code=502)
    return payload


@router.get("/api/search")
async def search(request: Request, q: str = ""):
    kernel = _kernel(request)
    query = str(q or "").strip()
    if len(query) < 2:
        return JSONResponse({"error": "query_too_short"}, status_code=400)

    def work() -> dict[str, Any]:
        options: list[dict[str, str]] = []
        seen: set[str] = set()

        def add(label: str, order_book_id: str, stock_name: str | None = None) -> None:
            if not order_book_id or order_book_id in seen:
                return
            options.append({"label": label, "order_book_id": order_book_id, "name": stock_name or label.split(" - owned", 1)[0]})
            seen.add(order_book_id)

        if kernel.latest_portfolio_data is not None:
            for label, order_book_id in holding_search_options(kernel.latest_portfolio_data, kernel.selected_account_id, query):
                add(label, order_book_id)
        remote_error = ""
        if kernel.avanza is not None:
            try:
                hits = flattened_search_hits(kernel.avanza.search_for_stock(query, 20))
            except Exception as exc:
                remote_error = str(exc)
                hits = []
            for hit in hits:
                add(search_hit_label(hit), search_hit_order_book_id(hit), str(hit.get("name") or ""))
        return {"results": options, "remote_error": remote_error}

    return await asyncio.to_thread(work)


@router.get("/api/quote/{order_book_id}")
async def quote(order_book_id: str, request: Request):
    kernel = _kernel(request)
    if kernel.avanza is None:
        return JSONResponse({"error": "no_session"}, status_code=409)
    try:
        payload = await _run(kernel, kernel.quote_payload_for_order_book, order_book_id)
    except Exception as exc:
        denial = _auth_expired_response(kernel, exc)
        if denial is not None:
            return denial
        return JSONResponse({"error": "upstream_failed", "detail": str(exc)}, status_code=502)
    return {"order_book_id": order_book_id, "quote": payload}


@router.get("/api/performance")
async def performance(request: Request, period: str = "month", account_id: str | None = None):
    kernel = _kernel(request)
    if kernel.avanza is None:
        return JSONResponse({"error": "no_session"}, status_code=409)
    target_account = str(account_id or kernel.selected_account_id or "")
    if not target_account:
        return JSONResponse({"error": "no_account"}, status_code=409)
    try:
        payload = await _run(kernel, kernel.account_performance_snapshot, kernel.avanza, target_account, period)
    except Exception as exc:
        denial = _auth_expired_response(kernel, exc)
        if denial is not None:
            return denial
        return JSONResponse({"error": "upstream_failed", "detail": str(exc)}, status_code=502)
    return payload


@router.get("/api/market/status")
async def market_status(request: Request):
    return {"clock": market_clock_text()}


@router.post("/api/refresh")
async def manual_refresh(request: Request):
    kernel = _kernel(request)
    kernel.refresh_selected_account_live()
    return {"ok": True}
