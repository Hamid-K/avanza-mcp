"""Paper-trading session engine and paper order accessors."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from avanza_mcp import config, utils
from avanza_mcp.config import PAPER_ORDER_ACTIVE_STATES
from avanza_mcp.rendering import build_order_preview, build_stop_loss_preview

def empty_paper_session() -> dict[str, Any]:
    now = datetime.now().isoformat(timespec="seconds")
    return {
        "version": 2,
        "created_at": now,
        "updated_at": now,
        "orders": [],
        "events": [],
        "positions": [],
        "trades": [],
        "watchlists": {},
    }


def load_paper_session(path: Path | None = None) -> dict[str, Any]:
    path = path or config.PAPER_SESSION_FILE
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
    data.setdefault("positions", [])
    data.setdefault("trades", [])
    data.setdefault("watchlists", {})
    if not isinstance(data["orders"], list):
        data["orders"] = []
    if not isinstance(data["events"], list):
        data["events"] = []
    if not isinstance(data["positions"], list):
        data["positions"] = []
    if not isinstance(data["trades"], list):
        data["trades"] = []
    if not isinstance(data["watchlists"], dict):
        data["watchlists"] = {}
    return data


def save_paper_session(session: dict[str, Any], path: Path | None = None) -> None:
    path = path or config.PAPER_SESSION_FILE
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


def paper_positions(session: dict[str, Any], account_id: str | None = None, session_id: str | None = None, active_only: bool = False) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in session.get("positions", []):
        if not isinstance(item, dict):
            continue
        if account_id and str(item.get("account_id", "")) != account_id:
            continue
        if session_id and str(item.get("session_id", "")) != session_id:
            continue
        if active_only and str(item.get("status", "")).upper() != "OPEN":
            continue
        rows.append(item)
    return rows


def paper_trades(session: dict[str, Any], account_id: str | None = None, session_id: str | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in session.get("trades", []):
        if not isinstance(item, dict):
            continue
        if account_id and str(item.get("account_id", "")) != account_id:
            continue
        if session_id and str(item.get("session_id", "")) != session_id:
            continue
        rows.append(item)
    return rows


def paper_session_id(raw: Any | None = None) -> str:
    text = str(raw or "").strip()
    if text:
        return text
    return datetime.now().strftime("%Y%m%d")


def paper_open_position(
    session: dict[str, Any],
    *,
    session_id: str,
    account_id: str,
    order_book_id: str,
    ticker: str,
    name: str,
    side: str,
    entry_price: float,
    quantity: int,
    estimated_fees: float,
    entry_reason: str = "",
    stop_price: float | None = None,
    target_price: float | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    position = {
        "position_id": f"paper-pos-{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
        "session_id": session_id,
        "account_id": account_id,
        "orderbook_id": str(order_book_id),
        "ticker": str(ticker or ""),
        "name": str(name or ""),
        "side": str(side or "").upper(),
        "entry_time": now,
        "exit_time": None,
        "entry_price": float(entry_price),
        "exit_price": None,
        "quantity": int(quantity),
        "notional": float(entry_price) * float(quantity),
        "estimated_fees": float(estimated_fees),
        "gross_pnl_sek": 0.0,
        "net_pnl_sek": -float(estimated_fees),
        "pnl_percent": 0.0,
        "entry_reason": entry_reason or None,
        "exit_reason": None,
        "stop_price": stop_price,
        "target_price": target_price,
        "max_favorable_excursion": 0.0,
        "max_adverse_excursion": 0.0,
        "status": "OPEN",
    }
    session.setdefault("positions", []).append(position)
    append_paper_event(session, "paper_position_open", {"position_id": position["position_id"], "orderbook_id": order_book_id})
    return position


def update_position_excursions(position: dict[str, Any], market_price: float) -> None:
    entry = utils.scalar_number(position.get("entry_price"))
    if entry in (None, 0):
        return
    side = str(position.get("side", "")).upper()
    move = ((market_price - entry) / entry) * 100.0
    if side == "SELL":
        move = -move
    mfe = utils.scalar_number(position.get("max_favorable_excursion")) or 0.0
    mae = utils.scalar_number(position.get("max_adverse_excursion")) or 0.0
    position["max_favorable_excursion"] = max(mfe, move)
    position["max_adverse_excursion"] = min(mae, move)


def paper_exit_position(
    session: dict[str, Any],
    *,
    account_id: str,
    position_id: str | None,
    order_book_id: str | None,
    exit_price: float,
    estimated_exit_fees: float,
    exit_reason: str = "",
) -> dict[str, Any]:
    candidates = paper_positions(session, account_id=account_id, active_only=True)
    target = None
    if position_id:
        target = next((item for item in candidates if str(item.get("position_id", "")) == position_id), None)
    elif order_book_id:
        target = next((item for item in candidates if str(item.get("orderbook_id", "")) == str(order_book_id)), None)
    if target is None:
        raise ValueError("No matching open paper position found.")

    entry_price = float(utils.scalar_number(target.get("entry_price")) or 0.0)
    quantity = int(target.get("quantity", 0) or 0)
    side = str(target.get("side", "")).upper()
    notional_entry = entry_price * quantity
    notional_exit = float(exit_price) * quantity
    gross = (notional_exit - notional_entry) if side == "BUY" else (notional_entry - notional_exit)
    fees_total = float(utils.scalar_number(target.get("estimated_fees")) or 0.0) + float(estimated_exit_fees)
    net = gross - fees_total
    pnl_percent = (net / notional_entry * 100.0) if notional_entry else 0.0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    target["exit_time"] = now
    target["exit_price"] = float(exit_price)
    target["estimated_fees"] = fees_total
    target["gross_pnl_sek"] = gross
    target["net_pnl_sek"] = net
    target["pnl_percent"] = pnl_percent
    target["exit_reason"] = exit_reason or None
    target["status"] = "CLOSED"
    update_position_excursions(target, float(exit_price))

    trade = dict(target)
    trade["trade_id"] = f"paper-trade-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    session.setdefault("trades", []).append(trade)
    append_paper_event(
        session,
        "paper_position_close",
        {"position_id": str(target.get("position_id", "")), "trade_id": trade["trade_id"], "net_pnl_sek": net},
    )
    return trade


def paper_session_summary(session: dict[str, Any], session_id: str | None = None, account_id: str | None = None) -> dict[str, Any]:
    positions_open = paper_positions(session, account_id=account_id, session_id=session_id, active_only=True)
    trades_closed = paper_trades(session, account_id=account_id, session_id=session_id)
    gross = sum(float(item.get("gross_pnl_sek") or 0.0) for item in trades_closed)
    net = sum(float(item.get("net_pnl_sek") or 0.0) for item in trades_closed)
    winners = sum(1 for item in trades_closed if float(item.get("net_pnl_sek") or 0.0) > 0)
    losers = sum(1 for item in trades_closed if float(item.get("net_pnl_sek") or 0.0) < 0)
    win_rate = (winners / len(trades_closed) * 100.0) if trades_closed else 0.0
    return {
        "session_id": session_id or None,
        "account_id": account_id or None,
        "open_positions": len(positions_open),
        "closed_trades": len(trades_closed),
        "gross_pnl_sek": gross,
        "net_pnl_sek": net,
        "winners": winners,
        "losers": losers,
        "win_rate_percent": win_rate,
    }


def paper_risk_state(
    session: dict[str, Any],
    *,
    session_id: str,
    account_id: str,
    max_open_trades: int,
    max_trade_notional_sek: float,
    max_loss_per_trade_sek: float,
    max_session_loss_sek: float,
    stop_after_consecutive_losses: int,
) -> dict[str, Any]:
    open_positions = paper_positions(session, account_id=account_id, session_id=session_id, active_only=True)
    closed = paper_trades(session, account_id=account_id, session_id=session_id)
    session_pnl = sum(float(item.get("net_pnl_sek") or 0.0) for item in closed)
    violations: list[str] = []
    if len(open_positions) >= max_open_trades:
        violations.append("max_open_trades")
    if session_pnl <= -abs(max_session_loss_sek):
        violations.append("max_session_loss")

    consecutive_losses = 0
    for item in sorted(closed, key=lambda row: str(row.get("exit_time", "") or row.get("entry_time", "")), reverse=True):
        if float(item.get("net_pnl_sek") or 0.0) < 0:
            consecutive_losses += 1
        else:
            break
    if stop_after_consecutive_losses > 0 and consecutive_losses >= stop_after_consecutive_losses:
        violations.append("consecutive_losses")

    largest_open_notional = max((abs(float(item.get("notional") or 0.0)) for item in open_positions), default=0.0)
    if largest_open_notional > max_trade_notional_sek:
        violations.append("max_trade_notional")

    large_loss_positions = [
        item for item in open_positions if float(item.get("net_pnl_sek") or 0.0) <= -abs(max_loss_per_trade_sek)
    ]
    if large_loss_positions:
        violations.append("max_loss_per_trade")

    return {
        "session_id": session_id,
        "account_id": account_id,
        "can_enter_new_trade": len(violations) == 0,
        "open_trade_count": len(open_positions),
        "current_session_pnl": session_pnl,
        "consecutive_losses": consecutive_losses,
        "max_trade_notional_sek": max_trade_notional_sek,
        "max_loss_per_trade_sek": max_loss_per_trade_sek,
        "max_session_loss_sek": max_session_loss_sek,
        "stop_after_consecutive_losses": stop_after_consecutive_losses,
        "violations": violations,
        "open_positions": open_positions,
        "closed_trades": len(closed),
    }


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
