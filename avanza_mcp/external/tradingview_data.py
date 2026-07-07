"""TradingView scanner queries, symbol/preopen/heatmap/watchlist snapshots."""

import html
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

from avanza_mcp import utils
from avanza_mcp.config import (
    EXTERNAL_HTTP_USER_AGENT,
    TRADINGVIEW_BROWSER_PROFILE_DIR,
    TRADINGVIEW_ANALYTICS_FIELDS,
    TRADINGVIEW_CRYPTO_EXCHANGE_FALLBACKS,
    TRADINGVIEW_CRYPTO_MARKET,
    TRADINGVIEW_CRYPTO_QUOTE_SUFFIXES,
    TRADINGVIEW_DEEP_ANALYTICS_CANDIDATE_FIELDS,
    TRADINGVIEW_DEFAULT_EXCHANGE,
    TRADINGVIEW_DEFAULT_MARKET,
    TRADINGVIEW_EXCHANGE_MARKET_HINTS,
    TRADINGVIEW_FIAT_CODES,
    TRADINGVIEW_FOREX_EXCHANGE_FALLBACKS,
    TRADINGVIEW_FOREX_MARKET,
    TRADINGVIEW_HEATMAP_FIELDS,
    TRADINGVIEW_MARKET_EXCHANGE_FALLBACKS,
    TRADINGVIEW_MARKET_FALLBACKS,
    TRADINGVIEW_NUMERIC_ID_PATTERN,
    TRADINGVIEW_OTC_EXCHANGES,
    TRADINGVIEW_PROFILE_URL_TEMPLATE,
    TRADINGVIEW_RECOMMENDATION_THRESHOLDS,
    TRADINGVIEW_SCANNER_URL_TEMPLATE,
    TRADINGVIEW_UNKNOWN_FIELD_PATTERN,
    TRADINGVIEW_US_EQUITY_EXCHANGES,
    TRADINGVIEW_WATCHLIST_ID_PATTERN,
    TRADINGVIEW_WATCHLIST_ROW_LIMIT,
    TRADINGVIEW_WATCHLIST_SYMBOL_PATTERN,
)
from avanza_mcp.external import http as ext_http
from avanza_mcp.external.http import append_cookie_header

TRADINGVIEW_UNSUPPORTED_FIELD_CACHE: dict[str, set[str]] = {}
TRADINGVIEW_UNSUPPORTED_FIELD_CACHE_LOCK = threading.Lock()

def recommendation_label(value: Any) -> str:
    score = utils.scalar_number(value)
    if score is None:
        return "Unknown"
    for threshold, label in TRADINGVIEW_RECOMMENDATION_THRESHOLDS:
        if score <= threshold:
            return label
    return "Strong Buy"


def normalize_tv_symbol(symbol: str, exchange: str = TRADINGVIEW_DEFAULT_EXCHANGE) -> str:
    text = str(symbol or "").strip().upper()
    if not text:
        raise ValueError("symbol is required.")
    if ":" in text:
        exchange_part, symbol_part = text.split(":", 1)
        normalized_exchange = re.sub(r"[^A-Z0-9_]", "", exchange_part.strip().upper()) or exchange.strip().upper()
        normalized_symbol = symbol_part.strip().upper()
        if not normalized_symbol:
            raise ValueError("symbol is required.")
        return f"{normalized_exchange}:{normalized_symbol}"
    return f"{exchange}:{text}"


def tradingview_market_hint_for_exchange(exchange: str) -> str | None:
    exchange_text = str(exchange or "").strip().upper()
    if not exchange_text:
        return None
    if exchange_text in TRADINGVIEW_CRYPTO_EXCHANGE_FALLBACKS:
        return TRADINGVIEW_CRYPTO_MARKET
    if exchange_text in TRADINGVIEW_FOREX_EXCHANGE_FALLBACKS:
        return TRADINGVIEW_FOREX_MARKET
    for prefix, market in TRADINGVIEW_EXCHANGE_MARKET_HINTS.items():
        if exchange_text == prefix or exchange_text.startswith(f"{prefix}_") or exchange_text.startswith(f"{prefix}."):
            return market
    return None


def tv_symbol_core(symbol: str) -> str:
    text = str(symbol or "").strip().upper()
    if ":" in text:
        text = text.split(":", 1)[1]
    return re.sub(r"[\s/_-]+", "", text)


def is_probable_forex_pair(symbol_core: str) -> bool:
    if len(symbol_core) != 6 or not symbol_core.isalpha():
        return False
    base = symbol_core[:3]
    quote = symbol_core[3:]
    return base in TRADINGVIEW_FIAT_CODES and quote in TRADINGVIEW_FIAT_CODES


def is_probable_crypto_pair(symbol_core: str) -> bool:
    if not re.fullmatch(r"[A-Z0-9]{5,16}", symbol_core):
        return False
    if is_probable_forex_pair(symbol_core):
        return False
    return any(symbol_core.endswith(suffix) and len(symbol_core) > len(suffix) for suffix in TRADINGVIEW_CRYPTO_QUOTE_SUFFIXES)


def tradingview_symbol_attempts(
    symbol: str,
    *,
    exchange: str = TRADINGVIEW_DEFAULT_EXCHANGE,
    market: str = TRADINGVIEW_DEFAULT_MARKET,
) -> list[tuple[str, str]]:
    symbol_text = str(symbol or "").strip().upper()
    exchange_text = str(exchange or TRADINGVIEW_DEFAULT_EXCHANGE).strip().upper()
    market_text = str(market or TRADINGVIEW_DEFAULT_MARKET).strip().lower()
    if not symbol_text:
        raise ValueError("symbol is required.")

    attempts: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def push(symbol_value: str, market_value: str) -> None:
        key = (symbol_value.strip().upper(), market_value.strip().lower())
        if not key[0] or not key[1] or key in seen:
            return
        seen.add(key)
        attempts.append(key)

    if ":" in symbol_text:
        normalized_qualified = normalize_tv_symbol(symbol_text, exchange_text)
        qualified_exchange, qualified_symbol = normalized_qualified.split(":", 1)
        inferred_market = tradingview_market_hint_for_exchange(qualified_exchange)
        market_candidates = unique_strings(
            [
                market_text,
                inferred_market or "",
                *TRADINGVIEW_MARKET_FALLBACKS,
            ]
        )
        if not market_candidates:
            market_candidates = [market_text]

        core = tv_symbol_core(qualified_symbol)
        symbol_variants = [normalized_qualified]
        if core and core != qualified_symbol:
            symbol_variants.append(f"{qualified_exchange}:{core}")

        for candidate_market in market_candidates:
            for variant in symbol_variants:
                push(variant, candidate_market)

        if not is_probable_crypto_pair(core) and not is_probable_forex_pair(core):
            fallback_exchanges = unique_strings(
                [
                    qualified_exchange,
                    *(TRADINGVIEW_MARKET_EXCHANGE_FALLBACKS.get(inferred_market or market_text, ())),
                    *(TRADINGVIEW_MARKET_EXCHANGE_FALLBACKS.get(market_text, ())),
                ]
            )
            if core:
                for candidate_exchange in fallback_exchanges:
                    for candidate_market in market_candidates:
                        push(f"{candidate_exchange}:{core}", candidate_market)
        return attempts

    core = tv_symbol_core(symbol_text)
    push(normalize_tv_symbol(core or symbol_text, exchange_text), market_text)

    if is_probable_crypto_pair(core):
        pairs = [core]
        if core.endswith("USD"):
            pairs.append(core[:-3] + "USDT")
        if core.endswith("USDT"):
            pairs.append(core[:-4] + "USD")
        for pair in unique_strings(pairs):
            for candidate_exchange in unique_strings([exchange_text, *TRADINGVIEW_CRYPTO_EXCHANGE_FALLBACKS]):
                push(f"{candidate_exchange}:{pair}", TRADINGVIEW_CRYPTO_MARKET)

    if is_probable_forex_pair(core):
        for candidate_exchange in unique_strings([exchange_text, *TRADINGVIEW_FOREX_EXCHANGE_FALLBACKS]):
            push(f"{candidate_exchange}:{core}", TRADINGVIEW_FOREX_MARKET)

    return attempts


