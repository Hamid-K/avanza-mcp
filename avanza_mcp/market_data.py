"""Market data helpers: quotes, orderbook metadata, performance/chart payloads."""

import hashlib
import hmac
import json
import math
import re
import secrets
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from avanza.constants import Resolution, TimePeriod

from avanza_mcp import utils
from avanza_mcp.config import (
    ACCOUNT_PERFORMANCE_PERIOD_CHOICES,
    ACCOUNT_PERFORMANCE_PERIOD_MAP,
    COUNTRY_CURRENCY_MAP,
    MARKET_CURRENCY_HINTS,
)
from avanza_mcp.utils import first_unit_text, first_value_number, nested_value

try:
    STOCKHOLM_TIMEZONE = ZoneInfo("Europe/Stockholm")
except ZoneInfoNotFoundError:  # pragma: no cover - platform fallback
    STOCKHOLM_TIMEZONE = timezone.utc

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


def map_instrument_chart_resolution(resolution: Any) -> tuple[str, Resolution]:
    requested = normalize_period_name(resolution or "DAY")
    for candidate in Resolution:
        if requested in {candidate.name, normalize_period_name(candidate.value)}:
            return candidate.value, candidate
    choices = ", ".join(item.value for item in Resolution)
    raise ValueError(f"Invalid chart resolution '{resolution}'. Choices: {choices}")


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
            return datetime.fromtimestamp(timestamp, tz=STOCKHOLM_TIMEZONE).date().isoformat()
        except Exception:
            return str(value)
    text = str(value or "").strip()
    if not text:
        return ""
    if "T" in text:
        return text.split("T", 1)[0]
    return text


def instrument_chart_summary_from_payload(
    payload: Any,
    *,
    orderbook_id: str,
    period: str,
    resolution: str,
) -> dict[str, Any]:
    payload_dict = payload_to_dict(payload)
    raw_points = payload_dict.get("ohlc")
    if not isinstance(raw_points, list):
        raw_points = []

    points: list[dict[str, Any]] = []
    for raw_point in raw_points:
        point = payload_to_dict(raw_point)
        timestamp = point.get("timestamp")
        if timestamp is None:
            continue
        points.append(
            {
                "timestamp": timestamp,
                "date": chart_date_text(timestamp),
                "open": utils.scalar_number(point.get("open")),
                "high": utils.scalar_number(point.get("high")),
                "low": utils.scalar_number(point.get("low")),
                "close": utils.scalar_number(point.get("close")),
                "volume": utils.scalar_number(
                    point.get("totalVolumeTraded")
                    if point.get("totalVolumeTraded") is not None
                    else point.get("volume")
                ),
            }
        )

    metadata = payload_dict.get("metadata")
    metadata_dict = payload_to_dict(metadata)
    actual_resolution = metadata_dict.get("resolution")
    if hasattr(actual_resolution, "value"):
        actual_resolution = actual_resolution.value
    actual_resolution = str(actual_resolution or resolution)

    from_value = payload_dict.get("from")
    if from_value is None:
        from_value = payload_dict.get("from_")

    return {
        "orderbook_id": orderbook_id,
        "period": period,
        "requested_resolution": resolution,
        "resolution": actual_resolution,
        "from": from_value,
        "to": payload_dict.get("to"),
        "previous_closing_price": utils.scalar_number(
            payload_dict.get("previousClosingPrice")
            if payload_dict.get("previousClosingPrice") is not None
            else payload_dict.get("previous_closing_price")
        ),
        "point_count": len(points),
        "points": points,
    }


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


