"""Read-only MCP snapshot providers backing the data tools."""

import os
import re
import time

from avanza.constants import TransactionsDetailsType
from avanza_mcp import avanza_ext, utils
from avanza_mcp.config import (
    ACCOUNT_READ_CACHE_SECONDS,
    KNOWN_ORDERBOOK_METADATA,
    LIVE_REFRESH_SECONDS,
    ORDERBOOK_METADATA_REFRESH_SECONDS,
    QUOTE_CACHE_SECONDS,
    QUOTE_REFRESH_COALESCE_SECONDS,
    REALTIME_STATUS_REFRESH_SECONDS,
    TRADINGVIEW_DEFAULT_EXCHANGE,
    TRADINGVIEW_DEFAULT_MARKET,
)
from avanza_mcp.external import feeds, tradingview_data as tv_data, zacks as zacks_feed
from avanza_mcp.external.tradingview_data import (
    normalize_tv_symbol,
    tradingview_compact_preopen_row,
    tradingview_symbol_request_parts,
    tv_symbol_core,
    unique_strings,
)
from avanza_mcp.market_data import (
    account_performance_summary_from_payload,
    display_symbol,
    infer_country_from_metadata,
    infer_currency_from_metadata,
    map_account_performance_period,
    market_quote_change_percent,
    market_quote_first_text,
    market_quote_last,
    merged_orderbook_metadata,
    metadata_from_market_guide_payload,
    normalize_symbol_candidate,
    orderbook_quote_row,
    payload_to_json_safe,
    trailing_parenthesized_symbol,
)
from avanza_mcp.models import AccountDataSnapshot, AvanzaTenantSession
from avanza_mcp.records import (
    flattened_search_hits,
    instrument_is_eth_like,
    name_matches_filter,
    normalized_search_rows,
    open_order_matches_filters,
    parse_transaction_types,
    position_mcp_dict,
    position_volume,
    stop_loss_matches_filters,
    stop_loss_mcp_dict,
    stop_loss_order_book_id,
    stop_loss_side,
    stop_loss_trigger_percent,
    stop_loss_volume,
    summarize_sold_transactions,
    summarize_stop_protection,
    transaction_history_dict_row,
    transaction_matches_instrument_filters,
    transaction_order_book_id,
    transaction_volume,
    transactions_items,
)
from avanza_mcp.rendering import (
    fund_order_items,
    account_rows_from_overview,
    first_known_realtime_status,
    lookup_realtime_status,
    matches_account,
    open_order_items,
    open_order_mcp_dict,
    open_order_order_book_id,
    position_order_book_id,
)
from avanza_mcp.utils import nested_value
from datetime import date, datetime, timedelta, timezone
from typing import Any