def should_retry_tv_scan_error(exc: Exception) -> bool:
    if isinstance(exc, HTTPError):
        return exc.code in {400, 404, 405}
    message = str(exc).lower()
    if "http error 400" in message or "http error 404" in message:
        return True
    if "http error 405" in message:
        return True
    if "tradingview scanner error" in message and "bad request" in message:
        return True
    if "scanner error: bad request" in message:
        return True
    if "returned no rows" in message:
        return True
    return False


def tv_row_to_dict(columns: list[str], row: dict[str, Any]) -> dict[str, Any]:
    values = row.get("d", [])
    mapped: dict[str, Any] = {}
    for index, column in enumerate(columns):
        mapped[column] = values[index] if index < len(values) else None
    mapped["ticker"] = str(row.get("s", ""))
    return mapped


def unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique


def tradingview_scan(
    *,
    symbols: list[str],
    columns: list[str],
    market: str = TRADINGVIEW_DEFAULT_MARKET,
    limit: int = 25,
    sort_by: str | None = None,
    descending: bool = True,
    cookie: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "symbols": {"tickers": symbols, "query": {"types": []}},
        "columns": columns,
    }
    if not symbols:
        payload["symbols"] = {"tickers": [], "query": {"types": ["stock"]}}
        payload["range"] = [0, max(0, limit - 1)]
    if sort_by:
        payload["sort"] = {"sortBy": sort_by, "sortOrder": "desc" if descending else "asc"}
    url = TRADINGVIEW_SCANNER_URL_TEMPLATE.format(market=market)
    headers = append_cookie_header({"Content-Type": "application/json"}, cookie)
    try:
        data = ext_http.external_fetch_json(url, method="POST", headers=headers, payload=payload)
    except HTTPError as exc:
        parsed_payload: dict[str, Any] | None = None
        try:
            parsed_text = exc.read().decode("utf-8", errors="replace")
            parsed = json.loads(parsed_text) if parsed_text else None
            if isinstance(parsed, dict):
                parsed_payload = parsed
        except Exception:
            parsed_payload = None
        if exc.code in {400, 404, 405} and isinstance(parsed_payload, dict):
            data = parsed_payload
        else:
            raise
    error_text = str(data.get("error", "") or "").strip() if isinstance(data, dict) else ""
    rows_raw = data.get("data", [])
    if not isinstance(rows_raw, list):
        rows_raw = []
    rows = [tv_row_to_dict(columns, row) for row in rows_raw if isinstance(row, dict)]
    return {
        "market": market,
        "columns": columns,
        "rows": rows,
        "total_count": int(data.get("totalCount", len(rows))),
        "error": error_text or None,
    }


