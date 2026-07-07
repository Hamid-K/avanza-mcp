"""Trading endpoints: two-step dry-run → review-nonce → place, plus cancel.

Safety model (mirrors the TUI's human path, made stronger):
- Paper mode on → submissions go to the paper ledger, no typed confirm.
- Live placement requires paper mode OFF and the exact typed confirmation
  ("PLACE" / "CANCEL"), like the TUI ticket.
- Additionally (web-only hardening) live placement runs in two steps: the
  dry-run response carries a single-use ``review_id`` whose stored,
  validated payload is what actually executes — a blind one-shot POST
  cannot place an order, and what was reviewed is exactly what runs.
- The MCP R/W + live-authorization toggles gate MCP tool calls, not this
  human path — identical to the TUI.
"""

import asyncio
import secrets
import threading
import time
from datetime import date
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from avanza_mcp.core.trading import build_regular_order_request_from_fields, build_stop_loss_request_from_fields
from avanza_mcp.stoploss_rules import validate_valid_until

router = APIRouter()

REVIEW_TTL_SECONDS = 120.0


class ReviewStore:
    """Single-use, TTL-bound storage of validated trade requests."""

    def __init__(self) -> None:
        self._items: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def put(self, payload: dict[str, Any]) -> str:
        review_id = secrets.token_urlsafe(16)
        now = time.monotonic()
        with self._lock:
            self._items = {k: v for k, v in self._items.items() if v[0] > now}
            self._items[review_id] = (now + REVIEW_TTL_SECONDS, payload)
        return review_id

    def pop(self, review_id: str) -> dict[str, Any] | None:
        now = time.monotonic()
        with self._lock:
            entry = self._items.pop(str(review_id or ""), None)
        if entry is None or entry[0] <= now:
            return None
        return entry[1]


_reviews = ReviewStore()


def _kernel(request: Request):
    return request.app.state.runtime.kernel


async def _run(kernel, fn, *args, **kwargs):
    def work():
        with kernel.state_lock:
            return fn(*args, **kwargs)

    return await asyncio.to_thread(work)


def _parse_iso_date(value: Any, label: str) -> date:
    if isinstance(value, date):
        return value
    try:
        parsed = date.fromisoformat(str(value or ""))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO date (YYYY-MM-DD).") from exc
    return validate_valid_until(parsed, label)


def _instrument_label(kernel, order_book_id: str) -> str:
    return (
        kernel.order_search_labels_by_order_book.get(order_book_id)
        or kernel.holding_labels_by_order_book.get(order_book_id)
        or kernel.stock_name_for_order_book(order_book_id)
        or order_book_id
    )


# --------------------------------------------------------------------- orders

@router.post("/api/orders/dry-run")
async def order_dry_run(request: Request):
    kernel = _kernel(request)
    body = await request.json()
    fields = {
        "account_id": str(body.get("account_id") or kernel.selected_account_id or ""),
        "order_book_id": body.get("order_book_id"),
        "order_type": body.get("order_type", "buy"),
        "price": body.get("price"),
        "valid_until": body.get("valid_until"),
        "volume": body.get("volume"),
        "condition": body.get("condition", "normal"),
    }
    try:
        order_type, condition, preview = build_regular_order_request_from_fields(fields)
    except (ValueError, KeyError, TypeError) as exc:
        return JSONResponse({"error": "invalid_request", "detail": str(exc)}, status_code=400)
    review_id = _reviews.put({"kind": "order", "order_type": order_type, "condition": condition, "preview": preview})
    return {
        "review_id": review_id,
        "preview": preview,
        "paper_mode": kernel.paper_mode_enabled,
        "confirm_required": None if kernel.paper_mode_enabled else "PLACE",
        "expires_in_seconds": int(REVIEW_TTL_SECONDS),
    }


@router.post("/api/orders/place")
async def order_place(request: Request):
    kernel = _kernel(request)
    body = await request.json()
    stored = _reviews.pop(str(body.get("review_id", "")))
    if stored is None or stored.get("kind") != "order":
        return JSONResponse({"error": "review_expired", "detail": "Dry-run again; the review expired or was already used."}, status_code=409)
    preview = stored["preview"]
    instrument = _instrument_label(kernel, preview["order_book_id"])

    if kernel.paper_mode_enabled:
        paper_order = await _run(kernel, kernel.submit_paper_order, preview, instrument, source="web")
        return {"ok": True, "mode": "paper", "order": paper_order}

    if str(body.get("confirm_text", "")) != "PLACE":
        return JSONResponse({"error": "confirm_required", "detail": 'Type "PLACE" to confirm live placement.'}, status_code=403)
    try:
        result = await _run(kernel, kernel.submit_live_order, stored["order_type"], stored["condition"], preview, source="web")
    except Exception as exc:
        return JSONResponse({"error": "placement_failed", "detail": str(exc)}, status_code=502)
    kernel.refresh_selected_account_live()
    return {"ok": True, "mode": "live", "result": result}


# ------------------------------------------------------------------ stoplosses

