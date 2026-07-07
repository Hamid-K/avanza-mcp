#!/usr/bin/env python3
import argparse
import cProfile
import copy
from dataclasses import dataclass, field
import getpass
import hashlib
import html
import io
import json
import os
import pstats
import re
import secrets
import shutil
import subprocess
import sys
import tomllib
import threading
import time
import textwrap
import webbrowser
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from avanza import Avanza
from avanza.constants import Condition, HttpMethod, InstrumentType, OrderType, StopLossPriceType, StopLossTriggerType, TimePeriod, TransactionsDetailsType
from avanza.entities import StopLossOrderEvent, StopLossTrigger
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Footer, Header, Input, RichLog, Select, Static, Switch, TabbedContent, TabPane


from avanza_mcp import config
from avanza_mcp.config import *  # noqa: F401,F403 -- transitional; removed at end of split
from avanza_mcp.models import AccountDataSnapshot, AvanzaTenantSession
from avanza_mcp import utils
from avanza_mcp.external import http as ext_http
from avanza_mcp.auth import connect, onepassword_command, onepassword_credentials, onepassword_field_value, onepassword_item_json, prompt_credentials
from avanza_mcp.stoploss_rules import max_valid_until_date, normalize_stoploss_order_valid_days, stoploss_triggered_order_expiry, validate_valid_until
from avanza_mcp.utils import append_jsonl, clamp, create_session_log_path, http_status_code_from_exception, is_unauthorized_http_error, mcp_call_log_line, mcp_result_log_detail, mcp_result_log_suffix, mcp_side_badge, mcp_stock_marker, mcp_trade_detail, strip_markup, summarize_mcp_result, timestamp
from avanza_mcp.external.http import append_cookie_header, bounded_text, external_http_headers, html_document_text, html_meta_content, html_title_text, mask_secret, normalize_text, parse_cookie_value
from avanza_mcp import avanza_ext, update_check
from avanza_mcp.external import feeds
from avanza_mcp.external import zacks as zacks_feed
from avanza_mcp.update_check import is_version_outdated, normalize_version_text, update_check_enabled, version_tuple
from avanza_mcp.external.zacks import zacks_analysis_summary_from_html, zacks_blocked_html, zacks_section_excerpt
from avanza_mcp.external.feeds import sec_cik_text, sec_lookup_cik, sec_ticker_index
from avanza_mcp.avanza_ext import estimate_avanza_fee
from avanza_mcp.external import tradingview_data as tv_data
from avanza_mcp.external import tradingview_session as tv_session
from avanza_mcp.external.tradingview_session import clear_tradingview_session, load_tradingview_session, load_tradingview_session_metadata, save_tradingview_session, save_tradingview_session_metadata, tradingview_auto_login_and_capture_session, tradingview_cookie_from_browser_cookies, tradingview_cookie_from_inputs, tradingview_session_backend, tradingview_session_status
from avanza_mcp.external.tradingview_data import normalize_tv_symbol, recommendation_label, should_retry_tv_scan_error, tradingview_batch_rows_by_request, tradingview_compact_preopen_row, tradingview_extract_symbol_candidates_from_html, tradingview_filter_heatmap_rows, tradingview_freshness_warning, tradingview_heatmap_snapshot, tradingview_heatmap_sort_value, tradingview_init_data_value_from_html, tradingview_json_ld_objects_from_html, tradingview_market_hint_for_exchange, tradingview_market_state, tradingview_numeric_field, tradingview_numeric_id, tradingview_preopen_batch_snapshot, tradingview_preopen_from_full_snapshot, tradingview_premarket_change, tradingview_row_match_keys, tradingview_symbol_attempts, tradingview_symbol_full_snapshot_from_row, tradingview_symbol_profile_html, tradingview_symbol_profile_metadata_from_html, tradingview_symbol_request_parts, tradingview_watchlist_entry_matches_target, tradingview_watchlist_id_from_input, tradingview_watchlist_snapshot, tv_row_to_dict, tv_symbol_core, unique_strings
from avanza_mcp.utils import nested_value, run_blocking_in_thread, scalar_number
from avanza_mcp.stoploss_rules import enforce_live_stoploss_order_valid_days, stoploss_order_valid_days_warnings
from avanza_mcp.market_data import account_performance_summary_from_payload, display_symbol, infer_country_from_metadata, infer_currency_from_metadata, map_account_performance_period, market_quote_change_percent, market_quote_first_text, market_quote_last, merged_orderbook_metadata, metadata_from_market_guide_payload, normalize_symbol_candidate, order_account_id, order_stock_name, orderbook_quote_row, payload_to_json_safe, trailing_parenthesized_symbol
from avanza_mcp.rendering import account_display_name, account_id_for_item, account_metric_values, account_row, account_rows_from_overview, active_paper_order_row, active_stop_loss_row, amount, build_order_preview, build_stop_loss_preview, changed_position_row, compact_account_type, compact_single_line, default_account, enum_value, first_known_realtime_status, format_order_request, format_stop_loss_request, holding_search_options, lookup_realtime_status, market_clock_text, matches_account, money_text, normalize_order_side, open_order_account_id, open_order_activity_row, open_order_items, open_order_mcp_dict, open_order_order_book_id, order_request_log_lines, parse_date, parse_price_type, plain_cell_value, position_order_book_id, position_row, position_state_row, position_state_row_with_quote, position_trade_target, profit_metric_label, realtime_status, render_accounts_overview, render_message, render_order_request, render_portfolio_positions, render_portfolio_summary, render_result, render_stop_loss_request, rows_as_dicts, sortable_cell_value, stop_loss_request_log_lines, stoploss_holding_options, stoploss_volume_by_order_book, trade_action_badge, trade_action_from_cell
from avanza_mcp.records import filter_mover_rows, first_nested_text_for_keys, flattened_search_hits, index_constituent_row, instrument_is_eth_like, mcp_orderbook_filter, movers_rows_from_payload, name_matches_filter, normalized_search_rows, open_order_matches_filters, parse_optional_iso_date, parse_transaction_types, position_mcp_dict, position_volume, render_orders, render_search_results, render_stoplosses, render_transactions_history, search_hit_label, search_hit_order_book_id, search_rows_with_market_data, stop_loss_account_id, stop_loss_matches_filters, stop_loss_mcp_dict, stop_loss_order_book_id, stop_loss_side, stop_loss_trigger_percent, stop_loss_volume, stoploss_instrument_metadata, summarize_sold_transactions, summarize_stop_protection, transaction_activity_row, transaction_history_dict_row, transaction_matches_filters, transaction_matches_instrument_filters, transaction_order_book_id, transaction_order_history_row, transactions_items
from avanza_mcp.paper import append_paper_event, cancel_paper_order, create_paper_order, create_paper_stop_loss_order, load_paper_session, paper_exit_position, paper_open_position, paper_orders, paper_positions, paper_risk_state, paper_session_id, paper_session_summary, paper_trades, save_paper_session





def pane_weights_after_drag(
    start_positions_weight: float,
    start_activity_weight: float,
    delta_rows: int,
) -> tuple[float, float]:
    delta_weight = delta_rows * PANE_RESIZE_STEP
    positions_weight = clamp(start_positions_weight + delta_weight, MIN_PANE_WEIGHT, MAX_PANE_WEIGHT)
    activity_weight = clamp(start_activity_weight - delta_weight, MIN_PANE_WEIGHT, MAX_PANE_WEIGHT)
    return positions_weight, activity_weight


def side_panel_width_after_drag(start_width: int, delta_columns: int) -> int:
    return clamp(start_width - delta_columns, MIN_ACTIVE_TRADES_WIDTH, MAX_ACTIVE_TRADES_WIDTH)


def ticket_pane_width_after_drag(start_width: int, delta_columns: int) -> int:
    return clamp(start_width - delta_columns, MIN_TICKET_PANE_WIDTH, MAX_TICKET_PANE_WIDTH)


def selected_table_row_key(table: DataTable) -> Any | None:
    if table.row_count == 0:
        return None
    try:
        return table.ordered_rows[table.cursor_row].key
    except Exception:
        return None


def restore_table_row_selection(table: DataTable, row_key: Any | None) -> None:
    if row_key is None:
        return
    try:
        table.move_cursor(row=table.get_row_index(row_key), animate=False, scroll=False)
    except Exception:
        return



class PaneResizer(Static):
    def __init__(self) -> None:
        super().__init__("─", id="pane-resizer")

    @staticmethod
    def event_y(event: events.MouseDown | events.MouseMove | events.MouseUp) -> int:
        return int(event.screen_y if event.screen_y is not None else event.y)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self.capture_mouse(True)
        self.add_class("dragging")
        self.app.start_pane_resize(self.event_y(event))
        event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        self.app.update_pane_resize(self.event_y(event))
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        self.release_mouse()
        self.remove_class("dragging")
        self.app.finish_pane_resize()
        event.stop()


class ActivityPaneResizer(Static):
    def __init__(self) -> None:
        super().__init__("─", id="activity-resizer")

    @staticmethod
    def event_y(event: events.MouseDown | events.MouseMove | events.MouseUp) -> int:
        return int(event.screen_y if event.screen_y is not None else event.y)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self.capture_mouse(True)
        self.add_class("dragging")
        self.app.start_activity_resize(self.event_y(event))
        event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        self.app.update_activity_resize(self.event_y(event))
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        self.release_mouse()
        self.remove_class("dragging")
        self.app.finish_activity_resize()
        event.stop()


class SidePaneResizer(Static):
    def __init__(self) -> None:
        super().__init__("│", id="side-pane-resizer")

    @staticmethod
    def event_x(event: events.MouseDown | events.MouseMove | events.MouseUp) -> int:
        return int(event.screen_x if event.screen_x is not None else event.x)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self.capture_mouse(True)
        self.add_class("dragging")
        self.app.start_side_pane_resize(self.event_x(event))
        event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        self.app.update_side_pane_resize(self.event_x(event))
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        self.release_mouse()
        self.remove_class("dragging")
        self.app.finish_side_pane_resize()
        event.stop()


class TicketPaneResizer(Static):
    def __init__(self, ticket: str) -> None:
        super().__init__("│", id=f"{ticket}-ticket-resizer", classes="ticket-resizer")

    @staticmethod
    def event_x(event: events.MouseDown | events.MouseMove | events.MouseUp) -> int:
        return int(event.screen_x if event.screen_x is not None else event.x)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self.capture_mouse(True)
        self.add_class("dragging")
        self.app.start_ticket_pane_resize(self.event_x(event))
        event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        self.app.update_ticket_pane_resize(self.event_x(event))
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        self.release_mouse()
        self.remove_class("dragging")
        self.app.finish_ticket_pane_resize()
        event.stop()


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
        "description": "Scrape Zacks symbol page for rank, Earnings ESP, and freely visible analysis/report summary text (best effort; may be blocked without valid browser session/cookies).",
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


def mcp_tools_catalog() -> list[dict[str, Any]]:
    """Return MCP tool schemas with normalized multi-tenant scope fields."""
    tools: list[dict[str, Any]] = copy.deepcopy(MCP_TOOLS)
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name", "")).strip()
        if not name.startswith("avanza_"):
            continue
        schema = tool.get("inputSchema")
        if not isinstance(schema, dict):
            continue
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            properties = {}
            schema["properties"] = properties

        if name in TENANT_SESSION_SCOPED_TOOLS and name not in TENANT_SESSION_CONTROL_TOOLS:
            properties.setdefault(
                "tenant_session_id",
                {
                    "type": "string",
                    "description": "Optional tenant session scope id for multi-session TUI/MCP routing.",
                },
            )

        if (
            name in TENANT_SESSION_SCOPED_TOOLS
            and name not in PAPER_SESSION_ID_TOOLS
            and name != "avanza_select_session"
        ):
            properties.setdefault(
                "session_id",
                {
                    "type": "string",
                    "description": "Legacy alias for tenant_session_id (non-paper tools only).",
                },
            )
    return tools


class AvanzaMcpHttpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], handler_class: type[BaseHTTPRequestHandler], app: "AvanzaTradingTui", token: str) -> None:
        super().__init__(server_address, handler_class)
        self.app = app
        self.token = token


class AvanzaMcpRequestHandler(BaseHTTPRequestHandler):
    server: AvanzaMcpHttpServer

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def send_json(self, status_code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self) -> bool:
        expected = self.server.token
        auth = self.headers.get("Authorization", "")
        header_token = self.headers.get("X-Avanza-MCP-Token", "")
        return auth == f"Bearer {expected}" or header_token == expected

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Expected JSON object.")
        return data

    def do_GET(self) -> None:
        if self.path != "/status":
            self.send_json(404, {"error": "not found"})
            return
        if not self.authorized():
            self.send_json(401, {"error": "unauthorized"})
            return
        try:
            payload = self.server.app.call_from_thread(self.server.app.mcp_status_payload)
            self.send_json(200, payload)
        except Exception as exc:
            self.send_json(500, {"ok": False, "error": str(exc)})

    def do_POST(self) -> None:
        if self.path != "/call":
            self.send_json(404, {"error": "not found"})
            return
        if not self.authorized():
            self.send_json(401, {"error": "unauthorized"})
            return
        try:
            request = self.read_json_body()
            tool = str(request.get("tool", ""))
            arguments = request.get("arguments") or {}
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be an object.")
            payload = self.server.app.call_from_thread(self.server.app.handle_mcp_tool_call, tool, arguments)
            self.send_json(200, payload)
        except Exception as exc:
            self.send_json(500, {"ok": False, "error": str(exc)})


def mcp_session_payload(host: str, port: int, token: str, read_write: bool) -> dict[str, Any]:
    return {
        "url": f"http://{host}:{port}",
        "token": token,
        "read_write": read_write,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "proxy_command": f"python {Path(__file__).name} mcp",
    }


def mcp_session_backend() -> str:
    value = str(os.getenv("AVANZA_MCP_SESSION_BACKEND", "auto") or "auto").strip().lower()
    if value in {"keychain", "file", "auto"}:
        return value
    return "auto"


def mcp_keychain_account(path: Path) -> str:
    scope = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:16]
    return f"mcp_session::{scope}"