class CoreSnapshotsMixin:
    """Read-only MCP snapshot providers backing the data tools."""
    def account_scrambled_id(self, account: dict[str, Any]) -> str:
        return str(account.get("urlParameterId") or account.get("url_parameter_id") or account.get("id") or "")

    def resolve_account_for_performance(
        self,
        avanza: Any,
        requested_account_id: str,
    ) -> tuple[str, str]:
        account_id = requested_account_id or self.require_selected_account_id()
        account = self.account_by_id(account_id)
        if account is None:
            overview = avanza.get_overview()
            if isinstance(overview, dict):
                for candidate in account_rows_from_overview(overview):
                    candidate_id = str(candidate.get("id", ""))
                    candidate_scrambled = self.account_scrambled_id(candidate)
                    if candidate_id == account_id or candidate_scrambled == account_id:
                        account = candidate
                        account_id = candidate_id or account_id
                        break
        if account is None:
            raise RuntimeError(f"Unknown account id for performance: {account_id}")
        scrambled_account_id = self.account_scrambled_id(account) or account_id
        return account_id, scrambled_account_id

    def account_performance_snapshot(
        self,
        avanza: Any,
        requested_account_id: str,
        requested_period: Any,
    ) -> dict[str, Any]:
        period_label, period_enum = map_account_performance_period(requested_period)
        account_id, scrambled_account_id = self.resolve_account_for_performance(avanza, requested_account_id)
        payload = avanza.get_account_performance_chart_data([scrambled_account_id], period_enum)
        return account_performance_summary_from_payload(payload, account_id, period_label, period_enum.value)

    def data_source_status_snapshot(
        self,
        *,
        symbol: str,
        exchange: str = TRADINGVIEW_DEFAULT_EXCHANGE,
        market: str = TRADINGVIEW_DEFAULT_MARKET,
    ) -> dict[str, Any]:
        symbol_input = str(symbol or "").strip()
        normalized_symbol = normalize_tv_symbol(symbol_input, exchange)
        as_of = datetime.now(timezone.utc).isoformat(timespec="seconds")
        sources: list[dict[str, Any]] = []
        effective_symbol = normalized_symbol
        symbol_token = tv_symbol_core(normalized_symbol)

        try:
            tv = tv_data.tradingview_symbol_snapshot(
                symbol_input,
                market=market,
                exchange=exchange,
                cookie="",
            )
            effective_symbol = str(tv.get("symbol") or normalized_symbol)
            symbol_token = tv_symbol_core(effective_symbol) or symbol_token
            sources.append(
                {
                    "source": "tradingview",
                    "status": "ok",
                    "symbol": tv["symbol"],
                    "recommendation": tv["technicals"]["overall_label"],
                    "unsafe_for_execution": bool(tv.get("unsafe_for_execution")),
                }
            )
        except Exception as exc:
            sources.append(
                {
                    "source": "tradingview",
                    "status": "error",
                    "error": str(exc),
                    "unsafe_for_execution": True,
                }
            )

        try:
            zacks = zacks_feed.zacks_symbol_snapshot(symbol_token)
            status = "blocked" if zacks.get("blocked") else "ok"
            sources.append(
                {
                    "source": "zacks",
                    "status": status,
                    "rank": zacks.get("rank"),
                    "unsafe_for_execution": bool(zacks.get("blocked", True)),
                }
            )
        except Exception as exc:
            sources.append(
                {
                    "source": "zacks",
                    "status": "error",
                    "error": str(exc),
                    "unsafe_for_execution": True,
                }
            )

        fmp_key = os.getenv("FMP_API_KEY", "").strip()
        if fmp_key:
            try:
                fmp = feeds.fmp_analyst_recommendations_snapshot(symbol_token, limit=1, api_key=fmp_key)
                latest = fmp.get("latest") if isinstance(fmp.get("latest"), dict) else {}
                sources.append(
                    {
                        "source": "fmp",
                        "status": "ok",
                        "latest_date": latest.get("date"),
                        "strong_buy": latest.get("strong_buy"),
                        "buy": latest.get("buy"),
                        "hold": latest.get("hold"),
                        "sell": latest.get("sell"),
                        "strong_sell": latest.get("strong_sell"),
                        "unsafe_for_execution": False,
                    }
                )
            except Exception as exc:
                sources.append(
                    {
                        "source": "fmp",
                        "status": "error",
                        "error": str(exc),
                        "unsafe_for_execution": True,
                    }
                )
        else:
            sources.append(
                {
                    "source": "fmp",
                    "status": "not_configured",
                    "details": "Set FMP_API_KEY for analyst recommendation history.",
                    "unsafe_for_execution": True,
                }
            )

        polygon_key = os.getenv("POLYGON_API_KEY", "").strip()
        if polygon_key:
            try:
                polygon = feeds.polygon_analyst_insights_snapshot(symbol_token, limit=1, api_key=polygon_key)
                first = polygon.get("rows", [{}])[0] if polygon.get("rows") else {}
                sources.append(
                    {
                        "source": "polygon",
                        "status": "ok",
                        "latest_date": first.get("date"),
                        "rating": first.get("rating"),
                        "rating_action": first.get("rating_action"),
                        "price_target": first.get("price_target"),
                        "unsafe_for_execution": False,
                    }
                )
            except Exception as exc:
                sources.append(
                    {
                        "source": "polygon",
                        "status": "error",
                        "error": str(exc),
                        "unsafe_for_execution": True,
                    }
                )
        else:
            sources.append(
                {
                    "source": "polygon",
                    "status": "not_configured",
                    "details": "Set POLYGON_API_KEY for analyst insights.",
                    "unsafe_for_execution": True,
                }
            )

        try:
            sec = feeds.sec_recent_filings_snapshot(ticker=symbol_token, cik=None, limit=5)
            sources.append(
                {
                    "source": "sec",
                    "status": "ok",
                    "ticker": sec.get("ticker"),
                    "filings_loaded": len(sec.get("filings", [])),
                    "unsafe_for_execution": False,
                }
            )
        except Exception as exc:
            sources.append(
                {
                    "source": "sec",
                    "status": "error",
                    "error": str(exc),
                    "unsafe_for_execution": True,
                }
            )

        fred_key = os.getenv("FRED_API_KEY", "").strip()
        if fred_key:
            try:
                fred = feeds.fred_observations_snapshot("FEDFUNDS", api_key=fred_key, limit=1)
                last = fred.get("observations", [{}])[-1] if fred.get("observations") else {}
                sources.append(
                    {
                        "source": "fred",
                        "status": "ok",
                        "series_id": fred.get("series_id"),
                        "latest_value": last.get("value"),
                        "unsafe_for_execution": False,
                    }
                )
            except Exception as exc:
                sources.append(
                    {
                        "source": "fred",
                        "status": "error",
                        "error": str(exc),
                        "unsafe_for_execution": True,
                    }
                )
        else:
            sources.append(
                {
                    "source": "fred",
                    "status": "not_configured",
                    "details": "Set FRED_API_KEY for macro series access.",
                    "unsafe_for_execution": True,
                }
            )

        sources.append(
            {
                "source": "avanza",
                "status": "ok" if self.avanza is not None else "not_connected",
                "selected_account_id": self.selected_account_id,
                "read_write": self.mcp_write_enabled,
                "unsafe_for_execution": self.avanza is None,
            }
        )

        return {
            "as_of": as_of,
            "symbol": effective_symbol,
            "requested_symbol": normalized_symbol,
            "market": market,
            "sources": sources,
            "unsafe_for_execution": any(bool(item.get("unsafe_for_execution")) for item in sources),
        }

    def signal_context_bundle_snapshot(
        self,
        *,
        symbol: str,
        exchange: str = TRADINGVIEW_DEFAULT_EXCHANGE,
        market: str = TRADINGVIEW_DEFAULT_MARKET,
        include_tradingview: bool = True,
        include_zacks: bool = True,
        include_fmp: bool = False,
        include_polygon: bool = False,
        include_sec: bool = True,
        fred_series_id: str | None = None,
        fred_api_key: str | None = None,
        fmp_api_key: str | None = None,
        polygon_api_key: str | None = None,
    ) -> dict[str, Any]:
        symbol_input = str(symbol or "").strip()
        normalized_symbol = normalize_tv_symbol(symbol_input, exchange)
        symbol_token = tv_symbol_core(normalized_symbol)
        payload: dict[str, Any] = {
            "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "symbol": normalized_symbol,
            "requested_symbol": normalized_symbol,
            "market": market,
            "sources": [],
            "errors": [],
        }

        if include_tradingview:
            try:
                tv = tv_data.tradingview_symbol_snapshot(
                    symbol_input,
                    market=market,
                    exchange=exchange,
                    cookie="",
                )
                payload["tradingview"] = tv
                payload["symbol"] = str(tv.get("symbol") or normalized_symbol)
                symbol_token = tv_symbol_core(payload["symbol"]) or symbol_token
                payload["sources"].append("tradingview")
            except Exception as exc:
                payload["tradingview"] = {"error": str(exc), "unsafe_for_execution": True}
                payload["errors"].append({"source": "tradingview", "symbol": symbol_input, "error": str(exc)})
                payload["sources"].append("tradingview")

        if include_zacks:
            try:
                zacks = zacks_feed.zacks_symbol_snapshot(symbol_token)
                payload["zacks"] = zacks
                payload["sources"].append("zacks")
            except Exception as exc:
                payload["zacks"] = {"error": str(exc), "unsafe_for_execution": True}
                payload["errors"].append({"source": "zacks", "symbol": symbol_token, "error": str(exc)})
                payload["sources"].append("zacks")

        if include_fmp:
            try:
                fmp = feeds.fmp_analyst_recommendations_snapshot(
                    symbol_token,
                    limit=52,
                    api_key=fmp_api_key,
                )
                payload["fmp"] = fmp
                payload["sources"].append("fmp")
            except Exception as exc:
                payload["fmp"] = {"error": str(exc), "unsafe_for_execution": True}
                payload["errors"].append({"source": "fmp", "symbol": symbol_token, "error": str(exc)})
                payload["sources"].append("fmp")

        if include_polygon:
            try:
                polygon = feeds.polygon_analyst_insights_snapshot(
                    symbol_token,
                    limit=50,
                    api_key=polygon_api_key,
                )
                payload["polygon"] = polygon
                payload["sources"].append("polygon")
            except Exception as exc:
                payload["polygon"] = {"error": str(exc), "unsafe_for_execution": True}
                payload["errors"].append({"source": "polygon", "symbol": symbol_token, "error": str(exc)})
                payload["sources"].append("polygon")

        if include_sec:
            try:
                sec = feeds.sec_recent_filings_snapshot(ticker=symbol_token, cik=None, limit=10)
                payload["sec"] = sec
                payload["sources"].append("sec")
            except Exception as exc:
                payload["sec"] = {"error": str(exc), "unsafe_for_execution": True}
                payload["errors"].append({"source": "sec", "symbol": symbol_token, "error": str(exc)})
                payload["sources"].append("sec")

        if fred_series_id:
            try:
                fred = feeds.fred_observations_snapshot(
                    fred_series_id,
                    api_key=fred_api_key,
                    limit=30,
                    sort_order="desc",
                )
                payload["fred"] = fred
                payload["sources"].append("fred")
            except Exception as exc:
                payload["fred"] = {"error": str(exc), "unsafe_for_execution": True}
                payload["errors"].append({"source": "fred", "series_id": fred_series_id, "error": str(exc)})
                payload["sources"].append("fred")

        unsafe_flags: list[bool] = []
        for key in ("tradingview", "zacks", "fmp", "polygon", "sec", "fred"):
            item = payload.get(key)
            if isinstance(item, dict):
                unsafe_flags.append(bool(item.get("unsafe_for_execution")))
                if "error" in item:
                    unsafe_flags.append(True)
        payload["unsafe_for_execution"] = any(unsafe_flags)
        payload["mode"] = "experimental_scrape_mode"
        return payload

    def signal_context_bundle_batch_snapshot(
        self,
        *,
        symbols: list[Any],
        exchange: str = TRADINGVIEW_DEFAULT_EXCHANGE,
        market: str = TRADINGVIEW_DEFAULT_MARKET,
        include_tradingview: bool = True,
        include_zacks: bool = True,
        include_fmp: bool = False,
        include_polygon: bool = False,
        include_sec: bool = True,
        fred_series_id: str | None = None,
        fred_api_key: str | None = None,
        fmp_api_key: str | None = None,
        polygon_api_key: str | None = None,
        compact: bool = False,
    ) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for index, item in enumerate(symbols):
            symbol, item_exchange = tradingview_symbol_request_parts(item, exchange)
            if not symbol:
                error = {"index": index, "error": "symbol is required"}
                rows.append(error)
                errors.append(error)
                continue
            try:
                snapshot = self.signal_context_bundle_snapshot(
                    symbol=symbol,
                    exchange=item_exchange,
                    market=market,
                    include_tradingview=include_tradingview,
                    include_zacks=include_zacks,
                    include_fmp=include_fmp,
                    include_polygon=include_polygon,
                    include_sec=include_sec,
                    fred_series_id=fred_series_id,
                    fred_api_key=fred_api_key,
                    fmp_api_key=fmp_api_key,
                    polygon_api_key=polygon_api_key,
                )
                if compact:
                    rows.append(
                        {
                            "index": index,
                            "symbol": snapshot.get("symbol"),
                            "requested_symbol": symbol,
                            "tradingview": snapshot.get("tradingview", {}),
                            "zacks": snapshot.get("zacks", {}),
                            "errors": snapshot.get("errors", []),
                            "unsafe_for_execution": snapshot.get("unsafe_for_execution"),
                        }
                    )
                else:
                    snapshot["index"] = index
                    rows.append(snapshot)
            except Exception as exc:
                error = {"index": index, "symbol": symbol, "exchange": item_exchange, "error": str(exc)}
                rows.append(error)
                errors.append(error)
        return {
            "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "market": market,
            "compact": compact,
            "rows": rows,
            "errors": errors,
            "unsafe_for_execution": any(bool(row.get("unsafe_for_execution")) or bool(row.get("error")) for row in rows),
        }

    def tradingview_symbol_for_position(self, position: dict[str, Any]) -> tuple[str, str, list[str]]:
        warnings: list[str] = []
        orderbook_id = position_order_book_id(position)
        metadata = self.orderbook_metadata_by_id.get(orderbook_id) or KNOWN_ORDERBOOK_METADATA.get(orderbook_id, {})
        ticker = str(metadata.get("ticker") or metadata.get("display_symbol") or "").strip()
        market_name = str(metadata.get("market") or "").strip().upper()
        exchange = str(metadata.get("exchange") or "").strip().upper()
        if not exchange:
            if "NASDAQ" in market_name:
                exchange = "NASDAQ"
            elif "NYSE" in market_name:
                exchange = "NYSE"
            elif "STOCKHOLM" in market_name:
                exchange = "OMXSTO"
        instrument = position.get("instrument") if isinstance(position.get("instrument"), dict) else {}
        orderbook = instrument.get("orderbook") if isinstance(instrument.get("orderbook"), dict) else {}
        ticker = ticker or str(
            instrument.get("tickerSymbol")
            or instrument.get("symbol")
            or orderbook.get("tickerSymbol")
            or orderbook.get("symbol")
            or ""
        ).strip()
        stock_name = str(nested_value(position, "instrument", "name") or "")
        if not ticker:
            parsed = trailing_parenthesized_symbol(stock_name)
            if parsed:
                ticker = parsed
        if not ticker:
            ticker = re.sub(r"[^A-Z0-9.]+", "", stock_name.upper())
            warnings.append("TradingView symbol was inferred from instrument name and may be wrong.")
        if not exchange:
            exchange = TRADINGVIEW_DEFAULT_EXCHANGE
            warnings.append("TradingView exchange was not available; defaulted to NASDAQ.")
        return ticker, exchange, warnings

    def avanza_tv_preopen_portfolio_bundle_snapshot(
        self,
        avanza: Any,
        account_id: str,
        *,
        include_symbols: list[Any] | None = None,
        market: str = TRADINGVIEW_DEFAULT_MARKET,
        authenticated: bool = True,
        compact: bool = True,
        cookie: str = "",
    ) -> dict[str, Any]:
        account_id = account_id or self.require_selected_account_id()
        _portfolio_data, positions = self.filtered_portfolio_items(avanza, account_id)
        stoploss_items = self.filtered_stoploss_items(avanza, account_id)
        _orders_payload, open_orders = self.filtered_open_order_items(avanza, account_id)
        stops_by_orderbook: dict[str, list[dict[str, Any]]] = {}
        orders_by_orderbook: dict[str, list[dict[str, Any]]] = {}
        for item in stoploss_items:
            stops_by_orderbook.setdefault(stop_loss_order_book_id(item), []).append(item)
        for item in open_orders:
            orders_by_orderbook.setdefault(open_order_order_book_id(item), []).append(item)

        include_symbol_tokens = {
            tv_symbol_core(tradingview_symbol_request_parts(item, TRADINGVIEW_DEFAULT_EXCHANGE)[0])
            for item in (include_symbols or [])
            if tradingview_symbol_request_parts(item, TRADINGVIEW_DEFAULT_EXCHANGE)[0]
        }
        rows: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        seen_symbols: set[str] = set()
        for position in positions:
            orderbook_id = position_order_book_id(position)
            ticker, exchange, symbol_warnings = self.tradingview_symbol_for_position(position)
            token = tv_symbol_core(ticker)
            if include_symbol_tokens and token not in include_symbol_tokens:
                continue
            seen_symbols.add(token)
            tv_snapshot: dict[str, Any] | None = None
            tv_error = ""
            try:
                tv_snapshot = tv_data.tradingview_preopen_symbol_snapshot(
                    ticker,
                    market=market,
                    exchange=exchange,
                    authenticated=authenticated,
                    cookie=cookie,
                )
            except Exception as exc:
                tv_error = str(exc)
                errors.append({"stock": str(nested_value(position, "instrument", "name") or ""), "symbol": ticker, "error": tv_error})
            position_row = position_mcp_dict(position, self.realtime_status_for_position(position))
            protection = summarize_stop_protection(position, stops_by_orderbook.get(orderbook_id, []))
            instrument_orders = [open_order_mcp_dict(item) for item in orders_by_orderbook.get(orderbook_id, [])]
            failed_orders = [
                row
                for row in instrument_orders
                if str(row.get("Status", "")).upper() in {"ERROR", "FAILED", "REJECTED", "FAULTY", "FELAKTIG"}
            ]
            stock_name = str(position_row.get("stock") or "")
            quote = tv_snapshot.get("quote", {}) if isinstance(tv_snapshot, dict) else {}
            liquidity = tv_snapshot.get("liquidity", {}) if isinstance(tv_snapshot, dict) else {}
            flags = {
                "stale_avanza_quote": str(position_row.get("Real-time") or "").strip().lower() not in {"yes", "real-time", "realtime", "true"},
                "tradingview_extended_hours_move": abs(float(quote.get("premarket_change_pct") or 0.0)) >= 1.0,
                "sell_protection_gap": protection["sell_protection_gap"] > 0 and not instrument_is_eth_like(stock_name, orderbook_id),
                "eth_approved_exception": instrument_is_eth_like(stock_name, orderbook_id),
                "raw_failed_orders": bool(failed_orders),
                "high_volatility": abs(float(liquidity.get("volatility_week") or 0.0)) >= 5.0,
                "oversized_exposure": False,
            }
            row = {
                "account_id": account_id,
                "stock": stock_name,
                "orderbook_id": orderbook_id,
                "tradingview_symbol": tv_snapshot.get("symbol") if isinstance(tv_snapshot, dict) else normalize_tv_symbol(ticker, exchange),
                "mapping_warnings": symbol_warnings,
                "position": position_row,
                "protection": protection,
                "open_orders": instrument_orders,
                "failed_orders": failed_orders,
                "flags": flags,
                "tradingview": tradingview_compact_preopen_row(tv_snapshot) if compact and isinstance(tv_snapshot, dict) else tv_snapshot,
                "tradingview_error": tv_error or None,
            }
            rows.append(row)

        for item in include_symbols or []:
            symbol, exchange = tradingview_symbol_request_parts(item, TRADINGVIEW_DEFAULT_EXCHANGE)
            token = tv_symbol_core(symbol)
            if not symbol or token in seen_symbols:
                continue
            try:
                tv_snapshot = tv_data.tradingview_preopen_symbol_snapshot(
                    symbol,
                    market=market,
                    exchange=exchange,
                    authenticated=authenticated,
                    cookie=cookie,
                )
                rows.append(
                    {
                        "account_id": account_id,
                        "stock": None,
                        "orderbook_id": None,
                        "tradingview_symbol": tv_snapshot.get("symbol"),
                        "position": None,
                        "protection": None,
                        "open_orders": [],
                        "failed_orders": [],
                        "flags": {"not_in_portfolio": True},
                        "tradingview": tradingview_compact_preopen_row(tv_snapshot) if compact else tv_snapshot,
                        "tradingview_error": None,
                    }
                )
            except Exception as exc:
                errors.append({"symbol": symbol, "exchange": exchange, "error": str(exc)})
        return {
            "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "account_id": account_id,
            "tenant_session_id": self.active_session_id,
            "market": market,
            "authenticated": authenticated,
            "compact": compact,
            "rows": rows,
            "errors": errors,
            "unsafe_for_execution": False,
            "note": "Read-only pre-open review bundle. Avanza remains source of truth for account/orders/stops; TradingView supplies market context.",
        }

    def account_snapshot_for_cache(self, account_id: str) -> tuple[AccountDataSnapshot, AvanzaTenantSession | None]:
        token = str(account_id or "").strip()
        context = self.active_tenant_session()
        if context is not None:
            snapshot = context.account_snapshots.get(token)
            if snapshot is None:
                snapshot = AccountDataSnapshot(account_id=token)
                context.account_snapshots[token] = snapshot
            return snapshot, context
        snapshot = self.account_snapshot_cache.get(token)
        if snapshot is None:
            snapshot = AccountDataSnapshot(account_id=token)
            self.account_snapshot_cache[token] = snapshot
        return snapshot, None

    def cached_at_is_fresh(self, refreshed_at: datetime | None) -> bool:
        if refreshed_at is None:
            return False
        if refreshed_at.tzinfo is None:
            return datetime.now() - refreshed_at < timedelta(seconds=ACCOUNT_READ_CACHE_SECONDS)
        return datetime.now(timezone.utc) - refreshed_at < timedelta(seconds=ACCOUNT_READ_CACHE_SECONDS)

    def cached_portfolio_data(self, avanza: Any, account_id: str, *, refresh: bool = False) -> dict[str, Any]:
        snapshot, context = self.account_snapshot_for_cache(account_id)
        cached_has_positions = False
        if isinstance(snapshot.portfolio_data, dict):
            cached_has_positions = any(
                isinstance(snapshot.portfolio_data.get(section), list) and bool(snapshot.portfolio_data.get(section))
                for section in ("withOrderbook", "withoutOrderbook")
            )
        if (
            not refresh
            and isinstance(snapshot.portfolio_data, dict)
            and self.cached_at_is_fresh(snapshot.portfolio_refreshed_at)
            and cached_has_positions
        ):
            return snapshot.portfolio_data
        data = avanza.get_accounts_positions()
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected portfolio response type: {type(data).__name__}")
        if context is not None:
            self.update_tenant_account_snapshot(context, account_id, portfolio_data=data)
        else:
            snapshot.portfolio_data = data
            snapshot.portfolio_refreshed_at = datetime.now(timezone.utc)
            snapshot.refreshed_at = snapshot.portfolio_refreshed_at
        if str(account_id or "") == str(self.selected_account_id or ""):
            self.latest_portfolio_data = data
        return data

    def cached_stoploss_items(self, avanza: Any, account_id: str, *, refresh: bool = False) -> list[dict[str, Any]]:
        snapshot, context = self.account_snapshot_for_cache(account_id)
        if not refresh and self.cached_at_is_fresh(snapshot.stoploss_refreshed_at):
            return list(snapshot.stoploss_items)
        data = avanza.get_all_stop_losses()
        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected stop-loss response type: {type(data).__name__}")
        items = [item for item in data if isinstance(item, dict)]
        if context is not None:
            self.update_tenant_account_snapshot(context, account_id, stoploss_items=items)
            snapshot = context.account_snapshots.get(str(account_id or "").strip(), snapshot)
        else:
            snapshot.stoploss_items = [
                item for item in items if stop_loss_matches_filters(item, account_id=account_id or None)
            ]
            snapshot.stoploss_refreshed_at = datetime.now(timezone.utc)
            snapshot.orders_refreshed_at = snapshot.stoploss_refreshed_at
            snapshot.refreshed_at = snapshot.stoploss_refreshed_at
        if str(account_id or "") == str(self.selected_account_id or ""):
            self.latest_stoploss_items = list(snapshot.stoploss_items)
        return list(snapshot.stoploss_items)

    def cached_open_order_items(
        self,
        avanza: Any,
        account_id: str,
        *,
        refresh: bool = False,
        require_raw: bool = False,
    ) -> tuple[Any, list[dict[str, Any]]]:
        snapshot, context = self.account_snapshot_for_cache(account_id)
        if (
            not refresh
            and self.cached_at_is_fresh(snapshot.open_orders_refreshed_at)
            and (not require_raw or snapshot.open_orders_payload is not None)
        ):
            return snapshot.open_orders_payload if require_raw else None, list(snapshot.open_order_items)
        try:
            data = avanza.get_orders()
        except Exception:
            return [], []
        items = [item for item in open_order_items(data) if isinstance(item, dict)]
        if context is not None:
            self.update_tenant_account_snapshot(context, account_id, open_orders=items)
            snapshot = context.account_snapshots.get(str(account_id or "").strip(), snapshot)
            snapshot.open_orders_payload = data
        else:
            snapshot.open_order_items = [
                item for item in items if open_order_matches_filters(item, account_id=account_id or None)
            ]
            snapshot.open_orders_payload = data
            snapshot.open_orders_refreshed_at = datetime.now(timezone.utc)
            snapshot.orders_refreshed_at = snapshot.open_orders_refreshed_at
            snapshot.refreshed_at = snapshot.open_orders_refreshed_at
        if str(account_id or "") == str(self.selected_account_id or ""):
            self.latest_open_order_items = list(snapshot.open_order_items)
        return data, list(snapshot.open_order_items)

    def filtered_portfolio_items(
        self,
        avanza: Any,
        account_id: str,
        *,
        orderbook_id: str | None = None,
        instrument_name: str | None = None,
        refresh: bool = False,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        data = self.cached_portfolio_data(avanza, account_id, refresh=refresh)
        items: list[dict[str, Any]] = []
        for section in ("withOrderbook", "withoutOrderbook"):
            for item in data.get(section, []):
                if not isinstance(item, dict) or not matches_account(item, account_id or None):
                    continue
                if orderbook_id and position_order_book_id(item) != str(orderbook_id):
                    continue
                if not name_matches_filter(nested_value(item, "instrument", "name"), instrument_name):
                    continue
                items.append(item)
        return data, items

    def portfolio_snapshot(
        self,
        avanza: Any,
        account_id: str,
        *,
        orderbook_id: str | None = None,
        instrument_name: str | None = None,
        compact: bool = False,
        refresh: bool = False,
    ) -> dict[str, Any]:
        _data, items = self.filtered_portfolio_items(
            avanza,
            account_id,
            orderbook_id=orderbook_id,
            instrument_name=instrument_name,
            refresh=refresh,
        )
        positions = [position_mcp_dict(item, self.realtime_status_for_position(item)) for item in items]
        if compact:
            positions = [
                {
                    "stock": row["stock"],
                    "orderbook_id": row["orderbook_id"],
                    "volume": row["volume"],
                    "value": row["Value"],
                    "avg_price": row["Avg Price"],
                    "day_percent": row["Day %"],
                    "day_sek": row["Day SEK"],
                    "profit_percent": row["Profit %"],
                    "profit": row["Profit"],
                    "realtime": row["Real-time"],
                }
                for row in positions
            ]
        return {
            "account_id": account_id or None,
            "positions": positions,
        }

    def filtered_stoploss_items(
        self,
        avanza: Any,
        account_id: str,
        *,
        orderbook_id: str | None = None,
        instrument_name: str | None = None,
        side: str | None = None,
        status: str | None = None,
        refresh: bool = False,
    ) -> list[dict[str, Any]]:
        data = self.cached_stoploss_items(avanza, account_id, refresh=refresh)
        return [
            item
            for item in data
            if isinstance(item, dict)
            and stop_loss_matches_filters(
                item,
                account_id=account_id or None,
                orderbook_id=orderbook_id,
                instrument_name=instrument_name,
                side=side,
                status=status,
            )
        ]

    def stoploss_snapshot(
        self,
        avanza: Any,
        account_id: str,
        *,
        orderbook_id: str | None = None,
        instrument_name: str | None = None,
        side: str | None = None,
        status: str | None = None,
        compact: bool = False,
        refresh: bool = False,
    ) -> dict[str, Any]:
        items = self.filtered_stoploss_items(
            avanza,
            account_id,
            orderbook_id=orderbook_id,
            instrument_name=instrument_name,
            side=side,
            status=status,
            refresh=refresh,
        )
        rows = [stop_loss_mcp_dict(item) for item in items]
        if compact:
            rows = [
                {
                    "stop_loss_id": row["stop_loss_id"],
                    "status": row["status"],
                    "account_id": row["account_id"],
                    "stock": row["stock"],
                    "orderbook_id": row["orderbook_id"],
                    "side": row["side"],
                    "volume": row["volume"],
                    "trigger_type": row["trigger_type"],
                    "trigger_value": row["trigger_value"],
                    "trigger_value_type": row["trigger_value_type"],
                    "order_price": row["order_price"],
                    "order_price_type": row["order_price_type"],
                    "valid_until": row["valid_until"],
                }
                for row in rows
            ]
        return {
            "account_id": account_id or None,
            "stoplosses": rows,
        }

    def filtered_open_order_items(
        self,
        avanza: Any,
        account_id: str,
        *,
        orderbook_id: str | None = None,
        instrument_name: str | None = None,
        side: str | None = None,
        status: str | None = None,
        refresh: bool = False,
        require_raw: bool = False,
    ) -> tuple[Any, list[dict[str, Any]]]:
        data, items = self.cached_open_order_items(avanza, account_id, refresh=refresh, require_raw=require_raw)
        filtered_items = [
            item
            for item in items
            if isinstance(item, dict)
            and open_order_matches_filters(
                item,
                account_id=account_id or None,
                orderbook_id=orderbook_id,
                instrument_name=instrument_name,
                side=side,
                status=status,
            )
        ]
        return data, filtered_items

    def open_orders_snapshot(
        self,
        avanza: Any,
        account_id: str,
        include_raw: bool = False,
        *,
        orderbook_id: str | None = None,
        instrument_name: str | None = None,
        side: str | None = None,
        status: str | None = None,
        compact: bool = False,
        refresh: bool = False,
    ) -> dict[str, Any]:
        data, filtered_items = self.filtered_open_order_items(
            avanza,
            account_id,
            orderbook_id=orderbook_id,
            instrument_name=instrument_name,
            side=side,
            status=status,
            refresh=refresh,
            require_raw=include_raw,
        )
        orders = [open_order_mcp_dict(item) for item in filtered_items]
        fund_orders = fund_order_items(data) if isinstance(data, dict) else []
        if compact:
            orders = [
                {
                    "order_id": row["order_id"],
                    "account_id": row["account_id"],
                    "orderbook_id": row["order_book_id"],
                    "stock": row["Stock"],
                    "side": row["side"],
                    "volume": row["Volume"],
                    "price": row["Price"],
                    "valid_until": row["Valid Until"],
                    "status": row["Status"],
                }
                for row in orders
            ]
        snapshot: dict[str, Any] = {
            "account_id": account_id or None,
            "orders": orders,
            "fund_orders": payload_to_json_safe(fund_orders),
            "fund_order_count": len(fund_orders),
        }
        if include_raw:
            snapshot["raw"] = payload_to_json_safe(data)
        return snapshot

    def transactions_snapshot(
        self,
        avanza: Any,
        account_id: str,
        *,
        orderbook_id: str | None = None,
        instrument_name: str | None = None,
        side: str | None = None,
        status: str | None = None,
        transactions_from: date | None = None,
        transactions_to: date | None = None,
        types: Any = None,
        isin: str | None = None,
        max_elements: int = 1000,
        executed_only: bool = True,
        compact: bool = False,
    ) -> dict[str, Any]:
        transaction_types = parse_transaction_types(types)
        account = self.account_by_id(account_id) if account_id else None
        account_name = str((account or {}).get("name") or "")
        payload = avanza.get_transactions_details(
            transaction_details_types=transaction_types,
            transactions_from=transactions_from,
            transactions_to=transactions_to,
            isin=isin,
            max_elements=max_elements,
        )
        items, first_date = transactions_items(payload)
        rows = [
            transaction_history_dict_row(item)
            for item in items
            if transaction_matches_instrument_filters(
                item,
                account_id=account_id or None,
                account_name=account_name or None,
                orderbook_id=orderbook_id,
                instrument_name=instrument_name,
                side=side,
                status=status,
                executed_only=executed_only,
            )
        ]
        if compact:
            rows = [
                {
                    "date": row["Trade Date"],
                    "stock": row["Stock"],
                    "type": row["Type"],
                    "volume": row["Volume"],
                    "price": row["Price"],
                    "amount": row["Amount"],
                    "result": row["Result"],
                }
                for row in rows
            ]
        truncation_risk = len(items) >= max_elements
        response = {
            "account_id": account_id or None,
            "executed_only": executed_only,
            "types": [item.value for item in transaction_types],
            "transactions_from": transactions_from.isoformat() if transactions_from else None,
            "transactions_to": transactions_to.isoformat() if transactions_to else None,
            "first_available_date": first_date,
            "max_elements": max_elements,
            "fetched_count": len(items),
            "returned_count": len(rows),
            "truncation_risk": truncation_risk,
            "transactions": rows,
        }
        if truncation_risk:
            response["warning"] = (
                f"Fetched count reached max_elements={max_elements}; older transactions may be "
                "missing. Re-run with a higher max_elements or a narrower date range."
            )
        return response

    def instrument_state_snapshot(
        self,
        avanza: Any,
        account_id: str,
        orderbook_id: str,
        *,
        transactions_from: date | None = None,
        transactions_to: date | None = None,
        include_raw_orders: bool = True,
    ) -> dict[str, Any]:
        account_id = account_id or self.require_selected_account_id()
        orderbook_id = str(orderbook_id)
        _portfolio_data, positions = self.filtered_portfolio_items(avanza, account_id, orderbook_id=orderbook_id)
        stoploss_items = self.filtered_stoploss_items(avanza, account_id, orderbook_id=orderbook_id)
        _orders_payload, open_order_items_for_instrument = self.filtered_open_order_items(
            avanza,
            account_id,
            orderbook_id=orderbook_id,
        )
        transaction_snapshot = self.transactions_snapshot(
            avanza,
            account_id,
            orderbook_id=orderbook_id,
            transactions_from=transactions_from,
            transactions_to=transactions_to,
            max_elements=1000,
            executed_only=False,
            compact=True,
        )
        quote = self.orderbook_quotes_snapshot([orderbook_id], refresh=True)["quotes"][0]
        active_buy_stops = [
            stop_loss_mcp_dict(item)
            for item in stoploss_items
            if str(item.get("status", "")).upper() == "ACTIVE" and stop_loss_side(item) == "BUY"
        ]
        active_sell_stops = [
            stop_loss_mcp_dict(item)
            for item in stoploss_items
            if str(item.get("status", "")).upper() == "ACTIVE" and stop_loss_side(item) == "SELL"
        ]
        non_active_stops = [
            stop_loss_mcp_dict(item)
            for item in stoploss_items
            if str(item.get("status", "")).upper() != "ACTIVE"
        ]
        open_orders = [open_order_mcp_dict(item) for item in open_order_items_for_instrument]
        failed_orders = [
            row
            for row in open_orders
            if str(row.get("Status", "")).upper() in {"ERROR", "FAILED", "REJECTED", "FAULTY", "FELAKTIG"}
        ]
        position = positions[0] if positions else None
        snapshot = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "account_id": account_id,
            "orderbook_id": orderbook_id,
            "quote": quote,
            "position": position_mcp_dict(position, self.realtime_status_for_position(position)) if position else None,
            "active_buy_stops": active_buy_stops,
            "active_sell_stops": active_sell_stops,
            "non_active_or_error_stops": non_active_stops,
            "open_orders": open_orders,
            "raw_open_orders_included": include_raw_orders,
            "failed_orders": failed_orders,
            "recent_transactions": transaction_snapshot["transactions"],
            "protection": summarize_stop_protection(position, stoploss_items),
        }
        if include_raw_orders:
            snapshot["open_orders_raw"] = payload_to_json_safe(open_order_items_for_instrument)
        return snapshot

    def protection_gaps_snapshot(
        self,
        avanza: Any,
        account_id: str,
        *,
        exclude_orderbook_ids: list[str] | None = None,
        exclude_eth: bool = False,
        coverage_target_percent: float = 100.0,
        exclude_non_stop_eligible: bool = False,
    ) -> dict[str, Any]:
        account_id = account_id or self.require_selected_account_id()
        excludes = {str(item) for item in (exclude_orderbook_ids or [])}
        target_fraction = max(0.0, min(float(coverage_target_percent), 100.0)) / 100.0
        _portfolio_data, positions = self.filtered_portfolio_items(avanza, account_id)
        stoploss_items = self.filtered_stoploss_items(avanza, account_id)
        stoplosses_by_orderbook: dict[str, list[dict[str, Any]]] = {}
        for item in stoploss_items:
            stoplosses_by_orderbook.setdefault(stop_loss_order_book_id(item), []).append(item)
        rows: list[dict[str, Any]] = []
        skipped_non_eligible: list[dict[str, str]] = []
        for position in positions:
            orderbook_id = position_order_book_id(position)
            stock = str(nested_value(position, "instrument", "name") or "")
            if orderbook_id in excludes:
                continue
            if exclude_eth and instrument_is_eth_like(stock, orderbook_id):
                continue
            if exclude_non_stop_eligible:
                instrument_type = str(nested_value(position, "instrument", "type") or "").upper()
                if not orderbook_id or instrument_type in {"FUND", "MUTUAL_FUND", "EXCHANGE_TRADED_FUND"}:
                    skipped_non_eligible.append({"stock": stock, "orderbook_id": orderbook_id, "instrument_type": instrument_type})
                    continue
            summary = summarize_stop_protection(position, stoplosses_by_orderbook.get(orderbook_id, []))
            target_volume = summary["holding_volume"] * target_fraction
            adjusted_gap = max(target_volume - summary["active_sell_stop_volume"], 0.0)
            if adjusted_gap <= 0 and summary["failed_stop_volume"] <= 0:
                continue
            rows.append(
                {
                    "stock": stock,
                    "orderbook_id": orderbook_id,
                    **summary,
                    "coverage_target_percent": coverage_target_percent,
                    "target_sell_stop_volume": target_volume,
                    "adjusted_protection_gap": adjusted_gap,
                }
            )
        return {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "account_id": account_id,
            "count": len(rows),
            "coverage_target_percent": coverage_target_percent,
            "gaps": rows,
            "skipped_non_stop_eligible": skipped_non_eligible,
        }

    def sold_today_buyback_state_snapshot(
        self,
        avanza: Any,
        account_id: str,
        *,
        trade_date: date | None = None,
        tight_trigger_percent_max: float = 8.0,
    ) -> dict[str, Any]:
        account_id = account_id or self.require_selected_account_id()
        target_date = trade_date or date.today()
        transaction_types = [TransactionsDetailsType.BUY, TransactionsDetailsType.SELL]
        payload = avanza.get_transactions_details(
            transaction_details_types=transaction_types,
            transactions_from=target_date,
            transactions_to=target_date,
            max_elements=5000,
        )
        items, _first_date = transactions_items(payload)
        sold_rows = summarize_sold_transactions(
            [
                item
                for item in items
                if transaction_matches_instrument_filters(
                    item,
                    account_id=account_id,
                    side="SELL",
                    executed_only=True,
                )
            ]
        )
        _portfolio_data, positions = self.filtered_portfolio_items(avanza, account_id)
        positions_by_orderbook = {position_order_book_id(item): item for item in positions}
        stoploss_items = self.filtered_stoploss_items(avanza, account_id)
        _orders_payload, order_items = self.filtered_open_order_items(avanza, account_id)
        # Same-day executed BUY fills per orderbook: already-completed buy-backs
        # must be netted so they are not reported as missing again.
        buy_fill_volume_by_orderbook: dict[str, float] = {}
        for item in items:
            if not transaction_matches_instrument_filters(item, account_id=account_id, side="BUY", executed_only=True):
                continue
            fill_orderbook = transaction_order_book_id(item)
            if fill_orderbook:
                buy_fill_volume_by_orderbook[fill_orderbook] = (
                    buy_fill_volume_by_orderbook.get(fill_orderbook, 0.0) + transaction_volume(item)
                )
        rows: list[dict[str, Any]] = []
        for sold in sold_rows:
            orderbook_id = str(sold.get("orderbook_id") or "")
            stock = str(sold.get("stock") or "")
            buy_stops = [
                item
                for item in stoploss_items
                if stop_loss_order_book_id(item) == orderbook_id
                and stop_loss_side(item) == "BUY"
                and str(item.get("status", "")).upper() == "ACTIVE"
            ]
            tight_volume = 0.0
            deep_volume = 0.0
            unclassified_volume = 0.0
            for item in buy_stops:
                volume = stop_loss_volume(item)
                trigger_percent = stop_loss_trigger_percent(item)
                if trigger_percent is None:
                    unclassified_volume += volume
                elif trigger_percent <= tight_trigger_percent_max:
                    tight_volume += volume
                else:
                    deep_volume += volume
            generated_orders = [
                open_order_mcp_dict(item)
                for item in order_items
                if open_order_order_book_id(item) == orderbook_id
            ]
            failed_statuses = {"ERROR", "FAILED", "REJECTED", "FAULTY", "FELAKTIG"}
            failed_orders = [
                row
                for row in generated_orders
                if str(row.get("Status", "")).upper() in failed_statuses
            ]
            open_buy_order_volume = 0.0
            failed_buy_order_volume = 0.0
            for row in generated_orders:
                if str(row.get("Side", "")).upper() != "BUY":
                    continue
                volume = utils.scalar_number(row.get("Volume")) or 0.0
                if str(row.get("Status", "")).upper() in failed_statuses:
                    failed_buy_order_volume += volume
                else:
                    open_buy_order_volume += volume
            current_holding = position_volume(positions_by_orderbook.get(orderbook_id, {}))
            sold_volume = float(sold.get("sold_volume") or 0.0)
            active_buyback_volume = tight_volume + deep_volume + unclassified_volume
            filled_buyback_volume = buy_fill_volume_by_orderbook.get(orderbook_id, 0.0)
            missing = max(
                sold_volume - filled_buyback_volume - active_buyback_volume - open_buy_order_volume,
                0.0,
            )
            rows.append(
                {
                    **sold,
                    "current_holding": current_holding,
                    "same_day_buy_fill_volume": filled_buyback_volume,
                    "active_tight_buyback_volume": tight_volume,
                    "active_deep_buyback_volume": deep_volume,
                    "active_unclassified_buyback_volume": unclassified_volume,
                    "open_buy_order_volume": open_buy_order_volume,
                    "failed_buy_order_volume": failed_buy_order_volume,
                    "generated_open_orders": generated_orders,
                    "failed_raw_orders": failed_orders,
                    "missing_buyback_volume": missing,
                    "tight_trigger_percent_max": tight_trigger_percent_max,
                }
            )
        return {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "account_id": account_id,
            "date": target_date.isoformat(),
            "items": rows,
        }

    def recent_fills_needing_protection_snapshot(
        self,
        avanza: Any,
        account_id: str,
        *,
        since: date | None = None,
        exclude_eth: bool = True,
    ) -> dict[str, Any]:
        account_id = account_id or self.require_selected_account_id()
        since_date = since or date.today()
        payload = avanza.get_transactions_details(
            transaction_details_types=[TransactionsDetailsType.BUY, TransactionsDetailsType.SELL],
            transactions_from=since_date,
            transactions_to=None,
            max_elements=5000,
        )
        items, _first_date = transactions_items(payload)
        bought_orderbooks = {
            transaction_order_book_id(item)
            for item in items
            if transaction_matches_instrument_filters(item, account_id=account_id, side="BUY", executed_only=True)
        }
        protection = self.protection_gaps_snapshot(avanza, account_id, exclude_eth=exclude_eth)
        rows = [
            item
            for item in protection["gaps"]
            if str(item.get("orderbook_id") or "") in bought_orderbooks
        ]
        return {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "account_id": account_id,
            "since": since_date.isoformat(),
            "exclude_eth": exclude_eth,
            "count": len(rows),
            "items": rows,
        }

    def verify_no_raw_failed_orders_snapshot(
        self,
        avanza: Any,
        account_id: str,
        *,
        orderbook_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        account_id = account_id or self.require_selected_account_id()
        ids = {str(item) for item in (orderbook_ids or []) if str(item)}
        _payload, items = self.filtered_open_order_items(avanza, account_id)
        rows = [
            open_order_mcp_dict(item)
            for item in items
            if (not ids or open_order_order_book_id(item) in ids)
            and str(item.get("status", "")).upper() in {"ERROR", "FAILED", "REJECTED", "FAULTY", "FELAKTIG"}
        ]
        return {
            "ok": not rows,
            "account_id": account_id,
            "orderbook_ids": sorted(ids),
            "failed_orders": rows,
        }

    def verify_protection_snapshot(
        self,
        avanza: Any,
        account_id: str,
        *,
        orderbook_ids: list[str] | None = None,
        full_holding: bool = True,
        exclude_eth: bool = True,
        coverage_target_percent: float = 100.0,
        exclude_orderbook_ids: list[str] | None = None,
        exclude_non_stop_eligible: bool = True,
    ) -> dict[str, Any]:
        account_id = account_id or self.require_selected_account_id()
        ids = {str(item) for item in (orderbook_ids or []) if str(item)}
        gaps_payload = self.protection_gaps_snapshot(
            avanza,
            account_id,
            exclude_orderbook_ids=list(exclude_orderbook_ids or []),
            exclude_eth=exclude_eth,
            coverage_target_percent=coverage_target_percent,
            exclude_non_stop_eligible=exclude_non_stop_eligible,
        )
        gaps = gaps_payload["gaps"]
        if ids:
            gaps = [item for item in gaps if str(item.get("orderbook_id") or "") in ids]
        if not full_holding:
            gaps = [item for item in gaps if float(item.get("active_sell_stop_volume") or 0.0) <= 0.0]
        return {
            "ok": not gaps,
            "account_id": account_id,
            "orderbook_ids": sorted(ids),
            "full_holding": full_holding,
            "exclude_eth": exclude_eth,
            "coverage_target_percent": coverage_target_percent,
            "excluded_orderbook_ids": sorted({str(i) for i in (exclude_orderbook_ids or [])}),
            "exclude_non_stop_eligible": exclude_non_stop_eligible,
            "skipped_non_stop_eligible": gaps_payload.get("skipped_non_stop_eligible", []),
            "gaps": gaps,
        }

    def quote_payload_for_order_book(self, order_book_id: str, refresh: bool = True) -> dict[str, Any] | None:
        if not order_book_id:
            return None
        cached = self.quote_payload_by_order_book.get(order_book_id)
        cached_at = self.quote_payload_checked_at.get(order_book_id)
        now = datetime.now()
        if (
            cached is not None
            and cached_at is not None
            and now - cached_at < timedelta(seconds=QUOTE_REFRESH_COALESCE_SECONDS)
        ):
            return cached
        if (
            not refresh
            and cached is not None
            and cached_at is not None
            and now - cached_at < timedelta(seconds=QUOTE_CACHE_SECONDS)
        ):
            return cached
        avanza = self.require_connection()
        try:
            payload = avanza.get_market_data(order_book_id)
        except Exception:
            return None
        if isinstance(payload, dict):
            self.quote_payload_by_order_book[order_book_id] = payload
            self.quote_payload_checked_at[order_book_id] = datetime.now()
            return payload
        if hasattr(payload, "model_dump"):
            try:
                dumped = payload.model_dump()
            except Exception:
                dumped = None
            if isinstance(dumped, dict):
                self.quote_payload_by_order_book[order_book_id] = dumped
                self.quote_payload_checked_at[order_book_id] = datetime.now()
                return dumped
        if hasattr(payload, "dict"):
            try:
                dumped = payload.dict()
            except Exception:
                dumped = None
            if isinstance(dumped, dict):
                self.quote_payload_by_order_book[order_book_id] = dumped
                self.quote_payload_checked_at[order_book_id] = datetime.now()
                return dumped
        return None

    def orderbook_quotes_snapshot(
        self,
        orderbook_ids: list[str],
        *,
        fields: list[str] | None = None,
        refresh: bool = True,
    ) -> dict[str, Any]:
        requested_ids = [str(item).strip() for item in orderbook_ids if str(item).strip()]
        ids = unique_strings(requested_ids)
        if not ids:
            raise ValueError("No valid orderbook IDs supplied.")
        if len(ids) > 100:
            raise ValueError("Limit orderbook_ids to <= 100 per request.")
        started = time.monotonic()
        rows: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        normalized_fields = [str(field).strip() for field in fields if str(field).strip()] if isinstance(fields, list) else []
        metadata_fields = {"name", "ticker", "market", "currency", "country", "instrument_type", "display_symbol"}
        needs_metadata = not normalized_fields or bool(metadata_fields.intersection(normalized_fields))
        for orderbook_id in ids:
            payload = None
            err = ""
            try:
                payload = self.quote_payload_for_order_book(orderbook_id, refresh=refresh)
            except Exception as exc:
                err = str(exc)
            if payload is None and not err:
                err = "quote_unavailable"
            row = orderbook_quote_row(
                orderbook_id,
                payload,
                fallback_name=self.holding_labels_by_order_book.get(orderbook_id, ""),
                error=err,
            )
            if needs_metadata:
                metadata = self.orderbook_metadata_for_quote(
                    orderbook_id,
                    payload if isinstance(payload, dict) else None,
                    allow_remote_lookup=refresh,
                )
                row["name"] = row.get("name") or metadata.get("name")
                row["ticker"] = normalize_symbol_candidate(row.get("ticker") or metadata.get("ticker")) or None
                row["market"] = row.get("market") or metadata.get("market")
                row["currency"] = row.get("currency") or metadata.get("currency") or infer_currency_from_metadata(metadata)
                row["country"] = metadata.get("country") or metadata.get("country_code") or infer_country_from_metadata(metadata)
                row["instrument_type"] = metadata.get("instrument_type")
                row["display_symbol"] = metadata.get("display_symbol") or display_symbol(row.get("ticker"), row.get("name"))
                if not row.get("currency"):
                    row["metadata_warnings"] = ["Currency unresolved from quote/search/index/mover metadata."]
            rows.append(row)
            if err:
                errors.append({"orderbook_id": orderbook_id, "error": err})
        if normalized_fields:
            keep = {"orderbook_id", "error"}
            keep.update(normalized_fields)
            filtered_rows: list[dict[str, Any]] = []
            for row in rows:
                filtered_rows.append({key: row.get(key) for key in row.keys() if key in keep})
            rows = filtered_rows
        elapsed_ms = int((time.monotonic() - started) * 1000.0)
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "count": len(rows),
            "requested_count": len(requested_ids),
            "deduplicated_count": len(ids),
            "poll_interval_seconds": LIVE_REFRESH_SECONDS,
            "quotes": rows,
            "errors": errors,
            "error_count": len(errors),
            "rate_limit": {
                "recommended_max_ids_per_call": 50,
                "recommended_poll_interval_seconds": LIVE_REFRESH_SECONDS,
                "elapsed_ms": elapsed_ms,
            },
        }

    def _cache_orderbook_metadata(self, orderbook_id: str, updates: dict[str, Any] | None = None) -> dict[str, Any]:
        current = self.orderbook_metadata_by_id.get(orderbook_id, {})
        merged = merged_orderbook_metadata(current, updates or {})
        if not merged.get("name"):
            cached_name = self.holding_labels_by_order_book.get(orderbook_id, "")
            if cached_name:
                merged["name"] = cached_name
        merged["orderbook_id"] = orderbook_id
        self.orderbook_metadata_by_id[orderbook_id] = merged
        self.orderbook_metadata_checked_at[orderbook_id] = datetime.now()
        return merged

    def _search_metadata_for_orderbook(self, orderbook_id: str) -> dict[str, Any]:
        avanza = self.require_connection()
        try:
            results = avanza.search_for_stock(orderbook_id, 15)
        except Exception:
            return {}
        hits = flattened_search_hits(results)
        rows = normalized_search_rows(hits, query=orderbook_id)
        match = next((row for row in rows if str(row.get("orderbook_id") or "") == orderbook_id), None)
        if not match and rows:
            match = rows[0]
        if not isinstance(match, dict):
            return {}
        return {
            "orderbook_id": orderbook_id,
            "name": str(match.get("name") or "").strip() or None,
            "ticker": normalize_symbol_candidate(str(match.get("ticker") or match.get("symbol") or "").strip()) or None,
            "market": str(match.get("market_place") or "").strip() or None,
            "currency": str(match.get("currency") or "").strip() or None,
            "country_code": str(match.get("country") or "").strip() or None,
            "country": str(match.get("country") or "").strip() or None,
            "instrument_type": str(match.get("instrument_type") or "").strip() or None,
            "display_symbol": str(match.get("display_symbol") or "").strip()
            or display_symbol(str(match.get("ticker") or match.get("symbol") or ""), str(match.get("name") or "")),
        }

    def _market_guide_metadata_for_orderbook(self, orderbook_id: str) -> dict[str, Any]:
        try:
            payload = payload_to_json_safe(avanza_ext.avanza_private_get(self.require_connection(), f"/_api/market-guide/stock/{orderbook_id}", options={}))
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        return metadata_from_market_guide_payload(orderbook_id, payload)

    def orderbook_metadata_for_quote(
        self,
        orderbook_id: str,
        quote_payload: dict[str, Any] | None = None,
        allow_remote_lookup: bool = True,
    ) -> dict[str, Any]:
        now = datetime.now()
        cached = self.orderbook_metadata_by_id.get(orderbook_id, {})
        known = KNOWN_ORDERBOOK_METADATA.get(orderbook_id, {})
        checked_at = self.orderbook_metadata_checked_at.get(orderbook_id)
        merged = merged_orderbook_metadata(
            known,
            {"orderbook_id": orderbook_id, "name": self.holding_labels_by_order_book.get(orderbook_id, "")},
        )
        merged = merged_orderbook_metadata(merged, cached)

        if isinstance(quote_payload, dict):
            merged = merged_orderbook_metadata(
                merged,
                {
                    "name": market_quote_first_text(quote_payload, (("name",), ("orderbook", "name"), ("instrument", "name"))),
                    "ticker": market_quote_first_text(quote_payload, (("ticker",), ("symbol",), ("orderbook", "symbol"))),
                    "market": market_quote_first_text(quote_payload, (("market",), ("marketPlace",), ("marketPlaceName",))),
                    "currency": market_quote_first_text(quote_payload, (("quote", "currency"), ("currency",))),
                    "country_code": market_quote_first_text(quote_payload, (("countryCode",), ("country",), ("flagCode",), ("orderbook", "countryCode"))),
                    "instrument_type": market_quote_first_text(quote_payload, (("instrumentType",), ("orderbook", "instrumentType"), ("instrument", "instrumentType"))),
                },
            )

        missing_core = (
            not merged.get("name")
            or not merged.get("ticker")
            or not merged.get("market")
            or not merged.get("currency")
            or not merged.get("country_code")
            or not merged.get("instrument_type")
        )
        stale = checked_at is None or (now - checked_at) > timedelta(seconds=ORDERBOOK_METADATA_REFRESH_SECONDS)
        if allow_remote_lookup and missing_core and stale:
            merged = merged_orderbook_metadata(merged, self._search_metadata_for_orderbook(orderbook_id))
            if (
                not merged.get("name")
                or not merged.get("ticker")
                or not merged.get("market")
                or not merged.get("currency")
                or not merged.get("country_code")
                or not merged.get("instrument_type")
            ):
                merged = merged_orderbook_metadata(merged, self._market_guide_metadata_for_orderbook(orderbook_id))

        return self._cache_orderbook_metadata(orderbook_id, merged)

    def stoploss_metadata_for_orderbook(self, order_book_id: str) -> dict[str, Any]:
        quote_payload = self.quote_payload_for_order_book(order_book_id, refresh=False)
        return self.orderbook_metadata_for_quote(order_book_id, quote_payload, allow_remote_lookup=True)

    def realtime_quotes_snapshot(self, account_id: str) -> list[dict[str, Any]]:
        avanza = self.require_connection()
        positions_data = self.latest_portfolio_data
        if not isinstance(positions_data, dict):
            positions_data = avanza.get_accounts_positions()
            if isinstance(positions_data, dict):
                self.latest_portfolio_data = positions_data
        if not isinstance(positions_data, dict):
            return []

        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for section in ("withOrderbook", "withoutOrderbook"):
            for item in positions_data.get(section, []):
                if not isinstance(item, dict) or not matches_account(item, account_id or None):
                    continue
                order_book_id = position_order_book_id(item)
                if not order_book_id or order_book_id in seen:
                    continue
                seen.add(order_book_id)
                quote_payload = self.quote_payload_for_order_book(order_book_id) or {}
                rows.append(
                    {
                        "stock": str(nested_value(item, "instrument", "name")),
                        "order_book_id": order_book_id,
                        "last": market_quote_last(quote_payload),
                        "change_percent": market_quote_change_percent(quote_payload),
                        "realtime_status": self.realtime_status_for_position(item, quote_payload),
                        "updated_at": datetime.now().isoformat(timespec="seconds"),
                    }
                )
        return rows

    def realtime_status_for_position(
        self,
        item: dict[str, Any],
        quote_payload: dict[str, Any] | None = None,
        allow_lookup: bool = True,
    ) -> str:
        direct_status = first_known_realtime_status(item, quote_payload or {})
        order_book_id = position_order_book_id(item)
        if not order_book_id:
            return direct_status

        if direct_status != "Unknown":
            self.realtime_status_by_order_book[order_book_id] = direct_status
            self.realtime_status_checked_at[order_book_id] = datetime.now()
            return direct_status

        checked_at = self.realtime_status_checked_at.get(order_book_id)
        cached_status = self.realtime_status_by_order_book.get(order_book_id)
        if cached_status and checked_at and datetime.now() - checked_at < timedelta(seconds=REALTIME_STATUS_REFRESH_SECONDS):
            return cached_status

        if not allow_lookup:
            return cached_status or "Unknown"

        try:
            status = lookup_realtime_status(self.require_connection(), item)
        except Exception:
            status = "Unknown"
        self.realtime_status_by_order_book[order_book_id] = status
        self.realtime_status_checked_at[order_book_id] = datetime.now()
        return status

    def prefetch_quote_and_status_by_order_book(
        self,
        data: dict[str, Any],
        account_id: str | None,
        allow_status_lookup: bool = False,
    ) -> tuple[dict[str, dict[str, Any] | None], dict[str, str]]:
        quote_payloads: dict[str, dict[str, Any] | None] = {}
        realtime_statuses: dict[str, str] = {}
        seen: set[str] = set()
        for section in ("withOrderbook", "withoutOrderbook"):
            for item in data.get(section, []):
                if not isinstance(item, dict) or not matches_account(item, account_id):
                    continue
                order_book_id = position_order_book_id(item)
                if not order_book_id or order_book_id in seen:
                    continue
                seen.add(order_book_id)
                quote_payload = self.quote_payload_for_order_book(order_book_id, refresh=True)
                quote_payloads[order_book_id] = quote_payload
                realtime_statuses[order_book_id] = self.realtime_status_for_position(
                    item,
                    quote_payload,
                    allow_lookup=allow_status_lookup,
                )
        return quote_payloads, realtime_statuses