@router.post("/api/stoplosses/dry-run")
async def stoploss_dry_run(request: Request):
    kernel = _kernel(request)
    body = await request.json()
    try:
        fields = {
            "account_id": str(body.get("account_id") or kernel.selected_account_id or ""),
            "order_book_id": body.get("order_book_id"),
            "valid_until": _parse_iso_date(body.get("valid_until"), "valid_until"),
            "trigger_type": body.get("trigger_type", "follow_upwards"),
            "trigger_value": body.get("trigger_value"),
            "trigger_value_type": body.get("trigger_value_type", "percentage"),
            "trigger_on_market_maker_quote": bool(body.get("trigger_on_market_maker_quote", False)),
            "order_type": body.get("order_type", "sell"),
            "order_price": body.get("order_price"),
            "volume": body.get("volume"),
            "order_valid_days": body.get("order_valid_days", 1),
            "order_price_type": body.get("order_price_type", "percentage"),
            "short_selling_allowed": bool(body.get("short_selling_allowed", False)),
        }
        trigger, order_event, preview = build_stop_loss_request_from_fields(fields)
        warnings = await _run(kernel, kernel.apply_stoploss_valid_days_safety, preview, live=not kernel.paper_mode_enabled)
    except (ValueError, KeyError, TypeError) as exc:
        return JSONResponse({"error": "invalid_request", "detail": str(exc)}, status_code=400)
    replace_id = str(body.get("replace_stoploss_id", "") or "").strip() or None
    review_id = _reviews.put(
        {"kind": "stoploss", "trigger": trigger, "order_event": order_event, "preview": preview, "replace_stoploss_id": replace_id}
    )
    return {
        "review_id": review_id,
        "preview": preview,
        "warnings": warnings,
        "replace_stoploss_id": replace_id,
        "paper_mode": kernel.paper_mode_enabled,
        "confirm_required": None if kernel.paper_mode_enabled else "PLACE",
        "expires_in_seconds": int(REVIEW_TTL_SECONDS),
    }


@router.post("/api/stoplosses/place")
async def stoploss_place(request: Request):
    kernel = _kernel(request)
    body = await request.json()
    stored = _reviews.pop(str(body.get("review_id", "")))
    if stored is None or stored.get("kind") != "stoploss":
        return JSONResponse({"error": "review_expired", "detail": "Dry-run again; the review expired or was already used."}, status_code=409)
    preview = stored["preview"]
    instrument = _instrument_label(kernel, preview["order_book_id"])

    if kernel.paper_mode_enabled:
        paper_order = await _run(kernel, kernel.submit_paper_stop_loss, preview, instrument, source="web")
        return {"ok": True, "mode": "paper", "order": paper_order}

    if str(body.get("confirm_text", "")) != "PLACE":
        return JSONResponse({"error": "confirm_required", "detail": 'Type "PLACE" to confirm live placement.'}, status_code=403)
    try:
        result = await _run(
            kernel,
            kernel.submit_live_stop_loss,
            stored["trigger"],
            stored["order_event"],
            preview,
            replace_stoploss_id=stored.get("replace_stoploss_id"),
            source="web",
        )
    except Exception as exc:
        return JSONResponse({"error": "placement_failed", "detail": str(exc)}, status_code=502)
    kernel.refresh_selected_account_live()
    return {"ok": True, "mode": "live", "result": result}


# ---------------------------------------------------------------------- cancel

@router.post("/api/orders/cancel")
async def cancel(request: Request):
    kernel = _kernel(request)
    body = await request.json()
    kind = str(body.get("kind", "order")).lower()
    identifier = str(body.get("id", "")).strip()
    if not identifier:
        return JSONResponse({"error": "invalid_request", "detail": "id is required."}, status_code=400)

    if kind == "paper":
        target = {"mode": "Paper", "kind": str(body.get("item_kind", "Order")), "id": identifier,
                  "account_id": str(body.get("account_id", "")), "stock": str(body.get("stock", ""))}
        try:
            result = await _run(kernel, kernel.submit_cancel, target, source="web")
        except (KeyError, ValueError) as exc:
            return JSONResponse({"error": "cancel_failed", "detail": str(exc)}, status_code=404)
        kernel.on_state_changed("stoplosses")
        return {"ok": True, "mode": "paper", "result": result}

    if str(body.get("confirm_text", "")) != "CANCEL":
        return JSONResponse({"error": "confirm_required", "detail": 'Type "CANCEL" to confirm live cancellation.'}, status_code=403)
    target = {
        "mode": "Live",
        "kind": "Stop-loss" if kind == "stoploss" else "Order",
        "id": identifier,
        "account_id": str(body.get("account_id", "") or kernel.selected_account_id or ""),
        "stock": str(body.get("stock", "")),
    }
    try:
        result = await _run(kernel, kernel.submit_cancel, target, source="web")
    except Exception as exc:
        return JSONResponse({"error": "cancel_failed", "detail": str(exc)}, status_code=502)
    kernel.refresh_selected_account_live()
    return {"ok": True, "mode": "live", "result": result}


# ------------------------------------------------------------------ paper mode

@router.post("/api/paper/mode")
async def paper_mode(request: Request):
    kernel = _kernel(request)
    body = await request.json()
    enabled = bool(body.get("enabled", True))
    if not enabled and not bool(body.get("acknowledge", False)):
        # Leaving paper mode arms LIVE ticket submissions; require an explicit,
        # server-verified acknowledgement so it cannot happen by accident.
        return JSONResponse(
            {"error": "acknowledge_required", "detail": "Confirm that ticket submissions become LIVE orders."},
            status_code=403,
        )
    kernel.paper_mode_enabled = enabled
    kernel.record_event(
        "trading",
        "paper_mode_toggled_from_web",
        {"paper_mode": enabled, "acknowledged": not enabled},
    )
    kernel.write_log(f"Paper trading mode {'enabled' if enabled else 'DISABLED — live trading ticket'} (web).")
    kernel.on_state_changed("mcp_status")
    return {"ok": True, "paper_mode": kernel.paper_mode_enabled}
