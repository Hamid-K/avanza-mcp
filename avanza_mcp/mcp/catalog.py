"""Static MCP tool catalog and tool-set constants."""

from avanza_mcp.config import (
    ACCOUNT_PERFORMANCE_PERIOD_CHOICES,
    STOPLOSS_ORDER_VALID_DAYS_DEFAULT,
    TRADINGVIEW_DEFAULT_EXCHANGE,
    TRADINGVIEW_DEFAULT_MARKET,
    TRADINGVIEW_WATCHLIST_ROW_LIMIT,
    TRANSACTION_TYPE_CHOICES,
)

MCP_COMPACT_FILTER_PROPERTIES = {
    "account_id": {"type": "string"},
    "orderbook_id": {"type": ["string", "integer"]},
    "order_book_id": {"type": ["string", "integer"]},
    "instrument_name": {"type": "string"},
    "side": {"type": "string", "enum": ["BUY", "SELL", "buy", "sell"]},
    "status": {"type": "string"},
    "compact": {"type": "boolean", "default": False},
}

MCP_DATE_FILTER_PROPERTIES = {
    "transactions_from": {"type": "string"},
    "transactions_to": {"type": "string"},
    "changed_since": {"type": "string"},
    "date": {"type": "string"},
    "from": {"type": "string"},
    "to": {"type": "string"},
}


