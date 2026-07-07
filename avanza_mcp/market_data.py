"""Market data helpers: quotes, orderbook metadata, performance/chart payloads."""

import re
from datetime import datetime, timezone
from typing import Any

from avanza.constants import TimePeriod

from avanza_mcp import utils
from avanza_mcp.config import (
    ACCOUNT_PERFORMANCE_PERIOD_CHOICES,
    ACCOUNT_PERFORMANCE_PERIOD_MAP,
    COUNTRY_CURRENCY_MAP,
    MARKET_CURRENCY_HINTS,
)
from avanza_mcp.utils import first_unit_text, first_value_number, nested_value

def trailing_parenthesized_symbol(text: str | None) -> str:
    source = str(text or "").strip()
    if not source:
        return ""
    match = re.search(r"\(([^()]+)\)\s*$", source)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1).strip()).upper()


def normalize_symbol_candidate(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    extracted = trailing_parenthesized_symbol(text)
    if extracted:
        text = extracted
    if ":" in text:
        text = text.split(":")[-1]
    text = re.sub(r"\s+", " ", text.strip()).upper()
    if not text:
        return ""
    if len(text) > 18:
        return ""
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9 .:/-]*", text):
        return ""
    words = [token for token in text.split(" ") if token]
    if len(words) > 2:
        return ""
    if all(len(token) == 1 for token in words):
        return ""
    return text


def display_symbol(ticker: str | None, name: str | None = None) -> str | None:
    name_text = str(name or "").strip()
    paren_symbol = trailing_parenthesized_symbol(name_text)
    if paren_symbol:
        return paren_symbol
    ticker_text = normalize_symbol_candidate(ticker)
    if ticker_text:
        return ticker_text
    return name_text or None


def normalize_period_name(value: Any) -> str:
    return str(value or "").strip().upper().replace("-", "_").replace(" ", "_")


def map_account_performance_period(period: Any) -> tuple[str, TimePeriod]:
    requested = normalize_period_name(period or "SINCE_START")
    mapped = ACCOUNT_PERFORMANCE_PERIOD_MAP.get(requested)
    if mapped is None:
        choices = ", ".join(ACCOUNT_PERFORMANCE_PERIOD_CHOICES)
        raise ValueError(f"Invalid period '{period}'. Choices: {choices}")
    canonical = requested if requested in ACCOUNT_PERFORMANCE_PERIOD_CHOICES else next(
        key for key, value in ACCOUNT_PERFORMANCE_PERIOD_MAP.items() if value == mapped and key in ACCOUNT_PERFORMANCE_PERIOD_CHOICES
    )
    return canonical, mapped