HEURISTIC_CURRENCY_SOURCES = {"MARKET_COUNTRY_INFERENCE", "SWEDISH_MARKET_FALLBACK", "CONSERVATIVE_NON_SWEDISH_FALLBACK"}
KNOWN_CURRENCY_CODES = frozenset(
    {
        "AED", "AUD", "BRL", "CAD", "CHF", "CNY", "CZK", "DKK", "EUR",
        "GBP", "HKD", "HUF", "ILS", "INR", "ISK", "JPY", "KRW", "MXN",
        "NOK", "NZD", "PLN", "RON", "SEK", "SGD", "TRY", "USD", "ZAR",
    }
)
BROKER_VERIFIED_CURRENCY_SOURCES = frozenset(
    {
        "AVANZA_SEARCH_EXPLICIT_CURRENCY",
        "AVANZA_MOVERS_EXPLICIT_CURRENCY",
        "AVANZA_INDEX_EXPLICIT_CURRENCY",
        "MARKET_DATA_EXPLICIT_CURRENCY",
        "MARKET_GUIDE_EXPLICIT_CURRENCY",
        "MARKET_GUIDE_LISTING_CURRENCY",
    }
)
_CURRENCY_EVIDENCE_SECRET = secrets.token_bytes(32)
_CURRENCY_EVIDENCE_TOKEN_KEY = "_currency_evidence_token"
_CURRENCY_EVIDENCE_DIGEST_KEY = "_currency_evidence_payload_sha256"
CURRENCY_SOURCE_PRIORITY = {
    "UNRESOLVED": 0,
    "UNSPECIFIED_METADATA_CURRENCY": 5,
    "MARKET_COUNTRY_INFERENCE": 10,
    "SWEDISH_MARKET_FALLBACK": 10,
    "CONSERVATIVE_NON_SWEDISH_FALLBACK": 10,
    "EXPLICIT_METADATA_CURRENCY": 60,
    "KNOWN_ORDERBOOK_METADATA": 70,
    "AVANZA_SEARCH_EXPLICIT_CURRENCY": 80,
    "AVANZA_MOVERS_EXPLICIT_CURRENCY": 80,
    "AVANZA_INDEX_EXPLICIT_CURRENCY": 80,
    "MARKET_DATA_EXPLICIT_CURRENCY": 90,
    "MARKET_GUIDE_EXPLICIT_CURRENCY": 95,
    "MARKET_GUIDE_LISTING_CURRENCY": 100,
}


_ORDERBOOK_RELATION_KEYS = frozenset(
    {"orderbook", "stock", "instrument", "listing", "quote", "security", "alternativesecurity", "alternativesecurities"}
)
_ORDERBOOK_IDENTITY_MAX_DEPTH = 10
_ORDERBOOK_IDENTITY_MAX_NODES = 256
_FATAL_CURRENCY_PROVENANCE_CONFLICTS = frozenset(
    {
        "ORDERBOOK_ID_MISMATCH",
        "CURRENCY_ORDERBOOK_ID_MISMATCH",
        "ORDERBOOK_IDENTITY_TRAVERSAL_LIMIT",
        "CURRENCY_BRANCH_UNBOUND",
        "CURRENCY_BRANCH_AMBIGUOUS",
        "CURRENCY_CROSS_BRANCH_CONFLICT",
        "CURRENCY_EVIDENCE_NON_JSON_PAYLOAD",
        "CURRENCY_EVIDENCE_SERIALIZATION_FAILED",
    }
)
_JSON_PAYLOAD_MAX_DEPTH = 64
_JSON_PAYLOAD_MAX_NODES = 10_000
_JSON_INTEGER_MAX_DIGITS = 128
_JSON_INTEGER_MAX_ABS = (10 ** _JSON_INTEGER_MAX_DIGITS) - 1


