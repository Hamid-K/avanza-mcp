"""Stop-loss validation rules and date-window helpers."""

from datetime import date, timedelta
from typing import Any

from avanza_mcp.config import VALID_UNTIL_MAX_DAYS

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
