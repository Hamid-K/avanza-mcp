"""Fail-closed frozen-starting-holdings performance reconstruction."""

from __future__ import annotations

import bisect
import re
from collections import defaultdict
from datetime import date
from typing import Any


def _number(value: Any) -> float:
    if value in (None, "", "None"):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value).replace("\u00a0", "").replace(",", ""))
    return float(match.group(0)) if match else 0.0


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
    dates: list[str] = []
    closes: list[float] = []
    for point_date, close in normalized:
        if close <= 0:
            continue
        if dates and dates[-1] == point_date:
            closes[-1] = close
        else:
            dates.append(point_date)
            closes.append(close)
    return dates, closes


def _close_on_or_before(dates: list[str], closes: list[float], target_date: str) -> float | None:
    index = bisect.bisect_right(dates, target_date) - 1
    return closes[index] if index >= 0 else None


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
) -> dict[str, Any]:
    start_text = start_date.isoformat()
    points = sorted(
        [point for point in performance_points if str(point.get("date") or "")[:10] >= start_text],
        key=lambda point: str(point.get("date") or ""),
    )
    if len(points) < 2:
        raise ValueError("At least two performance points are required for frozen attribution.")
    performance_dates = [str(point.get("date"))[:10] for point in points]
    end_text = performance_dates[-1]
    start_point = points[0]
    start_account_value = _performance_value(start_point, "account_value")
    if start_account_value <= 0:
        raise ValueError("The frozen attribution start account value must be positive.")

    current_volume_by_orderbook: defaultdict[str, float] = defaultdict(float)
    labels: dict[str, str] = {}
    for row in portfolio_rows:
        orderbook_id = _row_orderbook_id(row)
        if not orderbook_id:
            continue
        current_volume_by_orderbook[orderbook_id] += _row_volume(row)
        labels.setdefault(orderbook_id, _row_stock(row) or orderbook_id)

    net_trades_by_orderbook: defaultdict[str, float] = defaultdict(float)
    issues: list[dict[str, Any]] = []
    for row in transaction_rows:
        row_date = _row_date(row)
        if not row_date or not start_text < row_date <= end_text:
            continue
        orderbook_id = _row_orderbook_id(row)
        row_type = _row_type(row)
        if not orderbook_id:
            issues.append({"date": row_date, "type": row_type, "stock": _row_stock(row), "issue": "missing_orderbook_id"})
            continue
        labels.setdefault(orderbook_id, _row_stock(row) or orderbook_id)
        volume = _row_volume(row)
        if row_type == "BUY":
            net_trades_by_orderbook[orderbook_id] += volume
        elif row_type == "SELL":
            net_trades_by_orderbook[orderbook_id] -= volume

    start_volumes: dict[str, float] = {}
    for orderbook_id in sorted(set(current_volume_by_orderbook) | set(net_trades_by_orderbook)):
        start_volume = current_volume_by_orderbook[orderbook_id] - net_trades_by_orderbook[orderbook_id]
        if start_volume < -0.0001:
            issues.append({"orderbook_id": orderbook_id, "issue": "negative_reconstructed_start_volume", "volume": start_volume})
            continue
        if start_volume > 0.0001:
            start_volumes[orderbook_id] = start_volume

    instrument_rows: list[dict[str, Any]] = []
    start_holdings_value = 0.0
    for orderbook_id, volume in sorted(start_volumes.items()):
        chart_points = chart_points_by_orderbook.get(orderbook_id)
        if not chart_points:
            issues.append({"orderbook_id": orderbook_id, "stock": labels.get(orderbook_id, orderbook_id), "issue": "missing_chart_history"})
            continue
        chart_dates, chart_closes = _close_map(chart_points)
        start_close = _close_on_or_before(chart_dates, chart_closes, start_text)
        if start_close is None:
            issues.append({"orderbook_id": orderbook_id, "stock": labels.get(orderbook_id, orderbook_id), "issue": "missing_start_price"})
            continue
        start_value = volume * start_close
        start_holdings_value += start_value
        instrument_rows.append(
            {
                "orderbook_id": orderbook_id,
                "stock": labels.get(orderbook_id, orderbook_id),
                "start_volume": volume,
                "start_close": start_close,
                "chart_dates": chart_dates,
                "chart_closes": chart_closes,
            }
        )

    cash_events_by_date: defaultdict[str, dict[str, float]] = defaultdict(lambda: {"external_flow": 0.0, "dividends": 0.0})
    for row in cash_event_rows:
        row_date = _row_date(row)
        if not row_date or not start_text < row_date <= end_text:
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
    for point in points[1:]:
        point_date = str(point.get("date"))[:10]
        events = cash_events_by_date[point_date]
        cumulative_external_flow += events["external_flow"]
        cumulative_dividends += events["dividends"]
        holdings_value = 0.0
        for instrument in instrument_rows:
            close = _close_on_or_before(instrument["chart_dates"], instrument["chart_closes"], point_date)
            if close is None:
                issues.append({"date": point_date, "orderbook_id": instrument["orderbook_id"], "issue": "missing_daily_price"})
                continue
            holdings_value += instrument["start_volume"] * close
        frozen_value = frozen_start_cash + cumulative_external_flow + cumulative_dividends + holdings_value
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
    actual_return = ((1.0 + actual_end_relative / 100.0) / (1.0 + actual_start_relative / 100.0) - 1.0) * 100.0
    result: dict[str, Any] = {
        "status": "COMPLETE" if not issues else "BLOCKED_INCOMPLETE_HISTORY",
        "account_id": account_id,
        "window": {"start": start_text, "end": end_text, "performance_points": len(points)},
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
            "frozen_minus_actual_percentage_points": (frozen_index - 1.0) * 100.0 - actual_return,
        },
        "issues": issues,
        "trade_authority": "NONE_READ_ONLY_ATTRIBUTION",
    }
    if include_daily:
        result["daily"] = daily_rows
    return result