def mcp_keychain_get_token(path: Path) -> str:
    if not tv_session.tradingview_keychain_supported():
        return ""
    account = mcp_keychain_account(path)
    result = subprocess.run(
        ["security", "find-generic-password", "-a", account, "-s", MCP_KEYCHAIN_SERVICE, "-w"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return str(result.stdout or "").strip()


def mcp_keychain_set_token(path: Path, token: str) -> tuple[bool, str]:
    if not tv_session.tradingview_keychain_supported():
        return False, "keychain not supported"
    account = mcp_keychain_account(path)
    result = subprocess.run(
        [
            "security",
            "add-generic-password",
            "-a",
            account,
            "-s",
            MCP_KEYCHAIN_SERVICE,
            "-w",
            token,
            "-U",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return True, ""
    error = str(result.stderr or result.stdout or "").strip()
    return False, error or f"security exited with {result.returncode}"


def mcp_keychain_delete_token(path: Path) -> tuple[bool, str]:
    if not tv_session.tradingview_keychain_supported():
        return False, "keychain not supported"
    account = mcp_keychain_account(path)
    result = subprocess.run(
        ["security", "delete-generic-password", "-a", account, "-s", MCP_KEYCHAIN_SERVICE],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return True, ""
    error = str(result.stderr or result.stdout or "").strip().lower()
    if "could not be found" in error or "item not found" in error:
        return False, ""
    return False, error or f"security exited with {result.returncode}"


def write_mcp_session_file(path: Path, payload: dict[str, Any]) -> None:
    write_payload = dict(payload)
    token = str(write_payload.get("token", "") or "").strip()
    backend = mcp_session_backend()
    storage = "file"
    keychain_error = ""
    if token and backend in {"auto", "keychain"}:
        saved, keychain_error = mcp_keychain_set_token(path, token)
        if saved:
            storage = "keychain"
            write_payload.pop("token", None)
        elif backend == "keychain":
            raise RuntimeError(f"Could not save MCP session token in keychain: {keychain_error}")
    write_payload["storage"] = storage
    write_payload["backend"] = backend
    if keychain_error:
        write_payload["keychain_error"] = keychain_error
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(write_payload, indent=2), encoding="utf-8")
    os.replace(temp_path, path)
    try:
        path.chmod(0o600)
    except Exception:
        pass


def remove_mcp_session_file(path: Path | None = None) -> None:
    path = path or MCP_SESSION_FILE
    mcp_keychain_delete_token(path)
    try:
        path.unlink()
    except FileNotFoundError:
        pass


class AvanzaTradingTui(App):
    CSS = """
    Screen {
        layout: vertical;
        background: $surface;
    }

    #login-screen {
        height: 1fr;
        align: center middle;
        padding: 2 4;
    }

    #login-card {
        width: 50;
        height: auto;
        border: tall $primary;
        padding: 1 3;
        background: $panel;
    }

    #login-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #login-subtitle {
        color: $text-muted;
        margin-bottom: 1;
    }

    #login-progress {
        display: none;
        height: 1;
        margin-top: 1;
        color: $accent;
        text-style: bold;
    }

    #login-progress-detail {
        display: none;
        height: 1;
        color: $text-muted;
    }

    #workspace {
        display: none;
        height: 1fr;
    }

    #topbar {
        height: 9;
        padding: 0 3;
        background: $panel;
        border-bottom: solid $primary;
    }

    #topbar-grid {
        height: 8;
    }

    #left-info {
        width: 1fr;
        height: 8;
    }

    #right-controls {
        width: 74;
        min-width: 74;
        height: 8;
        padding: 0 1;
    }

    #account-row {
        height: 3;
        margin-bottom: 0;
        align: left top;
    }

    #app-title {
        width: 13;
        min-width: 13;
        height: 3;
        margin-right: 1;
        content-align: left middle;
        text-style: bold;
    }

    .account-select-block {
        height: 3;
        margin-right: 1;
    }

    .account-select-label {
        height: 1;
        margin-left: 0;
        color: $text-muted;
    }

    #session-select-block {
        width: 23;
        min-width: 21;
    }

    #account-select-block {
        width: 43;
        min-width: 36;
        max-width: 46;
    }

    #extra-login-block {
        width: 26;
        min-width: 24;
        max-width: 28;
        padding-top: 1;
        margin-right: 0;
    }

    #session-select,
    #account-select {
        height: 2;
        text-wrap: nowrap;
        content-align: left middle;
    }

    #open-extra-login {
        min-width: 0;
        width: 100%;
        height: 2;
        text-wrap: nowrap;
        text-overflow: ellipsis;
        text-align: center;
        content-align: center middle;
        margin-top: 0;
    }

    #logout-selected-session {
        min-width: 16;
    }

    #refresh-selected-session {
        min-width: 19;
    }

    #session-auth-badge {
        min-width: 20;
        content-align: left middle;
        text-wrap: nowrap;
    }

    #metric-grid {
        height: 4;
        margin-top: 1;
    }

    .metric-card {
        width: 1fr;
        height: 4;
        margin: 0 1 0 0;
        padding: 0 1;
        background: $boost;
        border-left: solid $primary;
        text-align: center;
        content-align: center middle;
    }

    #metric-total {
        border-left: solid $accent;
    }

    #metric-buying {
        border-left: solid $warning;
    }

    #metric-profit {
        border-left: solid $success;
    }

    #metric-status {
        border-left: solid $secondary;
    }

    #metric-profit {
        padding: 0;
    }

    #profit-cycle {
        min-width: 12;
        width: 100%;
        height: 1;
        margin: 0;
        padding: 0 1;
        background: $boost;
        color: $text-muted;
        text-style: bold;
        text-align: center;
        content-align: center middle;
    }

    #metric-profit-value {
        height: 3;
        padding: 0 1;
        text-align: center;
        content-align: center middle;
    }

    #clock-status {
        height: 2;
        content-align: center middle;
        color: $accent;
        text-style: bold;
    }

    #button-controls {
        height: 2;
        align: center middle;
        margin-bottom: 0;
    }

    #view-controls {
        height: 2;
        align: center middle;
        margin-top: 0;
    }

    #view-label {
        width: auto;
        margin-right: 2;
        color: $text-muted;
        text-style: bold;
    }

    #controls-separator {
        height: 1;
        margin: 0 1;
        border-top: solid $primary-darken-3;
    }

    .view-tab {
        min-width: 10;
        height: 1;
        padding: 0 1;
        background: #0f3f73;
        color: #ffffff;
        border: none;
        text-style: bold;
    }

    .view-tab:hover {
        background: #1662ad;
        color: #ffffff;
    }

    #open-orders-overlay {
        min-width: 10;
    }

    #open-transactions-overlay {
        min-width: 14;
    }

    #open-tv-lists-overlay {
        min-width: 18;
    }

    #toggle-controls {
        height: 2;
        align: center middle;
        margin-top: 0;
    }

    .toggle-control {
        width: auto;
        height: 1;
        margin-left: 1;
        align: left middle;
    }

    #live-status {
        width: 9;
        margin-right: 2;
        color: $success;
    }

    .mode-toggle-box {
        min-width: 3;
        width: 3;
        height: 1;
        margin: 0;
        padding: 0;
        text-style: bold;
    }

    .mode-toggle-box.enabled {
        background: $success-darken-2;
        color: $success-lighten-3;
    }

    .mode-toggle-box.disabled {
        background: $error-darken-3;
        color: $error-lighten-2;
    }

    .mode-toggle-label {
        width: auto;
        min-width: 5;
        height: 1;
        margin-left: 1;
        margin-right: 1;
        color: $text;
    }

    #main {
        height: 1fr;
        width: 1fr;
        padding: 1;
    }

    #workspace-tabs {
        height: 1fr;
    }

    #workspace-tabs Tabs {
        height: 1;
    }

    #workspace-tabs TabPane {
        padding: 0;
        height: 1fr;
    }

    #body {
        height: 1fr;
    }

    #active-trades-panel {
        width: 42;
        height: 1fr;
        padding: 1 1 1 0;
    }

    #side-pane-resizer {
        width: 1;
        height: 1fr;
        content-align: center middle;
        color: $text-muted;
        background: $boost;
    }

    #side-pane-resizer:hover {
        color: $text;
        background: $primary-darken-3;
    }

    #side-pane-resizer.dragging {
        color: $text;
        background: $accent;
    }

    #active-trades-table {
        height: 1fr;
    }

    #paper-tab-layout {
        height: 1fr;
        padding: 1;
    }

    #paper-summary {
        height: 3;
        border: solid $primary;
        padding: 0 1;
        background: $boost;
        margin-bottom: 1;
    }

    #paper-positions-table,
    #paper-orders-table {
        height: 1fr;
    }

    .panel {
        border: solid $primary;
        padding: 0 1;
        height: auto;
    }

    DataTable {
        height: 1fr;
        background: $panel;
        color: $text;
    }

    DataTable > .datatable--header {
        background: $primary-darken-3;
        color: $primary-lighten-3;
        text-style: bold;
    }

    DataTable > .datatable--header-hover {
        background: $accent;
        color: $text;
    }

    DataTable > .datatable--even-row {
        background: $surface-lighten-1 35%;
    }

    #positions-panel {
        height: 2fr;
    }

    #pane-resizer {
        height: 1;
        content-align: center middle;
        color: $text-muted;
        background: $boost;
    }

    #pane-resizer:hover {
        color: $text;
        background: $primary-darken-3;
    }

    #pane-resizer.dragging {
        color: $text;
        background: $accent;
    }

    #activity-panel {
        height: 1fr;
    }

    #activity-table-section {
        height: 3fr;
    }

    #activity-logs-section {
        height: 1fr;
        layout: vertical;
    }

    #activity-controls {
        height: 1;
        margin: 0;
        padding: 0;
    }

    #activity-resizer {
        height: 1;
        content-align: center middle;
        color: $text-muted;
        background: $boost;
    }

    #activity-resizer:hover {
        color: $text;
        background: $primary-darken-3;
    }

    #activity-resizer.dragging {
        color: $text;
        background: $accent;
    }

    #portfolio-table {
        height: 1fr;
    }

    #stoploss-table {
        height: 1fr;
    }

    #stoploss-modal,
    #order-modal,
    #cancel-modal {
        display: none;
        dock: right;
        width: 64;
        height: 1fr;
        margin: 0;
        padding: 1 2;
        border: tall $warning;
        background: $panel;
    }

    #extra-login-modal {
        display: none;
        layer: overlay;
        align: center middle;
        width: 1fr;
        height: 1fr;
        background: rgba(0, 0, 0, 0.45);
    }

    #extra-login-card {
        width: 62;
        min-width: 58;
        max-width: 68;
        height: auto;
        border: tall $primary;
        padding: 1 2;
        background: $panel;
    }

    #extra-login-title {
        text-style: bold;
        margin-bottom: 0;
    }

    #extra-login-subtitle {
        color: $text-muted;
        margin-bottom: 0;
    }

    #extra-login-card Input {
        margin-bottom: 0;
    }

    .extra-login-actions {
        height: 1;
        margin: 0;
        align: left middle;
    }

    #extra-onepassword-title {
        margin-top: 0;
    }

    #orders-overlay,
    #transactions-overlay,
    #tv-lists-overlay {
        display: none;
        dock: top;
        width: 1fr;
        height: 1fr;
        margin: 0;
        padding: 1;
        border: tall $primary;
        background: $surface;
    }

    #orders-overlay-note,
    #transactions-overlay-note,
    #tv-lists-overlay-note {
        height: 1;
        margin-bottom: 1;
        color: $text-muted;
    }

    #orders-history-table,
    #transactions-history-table,
    #tv-lists-table {
        height: 1fr;
    }

    #tv-lists-controls {
        height: 3;
        align: left middle;
        margin-bottom: 1;
    }

    #tv-lists-select {
        width: 1fr;
        margin-right: 1;
    }

    #status-bar {
        height: 1;
        dock: bottom;
        padding: 0 1;
        background: $surface-darken-1;
    }

    #update-status {
        width: 1fr;
        content-align: right middle;
        color: $text-muted;
    }

    .ticket-resizer {
        width: 1;
        height: 1fr;
        content-align: center middle;
        color: $text-muted;
        background: $boost;
    }

    .ticket-resizer:hover {
        color: $text;
        background: $primary-darken-3;
    }

    .ticket-resizer.dragging {
        color: $text;
        background: $accent;
    }

    .ticket-content {
        width: 1fr;
        height: 100%;
        padding: 0 1;
    }

    .modal-header {
        height: 3;
        align: left middle;
    }

    .modal-title {
        width: 1fr;
        text-style: bold;
        content-align: left middle;
    }

    .modal-close {
        min-width: 3;
        width: 3;
        margin-right: 1;
        background: $error-darken-3;
        color: $error-lighten-2;
        text-style: bold;
    }

    #order-search-row {
        height: 3;
    }

    #order-search {
        width: 1fr;
    }

    #order-search-status {
        height: 1;
        color: $text-muted;
    }

    #stoploss-modal Select,
    #stoploss-modal Input,
    #order-modal Select,
    #order-modal Input {
        margin-bottom: 1;
    }

    #console-row {
        width: 1fr;
        height: 1fr;
        min-height: 6;
    }

    #log {
        width: 1fr;
        height: 100%;
        border: solid $primary;
    }

    #mcp-log {
        width: 1fr;
        height: 100%;
        border: solid $warning;
    }

    Button {
        min-width: 8;
        height: 1;
        margin: 0 1 0 0;
        padding: 0 1;
        text-style: none;
        border: none;
        background: $boost;
        color: $text;
    }

    Button:hover {
        background: $accent;
        color: $text;
    }

    Button.-primary {
        background: $primary-darken-3;
        color: $primary-lighten-3;
        border: none;
    }

    Button.-warning {
        background: $warning-darken-3;
        color: $warning-lighten-2;
        border: none;
    }

    Button.-error {
        background: $error-darken-3;
        color: $error-lighten-2;
        border: none;
        text-style: bold;
    }

    #login {
        width: 100%;
        margin-top: 1;
    }

    #place-live,
    #order-place-live,
    #cancel-confirm-button {
        min-width: 18;
    }

    #dry-run,
    #order-dry-run,
    #cancel-review {
        min-width: 12;
    }

    Input {
        margin-bottom: 1;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh_stoplosses", "Refresh Stop-Losses"),
        ("p", "refresh_portfolio", "Refresh Portfolio"),
    ]

    def __init__(self, debug: bool = False, debug_profile_top: int = DEBUG_PROFILE_TOP_DEFAULT) -> None:
        super().__init__()
        self.title = TUI_TITLE
        self.debug_mode = bool(debug)
        self.debug_profile_top = max(5, int(debug_profile_top))
        self.debug_profile_depth = 0
        self.debug_session_log_path = create_session_log_path("debug") if self.debug_mode else None
        self.avanza: Avanza | None = None
        self.tenant_sessions: dict[str, AvanzaTenantSession] = {}
        self.active_session_id: str | None = None
        self.session_label_counter = 1
        self.accounts: list[dict[str, Any]] = []
        self.selected_account_id: str | None = None
        self.live_refresh_timer = None
        self.background_session_heartbeat_timer = None
        self.clock_timer = None
        self.mcp_health_timer = None
        self.order_search_timer = None
        self.tv_lists_refresh_timer = None
        self.update_check_timer = None
        self.update_blink_timer = None
        self.login_progress_timer = None
        self.login_thread: threading.Thread | None = None
        self.shutdown_event = threading.Event()
        self.login_busy = False
        self.login_spinner_index = 0
        self.login_progress_tick = 0
        self.login_progress_messages: tuple[str, ...] = ()
        self.login_progress_index = 0
        self.login_stage_message = ""
        self.login_target_mode = "initial"
        self.login_target_session_id: str | None = None
        self.login_target_session_label: str | None = None
        self.mcp_scope_original_session_id: str | None = None
        self.mcp_scope_depth = 0
        self.live_refresh_deferred_by_mcp_scope = False
        self.last_resize: tuple[int, int] | None = None
        self.position_row_cache: dict[str, tuple[str, ...]] = {}
        self.holding_volumes_by_order_book: dict[str, str] = {}
        self.holding_labels_by_order_book: dict[str, str] = {}
        self.order_search_labels_by_order_book: dict[str, str] = {}
        self.table_sort_state: dict[str, tuple[Any, bool]] = {}
        self.realtime_status_by_order_book: dict[str, str] = {}
        self.realtime_status_checked_at: dict[str, datetime] = {}
        self.quote_payload_by_order_book: dict[str, dict[str, Any]] = {}
        self.quote_payload_checked_at: dict[str, datetime] = {}
        self.orderbook_metadata_by_id: dict[str, dict[str, Any]] = {}
        self.orderbook_metadata_checked_at: dict[str, datetime] = {}
        self.live_refresh_thread: threading.Thread | None = None
        self.live_refresh_inflight = False
        self.live_refresh_pending = False
        self.live_refresh_lock = threading.Lock()
        self.background_session_heartbeat_thread: threading.Thread | None = None
        self.background_session_heartbeat_inflight = False
        self.background_session_heartbeat_lock = threading.Lock()
        self.live_refresh_auth_blocked_sessions: set[str] = set()
        self.live_refresh_auth_last_notice_at: dict[str, float] = {}
        self.mcp_server: AvanzaMcpHttpServer | None = None
        self.mcp_thread: threading.Thread | None = None
        self.mcp_token: str | None = None
        self.mcp_write_enabled = False
        self.live_trading_allowed_for_session = False
        self.paper_mode_enabled = True
        self.paper_session_path = PAPER_SESSION_FILE
        self.paper_session = load_paper_session(self.paper_session_path)
        self.session_log_path = create_session_log_path("tui")
        self.latest_portfolio_data: dict[str, Any] | None = None
        self.latest_stoploss_items: list[dict[str, Any]] = []
        self.latest_open_order_items: list[dict[str, Any]] = []
        self.latest_tv_lists: list[dict[str, Any]] = []
        self.latest_tv_list_items: list[dict[str, Any]] = []
        self.account_snapshot_cache: dict[str, AccountDataSnapshot] = {}
        self.tv_list_option_refs: dict[str, dict[str, str]] = {}
        self.session_select_updating = False
        self.account_select_updating = False
        self.tv_lists_loaded_value = ""
        self.tv_lists_select_updating = False
        self.tv_lists_refresh_thread: threading.Thread | None = None
        self.tv_lists_refresh_inflight = False
        self.tv_lists_refresh_pending_value: str | None = None
        self.tv_lists_refresh_lock = threading.Lock()
        self.update_check_thread: threading.Thread | None = None
        self.update_check_inflight = False
        self.update_check_lock = threading.Lock()
        self.update_status_repo = GITHUB_RELEASE_REPO
        self.update_status_text = "Update: checking..."
        self.update_status_latest = ""
        self.update_status_outdated = False
        self.update_status_error = ""
        self.update_status_blink_on = True
        self.portfolio_trade_targets_by_row_key: dict[str, dict[str, str]] = {}
        self.paper_trade_targets_by_row_key: dict[str, dict[str, str]] = {}
        self.cancel_targets_by_row_key: dict[str, dict[str, str]] = {}
        self.stoploss_items_by_row_key: dict[str, dict[str, Any]] = {}
        self.pending_cancel_target: dict[str, str] | None = None
        self.pending_stoploss_edit_id: str | None = None
        self.paper_quote_cache: dict[str, dict[str, Any]] = {}
        self.positions_pane_weight = 2
        self.activity_pane_weight = 1
        self.activity_table_weight = 3
        self.activity_logs_weight = 1
        self.active_trades_width = 42
        self.ticket_pane_width = 64
        self.profit_metric_mode = "day"
        self.is_resizing_panes = False
        self.is_resizing_activity = False
        self.is_resizing_side_pane = False
        self.is_resizing_ticket_pane = False
        self.resize_start_y = 0
        self.activity_resize_start_y = 0
        self.resize_start_x = 0
        self.resize_start_positions_weight = self.positions_pane_weight
        self.resize_start_activity_weight = self.activity_pane_weight
        self.activity_resize_start_table_weight = self.activity_table_weight
        self.activity_resize_start_logs_weight = self.activity_logs_weight
        self.resize_start_active_trades_width = self.active_trades_width
        self.resize_start_ticket_pane_width = self.ticket_pane_width
        self.record_event(
            "app",
            "tui_start",
            {
                "app_version": APP_VERSION,
                "session_log": str(self.session_log_path),
                "paper_session_file": str(self.paper_session_path),
                "debug_mode": self.debug_mode,
                "debug_session_log": str(self.debug_session_log_path) if self.debug_session_log_path else "",
            },
        )
        if self.debug_mode:
            self.debug_log("Debug mode enabled.")

    def safe_call_from_thread(self, callback: Callable[..., Any], *args: Any) -> bool:
        if self.shutdown_event.is_set():
            return False
        try:
            self.call_from_thread(callback, *args)
        except RuntimeError:
            return False
        except Exception:
            return False
        return True

    def compose(self) -> ComposeResult:
        default_valid_until = max_valid_until_date().isoformat()
        yield Header()
        with Vertical(id="login-screen"):
            with Vertical(id="login-card"):
                yield Static(f"{APP_NAME} Trading Console", id="login-title")
                yield Static("Sign in once. Credentials disappear after login.", id="login-subtitle")
                yield Input(placeholder="Username", id="username")
                yield Input(placeholder="Password", id="password", password=True)
                yield Input(
                    placeholder="Current TOTP code",
                    id="totp",
                    password=True,
                    restrict=r"[0-9]*",
                    max_length=8,
                )
                yield Button("Login", id="login", variant="primary")
                yield Static("Or use 1Password CLI", id="onepassword-title")
                yield Input(placeholder="1Password item name or ID", id="onepassword-item")
                yield Input(placeholder="1Password vault (optional)", id="onepassword-vault")
                yield Button("Login with 1Password", id="onepassword-login", variant="primary")
                yield Static("", id="login-progress")
                yield Static("", id="login-progress-detail")

        with Vertical(id="workspace"):
            with Horizontal(id="topbar"):
                with Vertical(id="left-info"):
                    with Horizontal(id="account-row"):
                        yield Static(f"{APP_NAME}\nv{APP_VERSION}", id="app-title")
                        with Vertical(id="session-select-block", classes="account-select-block"):
                            yield Static("Session", classes="account-select-label")
                            yield Select([], prompt="Session", allow_blank=True, id="session-select")
                        with Vertical(id="account-select-block", classes="account-select-block"):
                            yield Static("Account", classes="account-select-label")
                            yield Select([], prompt="Select account", allow_blank=True, id="account-select")
                        with Vertical(id="extra-login-block", classes="account-select-block"):
                            yield Button("Extra Account Login", id="open-extra-login", variant="primary")
                    with Horizontal(id="metric-grid"):
                        yield Static("Total\n-", id="metric-total", classes="metric-card")
                        yield Static("Buying\n-", id="metric-buying", classes="metric-card")
                        with Vertical(id="metric-profit", classes="metric-card"):
                            yield Button("1D P/L", id="profit-cycle", classes="metric-cycle")
                            yield Static("-", id="metric-profit-value")
                        yield Static("Status\n-", id="metric-status", classes="metric-card")
                with Vertical(id="right-controls"):
                    yield Static(market_clock_text(), id="clock-status")
                    with Horizontal(id="button-controls"):
                        yield Static(f"Live {LIVE_REFRESH_SECONDS:g}s", id="live-status")
                        yield Button("Refresh", id="refresh-all", variant="primary")
                        yield Button("Logout Session", id="logout-selected-session", variant="error")
                        yield Button("Refresh Session Auth", id="refresh-selected-session", variant="default")
                        yield Static("Session auth: -", id="session-auth-badge")
                        yield Button("Reload TUI", id="reload-tui", variant="default")
                        yield Button("Order", id="open-order-modal", variant="primary")
                        yield Button("Stop-Loss", id="open-stoploss-modal", variant="warning")
                    yield Static("", id="controls-separator")
                    with Horizontal(id="view-controls"):
                        yield Static("Views", id="view-label")
                        yield Button("Orders", id="open-orders-overlay", classes="view-tab")
                        yield Button("Transactions", id="open-transactions-overlay", classes="view-tab")
                        yield Button("TradingView Lists", id="open-tv-lists-overlay", classes="view-tab")
                    with Horizontal(id="toggle-controls"):
                        with Horizontal(classes="toggle-control"):
                            yield Button("✓", id="paper-mode-toggle", classes="mode-toggle-box enabled")
                            yield Static("Paper", id="paper-mode-label", classes="mode-toggle-label")
                        with Horizontal(classes="toggle-control"):
                            yield Button("×", id="mcp-toggle", classes="mode-toggle-box disabled")
                            yield Static("MCP", id="mcp-label", classes="mode-toggle-label")
                        with Horizontal(classes="toggle-control"):
                            yield Button("×", id="mcp-write-toggle", classes="mode-toggle-box disabled")
                            yield Static("R/W", id="mcp-write-label", classes="mode-toggle-label")
            with Horizontal(id="body"):
                with Vertical(id="main"):
                    with Vertical(id="positions-panel"):
                        yield Static("Selected Account Stocks", classes="panel")
                        yield DataTable(id="portfolio-table")
                    yield PaneResizer()
                    with Vertical(id="activity-panel"):
                        yield Static("Ongoing Orders", classes="panel")
                        with Vertical(id="activity-table-section"):
                            yield DataTable(id="stoploss-table")
                        yield ActivityPaneResizer()
                        with Vertical(id="activity-logs-section"):
                            with Horizontal(id="activity-controls"):
                                yield Button("Refresh Account", id="refresh-account", variant="primary")
                                yield Button("Edit Stop-Loss", id="edit-stoploss", variant="primary")
                                yield Button("Clear Log", id="clear-log")
                            with Horizontal(id="console-row"):
                                yield RichLog(id="log", highlight=True, markup=True)
                                yield RichLog(id="mcp-log", highlight=True, markup=True)
                yield SidePaneResizer()
                with Vertical(id="active-trades-panel"):
                    yield Static("Active Stop-Losses", classes="panel")
                    yield DataTable(id="active-trades-table")
                with Horizontal(id="stoploss-modal"):
                    yield TicketPaneResizer("stoploss")
                    with Vertical(classes="ticket-content"):
                        with Horizontal(classes="modal-header"):
                            yield Button("X", id="close-stoploss-modal", classes="modal-close")
                            yield Static("New Stop-Loss", id="stoploss-modal-title", classes="modal-title")
                        yield Static("Uses the selected account.", id="stoploss-account-note")
                        yield Select([], prompt="Select portfolio holding", allow_blank=True, id="instrument-select")
                        yield Input(placeholder="Volume", id="volume", type="number")
                        yield Select(
                            [(label, label) for label in TRIGGER_TYPE_CHOICES],
                            value="follow-upwards",
                            allow_blank=False,
                            id="trigger-type",
                        )
                        yield Input(placeholder="Trigger value", id="trigger-value", type="number")
                        yield Select(
                            PRICE_TYPE_SELECT_OPTIONS,
                            value="percentage",
                            allow_blank=False,
                            id="trigger-value-type",
                        )
                        yield Input(
                            value=default_valid_until,
                            placeholder=f"Valid until ({default_valid_until})",
                            id="valid-until",
                        )
                        yield Select(
                            [(label, label) for label in ORDER_TYPE_CHOICES],
                            value="sell",
                            allow_blank=False,
                            id="order-type",
                        )
                        yield Input(placeholder="Order price", id="order-price", type="number")
                        yield Select(
                            PRICE_TYPE_SELECT_OPTIONS,
                            value="percentage",
                            allow_blank=False,
                            id="order-price-type",
                        )
                        yield Input(
                            value=str(STOPLOSS_ORDER_VALID_DAYS_DEFAULT),
                            placeholder="Order valid days",
                            id="order-valid-days",
                            type="integer",
                        )
                        yield Switch(value=False, id="trigger-on-market-maker-quote")
                        yield Static("Trigger on market-maker quote")
                        yield Switch(value=False, id="short-selling-allowed")
                        yield Static("Allow short selling")
                        yield Input(placeholder='Type "PLACE" to enable live placement', id="place-confirm")
                        with Horizontal():
                            yield Button("Review Only", id="dry-run", variant="default")
                            yield Button("Create Paper Stop-Loss", id="place-live", variant="warning")
                with Horizontal(id="order-modal"):
                    yield TicketPaneResizer("order")
                    with Vertical(classes="ticket-content"):
                        with Horizontal(classes="modal-header"):
                            yield Button("X", id="close-order-modal", classes="modal-close")
                            yield Static("New Buy/Sell Order", classes="modal-title")
                        yield Static("Uses the selected account.", id="order-account-note")
                        with Horizontal(id="order-search-row"):
                            yield Input(placeholder="Search stock, ticker, or ISIN", id="order-search")
                            yield Button("Search", id="order-search-button", variant="primary")
                        yield Static("Type at least 2 characters to search stocks.", id="order-search-status")
                        yield Select([], prompt="Select stock/order book", allow_blank=True, id="order-instrument-select")
                        yield Select(
                            [(label, label) for label in ORDER_TYPE_CHOICES],
                            value="buy",
                            allow_blank=False,
                            id="regular-order-type",
                        )
                        yield Input(placeholder="Volume", id="regular-order-volume", type="integer")
                        yield Input(placeholder="Limit price (SEK)", id="regular-order-price", type="number")
                        yield Static("Order value: -", id="regular-order-value")
                        yield Select(
                            [(label, label) for label in ORDER_CONDITION_CHOICES],
                            value="normal",
                            allow_blank=False,
                            id="regular-order-condition",
                        )
                        yield Input(
                            value=default_valid_until,
                            placeholder=f"Valid until ({default_valid_until})",
                            id="regular-order-valid-until",
                        )
                        yield Input(placeholder='Type "PLACE" to enable live placement', id="regular-order-confirm")
                        with Horizontal():
                            yield Button("Review Only", id="order-dry-run", variant="default")
                            yield Button("Create Paper Order", id="order-place-live", variant="warning")
                with Vertical(id="cancel-modal"):
                    with Horizontal(classes="modal-header"):
                        yield Button("X", id="close-cancel-modal", classes="modal-close")
                        yield Static("Cancel Order", classes="modal-title")
                    yield Static("-", id="cancel-summary")
                    yield Static('Type "CANCEL" for live Avanza cancellation.', id="cancel-instructions")
                    yield Input(placeholder='Type "CANCEL" for live cancellation', id="cancel-confirm")
                    with Horizontal():
                        yield Button("Review Only", id="cancel-review", variant="default")
                        yield Button("Cancel Order", id="cancel-confirm-button", variant="error")
            with Vertical(id="orders-overlay"):
                with Horizontal(classes="modal-header"):
                    yield Button("X", id="close-orders-overlay", classes="modal-close")
                    yield Static("Orders", classes="modal-title")
                    yield Button("Refresh", id="refresh-orders-overlay", variant="primary")
                yield Static("Completed buy/sell orders for the selected account.", id="orders-overlay-note")
                yield DataTable(id="orders-history-table")
            with Vertical(id="transactions-overlay"):
                with Horizontal(classes="modal-header"):
                    yield Button("X", id="close-transactions-overlay", classes="modal-close")
                    yield Static("Transactions", classes="modal-title")
                    yield Button("Refresh", id="refresh-transactions-overlay", variant="primary")
                yield Static("Executed orders and account transactions for the selected account.", id="transactions-overlay-note")
                yield DataTable(id="transactions-history-table")
            with Vertical(id="tv-lists-overlay"):
                with Horizontal(classes="modal-header"):
                    yield Button("X", id="close-tv-lists-overlay", classes="modal-close")
                    yield Static("TradingView Custom Lists", classes="modal-title")
                    yield Button("Refresh", id="refresh-tv-lists-overlay", variant="primary")
                yield Static(
                    f"Authenticated profile watchlists. Auto-refresh every {TRADINGVIEW_TUI_REFRESH_SECONDS:g}s while open.",
                    id="tv-lists-overlay-note",
                )
                with Horizontal(id="tv-lists-controls"):
                    yield Select([], prompt="Select TradingView list", allow_blank=True, id="tv-lists-select")
                    yield Button("Reload Lists", id="reload-tv-lists", variant="default")
                yield DataTable(id="tv-lists-table")
            with Vertical(id="extra-login-modal"):
                with Vertical(id="extra-login-card"):
                    yield Static("Login to extra accounts", id="extra-login-title")
                    yield Static("Add another tenant session without leaving the TUI.", id="extra-login-subtitle")
                    yield Input(placeholder="Session label (e.g. Personal, DarkCell AB)", id="extra-session-label")
                    yield Input(placeholder="Username", id="extra-username")
                    yield Input(placeholder="Password", id="extra-password", password=True)
                    yield Input(
                        placeholder="Current TOTP code",
                        id="extra-totp",
                        password=True,
                        restrict=r"[0-9]*",
                        max_length=8,
                    )
                    with Horizontal(classes="extra-login-actions"):
                        yield Button("Login extra account", id="extra-login-submit", variant="primary")
                        yield Button("Cancel", id="extra-login-cancel", variant="default")
                    yield Static("Or use 1Password CLI", id="extra-onepassword-title")
                    yield Input(placeholder="1Password item name or ID", id="extra-onepassword-item")
                    yield Input(placeholder="1Password vault (optional)", id="extra-onepassword-vault")
                    with Horizontal(classes="extra-login-actions"):
                        yield Button("Login with 1Password", id="extra-onepassword-login", variant="primary")
                        yield Button("Cancel", id="extra-login-cancel-2", variant="default")
        with Horizontal(id="status-bar"):
            yield Static("Update: checking...", id="update-status")
        yield Footer()

    def on_mount(self) -> None:
        stoploss_table = self.query_one("#stoploss-table", DataTable)
        stoploss_table.add_columns(
            "Kind",
            "Status",
            "Stock",
            "Side",
            "Volume",
            "Price",
            "Valid Until",
            "Cancel",
        )
        stoploss_table.cursor_type = "cell"
        stoploss_table.zebra_stripes = True

        portfolio_table = self.query_one("#portfolio-table", DataTable)
        portfolio_table.add_columns(
            "Stock",
            "B",
            "S",
            "Order Book ID",
            "Volume",
            "Value",
            "Avg Price",
            "Day %",
            "Day SEK",
            "Profit %",
            "Profit",
            "Real-time",
        )
        portfolio_table.cursor_type = "cell"
        portfolio_table.zebra_stripes = True

        active_table = self.query_one("#active-trades-table", DataTable)
        active_table.add_columns(
            "Mode",
            "Kind",
            "Stock",
            "Side",
            "Volume",
            "Trigger/Price",
            "Valid/Created",
            "Status",
            "Cancel",
        )
        active_table.cursor_type = "cell"
        active_table.zebra_stripes = True

        orders_history_table = self.query_one("#orders-history-table", DataTable)
        orders_history_table.add_columns(
            "Date",
            "Side",
            "Stock",
            "Qty",
            "Price",
            "Amount",
            "Result",
            "Account",
        )
        orders_history_table.cursor_type = "row"
        orders_history_table.zebra_stripes = True

        transactions_history_table = self.query_one("#transactions-history-table", DataTable)
        transactions_history_table.add_columns(
            "Date",
            "Account",
            "Type",
            "Description",
            "Qty",
            "Price",
            "Amount",
            "Result",
            "ISIN",
        )
        transactions_history_table.cursor_type = "row"
        transactions_history_table.zebra_stripes = True

        tv_lists_table = self.query_one("#tv-lists-table", DataTable)
        tv_lists_table.add_columns(
            "Symbol",
            "Last",
            "Chg",
            "Chg%",
            "Volume",
            "Status",
        )
        tv_lists_table.cursor_type = "row"
        tv_lists_table.zebra_stripes = True

        self.write_log("Ready. Log in, then refresh portfolio or stop-losses.")
        if self.debug_mode and self.debug_session_log_path is not None:
            self.write_log(f"[yellow]Debug profiling enabled:[/yellow] {self.debug_session_log_path}")
        self.write_mcp_log("MCP disabled. Log in, then enable MCP mode.")
        self.update_clock_status()
        self.start_clock()
        self.start_update_checker()
        self.start_background_session_heartbeat()
        if self.mcp_health_timer is None:
            self.mcp_health_timer = self.set_interval(MCP_HEALTH_CHECK_SECONDS, self.ensure_mcp_bridge_health, pause=False)
        if self.tv_lists_refresh_timer is None:
            self.tv_lists_refresh_timer = self.set_interval(
                TRADINGVIEW_TUI_REFRESH_SECONDS,
                self.refresh_tv_lists_if_visible,
                pause=False,
            )
        self.apply_ticket_pane_width(self.ticket_pane_width)
        self.apply_activity_subpane_weights(self.activity_table_weight, self.activity_logs_weight)
        self.update_mode_toggles()

    def on_resize(self, event: events.Resize) -> None:
        self.last_resize = (event.size.width, event.size.height)
        self.refresh(layout=True)
        for selector in ("#workspace", "#main", "#portfolio-table", "#stoploss-table", "#active-trades-table"):
            try:
                self.query_one(selector).refresh(layout=True)
            except Exception:
                pass
        if self.avanza and self.selected_account_id:
            self.call_after_refresh(self.refresh_selected_account_live)

    def apply_pane_weights(self, positions_weight: float, activity_weight: float) -> None:
        self.positions_pane_weight = positions_weight
        self.activity_pane_weight = activity_weight
        self.query_one("#positions-panel").styles.height = f"{positions_weight}fr"
        self.query_one("#activity-panel").styles.height = f"{activity_weight}fr"
        self.query_one("#main").refresh(layout=True)

    def apply_active_trades_width(self, width: int) -> None:
        self.active_trades_width = width
        self.query_one("#active-trades-panel").styles.width = width
        self.query_one("#body").refresh(layout=True)

    def apply_activity_subpane_weights(self, table_weight: float, logs_weight: float) -> None:
        self.activity_table_weight = table_weight
        self.activity_logs_weight = logs_weight
        self.query_one("#activity-table-section").styles.height = f"{table_weight}fr"
        self.query_one("#activity-logs-section").styles.height = f"{logs_weight}fr"
        self.query_one("#console-row").styles.height = "1fr"
        self.query_one("#activity-panel").refresh(layout=True)
        self.query_one("#activity-logs-section").refresh(layout=True)
        self.query_one("#console-row").refresh(layout=True)
        self.query_one("#log").refresh(layout=True)
        self.query_one("#mcp-log").refresh(layout=True)

    def apply_ticket_pane_width(self, width: int) -> None:
        self.ticket_pane_width = width
        for selector in ("#order-modal", "#stoploss-modal"):
            self.query_one(selector).styles.width = width
        self.refresh(layout=True)

    def start_pane_resize(self, screen_y: int) -> None:
        self.is_resizing_panes = True
        self.resize_start_y = screen_y
        self.resize_start_positions_weight = self.positions_pane_weight
        self.resize_start_activity_weight = self.activity_pane_weight

    def update_pane_resize(self, screen_y: int) -> None:
        if not self.is_resizing_panes:
            return
        delta_rows = screen_y - self.resize_start_y
        weights = pane_weights_after_drag(
            self.resize_start_positions_weight,
            self.resize_start_activity_weight,
            delta_rows,
        )
        self.apply_pane_weights(*weights)

    def finish_pane_resize(self) -> None:
        self.is_resizing_panes = False

    def start_activity_resize(self, screen_y: int) -> None:
        self.is_resizing_activity = True
        self.activity_resize_start_y = screen_y
        self.activity_resize_start_table_weight = self.activity_table_weight
        self.activity_resize_start_logs_weight = self.activity_logs_weight

    def update_activity_resize(self, screen_y: int) -> None:
        if not self.is_resizing_activity:
            return
        delta_rows = screen_y - self.activity_resize_start_y
        table_weight, logs_weight = pane_weights_after_drag(
            self.activity_resize_start_table_weight,
            self.activity_resize_start_logs_weight,
            delta_rows,
        )
        self.apply_activity_subpane_weights(table_weight, logs_weight)

    def finish_activity_resize(self) -> None:
        self.is_resizing_activity = False

    def start_side_pane_resize(self, screen_x: int) -> None:
        self.is_resizing_side_pane = True
        self.resize_start_x = screen_x
        self.resize_start_active_trades_width = self.active_trades_width

    def update_side_pane_resize(self, screen_x: int) -> None:
        if not self.is_resizing_side_pane:
            return
        delta_columns = screen_x - self.resize_start_x
        self.apply_active_trades_width(
            side_panel_width_after_drag(self.resize_start_active_trades_width, delta_columns)
        )

    def finish_side_pane_resize(self) -> None:
        self.is_resizing_side_pane = False

    def start_ticket_pane_resize(self, screen_x: int) -> None:
        self.is_resizing_ticket_pane = True
        self.resize_start_x = screen_x
        self.resize_start_ticket_pane_width = self.ticket_pane_width

    def update_ticket_pane_resize(self, screen_x: int) -> None:
        if not self.is_resizing_ticket_pane:
            return
        delta_columns = screen_x - self.resize_start_x
        self.apply_ticket_pane_width(
            ticket_pane_width_after_drag(self.resize_start_ticket_pane_width, delta_columns)
        )

    def finish_ticket_pane_resize(self) -> None:
        self.is_resizing_ticket_pane = False

    def sort_table(self, table: DataTable, column_key: Any, reverse: bool) -> None:
        table.sort(column_key, key=sortable_cell_value, reverse=reverse)
        if table.id:
            self.table_sort_state[table.id] = (column_key, reverse)

    def reapply_table_sort(self, table: DataTable) -> None:
        if not table.id:
            return
        state = self.table_sort_state.get(table.id)
        if not state:
            return
        column_key, reverse = state
        self.sort_table(table, column_key, reverse)

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        table = event.data_table
        previous_column, previous_reverse = self.table_sort_state.get(table.id or "", (None, False))
        reverse = not previous_reverse if previous_column == event.column_key else False
        self.sort_table(table, event.column_key, reverse)
        direction = "descending" if reverse else "ascending"
        self.write_log(f"Sorted {table.id or 'table'} by {event.label.plain} ({direction}).")
        event.stop()

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        if event.data_table.id == "portfolio-table":
            action = trade_action_from_cell(event.value)
            if action not in {"buy", "sell"}:
                return
            row_key = str(getattr(event.cell_key.row_key, "value", ""))
            target = self.portfolio_trade_targets_by_row_key.get(row_key)
            if not target:
                self.write_log("[yellow]Could not resolve stock row for order ticket.[/yellow]")
                return
            self.open_order_modal_for_portfolio_action(action, target)
            event.stop()
            return

        if event.data_table.id not in {"stoploss-table", "active-trades-table"}:
            return
        if plain_cell_value(event.value).strip() != "×":
            return
        row_key = str(getattr(event.cell_key.row_key, "value", ""))
        target = self.cancel_targets_by_row_key.get(row_key)
        if not target:
            self.write_log("[yellow]Could not resolve cancellation target for this row.[/yellow]")
            return
        self.open_cancel_modal(target)
        event.stop()

    def open_order_modal_for_portfolio_action(self, side: str, target: dict[str, str]) -> None:
        order_book_id = target.get("order_book_id", "")
        if not order_book_id:
            raise ValueError("Selected stock row has no order book id.")

        self.query_one("#order-search", Input).value = ""
        if self.latest_portfolio_data is not None:
            self.restore_order_holding_options()

        select = self.query_one("#order-instrument-select", Select)
        self.query_one("#regular-order-type", Select).value = side
        if order_book_id not in self.holding_labels_by_order_book:
            stock = target.get("stock") or order_book_id
            volume = target.get("volume", "")
            owned = f" - owned {volume}" if volume else ""
            select.set_options([(f"{stock}{owned} ({order_book_id})", order_book_id)])
            self.holding_labels_by_order_book[order_book_id] = stock
            self.holding_volumes_by_order_book[order_book_id] = volume

        select.value = order_book_id
        volume_input = self.query_one("#regular-order-volume", Input)
        volume_input.value = target.get("volume", "") if side == "sell" else ""
        self.update_regular_order_value()
        stock_name = target.get("stock") or order_book_id
        self.query_one("#order-search-status", Static).update(f"{side.upper()} ticket opened for {stock_name}.")
        self.query_one("#order-modal").display = True

    def update_regular_order_value(self) -> None:
        try:
            volume_text = self.input_value("regular-order-volume")
            price_text = self.input_value("regular-order-price")
            if not volume_text or not price_text:
                self.query_one("#regular-order-value", Static).update("Order value: -")
                return
            volume = int(volume_text)
            price = float(price_text)
            self.query_one("#regular-order-value", Static).update(f"Order value: {money_text(volume * price, 'SEK')}")
        except Exception:
            self.query_one("#regular-order-value", Static).update("Order value: -")

    def input_value(self, widget_id: str) -> str:
        widget = self.query_one(f"#{widget_id}")
        if isinstance(widget, Input):
            return widget.value.strip()
        if isinstance(widget, Select):
            if self.is_blank_select_value(widget.value):
                return ""
            return str(widget.value)
        raise TypeError(f"Unsupported input widget: {widget_id}")

    def required_input_value(self, widget_id: str, label: str) -> str:
        value = self.input_value(widget_id)
        if not value:
            raise ValueError(f"{label} is required.")
        return value

    def input_float_value(self, widget_id: str, label: str) -> float:
        value = self.required_input_value(widget_id, label)
        try:
            return float(value)
        except ValueError as exc:
            raise ValueError(f"{label} must be a number.") from exc

    def input_int_value(self, widget_id: str, label: str) -> int:
        value = self.required_input_value(widget_id, label)
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"{label} must be a whole number.") from exc

    def input_date_value(self, widget_id: str, label: str) -> date:
        value = self.required_input_value(widget_id, label)
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{label} must be an ISO date, for example {date.today().isoformat()}.") from exc
        return validate_valid_until(parsed, label)

    def switch_value(self, widget_id: str) -> bool:
        return bool(self.query_one(f"#{widget_id}", Switch).value)

    def clear_secret_inputs(self) -> None:
        self.query_one("#password", Input).value = ""
        self.query_one("#totp", Input).value = ""

    def set_login_controls_enabled(self, enabled: bool) -> None:
        for widget_id in (
            "username",
            "password",
            "totp",
            "onepassword-item",
            "onepassword-vault",
            "extra-session-label",
            "extra-username",
            "extra-password",
            "extra-totp",
            "extra-onepassword-item",
            "extra-onepassword-vault",
        ):
            try:
                self.query_one(f"#{widget_id}", Input).disabled = not enabled
            except Exception:
                pass
        for widget_id in (
            "login",
            "onepassword-login",
            "extra-login-submit",
            "extra-onepassword-login",
            "extra-login-cancel",
            "extra-login-cancel-2",
        ):
            try:
                self.query_one(f"#{widget_id}", Button).disabled = not enabled
            except Exception:
                pass

    def render_login_progress(self) -> None:
        spinner = LOGIN_PROGRESS_FRAMES[self.login_spinner_index % len(LOGIN_PROGRESS_FRAMES)]
        if self.login_stage_message:
            message = self.login_stage_message
        elif self.login_progress_messages:
            message = self.login_progress_messages[self.login_progress_index % len(self.login_progress_messages)]
        else:
            message = "Working..."
        detail = f"Step {self.login_progress_index + 1}/{max(1, len(self.login_progress_messages))}" if self.login_progress_messages else ""
        try:
            self.query_one("#login-progress", Static).update(f"{spinner} {message}")
            self.query_one("#login-progress-detail", Static).update(detail)
        except Exception:
            pass

    def advance_login_progress(self) -> None:
        if not self.login_busy:
            return
        self.login_spinner_index = (self.login_spinner_index + 1) % len(LOGIN_PROGRESS_FRAMES)
        self.login_progress_tick += 1
        if not self.login_stage_message and self.login_progress_messages and self.login_progress_tick % LOGIN_PROGRESS_ROTATE_TICKS == 0:
            self.login_progress_index = (self.login_progress_index + 1) % len(self.login_progress_messages)
        self.render_login_progress()

    def set_login_stage(self, message: str, index: int | None = None) -> None:
        if index is not None:
            if self.login_progress_messages:
                self.login_progress_index = int(clamp(index, 0, len(self.login_progress_messages) - 1))
            else:
                self.login_progress_index = max(0, index)
        self.login_stage_message = message
        self.render_login_progress()

    def start_login_progress(self, messages: tuple[str, ...], initial_message: str) -> None:
        self.login_busy = True
        self.login_spinner_index = 0
        self.login_progress_tick = 0
        self.login_progress_messages = messages
        self.login_progress_index = 0
        self.login_stage_message = initial_message
        self.set_login_controls_enabled(False)
        try:
            progress = self.query_one("#login-progress", Static)
            detail = self.query_one("#login-progress-detail", Static)
            progress.display = True
            detail.display = True
        except Exception:
            pass
        if self.login_progress_timer is not None:
            self.login_progress_timer.stop()
        self.login_progress_timer = self.set_interval(0.12, self.advance_login_progress, pause=False)
        self.render_login_progress()

    def stop_login_progress(self) -> None:
        self.login_busy = False
        if self.login_progress_timer is not None:
            self.login_progress_timer.stop()
            self.login_progress_timer = None
        self.login_target_mode = "initial"
        self.login_target_session_id = None
        self.login_target_session_label = None
        self.login_stage_message = ""
        self.login_progress_messages = ()
        try:
            self.query_one("#login-progress", Static).display = False
            self.query_one("#login-progress-detail", Static).display = False
        except Exception:
            pass
        self.set_login_controls_enabled(True)

    def clear_extra_secret_inputs(self) -> None:
        for widget_id in ("extra-password", "extra-totp"):
            try:
                self.query_one(f"#{widget_id}", Input).value = ""
            except Exception:
                pass

    def next_session_color(self) -> str:
        return SESSION_ACCENT_COLORS[len(self.tenant_sessions) % len(SESSION_ACCENT_COLORS)]

    def build_session_id(self, label: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", str(label or "").strip().lower()).strip("-")
        if not slug:
            slug = f"session-{self.session_label_counter}"
            self.session_label_counter += 1
        candidate = slug
        counter = 2
        while candidate in self.tenant_sessions:
            candidate = f"{slug}-{counter}"
            counter += 1
        return candidate

    def auto_session_label(self, accounts: list[dict[str, Any]], fallback: str = "Session") -> str:
        lead = default_account(accounts)
        if isinstance(lead, dict):
            name = account_display_name(lead).strip()
            if name:
                return name
        label = f"{fallback} {self.session_label_counter}"
        self.session_label_counter += 1
        return label

    def active_tenant_session(self) -> AvanzaTenantSession | None:
        if not self.active_session_id:
            return None
        return self.tenant_sessions.get(self.active_session_id)

    def tenant_session_by_id(self, session_id: str) -> AvanzaTenantSession:
        context = self.tenant_sessions.get(str(session_id or "").strip())
        if context is None:
            raise ValueError(f"Unknown session_id: {session_id}")
        return context

    def tenant_session_for_account(self, account_id: str) -> AvanzaTenantSession | None:
        token = str(account_id or "").strip()
        if not token:
            return None
        for context in self.tenant_sessions.values():
            for account in context.accounts:
                if str(account.get("id", "")) == token:
                    return context
        return None

    def account_ids_for_payload(
        self,
        context: AvanzaTenantSession,
        portfolio_data: dict[str, Any] | None,
        stoploss_items: list[dict[str, Any]],
        open_orders: list[dict[str, Any]],
    ) -> list[str]:
        account_ids = {str(account.get("id", "")) for account in context.accounts if account.get("id")}
        if context.selected_account_id:
            account_ids.add(str(context.selected_account_id))
        if isinstance(portfolio_data, dict):
            for section in ("withOrderbook", "withoutOrderbook"):
                for item in portfolio_data.get(section, []):
                    if isinstance(item, dict):
                        item_account_id = account_id_for_item(item)
                        if item_account_id:
                            account_ids.add(item_account_id)
        for item in stoploss_items:
            item_account_id = stop_loss_account_id(item)
            if item_account_id:
                account_ids.add(item_account_id)
        for item in open_orders:
            item_account_id = open_order_account_id(item)
            if item_account_id:
                account_ids.add(item_account_id)
        return sorted(account_ids)

    def update_tenant_account_snapshot(
        self,
        context: AvanzaTenantSession,
        account_id: str,
        *,
        portfolio_data: dict[str, Any] | None = None,
        stoploss_items: list[dict[str, Any]] | None = None,
        open_orders: list[dict[str, Any]] | None = None,
        refreshed_at: datetime | None = None,
    ) -> AccountDataSnapshot:
        token = str(account_id or "").strip()
        snapshot = context.account_snapshots.get(token)
        if snapshot is None:
            snapshot = AccountDataSnapshot(account_id=token)
            context.account_snapshots[token] = snapshot
        now = refreshed_at or datetime.now(timezone.utc)
        if portfolio_data is not None:
            snapshot.portfolio_data = portfolio_data
            snapshot.portfolio_refreshed_at = now
        if stoploss_items is not None:
            snapshot.stoploss_items = [
                item for item in stoploss_items if stop_loss_matches_filters(item, account_id=token)
            ]
            snapshot.stoploss_refreshed_at = now
            snapshot.orders_refreshed_at = now
        if open_orders is not None:
            snapshot.open_order_items = [
                item for item in open_orders if open_order_matches_filters(item, account_id=token)
            ]
            snapshot.open_orders_refreshed_at = now
            snapshot.orders_refreshed_at = now
        snapshot.refreshed_at = now
        snapshot.auth_valid = True
        snapshot.auth_error = ""
        return snapshot

    def update_tenant_session_data_cache(
        self,
        session_id: str,
        overview: dict[str, Any] | None,
        portfolio_data: dict[str, Any] | None,
        stoplosses: Any,
        orders: Any,
    ) -> None:
        context = self.tenant_sessions.get(str(session_id or "").strip())
        if context is None:
            return
        if isinstance(overview, dict):
            accounts = account_rows_from_overview(overview)
            if accounts:
                context.accounts = accounts
                selected = str(context.selected_account_id or "").strip()
                if not selected or not any(str(item.get("id", "")) == selected for item in accounts):
                    default = default_account(accounts)
                    context.selected_account_id = str(default.get("id", "")) if default else None
        stoploss_items = [item for item in stoplosses if isinstance(item, dict)] if isinstance(stoplosses, list) else []
        open_orders = [item for item in open_order_items(orders) if isinstance(item, dict)]
        refreshed_at = datetime.now(timezone.utc)
        account_ids = self.account_ids_for_payload(context, portfolio_data, stoploss_items, open_orders)
        for account_id in account_ids:
            self.update_tenant_account_snapshot(
                context,
                account_id,
                portfolio_data=portfolio_data if isinstance(portfolio_data, dict) else None,
                stoploss_items=stoploss_items,
                open_orders=open_orders,
                refreshed_at=refreshed_at,
            )
        selected = str(context.selected_account_id or "").strip()
        if selected:
            snapshot = context.account_snapshots.get(selected)
            if snapshot is not None:
                if isinstance(snapshot.portfolio_data, dict):
                    context.latest_portfolio_data = snapshot.portfolio_data
                context.latest_stoploss_items = list(snapshot.stoploss_items)
                context.latest_open_order_items = list(snapshot.open_order_items)
        context.auth_valid = True
        context.auth_error = ""
        self.live_refresh_auth_blocked_sessions.discard(context.session_id)
        self.live_refresh_auth_last_notice_at.pop(context.session_id, None)
        self.refresh_session_select_options()
        self.update_session_auth_badge()

    def update_active_selected_account_snapshot(self, context: AvanzaTenantSession) -> None:
        selected = str(context.selected_account_id or "").strip()
        if not selected:
            return
        snapshot = self.update_tenant_account_snapshot(
            context,
            selected,
            portfolio_data=context.latest_portfolio_data if isinstance(context.latest_portfolio_data, dict) else None,
            stoploss_items=list(context.latest_stoploss_items),
            open_orders=list(context.latest_open_order_items),
        )
        if not isinstance(snapshot.portfolio_data, dict) and isinstance(context.latest_portfolio_data, dict):
            snapshot.portfolio_data = context.latest_portfolio_data

    def sync_active_state_to_tenant(self) -> None:
        context = self.active_tenant_session()
        if context is None:
            return
        context.accounts = list(self.accounts)
        context.selected_account_id = self.selected_account_id
        context.latest_portfolio_data = self.latest_portfolio_data
        context.latest_stoploss_items = list(self.latest_stoploss_items)
        context.latest_open_order_items = list(self.latest_open_order_items)
        context.holding_volumes_by_order_book = dict(self.holding_volumes_by_order_book)
        context.holding_labels_by_order_book = dict(self.holding_labels_by_order_book)
        context.order_search_labels_by_order_book = dict(self.order_search_labels_by_order_book)
        self.update_active_selected_account_snapshot(context)

    def load_active_state_from_tenant(self, context: AvanzaTenantSession) -> None:
        self.active_session_id = context.session_id
        self.avanza = context.avanza
        self.accounts = list(context.accounts)
        self.selected_account_id = context.selected_account_id
        snapshot = context.account_snapshots.get(str(context.selected_account_id or "").strip())
        self.latest_portfolio_data = (
            snapshot.portfolio_data
            if snapshot is not None and isinstance(snapshot.portfolio_data, dict)
            else context.latest_portfolio_data
        )
        self.latest_stoploss_items = list(snapshot.stoploss_items if snapshot is not None else context.latest_stoploss_items)
        self.latest_open_order_items = list(
            snapshot.open_order_items if snapshot is not None else context.latest_open_order_items
        )
        self.holding_volumes_by_order_book = dict(context.holding_volumes_by_order_book)
        self.holding_labels_by_order_book = dict(context.holding_labels_by_order_book)
        self.order_search_labels_by_order_book = dict(context.order_search_labels_by_order_book)

    def capture_active_runtime_state(self) -> dict[str, Any]:
        return {
            "active_session_id": self.active_session_id,
            "avanza": self.avanza,
            "accounts": list(self.accounts),
            "selected_account_id": self.selected_account_id,
            "latest_portfolio_data": self.latest_portfolio_data,
            "latest_stoploss_items": list(self.latest_stoploss_items),
            "latest_open_order_items": list(self.latest_open_order_items),
            "holding_volumes_by_order_book": dict(self.holding_volumes_by_order_book),
            "holding_labels_by_order_book": dict(self.holding_labels_by_order_book),
            "order_search_labels_by_order_book": dict(self.order_search_labels_by_order_book),
        }

    def restore_active_runtime_state(self, state: dict[str, Any]) -> None:
        self.active_session_id = state.get("active_session_id")
        self.avanza = state.get("avanza")
        self.accounts = list(state.get("accounts") or [])
        self.selected_account_id = state.get("selected_account_id")
        portfolio_data = state.get("latest_portfolio_data")
        self.latest_portfolio_data = portfolio_data if isinstance(portfolio_data, dict) else None
        self.latest_stoploss_items = list(state.get("latest_stoploss_items") or [])
        self.latest_open_order_items = list(state.get("latest_open_order_items") or [])
        self.holding_volumes_by_order_book = dict(state.get("holding_volumes_by_order_book") or {})
        self.holding_labels_by_order_book = dict(state.get("holding_labels_by_order_book") or {})
        self.order_search_labels_by_order_book = dict(state.get("order_search_labels_by_order_book") or {})

    def session_summary_text(self, context: AvanzaTenantSession) -> Text:
        label = compact_single_line(context.label, max_len=24)
        styled = Text()
        styled.append("● ", style=context.color)
        styled.append(label)
        if not context.auth_valid:
            styled.append(" [EXPIRED]", style="bold red")
        return styled

    def refresh_session_select_options(self) -> None:
        try:
            session_select = self.query_one("#session-select", Select)
        except Exception:
            return
        options = [(self.session_summary_text(context), context.session_id) for context in self.tenant_sessions.values()]
        self.session_select_updating = True
        try:
            session_select.set_options(options)
            if self.active_session_id and self.active_session_id in self.tenant_sessions:
                if session_select.value != self.active_session_id:
                    session_select.value = self.active_session_id
        finally:
            self.session_select_updating = False
        self.update_session_auth_badge()

    def apply_active_session_header(self) -> None:
        title = Text(f"{APP_NAME} v{APP_VERSION}")
        try:
            self.query_one("#app-title", Static).update(title)
        except Exception:
            pass

    def workspace_widgets_ready(self) -> bool:
        try:
            self.query_one("#account-select", Select)
            self.query_one("#portfolio-table", DataTable)
            self.query_one("#stoploss-table", DataTable)
        except Exception:
            return False
        return True

    def is_blank_select_value(self, value: Any) -> bool:
        if value is None:
            return True
        for sentinel_name in ("BLANK", "NULL"):
            sentinel = getattr(Select, sentinel_name, None)
            if sentinel is not None and (value is sentinel or value == sentinel):
                return True
        text = str(value).strip()
        return text in {"", "Select.BLANK", "Select.NULL"}

    def selected_session_id(self) -> str | None:
        try:
            session_value = self.query_one("#session-select", Select).value
        except Exception:
            session_value = None
        if not self.is_blank_select_value(session_value):
            token = str(session_value).strip()
            if token:
                return token
        token = str(self.active_session_id or "").strip()
        return token or None

    def update_session_auth_badge(self) -> None:
        try:
            badge = self.query_one("#session-auth-badge", Static)
        except Exception:
            return
        session_id = self.selected_session_id() or self.active_session_id
        if not session_id:
            badge.update("Session auth: -")
            return
        context = self.tenant_sessions.get(str(session_id).strip())
        if context is None:
            badge.update("Session auth: -")
            return
        if context.auth_valid:
            badge.update("[green]Session auth: OK[/green]")
            return
        badge.update("[red]Session auth: EXPIRED[/red]")

    def session_account_option(self, account: dict[str, Any]) -> tuple[Text, str]:
        context = self.active_tenant_session()
        display = Text()
        if context is not None:
            display.append("● ", style=context.color)
        account_name = compact_single_line(account_display_name(account), max_len=32)
        account_type = compact_account_type(account.get("type", ""))
        if account_type:
            display.append(f"{account_name} [{account_type}]")
        else:
            display.append(account_name)
        return display, str(account.get("id", ""))

    def register_tenant_session(
        self,
        avanza: Avanza,
        overview: dict[str, Any],
        portfolio: dict[str, Any],
        stoplosses: Any,
        orders: Any,
        *,
        label: str | None = None,
    ) -> AvanzaTenantSession:
        accounts = account_rows_from_overview(overview)
        session_label = str(label or "").strip() or self.auto_session_label(accounts)
        session_id = self.build_session_id(session_label)
        context = AvanzaTenantSession(
            session_id=session_id,
            label=session_label,
            color=self.next_session_color(),
            avanza=avanza,
            accounts=accounts,
            selected_account_id=str(default_account(accounts).get("id", "")) if default_account(accounts) else None,
            latest_portfolio_data=portfolio if isinstance(portfolio, dict) else None,
            latest_stoploss_items=[item for item in stoplosses if isinstance(item, dict)] if isinstance(stoplosses, list) else [],
            latest_open_order_items=[item for item in open_order_items(orders) if isinstance(item, dict)],
        )
        self.tenant_sessions[session_id] = context
        self.update_tenant_session_data_cache(
            session_id,
            overview,
            portfolio if isinstance(portfolio, dict) else None,
            stoplosses,
            orders,
        )
        return context

    def activate_tenant_session(
        self,
        session_id: str,
        *,
        refresh_ui: bool = True,
        announce: bool = True,
        update_controls: bool = True,
    ) -> None:
        self.sync_active_state_to_tenant()
        context = self.tenant_session_by_id(session_id)
        self.load_active_state_from_tenant(context)
        widgets_ready = self.workspace_widgets_ready()
        if update_controls and widgets_ready:
            self.refresh_session_select_options()
            self.apply_active_session_header()
        if refresh_ui and widgets_ready:
            if isinstance(self.latest_portfolio_data, dict):
                self.position_row_cache = {}
                self.apply_accounts_overview({"accounts": self.accounts}, announce=False)
                self.apply_portfolio_data(self.latest_portfolio_data, fetch_quotes=False, allow_status_lookup=False)
            else:
                self.apply_accounts_overview({"accounts": self.accounts}, announce=False)
            self.apply_stoploss_orders_data(self.latest_stoploss_items, self.latest_open_order_items)
            self.refresh_selected_account_live()
        if announce:
            self.write_log(f"Switched session to {context.label} ({context.session_id}).")

    def clear_workspace_tables(self) -> None:
        for selector in (
            "#portfolio-table",
            "#stoploss-table",
            "#active-trades-table",
            "#orders-history-table",
            "#transactions-history-table",
            "#tv-lists-table",
        ):
            try:
                self.query_one(selector, DataTable).clear()
            except Exception:
                pass

    def logout_all_sessions(self) -> None:
        self.stop_mcp_bridge(announce=False)
        self.tenant_sessions.clear()
        self.active_session_id = None
        self.avanza = None
        self.accounts = []
        self.selected_account_id = None
        self.latest_portfolio_data = None
        self.latest_stoploss_items = []
        self.latest_open_order_items = []
        self.account_snapshot_cache.clear()
        self.position_row_cache = {}
        self.holding_volumes_by_order_book = {}
        self.holding_labels_by_order_book = {}
        self.order_search_labels_by_order_book = {}
        self.live_refresh_auth_blocked_sessions.clear()
        self.live_refresh_auth_last_notice_at.clear()
        if self.live_refresh_timer is not None:
            self.live_refresh_timer.stop()
            self.live_refresh_timer = None
        self.clear_workspace_tables()
        self.refresh_session_select_options()
        try:
            self.query_one("#account-select", Select).set_options([])
        except Exception:
            pass
        self.update_selected_account_summary(None)
        self.query_one("#workspace").display = False
        self.query_one("#login-screen").display = True
        self.clear_secret_inputs()
        self.clear_extra_secret_inputs()
        self.update_mode_toggles()
        self.write_mcp_log("[yellow]MCP disabled: no logged-in tenant sessions.[/yellow]")
        self.record_event("app", "tenant_sessions_cleared", {})

    def logout_selected_session(self) -> None:
        session_id = self.selected_session_id()
        if not session_id:
            raise ValueError("No session is selected.")
        context = self.tenant_sessions.get(session_id)
        if context is None:
            raise ValueError(f"Unknown session_id: {session_id}")
        was_active = session_id == self.active_session_id
        self.tenant_sessions.pop(session_id, None)
        self.live_refresh_auth_blocked_sessions.discard(session_id)
        self.live_refresh_auth_last_notice_at.pop(session_id, None)
        if self.mcp_scope_original_session_id == session_id:
            self.mcp_scope_original_session_id = None
        self.record_event(
            "app",
            "tenant_session_logged_out",
            {"session_id": session_id, "label": context.label, "was_active": was_active},
        )
        if not self.tenant_sessions:
            self.write_log(f"[yellow]Logged out session:[/yellow] {context.label}. No active sessions left.")
            self.logout_all_sessions()
            return

        if was_active:
            next_context = next(iter(self.tenant_sessions.values()))
            self.activate_tenant_session(next_context.session_id, refresh_ui=True, announce=False, update_controls=True)
            self.write_log(
                f"[yellow]Logged out session:[/yellow] {context.label}. "
                f"Switched to {next_context.label}."
            )
        else:
            self.refresh_session_select_options()
            self.write_log(f"[yellow]Logged out session:[/yellow] {context.label}.")

    @contextmanager
    def temporary_tenant_scope(self, session_id: str | None) -> Iterator[None]:
        target = str(session_id or "").strip()
        if not target or target == self.active_session_id:
            self.mcp_scope_original_session_id = self.active_session_id
            try:
                yield
            finally:
                self.mcp_scope_original_session_id = None
            return
        context = self.tenant_session_by_id(target)
        current = self.active_session_id
        previous_original = self.mcp_scope_original_session_id
        previous_state = self.capture_active_runtime_state()
        self.sync_active_state_to_tenant()
        self.mcp_scope_original_session_id = current
        self.mcp_scope_depth += 1
        self.load_active_state_from_tenant(context)
        try:
            yield
        finally:
            self.sync_active_state_to_tenant()
            self.restore_active_runtime_state(previous_state)
            self.mcp_scope_original_session_id = previous_original
            self.mcp_scope_depth = max(0, self.mcp_scope_depth - 1)
            if self.mcp_scope_depth == 0 and self.live_refresh_deferred_by_mcp_scope:
                self.live_refresh_deferred_by_mcp_scope = False
                self.safe_call_from_thread(self.refresh_selected_account_live)

    def apply_accounts_overview(self, overview: dict[str, Any], announce: bool = True) -> None:
        self.accounts = account_rows_from_overview(overview)
        account_options = [self.session_account_option(account) for account in self.accounts]
        account_select = self.query_one("#account-select", Select)
        self.account_select_updating = True
        try:
            account_select.set_options(account_options)
            if announce:
                self.write_log(f"Loaded {len(self.accounts)} account(s).")
            if self.accounts:
                selected = next((a for a in self.accounts if str(a.get("id", "")) == self.selected_account_id), None)
                if selected is None:
                    selected = default_account(self.accounts)
                if selected is not None:
                    self.set_selected_account(selected)
                    if account_select.value != self.selected_account_id:
                        account_select.value = self.selected_account_id
        finally:
            self.account_select_updating = False
        self.sync_active_state_to_tenant()
        self.refresh_session_select_options()
        self.apply_active_session_header()

    def apply_portfolio_data(
        self,
        data: dict[str, Any],
        fetch_quotes: bool = True,
        quote_payloads: dict[str, dict[str, Any] | None] | None = None,
        realtime_statuses: dict[str, str] | None = None,
        allow_status_lookup: bool = True,
    ) -> None:
        table = self.query_one("#portfolio-table", DataTable)
        selected_row_key = selected_table_row_key(table)
        table.clear()
        self.latest_portfolio_data = data
        self.update_selected_account_summary(data)

        holding_options = stoploss_holding_options(data, self.selected_account_id)
        holding_select = self.query_one("#instrument-select", Select)
        order_holding_select = self.query_one("#order-instrument-select", Select)
        previous_holding = self.input_value("instrument-select")
        previous_order_holding = self.input_value("order-instrument-select")
        order_search_query = self.input_value("order-search")
        holding_select.set_options(holding_options)
        if not order_search_query:
            order_holding_select.set_options(holding_options)
        if previous_holding and previous_holding in {value for _, value in holding_options}:
            holding_select.value = previous_holding
        elif holding_options:
            holding_select.value = holding_options[0][1]
        if not order_search_query:
            if previous_order_holding and previous_order_holding in {value for _, value in holding_options}:
                order_holding_select.value = previous_order_holding
            elif holding_options:
                order_holding_select.value = holding_options[0][1]
        self.holding_volumes_by_order_book = stoploss_volume_by_order_book(data, self.selected_account_id)
        self.holding_labels_by_order_book = {
            value: label.split(" - owned", 1)[0]
            for label, value in holding_options
        }
        selected_holding = self.input_value("instrument-select")
        volume_input = self.query_one("#volume", Input)
        if selected_holding and not volume_input.value.strip():
            volume_input.value = self.holding_volumes_by_order_book.get(selected_holding, "")

        count = 0
        next_cache: dict[str, tuple[str, ...]] = {}
        self.portfolio_trade_targets_by_row_key = {}
        for section in ("withOrderbook", "withoutOrderbook"):
            for item in data.get(section, []):
                if isinstance(item, dict):
                    if not matches_account(item, self.selected_account_id):
                        continue
                    row_key = str(item.get("id", f"{section}-{count}"))
                    if fetch_quotes:
                        order_book_id = position_order_book_id(item)
                        quote_payload = None
                        if quote_payloads is not None:
                            quote_payload = quote_payloads.get(order_book_id)
                        if quote_payload is None:
                            quote_payload = self.quote_payload_for_order_book(order_book_id)
                        realtime_status = None
                        if realtime_statuses is not None and order_book_id:
                            realtime_status = realtime_statuses.get(order_book_id)
                        if realtime_status is None:
                            realtime_status = self.realtime_status_for_position(
                                item,
                                quote_payload,
                                allow_lookup=allow_status_lookup,
                            )
                        current_row = position_state_row_with_quote(
                            item,
                            quote_payload,
                            realtime_status,
                        )
                    else:
                        current_row = position_state_row(item, "Unknown")
                    previous_row = self.position_row_cache.get(row_key)
                    changed_row = changed_position_row(current_row, previous_row)
                    table.add_row(
                        changed_row[0],
                        trade_action_badge("buy"),
                        trade_action_badge("sell"),
                        *changed_row[1:],
                        key=row_key,
                    )
                    next_cache[row_key] = current_row
                    self.portfolio_trade_targets_by_row_key[row_key] = position_trade_target(item)
                    count += 1

        self.position_row_cache = next_cache
        self.reapply_table_sort(table)
        restore_table_row_selection(table, selected_row_key)
        suffix = f" for account {self.selected_account_id}" if self.selected_account_id else ""
        self.write_log(f"Loaded {count} portfolio row(s){suffix}.")
        self.sync_active_state_to_tenant()

    def apply_stoploss_orders_data(self, stoplosses: Any, orders: Any) -> None:
        table = self.query_one("#stoploss-table", DataTable)
        selected_row_key = selected_table_row_key(table)
        table.clear()
        self.stoploss_items_by_row_key = {}
        self.cancel_targets_by_row_key = {
            key: value
            for key, value in self.cancel_targets_by_row_key.items()
            if not key.startswith(("stoploss-", "order-", "active-"))
        }
        self.latest_stoploss_items = []
        if isinstance(stoplosses, list):
            for item in stoplosses:
                if isinstance(item, dict):
                    if not matches_account(item, self.selected_account_id):
                        continue
                    self.latest_stoploss_items.append(item)
        else:
            self.write_log(f"[yellow]Unexpected stop-loss response type:[/yellow] {type(stoplosses).__name__}")

        order_items = open_order_items(orders)
        order_count = 0
        self.latest_open_order_items = []
        for item in order_items:
            if isinstance(item, dict):
                if not matches_account(item, self.selected_account_id):
                    continue
                self.latest_open_order_items.append(item)
                row_key = f"order-{item.get('id', '') or item.get('orderId', '') or order_count}"
                table.add_row(*open_order_activity_row(item), key=row_key)
                self.cancel_targets_by_row_key[row_key] = self.live_cancel_target("Order", item)
                order_count += 1

        self.reapply_table_sort(table)
        restore_table_row_selection(table, selected_row_key)
        self.update_active_trades_table()
        suffix = f" for account {self.selected_account_id}" if self.selected_account_id else ""
        self.write_log(f"Loaded {len(self.latest_stoploss_items)} active stop-loss order(s) and {order_count} ongoing open order(s){suffix}.")
        self.sync_active_state_to_tenant()

    def apply_cached_account_snapshot(self, account_id: str) -> bool:
        context = self.active_tenant_session()
        if context is None:
            return False
        snapshot = context.account_snapshots.get(str(account_id or "").strip())
        if snapshot is None:
            return False
        applied = False
        if isinstance(snapshot.portfolio_data, dict):
            self.position_row_cache = {}
            self.apply_portfolio_data(snapshot.portfolio_data, fetch_quotes=False, allow_status_lookup=False)
            applied = True
        self.apply_stoploss_orders_data(snapshot.stoploss_items, snapshot.open_order_items)
        return applied

    def complete_login(
        self,
        avanza: Avanza,
        overview: dict[str, Any],
        portfolio: dict[str, Any],
        stoplosses: Any,
        orders: Any,
        target_mode: str = "initial",
        target_session_id: str | None = None,
        session_label: str | None = None,
    ) -> None:
        is_extra = str(target_mode or "").strip().lower() == "extra"
        refresh_token = str(target_session_id or "").strip()
        is_refresh = is_extra and bool(refresh_token) and refresh_token in self.tenant_sessions
        if is_refresh:
            context = self.tenant_sessions[refresh_token]
            accounts = account_rows_from_overview(overview)
            context.avanza = avanza
            if session_label:
                context.label = str(session_label)
            context.accounts = accounts
            selected = str(context.selected_account_id or "").strip()
            if not selected or not any(str(item.get("id", "")) == selected for item in accounts):
                default = default_account(accounts)
                context.selected_account_id = str(default.get("id", "")) if default else None
            context.latest_portfolio_data = portfolio if isinstance(portfolio, dict) else None
            context.latest_stoploss_items = [item for item in stoplosses if isinstance(item, dict)] if isinstance(stoplosses, list) else []
            context.latest_open_order_items = [item for item in open_order_items(orders) if isinstance(item, dict)]
            context.auth_valid = True
            context.auth_error = ""
            self.live_refresh_auth_blocked_sessions.discard(context.session_id)
            self.live_refresh_auth_last_notice_at.pop(context.session_id, None)
        else:
            context = self.register_tenant_session(
                avanza,
                overview,
                portfolio,
                stoplosses,
                orders,
                label=session_label,
            )
        self.update_tenant_session_data_cache(
            context.session_id,
            overview,
            portfolio if isinstance(portfolio, dict) else None,
            stoplosses,
            orders,
        )
        self.load_active_state_from_tenant(context)
        self.position_row_cache = {}
        self.query_one("#login-screen").display = False
        self.query_one("#workspace").display = True
        self.query_one("#extra-login-modal").display = False
        self.clear_secret_inputs()
        self.clear_extra_secret_inputs()
        self.refresh_session_select_options()
        self.apply_active_session_header()
        self.write_log(
            (
                f"[green]Session re-authenticated:[/green] {context.label} ({context.session_id})."
                if is_refresh
                else f"[green]Extra session logged in:[/green] {context.label} ({context.session_id})."
                if is_extra
                else "[green]Logged in. Secret fields cleared.[/green]"
            )
        )
        self.apply_accounts_overview(overview, announce=not is_extra)
        self.apply_portfolio_data(portfolio, fetch_quotes=False)
        self.apply_stoploss_orders_data(stoplosses, orders)
        self.start_live_refresh()
        self.update_session_auth_badge()

    def record_event(self, category: str, event: str, details: dict[str, Any] | None = None) -> None:
        record = {
            "timestamp": timestamp(),
            "category": category,
            "event": event,
            "details": details or {},
        }
        append_jsonl(self.session_log_path, record)
        category_file = LOG_CATEGORY_FILES.get(category)
        if category_file:
            append_jsonl(LOG_DIR / category_file, record)

    def debug_log(self, message: str) -> None:
        if not self.debug_mode or self.debug_session_log_path is None:
            return
        append_jsonl(
            self.debug_session_log_path,
            {
                "timestamp": timestamp(),
                "kind": "debug",
                "message": message,
            },
        )

    def run_profiled(self, label: str, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if not self.debug_mode or self.debug_profile_depth > 0:
            return callback(*args, **kwargs)

        profiler = cProfile.Profile()
        started = time.perf_counter()
        self.debug_profile_depth += 1
        try:
            profiler.enable()
            return callback(*args, **kwargs)
        finally:
            profiler.disable()
            elapsed = time.perf_counter() - started
            self.debug_profile_depth = max(0, self.debug_profile_depth - 1)
            if self.debug_session_log_path is None:
                return

            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            safe_label = re.sub(r"[^a-zA-Z0-9_.-]+", "_", label)
            prof_path = self.debug_session_log_path.with_name(f"profile-{safe_label}-{stamp}.prof")
            profiler.dump_stats(str(prof_path))
            self.debug_log(f"{label}: {elapsed:.3f}s -> {prof_path.name}")

            stream = io.StringIO()
            stats = pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats("cumtime")
            stats.print_stats(self.debug_profile_top)
            for line in stream.getvalue().splitlines():
                line = line.rstrip()
                if line:
                    self.debug_log(f"{label}: {line}")

    def write_log(self, message: str) -> None:
        stamped = f"{timestamp()} {message}"
        self.record_event("app", "console", {"message": strip_markup(message)})
        try:
            self.query_one("#log", RichLog).write(stamped)
        except Exception:
            # During shutdown the DOM may already be unmounted.
            pass

    def write_mcp_log(self, message: str) -> None:
        stamped = f"{timestamp()} {message}"
        self.record_event("mcp", "console", {"message": strip_markup(message)})
        try:
            self.query_one("#mcp-log", RichLog).write(stamped)
        except Exception:
            try:
                self.query_one("#log", RichLog).write(stamped)
            except Exception:
                # During shutdown the DOM may already be unmounted.
                pass

    def ensure_mcp_bridge_health(self) -> None:
        if self.mcp_server is None:
            return

        # If the bridge thread died, restart transparently.
        if self.mcp_thread is None or not self.mcp_thread.is_alive():
            self.record_event("mcp", "bridge_thread_dead", {"action": "restart"})
            self.write_mcp_log("[yellow]MCP bridge thread stopped; restarting.[/yellow]")
            self.stop_mcp_bridge(announce=False)
            try:
                self.start_mcp_bridge()
                self.update_mode_toggles()
            except Exception as exc:
                self.record_event("mcp", "bridge_restart_failed", {"error": str(exc)})
                self.write_mcp_log(f"[red]MCP bridge restart failed:[/red] {exc}")
            return

        # If the session file was removed or became stale, restore it.
        try:
            host, port = self.mcp_server.server_address
            expected_url = f"http://{host}:{port}"
            session = load_mcp_session(MCP_SESSION_FILE)
            token = str(session.get("token", ""))
            read_write = bool(session.get("read_write", False))
            if session.get("url") != expected_url or token != (self.mcp_token or "") or read_write != self.mcp_write_enabled:
                self.update_mcp_session_file()
        except Exception:
            self.update_mcp_session_file()

    def require_connection(self) -> Avanza:
        if self.avanza is None:
            raise RuntimeError("Log in first.")
        return self.avanza

    def require_selected_account_id(self) -> str:
        if not self.selected_account_id:
            raise RuntimeError("Select an account first.")
        return self.selected_account_id

    def stock_name_for_order_book(self, order_book_id: str) -> str:
        token = str(order_book_id or "").strip()
        if not token:
            return ""
        cached = str(self.holding_labels_by_order_book.get(token, "")).strip()
        if cached:
            return cached
        cached = str(self.order_search_labels_by_order_book.get(token, "")).strip()
        if cached:
            return cached
        data = self.latest_portfolio_data
        if isinstance(data, dict):
            for section in ("withOrderbook", "withoutOrderbook"):
                for item in data.get(section, []):
                    if not isinstance(item, dict):
                        continue
                    if position_order_book_id(item) == token:
                        return str(nested_value(item, "instrument", "name") or "").strip()
        return ""

    def mcp_stock_marker_for_call(self, arguments: dict[str, Any]) -> str:
        marker = mcp_stock_marker(arguments)
        if marker and not marker.startswith("OB "):
            return marker

        order_book_id = str(arguments.get("order_book_id", "")).strip()
        if order_book_id:
            return self.stock_name_for_order_book(order_book_id) or f"OB {order_book_id}"

        stop_loss_id = str(arguments.get("stop_loss_id", "")).strip()
        if stop_loss_id:
            for item in self.latest_stoploss_items:
                if str(item.get("id", "")).strip() == stop_loss_id:
                    return order_stock_name(item) or marker

        order_id = str(arguments.get("order_id", "")).strip()
        if order_id:
            for item in self.latest_open_order_items:
                current = str(item.get("id", "") or item.get("orderId", "")).strip()
                if current == order_id:
                    return order_stock_name(item) or marker
        return marker

    def update_selected_account_summary(self, portfolio_data: dict[str, Any] | None = None) -> None:
        portfolio_data = portfolio_data or self.latest_portfolio_data
        account = self.account_by_id(self.selected_account_id) if self.selected_account_id else None
        metrics = account_metric_values(account, portfolio_data, self.selected_account_id, self.profit_metric_mode)
        self.query_one("#metric-total", Static).update(metrics["total"])
        self.query_one("#metric-buying", Static).update(metrics["buying"])
        self.query_one("#profit-cycle", Button).label = metrics["profit_label"].plain
        self.query_one("#metric-profit-value", Static).update(metrics["profit"])
        self.query_one("#metric-status", Static).update(metrics["status"])

    def update_clock_status(self) -> None:
        self.query_one("#clock-status", Static).update(market_clock_text())

    def start_clock(self) -> None:
        if self.clock_timer is None:
            self.clock_timer = self.set_interval(1.0, self.update_clock_status, pause=False)

    def render_update_status(self) -> None:
        text = self.update_status_text
        style = "dim"
        if self.update_check_inflight:
            style = "cyan"
        elif self.update_status_error:
            style = "red"
        elif self.update_status_outdated:
            style = "bold yellow" if self.update_status_blink_on else "yellow"
        elif self.update_status_latest:
            style = "green"
        try:
            self.query_one("#update-status", Static).update(Text(text, style=style))
        except Exception:
            pass

    def start_update_checker(self) -> None:
        if not update_check_enabled():
            self.update_status_text = "Update: disabled"
            self.render_update_status()
            return
        self.render_update_status()
        if self.update_check_timer is None:
            self.update_check_timer = self.set_interval(
                UPDATE_CHECK_INTERVAL_SECONDS,
                self.schedule_update_check,
                pause=False,
            )
        if self.update_blink_timer is None:
            self.update_blink_timer = self.set_interval(
                UPDATE_BLINK_INTERVAL_SECONDS,
                self.toggle_update_blink,
                pause=False,
            )
        self.schedule_update_check()

    def toggle_update_blink(self) -> None:
        if not self.update_status_outdated:
            return
        self.update_status_blink_on = not self.update_status_blink_on
        self.render_update_status()

    def schedule_update_check(self) -> None:
        with self.update_check_lock:
            if self.update_check_inflight:
                return
            self.update_check_inflight = True
        self.update_status_text = "Update: checking..."
        self.render_update_status()
        self.update_check_thread = threading.Thread(
            target=self._update_check_worker,
            daemon=True,
            name="avanza-update-check",
        )
        self.update_check_thread.start()

    def _update_check_worker(self) -> None:
        info: dict[str, str] | None = None
        error = ""
        try:
            info = update_check.github_latest_version_info(self.update_status_repo)
        except Exception as exc:
            error = str(exc)
        if not self.safe_call_from_thread(self.apply_update_check_result, info, error):
            with self.update_check_lock:
                self.update_check_inflight = False

    def apply_update_check_result(self, info: dict[str, str] | None, error: str) -> None:
        self.update_status_error = error
        self.update_status_blink_on = True
        if error:
            self.update_status_latest = ""
            self.update_status_outdated = False
            self.update_status_text = "Update: check failed"
            self.render_update_status()
            with self.update_check_lock:
                self.update_check_inflight = False
            return

        latest = str((info or {}).get("version", "") or "")
        self.update_status_latest = latest
        self.update_status_outdated = is_version_outdated(APP_VERSION, latest)
        if self.update_status_outdated and latest:
            self.update_status_text = f"Update: v{latest} available"
        else:
            self.update_status_text = f"Update: latest ({APP_VERSION})"
        self.render_update_status()
        with self.update_check_lock:
            self.update_check_inflight = False

    def mcp_status_payload(self) -> dict[str, Any]:
        account = self.account_by_id(self.selected_account_id or "") if self.selected_account_id else None
        available_tools = sorted(
            tool.get("name", "")
            for tool in mcp_tools_catalog()
            if isinstance(tool, dict) and tool.get("name")
        )
        sessions = [
            {
                "session_id": context.session_id,
                "label": context.label,
                "active": context.session_id == self.active_session_id,
                "accounts_loaded": len(context.accounts),
                "selected_account_id": context.selected_account_id,
                "selected_account_name": (
                    account_display_name(next((a for a in context.accounts if str(a.get("id", "")) == str(context.selected_account_id or "")), {}))
                    if context.selected_account_id
                    else None
                ),
                "auth_valid": bool(context.auth_valid),
                "auth_error": str(context.auth_error or ""),
            }
            for context in self.tenant_sessions.values()
        ]
        return {
            "ok": True,
            "app_version": APP_VERSION,
            "enabled": self.mcp_server is not None,
            "mcp_enabled": self.mcp_server is not None,
            "read_write": self.mcp_write_enabled,
            "read_write_enabled": self.mcp_write_enabled,
            "paper_trading": True,
            "paper_trading_enabled": self.paper_mode_enabled,
            "live_trading_allowed_for_this_session": self.live_trading_allowed_for_session,
            "selected_account_id": self.selected_account_id,
            "selected_account_name": account_display_name(account) if isinstance(account, dict) else None,
            "account_type": str(account.get("type", "") or "") if isinstance(account, dict) else None,
            "accounts_loaded": len(self.accounts),
            "poll_interval_seconds": LIVE_REFRESH_SECONDS,
            "available_tools": available_tools,
            "can_read_quotes": True,
            "can_place_paper_orders": True,
            "can_place_live_orders": bool(self.mcp_write_enabled and self.live_trading_allowed_for_session),
            "can_cancel_live_orders": bool(self.mcp_write_enabled and self.live_trading_allowed_for_session),
            "active_session_id": self.active_session_id,
            "sessions_loaded": len(self.tenant_sessions),
            "sessions": sessions,
            "warning": (
                "Live mutation tools are enabled for this session. Use with extreme caution."
                if (self.mcp_write_enabled and self.live_trading_allowed_for_session)
                else "MCP R/W is enabled but live mutation is still blocked until explicitly authorized."
                if self.mcp_write_enabled
                else ""
            ),
            "paper_session_file": str(self.paper_session_path),
            "update_available": self.update_status_outdated,
            "latest_version": self.update_status_latest or APP_VERSION,
        }

    def tenant_sessions_payload(self) -> dict[str, Any]:
        sessions: list[dict[str, Any]] = []
        for context in self.tenant_sessions.values():
            active_account = next(
                (item for item in context.accounts if str(item.get("id", "")) == str(context.selected_account_id or "")),
                None,
            )
            sessions.append(
                {
                    "session_id": context.session_id,
                    "label": context.label,
                    "color": context.color,
                    "active": context.session_id == self.active_session_id,
                    "accounts_loaded": len(context.accounts),
                    "selected_account_id": context.selected_account_id,
                    "selected_account_name": account_display_name(active_account or {}) if active_account else None,
                    "auth_valid": bool(context.auth_valid),
                    "auth_error": str(context.auth_error or ""),
                }
            )
        return {
            "active_session_id": self.active_session_id,
            "sessions_loaded": len(self.tenant_sessions),
            "sessions": sessions,
        }

    def resolve_session_id_for_mcp(self, tool: str, arguments: dict[str, Any]) -> str | None:
        requested_tenant_session_id = str(arguments.get("tenant_session_id", "") or "").strip() or None
        if requested_tenant_session_id:
            _ = self.tenant_session_by_id(requested_tenant_session_id)
            return requested_tenant_session_id

        # Paper-ledger tools reserve session_id for paper strategy grouping.
        requested_session_id = None
        if tool not in PAPER_SESSION_ID_TOOLS:
            requested_session_id = str(arguments.get("session_id", "") or "").strip() or None
        requested_account_id = str(arguments.get("account_id", "") or "").strip() or None
        if requested_session_id:
            _ = self.tenant_session_by_id(requested_session_id)
            return requested_session_id
        if requested_account_id:
            match = self.tenant_session_for_account(requested_account_id)
            if match is not None:
                return match.session_id
        return self.active_session_id

    def save_paper_state(self) -> None:
        save_paper_session(self.paper_session, self.paper_session_path)
        self.update_active_trades_table()

    def set_mode_toggle(self, button_id: str, label_id: str, enabled: bool, text: str) -> None:
        button = self.query_one(f"#{button_id}", Button)
        button.label = "✓" if enabled else "×"
        button.remove_class("enabled")
        button.remove_class("disabled")
        button.add_class("enabled" if enabled else "disabled")
        self.query_one(f"#{label_id}", Static).update(text)

    def update_mode_toggles(self) -> None:
        self.set_mode_toggle("paper-mode-toggle", "paper-mode-label", self.paper_mode_enabled, "Paper")
        self.set_mode_toggle("mcp-toggle", "mcp-label", self.mcp_server is not None, "MCP")
        self.set_mode_toggle("mcp-write-toggle", "mcp-write-label", self.mcp_write_enabled, "Live R/W")

    def update_paper_mode_ui(self) -> None:
        stoploss_button = self.query_one("#place-live", Button)
        editing = bool(self.pending_stoploss_edit_id)
        if self.paper_mode_enabled:
            stoploss_button.label = "Update Paper Stop-Loss" if editing else "Create Paper Stop-Loss"
            stoploss_button.variant = "warning"
        else:
            stoploss_button.label = "Update Live Stop-Loss" if editing else "Submit Live Stop-Loss"
            stoploss_button.variant = "error"

        order_button = self.query_one("#order-place-live", Button)
        if self.paper_mode_enabled:
            order_button.label = "Create Paper Order"
            order_button.variant = "warning"
        else:
            order_button.label = "Submit Live Order"
            order_button.variant = "error"
        self.update_mode_toggles()

    def cycle_profit_metric(self) -> None:
        current_index = PROFIT_METRIC_MODES.index(self.profit_metric_mode)
        self.profit_metric_mode = PROFIT_METRIC_MODES[(current_index + 1) % len(PROFIT_METRIC_MODES)]
        self.update_selected_account_summary()
        self.write_log(f"Account P/L metric: {profit_metric_label(self.profit_metric_mode)}.")

    def live_cancel_target(self, kind: str, item: dict[str, Any]) -> dict[str, str]:
        identifier = str(item.get("id", "") or item.get("orderId", ""))
        return {
            "mode": "Live",
            "kind": kind,
            "id": identifier,
            "account_id": order_account_id(item, self.selected_account_id),
            "stock": order_stock_name(item),
        }

    def paper_cancel_target(self, item: dict[str, Any]) -> dict[str, str]:
        request = item.get("request") if isinstance(item.get("request"), dict) else {}
        return {
            "mode": "Paper",
            "kind": str(item.get("kind", "Order")),
            "id": str(item.get("id", "")),
            "account_id": str(request.get("account_id") or self.selected_account_id or ""),
            "stock": str(item.get("instrument") or request.get("order_book_id") or ""),
        }

    def active_trade_rows(self) -> list[tuple[Any, ...]]:
        rows: list[tuple[Any, ...]] = []
        rows.extend(active_stop_loss_row(item) for item in self.latest_stoploss_items)
        rows.extend(
            active_paper_order_row(item)
            for item in paper_orders(self.paper_session, self.selected_account_id, active_only=True)
            if str(item.get("kind", "")) == "Stop-loss"
        )
        return rows

    def active_trade_entries(self) -> list[tuple[tuple[Any, ...], dict[str, str]]]:
        entries: list[tuple[tuple[Any, ...], dict[str, str]]] = []
        entries.extend((active_stop_loss_row(item), self.live_cancel_target("Stop-loss", item)) for item in self.latest_stoploss_items)
        entries.extend(
            (active_paper_order_row(item), self.paper_cancel_target(item))
            for item in paper_orders(self.paper_session, self.selected_account_id, active_only=True)
            if str(item.get("kind", "")) == "Stop-loss"
        )
        return entries

    def update_active_trades_table(self) -> None:
        try:
            table = self.query_one("#active-trades-table", DataTable)
        except Exception:
            return
        selected_row_key = selected_table_row_key(table)
        table.clear()
        self.cancel_targets_by_row_key = {
            key: value
            for key, value in self.cancel_targets_by_row_key.items()
            if not key.startswith("active-")
        }
        for index, (row, target) in enumerate(self.active_trade_entries()):
            row_key = f"active-{index}-{row[0]}-{row[1]}-{row[2]}"
            table.add_row(*row, key=row_key)
            self.cancel_targets_by_row_key[row_key] = target
        restore_table_row_selection(table, selected_row_key)

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
        return {
            "account_id": account_id or None,
            "executed_only": executed_only,
            "types": [item.value for item in transaction_types],
            "transactions_from": transactions_from.isoformat() if transactions_from else None,
            "transactions_to": transactions_to.isoformat() if transactions_to else None,
            "first_available_date": first_date,
            "transactions": rows,
        }

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
    ) -> dict[str, Any]:
        account_id = account_id or self.require_selected_account_id()
        excludes = {str(item) for item in (exclude_orderbook_ids or [])}
        _portfolio_data, positions = self.filtered_portfolio_items(avanza, account_id)
        stoploss_items = self.filtered_stoploss_items(avanza, account_id)
        stoplosses_by_orderbook: dict[str, list[dict[str, Any]]] = {}
        for item in stoploss_items:
            stoplosses_by_orderbook.setdefault(stop_loss_order_book_id(item), []).append(item)
        rows: list[dict[str, Any]] = []
        for position in positions:
            orderbook_id = position_order_book_id(position)
            stock = str(nested_value(position, "instrument", "name") or "")
            if orderbook_id in excludes:
                continue
            if exclude_eth and instrument_is_eth_like(stock, orderbook_id):
                continue
            summary = summarize_stop_protection(position, stoplosses_by_orderbook.get(orderbook_id, []))
            if summary["sell_protection_gap"] <= 0 and summary["failed_stop_volume"] <= 0:
                continue
            rows.append(
                {
                    "stock": stock,
                    "orderbook_id": orderbook_id,
                    **summary,
                }
            )
        return {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "account_id": account_id,
            "count": len(rows),
            "gaps": rows,
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
            failed_orders = [
                row
                for row in generated_orders
                if str(row.get("Status", "")).upper() in {"ERROR", "FAILED", "REJECTED", "FAULTY", "FELAKTIG"}
            ]
            current_holding = position_volume(positions_by_orderbook.get(orderbook_id, {}))
            sold_volume = float(sold.get("sold_volume") or 0.0)
            active_buyback_volume = tight_volume + deep_volume + unclassified_volume
            rows.append(
                {
                    **sold,
                    "current_holding": current_holding,
                    "active_tight_buyback_volume": tight_volume,
                    "active_deep_buyback_volume": deep_volume,
                    "active_unclassified_buyback_volume": unclassified_volume,
                    "generated_open_orders": generated_orders,
                    "failed_raw_orders": failed_orders,
                    "missing_buyback_volume": max(sold_volume - active_buyback_volume, 0.0),
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
    ) -> dict[str, Any]:
        account_id = account_id or self.require_selected_account_id()
        ids = {str(item) for item in (orderbook_ids or []) if str(item)}
        gaps = self.protection_gaps_snapshot(
            avanza,
            account_id,
            exclude_orderbook_ids=[],
            exclude_eth=exclude_eth,
        )["gaps"]
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
            "gaps": gaps,
        }

    def stoploss_readback_match(
        self,
        avanza: Any,
        preview: dict[str, Any],
        result: Any | None = None,
    ) -> dict[str, Any] | None:
        account_id = str(preview.get("account_id") or "")
        orderbook_id = str(preview.get("order_book_id") or "")
        order_event = preview.get("stop_loss_order_event") if isinstance(preview.get("stop_loss_order_event"), dict) else {}
        side = str(order_event.get("type") or "")
        result_id = first_nested_text_for_keys(
            result,
            {"stoplossOrderId", "stopLossOrderId", "stop_loss_id", "stopLossId", "id"},
        )
        try:
            items = self.filtered_stoploss_items(
                avanza,
                account_id,
                orderbook_id=orderbook_id,
                side=side,
                refresh=True,
            )
        except Exception:
            return None
        if result_id:
            for item in items:
                if str(item.get("id") or "") == result_id:
                    return stop_loss_mcp_dict(item)
        requested_volume = utils.scalar_number(order_event.get("volume"))
        requested_price = utils.scalar_number(order_event.get("price"))
        trigger = preview.get("stop_loss_trigger") if isinstance(preview.get("stop_loss_trigger"), dict) else {}
        requested_trigger = utils.scalar_number(trigger.get("value"))
        best: dict[str, Any] | None = None
        best_score = -1
        for item in items:
            score = 0
            if str(item.get("status", "")).upper() == "ACTIVE":
                score += 1
            if requested_volume is not None and utils.scalar_number(nested_value(item, "order", "volume")) == requested_volume:
                score += 2
            if requested_price is not None and utils.scalar_number(nested_value(item, "order", "price")) == requested_price:
                score += 2
            if requested_trigger is not None and utils.scalar_number(nested_value(item, "trigger", "value")) == requested_trigger:
                score += 2
            if score > best_score:
                best = item
                best_score = score
        return stop_loss_mcp_dict(best) if best else None

    def stoploss_mutation_response(
        self,
        *,
        dry_run: bool,
        action: str,
        preview: dict[str, Any],
        result: Any | None = None,
        warnings: list[str] | None = None,
        deleted_stop_loss_id: str = "",
        deprecated_alias: bool = False,
    ) -> dict[str, Any]:
        row = None if dry_run else self.stoploss_readback_match(self.require_connection(), preview, result)
        order_event = preview.get("stop_loss_order_event") if isinstance(preview.get("stop_loss_order_event"), dict) else {}
        trigger = preview.get("stop_loss_trigger") if isinstance(preview.get("stop_loss_trigger"), dict) else {}
        payload: dict[str, Any] = {
            "dry_run": dry_run,
            "action": action,
            "request": preview,
            "stop_loss_id": (row or {}).get("stop_loss_id") or first_nested_text_for_keys(result, {"stoplossOrderId", "stopLossOrderId", "stop_loss_id", "stopLossId", "id"}) or deleted_stop_loss_id or None,
            "status": (row or {}).get("status"),
            "account_id": preview.get("account_id"),
            "orderbook_id": preview.get("order_book_id"),
            "stock": (row or {}).get("stock"),
            "side": normalize_order_side(order_event.get("type")),
            "volume": utils.scalar_number(order_event.get("volume")),
            "trigger_type": trigger.get("type"),
            "trigger_value": utils.scalar_number(trigger.get("value")),
            "trigger_value_type": trigger.get("value_type"),
            "order_price": utils.scalar_number(order_event.get("price")),
            "order_price_type": order_event.get("price_type"),
            "valid_until": trigger.get("valid_until"),
            "order_valid_days": order_event.get("valid_days"),
            "readback": row,
        }
        if warnings:
            payload["warnings"] = warnings
        if result is not None:
            payload["result"] = result
        if deleted_stop_loss_id:
            payload["deleted_stop_loss_id"] = deleted_stop_loss_id
        if deprecated_alias:
            payload["warning"] = "avanza_stoploss_replace is deprecated; use avanza_stoploss_edit."
        return payload

    def update_mcp_session_file(self) -> None:
        if self.mcp_server is None or self.mcp_token is None:
            return
        host, port = self.mcp_server.server_address
        write_mcp_session_file(
            MCP_SESSION_FILE,
            mcp_session_payload(str(host), int(port), self.mcp_token, self.mcp_write_enabled),
        )

    def start_mcp_bridge(self) -> None:
        self.require_connection()
        if self.mcp_server is not None:
            return
        self.mcp_token = secrets.token_urlsafe(24)
        server = AvanzaMcpHttpServer(("127.0.0.1", 0), AvanzaMcpRequestHandler, self, self.mcp_token)
        self.mcp_server = server
        self.mcp_thread = threading.Thread(target=server.serve_forever, name="avanza-mcp-bridge", daemon=True)
        self.mcp_thread.start()
        self.update_mcp_session_file()
        host, port = server.server_address
        self.write_mcp_log(f"[green]MCP enabled[/green] at http://{host}:{port}.")
        self.write_mcp_log(f"Proxy command: python {Path(__file__).name} mcp")

    def stop_mcp_bridge(self, announce: bool = True, wait: bool = True) -> None:
        if self.mcp_server is None:
            remove_mcp_session_file()
            return
        server = self.mcp_server
        thread = self.mcp_thread
        self.mcp_server = None
        self.mcp_thread = None
        self.mcp_token = None
        remove_mcp_session_file()

        def shutdown_server() -> None:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
            if thread is not None and thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=0.5 if wait else 0.05)

        if wait:
            shutdown_server()
        else:
            threading.Thread(target=shutdown_server, daemon=True, name="avanza-mcp-bridge-shutdown").start()

        if announce:
            self.write_mcp_log("[yellow]MCP disabled.[/yellow]")

    def on_unmount(self) -> None:
        self.shutdown_event.set()
        self.stop_login_progress()
        if self.order_search_timer is not None:
            self.order_search_timer.stop()
            self.order_search_timer = None
        if self.live_refresh_timer is not None:
            self.live_refresh_timer.stop()
            self.live_refresh_timer = None
        if self.background_session_heartbeat_timer is not None:
            self.background_session_heartbeat_timer.stop()
            self.background_session_heartbeat_timer = None
        if self.clock_timer is not None:
            self.clock_timer.stop()
            self.clock_timer = None
        if self.mcp_health_timer is not None:
            self.mcp_health_timer.stop()
            self.mcp_health_timer = None
        if self.update_check_timer is not None:
            self.update_check_timer.stop()
            self.update_check_timer = None
        if self.update_blink_timer is not None:
            self.update_blink_timer.stop()
            self.update_blink_timer = None
        if self.tv_lists_refresh_timer is not None:
            self.tv_lists_refresh_timer.stop()
            self.tv_lists_refresh_timer = None
        for worker in (
            self.live_refresh_thread,
            self.background_session_heartbeat_thread,
            self.tv_lists_refresh_thread,
            self.update_check_thread,
        ):
            if worker is not None and worker.is_alive() and worker is not threading.current_thread():
                worker.join(timeout=0.5)
        self.live_refresh_thread = None
        self.background_session_heartbeat_thread = None
        self.tv_lists_refresh_thread = None
        self.update_check_thread = None
        self.stop_mcp_bridge(announce=False, wait=False)

    def require_mcp_write(self, confirmed: bool) -> None:
        if not confirmed:
            return
        if not self.mcp_write_enabled:
            raise PermissionError("TUI MCP mode is read-only. Enable R/W in the TUI for live mutations.")
        if not self.live_trading_allowed_for_session:
            raise PermissionError(
                "Live trading is blocked for this MCP session. Explicitly authorize live mode first."
            )

    def handle_mcp_tool_call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        marker = self.mcp_stock_marker_for_call(arguments)
        self.write_mcp_log(mcp_call_log_line(tool, arguments, marker_override=marker))
        self.record_event("mcp", "tool_call", {"tool": tool, "arguments": arguments})
        try:
            result = self.execute_mcp_tool(tool, arguments)
            self.write_mcp_log(f"[green]✓[/green] {tool}{mcp_result_log_suffix(result)}{mcp_result_log_detail(result)}")
            self.record_event(
                "mcp",
                "tool_result",
                {"tool": tool, "ok": True, "summary": summarize_mcp_result(result)},
            )
            return {
                "ok": True,
                "tool": tool,
                "read_write": self.mcp_write_enabled,
                "result": result,
            }
        except Exception as exc:
            self.write_mcp_log(f"[red]✗ {tool}:[/red] {exc}")
            self.record_event("mcp", "tool_error", {"tool": tool, "error": str(exc)})
            return {
                "ok": False,
                "tool": tool,
                "read_write": self.mcp_write_enabled,
                "error": str(exc),
            }

    def execute_mcp_tool(self, tool: str, arguments: dict[str, Any]) -> Any:
        if tool == "avanza_sessions":
            return self.tenant_sessions_payload()

        if tool == "avanza_select_session":
            requested_session_id = str(arguments["session_id"]).strip()
            if not requested_session_id:
                raise ValueError("session_id is required.")
            self.activate_tenant_session(requested_session_id)
            return {
                "ok": True,
                "active_session_id": self.active_session_id,
                "sessions": self.tenant_sessions_payload()["sessions"],
                "capabilities": self.mcp_status_payload(),
            }

        session_scope_id = (
            self.resolve_session_id_for_mcp(tool, arguments)
            if tool in TENANT_SESSION_SCOPED_TOOLS
            else None
        )
        with self.temporary_tenant_scope(session_scope_id):
            if tool in {"avanza_status", "avanza_capabilities"}:
                return self.mcp_status_payload()
            try:
                return self._execute_mcp_tool_inner(tool, arguments)
            except Exception as exc:
                if session_scope_id and is_unauthorized_http_error(exc):
                    self.mark_tenant_session_auth_expired(session_scope_id, exc)
                raise

    def _execute_mcp_tool_inner(self, tool: str, arguments: dict[str, Any]) -> Any:
        avanza = self.require_connection()
        account_id = str(arguments.get("account_id") or self.selected_account_id or "")

        if tool == "avanza_live_session_authorize":
            acknowledge = bool(arguments.get("acknowledge", False))
            reason = str(arguments.get("reason", "") or "").strip() or None
            if not acknowledge:
                raise PermissionError("Set acknowledge=true to explicitly authorize live trading for this active session.")
            if not self.mcp_write_enabled:
                raise PermissionError("Enable MCP R/W mode in the TUI before authorizing live trading.")
            self.live_trading_allowed_for_session = True
            self.write_mcp_log("[yellow]Live mutation mode authorized for this session.[/yellow]")
            self.record_event("mcp", "live_session_authorized", {"reason": reason})
            return {
                "ok": True,
                "live_trading_allowed_for_this_session": True,
                "read_write_enabled": self.mcp_write_enabled,
                "warning": "Live mutation tools are now enabled for this active session.",
                "reason": reason,
            }

        if tool == "avanza_live_session_revoke":
            self.live_trading_allowed_for_session = False
            self.write_mcp_log("[green]Live mutation mode revoked. MCP is paper-safe again.[/green]")
            self.record_event("mcp", "live_session_revoked", {})
            return {
                "ok": True,
                "live_trading_allowed_for_this_session": False,
                "read_write_enabled": self.mcp_write_enabled,
            }

        if tool == "avanza_accounts":
            overview = avanza.get_overview()
            accounts = account_rows_from_overview(overview) if isinstance(overview, dict) else []
            return rows_as_dicts(["ID", "Name", "Type", "Total Value", "Buying Power", "Status"], [account_row(account) for account in accounts])

        if tool == "avanza_select_account":
            requested_account_id = str(arguments["account_id"]).strip()
            if not requested_account_id:
                raise ValueError("account_id is required.")
            if not self.accounts:
                overview = avanza.get_overview()
                if isinstance(overview, dict):
                    self.accounts = account_rows_from_overview(overview)
            account = self.account_by_id(requested_account_id)
            if not account:
                raise ValueError(f"Unknown account id: {requested_account_id}")
            restore_to = self.mcp_scope_original_session_id
            switching_foreign_session = bool(restore_to and restore_to != self.active_session_id)
            if switching_foreign_session:
                self.selected_account_id = requested_account_id
                self.sync_active_state_to_tenant()
            else:
                self.selected_account_id = requested_account_id
                try:
                    self.select_account(requested_account_id)
                except Exception:
                    # Keep MCP account context updated even if UI is not mounted.
                    self.selected_account_id = requested_account_id
                    self.sync_active_state_to_tenant()
            return {
                "ok": True,
                "session_id": self.active_session_id,
                "selected_account_id": requested_account_id,
                "selected_account_name": account_display_name(account),
                "account_type": str(account.get("type", "") or ""),
                "status": str(account.get("status", "") or ""),
                "capabilities": self.mcp_status_payload(),
            }

        if tool == "avanza_account_performance":
            requested_period = arguments.get("period", "SINCE_START")
            requested_account_id = str(arguments.get("account_id") or self.selected_account_id or "")
            return self.account_performance_snapshot(avanza, requested_account_id, requested_period)

        if tool == "tv_scrape_symbol_analytics":
            symbol = str(arguments["symbol"])
            exchange = str(arguments.get("exchange", TRADINGVIEW_DEFAULT_EXCHANGE))
            market = str(arguments.get("market", TRADINGVIEW_DEFAULT_MARKET))
            snapshot = tv_data.tradingview_symbol_snapshot(symbol, exchange=exchange, market=market, cookie="")
            snapshot["mode"] = "free_scrape"
            snapshot["experimental_scrape_mode"] = True
            return snapshot

        if tool == "tv_scrape_symbol_full":
            symbol = str(arguments["symbol"])
            exchange = str(arguments.get("exchange", TRADINGVIEW_DEFAULT_EXCHANGE))
            market = str(arguments.get("market", TRADINGVIEW_DEFAULT_MARKET))
            snapshot = tv_data.tradingview_symbol_full_snapshot(symbol, exchange=exchange, market=market, cookie="")
            snapshot["mode"] = "free_scrape"
            snapshot["experimental_scrape_mode"] = True
            return snapshot

        if tool == "tv_auth_session_start":
            open_browser = bool(arguments.get("open_browser", True))
            opened = False
            if open_browser:
                try:
                    opened = bool(webbrowser.open(TRADINGVIEW_LOGIN_URL, new=2, autoraise=True))
                except Exception:
                    opened = False
            return {
                "login_url": TRADINGVIEW_LOGIN_URL,
                "browser_opened": opened,
                "next_step": "After logging in via browser, call tv_auth_session_set with cookie or sessionid/sessionid_sign.",
                "session_file": str(config.TRADINGVIEW_SESSION_FILE),
                "status": tradingview_session_status(),
            }

        if tool == "tv_auth_session_set":
            source = str(arguments.get("source", "manual") or "manual")
            cookie = tradingview_cookie_from_inputs(arguments)
            if not cookie:
                raise ValueError("Provide cookie or sessionid/sessionid_sign to save TradingView session.")
            saved = save_tradingview_session(cookie, source=source)
            return {
                "saved": True,
                "status": tradingview_session_status(),
                "details": saved,
            }

        if tool == "tv_auth_session_login_auto":
            timeout_seconds = int(arguments.get("timeout_seconds", 300))
            return utils.run_blocking_in_thread(
                tradingview_auto_login_and_capture_session,
                timeout_seconds=timeout_seconds,
            )

        if tool == "tv_auth_session_status":
            return tradingview_session_status()

        if tool == "tv_auth_session_clear":
            deleted = clear_tradingview_session()
            return {
                "cleared": deleted,
                "status": tradingview_session_status(),
            }

        if tool == "tv_auth_symbol_analytics":
            symbol = str(arguments["symbol"])
            exchange = str(arguments.get("exchange", TRADINGVIEW_DEFAULT_EXCHANGE))
            market = str(arguments.get("market", TRADINGVIEW_DEFAULT_MARKET))
            cookie = tradingview_cookie_from_inputs(arguments, load_tradingview_session())
            if not cookie:
                raise ValueError("Authenticated mode requires cookie/sessionid input, saved session, or TRADINGVIEW_SESSIONID env.")
            snapshot = tv_data.tradingview_symbol_snapshot(symbol, exchange=exchange, market=market, cookie=cookie)
            snapshot["mode"] = "authenticated_scrape"
            snapshot["experimental_scrape_mode"] = True
            snapshot["unsafe_for_execution"] = False
            return snapshot

        if tool == "tv_auth_symbol_full":
            symbol = str(arguments["symbol"])
            exchange = str(arguments.get("exchange", TRADINGVIEW_DEFAULT_EXCHANGE))
            market = str(arguments.get("market", TRADINGVIEW_DEFAULT_MARKET))
            cookie = tradingview_cookie_from_inputs(arguments, load_tradingview_session())
            if not cookie:
                raise ValueError("Authenticated mode requires cookie/sessionid input, saved session, or TRADINGVIEW_SESSIONID env.")
            snapshot = tv_data.tradingview_symbol_full_snapshot(symbol, exchange=exchange, market=market, cookie=cookie)
            snapshot["mode"] = "authenticated_scrape"
            snapshot["experimental_scrape_mode"] = True
            snapshot["unsafe_for_execution"] = False
            return snapshot

        if tool == "tv_preopen_symbol_snapshot":
            symbol = str(arguments["symbol"])
            exchange = str(arguments.get("exchange", TRADINGVIEW_DEFAULT_EXCHANGE))
            market = str(arguments.get("market", TRADINGVIEW_DEFAULT_MARKET))
            authenticated = bool(arguments.get("authenticated", True))
            cookie = tradingview_cookie_from_inputs(arguments, load_tradingview_session()) if authenticated else ""
            if authenticated and not cookie:
                raise ValueError("Authenticated pre-open mode requires saved TradingView session or cookie/sessionid input.")
            snapshot = tv_data.tradingview_preopen_symbol_snapshot(
                symbol,
                exchange=exchange,
                market=market,
                authenticated=authenticated,
                cookie=cookie,
            )
            snapshot["experimental_scrape_mode"] = True
            return snapshot

        if tool == "tv_preopen_batch_snapshot":
            raw_symbols = arguments.get("symbols") or []
            if not isinstance(raw_symbols, list):
                raise ValueError("symbols must be a list.")
            exchange = str(arguments.get("exchange", TRADINGVIEW_DEFAULT_EXCHANGE))
            market = str(arguments.get("market", TRADINGVIEW_DEFAULT_MARKET))
            authenticated = bool(arguments.get("authenticated", True))
            cookie = tradingview_cookie_from_inputs(arguments, load_tradingview_session()) if authenticated else ""
            if authenticated and not cookie:
                raise ValueError("Authenticated pre-open mode requires saved TradingView session or cookie/sessionid input.")
            return tradingview_preopen_batch_snapshot(
                raw_symbols,
                exchange=exchange,
                market=market,
                authenticated=authenticated,
                compact=bool(arguments.get("compact", True)),
                max_concurrency=int(arguments.get("max_concurrency", 4)),
                cookie=cookie,
            )

        if tool == "tv_scrape_heatmap":
            market = str(arguments.get("market", TRADINGVIEW_DEFAULT_MARKET))
            limit = int(arguments.get("limit", 50))
            exchanges_raw = arguments.get("exchanges")
            exchanges = [str(item) for item in exchanges_raw] if isinstance(exchanges_raw, list) else None
            snapshot = tradingview_heatmap_snapshot(
                market=market,
                limit=limit,
                exchanges=exchanges,
                min_market_cap=utils.scalar_number(arguments.get("min_market_cap")),
                min_price=utils.scalar_number(arguments.get("min_price")),
                min_volume=utils.scalar_number(arguments.get("min_volume")),
                sector=str(arguments.get("sector") or "") or None,
                industry=str(arguments.get("industry") or "") or None,
                sort_by=str(arguments.get("sort_by", "change") or "change"),
                include_premarket=bool(arguments.get("include_premarket", True)),
                exclude_otc=bool(arguments.get("exclude_otc", True)),
                cookie="",
            )
            snapshot["mode"] = "free_scrape"
            snapshot["experimental_scrape_mode"] = True
            return snapshot

        if tool == "avanza_tv_preopen_portfolio_bundle":
            raw_symbols = arguments.get("include_symbols") or []
            include_symbols = raw_symbols if isinstance(raw_symbols, list) else []
            authenticated = bool(arguments.get("authenticated", True))
            cookie = tradingview_cookie_from_inputs(arguments, load_tradingview_session()) if authenticated else ""
            if authenticated and not cookie:
                raise ValueError("Authenticated pre-open mode requires saved TradingView session or cookie/sessionid input.")
            return self.avanza_tv_preopen_portfolio_bundle_snapshot(
                avanza,
                account_id,
                include_symbols=include_symbols,
                market=str(arguments.get("market", TRADINGVIEW_DEFAULT_MARKET)),
                authenticated=authenticated,
                compact=bool(arguments.get("compact", True)),
                cookie=cookie,
            )

        if tool == "tv_auth_watchlist":
            reference_symbol = str(arguments.get("reference_symbol", "AAPL"))
            exchange = str(arguments.get("exchange", TRADINGVIEW_DEFAULT_EXCHANGE))
            market = str(arguments.get("market", TRADINGVIEW_DEFAULT_MARKET))
            limit = int(arguments.get("limit", 25))
            cookie = tradingview_cookie_from_inputs(arguments, load_tradingview_session())
            if not cookie:
                raise ValueError("Authenticated watchlist mode requires cookie/sessionid input, saved session, or TRADINGVIEW_SESSIONID env.")
            snapshot = tradingview_watchlist_snapshot(
                reference_symbol=reference_symbol,
                exchange=exchange,
                market=market,
                limit=limit,
                cookie=cookie,
            )
            snapshot["mode"] = "authenticated_scrape"
            snapshot["experimental_scrape_mode"] = True
            return snapshot

        if tool == "tv_auth_custom_lists":
            list_id = tradingview_watchlist_id_from_input(arguments.get("list_id"))
            list_id = list_id or None
            list_name = str(arguments.get("list_name", "") or "").strip() or None
            limit = int(arguments.get("limit", TRADINGVIEW_WATCHLIST_ROW_LIMIT))
            snapshot = utils.run_blocking_in_thread(
                tv_data.tradingview_custom_watchlists_from_profile,
                list_id=list_id,
                list_name=list_name,
                limit=max(1, min(limit, TRADINGVIEW_WATCHLIST_ROW_LIMIT)),
            )
            snapshot["mode"] = "authenticated_scrape"
            snapshot["experimental_scrape_mode"] = True
            return snapshot

        if tool == "zacks_scrape_symbol":
            symbol = str(arguments["symbol"])
            cookie = str(arguments.get("cookie", "") or "")
            snapshot = zacks_feed.zacks_symbol_snapshot(symbol, cookie=cookie)
            snapshot["mode"] = "free_scrape"
            snapshot["experimental_scrape_mode"] = True
            return snapshot

        if tool == "fmp_analyst_recommendations":
            symbol = str(arguments["symbol"])
            limit = int(arguments.get("limit", 52))
            api_key = str(arguments.get("api_key", "") or "") or None
            snapshot = feeds.fmp_analyst_recommendations_snapshot(symbol, limit=limit, api_key=api_key)
            snapshot["mode"] = "api"
            snapshot["experimental_scrape_mode"] = True
            return snapshot

        if tool == "polygon_analyst_insights":
            symbol = str(arguments["symbol"])
            limit = int(arguments.get("limit", 50))
            date_value = str(arguments.get("date", "") or "") or None
            api_key = str(arguments.get("api_key", "") or "") or None
            snapshot = feeds.polygon_analyst_insights_snapshot(
                symbol,
                limit=limit,
                date_value=date_value,
                api_key=api_key,
            )
            snapshot["mode"] = "api"
            snapshot["experimental_scrape_mode"] = True
            return snapshot

        if tool == "sec_filings_recent":
            ticker = str(arguments.get("ticker", "") or "") or None
            cik = str(arguments.get("cik", "") or "") or None
            limit = int(arguments.get("limit", 20))
            return feeds.sec_recent_filings_snapshot(ticker=ticker, cik=cik, limit=limit)

        if tool == "fred_series":
            series_id = str(arguments["series_id"])
            api_key = str(arguments.get("api_key", "") or "") or None
            limit = int(arguments.get("limit", 120))
            sort_order = str(arguments.get("sort_order", "desc"))
            return feeds.fred_observations_snapshot(
                series_id=series_id,
                api_key=api_key,
                limit=limit,
                sort_order=sort_order,
            )

        if tool == "data_source_status":
            symbol = str(arguments.get("symbol", "AAPL"))
            exchange = str(arguments.get("exchange", TRADINGVIEW_DEFAULT_EXCHANGE))
            market = str(arguments.get("market", TRADINGVIEW_DEFAULT_MARKET))
            return self.data_source_status_snapshot(symbol=symbol, exchange=exchange, market=market)

        if tool == "signal_context_bundle":
            exchange = str(arguments.get("exchange", TRADINGVIEW_DEFAULT_EXCHANGE))
            market = str(arguments.get("market", TRADINGVIEW_DEFAULT_MARKET))
            symbols = arguments.get("symbols")
            include_tradingview = bool(arguments.get("include_tradingview", True))
            include_zacks = bool(arguments.get("include_zacks", True))
            include_fmp = bool(arguments.get("include_fmp", False))
            include_polygon = bool(arguments.get("include_polygon", False))
            include_sec = bool(arguments.get("include_sec", True))
            fred_series_id = str(arguments.get("fred_series_id", "") or "") or None
            fred_api_key = str(arguments.get("fred_api_key", "") or "") or None
            fmp_api_key = str(arguments.get("fmp_api_key", "") or "") or None
            polygon_api_key = str(arguments.get("polygon_api_key", "") or "") or None
            if isinstance(symbols, list) and symbols:
                return self.signal_context_bundle_batch_snapshot(
                    symbols=symbols,
                    exchange=exchange,
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
                    compact=bool(arguments.get("compact", False)),
                )
            symbol = str(arguments.get("symbol") or "").strip()
            if not symbol:
                raise ValueError("Provide symbol or symbols.")
            return self.signal_context_bundle_snapshot(
                symbol=symbol,
                exchange=exchange,
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

        if tool == "avanza_portfolio":
            return self.portfolio_snapshot(
                avanza,
                account_id,
                orderbook_id=mcp_orderbook_filter(arguments),
                instrument_name=str(arguments.get("instrument_name") or "") or None,
                compact=bool(arguments.get("compact", False)),
                refresh=bool(arguments.get("refresh", False)),
            )

        if tool == "avanza_stoplosses":
            return self.stoploss_snapshot(
                avanza,
                account_id,
                orderbook_id=mcp_orderbook_filter(arguments),
                instrument_name=str(arguments.get("instrument_name") or "") or None,
                side=str(arguments.get("side") or "") or None,
                status=str(arguments.get("status") or "") or None,
                compact=bool(arguments.get("compact", False)),
                refresh=bool(arguments.get("refresh", False)),
            )

        if tool == "avanza_open_orders":
            return self.open_orders_snapshot(
                avanza,
                account_id,
                orderbook_id=mcp_orderbook_filter(arguments),
                instrument_name=str(arguments.get("instrument_name") or "") or None,
                side=str(arguments.get("side") or "") or None,
                status=str(arguments.get("status") or "") or None,
                compact=bool(arguments.get("compact", False)),
                refresh=bool(arguments.get("refresh", False)),
            )

        if tool == "avanza_open_orders_raw":
            include_raw = bool(arguments.get("include_raw", False))
            return self.open_orders_snapshot(
                avanza,
                account_id,
                include_raw=include_raw,
                orderbook_id=mcp_orderbook_filter(arguments),
                instrument_name=str(arguments.get("instrument_name") or "") or None,
                side=str(arguments.get("side") or "") or None,
                status=str(arguments.get("status") or "") or None,
                compact=bool(arguments.get("compact", False)),
                refresh=bool(arguments.get("refresh", False)),
            )

        if tool == "avanza_ongoing_orders":
            include_paper = bool(arguments.get("include_paper", True))
            orderbook_id = mcp_orderbook_filter(arguments)
            instrument_name = str(arguments.get("instrument_name") or "") or None
            side = str(arguments.get("side") or "") or None
            status = str(arguments.get("status") or "") or None
            compact = bool(arguments.get("compact", False))
            return {
                "account_id": account_id or None,
                "stoplosses": self.stoploss_snapshot(
                    avanza,
                    account_id,
                    orderbook_id=orderbook_id,
                    instrument_name=instrument_name,
                    side=side,
                    status=status,
                    compact=compact,
                )["stoplosses"],
                "open_orders": self.open_orders_snapshot(
                    avanza,
                    account_id,
                    orderbook_id=orderbook_id,
                    instrument_name=instrument_name,
                    side=side,
                    status=status,
                    compact=compact,
                )["orders"],
                "paper_orders": (
                    paper_orders(self.paper_session, account_id or None, active_only=True)
                    if include_paper
                    else []
                ),
            }

        if tool == "avanza_transactions":
            transactions_from = parse_optional_iso_date(
                arguments.get("transactions_from") or arguments.get("changed_since") or arguments.get("from"),
                label="transactions_from",
            )
            transactions_to = parse_optional_iso_date(arguments.get("transactions_to") or arguments.get("to"), label="transactions_to")
            return self.transactions_snapshot(
                avanza,
                account_id,
                orderbook_id=mcp_orderbook_filter(arguments),
                instrument_name=str(arguments.get("instrument_name") or "") or None,
                side=str(arguments.get("side") or "") or None,
                status=str(arguments.get("status") or "") or None,
                transactions_from=transactions_from,
                transactions_to=transactions_to,
                types=arguments.get("types"),
                isin=str(arguments.get("isin", "") or "") or None,
                max_elements=int(arguments.get("max_elements", 1000)),
                executed_only=bool(arguments.get("executed_only", True)),
                compact=bool(arguments.get("compact", False)),
            )

        if tool == "avanza_live_snapshot":
            account_id = account_id or self.require_selected_account_id()
            orderbook_id = mcp_orderbook_filter(arguments)
            instrument_name = str(arguments.get("instrument_name") or "") or None
            side = str(arguments.get("side") or "") or None
            status = str(arguments.get("status") or "") or None
            compact = bool(arguments.get("compact", False))
            realtime_quotes = self.realtime_quotes_snapshot(account_id)
            return {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "account_id": account_id,
                "read_write": self.mcp_write_enabled,
                "paper_trading": self.paper_mode_enabled,
                "live_trading_allowed_for_this_session": self.live_trading_allowed_for_session,
                "poll_interval_seconds": LIVE_REFRESH_SECONDS,
                "capabilities": self.mcp_status_payload(),
                "portfolio": self.portfolio_snapshot(
                    avanza,
                    account_id,
                    orderbook_id=orderbook_id,
                    instrument_name=instrument_name,
                    compact=compact,
                ),
                "stoplosses": self.stoploss_snapshot(
                    avanza,
                    account_id,
                    orderbook_id=orderbook_id,
                    instrument_name=instrument_name,
                    side=side,
                    status=status,
                    compact=compact,
                ),
                "open_orders": self.open_orders_snapshot(
                    avanza,
                    account_id,
                    orderbook_id=orderbook_id,
                    instrument_name=instrument_name,
                    side=side,
                    status=status,
                    compact=compact,
                ),
                "realtime_quotes": realtime_quotes,
                "paper_orders": paper_orders(self.paper_session, account_id),
                "paper_positions": paper_positions(self.paper_session, account_id=account_id, active_only=False),
                "paper_trades": paper_trades(self.paper_session, account_id=account_id),
            }

        if tool == "avanza_position":
            requested_orderbook = str(arguments["orderbook_id"])
            snapshot = self.portfolio_snapshot(
                avanza,
                account_id,
                orderbook_id=requested_orderbook,
                compact=bool(arguments.get("compact", False)),
            )
            return {
                "account_id": account_id or None,
                "orderbook_id": requested_orderbook,
                "position": snapshot["positions"][0] if snapshot["positions"] else None,
            }

        if tool == "avanza_instrument_stoplosses":
            return self.stoploss_snapshot(
                avanza,
                account_id,
                orderbook_id=str(arguments["orderbook_id"]),
                side=str(arguments.get("side") or "") or None,
                status=str(arguments.get("status") or "") or None,
                compact=bool(arguments.get("compact", False)),
            )

        if tool == "avanza_instrument_open_orders":
            return self.open_orders_snapshot(
                avanza,
                account_id,
                include_raw=bool(arguments.get("include_raw", True)),
                orderbook_id=str(arguments["orderbook_id"]),
                side=str(arguments.get("side") or "") or None,
                status=str(arguments.get("status") or "") or None,
                compact=bool(arguments.get("compact", False)),
            )

        if tool == "avanza_instrument_transactions":
            transactions_from = parse_optional_iso_date(
                arguments.get("transactions_from") or arguments.get("changed_since") or arguments.get("from") or arguments.get("date"),
                label="transactions_from",
            )
            transactions_to = parse_optional_iso_date(arguments.get("transactions_to") or arguments.get("to") or arguments.get("date"), label="transactions_to")
            return self.transactions_snapshot(
                avanza,
                account_id,
                orderbook_id=str(arguments["orderbook_id"]),
                side=str(arguments.get("side") or "") or None,
                status=str(arguments.get("status") or "") or None,
                transactions_from=transactions_from,
                transactions_to=transactions_to,
                max_elements=int(arguments.get("max_elements", 1000)),
                executed_only=bool(arguments.get("executed_only", True)),
                compact=bool(arguments.get("compact", False)),
            )

        if tool == "avanza_instrument_state":
            return self.instrument_state_snapshot(
                avanza,
                account_id,
                str(arguments["orderbook_id"]),
                transactions_from=parse_optional_iso_date(
                    arguments.get("transactions_from") or arguments.get("changed_since") or arguments.get("from") or arguments.get("date"),
                    label="transactions_from",
                ),
                transactions_to=parse_optional_iso_date(arguments.get("transactions_to") or arguments.get("to") or arguments.get("date"), label="transactions_to"),
                include_raw_orders=bool(arguments.get("include_raw", True)),
            )

        if tool == "avanza_protection_gaps":
            excludes_raw = arguments.get("exclude_orderbook_ids") or []
            excludes = [str(item) for item in excludes_raw] if isinstance(excludes_raw, list) else []
            return self.protection_gaps_snapshot(
                avanza,
                account_id,
                exclude_orderbook_ids=excludes,
                exclude_eth=bool(arguments.get("exclude_eth", False)),
            )

        if tool == "avanza_sold_today_buyback_state":
            return self.sold_today_buyback_state_snapshot(
                avanza,
                account_id,
                trade_date=parse_optional_iso_date(arguments.get("date"), label="date"),
                tight_trigger_percent_max=float(arguments.get("tight_trigger_percent_max", 8.0)),
            )

        if tool == "avanza_recent_fills_needing_protection":
            return self.recent_fills_needing_protection_snapshot(
                avanza,
                account_id,
                since=parse_optional_iso_date(arguments.get("since"), label="since"),
                exclude_eth=bool(arguments.get("exclude_eth", True)),
            )

        if tool == "avanza_verify_no_raw_failed_orders":
            ids_raw = arguments.get("orderbook_ids") or []
            ids = [str(item) for item in ids_raw] if isinstance(ids_raw, list) else []
            return self.verify_no_raw_failed_orders_snapshot(avanza, account_id, orderbook_ids=ids)

        if tool == "avanza_verify_protection":
            ids_raw = arguments.get("orderbook_ids") or []
            ids = [str(item) for item in ids_raw] if isinstance(ids_raw, list) else []
            return self.verify_protection_snapshot(
                avanza,
                account_id,
                orderbook_ids=ids,
                full_holding=bool(arguments.get("full_holding", True)),
                exclude_eth=bool(arguments.get("exclude_eth", True)),
            )

        if tool == "avanza_realtime_quotes":
            account_id = account_id or self.require_selected_account_id()
            return {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "account_id": account_id,
                "poll_interval_seconds": LIVE_REFRESH_SECONDS,
                "quotes": self.realtime_quotes_snapshot(account_id),
            }

        if tool == "avanza_orderbook_quotes":
            raw_ids = arguments.get("orderbook_ids")
            if not isinstance(raw_ids, list) or not raw_ids:
                raise ValueError("orderbook_ids must be a non-empty array.")
            return self.orderbook_quotes_snapshot(
                [str(item).strip() for item in raw_ids if str(item).strip()],
                fields=arguments.get("fields") if isinstance(arguments.get("fields"), list) else None,
                refresh=bool(arguments.get("refresh", True)),
            )

        if tool == "avanza_market_movers":
            country_codes_raw = arguments.get("countryCodes", ["SE"])
            market_places_raw = arguments.get("marketPlaces")
            min_price = utils.scalar_number(arguments.get("min_price"))
            min_total_value_traded = utils.scalar_number(arguments.get("min_total_value_traded"))
            limit = int(arguments.get("limit", 30))
            country_codes = [str(item).strip().upper() for item in country_codes_raw if str(item).strip()] if isinstance(country_codes_raw, list) else ["SE"]
            market_places = [str(item).strip() for item in market_places_raw if str(item).strip()] if isinstance(market_places_raw, list) else []

            gainers_losers_payload = payload_to_json_safe(
                avanza_ext.avanza_private_post(
                    avanza,
                    "/_api/market-stock-filter/stocks/gainers-losers",
                    body={
                        "filter": {
                            "countryCodes": country_codes,
                            "marketPlaces": market_places,
                            "sectors": [],
                        }
                    },
                )
            )
            filter_options_payload = payload_to_json_safe(
                avanza_ext.avanza_private_get(
                    avanza,
                    "/_api/market-stock-filter/stocks/filter-options",
                    options={"countryCodes": country_codes},
                )
            )
            if not isinstance(gainers_losers_payload, dict):
                gainers_losers_payload = {}

            gainers_rows = movers_rows_from_payload(gainers_losers_payload.get("gainers"))
            losers_rows = movers_rows_from_payload(gainers_losers_payload.get("losers"))
            gainers_rows = filter_mover_rows(
                gainers_rows,
                min_price=min_price,
                min_total_value_traded=min_total_value_traded,
                limit=limit,
            )
            losers_rows = filter_mover_rows(
                losers_rows,
                min_price=min_price,
                min_total_value_traded=min_total_value_traded,
                limit=limit,
            )
            losers_rows.sort(key=lambda item: utils.scalar_number(item.get("one_day_change_percent")) or 0.0)
            for row in gainers_rows + losers_rows:
                orderbook_id = str(row.get("orderbook_id") or "").strip()
                if not orderbook_id:
                    continue
                self._cache_orderbook_metadata(
                    orderbook_id,
                    {
                        "orderbook_id": orderbook_id,
                        "name": row.get("name"),
                        "currency": row.get("currency"),
                        "country_code": row.get("country"),
                    },
                )
            return {
                "countryCodes": country_codes,
                "marketPlaces": market_places,
                "min_price": min_price,
                "min_total_value_traded": min_total_value_traded,
                "numberOfGainers": int(gainers_losers_payload.get("numberOfGainers") or len(gainers_rows)),
                "numberOfLosers": int(gainers_losers_payload.get("numberOfLosers") or len(losers_rows)),
                "numberOfNeutrals": int(gainers_losers_payload.get("numberOfNeutrals") or 0),
                "gainers": gainers_rows,
                "losers": losers_rows,
                "filter_options": filter_options_payload,
                "raw": gainers_losers_payload,
            }

        if tool == "avanza_index_constituents":
            index_id = str(arguments.get("index_id", "19002") or "19002").strip()
            index_name = str(arguments.get("index_name", "OMXS30") or "OMXS30").strip()
            include_quotes = bool(arguments.get("include_quotes", False))
            include_spread = bool(arguments.get("include_spread", False))
            if not index_id:
                raise ValueError("index_id is required.")

            raw_payload = payload_to_json_safe(
                avanza_ext.avanza_private_get(
                    avanza,
                    f"/_api/market-index/{index_id}/constituents",
                    options={},
                )
            )
            items: list[dict[str, Any]] = []
            if isinstance(raw_payload, list):
                items = [item for item in raw_payload if isinstance(item, dict)]
            elif isinstance(raw_payload, dict):
                for key in ("constituents", "items", "stocks", "results"):
                    candidate = raw_payload.get(key)
                    if isinstance(candidate, list):
                        items = [item for item in candidate if isinstance(item, dict)]
                        break
            rows = [index_constituent_row(item) for item in items]
            rows = [row for row in rows if row.get("orderbook_id")]
            for row in rows:
                orderbook_id = str(row.get("orderbook_id") or "").strip()
                if not orderbook_id:
                    continue
                self._cache_orderbook_metadata(
                    orderbook_id,
                    {
                        "orderbook_id": orderbook_id,
                        "name": row.get("name"),
                        "ticker": row.get("ticker"),
                        "country_code": row.get("country_code"),
                    },
                )

            if include_quotes:
                enriched: list[dict[str, Any]] = []
                for row in rows:
                    orderbook_id = str(row.get("orderbook_id") or "")
                    quote_payload = self.quote_payload_for_order_book(orderbook_id, refresh=True)
                    quote_row = orderbook_quote_row(
                        orderbook_id,
                        quote_payload,
                        fallback_name=str(row.get("name") or ""),
                        fallback_ticker=str(row.get("ticker") or ""),
                        fallback_currency="SEK" if str(row.get("country_code") or "").upper() == "SE" else "",
                    )
                    merged = {
                        **row,
                        "last": quote_row.get("last"),
                        "bid": quote_row.get("bid"),
                        "ask": quote_row.get("ask"),
                    }
                    if include_spread:
                        merged["spread_absolute"] = quote_row.get("spread_absolute")
                        merged["spread_percent"] = quote_row.get("spread_percent")
                    enriched.append(merged)
                rows = enriched
            elif include_spread:
                enriched = []
                for row in rows:
                    orderbook_id = str(row.get("orderbook_id") or "")
                    quote_payload = self.quote_payload_for_order_book(orderbook_id, refresh=False)
                    quote_row = orderbook_quote_row(
                        orderbook_id,
                        quote_payload,
                        fallback_name=str(row.get("name") or ""),
                        fallback_ticker=str(row.get("ticker") or ""),
                    )
                    merged = {
                        **row,
                        "last": quote_row.get("last"),
                        "bid": quote_row.get("bid"),
                        "ask": quote_row.get("ask"),
                        "spread_absolute": quote_row.get("spread_absolute"),
                        "spread_percent": quote_row.get("spread_percent"),
                    }
                    enriched.append(merged)
                rows = enriched

            return {
                "index_id": index_id,
                "index_name": index_name,
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "constituent_count": len(rows),
                "constituents": rows,
                "include_quotes": include_quotes,
                "include_spread": include_spread,
                "raw": raw_payload,
            }

        if tool == "avanza_fee_estimate":
            side = str(arguments.get("side", "")).strip()
            if not side:
                raise ValueError("side is required.")
            orderbook_id = str(arguments.get("orderbook_id") or arguments.get("order_book_id") or "").strip()
            if not orderbook_id:
                raise ValueError("orderbook_id is required.")
            price = utils.scalar_number(arguments.get("price"))
            quantity = int(arguments.get("quantity", 0) or 0)
            if price is None or price <= 0:
                raise ValueError("price must be > 0.")
            if quantity <= 0:
                raise ValueError("quantity must be > 0.")
            market = str(arguments.get("market", "")).strip()
            metadata = self.orderbook_metadata_for_quote(orderbook_id, quote_payload=None, allow_remote_lookup=True)
            warnings: list[str] = []
            currency_input = str(arguments.get("currency", "")).strip().upper()
            resolved_currency = currency_input or infer_currency_from_metadata(
                {
                    "currency": metadata.get("currency"),
                    "country_code": metadata.get("country_code") or metadata.get("country"),
                    "market": market or metadata.get("market"),
                }
            )
            if not resolved_currency:
                market_lower = (market or str(metadata.get("market", ""))).lower()
                country_code = str(metadata.get("country_code") or metadata.get("country") or "").upper()
                fallback = infer_currency_from_metadata({"country_code": country_code, "market": market_lower})
                if fallback:
                    resolved_currency = fallback
                    warnings.append(f"Currency missing; inferred {fallback} from market/country metadata.")
                elif country_code == "SE" or "stockholm" in market_lower or "xsto" in market_lower:
                    resolved_currency = "SEK"
                    warnings.append("Currency missing; inferred SEK from Swedish market context.")
                else:
                    resolved_currency = "USD"
                    warnings.append("Currency unknown for non-Swedish context; using conservative USD + FX estimate.")
            estimate = estimate_avanza_fee(
                account_id=str(arguments["account_id"]),
                order_book_id=orderbook_id,
                side=side,
                price=price,
                quantity=quantity,
                currency=resolved_currency,
                market=market or str(metadata.get("market", "") or ""),
                brokerage_class=str(arguments.get("brokerage_class", "")),
            )
            if warnings:
                estimate.setdefault("warnings", [])
                if isinstance(estimate["warnings"], list):
                    estimate["warnings"].extend(warnings)
            estimate["resolved_currency"] = resolved_currency
            estimate["metadata"] = {
                "name": metadata.get("name"),
                "ticker": metadata.get("ticker"),
                "market": metadata.get("market"),
                "country": metadata.get("country") or metadata.get("country_code"),
                "instrument_type": metadata.get("instrument_type"),
            }
            return estimate

        if tool == "avanza_search_stock":
            query = str(arguments["query"])
            limit = int(arguments.get("limit", 10))
            hits = flattened_search_hits(avanza.search_for_stock(query, max(10, limit * 3)))
            rows = normalized_search_rows(hits, query=query)
            rows = search_rows_with_market_data(avanza, rows, include_market_data=True)
            trimmed: list[dict[str, Any]] = []
            for row in rows[: max(1, min(limit, 50))]:
                orderbook_id = str(row.get("orderbook_id") or "").strip()
                if orderbook_id:
                    self._cache_orderbook_metadata(
                        orderbook_id,
                        {
                            "orderbook_id": orderbook_id,
                            "name": row.get("name"),
                            "ticker": row.get("ticker"),
                            "display_symbol": row.get("display_symbol"),
                            "market": row.get("market_place"),
                            "currency": row.get("currency"),
                            "country_code": row.get("country"),
                            "instrument_type": row.get("instrument_type"),
                        },
                    )
                trimmed.append(
                    {
                        "name": row.get("name"),
                        "ticker": row.get("ticker"),
                        "symbol": row.get("symbol"),
                        "display_symbol": row.get("display_symbol"),
                        "orderbook_id": row.get("orderbook_id"),
                        "market_place": row.get("market_place"),
                        "country": row.get("country"),
                        "currency": row.get("currency"),
                        "instrument_type": row.get("instrument_type"),
                        "tradeable": row.get("tradeable"),
                        "buyable": row.get("buyable"),
                        "sellable": row.get("sellable"),
                        "last_price": row.get("last_price"),
                        "bid": row.get("bid"),
                        "ask": row.get("ask"),
                        "spread_absolute": row.get("spread_absolute"),
                        "spread_percent": row.get("spread_percent"),
                        "isin": row.get("isin"),
                    }
                )
            return {
                "query": query,
                "count": len(trimmed),
                "results": trimmed,
            }

        if tool == "avanza_stoploss_set":
            confirmed = bool(arguments.get("confirm", False))
            self.require_mcp_write(confirmed)
            trigger, order_event, preview = build_stop_loss_preview(arguments)
            warnings = self.apply_stoploss_valid_days_safety(preview, live=confirmed)
            if not confirmed:
                payload = self.stoploss_mutation_response(
                    dry_run=True,
                    action="set",
                    preview=preview,
                    warnings=warnings,
                )
                payload["summary"] = format_stop_loss_request(preview)
                return payload
            result = avanza.place_stop_loss_order(
                parent_stop_loss_id=preview["parent_stop_loss_id"],
                account_id=preview["account_id"],
                order_book_id=preview["order_book_id"],
                stop_loss_trigger=trigger,
                stop_loss_order_event=order_event,
            )
            self.record_event("trading", "live_stoploss_set", {"request": preview, "result": result})
            return self.stoploss_mutation_response(
                dry_run=False,
                action="set",
                preview=preview,
                result=result,
                warnings=warnings,
            )

        if tool == "avanza_stoploss_set_batch":
            confirmed = bool(arguments.get("confirm", False))
            self.require_mcp_write(confirmed)
            batch_items = arguments.get("items")
            if not isinstance(batch_items, list) or not batch_items:
                raise ValueError("items must be a non-empty array.")
            parent_account_id = str(arguments["account_id"])
            prepared: list[tuple[StopLossTrigger, StopLossOrderEvent, dict[str, Any], list[str]]] = []
            dry_run_results: list[dict[str, Any]] = []
            for index, item in enumerate(batch_items):
                if not isinstance(item, dict):
                    raise ValueError(f"items[{index}] must be an object.")
                item_args = {**item, "account_id": parent_account_id}
                trigger, order_event, preview = build_stop_loss_preview(item_args)
                warnings = self.apply_stoploss_valid_days_safety(preview, live=confirmed)
                prepared.append((trigger, order_event, preview, warnings))
                if not confirmed:
                    dry_run_results.append(
                        {
                            "index": index,
                            **self.stoploss_mutation_response(
                                dry_run=True,
                                action="set",
                                preview=preview,
                                warnings=warnings,
                            ),
                        }
                    )
            if not confirmed:
                return {
                    "dry_run": True,
                    "account_id": parent_account_id,
                    "count": len(dry_run_results),
                    "results": dry_run_results,
                }

            results: list[dict[str, Any]] = []
            for index, (trigger, order_event, preview, warnings) in enumerate(prepared):
                try:
                    result = avanza.place_stop_loss_order(
                        parent_stop_loss_id=preview["parent_stop_loss_id"],
                        account_id=preview["account_id"],
                        order_book_id=preview["order_book_id"],
                        stop_loss_trigger=trigger,
                        stop_loss_order_event=order_event,
                    )
                    payload = self.stoploss_mutation_response(
                        dry_run=False,
                        action="set",
                        preview=preview,
                        result=result,
                        warnings=warnings,
                    )
                    payload["index"] = index
                    readback = payload.get("readback") if isinstance(payload.get("readback"), dict) else {}
                    if readback and str(readback.get("orderbook_id") or "") != str(preview["order_book_id"]):
                        payload["ok"] = False
                        payload["error"] = "Readback orderbook_id mismatch; stopping batch."
                        results.append(payload)
                        break
                    payload["ok"] = True
                    results.append(payload)
                except Exception as exc:
                    results.append(
                        {
                            "index": index,
                            "ok": False,
                            "dry_run": False,
                            "account_id": parent_account_id,
                            "orderbook_id": preview.get("order_book_id"),
                            "error": str(exc),
                        }
                    )
                    break
            self.record_event("trading", "live_stoploss_set_batch", {"count": len(results), "results": results})
            touched_ids = [
                str(item.get("orderbook_id") or "")
                for item in results
                if isinstance(item, dict) and item.get("orderbook_id")
            ]
            return {
                "dry_run": False,
                "account_id": parent_account_id,
                "requested_count": len(prepared),
                "completed_count": len(results),
                "all_ok": all(bool(item.get("ok")) for item in results) and len(results) == len(prepared),
                "results": results,
                "verification": self.verify_protection_snapshot(
                    avanza,
                    parent_account_id,
                    orderbook_ids=touched_ids,
                    full_holding=False,
                    exclude_eth=False,
                ),
            }

        if tool == "avanza_order_set":
            confirmed = bool(arguments.get("confirm", False))
            self.require_mcp_write(confirmed)
            order_type, condition, preview = build_order_preview(arguments)
            if not confirmed:
                return {"dry_run": True, "summary": format_order_request(preview), "request": preview}
            result = avanza.place_order(
                account_id=preview["account_id"],
                order_book_id=preview["order_book_id"],
                order_type=order_type,
                price=preview["price"],
                valid_until=date.fromisoformat(preview["valid_until"]),
                volume=preview["volume"],
                condition=condition,
            )
            self.record_event("trading", "live_order_set", {"request": preview, "result": result})
            return {"dry_run": False, "request": preview, "result": result}

        if tool in {"avanza_order_edit", "avanza_open_order_edit"}:
            confirmed = bool(arguments.get("confirm", False))
            self.require_mcp_write(confirmed)
            valid_until = arguments.get("valid_until")
            if isinstance(valid_until, str):
                valid_until = date.fromisoformat(valid_until)
            if not isinstance(valid_until, date):
                raise ValueError("valid_until must be an ISO date string.")
            valid_until = validate_valid_until(valid_until, "valid_until")
            request = {
                "account_id": str(arguments["account_id"]),
                "order_id": str(arguments["order_id"]),
                "price": float(arguments["price"]),
                "valid_until": valid_until.isoformat(),
                "volume": int(arguments["volume"]),
            }
            if not confirmed:
                return {"dry_run": True, "request": request}
            result = avanza.edit_order(
                order_id=request["order_id"],
                account_id=request["account_id"],
                price=request["price"],
                valid_until=valid_until,
                volume=request["volume"],
            )
            self.record_event("trading", "live_order_edit", {"request": request, "result": result})
            return {"dry_run": False, "request": request, "result": result}

        if tool == "avanza_paper_stoploss_set":
            paper_order = create_paper_stop_loss_order(arguments, instrument=str(arguments.get("instrument", "")))
            self.paper_session.setdefault("orders", []).append(paper_order)
            append_paper_event(self.paper_session, "paper_stoploss_set", {"id": paper_order["id"], "request": paper_order["request"]})
            self.save_paper_state()
            self.record_event("trading", "paper_stoploss_set", {"order": paper_order})
            return {"paper": True, "order": paper_order}

        if tool == "avanza_paper_order_set":
            paper_order = create_paper_order(arguments, instrument=str(arguments.get("instrument", "")))
            self.paper_session.setdefault("orders", []).append(paper_order)
            append_paper_event(self.paper_session, "paper_order_set", {"id": paper_order["id"], "request": paper_order["request"]})
            if bool(arguments.get("fill_immediately", False)):
                request = paper_order.get("request", {}) if isinstance(paper_order.get("request"), dict) else {}
                session_id = paper_session_id(arguments.get("session_id"))
                price = utils.scalar_number(request.get("price")) or 0.0
                quantity = int(request.get("volume", 0) or 0)
                if quantity <= 0 or price <= 0:
                    raise ValueError("Paper fill_immediately requires price > 0 and volume > 0.")
                fee = estimate_avanza_fee(
                    account_id=str(request.get("account_id", "")),
                    order_book_id=str(request.get("order_book_id", "")),
                    side=str(request.get("order_type", "buy")),
                    price=price,
                    quantity=quantity,
                    currency="SEK",
                )
                position = paper_open_position(
                    self.paper_session,
                    session_id=session_id,
                    account_id=str(request.get("account_id", "")),
                    order_book_id=str(request.get("order_book_id", "")),
                    ticker=str(arguments.get("ticker", "") or ""),
                    name=str(paper_order.get("instrument", "") or arguments.get("instrument", "")),
                    side=str(request.get("order_type", "buy")),
                    entry_price=price,
                    quantity=quantity,
                    estimated_fees=float(fee.get("estimated_total_cost", 0.0) or 0.0),
                    entry_reason=str(arguments.get("entry_reason", "") or ""),
                    stop_price=utils.scalar_number(arguments.get("stop_price")),
                    target_price=utils.scalar_number(arguments.get("target_price")),
                )
                paper_order["status"] = "FILLED"
                paper_order["updated_at"] = datetime.now().isoformat(timespec="seconds")
                append_paper_event(
                    self.paper_session,
                    "paper_order_filled",
                    {"order_id": paper_order["id"], "position_id": position["position_id"]},
                )
            self.save_paper_state()
            self.record_event("trading", "paper_order_set", {"order": paper_order})
            return {"paper": True, "order": paper_order}

        if tool == "avanza_paper_order_exit":
            requested_account_id = str(arguments.get("account_id") or self.selected_account_id or "")
            if not requested_account_id:
                raise ValueError("account_id is required.")
            position_id = str(arguments.get("position_id", "") or "") or None
            orderbook_id = str(arguments.get("orderbook_id", "") or "") or None
            exit_price = utils.scalar_number(arguments.get("exit_price"))
            if exit_price is None or exit_price <= 0:
                raise ValueError("exit_price must be > 0.")
            session_id = paper_session_id(arguments.get("session_id"))
            candidate_positions = paper_positions(self.paper_session, account_id=requested_account_id, session_id=None, active_only=True)
            target_position = None
            if position_id:
                target_position = next((item for item in candidate_positions if str(item.get("position_id", "")) == position_id), None)
            elif orderbook_id:
                target_position = next((item for item in candidate_positions if str(item.get("orderbook_id", "")) == str(orderbook_id)), None)
            if target_position is None:
                raise ValueError("No matching open paper position found.")
            quantity = max(1, int(target_position.get("quantity", 0) or 1))
            exit_fee = estimate_avanza_fee(
                account_id=requested_account_id,
                order_book_id=str(target_position.get("orderbook_id", "") or orderbook_id or ""),
                side="sell",
                price=float(exit_price),
                quantity=quantity,
                currency="SEK",
            )
            trade = paper_exit_position(
                self.paper_session,
                account_id=requested_account_id,
                position_id=position_id,
                order_book_id=orderbook_id,
                exit_price=float(exit_price),
                estimated_exit_fees=float(exit_fee.get("estimated_total_cost", 0.0) or 0.0),
                exit_reason=str(arguments.get("exit_reason", "") or ""),
            )
            trade["session_id"] = trade.get("session_id") or session_id
            self.save_paper_state()
            self.record_event("trading", "paper_order_exit", {"trade": trade})
            return {"paper": True, "trade": trade}

        if tool == "avanza_paper_orders":
            requested_account_id = str(arguments.get("account_id") or self.selected_account_id or "")
            active_only = bool(arguments.get("active_only", False))
            return {
                "paper": True,
                "account_id": requested_account_id or None,
                "orders": paper_orders(self.paper_session, requested_account_id or None, active_only),
                "events": self.paper_session.get("events", []),
            }

        if tool == "avanza_paper_positions":
            requested_account_id = str(arguments.get("account_id") or self.selected_account_id or "")
            if not requested_account_id:
                raise ValueError("account_id is required.")
            session_id = str(arguments.get("session_id", "") or "") or None
            active_only = bool(arguments.get("active_only", False))
            return {
                "paper": True,
                "account_id": requested_account_id,
                "session_id": session_id,
                "positions": paper_positions(
                    self.paper_session,
                    account_id=requested_account_id,
                    session_id=session_id,
                    active_only=active_only,
                ),
            }

        if tool == "avanza_paper_trades":
            requested_account_id = str(arguments.get("account_id") or self.selected_account_id or "")
            if not requested_account_id:
                raise ValueError("account_id is required.")
            session_id = str(arguments.get("session_id", "") or "") or None
            return {
                "paper": True,
                "account_id": requested_account_id,
                "session_id": session_id,
                "trades": paper_trades(self.paper_session, account_id=requested_account_id, session_id=session_id),
            }

        if tool == "avanza_paper_session_summary":
            requested_account_id = str(arguments.get("account_id") or self.selected_account_id or "")
            if not requested_account_id:
                raise ValueError("account_id is required.")
            session_id = str(arguments.get("session_id", "") or "") or None
            summary = paper_session_summary(self.paper_session, session_id=session_id, account_id=requested_account_id)
            summary["paper"] = True
            return summary

        if tool == "avanza_paper_risk_state":
            requested_account_id = str(arguments.get("account_id") or self.selected_account_id or "")
            if not requested_account_id:
                raise ValueError("account_id is required.")
            session_id = paper_session_id(arguments.get("session_id"))
            return {
                "paper": True,
                **paper_risk_state(
                    self.paper_session,
                    session_id=session_id,
                    account_id=requested_account_id,
                    max_open_trades=max(1, int(arguments.get("max_open_trades", 3))),
                    max_trade_notional_sek=max(0.0, float(arguments.get("max_trade_notional_sek", 5000) or 0.0)),
                    max_loss_per_trade_sek=max(0.0, float(arguments.get("max_loss_per_trade_sek", 250) or 0.0)),
                    max_session_loss_sek=max(0.0, float(arguments.get("max_session_loss_sek", 800) or 0.0)),
                    stop_after_consecutive_losses=max(0, int(arguments.get("stop_after_consecutive_losses", 3))),
                ),
            }

        if tool == "avanza_scalp_watchlist_set":
            watchlist_id = str(arguments.get("watchlist_id", "")).strip()
            if not watchlist_id:
                raise ValueError("watchlist_id is required.")
            items = arguments.get("items")
            if not isinstance(items, list) or not items:
                raise ValueError("items must be a non-empty array.")
            normalized: list[dict[str, Any]] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                orderbook_id = str(item.get("orderbook_id", "")).strip()
                if not orderbook_id:
                    continue
                normalized.append(
                    {
                        "orderbook_id": orderbook_id,
                        "label": str(item.get("label", "") or "").strip() or None,
                    }
                )
            if not normalized:
                raise ValueError("No valid watchlist items.")
            watchlists = self.paper_session.setdefault("watchlists", {})
            watchlists[watchlist_id] = normalized
            append_paper_event(self.paper_session, "watchlist_set", {"watchlist_id": watchlist_id, "count": len(normalized)})
            self.save_paper_state()
            return {"paper": True, "watchlist_id": watchlist_id, "count": len(normalized), "items": normalized}

        if tool == "avanza_scalp_watchlist_get":
            watchlist_id = str(arguments.get("watchlist_id", "")).strip()
            if not watchlist_id:
                raise ValueError("watchlist_id is required.")
            include_quotes = bool(arguments.get("include_quotes", True))
            watchlists = self.paper_session.setdefault("watchlists", {})
            items = watchlists.get(watchlist_id)
            if not isinstance(items, list):
                raise ValueError(f"Unknown watchlist_id: {watchlist_id}")
            payload: dict[str, Any] = {
                "paper": True,
                "watchlist_id": watchlist_id,
                "count": len(items),
                "items": items,
            }
            if include_quotes:
                orderbook_ids = [str(item.get("orderbook_id", "")).strip() for item in items if str(item.get("orderbook_id", "")).strip()]
                quotes: list[dict[str, Any]] = []
                for orderbook_id in orderbook_ids:
                    quote_payload = self.quote_payload_for_order_book(orderbook_id, refresh=True)
                    fallback_name = ""
                    for item in items:
                        if str(item.get("orderbook_id", "")).strip() == orderbook_id:
                            fallback_name = str(item.get("label", "") or "")
                            break
                    quotes.append(orderbook_quote_row(orderbook_id, quote_payload, fallback_name=fallback_name))
                payload["quotes"] = quotes
            return payload

        if tool == "avanza_paper_cancel":
            paper_order = cancel_paper_order(self.paper_session, str(arguments["paper_order_id"]))
            self.save_paper_state()
            self.record_event("trading", "paper_order_cancel", {"order": paper_order})
            return {"paper": True, "order": paper_order}

        if tool in {"avanza_order_delete", "avanza_open_order_cancel"}:
            confirmed = bool(arguments.get("confirm", False))
            self.require_mcp_write(confirmed)
            request = {
                "account_id": str(arguments["account_id"]),
                "order_id": str(arguments["order_id"]),
            }
            if not confirmed:
                return {"dry_run": True, "request": request}
            result = avanza.delete_order(request["account_id"], request["order_id"])
            self.record_event("trading", "live_order_delete", {"request": request, "result": result})
            return {"dry_run": False, "request": request, "result": result}

        if tool == "avanza_stoploss_delete":
            confirmed = bool(arguments.get("confirm", False))
            self.require_mcp_write(confirmed)
            request = {
                "account_id": str(arguments["account_id"]),
                "stop_loss_id": str(arguments["stop_loss_id"]),
            }
            if not confirmed:
                return {"dry_run": True, "request": request}
            result = avanza.delete_stop_loss_order(request["account_id"], request["stop_loss_id"])
            self.record_event("trading", "live_stoploss_delete", {"request": request, "result": result})
            return {
                "dry_run": False,
                "action": "delete",
                "stop_loss_id": request["stop_loss_id"],
                "account_id": request["account_id"],
                "status": "DELETED",
                "request": request,
                "result": result,
            }

        if tool in {"avanza_stoploss_replace", "avanza_stoploss_edit"}:
            confirmed = bool(arguments.get("confirm", False))
            self.require_mcp_write(confirmed)
            stop_loss_id = str(arguments["stop_loss_id"])
            trigger, order_event, preview = build_stop_loss_preview(arguments)
            warnings = self.apply_stoploss_valid_days_safety(preview, live=confirmed)
            deprecated_alias = tool == "avanza_stoploss_replace"
            request = {
                "stop_loss_id": stop_loss_id,
                "replacement": preview,
            }
            if not confirmed:
                payload = self.stoploss_mutation_response(
                    dry_run=True,
                    action="edit",
                    preview=preview,
                    warnings=warnings,
                    deleted_stop_loss_id=stop_loss_id,
                    deprecated_alias=deprecated_alias,
                )
                payload["summary"] = format_stop_loss_request(preview)
                payload["request"] = request
                if deprecated_alias:
                    payload["warning"] = "avanza_stoploss_replace is deprecated; use avanza_stoploss_edit."
                return payload
            delete_result = avanza.delete_stop_loss_order(preview["account_id"], stop_loss_id)
            place_result = avanza.place_stop_loss_order(
                parent_stop_loss_id=preview["parent_stop_loss_id"],
                account_id=preview["account_id"],
                order_book_id=preview["order_book_id"],
                stop_loss_trigger=trigger,
                stop_loss_order_event=order_event,
            )
            result = {"delete": delete_result, "place": place_result}
            self.record_event(
                "trading",
                "live_stoploss_edit",
                {"request": request, "result": result, "used_deprecated_alias": deprecated_alias},
            )
            payload = self.stoploss_mutation_response(
                dry_run=False,
                action="edit",
                preview=preview,
                result=result,
                warnings=warnings,
                deleted_stop_loss_id=stop_loss_id,
                deprecated_alias=deprecated_alias,
            )
            payload["request"] = request
            if deprecated_alias:
                payload["warning"] = "avanza_stoploss_replace is deprecated; use avanza_stoploss_edit."
            return payload

        raise ValueError(f"Unknown MCP tool: {tool}")

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

    def apply_stoploss_valid_days_safety(self, preview: dict[str, Any], *, live: bool) -> list[str]:
        order_book_id = str(preview.get("order_book_id") or "").strip()
        order_event = preview.get("stop_loss_order_event")
        if not isinstance(order_event, dict):
            return []
        valid_days = normalize_stoploss_order_valid_days(order_event.get("valid_days"), "order_valid_days")
        metadata = self.stoploss_metadata_for_orderbook(order_book_id) if order_book_id else {}
        warnings = enforce_live_stoploss_order_valid_days(valid_days, metadata, live=live)
        order_event["valid_days"] = valid_days
        order_event["derived_expiry_if_triggered_today"] = stoploss_triggered_order_expiry(valid_days)
        preview["warnings"] = warnings
        return warnings

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

    def set_selected_account(self, account: dict[str, Any]) -> None:
        account_id = str(account.get("id", ""))
        if not account_id:
            raise ValueError("Selected account has no id.")

        self.selected_account_id = account_id
        self.update_selected_account_summary()
        account_select = self.query_one("#account-select", Select)
        if account_select.value != account_id:
            account_select.value = account_id
        self.write_log(f"Selected account {account_display_name(account)} ({account_id}).")
        self.sync_active_state_to_tenant()
        self.refresh_session_select_options()

    def restore_account_select_to_current(self) -> None:
        try:
            account_select = self.query_one("#account-select", Select)
        except Exception:
            return
        if not self.selected_account_id:
            return
        self.account_select_updating = True
        try:
            if account_select.value != self.selected_account_id:
                account_select.value = self.selected_account_id
        finally:
            self.account_select_updating = False

    def build_stop_loss_request(self) -> tuple[StopLossTrigger, StopLossOrderEvent, dict[str, Any]]:
        selected_account_id = self.require_selected_account_id()
        order_book_id = self.input_value("instrument-select")
        if not order_book_id:
            raise ValueError("Select a portfolio holding first.")
        valid_until = self.input_date_value("valid-until", "Stop-loss valid until")
        trigger = StopLossTrigger(
            type=enum_value(StopLossTriggerType, self.input_value("trigger-type")),
            value=self.input_float_value("trigger-value", "Trigger value"),
            valid_until=valid_until,
            value_type=enum_value(StopLossPriceType, self.input_value("trigger-value-type")),
            trigger_on_market_maker_quote=self.switch_value("trigger-on-market-maker-quote"),
        )
        order_event = StopLossOrderEvent(
            type=enum_value(OrderType, self.input_value("order-type")),
            price=self.input_float_value("order-price", "Order price"),
            volume=self.input_float_value("volume", "Volume"),
            valid_days=normalize_stoploss_order_valid_days(
                self.input_int_value("order-valid-days", "Order valid days"),
                "Order valid days",
            ),
            price_type=enum_value(StopLossPriceType, self.input_value("order-price-type")),
            short_selling_allowed=self.switch_value("short-selling-allowed"),
        )
        preview = {
            "account_id": selected_account_id,
            "order_book_id": order_book_id,
            "parent_stop_loss_id": "0",
            "stop_loss_trigger": {
                "type": trigger.type.value,
                "value": trigger.value,
                "valid_until": trigger.valid_until.isoformat(),
                "value_type": trigger.value_type.value,
                "trigger_on_market_maker_quote": trigger.trigger_on_market_maker_quote,
            },
            "stop_loss_order_event": {
                "type": order_event.type.value,
                "price": order_event.price,
                "volume": order_event.volume,
                "valid_days": order_event.valid_days,
                "derived_expiry_if_triggered_today": stoploss_triggered_order_expiry(order_event.valid_days),
                "price_type": order_event.price_type.value,
                "short_selling_allowed": order_event.short_selling_allowed,
            },
            "warnings": [],
        }
        return trigger, order_event, preview

    def build_regular_order_request(self) -> tuple[OrderType, Condition, dict[str, Any]]:
        selected_account_id = self.require_selected_account_id()
        order_book_id = self.input_value("order-instrument-select")
        if not order_book_id:
            raise ValueError("Select a stock/order book first.")
        return build_order_preview(
            {
                "account_id": selected_account_id,
                "order_book_id": order_book_id,
                "order_type": self.input_value("regular-order-type"),
                "price": self.input_float_value("regular-order-price", "Limit price"),
                "valid_until": self.input_date_value("regular-order-valid-until", "Valid until"),
                "volume": self.input_int_value("regular-order-volume", "Volume"),
                "condition": self.input_value("regular-order-condition"),
            }
        )

    def _refresh_stoplosses_impl(self) -> None:
        avanza = self.require_connection()
        try:
            orders = avanza.get_orders()
        except Exception as exc:
            self.write_log(f"[yellow]Could not load open orders:[/yellow] {exc}")
            orders = []
        self.apply_stoploss_orders_data(avanza.get_all_stop_losses(), orders)

    def refresh_stoplosses(self) -> None:
        self.run_profiled("refresh_stoplosses", self._refresh_stoplosses_impl)

    def _refresh_orders_overlay_impl(self) -> None:
        avanza = self.require_connection()
        table = self.query_one("#orders-history-table", DataTable)
        selected_row_key = selected_table_row_key(table)
        table.clear()

        payload = avanza.get_transactions_details(
            transaction_details_types=[TransactionsDetailsType.BUY, TransactionsDetailsType.SELL],
            max_elements=5000,
        )
        items, _first_date = transactions_items(payload)
        rows = [
            transaction_order_history_row(item)
            for item in items
            if transaction_matches_filters(item, self.selected_account_id, executed_only=True)
        ]
        for index, row in enumerate(rows):
            table.add_row(*row, key=f"orders-history-{index}")
        restore_table_row_selection(table, selected_row_key)
        suffix = f" for account {self.selected_account_id}" if self.selected_account_id else ""
        self.write_log(f"Loaded {len(rows)} completed order row(s){suffix}.")

    def refresh_orders_overlay(self) -> None:
        self.run_profiled("refresh_orders_overlay", self._refresh_orders_overlay_impl)

    def open_orders_overlay(self) -> None:
        self.refresh_orders_overlay()
        self.query_one("#orders-overlay").display = True

    def close_orders_overlay(self) -> None:
        self.query_one("#orders-overlay").display = False

    def _refresh_transactions_overlay_impl(self) -> None:
        avanza = self.require_connection()
        table = self.query_one("#transactions-history-table", DataTable)
        selected_row_key = selected_table_row_key(table)
        table.clear()

        payload = avanza.get_transactions_details(
            transaction_details_types=list(TransactionsDetailsType),
            max_elements=5000,
        )
        items, _first_date = transactions_items(payload)
        rows = [
            transaction_activity_row(item)
            for item in items
            if transaction_matches_filters(item, self.selected_account_id, executed_only=False)
        ]
        for index, row in enumerate(rows):
            table.add_row(*row, key=f"transactions-history-{index}")
        restore_table_row_selection(table, selected_row_key)
        suffix = f" for account {self.selected_account_id}" if self.selected_account_id else ""
        self.write_log(f"Loaded {len(rows)} transaction row(s){suffix}.")

    def refresh_transactions_overlay(self) -> None:
        self.run_profiled("refresh_transactions_overlay", self._refresh_transactions_overlay_impl)

    def open_transactions_overlay(self) -> None:
        self.refresh_transactions_overlay()
        self.query_one("#transactions-overlay").display = True

    def close_transactions_overlay(self) -> None:
        self.query_one("#transactions-overlay").display = False

    def tv_list_selection(self, value: str | None = None) -> dict[str, str | None]:
        selection = value
        if selection is None:
            widget_value = self.query_one("#tv-lists-select", Select).value
            if not self.is_blank_select_value(widget_value):
                selection = str(widget_value)
        ref = self.tv_list_option_refs.get(str(selection or ""), {})
        list_id = str(ref.get("id", "") or "").strip() or None
        list_name = str(ref.get("name", "") or "").strip() or None
        return {"value": str(selection or ""), "list_id": list_id, "list_name": list_name}

    def refresh_tv_lists_if_visible(self) -> None:
        if self.query_one("#tv-lists-overlay").display:
            self.refresh_tv_lists()

    def refresh_tv_lists(self, selection_value: str | None = None) -> None:
        selection = self.tv_list_selection(selection_value)
        with self.tv_lists_refresh_lock:
            if self.tv_lists_refresh_inflight:
                self.tv_lists_refresh_pending_value = selection.get("value") or self.tv_lists_refresh_pending_value
                return
            self.tv_lists_refresh_inflight = True
        self.query_one("#tv-lists-overlay-note", Static).update("Loading TradingView custom lists...")
        self.tv_lists_refresh_thread = threading.Thread(
            target=self._refresh_tv_lists_worker,
            args=(selection.get("list_id"), selection.get("list_name")),
            daemon=True,
            name="avanza-tv-lists-refresh",
        )
        self.tv_lists_refresh_thread.start()

    def _refresh_tv_lists_worker(self, list_id: str | None, list_name: str | None) -> None:
        try:
            snapshot = tv_data.tradingview_custom_watchlists_from_profile(
                list_id=list_id,
                list_name=list_name,
                limit=TRADINGVIEW_WATCHLIST_ROW_LIMIT,
            )
            self.safe_call_from_thread(self.apply_tv_lists_snapshot, snapshot)
        except Exception as exc:
            self.safe_call_from_thread(self.write_log, f"[yellow]TradingView list refresh failed:[/yellow] {exc}")
            self.safe_call_from_thread(self.set_tv_lists_overlay_note, f"TradingView list refresh failed: {exc}")
        finally:
            if not self.safe_call_from_thread(self.finish_tv_lists_refresh):
                with self.tv_lists_refresh_lock:
                    self.tv_lists_refresh_pending_value = None
                    self.tv_lists_refresh_inflight = False

    def finish_tv_lists_refresh(self) -> None:
        pending_value = None
        with self.tv_lists_refresh_lock:
            pending_value = self.tv_lists_refresh_pending_value
            self.tv_lists_refresh_pending_value = None
            self.tv_lists_refresh_inflight = False
        if pending_value:
            self.refresh_tv_lists(pending_value)

    def apply_tv_lists_snapshot(self, snapshot: dict[str, Any]) -> None:
        list_entries = [entry for entry in snapshot.get("lists", []) if isinstance(entry, dict)]
        rows = [item for item in snapshot.get("items", []) if isinstance(item, dict)]
        self.latest_tv_lists = list_entries
        self.latest_tv_list_items = rows

        select = self.query_one("#tv-lists-select", Select)
        prior = select.value
        options: list[tuple[str, str]] = []
        refs: dict[str, dict[str, str]] = {}
        for index, entry in enumerate(list_entries):
            entry_id = str(entry.get("id", "") or "").strip()
            entry_name = str(entry.get("name", "") or entry.get("raw_label", "") or f"List {index + 1}").strip()
            count = utils.scalar_number(entry.get("count"))
            count_suffix = f" ({int(count)})" if count is not None else ""
            option_value = entry_id or f"list-{index}"
            options.append((f"{entry_name}{count_suffix}", option_value))
            refs[option_value] = {"id": entry_id, "name": entry_name}
        self.tv_list_option_refs = refs
        self.tv_lists_select_updating = True
        select.set_options(options)

        selected_entry = snapshot.get("selected_list") if isinstance(snapshot.get("selected_list"), dict) else {}
        selected_id = str(selected_entry.get("id", "") or "").strip()
        selected_name = str(selected_entry.get("name", "") or "").strip().lower()

        selected_value = None
        if not self.is_blank_select_value(prior) and str(prior) in refs:
            selected_value = str(prior)
        if selected_id:
            selected_value = next((value for value, ref in refs.items() if ref.get("id") == selected_id), selected_value)
        if selected_value is None and selected_name:
            selected_value = next(
                (value for value, ref in refs.items() if str(ref.get("name", "")).strip().lower() == selected_name),
                None,
            )
        if selected_value is None and options:
            selected_value = options[0][1]
        self.tv_lists_loaded_value = str(selected_value or "")
        if selected_value is not None:
            select.value = selected_value
        self.tv_lists_select_updating = False

        table = self.query_one("#tv-lists-table", DataTable)
        selected_row = selected_table_row_key(table)
        table.clear()
        for index, item in enumerate(rows):
            row = (
                str(item.get("symbol", "") or "-"),
                str(item.get("last", "") or "-"),
                str(item.get("change", "") or "-"),
                str(item.get("change_percent", "") or "-"),
                str(item.get("volume", "") or "-"),
                str(item.get("market_status", "") or "-"),
            )
            table.add_row(*row, key=f"tv-list-row-{index}")
        restore_table_row_selection(table, selected_row)

        active_name = str(snapshot.get("active_list_name", "") or "")
        as_of = str(snapshot.get("as_of", "") or "")
        self.set_tv_lists_overlay_note(f"Loaded {len(rows)} row(s) from {active_name or 'TradingView list'} at {as_of}.")
        if self.query_one("#tv-lists-overlay").display:
            self.write_log(f"Loaded {len(rows)} TradingView list row(s) from {active_name or 'selected list'}.")

    def set_tv_lists_overlay_note(self, message: str) -> None:
        self.query_one("#tv-lists-overlay-note", Static).update(message)

    def open_tv_lists_overlay(self) -> None:
        self.query_one("#tv-lists-overlay").display = True
        self.refresh_tv_lists()

    def close_tv_lists_overlay(self) -> None:
        self.query_one("#tv-lists-overlay").display = False

    def reset_stoploss_modal_for_new(self) -> None:
        self.pending_stoploss_edit_id = None
        self.query_one("#stoploss-modal-title", Static).update("New Stop-Loss")
        self.query_one("#place-confirm", Input).value = ""
        self.query_one("#place-live", Button).label = "Create Paper Stop-Loss" if self.paper_mode_enabled else "Submit Live Stop-Loss"

    def selected_stoploss_item(self) -> dict[str, Any]:
        table = self.query_one("#active-trades-table", DataTable)
        row_key = selected_table_row_key(table)
        if row_key is None:
            raise ValueError("Select a stop-loss row in Active Stop-Losses first.")
        row_key_value = str(getattr(row_key, "value", row_key))
        target = self.cancel_targets_by_row_key.get(row_key_value, {})
        if str(target.get("kind", "")).lower() != "stop-loss":
            raise ValueError("Selected active row is not a stop-loss entry.")
        target_id = str(target.get("id", ""))
        item = next((entry for entry in self.latest_stoploss_items if str(entry.get("id", "")) == target_id), None)
        if item is None:
            raise ValueError("Could not resolve selected stop-loss entry.")
        return item

    def open_stoploss_edit_modal(self) -> None:
        item = self.selected_stoploss_item()
        orderbook = item.get("orderbook") if isinstance(item.get("orderbook"), dict) else {}
        trigger = item.get("trigger") if isinstance(item.get("trigger"), dict) else {}
        order = item.get("order") if isinstance(item.get("order"), dict) else {}
        order_book_id = str(orderbook.get("id", ""))
        if not order_book_id:
            raise ValueError("Selected stop-loss is missing order book id.")

        self.pending_stoploss_edit_id = str(item.get("id", ""))
        if not self.pending_stoploss_edit_id:
            raise ValueError("Selected stop-loss is missing id.")

        trigger_value_type = str(trigger.get("valueType", "") or "SEK")
        order_price_type = str(order.get("priceType", "") or "SEK")
        try:
            trigger_value_type = parse_price_type(trigger_value_type)
        except Exception:
            trigger_value_type = "monetary"
        try:
            order_price_type = parse_price_type(order_price_type)
        except Exception:
            order_price_type = "monetary"

        self.query_one("#stoploss-modal-title", Static).update("Edit Stop-Loss")
        self.query_one("#instrument-select", Select).value = order_book_id
        self.query_one("#volume", Input).value = str(order.get("volume", "") or "")
        self.query_one("#trigger-type", Select).value = str(trigger.get("type", "") or "follow-upwards").lower().replace("_", "-")
        self.query_one("#trigger-value", Input).value = str(trigger.get("value", "") or "")
        self.query_one("#trigger-value-type", Select).value = trigger_value_type
        self.query_one("#valid-until", Input).value = str(trigger.get("validUntil", "") or max_valid_until_date().isoformat())
        self.query_one("#order-type", Select).value = str(order.get("type", "") or "sell").lower()
        self.query_one("#order-price", Input).value = str(order.get("price", "") or "")
        self.query_one("#order-price-type", Select).value = order_price_type
        existing_valid_days_raw = order.get("validDays", STOPLOSS_ORDER_VALID_DAYS_DEFAULT)
        try:
            existing_valid_days = normalize_stoploss_order_valid_days(existing_valid_days_raw, "Order valid days")
        except Exception:
            existing_valid_days = STOPLOSS_ORDER_VALID_DAYS_DEFAULT
        safe_valid_days = 1 if existing_valid_days > 1 else existing_valid_days
        self.query_one("#order-valid-days", Input).value = str(safe_valid_days)
        if existing_valid_days > 1:
            self.write_log(
                "[yellow]Safety default applied:[/yellow] "
                f"edited stop-loss order-valid-days reset from {existing_valid_days} to 1."
            )
        self.query_one("#trigger-on-market-maker-quote", Switch).value = bool(trigger.get("triggerOnMarketMakerQuote", False))
        self.query_one("#short-selling-allowed", Switch).value = bool(order.get("shortSellingAllowed", False))
        self.query_one("#place-confirm", Input).value = ""
        self.query_one("#place-live", Button).label = "Update Paper Stop-Loss" if self.paper_mode_enabled else "Update Live Stop-Loss"
        self.query_one("#stoploss-modal").display = True
        self.write_log(f"Editing stop-loss for {orderbook.get('name', order_book_id)}.")

    def refresh_accounts(self) -> None:
        avanza = self.require_connection()
        overview = avanza.get_overview()
        if not isinstance(overview, dict):
            self.write_log(f"[yellow]Unexpected account overview response type:[/yellow] {type(overview).__name__}")
            return
        self.apply_accounts_overview(overview, announce=True)

    def _refresh_portfolio_impl(self) -> None:
        avanza = self.require_connection()
        data = avanza.get_accounts_positions()
        if not isinstance(data, dict):
            self.write_log(f"[yellow]Unexpected portfolio response type:[/yellow] {type(data).__name__}")
            return
        self.apply_portfolio_data(data, fetch_quotes=True, allow_status_lookup=False)

    def refresh_portfolio(self) -> None:
        self.run_profiled("refresh_portfolio", self._refresh_portfolio_impl)

    def action_refresh_stoplosses(self) -> None:
        try:
            self.refresh_stoplosses()
        except Exception as exc:
            self.write_log(f"[red]Refresh failed:[/red] {exc}")

    def action_refresh_portfolio(self) -> None:
        try:
            self.refresh_portfolio()
        except Exception as exc:
            self.write_log(f"[red]Portfolio refresh failed:[/red] {exc}")

    def tenant_session_label(self, session_id: str | None) -> str:
        token = str(session_id or "").strip()
        if not token:
            return "active session"
        context = self.tenant_sessions.get(token)
        return context.label if context else token

    def mark_tenant_session_auth_expired(self, session_id: str | None, exc: Exception) -> None:
        token = str(session_id or "").strip()
        if not token:
            self.write_log(f"[red]Live refresh failed:[/red] {exc}")
            return
        if token not in self.tenant_sessions:
            return
        self.live_refresh_auth_blocked_sessions.add(token)
        context = self.tenant_sessions.get(token)
        if context is not None:
            context.auth_valid = False
            context.auth_error = str(exc)
        now = time.monotonic()
        last_notice = self.live_refresh_auth_last_notice_at.get(token, 0.0)
        if now - last_notice >= AUTH_ERROR_LOG_THROTTLE_SECONDS:
            self.live_refresh_auth_last_notice_at[token] = now
            label = self.tenant_session_label(token)
            self.write_log(
                f"[yellow]Session auth expired:[/yellow] {label}. "
                "Live refresh paused for this session. Re-login it via [bold]Extra Account Login[/bold]."
            )
        self.record_event(
            "app",
            "session_auth_expired",
            {
                "session_id": token,
                "session_label": self.tenant_session_label(token),
                "error": str(exc),
            },
        )
        self.refresh_session_select_options()
        self.update_session_auth_badge()

    def mark_tenant_session_auth_ok(self, session_id: str | None) -> None:
        token = str(session_id or "").strip()
        if not token:
            return
        if token not in self.tenant_sessions:
            return
        self.live_refresh_auth_blocked_sessions.discard(token)
        context = self.tenant_sessions.get(token)
        if context is not None:
            context.auth_valid = True
            context.auth_error = ""
        self.refresh_session_select_options()
        self.update_session_auth_badge()

    def _refresh_selected_account_live_worker(self) -> None:
        started = time.perf_counter()
        if self.mcp_scope_depth > 0:
            self.live_refresh_deferred_by_mcp_scope = True
            self._finish_live_refresh_cycle()
            return
        active_session_id = self.active_session_id
        if not self.avanza or not self.selected_account_id:
            self._finish_live_refresh_cycle()
            return

        try:
            avanza = self.require_connection()
            selected_account_id = self.selected_account_id
            data = avanza.get_accounts_positions()
            if not isinstance(data, dict):
                raise RuntimeError(f"Unexpected portfolio response type: {type(data).__name__}")
            quote_payloads, realtime_statuses = self.prefetch_quote_and_status_by_order_book(
                data,
                selected_account_id,
                allow_status_lookup=False,
            )
            stoplosses = avanza.get_all_stop_losses()
            try:
                orders = avanza.get_orders()
            except Exception:
                orders = []
            elapsed = time.perf_counter() - started
            if not self.safe_call_from_thread(
                self._apply_live_refresh_payload,
                data,
                quote_payloads,
                realtime_statuses,
                stoplosses,
                orders,
                elapsed,
                active_session_id,
            ):
                self._finish_live_refresh_cycle()
        except Exception as exc:
            if is_unauthorized_http_error(exc):
                self.safe_call_from_thread(self.mark_tenant_session_auth_expired, active_session_id, exc)
            else:
                self.safe_call_from_thread(self.write_log, f"[red]Live refresh failed:[/red] {exc}")
            self._finish_live_refresh_cycle()

    def _apply_live_refresh_payload(
        self,
        data: dict[str, Any],
        quote_payloads: dict[str, dict[str, Any] | None],
        realtime_statuses: dict[str, str],
        stoplosses: Any,
        orders: Any,
        elapsed: float,
        session_id: str | None,
    ) -> None:
        if self.mcp_scope_depth > 0:
            self.live_refresh_deferred_by_mcp_scope = True
            self._finish_live_refresh_cycle()
            return
        if session_id and self.active_session_id and session_id != self.active_session_id:
            self._finish_live_refresh_cycle()
            return
        if session_id:
            self.update_tenant_session_data_cache(session_id, None, data, stoplosses, orders)
        self.mark_tenant_session_auth_ok(session_id)
        self.apply_portfolio_data(
            data,
            fetch_quotes=True,
            quote_payloads=quote_payloads,
            realtime_statuses=realtime_statuses,
            allow_status_lookup=False,
        )
        self.apply_stoploss_orders_data(stoplosses, orders)
        if self.debug_mode:
            self.debug_log(f"refresh_selected_account_live(background): {elapsed:.3f}s")
        self._finish_live_refresh_cycle()

    def _finish_live_refresh_cycle(self) -> None:
        with self.live_refresh_lock:
            had_pending = self.live_refresh_pending
            self.live_refresh_inflight = False
            self.live_refresh_pending = False
        if had_pending and not self.shutdown_event.is_set():
            self.refresh_selected_account_live()

    def update_tenant_session_accounts(self, session_id: str, accounts: list[dict[str, Any]]) -> None:
        token = str(session_id or "").strip()
        context = self.tenant_sessions.get(token)
        if context is None:
            return
        context.accounts = list(accounts)
        selected = str(context.selected_account_id or "").strip()
        if selected and any(str(item.get("id", "")) == selected for item in context.accounts):
            return
        default = default_account(context.accounts)
        context.selected_account_id = str(default.get("id", "")) if default else None
        self.refresh_session_select_options()
        self.update_session_auth_badge()

    def _background_session_heartbeat_worker(self) -> None:
        try:
            active_session_id = str(self.active_session_id or "").strip()
            session_snapshot = list(self.tenant_sessions.items())
            for session_id, context in session_snapshot:
                if self.shutdown_event.is_set():
                    break
                if session_id == active_session_id:
                    continue
                try:
                    overview = context.avanza.get_overview()
                    portfolio = context.avanza.get_accounts_positions()
                    stoplosses = context.avanza.get_all_stop_losses()
                    try:
                        orders = context.avanza.get_orders()
                    except Exception as exc:
                        self.safe_call_from_thread(
                            self.debug_log,
                            f"background_session_refresh({session_id}) orders fetch failed: {exc}",
                        )
                        orders = []
                except Exception as exc:
                    if is_unauthorized_http_error(exc):
                        self.safe_call_from_thread(self.mark_tenant_session_auth_expired, session_id, exc)
                    else:
                        self.safe_call_from_thread(
                            self.debug_log,
                            f"background_session_refresh({session_id}) failed: {exc}",
                        )
                    continue
                if isinstance(portfolio, dict):
                    self.safe_call_from_thread(
                        self.update_tenant_session_data_cache,
                        session_id,
                        overview if isinstance(overview, dict) else None,
                        portfolio,
                        stoplosses,
                        orders,
                    )
        finally:
            with self.background_session_heartbeat_lock:
                self.background_session_heartbeat_inflight = False

    def refresh_background_sessions(self) -> None:
        if self.shutdown_event.is_set():
            return
        if len(self.tenant_sessions) < 2:
            return
        with self.background_session_heartbeat_lock:
            if self.background_session_heartbeat_inflight:
                return
            self.background_session_heartbeat_inflight = True
        self.background_session_heartbeat_thread = threading.Thread(
            target=self._background_session_heartbeat_worker,
            daemon=True,
            name="avanza-background-session-heartbeat",
        )
        self.background_session_heartbeat_thread.start()

    def start_background_session_heartbeat(self) -> None:
        if self.background_session_heartbeat_timer is None:
            self.background_session_heartbeat_timer = self.set_interval(
                BACKGROUND_SESSION_HEARTBEAT_SECONDS,
                self.refresh_background_sessions,
                pause=False,
            )

    def refresh_selected_account_live(self) -> None:
        if self.shutdown_event.is_set():
            return
        if self.mcp_scope_depth > 0:
            self.live_refresh_deferred_by_mcp_scope = True
            return
        if not self.avanza or not self.selected_account_id:
            return
        active_session_id = str(self.active_session_id or "").strip()
        if active_session_id and active_session_id in self.live_refresh_auth_blocked_sessions:
            now = time.monotonic()
            last_notice = self.live_refresh_auth_last_notice_at.get(active_session_id, 0.0)
            if now - last_notice >= AUTH_ERROR_LOG_THROTTLE_SECONDS:
                self.live_refresh_auth_last_notice_at[active_session_id] = now
                label = self.tenant_session_label(active_session_id)
                self.write_log(
                    f"[yellow]Live refresh still paused:[/yellow] {label} is unauthorized. "
                    "Re-login it via [bold]Extra Account Login[/bold]."
                )
            return
        with self.live_refresh_lock:
            if self.live_refresh_inflight:
                self.live_refresh_pending = True
                return
            self.live_refresh_inflight = True
        self.live_refresh_thread = threading.Thread(
            target=self._refresh_selected_account_live_worker,
            daemon=True,
            name="avanza-live-refresh",
        )
        self.live_refresh_thread.start()

    def start_live_refresh(self) -> None:
        if self.live_refresh_timer is None:
            self.live_refresh_timer = self.set_interval(
                LIVE_REFRESH_SECONDS,
                self.refresh_selected_account_live,
                pause=False,
            )
            self.write_log(f"Live refresh enabled every {LIVE_REFRESH_SECONDS:g}s.")

    def account_by_id(self, account_id: str) -> dict[str, Any] | None:
        for account in self.accounts:
            if str(account.get("id", "")) == account_id:
                return account
        return None

    def select_account(self, account_id: str) -> None:
        account = self.account_by_id(account_id)
        if not account:
            raise ValueError(f"Unknown account id: {account_id}")
        self.set_selected_account(account)
        self.position_row_cache = {}
        self.apply_cached_account_snapshot(account_id)
        self.refresh_selected_account_live()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "session-select" and not self.is_blank_select_value(event.value):
            if self.session_select_updating:
                return
            next_session_id = str(event.value)
            if next_session_id == self.active_session_id:
                return
            try:
                self.activate_tenant_session(next_session_id)
            except Exception as exc:
                self.write_log(f"[red]Session switch failed:[/red] {exc}")
        elif event.select.id == "account-select" and not self.is_blank_select_value(event.value):
            if self.account_select_updating:
                return
            next_account_id = str(event.value)
            if next_account_id == self.selected_account_id:
                return
            if self.account_by_id(next_account_id) is None:
                self.debug_log(
                    f"Ignored stale account-select value {next_account_id!r} for session {self.active_session_id!r}."
                )
                self.restore_account_select_to_current()
                return
            try:
                self.select_account(next_account_id)
            except Exception as exc:
                self.write_log(f"[red]Account switch failed:[/red] {exc}")
        elif event.select.id == "instrument-select" and not self.is_blank_select_value(event.value):
            try:
                volume_input = self.query_one("#volume", Input)
            except Exception:
                return
            if not volume_input.value.strip():
                volume_input.value = self.holding_volumes_by_order_book.get(str(event.value), "")
        elif event.select.id == "order-instrument-select" and not self.is_blank_select_value(event.value):
            try:
                volume_input = self.query_one("#regular-order-volume", Input)
            except Exception:
                return
            if self.input_value("regular-order-type") == "sell" and not volume_input.value.strip():
                volume_input.value = self.holding_volumes_by_order_book.get(str(event.value), "")
            self.update_regular_order_value()
        elif event.select.id == "tv-lists-select":
            if self.tv_lists_select_updating:
                return
            if not self.is_blank_select_value(event.value):
                if str(event.value) == self.tv_lists_loaded_value:
                    return
                self.refresh_tv_lists(str(event.value))
        elif event.select.id == "regular-order-type":
            if str(event.value) == "sell":
                order_book_id = self.input_value("order-instrument-select")
                volume_input = self.query_one("#regular-order-volume", Input)
                if order_book_id and not volume_input.value.strip():
                    volume_input.value = self.holding_volumes_by_order_book.get(order_book_id, "")
            self.update_regular_order_value()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id in {"regular-order-volume", "regular-order-price"}:
            self.update_regular_order_value()
            return
        if event.input.id != "order-search":
            return
        query = event.value.strip()
        self.stop_order_search_timer()
        if len(query) < 2:
            self.query_one("#order-search-status", Static).update("Type at least 2 characters to search stocks.")
            if not query:
                self.restore_order_holding_options()
            return
        self.query_one("#order-search-status", Static).update(f"Searching '{query}'...")
        self.order_search_timer = self.set_timer(0.35, self.handle_order_search_from_timer)

    def on_switch_changed(self, event: Switch.Changed) -> None:
        # No action required for switch state changes; values are read on submit.
        return

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        try:
            if button_id == "login":
                self.handle_login()
            elif button_id == "onepassword-login":
                self.handle_1password_login()
            elif button_id == "open-extra-login":
                self.open_extra_login_modal()
            elif button_id == "logout-selected-session":
                self.logout_selected_session()
            elif button_id == "refresh-selected-session":
                self.handle_refresh_selected_session()
            elif button_id in {"extra-login-cancel", "extra-login-cancel-2"}:
                self.close_extra_login_modal()
            elif button_id == "extra-login-submit":
                self.handle_extra_login()
            elif button_id == "extra-onepassword-login":
                self.handle_extra_1password_login()
            elif button_id == "profit-cycle":
                self.cycle_profit_metric()
            elif button_id == "paper-mode-toggle":
                self.paper_mode_enabled = not self.paper_mode_enabled
                self.update_paper_mode_ui()
                mode = "paper" if self.paper_mode_enabled else "live"
                self.write_log(f"Order placement mode: {mode}.")
                self.record_event("trading", "paper_mode_changed", {"enabled": self.paper_mode_enabled})
            elif button_id == "mcp-toggle":
                if self.mcp_server is None:
                    self.start_mcp_bridge()
                else:
                    self.stop_mcp_bridge()
                self.update_mode_toggles()
            elif button_id == "mcp-write-toggle":
                self.mcp_write_enabled = not self.mcp_write_enabled
                if not self.mcp_write_enabled:
                    self.live_trading_allowed_for_session = False
                self.update_mcp_session_file()
                self.update_mode_toggles()
                mode = "read/write" if self.mcp_write_enabled else "read-only"
                self.write_mcp_log(f"MCP mode: {mode}.")
            elif button_id == "refresh-all":
                self.refresh_accounts()
                self.refresh_portfolio()
                self.refresh_stoplosses()
            elif button_id == "reload-tui":
                self.write_log("[yellow]Reloading TUI process to apply latest code changes...[/yellow]")
                self.exit({"reload_tui": True})
            elif button_id in {"refresh", "refresh-account"}:
                self.refresh_portfolio()
                self.refresh_stoplosses()
            elif button_id == "open-stoploss-modal":
                self.reset_stoploss_modal_for_new()
                self.query_one("#stoploss-modal").display = True
            elif button_id == "edit-stoploss":
                self.open_stoploss_edit_modal()
            elif button_id == "open-order-modal":
                self.query_one("#order-modal").display = True
            elif button_id == "open-orders-overlay":
                self.open_orders_overlay()
            elif button_id == "open-transactions-overlay":
                self.open_transactions_overlay()
            elif button_id == "open-tv-lists-overlay":
                self.open_tv_lists_overlay()
            elif button_id == "close-stoploss-modal":
                self.reset_stoploss_modal_for_new()
                self.query_one("#stoploss-modal").display = False
            elif button_id == "close-order-modal":
                self.query_one("#order-modal").display = False
            elif button_id == "close-orders-overlay":
                self.close_orders_overlay()
            elif button_id == "refresh-orders-overlay":
                self.refresh_orders_overlay()
            elif button_id == "close-transactions-overlay":
                self.close_transactions_overlay()
            elif button_id == "refresh-transactions-overlay":
                self.refresh_transactions_overlay()
            elif button_id == "close-tv-lists-overlay":
                self.close_tv_lists_overlay()
            elif button_id in {"refresh-tv-lists-overlay", "reload-tv-lists"}:
                self.refresh_tv_lists()
            elif button_id == "close-cancel-modal":
                self.close_cancel_modal()
            elif button_id == "clear-log":
                self.query_one("#log", RichLog).clear()
            elif button_id == "dry-run":
                self.handle_dry_run()
            elif button_id == "place-live":
                self.handle_place_live()
            elif button_id == "order-search-button":
                self.handle_order_search()
            elif button_id == "order-dry-run":
                self.handle_order_dry_run()
            elif button_id == "order-place-live":
                self.handle_order_place_live()
            elif button_id == "cancel-review":
                self.handle_cancel_review()
            elif button_id == "cancel-confirm-button":
                self.handle_cancel_confirm()
        except Exception as exc:
            self.write_log(f"[red]Error:[/red] {exc}")

    def open_extra_login_modal(self, *, refresh_session_id: str | None = None) -> None:
        if not self.tenant_sessions:
            raise ValueError("Log in to your first account first.")
        refresh_token = str(refresh_session_id or "").strip()
        refresh_context = self.tenant_sessions.get(refresh_token) if refresh_token else None
        self.query_one("#extra-login-modal").display = True
        self.query_one("#extra-login-title", Static).update(
            "Refresh selected session login" if refresh_context is not None else "Login to extra accounts"
        )
        self.query_one("#extra-login-subtitle", Static).update(
            (
                f"Re-authenticate {refresh_context.label} to restore MCP/TUI access."
                if refresh_context is not None
                else "Add another tenant session without leaving the TUI."
            )
        )
        self.query_one("#extra-session-label", Input).value = refresh_context.label if refresh_context is not None else ""
        self.query_one("#extra-username", Input).value = ""
        self.clear_extra_secret_inputs()
        self.query_one("#extra-onepassword-item", Input).value = ""
        self.query_one("#extra-onepassword-vault", Input).value = ""
        self.login_target_session_id = refresh_context.session_id if refresh_context is not None else None

    def close_extra_login_modal(self) -> None:
        self.query_one("#extra-login-modal").display = False
        self.query_one("#extra-login-title", Static).update("Login to extra accounts")
        self.query_one("#extra-login-subtitle", Static).update("Add another tenant session without leaving the TUI.")
        self.query_one("#extra-session-label", Input).value = ""
        self.query_one("#extra-username", Input).value = ""
        self.query_one("#extra-onepassword-item", Input).value = ""
        self.query_one("#extra-onepassword-vault", Input).value = ""
        self.clear_extra_secret_inputs()
        self.login_target_session_id = None

    def extra_session_label(self) -> str:
        return str(self.input_value("extra-session-label") or "").strip() or self.auto_session_label([])

    def handle_refresh_selected_session(self) -> None:
        session_id = self.selected_session_id()
        if not session_id:
            raise ValueError("No session is selected.")
        _ = self.tenant_session_by_id(session_id)
        self.open_extra_login_modal(refresh_session_id=session_id)

    def handle_extra_login(self) -> None:
        username = self.input_value("extra-username")
        password = self.input_value("extra-password")
        totp = self.input_value("extra-totp")
        if not username or not password or not totp:
            raise ValueError("Username, password, and TOTP are required.")

        label = self.extra_session_label()
        self.write_log(f"Logging in extra session '{label}'...")
        self.start_login_worker(
            self.login_worker_with_credentials,
            (
                {"username": username, "password": password, "totpToken": totp},
            ),
            (
                "Connecting to Avanza...",
                "Loading account overview...",
                "Loading portfolio...",
                "Loading stop-losses and open orders...",
                "Building workspace...",
            ),
            "Connecting to Avanza...",
            target_mode="extra",
            session_label=label,
            session_id=self.login_target_session_id,
        )

    def handle_extra_1password_login(self) -> None:
        item = self.input_value("extra-onepassword-item")
        vault = self.input_value("extra-onepassword-vault") or None
        if not item:
            raise ValueError("1Password item name or ID is required.")
        label = self.extra_session_label()
        self.write_log(f"Requesting extra-session credentials from 1Password ({label})...")
        self.start_login_worker(
            self.login_worker_with_1password,
            (item, vault),
            (
                "Waiting for 1Password approval...",
                "Reading credentials from 1Password...",
                "Connecting to Avanza...",
                "Loading account overview...",
                "Loading portfolio...",
                "Loading stop-losses and open orders...",
                "Building workspace...",
            ),
            "Waiting for 1Password approval...",
            target_mode="extra",
            session_label=label,
            session_id=self.login_target_session_id,
        )

    def handle_login(self) -> None:
        username = self.input_value("username")
        password = self.input_value("password")
        totp = self.input_value("totp")
        if not username or not password or not totp:
            raise ValueError("Username, password, and TOTP are required.")

        self.write_log("Logging in...")
        self.start_login_worker(
            self.login_worker_with_credentials,
            (
                {"username": username, "password": password, "totpToken": totp},
            ),
            (
                "Connecting to Avanza...",
                "Loading account overview...",
                "Loading portfolio...",
                "Loading stop-losses and open orders...",
                "Building workspace...",
            ),
            "Connecting to Avanza...",
            target_mode="initial",
        )

    def handle_1password_login(self) -> None:
        item = self.input_value("onepassword-item")
        vault = self.input_value("onepassword-vault") or None
        if not item:
            raise ValueError("1Password item name or ID is required.")

        self.write_log("Requesting Avanza credentials from 1Password CLI...")
        self.start_login_worker(
            self.login_worker_with_1password,
            (item, vault),
            (
                "Waiting for 1Password approval...",
                "Reading credentials from 1Password...",
                "Connecting to Avanza...",
                "Loading account overview...",
                "Loading portfolio...",
                "Loading stop-losses and open orders...",
                "Building workspace...",
            ),
            "Waiting for 1Password approval...",
            target_mode="initial",
        )

    def start_login_worker(
        self,
        target: Callable[..., None],
        args: tuple[Any, ...],
        progress_messages: tuple[str, ...],
        initial_message: str,
        *,
        target_mode: str = "initial",
        session_label: str | None = None,
        session_id: str | None = None,
    ) -> None:
        if self.login_busy:
            self.write_log("[yellow]Login already in progress...[/yellow]")
            return
        self.login_target_mode = target_mode
        self.login_target_session_id = str(session_id or "").strip() or None
        self.login_target_session_label = session_label
        self.start_login_progress(progress_messages, initial_message)
        worker = threading.Thread(target=target, args=args, daemon=True, name="avanza-login-worker")
        self.login_thread = worker
        worker.start()

    def run_login_stage_call(self, stage_message: str, stage_index: int, callback: Callable[..., Any], *args: Any) -> Any:
        self.call_from_thread(self.set_login_stage, stage_message, stage_index)
        done = threading.Event()
        started = time.perf_counter()

        def pulse() -> None:
            while not done.wait(1.0):
                elapsed = int(time.perf_counter() - started)
                try:
                    self.call_from_thread(
                        self.set_login_stage,
                        f"{stage_message} ({elapsed}s)",
                        stage_index,
                    )
                except RuntimeError:
                    break

        pulse_thread = threading.Thread(target=pulse, daemon=True, name="avanza-login-stage-pulse")
        pulse_thread.start()
        try:
            return callback(*args)
        finally:
            done.set()
            pulse_thread.join(timeout=0.2)

    def login_worker_with_credentials(self, credentials: dict[str, str]) -> None:
        self.perform_login(credentials, connect_stage_index=0)

    def login_worker_with_1password(self, item: str, vault: str | None) -> None:
        credentials = self.run_login_stage_call(
            "Reading credentials from 1Password...",
            1,
            onepassword_credentials,
            item,
            vault,
        )
        self.perform_login(credentials, connect_stage_index=2)

    def perform_login(self, credentials: dict[str, str], connect_stage_index: int) -> None:
        try:
            avanza = self.run_login_stage_call("Connecting to Avanza...", connect_stage_index, Avanza, credentials)

            overview = self.run_login_stage_call(
                "Loading account overview...",
                connect_stage_index + 1,
                avanza.get_overview,
            )
            if not isinstance(overview, dict):
                raise RuntimeError(f"Unexpected account overview response type: {type(overview).__name__}")

            portfolio = self.run_login_stage_call(
                "Loading portfolio...",
                connect_stage_index + 2,
                avanza.get_accounts_positions,
            )
            if not isinstance(portfolio, dict):
                raise RuntimeError(f"Unexpected portfolio response type: {type(portfolio).__name__}")

            stoplosses = self.run_login_stage_call(
                "Loading stop-losses and open orders...",
                connect_stage_index + 3,
                avanza.get_all_stop_losses,
            )
            try:
                orders = avanza.get_orders()
            except Exception:
                orders = []

            self.call_from_thread(self.set_login_stage, "Building workspace...", connect_stage_index + 4)
            self.call_from_thread(
                self.complete_login,
                avanza,
                overview,
                portfolio,
                stoplosses,
                orders,
                self.login_target_mode,
                self.login_target_session_id,
                self.login_target_session_label,
            )
            self.call_from_thread(self.stop_login_progress)
        except Exception as exc:
            self.call_from_thread(self.stop_login_progress)
            self.call_from_thread(self.write_log, f"[red]Login failed:[/red] {exc}")

    def handle_dry_run(self) -> None:
        _, _, preview = self.build_stop_loss_request()
        self.apply_stoploss_valid_days_safety(preview, live=False)
        self.write_log("[yellow]Review-only stop-loss request. No paper or live order is created:[/yellow]")
        for line in stop_loss_request_log_lines(preview):
            self.write_log(line)

    def handle_order_dry_run(self) -> None:
        _, _, preview = self.build_regular_order_request()
        self.write_log("[yellow]Review-only buy/sell order request. No paper or live order is created:[/yellow]")
        for line in order_request_log_lines(preview):
            self.write_log(line)

    def cancel_summary_text(self, target: dict[str, str]) -> str:
        stock = f" {target['stock']}" if target.get("stock") else ""
        return f"{target.get('mode', '')} {target.get('kind', '')}{stock}\nAccount {target.get('account_id', '')}"

    def open_cancel_modal(self, target: dict[str, str]) -> None:
        self.pending_cancel_target = target
        self.query_one("#cancel-summary", Static).update(self.cancel_summary_text(target))
        self.query_one("#cancel-confirm", Input).value = ""
        button = self.query_one("#cancel-confirm-button", Button)
        if target.get("mode") == "Paper":
            self.query_one("#cancel-instructions", Static).update("Cancels the local paper order only. Avanza is not touched.")
            button.label = "Cancel Paper Order"
            button.variant = "warning"
        else:
            self.query_one("#cancel-instructions", Static).update('Type "CANCEL" to cancel this live Avanza order.')
            button.label = "Cancel Live Order"
            button.variant = "error"
        self.query_one("#cancel-modal").display = True

    def close_cancel_modal(self) -> None:
        self.query_one("#cancel-modal").display = False
        self.query_one("#cancel-confirm", Input).value = ""
        self.pending_cancel_target = None

    def handle_cancel_review(self) -> None:
        target = self.pending_cancel_target
        if not target:
            raise ValueError("Select an order to cancel first.")
        self.write_log("[yellow]Review-only cancel request. No order is cancelled:[/yellow]")
        self.write_log(self.cancel_summary_text(target).replace("[", "\\[").replace("]", "\\]"))

    def handle_cancel_confirm(self) -> None:
        target = self.pending_cancel_target
        if not target:
            raise ValueError("Select an order to cancel first.")
        identifier = target.get("id", "")
        if not identifier:
            raise ValueError("Selected order has no id.")

        if target.get("mode") == "Paper":
            paper_order = cancel_paper_order(self.paper_session, identifier)
            self.save_paper_state()
            self.record_event("trading", "paper_order_cancel_from_tui", {"order": paper_order})
            self.write_log(f"[green]Paper order cancelled:[/green] {identifier}")
            self.close_cancel_modal()
            return

        if self.input_value("cancel-confirm") != "CANCEL":
            raise ValueError('Type "CANCEL" before live cancellation.')
        account_id = target.get("account_id") or self.require_selected_account_id()
        avanza = self.require_connection()
        kind = target.get("kind", "")
        if kind == "Stop-loss":
            result = avanza.delete_stop_loss_order(account_id, identifier)
            event_name = "live_stoploss_cancel_from_tui"
        else:
            result = avanza.delete_order(account_id, identifier)
            event_name = "live_order_cancel_from_tui"
        self.record_event("trading", event_name, {"target": target, "result": result})
        self.write_log(f"[green]Live {kind.lower()} cancellation sent:[/green] {identifier}")
        self.close_cancel_modal()
        self.refresh_stoplosses()

    def restore_order_holding_options(self) -> None:
        if self.latest_portfolio_data is None:
            return
        holding_options = stoploss_holding_options(self.latest_portfolio_data, self.selected_account_id)
        select = self.query_one("#order-instrument-select", Select)
        previous_value = self.input_value("order-instrument-select")
        select.set_options(holding_options)
        values = {value for _, value in holding_options}
        if previous_value in values:
            select.value = previous_value
        elif holding_options:
            select.value = holding_options[0][1]
        self.order_search_labels_by_order_book = {}

    def stop_order_search_timer(self) -> None:
        if self.order_search_timer is not None:
            self.order_search_timer.stop()
            self.order_search_timer = None

    def handle_order_search_from_timer(self) -> None:
        self.stop_order_search_timer()
        try:
            self.handle_order_search(automatic=True)
        except Exception as exc:
            try:
                self.write_log(f"[yellow]Order search failed:[/yellow] {exc}")
            except Exception:
                pass

    def handle_order_search(self, automatic: bool = False) -> None:
        self.stop_order_search_timer()
        query = self.input_value("order-search")
        if len(query) < 2:
            self.query_one("#order-search-status", Static).update("Type at least 2 characters to search stocks.")
            raise ValueError("Type at least 2 characters to search stocks.")

        options: list[tuple[str, str]] = []
        labels_by_order_book: dict[str, str] = {}
        seen: set[str] = set()

        def add_option(label: str, order_book_id: str, stock_name: str | None = None) -> None:
            if not order_book_id or order_book_id in seen:
                return
            options.append((label, order_book_id))
            labels_by_order_book[order_book_id] = stock_name or label.split(" - owned", 1)[0]
            seen.add(order_book_id)

        if self.latest_portfolio_data is not None:
            for label, order_book_id in holding_search_options(self.latest_portfolio_data, self.selected_account_id, query):
                add_option(label, order_book_id)

        remote_error: Exception | None = None
        try:
            hits = flattened_search_hits(self.require_connection().search_for_stock(query, 20))
        except Exception as exc:
            remote_error = exc
            hits = []

        for hit in hits:
            order_book_id = search_hit_order_book_id(hit)
            add_option(search_hit_label(hit), order_book_id, str(hit.get("name") or ""))

        select = self.query_one("#order-instrument-select", Select)
        select.set_options(options)
        self.order_search_labels_by_order_book = labels_by_order_book
        if options:
            select.value = options[0][1]
            if remote_error is not None:
                self.query_one("#order-search-status", Static).update(
                    f"{len(options)} portfolio result(s). Remote search failed: {remote_error}"
                )
            else:
                self.query_one("#order-search-status", Static).update(f"{len(options)} result(s). First result selected.")
            if not automatic:
                self.write_log(f"Found {len(options)} stock/order book result(s) for '{query}'.")
        elif remote_error is not None:
            self.query_one("#order-search-status", Static).update(f"Search failed: {remote_error}")
            raise remote_error
        else:
            self.query_one("#order-search-status", Static).update(f"No stock/order book results for '{query}'.")
            if not automatic:
                self.write_log(f"[yellow]No stock/order book results for '{query}'.[/yellow]")

    def handle_place_live(self) -> None:
        is_edit = bool(self.pending_stoploss_edit_id)
        action_label = "updated" if is_edit else "created"
        if self.paper_mode_enabled:
            _, _, preview = self.build_stop_loss_request()
            warnings = self.apply_stoploss_valid_days_safety(preview, live=False)
            for warning in warnings:
                self.write_log(f"[yellow]Warning:[/yellow] {warning}")
            order_book_id = self.input_value("instrument-select")
            instrument = self.holding_labels_by_order_book.get(order_book_id, order_book_id)
            paper_order = create_paper_stop_loss_order(
                {
                    **preview,
                    "account_id": preview["account_id"],
                    "order_book_id": preview["order_book_id"],
                    "trigger_type": preview["stop_loss_trigger"]["type"],
                    "trigger_value": preview["stop_loss_trigger"]["value"],
                    "trigger_value_type": preview["stop_loss_trigger"]["value_type"],
                    "valid_until": preview["stop_loss_trigger"]["valid_until"],
                    "order_type": preview["stop_loss_order_event"]["type"],
                    "order_price": preview["stop_loss_order_event"]["price"],
                    "order_price_type": preview["stop_loss_order_event"]["price_type"],
                    "volume": preview["stop_loss_order_event"]["volume"],
                    "order_valid_days": preview["stop_loss_order_event"]["valid_days"],
                    "trigger_on_market_maker_quote": preview["stop_loss_trigger"]["trigger_on_market_maker_quote"],
                    "short_selling_allowed": preview["stop_loss_order_event"]["short_selling_allowed"],
                },
                instrument=str(instrument),
            )
            self.paper_session.setdefault("orders", []).append(paper_order)
            append_paper_event(self.paper_session, "paper_stoploss_set_from_tui", {"id": paper_order["id"], "request": paper_order["request"]})
            self.save_paper_state()
            self.record_event("trading", "paper_stoploss_set_from_tui", {"order": paper_order})
            self.write_log(f"[green]Paper stop-loss {action_label}:[/green] {paper_order['id']}")
            self.reset_stoploss_modal_for_new()
            self.query_one("#stoploss-modal").display = False
            return

        if self.input_value("place-confirm") != "PLACE":
            raise ValueError('Type "PLACE" in the confirmation field before live placement.')

        avanza = self.require_connection()
        trigger, order_event, preview = self.build_stop_loss_request()
        warnings = self.apply_stoploss_valid_days_safety(preview, live=True)
        for warning in warnings:
            self.write_log(f"[yellow]Warning:[/yellow] {warning}")
        self.write_log("[red]Placing live stop-loss request:[/red]")
        for line in stop_loss_request_log_lines(preview):
            self.write_log(line)

        if is_edit:
            edit_id = str(self.pending_stoploss_edit_id)
            account_id = self.require_selected_account_id()
            delete_result = avanza.delete_stop_loss_order(account_id, edit_id)
            result = avanza.place_stop_loss_order(
                parent_stop_loss_id="0",
                account_id=account_id,
                order_book_id=self.input_value("instrument-select"),
                stop_loss_trigger=trigger,
                stop_loss_order_event=order_event,
            )
            self.record_event(
                "trading",
                "live_stoploss_replace_from_tui",
                {"stop_loss_id": edit_id, "delete_result": delete_result, "request": preview, "result": result},
            )
        else:
            result = avanza.place_stop_loss_order(
                parent_stop_loss_id="0",
                account_id=self.require_selected_account_id(),
                order_book_id=self.input_value("instrument-select"),
                stop_loss_trigger=trigger,
                stop_loss_order_event=order_event,
            )
            self.record_event("trading", "live_stoploss_set_from_tui", {"request": preview, "result": result})
        if isinstance(result, dict):
            status = result.get("status") or result.get("orderRequestStatus") or "response received"
            identifier = result.get("stoplossOrderId") or result.get("orderId") or ""
            suffix = f" ({identifier})" if identifier else ""
            self.write_log(f"[green]Avanza status:[/green] {status}{suffix}")
        else:
            self.write_log("[green]Avanza accepted the request.[/green]")
        self.reset_stoploss_modal_for_new()
        self.query_one("#stoploss-modal").display = False
        self.refresh_stoplosses()

    def handle_order_place_live(self) -> None:
        order_type, condition, preview = self.build_regular_order_request()
        order_book_id = self.input_value("order-instrument-select")
        instrument = (
            self.order_search_labels_by_order_book.get(order_book_id)
            or self.holding_labels_by_order_book.get(order_book_id, order_book_id)
        )

        if self.paper_mode_enabled:
            paper_order = create_paper_order(preview, instrument=instrument)
            self.paper_session.setdefault("orders", []).append(paper_order)
            append_paper_event(self.paper_session, "paper_order_set_from_tui", {"id": paper_order["id"], "request": paper_order["request"]})
            self.save_paper_state()
            self.record_event("trading", "paper_order_set_from_tui", {"order": paper_order})
            self.write_log(f"[green]Paper order created:[/green] {paper_order['id']}")
            self.query_one("#order-modal").display = False
            return

        if self.input_value("regular-order-confirm") != "PLACE":
            raise ValueError('Type "PLACE" in the confirmation field before live placement.')

        avanza = self.require_connection()
        self.write_log("[red]Placing live buy/sell order request:[/red]")
        for line in order_request_log_lines(preview):
            self.write_log(line)

        result = avanza.place_order(
            account_id=preview["account_id"],
            order_book_id=preview["order_book_id"],
            order_type=order_type,
            price=preview["price"],
            valid_until=date.fromisoformat(preview["valid_until"]),
            volume=preview["volume"],
            condition=condition,
        )
        self.record_event("trading", "live_order_set_from_tui", {"request": preview, "result": result})
        if isinstance(result, dict):
            status = result.get("orderRequestStatus") or result.get("status") or "response received"
            identifier = result.get("orderId") or ""
            suffix = f" ({identifier})" if identifier else ""
            self.write_log(f"[green]Avanza status:[/green] {status}{suffix}")
        else:
            self.write_log("[green]Avanza accepted the order request.[/green]")
        self.query_one("#order-modal").display = False
        self.refresh_stoplosses()


def load_mcp_session(path: Path | None = None) -> dict[str, Any]:
    path = path or MCP_SESSION_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"MCP session file not found: {path}. Enable MCP mode in the TUI first.") from exc
    if not isinstance(data, dict) or not data.get("url"):
        raise RuntimeError(f"Invalid MCP session file: {path}")
    token = str(data.get("token", "") or "").strip()
    if not token:
        storage = str(data.get("storage", "") or "").strip().lower()
        if storage == "keychain":
            token = mcp_keychain_get_token(path)
    if not token:
        raise RuntimeError(f"Invalid MCP session file: {path}")
    data = dict(data)
    data["token"] = token
    return data


def call_mcp_bridge(session: dict[str, Any], tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    url = str(session["url"]).rstrip("/") + "/call"
    body = json.dumps({"tool": tool, "arguments": arguments}).encode("utf-8")
    request = Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {session['token']}",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body or "{}")
        except json.JSONDecodeError:
            payload = {"error": body or f"HTTP {exc.code}"}
        payload.setdefault("ok", False)
        payload.setdefault("error", f"HTTP {exc.code}")
    except URLError as exc:
        raise RuntimeError(f"Could not reach TUI MCP bridge at {url}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("MCP bridge returned a non-object response.")
    return payload


def mcp_tool_response(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, indent=2, ensure_ascii=False, default=str),
            }
        ],
        "isError": not bool(payload.get("ok", True)),
    }


def read_mcp_message(stream: Any) -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = stream.readline()
        if line == b"":
            return None
        if line in (b"\r\n", b"\n"):
            break
        key, _, value = line.decode("utf-8").partition(":")
        headers[key.lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    return json.loads(stream.read(length).decode("utf-8"))


def write_mcp_message(stream: Any, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    stream.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
    stream.flush()


def mcp_success(message_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def mcp_error(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def run_mcp_stdio_proxy(session_file: Path | None = None) -> None:
    input_stream = sys.stdin.buffer
    output_stream = sys.stdout.buffer

    while True:
        message = read_mcp_message(input_stream)
        if message is None:
            return
        method = message.get("method")
        message_id = message.get("id")
        params = message.get("params") or {}
        if message_id is None and str(method).startswith("notifications/"):
            continue

        try:
            if method == "initialize":
                write_mcp_message(
                    output_stream,
                    mcp_success(
                        message_id,
                        {
                            "protocolVersion": MCP_PROTOCOL_VERSION,
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "avanza_cli", "version": APP_VERSION},
                        },
                    ),
                )
            elif method == "notifications/initialized":
                continue
            elif method == "ping":
                write_mcp_message(output_stream, mcp_success(message_id, {}))
            elif method == "tools/list":
                write_mcp_message(output_stream, mcp_success(message_id, {"tools": mcp_tools_catalog()}))
            elif method == "tools/call":
                tool_name = str(params.get("name", ""))
                arguments = params.get("arguments") or {}
                if not isinstance(arguments, dict):
                    raise ValueError("arguments must be an object.")
                session = load_mcp_session(session_file)
                payload = call_mcp_bridge(session, tool_name, arguments)
                write_mcp_message(output_stream, mcp_success(message_id, mcp_tool_response(payload)))
            else:
                write_mcp_message(output_stream, mcp_error(message_id, -32601, f"Unknown method: {method}"))
        except Exception as exc:
            write_mcp_message(output_stream, mcp_error(message_id, -32000, str(exc)))


def cmd_tui(args: argparse.Namespace) -> None:
    result = AvanzaTradingTui(
        debug=bool(getattr(args, "debug", False)),
        debug_profile_top=int(getattr(args, "debug_profile_top", DEBUG_PROFILE_TOP_DEFAULT)),
    ).run()
    if isinstance(result, dict) and bool(result.get("reload_tui")):
        os.execv(sys.executable, [sys.executable, *sys.argv])


def cmd_mcp(args: argparse.Namespace) -> None:
    run_mcp_stdio_proxy(Path(args.session_file))


def cmd_accounts(args: argparse.Namespace) -> None:
    avanza = connect(args)
    render_accounts_overview(avanza.get_overview())


def cmd_portfolio_positions(args: argparse.Namespace) -> None:
    avanza = connect(args)
    render_portfolio_positions(avanza.get_accounts_positions())


def cmd_portfolio_summary(args: argparse.Namespace) -> None:
    avanza = connect(args)
    render_portfolio_summary(avanza.get_accounts_positions())


def cmd_search(args: argparse.Namespace) -> None:
    avanza = connect(args)
    render_search_results(avanza.search_for_stock(args.query, args.limit))


def cmd_stoploss_list(args: argparse.Namespace) -> None:
    avanza = connect(args)
    render_stoplosses(avanza.get_all_stop_losses())


def cmd_stoploss_delete(args: argparse.Namespace) -> None:
    if not args.confirm:
        render_message(
            "Dry Run",
            [
                "Add --confirm to delete this stop-loss order.",
                f"Account: {args.account_id}",
                f"Stop-loss ID: {args.stop_loss_id}",
            ],
        )
        return

    avanza = connect(args)
    result = avanza.delete_stop_loss_order(args.account_id, args.stop_loss_id)
    render_result("Delete Stop-Loss Result", {"deleted": True, "result": result})


def cmd_stoploss_set(args: argparse.Namespace) -> None:
    trigger, order_event, request_preview = build_stop_loss_preview(vars(args))
    metadata = merged_orderbook_metadata(
        {"orderbook_id": args.order_book_id},
        KNOWN_ORDERBOOK_METADATA.get(str(args.order_book_id), {}),
    )
    request_preview["warnings"] = stoploss_order_valid_days_warnings(order_event.valid_days, metadata)

    if not args.confirm:
        render_stop_loss_request(
            "Dry Run: add --confirm to place this stop-loss order.",
            request_preview,
        )
        return

    avanza = connect(args)
    live_metadata = stoploss_instrument_metadata(avanza, str(args.order_book_id), base=metadata)
    request_preview["warnings"] = enforce_live_stoploss_order_valid_days(
        order_event.valid_days,
        live_metadata,
        live=True,
    )
    result = avanza.place_stop_loss_order(
        parent_stop_loss_id=args.parent_stop_loss_id,
        account_id=args.account_id,
        order_book_id=args.order_book_id,
        stop_loss_trigger=trigger,
        stop_loss_order_event=order_event,
    )
    render_result("Place Stop-Loss Result", result)


def cmd_stoploss_edit(args: argparse.Namespace) -> None:
    trigger, order_event, request_preview = build_stop_loss_preview(vars(args))
    request_preview["stop_loss_id"] = args.stop_loss_id
    metadata = merged_orderbook_metadata(
        {"orderbook_id": args.order_book_id},
        KNOWN_ORDERBOOK_METADATA.get(str(args.order_book_id), {}),
    )
    request_preview["warnings"] = stoploss_order_valid_days_warnings(order_event.valid_days, metadata)

    if not args.confirm:
        render_message(
            "Dry Run: add --confirm to update this stop-loss (delete + place replacement).",
            [
                f"Existing stop-loss ID: {args.stop_loss_id}",
                *format_stop_loss_request(request_preview),
            ],
        )
        return

    avanza = connect(args)
    live_metadata = stoploss_instrument_metadata(avanza, str(args.order_book_id), base=metadata)
    request_preview["warnings"] = enforce_live_stoploss_order_valid_days(
        order_event.valid_days,
        live_metadata,
        live=True,
    )
    delete_result = avanza.delete_stop_loss_order(args.account_id, args.stop_loss_id)
    place_result = avanza.place_stop_loss_order(
        parent_stop_loss_id=args.parent_stop_loss_id,
        account_id=args.account_id,
        order_book_id=args.order_book_id,
        stop_loss_trigger=trigger,
        stop_loss_order_event=order_event,
    )
    render_result(
        "Update Stop-Loss Result",
        {"updated": True, "deleted": delete_result, "placed": place_result},
    )


def cmd_orders_list(args: argparse.Namespace) -> None:
    avanza = connect(args)
    render_orders(avanza.get_orders())


def cmd_transactions_list(args: argparse.Namespace) -> None:
    avanza = connect(args)
    if args.max_elements < 1:
        raise ValueError("--max-elements must be >= 1.")
    transaction_types = parse_transaction_types(args.types)
    transactions_from = None if args.all else args.transactions_from
    transactions_to = None if args.all else args.transactions_to
    if transactions_from and transactions_to and transactions_from > transactions_to:
        raise ValueError("--from cannot be after --to.")
    payload = avanza.get_transactions_details(
        transaction_details_types=transaction_types,
        transactions_from=transactions_from,
        transactions_to=transactions_to,
        isin=args.isin,
        max_elements=args.max_elements,
    )
    render_transactions_history(
        payload,
        account_id=args.account_id,
        executed_only=not args.include_non_executed,
    )


def cmd_order_delete(args: argparse.Namespace) -> None:
    if not args.confirm:
        render_message(
            "Dry Run",
            [
                "Add --confirm to delete this regular order.",
                f"Account: {args.account_id}",
                f"Order ID: {args.order_id}",
            ],
        )
        return

    avanza = connect(args)
    result = avanza.delete_order(args.account_id, args.order_id)
    render_result("Delete Order Result", {"deleted": True, "result": result})


def cmd_order_set(args: argparse.Namespace) -> None:
    order_type, condition, preview = build_order_preview(
        {
            "account_id": args.account_id,
            "order_book_id": args.order_book_id,
            "order_type": args.order_type,
            "price": args.price,
            "valid_until": args.valid_until,
            "volume": args.volume,
            "condition": args.condition,
        }
    )

    if not args.confirm:
        render_order_request(
            "Dry Run: add --confirm to place this buy/sell order.",
            preview,
        )
        return

    avanza = connect(args)
    result = avanza.place_order(
        account_id=args.account_id,
        order_book_id=args.order_book_id,
        order_type=order_type,
        price=args.price,
        valid_until=args.valid_until,
        volume=args.volume,
        condition=condition,
    )
    render_result("Place Order Result", result)


def add_common_auth(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--username",
        metavar="USER",
        help="Avanza username. If omitted, you are prompted interactively.",
    )
    parser.add_argument(
        "--onepassword-item",
        metavar="ITEM",
        help="Read Avanza username, password, and TOTP from a 1Password item via the op CLI.",
    )
    parser.add_argument(
        "--onepassword-vault",
        metavar="VAULT",
        help="Optional 1Password vault name or ID for --onepassword-item.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="avanza_cli.py",
        formatter_class=HELP_FORMATTER,
        description="Human-readable Avanza account, portfolio, search, order, and stop-loss tools.",
        epilog=textwrap.dedent(
            """\
            Common examples:
              python avanza_cli.py tui
              python avanza_cli.py accounts
              python avanza_cli.py portfolio summary
              python avanza_cli.py portfolio positions
              python avanza_cli.py search-stock "VOLV B"
              python avanza_cli.py transactions list
              python avanza_cli.py orders list
              python avanza_cli.py stoploss list

            Credentials:
              Password and current TOTP code are prompted interactively and masked.
              Or use --onepassword-item ITEM with the 1Password CLI.

            Safety:
              Mutating commands dry-run unless you pass --confirm.
            """
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{APP_NAME} {APP_VERSION}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    tui = subparsers.add_parser(
        "tui",
        formatter_class=HELP_FORMATTER,
        help="Launch the interactive Textual terminal UI.",
        description="Launch the interactive terminal UI for account switching, portfolio viewing, and stop-loss management.",
    )
    tui.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug profiling mode. Writes timing/profile logs under avanza-cli/logs/.",
    )
    tui.add_argument(
        "--debug-profile-top",
        metavar="N",
        type=int,
        default=DEBUG_PROFILE_TOP_DEFAULT,
        help=f"How many top functions to include per profile sample in --debug mode. Default: {DEBUG_PROFILE_TOP_DEFAULT}.",
    )
    tui.set_defaults(func=cmd_tui)

    mcp = subparsers.add_parser(
        "mcp",
        formatter_class=HELP_FORMATTER,
        help="Run the stdio MCP proxy for a TUI-managed authenticated session.",
        description=textwrap.dedent(
            """\
            Run a stdio MCP server proxy that forwards tool calls to the currently
            authenticated TUI MCP bridge. Start `python avanza_cli.py tui`, log in,
            enable MCP mode in the TUI, then configure Codex/desktop clients to run
            this command.
            """
        ),
        epilog=textwrap.dedent(
            """\
            Example MCP server command:
              python avanza_cli.py mcp
            """
        ),
    )
    mcp.add_argument(
        "--session-file",
        default=str(MCP_SESSION_FILE),
        help="Path to the TUI-written MCP session file. Default: .avanza_mcp_session.json next to avanza_cli.py.",
    )
    mcp.set_defaults(func=cmd_mcp)

    accounts = subparsers.add_parser(
        "accounts",
        formatter_class=HELP_FORMATTER,
        help="Show all accounts with balances and buying power.",
        description="Show all Avanza accounts in a readable table.",
        epilog="Example:\n  python avanza_cli.py accounts",
    )
    add_common_auth(accounts)
    accounts.set_defaults(func=cmd_accounts)

    portfolio = subparsers.add_parser(
        "portfolio",
        formatter_class=HELP_FORMATTER,
        help="View portfolio summaries and positions.",
        description="View portfolio data across accounts in readable terminal tables.",
        epilog=textwrap.dedent(
            """\
            Examples:
              python avanza_cli.py portfolio summary
              python avanza_cli.py portfolio positions
            """
        ),
    )
    portfolio_subparsers = portfolio.add_subparsers(dest="portfolio_command", required=True)

    portfolio_summary = portfolio_subparsers.add_parser(
        "summary",
        formatter_class=HELP_FORMATTER,
        help="Show position counts and cash balances.",
        description="Show portfolio position counts and cash positions.",
        epilog="Example:\n  python avanza_cli.py portfolio summary",
    )
    add_common_auth(portfolio_summary)
    portfolio_summary.set_defaults(func=cmd_portfolio_summary)

    portfolio_positions = portfolio_subparsers.add_parser(
        "positions",
        formatter_class=HELP_FORMATTER,
        help="Show instrument and cash positions.",
        description="Show all portfolio stock positions and cash balances in tables.",
        epilog="Example:\n  python avanza_cli.py portfolio positions",
    )
    add_common_auth(portfolio_positions)
    portfolio_positions.set_defaults(func=cmd_portfolio_positions)

    search = subparsers.add_parser(
        "search-stock",
        formatter_class=HELP_FORMATTER,
        help="Search stocks by name, ticker, or ISIN.",
        description="Search Avanza stocks and show matching order book ids.",
        epilog='Example:\n  python avanza_cli.py search-stock "VOLV B" --limit 5',
    )
    add_common_auth(search)
    search.add_argument("query", help="Name, ticker, or ISIN to search for.")
    search.add_argument(
        "--limit",
        metavar="N",
        type=int,
        default=10,
        help="Maximum number of search results to request. Default: 10.",
    )
    search.set_defaults(func=cmd_search)

    transactions = subparsers.add_parser(
        "transactions",
        formatter_class=HELP_FORMATTER,
        help="View transaction history / executed orders.",
        description="List transaction history. Defaults to executed orders (BUY/SELL).",
        epilog=textwrap.dedent(
            """\
            Examples:
              python avanza_cli.py transactions list
              python avanza_cli.py transactions list --account-id ACCOUNT_ID --max-elements 5000
              python avanza_cli.py transactions list --all
              python avanza_cli.py transactions list --types BUY,SELL,DIVIDEND --from 2026-01-01 --to 2026-05-01
            """
        ),
    )
    transactions_subparsers = transactions.add_subparsers(dest="transactions_command", required=True)

    transactions_list = transactions_subparsers.add_parser(
        "list",
        formatter_class=HELP_FORMATTER,
        help="List transaction history entries.",
        description="List transaction history entries with account/date/type filters.",
    )
    add_common_auth(transactions_list)
    transactions_list.add_argument("--account-id", metavar="ID", help="Optional Avanza account id filter.")
    transactions_list.add_argument(
        "--from",
        dest="transactions_from",
        metavar="YYYY-MM-DD",
        type=parse_date,
        help="Start date filter (inclusive).",
    )
    transactions_list.add_argument(
        "--to",
        dest="transactions_to",
        metavar="YYYY-MM-DD",
        type=parse_date,
        help="End date filter (inclusive).",
    )
    transactions_list.add_argument(
        "--types",
        metavar="CSV",
        default="BUY,SELL",
        help="Comma-separated transaction types. Default: BUY,SELL.",
    )
    transactions_list.add_argument("--isin", metavar="ISIN", help="Optional ISIN filter.")
    transactions_list.add_argument(
        "--max-elements",
        metavar="N",
        type=int,
        default=1000,
        help="Maximum number of transactions to request. Default: 1000.",
    )
    transactions_list.add_argument(
        "--include-non-executed",
        action="store_true",
        help="Include non-executed types (deposits/dividends/withdrawals) in output.",
    )
    transactions_list.add_argument(
        "--all",
        action="store_true",
        help="Request practically all available history by removing date filters.",
    )
    transactions_list.set_defaults(func=cmd_transactions_list)

    orders = subparsers.add_parser(
        "orders",
        formatter_class=HELP_FORMATTER,
        help="List, create, and delete regular buy/sell orders.",
        description="Manage regular Avanza buy/sell orders. Placement and deletion dry-run unless --confirm is passed.",
        epilog=textwrap.dedent(
            """\
            Examples:
              python avanza_cli.py orders list
              python avanza_cli.py orders set --help
              python avanza_cli.py orders delete --help
            """
        ),
    )
    orders_subparsers = orders.add_subparsers(dest="orders_command", required=True)

    orders_list = orders_subparsers.add_parser(
        "list",
        formatter_class=HELP_FORMATTER,
        help="List open regular orders.",
        description="List open regular buy/sell orders in a readable table.",
        epilog="Example:\n  python avanza_cli.py orders list",
    )
    add_common_auth(orders_list)
    orders_list.set_defaults(func=cmd_orders_list)

    orders_delete = orders_subparsers.add_parser(
        "delete",
        formatter_class=HELP_FORMATTER,
        help="Delete a regular order.",
        description="Delete a regular order. Without --confirm this only prints the intended deletion.",
        epilog=textwrap.dedent(
            """\
            Dry-run:
              python avanza_cli.py orders delete --account-id ACCOUNT_ID --order-id ORDER_ID

            Live deletion:
              python avanza_cli.py orders delete --account-id ACCOUNT_ID --order-id ORDER_ID --confirm
            """
        ),
    )
    add_common_auth(orders_delete)
    orders_delete.add_argument("--account-id", metavar="ID", required=True, help="Avanza account id that owns the order.")
    orders_delete.add_argument("--order-id", metavar="ID", required=True, help="Order id to delete.")
    orders_delete.add_argument("--confirm", action="store_true", help="Actually delete the order. Omit for dry-run.")
    orders_delete.set_defaults(func=cmd_order_delete)

    orders_set = orders_subparsers.add_parser(
        "set",
        formatter_class=HELP_FORMATTER,
        help="Create a regular buy/sell order.",
        description=textwrap.dedent(
            """\
            Create a regular buy/sell order.

            Without --confirm, this command prints a readable dry-run summary and does not log in.

            Conditions:
              normal         normal limit order
              fill-or-kill   fill entire order immediately or cancel
              fill-and-kill  fill available volume immediately and cancel remainder
            """
        ),
        epilog=textwrap.dedent(
            """\
            Buy order dry-run:
              python avanza_cli.py orders set \\
                --account-id ACCOUNT_ID \\
                --order-book-id ORDER_BOOK_ID \\
                --order-type buy \\
                --price 100 \\
                --valid-until 2026-05-28 \\
                --volume 10 \\
                --condition normal

            Add --confirm only after reviewing the dry-run summary.
            """
        ),
    )
    add_common_auth(orders_set)
    orders_set.add_argument("--account-id", metavar="ID", required=True, help="Avanza account id to place the order on.")
    orders_set.add_argument("--order-book-id", metavar="ID", required=True, help="Avanza order book id for the instrument.")
    orders_set.add_argument("--order-type", choices=ORDER_TYPE_CHOICES, default="buy", help="Order side. Default: buy.")
    orders_set.add_argument("--price", metavar="SEK", required=True, type=float, help="Limit price in SEK.")
    orders_set.add_argument("--valid-until", metavar="YYYY-MM-DD", required=True, type=parse_date, help="Last date the order remains valid.")
    orders_set.add_argument("--volume", metavar="QTY", required=True, type=int, help="Number of shares/contracts to order.")
    orders_set.add_argument("--condition", choices=ORDER_CONDITION_CHOICES, default="normal", help="Order condition. Default: normal.")
    orders_set.add_argument("--confirm", action="store_true", help="Actually place the order. Omit for dry-run.")
    orders_set.set_defaults(func=cmd_order_set)

    stoploss = subparsers.add_parser(
        "stoploss",
        formatter_class=HELP_FORMATTER,
        help="List, create, and delete stop-loss orders.",
        description="Manage Avanza stop-loss orders. Placement and deletion dry-run unless --confirm is passed.",
        epilog=textwrap.dedent(
            """\
            Examples:
              python avanza_cli.py stoploss list
              python avanza_cli.py stoploss set --help
              python avanza_cli.py stoploss delete --help
              python avanza_cli.py stoploss edit --help
            """
        ),
    )
    stoploss_subparsers = stoploss.add_subparsers(dest="stoploss_command", required=True)

    stoploss_list = stoploss_subparsers.add_parser(
        "list",
        formatter_class=HELP_FORMATTER,
        help="List open stop-loss orders.",
        description="List open stop-loss orders in a readable table.",
        epilog="Example:\n  python avanza_cli.py stoploss list",
    )
    add_common_auth(stoploss_list)
    stoploss_list.set_defaults(func=cmd_stoploss_list)

    stoploss_delete = stoploss_subparsers.add_parser(
        "delete",
        formatter_class=HELP_FORMATTER,
        help="Delete a stop-loss order.",
        description="Delete a stop-loss order. Without --confirm this only prints the intended deletion.",
        epilog=textwrap.dedent(
            """\
            Dry-run:
              python avanza_cli.py stoploss delete --account-id ACCOUNT_ID --stop-loss-id STOP_LOSS_ID

            Live deletion:
              python avanza_cli.py stoploss delete --account-id ACCOUNT_ID --stop-loss-id STOP_LOSS_ID --confirm
            """
        ),
    )
    add_common_auth(stoploss_delete)
    stoploss_delete.add_argument("--account-id", metavar="ID", required=True, help="Avanza account id that owns the stop-loss.")
    stoploss_delete.add_argument("--stop-loss-id", metavar="ID", required=True, help="Stop-loss id to delete.")
    stoploss_delete.add_argument("--confirm", action="store_true", help="Actually delete the stop-loss. Omit for dry-run.")
    stoploss_delete.set_defaults(func=cmd_stoploss_delete)

    stoploss_set = stoploss_subparsers.add_parser(
        "set",
        formatter_class=HELP_FORMATTER,
        help="Create a fixed or gliding stop-loss order.",
        description=textwrap.dedent(
            """\
            Create a stop-loss order.

            Without --confirm, this command prints a readable dry-run summary and does not log in.

            Trigger types:
              less-or-equal   fixed trigger at or below a price
              more-or-equal   fixed trigger at or above a price
              follow-upwards  gliding/trailing trigger for long positions
              follow-downwards gliding/trailing trigger for short/downward logic

            Price/value types:
              SEK             explicit currency value
              %               relative offset/value, interpreted by Avanza

            If --valid-until is omitted, avanza_cli auto-sets it to the longest allowed date.
            """
        ),
        epilog=textwrap.dedent(
            """\
            Gliding sell stop-loss dry-run:
              python avanza_cli.py stoploss set \\
                --account-id ACCOUNT_ID \\
                --order-book-id ORDER_BOOK_ID \\
                --trigger-type follow-upwards \\
                --trigger-value 5 \\
                --trigger-value-type % \\
                --order-type sell \\
                --order-price 1 \\
                --order-price-type % \\
                --volume 10

            Add --confirm only after reviewing the dry-run summary.
            """
        ),
    )
    add_common_auth(stoploss_set)
    stoploss_set.add_argument("--account-id", metavar="ID", required=True, help="Avanza account id to place the stop-loss on.")
    stoploss_set.add_argument("--order-book-id", metavar="ID", required=True, help="Avanza order book id for the instrument.")
    stoploss_set.add_argument("--parent-stop-loss-id", metavar="ID", default="0", help="Parent stop-loss id. Default: 0.")
    stoploss_set.add_argument("--trigger-type", choices=TRIGGER_TYPE_CHOICES, required=True, help="Stop-loss trigger behavior.")
    stoploss_set.add_argument("--trigger-value", metavar="VALUE", required=True, type=float, help="Trigger value, interpreted with --trigger-value-type.")
    stoploss_set.add_argument(
        "--trigger-value-type",
        metavar="{SEK,%}",
        type=parse_price_type,
        default="monetary",
        help="How to interpret --trigger-value. Use SEK or %%. Default: SEK.",
    )
    stoploss_set.add_argument(
        "--valid-until",
        metavar="YYYY-MM-DD",
        default=max_valid_until_date().isoformat(),
        type=parse_date,
        help=f"Last date the trigger remains valid. Default: max allowed ({VALID_UNTIL_MAX_DAYS} days from today).",
    )
    stoploss_set.add_argument("--trigger-on-market-maker-quote", action="store_true", help="Allow market-maker quote to trigger the stop-loss.")
    stoploss_set.add_argument("--order-type", choices=ORDER_TYPE_CHOICES, default="sell", help="Order side after trigger. Default: sell.")
    stoploss_set.add_argument("--order-price", metavar="VALUE", required=True, type=float, help="Order price or offset, interpreted with --order-price-type.")
    stoploss_set.add_argument(
        "--order-price-type",
        metavar="{SEK,%}",
        type=parse_price_type,
        default="monetary",
        help="How to interpret --order-price. Use SEK or %%. Default: SEK.",
    )
    stoploss_set.add_argument("--volume", metavar="QTY", required=True, type=float, help="Number of shares/contracts to include in the triggered order.")
    stoploss_set.add_argument(
        "--order-valid-days",
        metavar="DAYS",
        default=STOPLOSS_ORDER_VALID_DAYS_DEFAULT,
        type=int,
        help=f"Triggered order validity in days. Default: {STOPLOSS_ORDER_VALID_DAYS_DEFAULT}.",
    )
    stoploss_set.add_argument("--short-selling-allowed", action="store_true", help="Allow short selling for the triggered order.")
    stoploss_set.add_argument("--confirm", action="store_true", help="Actually place the stop-loss. Omit for dry-run.")
    stoploss_set.set_defaults(func=cmd_stoploss_set)

    stoploss_edit = stoploss_subparsers.add_parser(
        "edit",
        formatter_class=HELP_FORMATTER,
        help="Update an existing stop-loss (replace workflow).",
        description=textwrap.dedent(
            """\
            Update an existing stop-loss by deleting the old one and placing a replacement.

            This command uses the same trigger/order fields as `stoploss set`, plus --stop-loss-id.
            Without --confirm, it prints a dry-run summary.
            """
        ),
    )
    add_common_auth(stoploss_edit)
    stoploss_edit.add_argument("--stop-loss-id", metavar="ID", required=True, help="Existing stop-loss id to update.")
    stoploss_edit.add_argument("--account-id", metavar="ID", required=True, help="Avanza account id that owns the stop-loss.")
    stoploss_edit.add_argument("--order-book-id", metavar="ID", required=True, help="Avanza order book id for the instrument.")
    stoploss_edit.add_argument("--parent-stop-loss-id", metavar="ID", default="0", help="Parent stop-loss id. Default: 0.")
    stoploss_edit.add_argument("--trigger-type", choices=TRIGGER_TYPE_CHOICES, required=True, help="Stop-loss trigger behavior.")
    stoploss_edit.add_argument("--trigger-value", metavar="VALUE", required=True, type=float, help="Trigger value, interpreted with --trigger-value-type.")
    stoploss_edit.add_argument(
        "--trigger-value-type",
        metavar="{SEK,%}",
        type=parse_price_type,
        default="monetary",
        help="How to interpret --trigger-value. Use SEK or %%. Default: SEK.",
    )
    stoploss_edit.add_argument(
        "--valid-until",
        metavar="YYYY-MM-DD",
        default=max_valid_until_date().isoformat(),
        type=parse_date,
        help=f"Last date the trigger remains valid. Default: max allowed ({VALID_UNTIL_MAX_DAYS} days from today).",
    )
    stoploss_edit.add_argument("--trigger-on-market-maker-quote", action="store_true", help="Allow market-maker quote to trigger the stop-loss.")
    stoploss_edit.add_argument("--order-type", choices=ORDER_TYPE_CHOICES, default="sell", help="Order side after trigger. Default: sell.")
    stoploss_edit.add_argument("--order-price", metavar="VALUE", required=True, type=float, help="Order price or offset, interpreted with --order-price-type.")
    stoploss_edit.add_argument(
        "--order-price-type",
        metavar="{SEK,%}",
        type=parse_price_type,
        default="monetary",
        help="How to interpret --order-price. Use SEK or %%. Default: SEK.",
    )
    stoploss_edit.add_argument("--volume", metavar="QTY", required=True, type=float, help="Number of shares/contracts to include in the triggered order.")
    stoploss_edit.add_argument(
        "--order-valid-days",
        metavar="DAYS",
        default=STOPLOSS_ORDER_VALID_DAYS_DEFAULT,
        type=int,
        help=f"Triggered order validity in days. Default: {STOPLOSS_ORDER_VALID_DAYS_DEFAULT}.",
    )
    stoploss_edit.add_argument("--short-selling-allowed", action="store_true", help="Allow short selling for the triggered order.")
    stoploss_edit.add_argument("--confirm", action="store_true", help="Actually update the stop-loss (delete + place replacement).")
    stoploss_edit.set_defaults(func=cmd_stoploss_edit)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        args.func(args)
        return 0
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
