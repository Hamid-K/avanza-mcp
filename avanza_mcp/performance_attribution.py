"""Read-only account performance and transaction-cost attribution helpers."""

from __future__ import annotations

import bisect
import json
import math
import re
from collections import Counter, defaultdict
from datetime import date
from typing import Any


def _number(value: Any) -> float:
    if value in (None, "", "None"):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else 0.0
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value).replace("\u00a0", "").replace(",", ""))
    return float(match.group(0)) if match else 0.0


def _missing_value(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() in {"", "None"})


def _trade_date(row: dict[str, Any]) -> str:
    return str(row.get("Trade Date") or row.get("date") or "").strip()[:10]


def _deduplicate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        fingerprint = json.dumps(row, sort_keys=True, default=str, separators=(",", ":"))
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        result.append(row)
    return result


def _cost_date(target_date: str, performance_dates: list[str]) -> str | None:
    if not target_date:
        return None
    index = bisect.bisect_left(performance_dates, target_date)
    if index >= len(performance_dates):
        return None
    return performance_dates[index]


def _monthly_summaries(daily_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, float]] = {}
    for row in daily_rows:
        month = str(row["date"])[:7]
        state = grouped.setdefault(
            month,
            {
                "actual_index": 1.0,
                "zero_commission_index": 1.0,
                "zero_commission_fx_index": 1.0,
                "commission_sek": 0.0,
                "modeled_fx_sek": 0.0,
                "days": 0.0,
            },
        )
        state["actual_index"] *= 1.0 + float(row["actual_daily_return"])
        state["zero_commission_index"] *= 1.0 + float(row["zero_commission_daily_return"])
        state["zero_commission_fx_index"] *= 1.0 + float(row["zero_commission_fx_daily_return"])
        state["commission_sek"] += float(row["commission_sek"])
        state["modeled_fx_sek"] += float(row["modeled_fx_sek"])
        state["days"] += 1

    result: list[dict[str, Any]] = []
    for month, state in sorted(grouped.items()):
        actual = (state["actual_index"] - 1.0) * 100.0
        zero_commission = (state["zero_commission_index"] - 1.0) * 100.0
        zero_commission_fx = (state["zero_commission_fx_index"] - 1.0) * 100.0
        result.append(
            {
                "month": month,
                "performance_days": int(state["days"]),
                "actual_return_percent": actual,
                "zero_commission_return_percent": zero_commission,
                "zero_commission_fx_return_percent": zero_commission_fx,
                "commission_drag_percentage_points": zero_commission - actual,
                "commission_plus_modeled_fx_drag_percentage_points": zero_commission_fx - actual,
                "commission_sek": state["commission_sek"],
                "modeled_fx_sek": state["modeled_fx_sek"],
            }
        )
    return result


def summarize_cash_events(rows: list[dict[str, Any]]) -> dict[str, Any]:
    deduplicated = _deduplicate_rows(rows)
    counts: Counter[str] = Counter()
    amounts: defaultdict[str, float] = defaultdict(float)
    for row in deduplicated:
        event_type = str(row.get("Type") or "UNKNOWN").upper()
        counts[event_type] += 1
        amount = _number(row.get("Amount"))
        if event_type == "DEPOSIT":
            amounts[event_type] += abs(amount)
        elif event_type == "WITHDRAW":
            amounts[event_type] -= abs(amount)
        else:
            amounts[event_type] += amount
    return {
        "raw_rows": len(rows),
        "deduplicated_rows": len(deduplicated),
        "counts": dict(sorted(counts.items())),
        "amounts_sek": dict(sorted(amounts.items())),
        "net_external_flow_sek": amounts["DEPOSIT"] + amounts["WITHDRAW"],
    }