def _strict_json_payload_error(payload: Any) -> dict[str, Any] | None:
    active: set[int] = set()
    node_count = 0

    def visit(value: Any, depth: int) -> dict[str, Any] | None:
        nonlocal node_count
        node_count += 1
        if node_count > _JSON_PAYLOAD_MAX_NODES:
            return {"reason": "NODE_LIMIT", "max_nodes": _JSON_PAYLOAD_MAX_NODES}
        if depth > _JSON_PAYLOAD_MAX_DEPTH:
            return {"reason": "DEPTH_LIMIT", "max_depth": _JSON_PAYLOAD_MAX_DEPTH}
        value_type = type(value)
        if value is None or value_type in {str, bool}:
            return None
        if value_type is int:
            if -_JSON_INTEGER_MAX_ABS <= value <= _JSON_INTEGER_MAX_ABS:
                return None
            return {
                "reason": "INTEGER_MAGNITUDE_LIMIT",
                "max_decimal_digits": _JSON_INTEGER_MAX_DIGITS,
            }
        if value_type is float:
            if math.isfinite(value):
                return None
            return {"reason": "NON_FINITE_NUMBER", "value_type": "float"}
        if value_type not in {dict, list}:
            return {"reason": "UNSUPPORTED_TYPE", "value_type": "unsupported"}
        marker = id(value)
        if marker in active:
            return {"reason": "CYCLE", "value_type": value_type.__name__}
        active.add(marker)
        try:
            if value_type is list:
                for item in value:
                    error = visit(item, depth + 1)
                    if error:
                        return error
                return None
            for key, item in value.items():
                if type(key) is not str:
                    return {"reason": "NON_STRING_OBJECT_KEY", "key_type": type(key).__name__}
                error = visit(item, depth + 1)
                if error:
                    return error
            return None
        finally:
            active.remove(marker)

    try:
        return visit(payload, 0)
    except (Exception, RecursionError):
        return {"reason": "VALIDATION_EXCEPTION"}


def _safe_payload_scalar_text(value: Any) -> str:
    if type(value) is str:
        return value.strip()
    if type(value) is int:
        return str(value)
    if type(value) is float and math.isfinite(value):
        return str(value)
    return ""


def broker_payload_json_error(payload: Any) -> dict[str, Any] | None:
    """Return a bounded strict-JSON validation error without coercing values."""

    return _strict_json_payload_error(payload)


def _payload_key(value: Any) -> str:
    return "".join(character for character in str(value or "").lower() if character.isalnum())


def _broker_payload_identity_scan(payload: Any) -> dict[str, Any]:
    identities: list[dict[str, Any]] = []
    currencies: list[dict[str, Any]] = []
    node_count = 0
    limit_exceeded = False
    cycle_detected = False
    active: set[int] = set()

    def visit(value: Any, path: tuple[str, ...], depth: int) -> None:
        nonlocal node_count, limit_exceeded, cycle_detected
        if not isinstance(value, (dict, list)):
            return
        if depth > _ORDERBOOK_IDENTITY_MAX_DEPTH:
            limit_exceeded = True
            return
        node_count += 1
        if node_count > _ORDERBOOK_IDENTITY_MAX_NODES:
            limit_exceeded = True
            return
        marker = id(value)
        if marker in active:
            cycle_detected = True
            return
        active.add(marker)
        try:
            if isinstance(value, list):
                for item in value:
                    visit(item, path, depth + 1)
                return

            for raw_key, raw_value in value.items():
                key = _payload_key(raw_key)
                if key == "orderbookid" or (key == "id" and (not path or path[-1] == "orderbook")):
                    text = str(raw_value or "").strip()
                    if text:
                        identities.append({"value": text, "path": path, "key": key})
                if key == "currency":
                    text = str(raw_value or "").strip().upper()
                    if text:
                        currencies.append({"value": text, "path": path})

            for raw_key, raw_value in value.items():
                relation = _payload_key(raw_key)
                if relation in _ORDERBOOK_RELATION_KEYS and isinstance(raw_value, (dict, list)):
                    visit(raw_value, (*path, relation), depth + 1)
        finally:
            active.remove(marker)

    visit(payload, (), 0)
    values: list[str] = []
    for identity in identities:
        if identity["value"] not in values:
            values.append(identity["value"])
    return {
        "ids": values,
        "identities": identities,
        "currencies": currencies,
        "limit_exceeded": limit_exceeded,
        "cycle_detected": cycle_detected,
        "node_count": node_count,
    }


def broker_payload_orderbook_ids(payload: Any) -> list[str]:
    """Collect every semantically orderbook identity exposed by a broker row.

    Currency proof is valid only when every discovered identity agrees.  The
    supported broker payloads place orderbook identities at the root, inside
    an orderbook object, or on the stock/instrument/listing relation (including
    an orderbook object nested under those branches).  Generic branch ``id``
    values are deliberately excluded because they may identify an instrument
    or listing rather than its orderbook.
    """

    if _strict_json_payload_error(payload):
        return []
    return list(_broker_payload_identity_scan(payload)["ids"])