def tradingview_symbol_profile_html(symbol: str, cookie: str = "") -> str:
    slug = symbol.replace(":", "-").upper()
    headers = append_cookie_header({"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}, cookie)
    return ext_http.external_fetch_text(TRADINGVIEW_PROFILE_URL_TEMPLATE.format(symbol_slug=slug), headers=headers)


def tradingview_watchlist_id_from_input(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = TRADINGVIEW_WATCHLIST_ID_PATTERN.search(text)
    if match:
        return str(match.group(1))
    return text


def tradingview_numeric_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = TRADINGVIEW_NUMERIC_ID_PATTERN.search(text)
    if match:
        return str(match.group(1))
    return ""


def tradingview_watchlist_entry_matches_target(entry: dict[str, Any], target_list_id: str, target_list_name: str) -> bool:
    if not isinstance(entry, dict):
        return False
    if target_list_id:
        direct_candidates = [
            str(entry.get("id", "") or ""),
            str(entry.get("title", "") or ""),
            str(entry.get("name", "") or ""),
            str(entry.get("raw_label", "") or ""),
        ]
        for candidate in direct_candidates:
            if candidate == target_list_id:
                return True
            if tradingview_watchlist_id_from_input(candidate) == target_list_id:
                return True
            if tradingview_numeric_id(candidate) == target_list_id:
                return True
    if target_list_name:
        normalized_name = target_list_name.strip().lower()
        name = str(entry.get("name", "") or "").strip().lower()
        raw_label = str(entry.get("raw_label", "") or "").strip().lower()
        if name == normalized_name or raw_label.startswith(normalized_name):
            return True
    return False


def tradingview_scan_with_field_fallback(
    *,
    symbols: list[str],
    fields: list[str],
    market: str = TRADINGVIEW_DEFAULT_MARKET,
    limit: int | None = None,
    sort_by: str | None = None,
    descending: bool = True,
    cookie: str = "",
) -> tuple[dict[str, Any], list[str]]:
    cache_key = str(market or TRADINGVIEW_DEFAULT_MARKET).strip().lower() or TRADINGVIEW_DEFAULT_MARKET
    with TRADINGVIEW_UNSUPPORTED_FIELD_CACHE_LOCK:
        cached_unsupported = set(TRADINGVIEW_UNSUPPORTED_FIELD_CACHE.get(cache_key, set()))
    unsupported: list[str] = [field for field in unique_strings(fields) if field in cached_unsupported]
    columns = [field for field in unique_strings(fields) if field not in cached_unsupported]
    attempts = 0
    while attempts < 80 and columns:
        attempts += 1
        snapshot = tradingview_scan(
            symbols=symbols,
            columns=columns,
            market=market,
            limit=max(1, int(limit if limit is not None else len(symbols))),
            sort_by=sort_by,
            descending=descending,
            cookie=cookie,
        )
        error_text = str(snapshot.get("error", "") or "")
        if not error_text:
            return snapshot, unsupported
        unknown_match = TRADINGVIEW_UNKNOWN_FIELD_PATTERN.search(error_text)
        if not unknown_match:
            raise RuntimeError(f"TradingView scanner error: {error_text}")
        unknown_field = str(unknown_match.group(1))
        if unknown_field not in columns:
            raise RuntimeError(f"TradingView scanner rejected unsupported field '{unknown_field}'.")
        columns = [column for column in columns if column != unknown_field]
        unsupported.append(unknown_field)
        with TRADINGVIEW_UNSUPPORTED_FIELD_CACHE_LOCK:
            TRADINGVIEW_UNSUPPORTED_FIELD_CACHE.setdefault(cache_key, set()).add(unknown_field)
    raise RuntimeError("TradingView scanner could not return data with the requested field set.")


def tradingview_json_ld_objects_from_html(html_text: str) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for match in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html_text, re.IGNORECASE | re.DOTALL):
        raw = html.unescape(str(match.group(1) or "").strip())
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        if isinstance(payload, dict):
            objects.append(payload)
        elif isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    objects.append(item)
    return objects


def tradingview_init_data_value_from_html(html_text: str, key: str) -> Any:
    safe_key = re.escape(key)
    pattern = re.compile(rf"window\.initData\.{safe_key}\s*=\s*(.*?);(?:\n|$)", re.DOTALL)
    match = pattern.search(html_text)
    if not match:
        return None
    raw = str(match.group(1) or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return raw


def tradingview_symbol_profile_metadata_from_html(symbol: str, html_text: str) -> dict[str, Any]:
    title_match = re.search(r"<title>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
    title = html.unescape(str(title_match.group(1) or "").strip()) if title_match else ""
    title = re.sub(r"\s+", " ", title)
    meta_description_match = re.search(
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
        html_text,
        re.IGNORECASE | re.DOTALL,
    )
    description = html.unescape(str(meta_description_match.group(1) or "").strip()) if meta_description_match else ""
    description = re.sub(r"\s+", " ", description)
    canonical_match = re.search(
        r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\'](.*?)["\']',
        html_text,
        re.IGNORECASE | re.DOTALL,
    )
    canonical_url = str(canonical_match.group(1) or "").strip() if canonical_match else ""
    json_ld_objects = tradingview_json_ld_objects_from_html(html_text)
    symbol_info = tradingview_init_data_value_from_html(html_text, "symbolInfo")
    return {
        "symbol": symbol,
        "title": title,
        "description": description,
        "canonical_url": canonical_url,
        "symbol_info": symbol_info if isinstance(symbol_info, dict) else {},
        "json_ld": json_ld_objects,
    }


def tradingview_extract_symbol_candidates_from_html(html_text: str, max_symbols: int = 120) -> list[str]:
    seen: set[str] = set()
    symbols: list[str] = []
    for match in TRADINGVIEW_WATCHLIST_SYMBOL_PATTERN.findall(html_text):
        normalized = str(match).strip().upper().replace("-", ":", 1)
        if ":" not in normalized:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        symbols.append(normalized)
        if len(symbols) >= max_symbols:
            break
    return symbols


def tradingview_symbol_full_snapshot(
    symbol: str,
    *,
    market: str = TRADINGVIEW_DEFAULT_MARKET,
    exchange: str = TRADINGVIEW_DEFAULT_EXCHANGE,
    cookie: str = "",
    include_profile: bool = True,
) -> dict[str, Any]:
    attempts = tradingview_symbol_attempts(symbol, exchange=exchange, market=market)
    errors: list[str] = []
    for attempt_index, (attempt_symbol, attempt_market) in enumerate(attempts):
        try:
            scan, unsupported_fields = tradingview_scan_with_field_fallback(
                symbols=[attempt_symbol],
                fields=TRADINGVIEW_DEEP_ANALYTICS_CANDIDATE_FIELDS,
                market=attempt_market,
                cookie=cookie,
            )
            rows = scan.get("rows", [])
            if not rows:
                raise ValueError(f"TradingView returned no rows for {attempt_symbol}.")
            analytics = rows[0]
            technical_score = analytics.get("Recommend.All")
            moving_average_score = analytics.get("Recommend.MA")
            oscillator_score = analytics.get("Recommend.Other")
            profile_metadata: dict[str, Any] = {}
            symbol_candidates: list[str] = []
            source = "tradingview-scanner"
            if include_profile:
                profile_html = tradingview_symbol_profile_html(attempt_symbol, cookie=cookie)
                profile_metadata = tradingview_symbol_profile_metadata_from_html(attempt_symbol, profile_html)
                symbol_candidates = tradingview_extract_symbol_candidates_from_html(profile_html, max_symbols=120)
                source = "tradingview-scanner+profile-html"
            return {
                "symbol": attempt_symbol,
                "market": attempt_market,
                "requested_symbol": str(symbol or "").strip().upper(),
                "requested_market": str(market or "").strip().lower(),
                "requested_exchange": str(exchange or "").strip().upper(),
                "fallback_used": attempt_index > 0,
                "analytics": analytics,
                "technicals": {
                    "overall_score": technical_score,
                    "overall_label": recommendation_label(technical_score),
                    "moving_average_score": moving_average_score,
                    "moving_average_label": recommendation_label(moving_average_score),
                    "oscillator_score": oscillator_score,
                    "oscillator_label": recommendation_label(oscillator_score),
                },
                "profile": profile_metadata,
                "related_symbols": symbol_candidates,
                "unsupported_fields": unsupported_fields,
                "field_count": len(analytics),
                "source": source,
                "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "unsafe_for_execution": False,
            }
        except Exception as exc:
            errors.append(f"{attempt_symbol} @ {attempt_market}: {exc}")
            if not should_retry_tv_scan_error(exc) or attempt_index >= len(attempts) - 1:
                break
    raise RuntimeError(
        "TradingView analytics lookup failed after fallback attempts. "
        f"requested={symbol!r}, market={market!r}, exchange={exchange!r}. "
        f"errors={errors[:5]}"
    )


def tradingview_symbol_full_snapshot_from_row(
    row: dict[str, Any],
    *,
    symbol: str,
    requested_symbol: str,
    requested_market: str,
    requested_exchange: str,
    fallback_used: bool = False,
    unsupported_fields: list[str] | None = None,
) -> dict[str, Any]:
    technical_score = row.get("Recommend.All")
    moving_average_score = row.get("Recommend.MA")
    oscillator_score = row.get("Recommend.Other")
    return {
        "symbol": symbol,
        "market": requested_market,
        "requested_symbol": str(requested_symbol or "").strip().upper(),
        "requested_market": str(requested_market or "").strip().lower(),
        "requested_exchange": str(requested_exchange or "").strip().upper(),
        "fallback_used": fallback_used,
        "analytics": row,
        "technicals": {
            "overall_score": technical_score,
            "overall_label": recommendation_label(technical_score),
            "moving_average_score": moving_average_score,
            "moving_average_label": recommendation_label(moving_average_score),
            "oscillator_score": oscillator_score,
            "oscillator_label": recommendation_label(oscillator_score),
        },
        "profile": {},
        "related_symbols": [],
        "unsupported_fields": list(unsupported_fields or []),
        "field_count": len(row),
        "source": "tradingview-scanner",
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "unsafe_for_execution": False,
    }


def tradingview_symbol_snapshot(
    symbol: str,
    *,
    market: str = TRADINGVIEW_DEFAULT_MARKET,
    exchange: str = TRADINGVIEW_DEFAULT_EXCHANGE,
    cookie: str = "",
) -> dict[str, Any]:
    attempts = tradingview_symbol_attempts(symbol, exchange=exchange, market=market)
    errors: list[str] = []
    for attempt_index, (attempt_symbol, attempt_market) in enumerate(attempts):
        try:
            scan, unsupported_fields = tradingview_scan_with_field_fallback(
                symbols=[attempt_symbol],
                fields=TRADINGVIEW_ANALYTICS_FIELDS,
                market=attempt_market,
                cookie=cookie,
            )
            rows = scan["rows"]
            if not rows:
                raise ValueError(f"TradingView returned no rows for {attempt_symbol}.")
            row = rows[0]
            technical_score = row.get("Recommend.All")
            moving_average_score = row.get("Recommend.MA")
            oscillator_score = row.get("Recommend.Other")
            return {
                "symbol": attempt_symbol,
                "market": attempt_market,
                "requested_symbol": str(symbol or "").strip().upper(),
                "requested_market": str(market or "").strip().lower(),
                "requested_exchange": str(exchange or "").strip().upper(),
                "fallback_used": attempt_index > 0,
                "analytics": row,
                "technicals": {
                    "overall_score": technical_score,
                    "overall_label": recommendation_label(technical_score),
                    "moving_average_score": moving_average_score,
                    "moving_average_label": recommendation_label(moving_average_score),
                    "oscillator_score": oscillator_score,
                    "oscillator_label": recommendation_label(oscillator_score),
                },
                "unsupported_fields": unsupported_fields,
                "source": "tradingview-scanner",
                "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "unsafe_for_execution": False,
            }
        except Exception as exc:
            errors.append(f"{attempt_symbol} @ {attempt_market}: {exc}")
            if not should_retry_tv_scan_error(exc) or attempt_index >= len(attempts) - 1:
                break
    raise RuntimeError(
        "TradingView analytics lookup failed after fallback attempts. "
        f"requested={symbol!r}, market={market!r}, exchange={exchange!r}. "
        f"errors={errors[:5]}"
    )


def tradingview_numeric_field(row: dict[str, Any], key: str) -> float | None:
    return utils.scalar_number(row.get(key))


def tradingview_premarket_change(row: dict[str, Any]) -> tuple[float | None, float | None]:
    regular_close = tradingview_numeric_field(row, "close")
    premarket_price = tradingview_numeric_field(row, "premarket_close")
    if regular_close is None or regular_close == 0 or premarket_price is None:
        return None, None
    change_abs = premarket_price - regular_close
    return change_abs, (change_abs / regular_close) * 100.0


def tradingview_market_state(market: str, exchange: str) -> tuple[str, str]:
    exchange_text = str(exchange or "").strip().upper()
    if str(market or "").strip().lower() != "america" and exchange_text not in TRADINGVIEW_US_EQUITY_EXCHANGES:
        return "unknown", ""
    try:
        now = datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return "unknown", "America/New_York"
    minutes = now.hour * 60 + now.minute
    if 4 * 60 <= minutes < 9 * 60 + 30:
        return "pre_market", "America/New_York"
    if 9 * 60 + 30 <= minutes < 16 * 60:
        return "regular", "America/New_York"
    if 16 * 60 <= minutes < 20 * 60:
        return "post_market", "America/New_York"
    return "closed", "America/New_York"


def tradingview_freshness_warning(row: dict[str, Any]) -> str | None:
    update_mode = str(row.get("update_mode") or "").strip()
    warnings: list[str] = []
    if "delayed" in update_mode.lower():
        warnings.append(f"TradingView update mode is {update_mode}.")
    if tradingview_numeric_field(row, "premarket_close") is None and tradingview_numeric_field(row, "postmarket_close") is None:
        warnings.append("Extended-hours price is unavailable in this TradingView payload.")
    return " ".join(warnings) if warnings else None


def tradingview_preopen_from_full_snapshot(snapshot: dict[str, Any], *, authenticated: bool) -> dict[str, Any]:
    row = snapshot.get("analytics") if isinstance(snapshot.get("analytics"), dict) else {}
    symbol = str(snapshot.get("symbol") or row.get("ticker") or row.get("name") or "")
    exchange = str(row.get("exchange") or (symbol.split(":", 1)[0] if ":" in symbol else ""))
    premarket_abs, premarket_pct = tradingview_premarket_change(row)
    freshness_warning = tradingview_freshness_warning(row)
    market_state, exchange_tz = tradingview_market_state(str(snapshot.get("market") or ""), exchange)
    technicals = snapshot.get("technicals") if isinstance(snapshot.get("technicals"), dict) else {}
    regular_close = tradingview_numeric_field(row, "close")
    premarket_price = tradingview_numeric_field(row, "premarket_close")
    postmarket_price = tradingview_numeric_field(row, "postmarket_close")
    return {
        "symbol": symbol,
        "as_of": snapshot.get("as_of") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "tradingview-scanner",
        "mode": "authenticated_scrape" if authenticated else "free_scrape",
        "requested_symbol": snapshot.get("requested_symbol"),
        "requested_exchange": snapshot.get("requested_exchange"),
        "market": snapshot.get("market"),
        "quote": {
            "regular_close": regular_close,
            "regular_change_abs": tradingview_numeric_field(row, "change_abs"),
            "regular_change_pct": tradingview_numeric_field(row, "change"),
            "premarket_price": premarket_price,
            "premarket_change_abs": premarket_abs,
            "premarket_change_pct": premarket_pct,
            "premarket_high": tradingview_numeric_field(row, "premarket_high"),
            "premarket_low": tradingview_numeric_field(row, "premarket_low"),
            "postmarket_price": postmarket_price,
            "postmarket_high": tradingview_numeric_field(row, "postmarket_high"),
            "postmarket_low": tradingview_numeric_field(row, "postmarket_low"),
            "update_mode": row.get("update_mode"),
            "freshness_warning": freshness_warning,
        },
        "session": {
            "market_state": market_state,
            "exchange_tz": exchange_tz or None,
        },
        "technicals": {
            "overall_label": technicals.get("overall_label") or recommendation_label(row.get("Recommend.All")),
            "overall_score": technicals.get("overall_score") if "overall_score" in technicals else row.get("Recommend.All"),
            "moving_average_label": technicals.get("moving_average_label") or recommendation_label(row.get("Recommend.MA")),
            "moving_average_score": technicals.get("moving_average_score") if "moving_average_score" in technicals else row.get("Recommend.MA"),
            "oscillator_label": technicals.get("oscillator_label") or recommendation_label(row.get("Recommend.Other")),
            "oscillator_score": technicals.get("oscillator_score") if "oscillator_score" in technicals else row.get("Recommend.Other"),
            "rsi": tradingview_numeric_field(row, "RSI"),
            "macd": tradingview_numeric_field(row, "MACD.macd"),
            "macd_signal": tradingview_numeric_field(row, "MACD.signal"),
            "stoch_k": tradingview_numeric_field(row, "Stoch.K"),
            "stoch_d": tradingview_numeric_field(row, "Stoch.D"),
            "adx": tradingview_numeric_field(row, "ADX"),
            "cci20": tradingview_numeric_field(row, "CCI20"),
        },
        "levels": {
            "open": tradingview_numeric_field(row, "open"),
            "high": tradingview_numeric_field(row, "high"),
            "low": tradingview_numeric_field(row, "low"),
            "close": regular_close,
            "ema20": tradingview_numeric_field(row, "EMA20"),
            "ema50": tradingview_numeric_field(row, "EMA50"),
            "ema100": tradingview_numeric_field(row, "EMA100"),
            "ema200": tradingview_numeric_field(row, "EMA200"),
            "sma20": tradingview_numeric_field(row, "SMA20"),
            "sma50": tradingview_numeric_field(row, "SMA50"),
            "sma100": tradingview_numeric_field(row, "SMA100"),
            "sma200": tradingview_numeric_field(row, "SMA200"),
            "vwma": tradingview_numeric_field(row, "VWMA"),
            "week_52_high": tradingview_numeric_field(row, "52_week_high"),
            "week_52_low": tradingview_numeric_field(row, "52_week_low"),
        },
        "liquidity": {
            "volume": tradingview_numeric_field(row, "volume"),
            "relative_volume": tradingview_numeric_field(row, "relative_volume_10d_calc"),
            "value_traded": tradingview_numeric_field(row, "Value.Traded"),
            "market_cap": tradingview_numeric_field(row, "market_cap_basic"),
            "volatility_week": tradingview_numeric_field(row, "Volatility.W"),
        },
        "events": {
            "next_earnings_date": row.get("earnings_release_next_date"),
            "next_earnings_time": row.get("earnings_release_next_time"),
        },
        "unsupported_fields": snapshot.get("unsupported_fields", []),
        "unsafe_for_execution": False,
    }


def tradingview_preopen_symbol_snapshot(
    symbol: str,
    *,
    market: str = TRADINGVIEW_DEFAULT_MARKET,
    exchange: str = TRADINGVIEW_DEFAULT_EXCHANGE,
    authenticated: bool = True,
    cookie: str = "",
) -> dict[str, Any]:
    full = tradingview_symbol_full_snapshot(symbol, market=market, exchange=exchange, cookie=cookie, include_profile=False)
    return tradingview_preopen_from_full_snapshot(full, authenticated=authenticated)


def tradingview_compact_preopen_row(snapshot: dict[str, Any]) -> dict[str, Any]:
    quote = snapshot.get("quote") if isinstance(snapshot.get("quote"), dict) else {}
    technicals = snapshot.get("technicals") if isinstance(snapshot.get("technicals"), dict) else {}
    liquidity = snapshot.get("liquidity") if isinstance(snapshot.get("liquidity"), dict) else {}
    events = snapshot.get("events") if isinstance(snapshot.get("events"), dict) else {}
    return {
        "symbol": snapshot.get("symbol"),
        "regular_close": quote.get("regular_close"),
        "premarket_price": quote.get("premarket_price"),
        "premarket_change_pct": quote.get("premarket_change_pct"),
        "regular_change_pct": quote.get("regular_change_pct"),
        "update_mode": quote.get("update_mode"),
        "freshness_warning": quote.get("freshness_warning"),
        "technical": technicals.get("overall_label"),
        "technical_score": technicals.get("overall_score"),
        "ma": technicals.get("moving_average_label"),
        "oscillator": technicals.get("oscillator_label"),
        "rsi": technicals.get("rsi"),
        "relative_volume": liquidity.get("relative_volume"),
        "volume": liquidity.get("volume"),
        "market_cap": liquidity.get("market_cap"),
        "next_earnings_date": events.get("next_earnings_date"),
    }


def tradingview_symbol_request_parts(item: Any, default_exchange: str) -> tuple[str, str]:
    if isinstance(item, dict):
        symbol = str(item.get("symbol") or item.get("ticker") or item.get("name") or "").strip()
        exchange = str(item.get("exchange") or default_exchange).strip() or default_exchange
        return symbol, exchange
    return str(item or "").strip(), default_exchange


def tradingview_row_match_keys(row: dict[str, Any]) -> set[tuple[str, str]]:
    exchange = str(row.get("exchange") or "").strip().upper()
    tokens = {
        tv_symbol_core(row.get("ticker")),
        tv_symbol_core(row.get("name")),
        tv_symbol_core(row.get("description")),
    }
    return {(exchange, token) for token in tokens if token}


def tradingview_batch_rows_by_request(
    rows: list[dict[str, Any]],
    request_symbols: list[str],
) -> dict[str, dict[str, Any]]:
    by_symbol: dict[str, dict[str, Any]] = {}
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    allow_index_fallback = len(rows) == len(request_symbols)
    for index, row in enumerate(rows):
        if allow_index_fallback and index < len(request_symbols):
            by_symbol.setdefault(request_symbols[index], row)
        for key in tradingview_row_match_keys(row):
            by_key.setdefault(key, row)

    matched: dict[str, dict[str, Any]] = {}
    for requested in request_symbols:
        exchange = requested.split(":", 1)[0].upper() if ":" in requested else ""
        token = tv_symbol_core(requested)
        row = by_key.get((exchange, token)) if exchange and token else None
        if row is None:
            row = by_symbol.get(requested)
        if row is not None:
            matched[requested] = row
    return matched


def tradingview_preopen_batch_snapshot(
    symbols: list[Any],
    *,
    market: str = TRADINGVIEW_DEFAULT_MARKET,
    exchange: str = TRADINGVIEW_DEFAULT_EXCHANGE,
    authenticated: bool = True,
    compact: bool = False,
    max_concurrency: int = 4,
    cookie: str = "",
) -> dict[str, Any]:
    del max_concurrency  # Bulk scanner calls replace per-symbol concurrency for normal operation.
    requests: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index, item in enumerate(symbols):
        symbol, item_exchange = tradingview_symbol_request_parts(item, exchange)
        if not symbol:
            errors.append({"index": index, "symbol": symbol, "error": "symbol is required"})
            rows.append({"index": index, "ok": False, "error": "symbol is required"} if compact else {})
            continue
        normalized_symbol = normalize_tv_symbol(symbol, item_exchange)
        requests.append(
            {
                "index": index,
                "symbol": symbol,
                "exchange": item_exchange,
                "normalized_symbol": normalized_symbol,
            }
        )
        rows.append({})

    batch_rows_by_symbol: dict[str, dict[str, Any]] = {}
    unsupported_fields: list[str] = []
    batch_error = ""
    if requests:
        try:
            scan, unsupported_fields = tradingview_scan_with_field_fallback(
                symbols=[request["normalized_symbol"] for request in requests],
                fields=TRADINGVIEW_DEEP_ANALYTICS_CANDIDATE_FIELDS,
                market=market,
                cookie=cookie,
            )
        except Exception as exc:
            batch_error = str(exc)
            scan = {"rows": []}
        batch_rows_by_symbol = tradingview_batch_rows_by_request(
            [row for row in scan.get("rows", []) if isinstance(row, dict)] if isinstance(scan, dict) else [],
            [request["normalized_symbol"] for request in requests],
        )

    fallback_count = 0
    for request in requests:
        index = int(request["index"])
        symbol = str(request["symbol"])
        item_exchange = str(request["exchange"])
        normalized_symbol = str(request["normalized_symbol"])
        row = batch_rows_by_symbol.get(normalized_symbol)
        try:
            if row is not None:
                snapshot = tradingview_preopen_from_full_snapshot(
                    tradingview_symbol_full_snapshot_from_row(
                        row,
                        symbol=normalized_symbol,
                        requested_symbol=symbol,
                        requested_market=market,
                        requested_exchange=item_exchange,
                        unsupported_fields=unsupported_fields,
                    ),
                    authenticated=authenticated,
                )
            else:
                fallback_count += 1
                if batch_error:
                    raise RuntimeError(batch_error)
                snapshot = tradingview_preopen_symbol_snapshot(
                    symbol,
                    market=market,
                    exchange=item_exchange,
                    authenticated=authenticated,
                    cookie=cookie,
                )
            output_row = tradingview_compact_preopen_row(snapshot) if compact else snapshot
            output_row["index"] = index
            output_row["ok"] = True
            rows[index] = output_row
        except Exception as exc:
            error = {"index": index, "symbol": symbol, "exchange": item_exchange, "error": str(exc)}
            errors.append(error)
            rows[index] = {"index": index, "symbol": symbol, "exchange": item_exchange, "ok": False, "error": str(exc)}
    return {
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "market": market,
        "authenticated": authenticated,
        "compact": compact,
        "batch_mode": "bulk_scanner",
        "fallback_count": fallback_count,
        "count": len(rows),
        "ok_count": sum(1 for row in rows if row.get("ok")),
        "error_count": len(errors),
        "rows": rows,
        "errors": errors,
        "unsafe_for_execution": False,
    }


def tradingview_heatmap_sort_value(row: dict[str, Any], sort_by: str) -> float:
    key = str(sort_by or "change").strip()
    if key in {"premarket_change", "premarket_change_pct"}:
        _change_abs, change_pct = tradingview_premarket_change(row)
        return float(change_pct if change_pct is not None else -1e300)
    value = tradingview_numeric_field(row, key)
    if value is None and key.lower() == "relative_volume":
        value = tradingview_numeric_field(row, "relative_volume_10d_calc")
    return float(value if value is not None else -1e300)


def tradingview_filter_heatmap_rows(
    rows: list[dict[str, Any]],
    *,
    exchanges: list[str] | None = None,
    min_market_cap: float | None = None,
    min_price: float | None = None,
    min_volume: float | None = None,
    sector: str | None = None,
    industry: str | None = None,
    sort_by: str = "change",
    exclude_otc: bool = True,
    limit: int = 50,
) -> list[dict[str, Any]]:
    allowed_exchanges = {str(item).strip().upper() for item in (exchanges or []) if str(item).strip()}
    sector_filter = str(sector or "").strip().lower()
    industry_filter = str(industry or "").strip().lower()
    filtered: list[dict[str, Any]] = []
    for row in rows:
        exchange = str(row.get("exchange") or "").strip().upper()
        if allowed_exchanges and exchange not in allowed_exchanges:
            continue
        if exclude_otc and any(exchange == item or exchange.startswith(f"{item}.") for item in TRADINGVIEW_OTC_EXCHANGES):
            continue
        if min_market_cap is not None and (tradingview_numeric_field(row, "market_cap_basic") or 0.0) < min_market_cap:
            continue
        if min_price is not None and (tradingview_numeric_field(row, "close") or 0.0) < min_price:
            continue
        if min_volume is not None and (tradingview_numeric_field(row, "volume") or 0.0) < min_volume:
            continue
        if sector_filter and sector_filter not in str(row.get("sector") or "").strip().lower():
            continue
        if industry_filter and industry_filter not in str(row.get("industry") or "").strip().lower():
            continue
        filtered.append(row)
    filtered.sort(key=lambda row: tradingview_heatmap_sort_value(row, sort_by), reverse=True)
    return filtered[: max(1, min(limit, 200))]


def tradingview_heatmap_snapshot(
    *,
    market: str = TRADINGVIEW_DEFAULT_MARKET,
    limit: int = 50,
    exchanges: list[str] | None = None,
    min_market_cap: float | None = None,
    min_price: float | None = None,
    min_volume: float | None = None,
    sector: str | None = None,
    industry: str | None = None,
    sort_by: str = "change",
    include_premarket: bool = True,
    exclude_otc: bool = True,
    cookie: str = "",
) -> dict[str, Any]:
    fields = list(TRADINGVIEW_HEATMAP_FIELDS)
    if not include_premarket:
        fields = [field for field in fields if not field.startswith("premarket_") and not field.startswith("postmarket_")]
    fetch_limit = max(limit, min(1000, max(limit * 5, 100)))
    scanner_sort = sort_by if sort_by in fields else "change"
    scan, unsupported_fields = tradingview_scan_with_field_fallback(
        symbols=[],
        fields=fields,
        market=market,
        limit=fetch_limit,
        sort_by=scanner_sort,
        descending=True,
        cookie=cookie,
    )
    rows = tradingview_filter_heatmap_rows(
        scan["rows"],
        exchanges=exchanges if exchanges is not None else list(TRADINGVIEW_US_EQUITY_EXCHANGES),
        min_market_cap=min_market_cap,
        min_price=min_price,
        min_volume=min_volume,
        sector=sector,
        industry=industry,
        sort_by=sort_by,
        exclude_otc=exclude_otc,
        limit=limit,
    )
    return {
        "market": market,
        "rows": rows,
        "rows_before_filter": len(scan["rows"]),
        "total_count": scan["total_count"],
        "unsupported_fields": unsupported_fields,
        "filters": {
            "exchanges": exchanges if exchanges is not None else list(TRADINGVIEW_US_EQUITY_EXCHANGES),
            "min_market_cap": min_market_cap,
            "min_price": min_price,
            "min_volume": min_volume,
            "sector": sector,
            "industry": industry,
            "sort_by": sort_by,
            "include_premarket": include_premarket,
            "exclude_otc": exclude_otc,
        },
        "source": "tradingview-scanner",
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "unsafe_for_execution": False,
    }


def tradingview_watchlist_snapshot(
    *,
    reference_symbol: str,
    market: str = TRADINGVIEW_DEFAULT_MARKET,
    exchange: str = TRADINGVIEW_DEFAULT_EXCHANGE,
    cookie: str = "",
    limit: int = 25,
) -> dict[str, Any]:
    normalized_reference = normalize_tv_symbol(reference_symbol, exchange)
    html_text = tradingview_symbol_profile_html(normalized_reference, cookie=cookie)
    symbols = tradingview_extract_symbol_candidates_from_html(html_text, max_symbols=max(5, min(limit * 3, 150)))
    if normalized_reference not in symbols:
        symbols.insert(0, normalized_reference)
    symbols = symbols[: max(1, min(limit, 100))]
    scan = tradingview_scan(
        symbols=symbols,
        columns=["name", "description", "close", "change", "change_abs", "volume", "Recommend.All"],
        market=market,
        limit=len(symbols),
        cookie=cookie,
    )
    rows = []
    for row in scan["rows"]:
        score = row.get("Recommend.All")
        row["recommendation"] = recommendation_label(score)
        rows.append(row)
    auth_mode = "authenticated" if cookie else "anonymous"
    return {
        "reference_symbol": normalized_reference,
        "market": market,
        "auth_mode": auth_mode,
        "rows": rows,
        "raw_symbol_candidates": symbols,
        "source": "tradingview-profile+scanner",
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "unsafe_for_execution": auth_mode != "authenticated",
    }


def tradingview_custom_watchlists_from_profile(
    *,
    list_id: str | None = None,
    list_name: str | None = None,
    limit: int = TRADINGVIEW_WATCHLIST_ROW_LIMIT,
    profile_dir: Path | None = None,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError(
            "Playwright is required for TradingView custom list scraping. "
            "Install with: uv add --dev playwright && uv run playwright install chromium"
        ) from exc

    target_profile_dir = profile_dir or TRADINGVIEW_BROWSER_PROFILE_DIR
    if not target_profile_dir.exists():
        raise RuntimeError(
            f"TradingView profile directory not found: {target_profile_dir}. "
            "Run tv_auth_session_login_auto first."
        )

    target_list_id = tradingview_watchlist_id_from_input(list_id)
    target_list_name = str(list_name or "").strip()

    extraction_script = """
() => {
  const dialog = document.querySelector('[data-name="watchlists-dialog"]');
  const listEntries = dialog
    ? [...dialog.querySelectorAll('[data-role="list-item"]')].map((el, index) => {
        const text = (el.textContent || '').replace(/\\s+/g, ' ').trim();
        const countMatch = text.match(/(\\d+)$/);
        const count = countMatch ? Number(countMatch[1]) : null;
        const name = countMatch ? text.slice(0, countMatch.index).trim() : text;
        return {
          index,
          id: el.getAttribute('data-id') || '',
          title: el.getAttribute('data-title') || '',
          name,
          raw_label: text,
          count,
          selected: el.className.includes('selected-') || el.getAttribute('aria-selected') === 'true',
        };
      })
    : [];

  const activeHeader = document.querySelector('.widgetbar-widget-watchlist [class*="separator-"][class*="firstItem-"]');
  const activeListName = activeHeader ? (activeHeader.textContent || '').replace(/\\s+/g, ' ').trim() : '';
  const symbolRows = [...document.querySelectorAll('.widgetbar-widget-watchlist [data-symbol-full]')];
  const rows = symbolRows.map((row) => {
    const pick = (selector) => {
      const element = row.querySelector(selector);
      return element ? (element.textContent || '').replace(/\\s+/g, ' ').trim() : '';
    };
    const symbolShort = row.getAttribute('data-symbol-short') || pick('[class*="symbolNameText"]');
    const symbolFull = row.getAttribute('data-symbol-full') || '';
    const marketStatusRaw = pick('[class*="displayContents"]');
    const marketStatus = marketStatusRaw
      .replace(symbolShort, '')
      .replace(/RMarket\\s*/i, 'Market ')
      .replace(/\\s+/g, ' ')
      .trim();
    return {
      symbol: symbolShort,
      symbol_full: symbolFull,
      last: pick('[class*="last-"] [class*="inner-"]'),
      change: pick('[class*="change-"] [class*="inner-"]'),
      change_percent: pick('[class*="changeInPercents"] [class*="inner-"]'),
      volume: pick('[class*="volume-"] [class*="inner-"]'),
      market_status: marketStatus,
    };
  });

  return { list_entries: listEntries, active_list_name: activeListName, rows };
}
"""
    rows_collection_script = """
async (maxRows) => {
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const widget = document.querySelector('.widgetbar-widget-watchlist');
  if (!widget) {
    return { rows: [], active_list_name: '', scanned_rows: 0, scroll_steps: 0 };
  }

  const activeHeader = widget.querySelector('[class*="separator-"][class*="firstItem-"]');
  const activeListName = activeHeader ? (activeHeader.textContent || '').replace(/\\s+/g, ' ').trim() : '';

  const rowSelector = '[data-symbol-full]';
  const pick = (root, selector) => {
    const element = root.querySelector(selector);
    return element ? (element.textContent || '').replace(/\\s+/g, ' ').trim() : '';
  };

  const serializeRow = (row) => {
    const symbolShort = row.getAttribute('data-symbol-short') || pick(row, '[class*="symbolNameText"]');
    const symbolFull = row.getAttribute('data-symbol-full') || '';
    const marketStatusRaw = pick(row, '[class*="displayContents"]');
    const marketStatus = marketStatusRaw
      .replace(symbolShort, '')
      .replace(/RMarket\\s*/i, 'Market ')
      .replace(/\\s+/g, ' ')
      .trim();
    return {
      symbol: symbolShort,
      symbol_full: symbolFull,
      last: pick(row, '[class*="last-"] [class*="inner-"]'),
      change: pick(row, '[class*="change-"] [class*="inner-"]'),
      change_percent: pick(row, '[class*="changeInPercents"] [class*="inner-"]'),
      volume: pick(row, '[class*="volume-"] [class*="inner-"]'),
      market_status: marketStatus,
    };
  };

  const findScroller = () => {
    const candidates = [widget, ...widget.querySelectorAll('*')];
    let best = null;
    let bestHeight = 0;
    for (const element of candidates) {
      const delta = element.scrollHeight - element.clientHeight;
      if (delta > 12 && element.clientHeight > bestHeight) {
        best = element;
        bestHeight = element.clientHeight;
      }
    }
    return best;
  };

  const scroller = findScroller();
  const seen = new Map();
  const readVisible = () => {
    for (const row of widget.querySelectorAll(rowSelector)) {
      const payload = serializeRow(row);
      const key = payload.symbol_full || payload.symbol || `${payload.last}|${payload.volume}`;
      if (!key) continue;
      if (!seen.has(key)) {
        seen.set(key, payload);
      }
      if (seen.size >= maxRows) break;
    }
  };

  if (scroller) {
    scroller.scrollTop = 0;
    scroller.dispatchEvent(new Event('scroll', { bubbles: true }));
    await wait(140);
  }
  readVisible();

  let steps = 0;
  let stable = 0;
  let lastCount = seen.size;
  while (scroller && steps < 900 && seen.size < maxRows) {
    const maxTop = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
    const currentTop = Math.max(0, scroller.scrollTop);
    if (currentTop >= maxTop - 1) {
      break;
    }
    const increment = Math.max(18, Math.floor(scroller.clientHeight * 0.7));
    scroller.scrollTop = Math.min(maxTop, currentTop + increment);
    scroller.dispatchEvent(new Event('scroll', { bubbles: true }));
    await wait(80);
    readVisible();
    steps += 1;
    if (seen.size == lastCount) {
      stable += 1;
      if (stable >= 5) {
        break;
      }
    } else {
      stable = 0;
      lastCount = seen.size;
    }
  }

  return {
    rows: [...seen.values()],
    active_list_name: activeListName,
    scanned_rows: seen.size,
    scroll_steps: steps,
  };
}
"""

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(target_profile_dir),
            headless=True,
            viewport={"width": 1700, "height": 1050},
            user_agent=EXTERNAL_HTTP_USER_AGENT,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto("https://www.tradingview.com/chart/", wait_until="domcontentloaded", timeout=90_000)
            page.wait_for_timeout(5_000)

            if page.locator("text=Sign in").count() > 0:
                raise RuntimeError("TradingView profile is not authenticated. Run tv_auth_session_login_auto first.")

            watchlist_button = page.locator('[data-name="watchlists-button"]').first
            if watchlist_button.count() == 0:
                raise RuntimeError("TradingView watchlist widget not found on chart page.")
            watchlist_button.click(timeout=10_000)
            page.wait_for_timeout(700)
            open_list_entry = page.locator("text=Open list…").first
            if open_list_entry.count() == 0:
                open_list_entry = page.locator("text=Open list...").first
            if open_list_entry.count() == 0:
                raise RuntimeError("Could not open TradingView watchlist list-selector.")
            open_list_entry.click(timeout=10_000)
            page.wait_for_timeout(1_200)

            snapshot = page.evaluate(extraction_script)
            list_entries = snapshot.get("list_entries", []) if isinstance(snapshot, dict) else []

            selected_entry: dict[str, Any] | None = next(
                (entry for entry in list_entries if isinstance(entry, dict) and bool(entry.get("selected"))),
                None,
            )

            if target_list_id or target_list_name:
                target_entry = None
                target_entry = next(
                    (
                        entry
                        for entry in list_entries
                        if isinstance(entry, dict)
                        and tradingview_watchlist_entry_matches_target(entry, target_list_id, target_list_name)
                    ),
                    None,
                )
                if target_entry is None:
                    raise ValueError(f"TradingView list not found: id='{target_list_id}' name='{target_list_name}'")
                target_dom_id = target_entry.get("id")
                if target_dom_id:
                    page.locator(f'[data-role=\"list-item\"][data-id=\"{target_dom_id}\"]').first.click(timeout=10_000)
                    page.wait_for_timeout(1_500)
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(500)
                    snapshot = page.evaluate(extraction_script)
                    selected_entry = target_entry

            if page.locator('[data-name=\"watchlists-dialog\"]').count() > 0:
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)

            if not isinstance(snapshot, dict):
                snapshot = {}

            limit_value = max(1, min(int(limit), TRADINGVIEW_WATCHLIST_ROW_LIMIT))
            rows_snapshot = page.evaluate(rows_collection_script, limit_value)
            if not isinstance(rows_snapshot, dict):
                rows_snapshot = {}
            rows = rows_snapshot.get("rows", [])
            rows = [row for row in rows if isinstance(row, dict)]
            rows = rows[:limit_value]
            active_name = str(rows_snapshot.get("active_list_name", "") or snapshot.get("active_list_name", "") or "")
            list_entries = [entry for entry in list_entries if isinstance(entry, dict)]
            if selected_entry is None:
                selected_entry = next((entry for entry in list_entries if str(entry.get("name", "")) == active_name), None)

            return {
                "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "profile_dir": str(target_profile_dir),
                "active_list_name": active_name,
                "selected_list": selected_entry,
                "lists": list_entries,
                "items": rows,
                "collected_rows": int(rows_snapshot.get("scanned_rows", len(rows))),
                "scroll_steps": int(rows_snapshot.get("scroll_steps", 0)),
                "source": "tradingview-auth-watchlists",
                "unsafe_for_execution": False,
            }
        finally:
            context.close()
