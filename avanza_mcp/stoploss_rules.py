"""Stop-loss validation rules and date-window helpers."""

from datetime import date, timedelta
from typing import Any

from avanza_mcp.config import VALID_UNTIL_MAX_DAYS
from avanza_mcp.market_data import (
    currency_metadata_evidence,
    infer_currency_from_metadata,
    merged_orderbook_metadata,
)

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


def normalize_stoploss_order_valid_days(value: Any, label: str = "Order valid days") -> int:
    try:
        parsed = int(value)
    except Exception as exc:
        raise ValueError(f"{label} must be an integer.") from exc
    if parsed < 1:
        raise ValueError(f"{label} must be at least 1.")
    return parsed


def stoploss_triggered_order_expiry(valid_days: int, reference: date | None = None) -> str:
    base = reference or date.today()
    return (base + timedelta(days=normalize_stoploss_order_valid_days(valid_days))).isoformat()


def enforce_stoploss_currency_safety(
    preview: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    *,
    live: bool,
) -> list[str]:
    merged = merged_orderbook_metadata(metadata or {}, {})
    currency, currency_source, currency_verified = currency_metadata_evidence(merged)
    preview["currency"] = currency
    preview["currency_source"] = currency_source
    preview["currency_verified"] = currency_verified
    preview["currency_orderbook_id"] = merged.get("currency_orderbook_id")
    preview["currency_conflicts"] = list(merged.get("currency_conflicts") or [])

    trigger = preview.get("stop_loss_trigger") if isinstance(preview.get("stop_loss_trigger"), dict) else {}
    order_event = preview.get("stop_loss_order_event") if isinstance(preview.get("stop_loss_order_event"), dict) else {}
    monetary = any(
        str(value).strip().upper() == "MONETARY"
        for value in (trigger.get("value_type"), order_event.get("price_type"))
    )
    if not monetary or (currency and currency_verified):
        return []

    order_book_id = str(preview.get("order_book_id") or merged.get("orderbook_id") or "instrument").strip()
    if currency:
        warning = (
            f"{order_book_id}: instrument currency {currency} is unverified "
            f"({currency_source}); monetary stop values cannot be interpreted safely."
        )
    else:
        warning = (
            f"{order_book_id}: instrument currency is unresolved; monetary "
            "stop values cannot be interpreted safely."
        )
    if live:
        raise ValueError(warning)
    return [warning]


def stoploss_order_valid_days_warnings(
    valid_days: int,
    metadata: dict[str, Any] | None = None,
) -> list[str]:
    days = normalize_stoploss_order_valid_days(valid_days)
    if days <= 1:
        return []
    merged = merged_orderbook_metadata(metadata or {}, {})
    currency = str(merged.get("currency") or infer_currency_from_metadata(merged) or "").strip().upper()
    market = str(merged.get("market") or "").strip()
    country = str(merged.get("country_code") or merged.get("country") or "").strip().upper()
    label = str(merged.get("name") or merged.get("ticker") or merged.get("orderbook_id") or "instrument")
    if currency and currency != "SEK":
        return [
            (
                f"{label}: order_valid_days={days} with {currency} instrument can fail with "
                f"'Ogiltigt giltighetsdatum'. Use order_valid_days=1."
            )
        ]
    if not currency and (market or country):
        return [
            (
                f"{label}: currency unresolved ({country or '-'} / {market or '-'}). "
                "order_valid_days>1 may be rejected by Avanza; use 1 day."
            )
        ]
    return []


def stoploss_is_foreign_instrument(metadata: dict[str, Any] | None = None) -> bool | None:
    merged = merged_orderbook_metadata(metadata or {}, {})
    currency = str(merged.get("currency") or infer_currency_from_metadata(merged) or "").strip().upper()
    if currency:
        return currency != "SEK"
    country = str(merged.get("country_code") or merged.get("country") or "").strip().upper()
    if country:
        return country not in {"", "SE"}
    market = str(merged.get("market") or "").strip().lower()
    if market:
        if any(token in market for token in ("stockholm", "xsto", "first north", "spotlight", "ngm")):
            return False
        if any(token in market for token in ("nasdaq", "nyse", "xhel", "xcse", "xosl", "xlon")):
            return True
    return None



def enforce_live_stoploss_order_valid_days(
    valid_days: int,
    metadata: dict[str, Any] | None = None,
    *,
    live: bool,
) -> list[str]:
    days = normalize_stoploss_order_valid_days(valid_days)
    warnings = stoploss_order_valid_days_warnings(days, metadata)
    if live and days > 1 and stoploss_is_foreign_instrument(metadata) is True and warnings:
        raise ValueError(warnings[0])
    return warnings
