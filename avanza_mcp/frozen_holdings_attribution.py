"""Fail-closed frozen-starting-holdings performance reconstruction."""

from __future__ import annotations

import bisect
import math
import re
from collections import defaultdict
from datetime import date
from typing import Any


def _number(value: Any) -> float:
    if value in (None, "", "None"):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("\u00a0", " ").replace(",", "").strip()
    match = re.fullmatch(r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)(?:\s*[A-Za-z%]+)?", text)
    return float(match.group(1)) if match else float("nan")


def _row_date(row: dict[str, Any]) -> str:
    return str(row.get("Trade Date") or row.get("date") or "").strip()[:10]


def _row_type(row: dict[str, Any]) -> str:
    return str(row.get("Type") or row.get("type") or "").strip().upper()


def _row_orderbook_id(row: dict[str, Any]) -> str:
    return str(row.get("Order Book ID") or row.get("orderbook_id") or row.get("orderBookId") or "").strip()


def _row_stock(row: dict[str, Any]) -> str:
    return str(row.get("Stock") or row.get("stock") or row.get("instrumentName") or "").strip()


def _row_volume(row: dict[str, Any]) -> float:
    return abs(_number(row.get("Volume") or row.get("volume")))


def _performance_value(point: dict[str, Any], key: str) -> float:
    value = point.get(key)
    if isinstance(value, dict):
        value = value.get("value")
    return _number(value)


def _close_map(points: list[dict[str, Any]]) -> tuple[list[str], list[float]]:
    normalized = [
        (str(point.get("date") or "")[:10], _number(point.get("close")))
        for point in points
        if point.get("date") and point.get("close") is not None
    ]
    normalized.sort(key=lambda item: item[0])
    if len(normalized) != len(points):
        raise ValueError("missing_price_date_or_value")
    dates: list[str] = []
    closes: list[float] = []
    for point_date, close in normalized:
        date.fromisoformat(point_date)
        if not math.isfinite(close) or close <= 0:
            raise ValueError("invalid_price")
        if dates and dates[-1] == point_date:
            raise ValueError("duplicate_price_date")
        else:
            dates.append(point_date)
            closes.append(close)
    return dates, closes


def _close_on_or_before(dates: list[str], closes: list[float], target_date: str) -> float | None:
    index = bisect.bisect_right(dates, target_date) - 1
    if index < 0 or (date.fromisoformat(target_date) - date.fromisoformat(dates[index])).days > 7:
        return None
    return closes[index]


def _validate_ohlc(points: list[dict[str, Any]]) -> None:
    for point in points:
        if not point.get("date") or point.get("close") is None:
            raise ValueError("missing_price_date_or_value")
        values = {key: _number(point[key]) for key in ("open", "high", "low", "close") if point.get(key) is not None}
        if any(not math.isfinite(value) or value <= 0 for value in values.values()):
            raise ValueError("invalid_ohlc")
        if "high" in values and values["close"] > values["high"]:
            raise ValueError("invalid_ohlc")
        if "low" in values and values["close"] < values["low"]:
            raise ValueError("invalid_ohlc")
        if "high" in values and "low" in values and values["high"] < values["low"]:
            raise ValueError("invalid_ohlc")


def suppress_frozen_benchmark(result: dict[str, Any]) -> None:
    """Never publish a partial or dimensionally invalid benchmark as a return."""
    result["status"] = "BLOCKED_INCOMPLETE_HISTORY"
    for key in ("start_holdings_value_sek", "start_cash_residual_sek"):
        result["reconstruction"][key] = None
    for key in ("frozen_starting_holdings_percent", "frozen_minus_actual_percentage_points"):
        result["returns"][key] = None
    if "daily" in result:
        result["daily"] = []


def _external_cash_event(row: dict[str, Any]) -> tuple[float, float, str | None]:
    event_type = _row_type(row)
    amount = _number(row.get("Amount") or row.get("amount"))
    if event_type == "DEPOSIT":
        return abs(amount), 0.0, None
    if event_type == "WITHDRAW":
        return -abs(amount), 0.0, None
    if event_type == "DIVIDEND":
        return 0.0, amount, None
    if abs(amount) > 0.005:
        return 0.0, 0.0, f"Unsupported non-zero cash event type: {event_type or 'EMPTY'}"
    return 0.0, 0.0, None