def payload_to_dict(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if hasattr(payload, "model_dump"):
        try:
            dumped = payload.model_dump()
        except Exception:
            dumped = None
        if isinstance(dumped, dict):
            return dumped
    if hasattr(payload, "dict"):
        try:
            dumped = payload.dict()
        except Exception:
            dumped = None
        if isinstance(dumped, dict):
            return dumped
    return {}


def payload_to_json_safe(payload: Any) -> Any:
    if isinstance(payload, (dict, list, str, int, float, bool)) or payload is None:
        return payload
    if hasattr(payload, "model_dump"):
        try:
            return payload_to_json_safe(payload.model_dump())
        except Exception:
            pass
    if hasattr(payload, "dict"):
        try:
            return payload_to_json_safe(payload.dict())
        except Exception:
            pass
    return str(payload)


def chart_date_text(value: Any) -> str:
    if isinstance(value, (int, float)):
        try:
            timestamp = float(value)
            if timestamp > 10_000_000_000:
                timestamp /= 1000.0
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat()
        except Exception:
            return str(value)
    text = str(value or "").strip()
    if not text:
        return ""
    if "T" in text:
        return text.split("T", 1)[0]
    return text


def normalize_relative_unit(unit: Any) -> str:
    normalized = str(unit or "").strip().upper()
    if not normalized:
        return "%"
    if normalized in {"PERCENT", "PERCENTAGE", "PROCENT", "PROCENT"}:
        return "%"
    if normalized == "%":
        return "%"
    return str(unit)


def extract_performance_series(
    payload: dict[str, Any], key: str, default_unit: str
) -> list[dict[str, Any]]:
    series = payload.get(key)
    if not isinstance(series, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in series:
        if not isinstance(item, dict):
            continue
        performance = item.get("performance")
        if not isinstance(performance, dict):
            performance = item
        value = utils.scalar_number(performance.get("value"))
        timestamp = item.get("timestamp")
        if timestamp is None:
            timestamp = item.get("time")
        if timestamp is None:
            timestamp = item.get("date")
        unit = performance.get("unit")
        if unit is None:
            unit = item.get("unit")
        if default_unit == "%":
            unit = normalize_relative_unit(unit or default_unit)
        else:
            unit = str(unit or default_unit)
        rows.append(
            {
                "timestamp": timestamp,
                "date": chart_date_text(timestamp),
                "value": value,
                "unit": unit,
            }
        )
    return rows


def chart_points_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    absolute_series = extract_performance_series(payload, "absoluteSeries", "SEK")
    relative_series = extract_performance_series(payload, "relativeSeries", "%")
    value_series = extract_performance_series(payload, "valueSeries", "SEK")
    if absolute_series or relative_series or value_series:
        merged: dict[str, dict[str, Any]] = {}
        order_index: dict[str, tuple[int, str]] = {}

        def ensure_point(item: dict[str, Any], sequence_index: int) -> dict[str, Any]:
            timestamp = item.get("timestamp")
            key = str(timestamp)
            if key not in merged:
                merged[key] = {
                    "timestamp": timestamp,
                    "date": item.get("date", ""),
                    "development_absolute": {"value": None, "unit": "SEK"},
                    "development_relative": {"value": None, "unit": "%"},
                    "account_value": {"value": None, "unit": "SEK"},
                }
                order_index[key] = (sequence_index, key)
            return merged[key]

        for idx, item in enumerate(absolute_series):
            point = ensure_point(item, idx)
            point["development_absolute"] = {
                "value": item.get("value"),
                "unit": str(item.get("unit") or "SEK"),
            }
        for idx, item in enumerate(relative_series):
            point = ensure_point(item, idx)
            point["development_relative"] = {
                "value": item.get("value"),
                "unit": normalize_relative_unit(item.get("unit") or "%"),
            }
        for idx, item in enumerate(value_series):
            point = ensure_point(item, idx)
            point["account_value"] = {
                "value": item.get("value"),
                "unit": str(item.get("unit") or "SEK"),
            }

        def sort_key(point: dict[str, Any], fallback_index: int) -> tuple[int, float, int]:
            timestamp = point.get("timestamp")
            if isinstance(timestamp, (int, float)):
                return (0, float(timestamp), fallback_index)
            parsed = utils.scalar_number(timestamp)
            if parsed is not None:
                return (0, parsed, fallback_index)
            return (1, float(fallback_index), fallback_index)

        rows = list(merged.values())
        rows.sort(key=lambda point: sort_key(point, order_index.get(str(point.get("timestamp")), (0, ""))[0]))
        return rows

    containers: list[Any] = []

    for key in ("chart_points", "chartPoints", "chartData", "points", "data", "values"):
        value = payload.get(key)
        if isinstance(value, list):
            containers.append(value)
        elif isinstance(value, dict):
            nested = value.get("data")
            if isinstance(nested, list):
                containers.append(nested)

    series = payload.get("series")
    if isinstance(series, list):
        for entry in series:
            if isinstance(entry, dict):
                nested = entry.get("data")
                if isinstance(nested, list):
                    containers.append(nested)

    rows: list[dict[str, Any]] = []
    for container in containers:
        for point in container:
            if isinstance(point, dict):
                point_date = chart_date_text(
                    point.get("date")
                    or point.get("x")
                    or point.get("timestamp")
                    or point.get("time")
                )
                point_value = first_value_number(
                    point,
                    (
                        ("value",),
                        ("y",),
                        ("close",),
                        ("latest",),
                        ("amount",),
                        ("development", "absolute"),
                    ),
                )
                point_abs = first_value_number(
                    point,
                    (
                        ("development_absolute",),
                        ("developmentAbsolute",),
                        ("absolute",),
                        ("development", "absolute"),
                    ),
                )
                point_rel = first_value_number(
                    point,
                    (
                        ("development_relative",),
                        ("developmentRelative",),
                        ("relative",),
                        ("development", "relative"),
                    ),
                )
                if point_value is None and point_abs is None and point_rel is None:
                    continue
                rows.append(
                    {
                        "date": point_date,
                        "value": point_value,
                        "development_absolute": point_abs,
                        "development_relative": point_rel,
                    }
                )
            elif isinstance(point, (list, tuple)) and len(point) >= 2:
                point_date = chart_date_text(point[0])
                point_value = utils.scalar_number(point[1])
                if point_value is None:
                    continue
                rows.append(
                    {
                        "date": point_date,
                        "value": point_value,
                        "development_absolute": None,
                        "development_relative": None,
                    }
                )
    return rows


def account_performance_summary_from_payload(
    payload: Any,
    account_id: str,
    requested_period: str,
    raw_period: str,
) -> dict[str, Any]:
    payload_dict = payload_to_dict(payload)
    chart_points = chart_points_from_payload(payload_dict)

    absolute_series = extract_performance_series(payload_dict, "absoluteSeries", "SEK")
    relative_series = extract_performance_series(payload_dict, "relativeSeries", "%")

    absolute_value = absolute_series[-1]["value"] if absolute_series else None
    absolute_unit = str(absolute_series[-1]["unit"] or "SEK") if absolute_series else "SEK"
    relative_value = relative_series[-1]["value"] if relative_series else None
    relative_unit = normalize_relative_unit(relative_series[-1]["unit"] if relative_series else "%")

    if absolute_value is None:
        absolute_value = first_value_number(
        payload_dict,
        (
            ("development", "absolute"),
            ("developmentAbsolute",),
            ("absoluteDevelopment",),
            ("performance", "absolute"),
        ),
        )
        absolute_unit = first_unit_text(
            payload_dict,
            (
                ("development", "absolute"),
                ("developmentAbsolute",),
                ("absoluteDevelopment",),
                ("performance", "absolute"),
            ),
            "SEK",
        )
    if relative_value is None:
        relative_value = first_value_number(
            payload_dict,
            (
                ("development", "relative"),
                ("developmentRelative",),
                ("relativeDevelopment",),
                ("performance", "relative"),
            ),
        )
        relative_unit = normalize_relative_unit(
            first_unit_text(
                payload_dict,
                (
                    ("development", "relative"),
                    ("developmentRelative",),
                    ("relativeDevelopment",),
                    ("performance", "relative"),
                ),
                "%",
            )
        )

    if (absolute_value is None or relative_value is None) and len(chart_points) >= 2:
        first = chart_points[0].get("value")
        last = chart_points[-1].get("value")
        if isinstance(first, (int, float)) and isinstance(last, (int, float)):
            best_effort_abs = float(last) - float(first)
            best_effort_rel = (best_effort_abs / float(first) * 100.0) if float(first) != 0 else None
            if absolute_value is None:
                absolute_value = best_effort_abs
            if relative_value is None and best_effort_rel is not None:
                relative_value = best_effort_rel

    deposits_value = first_value_number(payload_dict, (("deposits",), ("deposit",), ("transactions", "deposits")))
    deposits_unit = first_unit_text(payload_dict, (("deposits",), ("deposit",), ("transactions", "deposits")), "SEK")
    withdrawals_value = first_value_number(payload_dict, (("withdrawals",), ("withdraw",), ("transactions", "withdrawals")))
    withdrawals_unit = first_unit_text(payload_dict, (("withdrawals",), ("withdraw",), ("transactions", "withdrawals")), "SEK")
    dividends_value = first_value_number(payload_dict, (("dividends",), ("dividend",), ("transactions", "dividends")))
    dividends_unit = first_unit_text(payload_dict, (("dividends",), ("dividend",), ("transactions", "dividends")), "SEK")

    return {
        "account_id": account_id,
        "period": requested_period,
        "raw_period": raw_period,
        "development_absolute": {"value": absolute_value, "unit": absolute_unit},
        "development_relative": {"value": relative_value, "unit": relative_unit},
        "chart_points": chart_points,
        "deposits": {"value": deposits_value, "unit": deposits_unit} if deposits_value is not None else None,
        "withdrawals": {"value": withdrawals_value, "unit": withdrawals_unit} if withdrawals_value is not None else None,
        "dividends": {"value": dividends_value, "unit": dividends_unit} if dividends_value is not None else None,
        "raw": payload_to_json_safe(payload),
    }


def first_numeric(payload: Any, paths: tuple[tuple[str, ...], ...]) -> float | None:
    for path in paths:
        current = payload
        for key in path:
            if isinstance(current, dict):
                current = current.get(key)
            else:
                current = None
            if current is None:
                break
        value = utils.scalar_number(current)
        if value is not None:
            return value
    return None


def market_quote_last(payload: dict[str, Any]) -> float | None:
    return first_numeric(
        payload,
        (
            ("quote", "last"),
            ("quote", "lastPrice"),
            ("lastPrice",),
            ("last",),
            ("price",),
            ("orderBook", "quote", "last"),
            ("orderbook", "quote", "last"),
        ),
    )


def market_quote_change_percent(payload: dict[str, Any]) -> float | None:
    return first_numeric(
        payload,
        (
            ("quote", "changePercent"),
            ("changePercent",),
            ("quote", "change", "percent"),
            ("change", "percent"),
        ),
    )


def market_quote_first_number(payload: dict[str, Any], paths: tuple[tuple[str, ...], ...]) -> float | None:
    return first_numeric(payload, paths)


def market_quote_first_text(payload: dict[str, Any], paths: tuple[tuple[str, ...], ...]) -> str:
    for path in paths:
        current: Any = payload
        for key in path:
            if isinstance(current, dict):
                current = current.get(key)
            else:
                current = None
            if current is None:
                break
        if current is None:
            continue
        text = str(current).strip()
        if text:
            return text
    return ""


def iso_from_any_timestamp(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
        except Exception:
            parsed_num = utils.scalar_number(text)
            if parsed_num is None:
                return text
            value = parsed_num
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 10_000_000_000:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")
        except Exception:
            return None
    return None


def quote_age_ms_from_timestamp(timestamp_iso: str | None) -> int | None:
    if not timestamp_iso:
        return None
    try:
        parsed = datetime.fromisoformat(timestamp_iso.replace("Z", "+00:00"))
    except Exception:
        return None
    now = datetime.now(timezone.utc)
    age = (now - parsed).total_seconds() * 1000.0
    return int(age) if age >= 0 else 0


def orderbook_quote_row(
    order_book_id: str,
    payload: dict[str, Any] | None,
    *,
    fallback_name: str = "",
    fallback_ticker: str = "",
    fallback_market: str = "",
    fallback_currency: str = "",
    error: str = "",
) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    quote = data.get("quote")
    if not isinstance(quote, dict):
        quote = data

    last = market_quote_first_number(data, (("quote", "last"), ("last",)))
    bid = market_quote_first_number(data, (("quote", "buy"), ("buy",)))
    ask = market_quote_first_number(data, (("quote", "sell"), ("sell",)))
    spread_absolute = (ask - bid) if ask is not None and bid is not None else None
    spread_percent = ((spread_absolute / bid) * 100.0) if spread_absolute is not None and bid not in (None, 0) else None

    timestamp_iso = iso_from_any_timestamp(
        market_quote_first_number(data, (("quote", "updated"), ("updated",), ("quote", "timeOfLast"), ("timeOfLast",)))
        or market_quote_first_text(data, (("quote", "updated"), ("updated",), ("quote", "timeOfLast"), ("timeOfLast",)))
    )
    if not timestamp_iso:
        timestamp_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    ticker_value = normalize_symbol_candidate(
        market_quote_first_text(data, (("ticker",), ("symbol",), ("orderbook", "symbol")))
        or fallback_ticker
    )
    return {
        "orderbook_id": str(order_book_id),
        "name": market_quote_first_text(data, (("name",), ("orderbook", "name"), ("instrument", "name"))) or fallback_name or None,
        "ticker": ticker_value or None,
        "market": market_quote_first_text(data, (("market",), ("marketPlace",), ("marketPlaceName",))) or fallback_market or None,
        "currency": (
            str(market_quote_first_text(data, (("quote", "currency"), ("currency",))) or fallback_currency or "").strip().upper()
            or None
        ),
        "timestamp": timestamp_iso,
        "last": last,
        "bid": bid,
        "ask": ask,
        "spread_absolute": spread_absolute,
        "spread_percent": spread_percent,
        "day_change_percent": market_quote_first_number(data, (("quote", "changePercent"), ("changePercent",))),
        "day_volume": market_quote_first_number(data, (("quote", "totalVolumeTraded"), ("totalVolumeTraded",))),
        "total_value_traded": market_quote_first_number(data, (("quote", "totalValueTraded"), ("totalValueTraded",))),
        "turnover": market_quote_first_number(data, (("quote", "turnover"), ("turnover",))),
        "high": market_quote_first_number(data, (("quote", "highest"), ("highest",))),
        "low": market_quote_first_number(data, (("quote", "lowest"), ("lowest",))),
        "open": market_quote_first_number(data, (("quote", "open"), ("open",))),
        "previous_close": market_quote_first_number(data, (("quote", "previousClose"), ("previousClose",))),
        "quote_age_ms": quote_age_ms_from_timestamp(timestamp_iso),
        "trading_status": market_quote_first_text(data, (("quote", "tradingStatus"), ("tradingStatus",), ("status",))) or None,
        "error": error or None,
    }


def infer_currency_from_metadata(metadata: dict[str, Any] | None) -> str | None:
    data = metadata or {}
    currency = str(data.get("currency") or "").strip().upper()
    if currency:
        return currency
    country = str(data.get("country_code") or data.get("country") or "").strip().upper()
    market = str(data.get("market") or data.get("market_place") or "").strip().lower()
    if country in COUNTRY_CURRENCY_MAP:
        return COUNTRY_CURRENCY_MAP[country]
    for hint, mapped_currency in MARKET_CURRENCY_HINTS:
        if hint in market:
            return mapped_currency
    return None


def infer_country_from_metadata(metadata: dict[str, Any] | None) -> str | None:
    data = metadata or {}
    country = str(data.get("country_code") or data.get("country") or "").strip().upper()
    if country:
        return country
    market = str(data.get("market") or "").strip().lower()
    if "stockholm" in market or "xsto" in market:
        return "SE"
    if "nasdaq" in market or "nyse" in market:
        return "US"
    if "helsinki" in market or "xhel" in market:
        return "FI"
    if "copenhagen" in market or "xcse" in market:
        return "DK"
    if "oslo" in market or "xosl" in market:
        return "NO"
    if "london" in market or "xlon" in market:
        return "GB"
    return None


def merged_orderbook_metadata(base: dict[str, Any] | None = None, updates: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = dict(base or {})
    for key in ("name", "ticker", "market", "currency", "country_code", "country", "instrument_type", "orderbook_id", "display_symbol"):
        value = (updates or {}).get(key)
        if value is None:
            continue
        text = str(value).strip() if not isinstance(value, (int, float, bool)) else str(value)
        if text:
            merged[key] = text
    if not merged.get("display_symbol"):
        merged["display_symbol"] = display_symbol(str(merged.get("ticker") or ""), str(merged.get("name") or ""))
    if not merged.get("country"):
        merged["country"] = merged.get("country_code")
    if not merged.get("country_code"):
        merged["country_code"] = merged.get("country")
    inferred_country = infer_country_from_metadata(merged)
    if inferred_country:
        merged["country"] = merged.get("country") or inferred_country
        merged["country_code"] = merged.get("country_code") or inferred_country
    if not merged.get("instrument_type"):
        market = str(merged.get("market") or "").strip().lower()
        if market:
            merged["instrument_type"] = "STOCK"
    inferred_currency = infer_currency_from_metadata(merged)
    if inferred_currency and not merged.get("currency"):
        merged["currency"] = inferred_currency
    return merged


def metadata_from_market_guide_payload(orderbook_id: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    orderbook = data.get("orderbook")
    if not isinstance(orderbook, dict):
        orderbook = {}
    instrument = data.get("instrument")
    if not isinstance(instrument, dict):
        instrument = {}
    name = str(
        data.get("name")
        or orderbook.get("name")
        or instrument.get("name")
        or nested_value(data, "stock", "name")
        or ""
    ).strip()
    ticker = normalize_symbol_candidate(
        str(
            data.get("tickerSymbol")
            or data.get("symbol")
            or orderbook.get("symbol")
            or instrument.get("tickerSymbol")
            or ""
        ).strip()
    )
    if not ticker:
        ticker = trailing_parenthesized_symbol(name)
    return {
        "orderbook_id": orderbook_id,
        "name": name or None,
        "ticker": ticker or None,
        "display_symbol": display_symbol(ticker or None, name or None),
        "market": str(
            data.get("marketPlaceName")
            or data.get("market")
            or orderbook.get("marketPlaceName")
            or instrument.get("market")
            or ""
        ).strip()
        or None,
        "currency": str(
            data.get("currency")
            or nested_value(data, "quote", "currency")
            or orderbook.get("currency")
            or instrument.get("currency")
            or ""
        ).strip()
        or None,
        "country_code": str(
            data.get("countryCode")
            or data.get("country")
            or data.get("flagCode")
            or orderbook.get("countryCode")
            or instrument.get("countryCode")
            or ""
        ).strip()
        or None,
        "instrument_type": str(
            data.get("instrumentType")
            or instrument.get("instrumentType")
            or instrument.get("type")
            or orderbook.get("instrumentType")
            or ""
        ).strip()
        or None,
    }


def order_account_id(item: dict[str, Any], fallback: str | None = None) -> str:
    account = item.get("account") if isinstance(item.get("account"), dict) else {}
    return str(
        account.get("id")
        or item.get("accountId")
        or item.get("account_id")
        or fallback
        or ""
    )


def order_stock_name(item: dict[str, Any]) -> str:
    orderbook = item.get("orderbook") or item.get("instrument") or {}
    if isinstance(orderbook, dict):
        return str(orderbook.get("name") or "")
    return ""