def build_account_cost_attribution(
    *,
    account_id: str,
    performance_points: list[dict[str, Any]],
    transaction_rows: list[dict[str, Any]],
    cash_event_rows: list[dict[str, Any]],
    start_date: date | None,
    fx_fee_rate: float,
    include_daily: bool,
    top_cost_days: int,
) -> dict[str, Any]:
    if not 0 <= fx_fee_rate <= 0.02:
        raise ValueError("fx_fee_rate must be between 0 and 0.02.")
    if not 0 <= top_cost_days <= 100:
        raise ValueError("top_cost_days must be between 0 and 100.")

    usable_points = [
        point
        for point in performance_points
        if point.get("date")
        and point.get("development_relative", {}).get("value") is not None
        and point.get("account_value", {}).get("value") is not None
        and (start_date is None or str(point["date"]) >= start_date.isoformat())
    ]
    usable_points.sort(key=lambda point: (str(point["date"]), float(point.get("timestamp") or 0)))
    if len(usable_points) < 2:
        raise ValueError("At least two cash-flow-adjusted performance points are required.")

    performance_dates = [str(point["date"]) for point in usable_points]
    first_date = performance_dates[0]
    last_date = performance_dates[-1]
    deduplicated_transactions = _deduplicate_rows(transaction_rows)

    daily_commission: defaultdict[str, float] = defaultdict(float)
    daily_modeled_fx: defaultdict[str, float] = defaultdict(float)
    daily_turnover: defaultdict[str, float] = defaultdict(float)
    missing_commission_rows = 0
    missing_commission_notional = 0.0
    unaligned_rows = 0

    for row in deduplicated_transactions:
        transaction_date = _trade_date(row)
        if not transaction_date or transaction_date < first_date or transaction_date > last_date:
            continue
        assigned_date = _cost_date(transaction_date, performance_dates)
        if assigned_date is None:
            unaligned_rows += 1
            continue
        amount = abs(_number(row.get("Amount")))
        commission = max(0.0, _number(row.get("Commission")))
        if _missing_value(row.get("Commission")):
            missing_commission_rows += 1
            missing_commission_notional += amount
        isin = str(row.get("ISIN") or "").upper().strip()
        modeled_fx = amount * fx_fee_rate if isin and not isin.startswith("SE") else 0.0
        daily_turnover[assigned_date] += amount
        daily_commission[assigned_date] += commission
        daily_modeled_fx[assigned_date] += modeled_fx

    actual_index = 1.0
    zero_commission_index = 1.0
    zero_commission_fx_index = 1.0
    daily_rows: list[dict[str, Any]] = []

    for previous, current in zip(usable_points, usable_points[1:]):
        previous_relative = float(previous["development_relative"]["value"]) / 100.0
        current_relative = float(current["development_relative"]["value"]) / 100.0
        actual_daily_return = (1.0 + current_relative) / (1.0 + previous_relative) - 1.0
        prior_capital = float(previous["account_value"]["value"])
        if prior_capital <= 0:
            raise ValueError(f"Non-positive prior account value on {previous['date']}.")

        current_date = str(current["date"])
        commission = daily_commission[current_date]
        modeled_fx = daily_modeled_fx[current_date]
        zero_commission_daily_return = actual_daily_return + commission / prior_capital
        zero_commission_fx_daily_return = actual_daily_return + (commission + modeled_fx) / prior_capital

        actual_index *= 1.0 + actual_daily_return
        zero_commission_index *= 1.0 + zero_commission_daily_return
        zero_commission_fx_index *= 1.0 + zero_commission_fx_daily_return
        daily_rows.append(
            {
                "date": current_date,
                "account_value_sek": float(current["account_value"]["value"]),
                "actual_daily_return": actual_daily_return,
                "zero_commission_daily_return": zero_commission_daily_return,
                "zero_commission_fx_daily_return": zero_commission_fx_daily_return,
                "turnover_sek": daily_turnover[current_date],
                "commission_sek": commission,
                "modeled_fx_sek": modeled_fx,
            }
        )

    actual_return = (actual_index - 1.0) * 100.0
    zero_commission_return = (zero_commission_index - 1.0) * 100.0
    zero_commission_fx_return = (zero_commission_fx_index - 1.0) * 100.0
    commission_drag = zero_commission_return - actual_return
    commission_fx_drag = zero_commission_fx_return - actual_return
    negative_actual = abs(actual_return) if actual_return < 0 else None

    cost_days = sorted(
        daily_rows,
        key=lambda row: float(row["commission_sek"]) + float(row["modeled_fx_sek"]),
        reverse=True,
    )[:top_cost_days]

    result: dict[str, Any] = {
        "status": "COMPLETE_COMMISSION_LOWER_BOUND_WITH_MODELED_FX",
        "account_id": account_id,
        "window": {
            "start": first_date,
            "end": last_date,
            "performance_points": len(usable_points),
        },
        "method": {
            "actual_path": "Avanza cash-flow-adjusted cumulative relative series",
            "commission_path": "Exact posted commission added back on trade date against prior account value",
            "fx_path": "Commission path plus modeled FX rate on non-SE ISIN turnover",
            "fx_fee_rate": fx_fee_rate,
            "deduplication": "Exact normalized transaction-row fingerprint",
            "trade_authority": "NONE_READ_ONLY_ATTRIBUTION",
        },
        "returns": {
            "actual_percent": actual_return,
            "zero_commission_percent": zero_commission_return,
            "zero_commission_plus_modeled_fx_percent": zero_commission_fx_return,
            "commission_drag_percentage_points": commission_drag,
            "commission_plus_modeled_fx_drag_percentage_points": commission_fx_drag,
            "commission_share_of_negative_actual_return_percent": (
                commission_drag / negative_actual * 100.0 if negative_actual else None
            ),
            "commission_plus_modeled_fx_share_of_negative_actual_return_percent": (
                commission_fx_drag / negative_actual * 100.0 if negative_actual else None
            ),
        },
        "costs": {
            "raw_transaction_rows": len(transaction_rows),
            "deduplicated_transaction_rows": len(deduplicated_transactions),
            "turnover_sek": sum(float(row["turnover_sek"]) for row in daily_rows),
            "posted_commission_sek": sum(float(row["commission_sek"]) for row in daily_rows),
            "modeled_fx_sek": sum(float(row["modeled_fx_sek"]) for row in daily_rows),
            "missing_commission_rows": missing_commission_rows,
            "missing_commission_notional_sek": missing_commission_notional,
            "unaligned_transaction_rows": unaligned_rows,
        },
        "cash_events": summarize_cash_events(cash_event_rows),
        "monthly": _monthly_summaries(daily_rows),
        "top_cost_days": cost_days,
        "limitations": [
            "Posted commission is a lower bound where recent rows have no commission value.",
            "Modeled FX is not a broker invoice and infers foreign turnover from non-SE ISINs.",
            "The zero-cost path holds executed trades and gross returns constant; it does not yet model frozen starting holdings or missed participation.",
            "Spread, slippage, taxes, and opportunity cost are not removed.",
        ],
    }
    if include_daily:
        result["daily"] = daily_rows
    return result
