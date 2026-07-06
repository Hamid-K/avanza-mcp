"""Direct pokes at the Avanza private API plus fee estimation."""

from typing import Any

from avanza.constants import HttpMethod

from avanza_mcp.config import (
    DEFAULT_COURTAGE_MIN_SEK,
    DEFAULT_COURTAGE_MIN_USD,
    DEFAULT_COURTAGE_RATE_SE,
    DEFAULT_COURTAGE_RATE_US,
    DEFAULT_FX_FEE_RATE,
)

def estimate_avanza_fee(
    *,
    account_id: str,
    order_book_id: str,
    side: str,
    price: float,
    quantity: int,
    currency: str = "SEK",
    market: str = "",
    brokerage_class: str = "",
) -> dict[str, Any]:
    _ = account_id
    _ = order_book_id
    _ = side
    notional = float(price) * float(quantity)
    currency_norm = str(currency or "SEK").upper()
    market_norm = str(market or "").lower()
    brokerage_norm = str(brokerage_class or "").strip().lower()

    if "fast" in brokerage_norm:
        courtage_rate = 0.0
        courtage_min = 99.0 if currency_norm == "SEK" else 9.99
        notes = ["Using conservative fixed-fee assumption for Fast brokerage class."]
    else:
        if currency_norm == "SEK" and ("se" in market_norm or not market_norm):
            courtage_rate = DEFAULT_COURTAGE_RATE_SE
            courtage_min = DEFAULT_COURTAGE_MIN_SEK
        else:
            courtage_rate = DEFAULT_COURTAGE_RATE_US
            courtage_min = DEFAULT_COURTAGE_MIN_USD
        notes = ["Using conservative percentage+minimum estimate; exact fee depends on Avanza courtage class and market."]

    estimated_courtage = max(courtage_min, abs(notional) * courtage_rate)
    fx_fee = 0.0
    if currency_norm != "SEK":
        fx_fee = abs(notional) * DEFAULT_FX_FEE_RATE
        notes.append("Includes one-way FX fee estimate; round-trip trading incurs FX costs on both entry and exit.")

    estimated_total_cost = estimated_courtage + fx_fee
    round_trip_cost = estimated_total_cost * 2.0
    break_even_move_percent = ((round_trip_cost / abs(notional)) * 100.0) if notional not in (0, -0.0) else None
    return {
        "account_id": str(account_id),
        "orderbook_id": str(order_book_id),
        "side": str(side).upper(),
        "currency": currency_norm,
        "market": market,
        "brokerage_class": brokerage_class or None,
        "notional": notional,
        "estimated_courtage": estimated_courtage,
        "estimated_fx_fee": fx_fee,
        "estimated_total_cost": estimated_total_cost,
        "estimated_round_trip_cost": round_trip_cost,
        "break_even_move_percent": break_even_move_percent,
        "notes": notes,
        "assumptions": {
            "courtage_rate": courtage_rate,
            "courtage_minimum": courtage_min,
            "fx_fee_rate": DEFAULT_FX_FEE_RATE if currency_norm != "SEK" else 0.0,
        },
    }


def avanza_private_get(avanza: Any, path: str, options: dict[str, Any] | None = None) -> Any:
    caller = getattr(avanza, "_Avanza__call", None)
    if not callable(caller):
        raise RuntimeError("Avanza private API call path unavailable in this client version.")
    return caller(HttpMethod.GET, path, options=options or {})


def avanza_private_post(avanza: Any, path: str, body: dict[str, Any] | None = None) -> Any:
    caller = getattr(avanza, "_Avanza__call", None)
    if not callable(caller):
        raise RuntimeError("Avanza private API call path unavailable in this client version.")
    return caller(HttpMethod.POST, path, options=body or {})