MCP_TOOLS = [
    {
        "name": "avanza_status",
        "description": "Show TUI MCP bridge status, selected account, and current safety mode.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "avanza_capabilities",
        "description": "Return consolidated MCP safety/capability status for automation loops (paper/live guards, account context, and tool availability).",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "avanza_live_session_authorize",
        "description": "Explicitly enable live mutation permission for this active MCP/TUI session. Requires read_write mode and acknowledge=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "acknowledge": {"type": "boolean", "default": False},
                "reason": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_live_session_revoke",
        "description": "Disable live mutation permission for this MCP/TUI session and force paper-only mode.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "avanza_accounts",
        "description": "List Avanza accounts currently visible to the authenticated TUI session.",
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_sessions",
        "description": "List authenticated Avanza tenant sessions currently loaded in TUI.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "avanza_select_session",
        "description": "Switch active MCP/TUI session context to a loaded tenant session (read-only context switch).",
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_select_account",
        "description": "Safely switch MCP/TUI selected account context. Read-only context switch; no order mutations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "account_id": {"type": "string"},
            },
            "required": ["account_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_account_performance",
        "description": "Read Avanza account performance/development for the selected or supplied account_id over a chosen period.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "period": {"type": "string", "enum": ACCOUNT_PERFORMANCE_PERIOD_CHOICES, "default": "SINCE_START"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "tv_scrape_symbol_analytics",
        "description": "Fetch TradingView symbol analytics and technical recommendation barometers from public scanner data.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "exchange": {"type": "string", "default": TRADINGVIEW_DEFAULT_EXCHANGE},
                "market": {"type": "string", "default": TRADINGVIEW_DEFAULT_MARKET},
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "tv_scrape_symbol_full",
        "description": "Fetch rich TradingView symbol payload (scanner analytics + technical labels + symbol profile metadata) in LLM-friendly JSON.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "exchange": {"type": "string", "default": TRADINGVIEW_DEFAULT_EXCHANGE},
                "market": {"type": "string", "default": TRADINGVIEW_DEFAULT_MARKET},
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "tv_auth_session_start",
        "description": "Open TradingView login page in browser and show session setup instructions for authenticated MCP usage.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "open_browser": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "tv_auth_session_set",
        "description": "Persist TradingView session cookie for authenticated tv_auth_* MCP tools.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cookie": {"type": "string"},
                "sessionid": {"type": "string"},
                "sessionid_sign": {"type": "string"},
                "source": {"type": "string", "default": "manual"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "tv_auth_session_login_auto",
        "description": "Open instrumented browser, let user log in normally, and automatically capture/save TradingView session cookies.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "timeout_seconds": {"type": "integer", "minimum": 30, "maximum": 1800, "default": 300},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "tv_auth_session_status",
        "description": "Show saved TradingView authenticated session status used by tv_auth_* tools.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "tv_auth_session_clear",
        "description": "Delete saved TradingView authenticated session cookie.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "tv_auth_symbol_analytics",
        "description": "Fetch TradingView symbol analytics in authenticated mode (inherits account entitlements from supplied TradingView cookie/session).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "exchange": {"type": "string", "default": TRADINGVIEW_DEFAULT_EXCHANGE},
                "market": {"type": "string", "default": TRADINGVIEW_DEFAULT_MARKET},
                "cookie": {"type": "string"},
                "sessionid": {"type": "string"},
                "sessionid_sign": {"type": "string"},
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "tv_auth_symbol_full",
        "description": "Fetch rich TradingView symbol payload in authenticated mode (scanner analytics + technical labels + profile metadata + entitlement context).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "exchange": {"type": "string", "default": TRADINGVIEW_DEFAULT_EXCHANGE},
                "market": {"type": "string", "default": TRADINGVIEW_DEFAULT_MARKET},
                "cookie": {"type": "string"},
                "sessionid": {"type": "string"},
                "sessionid_sign": {"type": "string"},
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "tv_preopen_symbol_snapshot",
        "description": "Return a compact TradingView pre-open/extended-hours review snapshot for one symbol.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "exchange": {"type": "string", "default": TRADINGVIEW_DEFAULT_EXCHANGE},
                "market": {"type": "string", "default": TRADINGVIEW_DEFAULT_MARKET},
                "authenticated": {"type": "boolean", "default": True},
                "cookie": {"type": "string"},
                "sessionid": {"type": "string"},
                "sessionid_sign": {"type": "string"},
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "tv_preopen_batch_snapshot",
        "description": "Return TradingView pre-open snapshots for a symbol list with per-symbol error isolation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbols": {
                    "type": "array",
                    "items": {
                        "oneOf": [
                            {"type": "string"},
                            {
                                "type": "object",
                                "properties": {
                                    "symbol": {"type": "string"},
                                    "exchange": {"type": "string"},
                                },
                                "required": ["symbol"],
                                "additionalProperties": True,
                            },
                        ]
                    },
                },
                "exchange": {"type": "string", "default": TRADINGVIEW_DEFAULT_EXCHANGE},
                "market": {"type": "string", "default": TRADINGVIEW_DEFAULT_MARKET},
                "authenticated": {"type": "boolean", "default": True},
                "compact": {"type": "boolean", "default": True},
                "max_concurrency": {"type": "integer", "minimum": 1, "maximum": 8, "default": 4},
                "cookie": {"type": "string"},
                "sessionid": {"type": "string"},
                "sessionid_sign": {"type": "string"},
            },
            "required": ["symbols"],
            "additionalProperties": False,
        },
    },
    {
        "name": "tv_scrape_heatmap",
        "description": "Fetch TradingView market heatmap rows (top movers) using free scanner data.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "market": {"type": "string", "default": TRADINGVIEW_DEFAULT_MARKET},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                "exchanges": {"type": "array", "items": {"type": "string"}},
                "min_market_cap": {"type": "number"},
                "min_price": {"type": "number"},
                "min_volume": {"type": "number"},
                "sector": {"type": "string"},
                "industry": {"type": "string"},
                "sort_by": {"type": "string", "default": "change"},
                "include_premarket": {"type": "boolean", "default": True},
                "exclude_otc": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_tv_preopen_portfolio_bundle",
        "description": "Read-only Avanza portfolio protection state merged with TradingView pre-open market/technical snapshots.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "include_symbols": {
                    "type": "array",
                    "items": {
                        "oneOf": [
                            {"type": "string"},
                            {
                                "type": "object",
                                "properties": {
                                    "symbol": {"type": "string"},
                                    "exchange": {"type": "string"},
                                },
                                "required": ["symbol"],
                                "additionalProperties": True,
                            },
                        ]
                    },
                },
                "market": {"type": "string", "default": TRADINGVIEW_DEFAULT_MARKET},
                "authenticated": {"type": "boolean", "default": True},
                "compact": {"type": "boolean", "default": True},
                "cookie": {"type": "string"},
                "sessionid": {"type": "string"},
                "sessionid_sign": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "tv_auth_watchlist",
        "description": "Best-effort TradingView watchlist monitor in authenticated mode (cookie/session required for private list context).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "reference_symbol": {"type": "string", "default": "AAPL"},
                "exchange": {"type": "string", "default": TRADINGVIEW_DEFAULT_EXCHANGE},
                "market": {"type": "string", "default": TRADINGVIEW_DEFAULT_MARKET},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
                "cookie": {"type": "string"},
                "sessionid": {"type": "string"},
                "sessionid_sign": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "tv_auth_custom_lists",
        "description": "Load authenticated TradingView custom tracking lists and rows from your TradingView profile session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "list_id": {"type": "string"},
                "list_name": {"type": "string"},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": TRADINGVIEW_WATCHLIST_ROW_LIMIT,
                    "default": TRADINGVIEW_WATCHLIST_ROW_LIMIT,
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "zacks_scrape_symbol",
        "description": "Fetch Zacks rank via quote-feed and scrape symbol/report pages for Earnings ESP plus visible analysis text (best effort; HTML may be blocked without valid browser session/cookies).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "cookie": {"type": "string"},
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "fmp_analyst_recommendations",
        "description": "Fetch analyst recommendation history for a symbol from Financial Modeling Prep (requires FMP API key).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 52},
                "api_key": {"type": "string"},
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "polygon_analyst_insights",
        "description": "Fetch analyst insights/ratings for a symbol from Polygon Benzinga feed (requires Polygon API key).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 50},
                "date": {"type": "string"},
                "api_key": {"type": "string"},
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "sec_filings_recent",
        "description": "Fetch recent SEC EDGAR filings by ticker or CIK (official SEC data).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "cik": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "fred_series",
        "description": "Fetch FRED macro observations (requires a free FRED API key via FRED_API_KEY or api_key input).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "series_id": {"type": "string"},
                "api_key": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 120},
                "sort_order": {"type": "string", "enum": ["asc", "desc"], "default": "desc"},
            },
            "required": ["series_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "data_source_status",
        "description": "Return current health, freshness, and safety flags for Avanza, TradingView, Zacks, FMP, Polygon, SEC, and FRED source integrations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "default": "AAPL"},
                "exchange": {"type": "string", "default": TRADINGVIEW_DEFAULT_EXCHANGE},
                "market": {"type": "string", "default": TRADINGVIEW_DEFAULT_MARKET},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "signal_context_bundle",
        "description": "Build a compact cross-source signal bundle (TradingView technicals + SEC filings + optional Zacks/FMP/Polygon + optional FRED macro).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "symbols": {
                    "type": "array",
                    "items": {
                        "oneOf": [
                            {"type": "string"},
                            {
                                "type": "object",
                                "properties": {
                                    "symbol": {"type": "string"},
                                    "exchange": {"type": "string"},
                                },
                                "required": ["symbol"],
                                "additionalProperties": True,
                            },
                        ]
                    },
                },
                "exchange": {"type": "string", "default": TRADINGVIEW_DEFAULT_EXCHANGE},
                "market": {"type": "string", "default": TRADINGVIEW_DEFAULT_MARKET},
                "include_tradingview": {"type": "boolean", "default": True},
                "include_zacks": {"type": "boolean", "default": True},
                "include_fmp": {"type": "boolean", "default": False},
                "include_polygon": {"type": "boolean", "default": False},
                "include_sec": {"type": "boolean", "default": True},
                "compact": {"type": "boolean", "default": False},
                "fred_series_id": {"type": "string"},
                "fred_api_key": {"type": "string"},
                "fmp_api_key": {"type": "string"},
                "polygon_api_key": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_portfolio",
        "description": "List portfolio positions for the selected account, optionally filtered by one instrument.",
        "inputSchema": {
            "type": "object",
            "properties": MCP_COMPACT_FILTER_PROPERTIES,
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_stoplosses",
        "description": "List stop-loss orders for the selected account, optionally filtered by instrument, side, or status.",
        "inputSchema": {
            "type": "object",
            "properties": MCP_COMPACT_FILTER_PROPERTIES,
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_open_orders",
        "description": "List live open/pending regular orders, optionally filtered by instrument, side, or status, with stable IDs for edit/cancel flows.",
        "inputSchema": {
            "type": "object",
            "properties": MCP_COMPACT_FILTER_PROPERTIES,
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_open_orders_raw",
        "description": "Debug tool: return normalized open orders plus raw Avanza order payload for schema diagnostics.",
        "inputSchema": {
            "type": "object",
            "properties": {
                **MCP_COMPACT_FILTER_PROPERTIES,
                "include_raw": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_ongoing_orders",
        "description": "List ongoing orders for the selected account: live stop-losses + live open orders, with optional paper active orders.",
        "inputSchema": {
            "type": "object",
            "properties": {
                **MCP_COMPACT_FILTER_PROPERTIES,
                "include_paper": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_transactions",
        "description": "List executed orders/history (BUY/SELL by default) with optional account/date/type filters.",
        "inputSchema": {
            "type": "object",
            "properties": {
                **MCP_COMPACT_FILTER_PROPERTIES,
                **MCP_DATE_FILTER_PROPERTIES,
                "types": {
                    "type": "array",
                    "items": {"type": "string", "enum": TRANSACTION_TYPE_CHOICES},
                },
                "isin": {"type": "string"},
                "max_elements": {"type": "integer", "minimum": 1, "maximum": 20000, "default": 1000},
                "executed_only": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_live_snapshot",
        "description": "Read a decision-ready snapshot for polling loops; supports compact instrument filtering to reduce payload size.",
        "inputSchema": {
            "type": "object",
            "properties": MCP_COMPACT_FILTER_PROPERTIES,
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_position",
        "description": "Read one account position by orderbook_id without dumping the full portfolio.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "orderbook_id": {"type": ["string", "integer"]},
            },
            "required": ["account_id", "orderbook_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_instrument_stoplosses",
        "description": "Read stop-loss rows for one instrument/account with optional side/status filters.",
        "inputSchema": {
            "type": "object",
            "properties": {
                **MCP_COMPACT_FILTER_PROPERTIES,
                "account_id": {"type": "string"},
                "orderbook_id": {"type": ["string", "integer"]},
            },
            "required": ["account_id", "orderbook_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_instrument_open_orders",
        "description": "Read open/pending regular orders for one instrument/account, with optional raw payload diagnostics.",
        "inputSchema": {
            "type": "object",
            "properties": {
                **MCP_COMPACT_FILTER_PROPERTIES,
                "account_id": {"type": "string"},
                "orderbook_id": {"type": ["string", "integer"]},
                "include_raw": {"type": "boolean", "default": True},
            },
            "required": ["account_id", "orderbook_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_instrument_transactions",
        "description": "Read recent transactions for one instrument/account without dumping unrelated history.",
        "inputSchema": {
            "type": "object",
            "properties": {
                **MCP_COMPACT_FILTER_PROPERTIES,
                **MCP_DATE_FILTER_PROPERTIES,
                "account_id": {"type": "string"},
                "orderbook_id": {"type": ["string", "integer"]},
                "max_elements": {"type": "integer", "minimum": 1, "maximum": 20000, "default": 1000},
                "executed_only": {"type": "boolean", "default": True},
            },
            "required": ["account_id", "orderbook_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_instrument_state",
        "description": "Read one instrument's quote, position, active/error stops, open orders, recent transactions, and protection summary.",
        "inputSchema": {
            "type": "object",
            "properties": {
                **MCP_DATE_FILTER_PROPERTIES,
                "account_id": {"type": "string"},
                "orderbook_id": {"type": ["string", "integer"]},
                "include_raw": {"type": "boolean", "default": True},
            },
            "required": ["account_id", "orderbook_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_protection_gaps",
        "description": "Return positions whose active sell stop-loss volume is below current holding, plus error stops.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "exclude_orderbook_ids": {"type": "array", "items": {"type": ["string", "integer"]}},
                "exclude_eth": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_sold_today_buyback_state",
        "description": "Summarize same-day sold instruments and whether active buy-back stops/orders cover sold volume.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "date": {"type": "string"},
                "tight_trigger_percent_max": {"type": "number", "default": 8.0},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_recent_fills_needing_protection",
        "description": "Return recently bought non-ETH instruments whose active sell protection is below current holding.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "since": {"type": "string"},
                "exclude_eth": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_verify_no_raw_failed_orders",
        "description": "Compact post-mutation check for failed/rejected open regular orders.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "orderbook_ids": {"type": "array", "items": {"type": ["string", "integer"]}},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_verify_protection",
        "description": "Compact post-mutation check that positions are covered by active sell stop-losses.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "orderbook_ids": {"type": "array", "items": {"type": ["string", "integer"]}},
                "full_holding": {"type": "boolean", "default": True},
                "exclude_eth": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_realtime_quotes",
        "description": "Fetch real-time quote snapshot for selected account holdings (best with a 5s polling loop).",
        "inputSchema": {
            "type": "object",
            "properties": {"account_id": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_orderbook_quotes",
        "description": "Fetch arbitrary quote snapshots for supplied orderbook IDs (supports 5s polling loops for 20-50 symbols).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "orderbook_ids": {
                    "type": "array",
                    "items": {"type": ["string", "integer"]},
                },
                "fields": {"type": "array", "items": {"type": "string"}},
                "refresh": {"type": "boolean", "default": True},
            },
            "required": ["orderbook_ids"],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_market_movers",
        "description": "Fetch Avanza market movers (gainers/losers) with optional country/market/turnover filters.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "countryCodes": {"type": "array", "items": {"type": "string"}, "default": ["SE"]},
                "marketPlaces": {"type": "array", "items": {"type": "string"}},
                "min_price": {"type": "number"},
                "min_total_value_traded": {"type": "number"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 30},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_index_constituents",
        "description": "Fetch index constituents (default OMXS30) with optional quote/spread enrichment for building a liquid scalp universe.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "index_id": {"type": ["string", "integer"], "default": "19002"},
                "index_name": {"type": "string", "default": "OMXS30"},
                "include_quotes": {"type": "boolean", "default": False},
                "include_spread": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_fee_estimate",
        "description": "Estimate courtage/FX costs and break-even move for a planned trade (conservative assumptions when exact class data is unavailable).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "orderbook_id": {"type": "string"},
                "side": {"type": "string"},
                "price": {"type": "number"},
                "quantity": {"type": "integer"},
                "currency": {"type": "string"},
                "market": {"type": "string"},
                "brokerage_class": {"type": "string"},
            },
            "required": ["account_id", "orderbook_id", "side", "price", "quantity"],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_search_stock",
        "description": "Search Avanza stock/order book data by name, ticker, or ISIN.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_paper_stoploss_set",
        "description": "Create a local paper stop-loss order. This never places an Avanza order and is allowed in MCP read-only mode.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "order_book_id": {"type": "string"},
                "instrument": {"type": "string"},
                "trigger_type": {"type": "string"},
                "trigger_value": {"type": "number"},
                "trigger_value_type": {"type": "string", "default": "%"},
                "valid_until": {"type": "string"},
                "order_type": {"type": "string", "default": "sell"},
                "order_price": {"type": "number"},
                "order_price_type": {"type": "string", "default": "%"},
                "volume": {"type": "number"},
                "order_valid_days": {"type": "integer", "default": STOPLOSS_ORDER_VALID_DAYS_DEFAULT},
                "trigger_on_market_maker_quote": {"type": "boolean", "default": False},
                "short_selling_allowed": {"type": "boolean", "default": False},
            },
            "required": [
                "account_id",
                "order_book_id",
                "trigger_value",
                "order_price",
                "volume",
            ],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_paper_orders",
        "description": "List local paper-trading orders and events for the selected account, or a supplied account_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "active_only": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_paper_positions",
        "description": "List paper positions for a selected account/session, with optional active-only filter.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "session_id": {"type": "string"},
                "tenant_session_id": {"type": "string"},
                "active_only": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_paper_trades",
        "description": "List completed paper trades (entry+exit ledger rows) for account/session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "session_id": {"type": "string"},
                "tenant_session_id": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_paper_session_summary",
        "description": "Return P/L summary for a paper trading session/account.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "session_id": {"type": "string"},
                "tenant_session_id": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_paper_order_set",
        "description": "Create a local paper buy/sell order. This never places an Avanza order and is allowed in MCP read-only mode.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "order_book_id": {"type": "string"},
                "instrument": {"type": "string"},
                "order_type": {"type": "string", "default": "buy"},
                "price": {"type": "number"},
                "valid_until": {"type": "string"},
                "volume": {"type": "integer"},
                "condition": {"type": "string", "default": "normal"},
                "session_id": {"type": "string"},
                "tenant_session_id": {"type": "string"},
                "fill_immediately": {"type": "boolean", "default": False},
                "entry_reason": {"type": "string"},
                "stop_price": {"type": "number"},
                "target_price": {"type": "number"},
            },
            "required": ["account_id", "order_book_id", "price", "valid_until", "volume"],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_paper_order_exit",
        "description": "Close an open paper position by position_id or orderbook_id and create a completed paper trade entry.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "position_id": {"type": "string"},
                "orderbook_id": {"type": "string"},
                "exit_price": {"type": "number"},
                "exit_reason": {"type": "string"},
                "session_id": {"type": "string"},
                "tenant_session_id": {"type": "string"},
            },
            "required": ["account_id", "exit_price"],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_paper_risk_state",
        "description": "Evaluate paper-session guardrails before allowing a new trade entry.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "tenant_session_id": {"type": "string"},
                "account_id": {"type": "string"},
                "max_open_trades": {"type": "integer", "default": 3},
                "max_trade_notional_sek": {"type": "number", "default": 5000},
                "max_loss_per_trade_sek": {"type": "number", "default": 250},
                "max_session_loss_sek": {"type": "number", "default": 800},
                "stop_after_consecutive_losses": {"type": "integer", "default": 3},
            },
            "required": ["account_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_scalp_watchlist_set",
        "description": "Store/update a named scalp watchlist (orderbook IDs + optional labels) in local paper session state.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "watchlist_id": {"type": "string"},
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "orderbook_id": {"type": ["string", "integer"]},
                            "label": {"type": "string"},
                        },
                        "required": ["orderbook_id"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["watchlist_id", "items"],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_scalp_watchlist_get",
        "description": "Load a named scalp watchlist and optionally include current quotes for all members.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "watchlist_id": {"type": "string"},
                "include_quotes": {"type": "boolean", "default": True},
            },
            "required": ["watchlist_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_paper_cancel",
        "description": "Cancel a local paper order. This never changes Avanza and is allowed in MCP read-only mode.",
        "inputSchema": {
            "type": "object",
            "properties": {"paper_order_id": {"type": "string"}},
            "required": ["paper_order_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_stoploss_set",
        "description": "Dry-run or place a stop-loss order. Live placement requires TUI R/W mode and confirm=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "order_book_id": {"type": "string"},
                "trigger_type": {"type": "string"},
                "trigger_value": {"type": "number"},
                "trigger_value_type": {"type": "string", "default": "%"},
                "valid_until": {"type": "string"},
                "order_type": {"type": "string", "default": "sell"},
                "order_price": {"type": "number"},
                "order_price_type": {"type": "string", "default": "%"},
                "volume": {"type": "number"},
                "order_valid_days": {"type": "integer", "default": STOPLOSS_ORDER_VALID_DAYS_DEFAULT},
                "trigger_on_market_maker_quote": {"type": "boolean", "default": False},
                "short_selling_allowed": {"type": "boolean", "default": False},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": [
                "account_id",
                "order_book_id",
                "trigger_value",
                "order_price",
                "volume",
            ],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_stoploss_set_batch",
        "description": "Place multiple stop-loss orders with per-item validation/readback. Live placement requires TUI R/W mode, live authorization, and confirm=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tenant_session_id": {"type": "string"},
                "session_id": {"type": "string"},
                "account_id": {"type": "string"},
                "confirm": {"type": "boolean", "default": False},
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "order_book_id": {"type": "string"},
                            "trigger_type": {"type": "string"},
                            "trigger_value": {"type": "number"},
                            "trigger_value_type": {"type": "string", "default": "%"},
                            "valid_until": {"type": "string"},
                            "order_type": {"type": "string", "default": "sell"},
                            "order_price": {"type": "number"},
                            "order_price_type": {"type": "string", "default": "%"},
                            "volume": {"type": "number"},
                            "order_valid_days": {"type": "integer", "default": STOPLOSS_ORDER_VALID_DAYS_DEFAULT},
                            "trigger_on_market_maker_quote": {"type": "boolean", "default": False},
                            "short_selling_allowed": {"type": "boolean", "default": False},
                        },
                        "required": ["order_book_id", "trigger_value", "order_price", "volume"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["account_id", "items"],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_order_set",
        "description": "Dry-run or place a regular buy/sell order. Live placement requires TUI R/W mode and confirm=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "order_book_id": {"type": "string"},
                "order_type": {"type": "string", "default": "buy"},
                "price": {"type": "number"},
                "valid_until": {"type": "string"},
                "volume": {"type": "integer"},
                "condition": {"type": "string", "default": "normal"},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["account_id", "order_book_id", "price", "valid_until", "volume"],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_order_edit",
        "description": "Dry-run or update an existing open order (price/volume/valid_until). Live update requires TUI R/W mode and confirm=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "order_id": {"type": "string"},
                "price": {"type": "number"},
                "valid_until": {"type": "string"},
                "volume": {"type": "integer"},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["account_id", "order_id", "price", "valid_until", "volume"],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_open_order_edit",
        "description": "Dry-run or update an existing open/pending regular order (alias of avanza_order_edit). Live update requires TUI R/W mode and confirm=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "order_id": {"type": "string"},
                "price": {"type": "number"},
                "valid_until": {"type": "string"},
                "volume": {"type": "integer"},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["account_id", "order_id", "price", "valid_until", "volume"],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_order_delete",
        "description": "Dry-run or delete a regular open order. Live deletion requires TUI R/W mode and confirm=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "order_id": {"type": "string"},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["account_id", "order_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_open_order_cancel",
        "description": "Dry-run or cancel an existing open/pending regular order (alias of avanza_order_delete). Live cancellation requires TUI R/W mode and confirm=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "order_id": {"type": "string"},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["account_id", "order_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_stoploss_delete",
        "description": "Dry-run or delete a stop-loss order. Live deletion requires TUI R/W mode and confirm=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "stop_loss_id": {"type": "string"},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["account_id", "stop_loss_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "avanza_stoploss_edit",
        "description": "Dry-run or edit an existing stop-loss (delete old + place new). Supports gliding triggers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "stop_loss_id": {"type": "string"},
                "order_book_id": {"type": "string"},
                "trigger_type": {"type": "string"},
                "trigger_value": {"type": "number"},
                "trigger_value_type": {"type": "string", "default": "%"},
                "valid_until": {"type": "string"},
                "order_type": {"type": "string", "default": "sell"},
                "order_price": {"type": "number"},
                "order_price_type": {"type": "string", "default": "%"},
                "volume": {"type": "number"},
                "order_valid_days": {"type": "integer", "default": STOPLOSS_ORDER_VALID_DAYS_DEFAULT},
                "trigger_on_market_maker_quote": {"type": "boolean", "default": False},
                "short_selling_allowed": {"type": "boolean", "default": False},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": [
                "account_id",
                "stop_loss_id",
                "order_book_id",
                "trigger_value",
                "order_price",
                "volume",
            ],
            "additionalProperties": False,
        },
    },
]

PAPER_SESSION_ID_TOOLS = {
    "avanza_paper_order_set",
    "avanza_paper_order_exit",
    "avanza_paper_positions",
    "avanza_paper_trades",
    "avanza_paper_session_summary",
    "avanza_paper_risk_state",
}
TENANT_SESSION_CONTROL_TOOLS = {"avanza_select_session"}
TENANT_SESSION_SCOPED_TOOLS = {
    "avanza_status",
    "avanza_capabilities",
    "avanza_live_session_authorize",
    "avanza_live_session_revoke",
    "avanza_accounts",
    "avanza_select_account",
    "avanza_account_performance",
    "avanza_portfolio",
    "avanza_stoplosses",
    "avanza_open_orders",
    "avanza_open_orders_raw",
    "avanza_ongoing_orders",
    "avanza_transactions",
    "avanza_live_snapshot",
    "avanza_position",
    "avanza_instrument_stoplosses",
    "avanza_instrument_open_orders",
    "avanza_instrument_transactions",
    "avanza_instrument_state",
    "avanza_protection_gaps",
    "avanza_sold_today_buyback_state",
    "avanza_recent_fills_needing_protection",
    "avanza_verify_no_raw_failed_orders",
    "avanza_verify_protection",
    "avanza_realtime_quotes",
    "avanza_tv_preopen_portfolio_bundle",
    "avanza_fee_estimate",
    "avanza_stoploss_set",
    "avanza_stoploss_set_batch",
    "avanza_order_set",
    "avanza_order_edit",
    "avanza_open_order_edit",
    "avanza_order_delete",
    "avanza_open_order_cancel",
    "avanza_stoploss_delete",
    "avanza_stoploss_edit",
    *PAPER_SESSION_ID_TOOLS,
}