def inferred_currency_from_location(metadata: dict[str, Any] | None) -> str | None:
    data = metadata or {}
    country = str(data.get("country_code") or data.get("country") or "").strip().upper()
    market = str(data.get("market") or data.get("market_place") or "").strip().lower()
    if country in COUNTRY_CURRENCY_MAP:
        return COUNTRY_CURRENCY_MAP[country]
    for hint, mapped_currency in MARKET_CURRENCY_HINTS:
        if hint in market:
            return mapped_currency
    return None


def infer_currency_from_metadata(metadata: dict[str, Any] | None) -> str | None:
    data = metadata or {}
    currency = str(data.get("currency") or "").strip().upper()
    return currency if currency in KNOWN_CURRENCY_CODES else inferred_currency_from_location(data)


def broker_verified_currency_metadata(
    *,
    currency: Any,
    source: Any,
    orderbook_id: Any,
    raw_payload: Any,
    observed_orderbook_ids: list[Any] | tuple[Any, ...],
    currency_path: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Mint process-local proof for a currency read from a broker payload."""

    raw_payload_error = _strict_json_payload_error(raw_payload)
    if raw_payload_error is not None:
        safe_source = source.strip().upper() if type(source) is str else "UNRESOLVED"
        return {
            "currency": None,
            "currency_source": safe_source or "UNRESOLVED",
            "currency_verified": False,
            "currency_orderbook_id": None,
            "currency_conflicts": [{
                "type": "CURRENCY_EVIDENCE_NON_JSON_PAYLOAD",
                **raw_payload_error,
                "source": safe_source or "UNRESOLVED",
            }],
        }
    normalized_currency = _safe_payload_scalar_text(currency).upper()
    normalized_source = _safe_payload_scalar_text(source).upper()
    normalized_orderbook_id = _safe_payload_scalar_text(orderbook_id)
    scan = _broker_payload_identity_scan(raw_payload)
    observed: list[str] = []
    for value in [*observed_orderbook_ids, *scan["ids"]]:
        text = _safe_payload_scalar_text(value)
        if text and text not in observed:
            observed.append(text)
    result: dict[str, Any] = {
        "currency": normalized_currency if normalized_currency in KNOWN_CURRENCY_CODES else None,
        "currency_source": normalized_source or "UNRESOLVED",
        "currency_verified": False,
        "currency_orderbook_id": observed[0] if observed else None,
    }
    broker_currency_candidate = (
        normalized_currency in KNOWN_CURRENCY_CODES
        and normalized_source in BROKER_VERIFIED_CURRENCY_SOURCES
    )
    validating_broker_currency = (
        broker_currency_candidate
        and normalized_orderbook_id
    )
    normalized_currency_path = tuple(_payload_key(part) for part in currency_path) if currency_path is not None else None
    currencies_by_path: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for item in scan["currencies"]:
        currencies_by_path.setdefault(item["path"], []).append(item)
    ambiguous_currency_paths = [
        path for path, items in currencies_by_path.items() if len(items) != 1
    ]
    cross_branch_values = sorted({
        item["value"]
        for item in scan["currencies"]
    })
    cross_branch_conflict = len(cross_branch_values) > 1
    all_currency_branches_bound = all(
        any(
            identity["path"][: len(item["path"])] == item["path"]
            or item["path"][: len(identity["path"])] == identity["path"]
            for identity in scan["identities"]
        )
        for item in scan["currencies"]
    )
    path_currency_candidates = [
        item
        for item in scan["currencies"]
        if normalized_currency_path is None or item["path"] == normalized_currency_path
    ]
    branch_ambiguous = bool(ambiguous_currency_paths)
    branch_value_matches = (
        len(path_currency_candidates) == 1
        and path_currency_candidates[0]["value"] == normalized_currency
    )
    branch_bound = False
    if branch_value_matches:
        branch_path = path_currency_candidates[0]["path"]
        branch_bound = any(
            identity["path"][: len(branch_path)] == branch_path
            or branch_path[: len(identity["path"])] == identity["path"]
            for identity in scan["identities"]
        )
    if validating_broker_currency and (not observed or any(value != normalized_orderbook_id for value in observed)):
        result["currency_conflicts"] = [{
            "type": "ORDERBOOK_ID_MISMATCH",
            "requested_orderbook_id": normalized_orderbook_id,
            "payload_orderbook_ids": observed,
            "source": normalized_source or "UNRESOLVED",
        }]
    if validating_broker_currency and (scan["limit_exceeded"] or scan["cycle_detected"]):
        result.setdefault("currency_conflicts", []).append({
            "type": "ORDERBOOK_IDENTITY_TRAVERSAL_LIMIT",
            "max_depth": _ORDERBOOK_IDENTITY_MAX_DEPTH,
            "max_nodes": _ORDERBOOK_IDENTITY_MAX_NODES,
            "source": normalized_source,
        })
    if validating_broker_currency and (branch_ambiguous or not branch_value_matches or not branch_bound):
        result.setdefault("currency_conflicts", []).append({
            "type": "CURRENCY_BRANCH_AMBIGUOUS" if branch_ambiguous else "CURRENCY_BRANCH_UNBOUND",
            "currency": normalized_currency,
            "currency_path": list(normalized_currency_path) if normalized_currency_path is not None else None,
            "ambiguous_currency_paths": [list(path) for path in ambiguous_currency_paths],
            "branch_candidate_count": len(path_currency_candidates),
            "branch_candidate_values": [item["value"] for item in path_currency_candidates],
            "source": normalized_source,
        })
    if validating_broker_currency and cross_branch_conflict:
        result.setdefault("currency_conflicts", []).append({
            "type": "CURRENCY_CROSS_BRANCH_CONFLICT",
            "currency": normalized_currency,
            "currency_path": list(normalized_currency_path) if normalized_currency_path is not None else None,
            "response_currency_values": cross_branch_values,
            "source": normalized_source,
        })
    if validating_broker_currency and scan["currencies"] and not all_currency_branches_bound:
        result.setdefault("currency_conflicts", []).append({
            "type": "CURRENCY_BRANCH_UNBOUND",
            "currency": normalized_currency,
            "currency_path": None,
            "branch_candidate_count": len(scan["currencies"]),
            "source": normalized_source,
        })
    if (
        normalized_currency not in KNOWN_CURRENCY_CODES
        or normalized_source not in BROKER_VERIFIED_CURRENCY_SOURCES
        or not normalized_orderbook_id
        or not observed
        or any(value != normalized_orderbook_id for value in observed)
        or scan["limit_exceeded"]
        or scan["cycle_detected"]
        or branch_ambiguous
        or cross_branch_conflict
        or not all_currency_branches_bound
        or not branch_value_matches
        or not branch_bound
    ):
        return result
    try:
        encoded = json.dumps(
            raw_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        result.setdefault("currency_conflicts", []).append({
            "type": "CURRENCY_EVIDENCE_SERIALIZATION_FAILED",
            "error_type": type(exc).__name__,
            "source": normalized_source,
        })
        return result
    digest = hashlib.sha256(encoded).hexdigest()
    message = "\x00".join(
        (normalized_currency, normalized_source, normalized_orderbook_id, digest)
    ).encode("utf-8")
    token = hmac.new(_CURRENCY_EVIDENCE_SECRET, message, hashlib.sha256).hexdigest()
    result.update(
        {
            "currency_verified": True,
            "currency_orderbook_id": normalized_orderbook_id,
            _CURRENCY_EVIDENCE_DIGEST_KEY: digest,
            _CURRENCY_EVIDENCE_TOKEN_KEY: token,
        }
    )
    return result


def _currency_evidence_token_valid(data: dict[str, Any], currency: str, source: str, orderbook_id: str) -> bool:
    digest = str(data.get(_CURRENCY_EVIDENCE_DIGEST_KEY) or "").strip().lower()
    token = str(data.get(_CURRENCY_EVIDENCE_TOKEN_KEY) or "").strip().lower()
    if len(digest) != 64 or len(token) != 64:
        return False
    message = "\x00".join((currency, source, orderbook_id, digest)).encode("utf-8")
    expected = hmac.new(_CURRENCY_EVIDENCE_SECRET, message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(token, expected)


def currency_metadata_evidence(
    metadata: dict[str, Any] | None,
    *,
    default_explicit_source: str = "UNSPECIFIED_METADATA_CURRENCY",
) -> tuple[str | None, str, bool]:
    data = metadata or {}
    raw_currency = str(data.get("currency") or "").strip().upper()
    currency = raw_currency if raw_currency in KNOWN_CURRENCY_CODES else None
    if currency:
        source = str(data.get("currency_source") or default_explicit_source).strip().upper()
        orderbook_id = str(data.get("orderbook_id") or "").strip()
        evidence_orderbook_id = str(data.get("currency_orderbook_id") or "").strip()
        identity_conflict = any(
            conflict.get("type") in _FATAL_CURRENCY_PROVENANCE_CONFLICTS
            for conflict in (data.get("currency_conflicts") or [])
            if isinstance(conflict, dict)
        )
        verified = (
            data.get("currency_verified") is True
            and source in BROKER_VERIFIED_CURRENCY_SOURCES
            and bool(orderbook_id)
            and evidence_orderbook_id == orderbook_id
            and not identity_conflict
            and _currency_evidence_token_valid(data, currency, source, orderbook_id)
        )
        return currency, source, verified
    if raw_currency:
        return None, "INVALID_CURRENCY_CODE", False
    inferred = inferred_currency_from_location(data)
    if inferred:
        return inferred, "MARKET_COUNTRY_INFERENCE", False
    return None, "UNRESOLVED", False


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
    update_values = updates or {}
    for key in ("name", "ticker", "market", "country_code", "country", "instrument_type", "orderbook_id", "display_symbol"):
        value = update_values.get(key)
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
    base_values = base or {}
    target_orderbook_id = str(
        update_values.get("orderbook_id")
        or base_values.get("orderbook_id")
        or merged.get("orderbook_id")
        or ""
    ).strip()
    base_currency_values = {**base_values, "orderbook_id": target_orderbook_id}
    update_currency_values = {**update_values, "orderbook_id": target_orderbook_id}
    base_currency, base_source, base_verified = currency_metadata_evidence(base_currency_values)
    update_has_currency_context = any(
        update_values.get(key) is not None
        for key in ("currency", "currency_source", "currency_verified", "country_code", "country", "market", "market_place")
    )
    update_currency, update_source, update_verified = currency_metadata_evidence(update_currency_values)
    selected_currency, selected_source, selected_verified = base_currency, base_source, base_verified
    selected_currency_orderbook_id = str(base_values.get("currency_orderbook_id") or "").strip() or None
    selected_currency_token = base_values.get(_CURRENCY_EVIDENCE_TOKEN_KEY)
    selected_currency_digest = base_values.get(_CURRENCY_EVIDENCE_DIGEST_KEY)
    conflicts = [
        item
        for source in (base_values.get("currency_conflicts"), update_values.get("currency_conflicts"))
        if isinstance(source, list)
        for item in source
        if isinstance(item, dict)
    ]
    for values in (base_currency_values, update_currency_values):
        raw_currency = str(values.get("currency") or "").strip().upper()
        source = str(values.get("currency_source") or "UNSPECIFIED").strip().upper()
        verified_flag = values.get("currency_verified")
        evidence_orderbook_id = str(values.get("currency_orderbook_id") or "").strip()
        if raw_currency and raw_currency not in KNOWN_CURRENCY_CODES:
            conflicts.append(
                {
                    "type": "INVALID_CURRENCY_CODE",
                    "currency": raw_currency,
                    "source": source,
                }
            )
        if verified_flag is not None and not isinstance(verified_flag, bool):
            conflicts.append(
                {
                    "type": "INVALID_CURRENCY_VERIFIED_FLAG",
                    "value_type": type(verified_flag).__name__,
                    "source": source,
                }
            )
        if raw_currency in KNOWN_CURRENCY_CODES and verified_flag is True:
            if source not in BROKER_VERIFIED_CURRENCY_SOURCES:
                conflicts.append(
                    {
                        "type": "UNVERIFIED_CURRENCY_SOURCE",
                        "currency": raw_currency,
                        "source": source,
                    }
                )
            if not target_orderbook_id or evidence_orderbook_id != target_orderbook_id:
                conflicts.append(
                    {
                        "type": "CURRENCY_ORDERBOOK_ID_MISMATCH",
                        "currency": raw_currency,
                        "source": source,
                        "requested_orderbook_id": target_orderbook_id or None,
                        "currency_orderbook_id": evidence_orderbook_id or None,
                    }
                )
    if base_currency and update_currency and base_currency != update_currency:
        conflicts.append(
            {
                "type": "CURRENCY_SOURCE_CONFLICT",
                "base_currency": base_currency,
                "base_source": base_source,
                "update_currency": update_currency,
                "update_source": update_source,
            }
        )
    if update_has_currency_context and update_currency:
        base_priority = CURRENCY_SOURCE_PRIORITY.get(base_source, 50 if base_verified else 10)
        update_priority = CURRENCY_SOURCE_PRIORITY.get(update_source, 50 if update_verified else 10)
        base_rank = (1 if base_verified else 0, base_priority)
        update_rank = (1 if update_verified else 0, update_priority)
        if not selected_currency or update_rank >= base_rank:
            selected_currency, selected_source, selected_verified = update_currency, update_source, update_verified
            selected_currency_orderbook_id = str(update_values.get("currency_orderbook_id") or "").strip() or None
            selected_currency_token = update_values.get(_CURRENCY_EVIDENCE_TOKEN_KEY)
            selected_currency_digest = update_values.get(_CURRENCY_EVIDENCE_DIGEST_KEY)
    elif not selected_currency:
        selected_currency, selected_source, selected_verified = currency_metadata_evidence(merged)
    if any(
        conflict.get("type") in _FATAL_CURRENCY_PROVENANCE_CONFLICTS
        for conflict in conflicts
    ):
        selected_verified = False
        selected_currency_token = None
        selected_currency_digest = None
    if selected_currency:
        merged["currency"] = selected_currency
        merged["currency_source"] = selected_source
        merged["currency_verified"] = selected_verified
        merged["currency_orderbook_id"] = selected_currency_orderbook_id
        if selected_verified:
            merged[_CURRENCY_EVIDENCE_TOKEN_KEY] = selected_currency_token
            merged[_CURRENCY_EVIDENCE_DIGEST_KEY] = selected_currency_digest
        else:
            merged.pop(_CURRENCY_EVIDENCE_TOKEN_KEY, None)
            merged.pop(_CURRENCY_EVIDENCE_DIGEST_KEY, None)
    else:
        merged.pop("currency", None)
        merged["currency_source"] = "UNRESOLVED"
        merged["currency_verified"] = False
        merged["currency_orderbook_id"] = None
        merged.pop(_CURRENCY_EVIDENCE_TOKEN_KEY, None)
        merged.pop(_CURRENCY_EVIDENCE_DIGEST_KEY, None)
    if conflicts:
        unique_conflicts: list[dict[str, Any]] = []
        seen_conflicts: set[str] = set()
        for conflict in conflicts:
            marker = repr(sorted(conflict.items()))
            if marker in seen_conflicts:
                continue
            seen_conflicts.add(marker)
            unique_conflicts.append(conflict)
        merged["currency_conflicts"] = unique_conflicts
    else:
        merged.pop("currency_conflicts", None)
    return merged


def demote_stale_currency_metadata(metadata: dict[str, Any], *, reason: str) -> dict[str, Any]:
    """Invalidate cached proof after a due broker revalidation fails."""

    result = dict(metadata)
    currency, source, _ = currency_metadata_evidence(result)
    result.pop(_CURRENCY_EVIDENCE_TOKEN_KEY, None)
    result.pop(_CURRENCY_EVIDENCE_DIGEST_KEY, None)
    result["currency"] = currency
    result["currency_source"] = f"STALE_{source}" if currency and not source.startswith("STALE_") else source
    result["currency_verified"] = False
    conflicts = list(result.get("currency_conflicts") or [])
    conflicts.append({"type": "STALE_CURRENCY_REVALIDATION_FAILED", "reason": str(reason or "UNKNOWN")})
    result["currency_conflicts"] = conflicts
    return merged_orderbook_metadata(result, {})


def metadata_from_market_guide_payload(orderbook_id: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    if broker_payload_json_error(data):
        failure = broker_verified_currency_metadata(
            currency=None,
            source="MARKET_GUIDE_EXPLICIT_CURRENCY",
            orderbook_id=orderbook_id,
            raw_payload=data,
            observed_orderbook_ids=[],
        )
        return {
            "orderbook_id": str(orderbook_id),
            "name": None,
            "ticker": None,
            "display_symbol": "",
            "market": None,
            **failure,
            "country_code": None,
            "instrument_type": None,
        }
    orderbook = data.get("orderbook")
    if not isinstance(orderbook, dict):
        orderbook = {}
    instrument = data.get("instrument")
    if not isinstance(instrument, dict):
        instrument = {}
    listing = data.get("listing")
    if not isinstance(listing, dict):
        listing = {}
    discovered_orderbook_ids = broker_payload_orderbook_ids(data)
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
            or listing.get("tickerSymbol")
            or orderbook.get("symbol")
            or instrument.get("tickerSymbol")
            or ""
        ).strip()
    )
    if not ticker:
        ticker = trailing_parenthesized_symbol(name)
    listing_currency = str(listing.get("currency") or "").strip().upper()
    currency_path: tuple[str, ...] | None = ("listing",) if listing_currency else None
    other_currency = ""
    if not listing_currency:
        for candidate, candidate_path in (
            (data.get("currency"), ()),
            (nested_value(data, "quote", "currency"), ("quote",)),
            (orderbook.get("currency"), ("orderbook",)),
            (instrument.get("currency"), ("instrument",)),
        ):
            text = str(candidate or "").strip().upper()
            if text:
                other_currency = text
                currency_path = candidate_path
                break
    raw_currency = listing_currency or other_currency
    currency = raw_currency if raw_currency in KNOWN_CURRENCY_CODES else None
    currency_source = (
        "MARKET_GUIDE_LISTING_CURRENCY"
        if listing_currency
        else "MARKET_GUIDE_EXPLICIT_CURRENCY" if other_currency else "UNRESOLVED"
    )
    currency_metadata = broker_verified_currency_metadata(
        currency=currency,
        source=currency_source,
        orderbook_id=orderbook_id,
        raw_payload=data,
        observed_orderbook_ids=discovered_orderbook_ids,
        currency_path=currency_path,
    )
    guide_conflicts = list(currency_metadata.get("currency_conflicts") or [])
    if raw_currency and not currency:
        guide_conflicts.append({
            "type": "INVALID_CURRENCY_CODE",
            "currency": raw_currency,
            "source": currency_source,
        })
    return {
        "orderbook_id": orderbook_id,
        "name": name or None,
        "ticker": ticker or None,
        "display_symbol": display_symbol(ticker or None, name or None),
        "market": str(
            data.get("marketPlaceName")
            or data.get("market")
            or listing.get("marketPlaceName")
            or orderbook.get("marketPlaceName")
            or instrument.get("market")
            or ""
        ).strip()
        or None,
        **currency_metadata,
        "currency_conflicts": guide_conflicts,
        "country_code": str(
            data.get("countryCode")
            or data.get("country")
            or data.get("flagCode")
            or listing.get("countryCode")
            or orderbook.get("countryCode")
            or instrument.get("countryCode")
            or ""
        ).strip()
        or None,
        "instrument_type": str(
            data.get("instrumentType")
            or data.get("type")
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