def build_frozen_holdings_attribution(
    *,
    account_id: str,
    start_date: date,
    performance_points: list[dict[str, Any]],
    portfolio_rows: list[dict[str, Any]],
    transaction_rows: list[dict[str, Any]],
    cash_event_rows: list[dict[str, Any]],
    chart_points_by_orderbook: dict[str, list[dict[str, Any]]],
    include_daily: bool,
    portfolio_as_of: date | None = None,
    transaction_history_through: date | None = None,
    price_currency_by_orderbook: dict[str, str] | None = None,
    fx_history_by_currency: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Value native prices in SEK using dated SEK-per-unit FX, never today's FX.

    The current inventory must be bridged through its own date, not merely the
    last performance point. Dividends remain observed-account cash, not modeled
    frozen-share entitlement; this limitation is explicit in the output.
    """
    start_text = start_date.isoformat()
    points = sorted(
        [point for point in performance_points if str(point.get("date") or "")[:10] >= start_text],
        key=lambda point: str(point.get("date") or ""),
    )
    if len(points) < 2:
        raise ValueError("At least two performance points are required for frozen attribution.")
    performance_dates = [str(point.get("date"))[:10] for point in points]
    for point_date in performance_dates:
        date.fromisoformat(point_date)
    if performance_dates[0] != start_text or len(set(performance_dates)) != len(points):
        raise ValueError("Performance dates must be unique and begin on start_date.")
    end_text = performance_dates[-1]
    start_point = points[0]
    start_account_value = _performance_value(start_point, "account_value")
    if not math.isfinite(start_account_value) or start_account_value <= 0:
        raise ValueError("The frozen attribution start account value must be positive.")

    issues: list[dict[str, Any]] = []
    if portfolio_as_of is None or portfolio_as_of.isoformat() < end_text:
        issues.append({"issue": "missing_or_invalid_inventory_as_of"})
    if transaction_history_through is None or portfolio_as_of is None or transaction_history_through < portfolio_as_of:
        issues.append({"issue": "inventory_history_horizon_not_verified"})
    inventory_end = portfolio_as_of.isoformat() if portfolio_as_of else end_text
    for point in points:
        account_value = point.get("account_value")
        if not isinstance(account_value, dict) or account_value.get("unit") != "SEK":
            issues.append({"date": point.get("date"), "issue": "account_value_currency_not_verified_sek"})
        if not math.isfinite(_performance_value(point, "account_value")):
            issues.append({"date": point.get("date"), "issue": "non_finite_account_value"})

    current_volume_by_orderbook: defaultdict[str, float] = defaultdict(float)
    labels: dict[str, str] = {}
    for row in portfolio_rows:
        orderbook_id = _row_orderbook_id(row)
        if not orderbook_id:
            issues.append({"issue": "missing_position_orderbook_id"})
            continue
        volume = _number(row.get("Volume") if "Volume" in row else row.get("volume"))
        if not math.isfinite(volume) or volume < 0:
            issues.append({"orderbook_id": orderbook_id, "issue": "invalid_current_volume"})
            continue
        current_volume_by_orderbook[orderbook_id] += volume
        labels.setdefault(orderbook_id, _row_stock(row) or orderbook_id)

    net_trades_by_orderbook: defaultdict[str, float] = defaultdict(float)
    for row in transaction_rows:
        row_date = _row_date(row)
        try:
            date.fromisoformat(row_date)
        except ValueError:
            issues.append({"issue": "invalid_transaction_date"})
            continue
        if not start_text < row_date <= inventory_end:
            continue
        orderbook_id = _row_orderbook_id(row)
        row_type = _row_type(row)
        if not orderbook_id:
            issues.append({"date": row_date, "type": row_type, "stock": _row_stock(row), "issue": "missing_orderbook_id"})
            continue
        labels.setdefault(orderbook_id, _row_stock(row) or orderbook_id)
        volume = _row_volume(row)
        if not math.isfinite(volume) or volume <= 0:
            issues.append({"orderbook_id": orderbook_id, "issue": "invalid_trade_volume"})
            continue
        if row_type == "BUY":
            net_trades_by_orderbook[orderbook_id] += volume
        elif row_type == "SELL":
            net_trades_by_orderbook[orderbook_id] -= volume
        else:
            issues.append({"orderbook_id": orderbook_id, "issue": "unsupported_inventory_event"})

    start_volumes: dict[str, float] = {}
    for orderbook_id in sorted(set(current_volume_by_orderbook) | set(net_trades_by_orderbook)):
        start_volume = current_volume_by_orderbook[orderbook_id] - net_trades_by_orderbook[orderbook_id]
        if start_volume < -0.0001:
            issues.append({"orderbook_id": orderbook_id, "issue": "negative_reconstructed_start_volume", "volume": start_volume})
            continue
        if start_volume > 0.0001:
            start_volumes[orderbook_id] = start_volume

    instrument_rows: list[dict[str, Any]] = []
    currencies = price_currency_by_orderbook or {}
    fx_history = fx_history_by_currency or {}
    fx_maps: dict[str, dict[str, float]] = {}
    for currency, rates in fx_history.items():
        try:
            rate_dates, rate_values = _close_map(rates)
            fx_maps[currency] = dict(zip(rate_dates, rate_values))
        except (ValueError, TypeError):
            issues.append({"currency": currency, "issue": "invalid_historical_fx"})

    def rate_for(currency: str, point_date: str, orderbook_id: str) -> float | None:
        rate = 1.0 if currency == "SEK" else fx_maps.get(currency, {}).get(point_date)
        if rate is None:
            issues.append({"date": point_date, "orderbook_id": orderbook_id, "currency": currency, "issue": "missing_historical_fx"})
        return rate

    start_holdings_value = 0.0
    for orderbook_id, volume in sorted(start_volumes.items()):
        chart_points = chart_points_by_orderbook.get(orderbook_id)
        if not chart_points:
            issues.append({"orderbook_id": orderbook_id, "stock": labels.get(orderbook_id, orderbook_id), "issue": "missing_chart_history"})
            continue
        try:
            _validate_ohlc(chart_points)
            chart_dates, chart_closes = _close_map(chart_points)
        except (ValueError, TypeError):
            issues.append({"orderbook_id": orderbook_id, "issue": "invalid_chart_history"})
            continue
        start_close = _close_on_or_before(chart_dates, chart_closes, start_text)
        if start_close is None:
            issues.append({"orderbook_id": orderbook_id, "stock": labels.get(orderbook_id, orderbook_id), "issue": "missing_start_price"})
            continue
        currency = str(currencies.get(orderbook_id) or "").strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", currency):
            issues.append({"orderbook_id": orderbook_id, "issue": "missing_price_currency"})
            continue
        start_rate = rate_for(currency, start_text, orderbook_id)
        if start_rate is None:
            continue
        start_value = volume * start_close * start_rate
        if not math.isfinite(start_value):
            issues.append({"orderbook_id": orderbook_id, "issue": "non_finite_start_valuation"})
            continue
        start_holdings_value += start_value
        instrument_rows.append(
            {
                "orderbook_id": orderbook_id,
                "stock": labels.get(orderbook_id, orderbook_id),
                "start_volume": volume,
                "start_close": start_close,
                "currency": currency,
                "chart_dates": chart_dates,
                "chart_closes": chart_closes,
            }
        )

    cash_events_by_date: defaultdict[str, dict[str, float]] = defaultdict(lambda: {"external_flow": 0.0, "dividends": 0.0})
    for row in cash_event_rows:
        row_date = _row_date(row)
        try:
            date.fromisoformat(row_date)
        except ValueError:
            issues.append({"issue": "invalid_cash_event_date"})
            continue
        if start_text < row_date <= inventory_end and _row_type(row) == "UNKNOWN" and (
            _row_volume(row) or _number(row.get("volumeFactor"))
        ):
            issues.append({"date": row_date, "issue": "unsupported_inventory_event"})
        if not start_text < row_date <= end_text:
            continue
        amount = row.get("Amount") if "Amount" in row else row.get("amount")
        if isinstance(amount, str) and not amount.strip().endswith("SEK"):
            issues.append({"date": row_date, "issue": "cash_event_currency_not_verified_sek"})
            continue
        if not math.isfinite(_number(amount)):
            issues.append({"date": row_date, "issue": "invalid_cash_event_amount"})
            continue
        external_flow, dividends, issue = _external_cash_event(row)
        if issue:
            issues.append({"date": row_date, "type": _row_type(row), "issue": issue})
            continue
        cash_events_by_date[row_date]["external_flow"] += external_flow
        cash_events_by_date[row_date]["dividends"] += dividends

    frozen_start_cash = start_account_value - start_holdings_value
    if frozen_start_cash < -0.01:
        issues.append({"issue": "negative_start_cash_residual", "value": frozen_start_cash})

    daily_rows: list[dict[str, Any]] = []
    previous_value = start_account_value
    cumulative_external_flow = 0.0
    cumulative_dividends = 0.0
    frozen_index = 1.0
    for point_index, point in enumerate(points[1:], start=1):
        point_date = str(point.get("date"))[:10]
        events = {"external_flow": 0.0, "dividends": 0.0}
        for event_date in cash_events_by_date:
            if performance_dates[point_index - 1] < event_date <= point_date:
                for key in events:
                    events[key] += cash_events_by_date[event_date][key]
        cumulative_external_flow += events["external_flow"]
        cumulative_dividends += events["dividends"]
        holdings_value = 0.0
        for instrument in instrument_rows:
            close = _close_on_or_before(instrument["chart_dates"], instrument["chart_closes"], point_date)
            if close is None:
                issues.append({"date": point_date, "orderbook_id": instrument["orderbook_id"], "issue": "missing_daily_price"})
                continue
            rate = rate_for(instrument["currency"], point_date, instrument["orderbook_id"])
            if rate is not None:
                holdings_value += instrument["start_volume"] * close * rate
        frozen_value = frozen_start_cash + cumulative_external_flow + cumulative_dividends + holdings_value
        if not math.isfinite(frozen_value):
            issues.append({"date": point_date, "issue": "non_finite_frozen_valuation"})
            continue
        if previous_value <= 0:
            issues.append({"date": point_date, "issue": "non_positive_previous_frozen_value"})
            continue
        daily_return = (frozen_value - events["external_flow"]) / previous_value - 1.0
        frozen_index *= 1.0 + daily_return
        daily_rows.append(
            {
                "date": point_date,
                "frozen_value_sek": frozen_value,
                "holdings_value_sek": holdings_value,
                "external_flow_sek": events["external_flow"],
                "dividends_sek": events["dividends"],
                "daily_return": daily_return,
            }
        )
        previous_value = frozen_value

    actual_start_relative = _performance_value(start_point, "development_relative")
    actual_end_relative = _performance_value(points[-1], "development_relative")
    actual_return = None
    relative_units_valid = all(
        isinstance(point.get("development_relative"), dict)
        and point["development_relative"].get("unit") == "%"
        and point["development_relative"].get("value") is not None
        for point in (start_point, points[-1])
    )
    if relative_units_valid and math.isfinite(actual_start_relative) and math.isfinite(actual_end_relative) and actual_start_relative > -100:
        actual_return = ((1.0 + actual_end_relative / 100.0) / (1.0 + actual_start_relative / 100.0) - 1.0) * 100.0
    else:
        issues.append({"issue": "invalid_actual_return"})
    result: dict[str, Any] = {
        "status": "COMPLETE" if not issues else "BLOCKED_INCOMPLETE_HISTORY",
        "account_id": account_id,
        "window": {
            "start": start_text,
            "end": end_text,
            "performance_points": len(points),
            "inventory_as_of": portfolio_as_of.isoformat() if portfolio_as_of else None,
            "history_through": transaction_history_through.isoformat() if transaction_history_through else None,
        },
        "reconstruction": {
            "current_position_rows": len(portfolio_rows),
            "reconstructed_start_instrument_count": len(start_volumes),
            "priced_start_instrument_count": len(instrument_rows),
            "start_account_value_sek": start_account_value,
            "start_holdings_value_sek": start_holdings_value,
            "start_cash_residual_sek": frozen_start_cash,
        },
        "returns": {
            "actual_cash_flow_adjusted_percent": actual_return,
            "frozen_starting_holdings_percent": (frozen_index - 1.0) * 100.0,
            "frozen_minus_actual_percentage_points": (frozen_index - 1.0) * 100.0 - actual_return if actual_return is not None else None,
        },
        "issues": issues,
        "trade_authority": "NONE_READ_ONLY_ATTRIBUTION",
        "valuation_contract": "DATED_SEK_PER_NATIVE_CURRENCY_UNIT",
        "limitations": [
            "Observed account dividends, not frozen-share dividend entitlement.",
            "Prices carried forward at most seven calendar days.",
            "Unsupported corporate actions block reconstruction.",
        ],
    }
    if include_daily:
        result["daily"] = daily_rows
    if issues:
        suppress_frozen_benchmark(result)
        if any(item["issue"] == "account_value_currency_not_verified_sek" for item in issues):
            result["reconstruction"]["start_account_value_sek"] = None
    return result
