"""Application-wide constants, paths, and configuration tables.

All values here are import-time constants (plus the shared rich console).
Runtime session/paper/log file paths anchor to the repository root, i.e. the
directory containing the avanza_cli.py shim, matching the historical layout.
"""

import argparse
import os
import re
import tomllib
from pathlib import Path

from avanza.constants import TimePeriod, TransactionsDetailsType
from rich.console import Console

_REPO_ROOT = Path(__file__).resolve().parent.parent
SHIM_SCRIPT_NAME = "avanza_cli.py"

console = Console()
HELP_FORMATTER = argparse.RawDescriptionHelpFormatter
MCP_SESSION_FILE = _REPO_ROOT.joinpath(".avanza_mcp_session.json")
PAPER_SESSION_FILE = _REPO_ROOT.joinpath(".avanza_paper_session.json")
WEB_SESSION_FILE = _REPO_ROOT.joinpath(".avanza_web_session.json")
UI_LOCK_FILE = _REPO_ROOT.joinpath(".avanza_ui.lock")
WEB_DEFAULT_PORT = int(os.getenv("AVANZA_WEB_PORT", "8787"))
TRADINGVIEW_SESSION_FILE = _REPO_ROOT.joinpath(".avanza_tradingview_session.json")
TRADINGVIEW_BROWSER_PROFILE_DIR = _REPO_ROOT.joinpath(".avanza_tradingview_profile")
TRADINGVIEW_KEYCHAIN_SERVICE = "Avanza-MCP.TradingView"
MCP_KEYCHAIN_SERVICE = "Avanza-MCP.BridgeSession"
LOG_DIR = _REPO_ROOT.joinpath("avanza-cli") / "logs"
MCP_PROTOCOL_VERSION = "2024-11-05"
VALID_UNTIL_MAX_DAYS = int(os.getenv("AVANZA_VALID_UNTIL_MAX_DAYS", "90"))
STOPLOSS_ORDER_VALID_DAYS_DEFAULT = int(os.getenv("AVANZA_STOPLOSS_ORDER_VALID_DAYS_DEFAULT", "1"))

TRIGGER_TYPE_CHOICES = [
    "less-or-equal",
    "more-or-equal",
    "follow-upwards",
    "follow-downwards",
]
PRICE_TYPE_ALIASES = {
    "monetary": "monetary",
    "sek": "monetary",
    "currency": "monetary",
    "percentage": "percentage",
    "percent": "percentage",
    "%": "percentage",
}
PRICE_TYPE_SELECT_OPTIONS = [("SEK", "monetary"), ("%", "percentage")]
ORDER_TYPE_CHOICES = ["buy", "sell"]
ORDER_CONDITION_CHOICES = ["normal", "fill-or-kill", "fill-and-kill"]
TRANSACTION_TYPE_CHOICES = [item.value for item in TransactionsDetailsType]
ACCOUNT_PERFORMANCE_PERIOD_CHOICES = [
    "ONE_WEEK",
    "ONE_MONTH",
    "THREE_MONTHS",
    "YEAR_TO_DATE",
    "ONE_YEAR",
    "THREE_YEARS",
    "SINCE_START",
]
LIVE_REFRESH_SECONDS = 5.0
REALTIME_STATUS_REFRESH_SECONDS = 300.0
QUOTE_CACHE_SECONDS = 8.0
QUOTE_REFRESH_COALESCE_SECONDS = float(os.getenv("AVANZA_QUOTE_REFRESH_COALESCE_SECONDS", "1.0"))
MCP_HEALTH_CHECK_SECONDS = 5.0
BACKGROUND_SESSION_HEARTBEAT_SECONDS = float(os.getenv("AVANZA_BACKGROUND_SESSION_HEARTBEAT_SECONDS", "30"))
ORDERBOOK_METADATA_REFRESH_SECONDS = 1800.0
ACCOUNT_READ_CACHE_SECONDS = float(os.getenv("AVANZA_ACCOUNT_READ_CACHE_SECONDS", "2.0"))
AUTH_ERROR_LOG_THROTTLE_SECONDS = float(os.getenv("AVANZA_AUTH_ERROR_LOG_THROTTLE_SECONDS", "120"))
DEFAULT_COURTAGE_RATE_SE = float(os.getenv("AVANZA_DEFAULT_COURTAGE_RATE_SE", "0.0025"))
DEFAULT_COURTAGE_MIN_SEK = float(os.getenv("AVANZA_DEFAULT_COURTAGE_MIN_SEK", "1.0"))
DEFAULT_COURTAGE_RATE_US = float(os.getenv("AVANZA_DEFAULT_COURTAGE_RATE_US", "0.0025"))
DEFAULT_COURTAGE_MIN_USD = float(os.getenv("AVANZA_DEFAULT_COURTAGE_MIN_USD", "1.0"))
DEFAULT_FX_FEE_RATE = float(os.getenv("AVANZA_DEFAULT_FX_FEE_RATE", "0.0025"))
KNOWN_ORDERBOOK_METADATA: dict[str, dict[str, str]] = {
    "5269": {"name": "Volvo B", "ticker": "VOLV B", "market": "NASDAQ Stockholm", "currency": "SEK", "country_code": "SE", "instrument_type": "STOCK"},
    "5247": {"name": "Investor B", "ticker": "INVE B", "market": "NASDAQ Stockholm", "currency": "SEK", "country_code": "SE", "instrument_type": "STOCK"},
    "5401": {"name": "Saab B", "ticker": "SAAB B", "market": "NASDAQ Stockholm", "currency": "SEK", "country_code": "SE", "instrument_type": "STOCK"},
    "488235": {"name": "HANZA", "ticker": "HANZA", "market": "NASDAQ Stockholm", "currency": "SEK", "country_code": "SE", "instrument_type": "STOCK"},
    "804998": {"name": "Sivers Semiconductors", "ticker": "SIVE", "market": "NASDAQ Stockholm", "currency": "SEK", "country_code": "SE", "instrument_type": "STOCK"},
    "4478": {"name": "NVIDIA", "ticker": "NVDA", "market": "NASDAQ", "currency": "USD", "country_code": "US", "instrument_type": "STOCK"},
    "529720": {"name": "Advanced Micro Devices", "ticker": "AMD", "market": "NASDAQ", "currency": "USD", "country_code": "US", "instrument_type": "STOCK"},
    "1211627": {"name": "Coinbase Global, Inc. - Class A", "ticker": "COIN", "market": "NASDAQ", "currency": "USD", "country_code": "US", "instrument_type": "STOCK"},
    "1138439": {"name": "Palantir Technologies", "ticker": "PLTR", "market": "NYSE", "currency": "USD", "country_code": "US", "instrument_type": "STOCK"},
}
COUNTRY_CURRENCY_MAP = {
    "SE": "SEK",
    "US": "USD",
    "FI": "EUR",
    "DK": "DKK",
    "NO": "NOK",
    "GB": "GBP",
}
MARKET_CURRENCY_HINTS: tuple[tuple[str, str], ...] = (
    ("stockholm", "SEK"),
    ("xsto", "SEK"),
    ("first north", "SEK"),
    ("ngm", "SEK"),
    ("spotlight", "SEK"),
    ("nasdaq", "USD"),
    ("nyse american", "USD"),
    ("nyse", "USD"),
    ("helsinki", "EUR"),
    ("xhel", "EUR"),
    ("copenhagen", "DKK"),
    ("xcse", "DKK"),
    ("oslo", "NOK"),
    ("xosl", "NOK"),
    ("london", "GBP"),
    ("xlon", "GBP"),
)
LOGIN_PROGRESS_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
LOGIN_PROGRESS_ROTATE_TICKS = 10
DEBUG_PROFILE_TOP_DEFAULT = 25
CHANGED_CELL_STYLE = "#d7ba7d"
POSITIVE_CELL_STYLE = "#7fbf8f"
NEGATIVE_CELL_STYLE = "#d98f8f"
POSITIVE_PERCENT_STYLE = "#a9dcb8"
NEGATIVE_PERCENT_STYLE = "#ebb0b0"
BUY_SIDE_STYLE = "bold white on #1f6f43"
SELL_SIDE_STYLE = "bold white on #8f2438"
POSITION_CHANGE_COLUMNS = {2, 3, 4, 5, 6, 7, 8}
MIN_PANE_WEIGHT = 1
MAX_PANE_WEIGHT = 8
PANE_RESIZE_STEP = 0.10
MIN_ACTIVE_TRADES_WIDTH = 30
MAX_ACTIVE_TRADES_WIDTH = 110
MIN_TICKET_PANE_WIDTH = 52
MAX_TICKET_PANE_WIDTH = 110
PROFIT_METRIC_MODES = ("day", "week", "month", "year", "since_start", "total")
PAPER_ORDER_ACTIVE_STATES = {"ACTIVE", "PENDING"}
WINDOW_PERFORMANCE_KEYS = {
    "week": ("lastTradingWeekPerformance", "weekPerformance", "oneWeekPerformance", "lastWeekPerformance"),
    "month": ("lastTradingMonthPerformance", "monthPerformance", "oneMonthPerformance", "lastMonthPerformance"),
    "year": ("lastTradingYearPerformance", "yearPerformance", "oneYearPerformance", "lastYearPerformance"),
}
OVERVIEW_PERFORMANCE_KEYS = {
    "day": ("TODAY", "DAY", "ONE_DAY"),
    "week": ("ONE_WEEK", "WEEK", "LAST_TRADING_WEEK"),
    "month": ("ONE_MONTH", "MONTH", "LAST_TRADING_MONTH"),
    "year": ("ONE_YEAR", "YEAR", "THIS_YEAR", "LAST_TRADING_YEAR"),
    "since_start": ("SINCE_START", "SEDAN_START", "ALL_TIME", "TOTAL", "INCEPTION"),
}
ACCOUNT_PERFORMANCE_PERIOD_MAP = {
    "ONE_WEEK": TimePeriod.ONE_WEEK,
    "ONE_MONTH": TimePeriod.ONE_MONTH,
    "THREE_MONTHS": TimePeriod.THREE_MONTHS,
    "YEAR_TO_DATE": TimePeriod.THIS_YEAR,
    "ONE_YEAR": TimePeriod.ONE_YEAR,
    "THREE_YEARS": TimePeriod.THREE_YEARS,
    "SINCE_START": TimePeriod.ALL_TIME,
    "YTD": TimePeriod.THIS_YEAR,
    "THIS_YEAR": TimePeriod.THIS_YEAR,
    "ALL_TIME": TimePeriod.ALL_TIME,
    "TODAY": TimePeriod.TODAY,
    "FIVE_YEARS": TimePeriod.FIVE_YEARS,
    "THREE_YEARS_ROLLING": TimePeriod.THREE_YEARS_ROLLING,
    "FIVE_YEARS_ROLLING": TimePeriod.FIVE_YEARS_ROLLING,
    "SEDAN_START": TimePeriod.ALL_TIME,
}
REALTIME_KEYS = {
    "isRealTime",
    "isRealtime",
    "realTime",
    "realtime",
    "realTimeQuotes",
    "realtimeQuotes",
}
DELAYED_KEYS = {
    "delayed",
    "isDelayed",
    "delayedQuotes",
    "isDelayedQuote",
}
LOG_CATEGORY_FILES = {
    "app": "app.jsonl",
    "mcp": "mcp.jsonl",
    "trading": "trading.jsonl",
}
EXTERNAL_HTTP_TIMEOUT_SECONDS = float(os.getenv("AVANZA_EXTERNAL_HTTP_TIMEOUT_SECONDS", "20"))
DEFAULT_BRAVE_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)
EXTERNAL_HTTP_USER_AGENT = os.getenv(
    "AVANZA_EXTERNAL_HTTP_USER_AGENT",
    DEFAULT_BRAVE_USER_AGENT,
)
TRADINGVIEW_SCANNER_URL_TEMPLATE = "https://scanner.tradingview.com/{market}/scan"
TRADINGVIEW_DEFAULT_MARKET = "america"
TRADINGVIEW_DEFAULT_EXCHANGE = "NASDAQ"
TRADINGVIEW_CRYPTO_MARKET = "crypto"
TRADINGVIEW_FOREX_MARKET = "forex"
TRADINGVIEW_CRYPTO_EXCHANGE_FALLBACKS = ("CRYPTO", "BINANCE", "COINBASE", "BITSTAMP", "KRAKEN", "BYBIT", "OKX")
TRADINGVIEW_FOREX_EXCHANGE_FALLBACKS = ("FX_IDC", "OANDA", "FOREXCOM")
TRADINGVIEW_EXCHANGE_MARKET_HINTS = {
    "NASDAQ": "america",
    "NYSE": "america",
    "NYSEARCA": "america",
    "NYSEAMERICAN": "america",
    "AMEX": "america",
    "CBOE": "america",
    "IEX": "america",
    "OTC": "america",
    "OTCBB": "america",
    "LSE": "uk",
    "TSX": "canada",
    "TSXV": "canada",
    "ASX": "australia",
    "XETR": "germany",
    "FWB": "germany",
    "EPA": "france",
    "BME": "spain",
    "MIL": "italy",
    "AMS": "netherlands",
    "NSE": "india",
    "BSE": "india",
    "BIST": "turkey",
}
TRADINGVIEW_MARKET_EXCHANGE_FALLBACKS = {
    "america": ("NASDAQ", "NYSE", "NYSEARCA", "NYSEAMERICAN", "AMEX", "OTC"),
    "uk": ("LSE",),
    "canada": ("TSX", "TSXV"),
    "australia": ("ASX",),
    "germany": ("XETR", "FWB"),
    "france": ("EPA",),
    "spain": ("BME",),
    "italy": ("MIL",),
    "netherlands": ("AMS",),
    "india": ("NSE", "BSE"),
    "turkey": ("BIST",),
}
TRADINGVIEW_MARKET_FALLBACKS = ("global",)
TRADINGVIEW_FIAT_CODES = {"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD", "SEK", "NOK", "DKK"}
TRADINGVIEW_CRYPTO_QUOTE_SUFFIXES = ("USDT", "USDC", "USD", "BTC", "ETH", "EUR", "TRY", "BUSD")
TRADINGVIEW_PROFILE_URL_TEMPLATE = "https://www.tradingview.com/symbols/{symbol_slug}/"
TRADINGVIEW_LOGIN_URL = "https://www.tradingview.com/accounts/signin/"
TRADINGVIEW_HEATMAP_FIELDS = [
    "name",
    "description",
    "exchange",
    "type",
    "subtype",
    "sector",
    "industry",
    "close",
    "change",
    "change_abs",
    "volume",
    "Value.Traded",
    "relative_volume_10d_calc",
    "market_cap_basic",
    "fundamental_currency_code",
    "currency",
    "premarket_close",
    "postmarket_close",
    "update_mode",
]
TRADINGVIEW_US_EQUITY_EXCHANGES = ("NASDAQ", "NYSE", "NYSEARCA", "NYSEAMERICAN", "AMEX")
TRADINGVIEW_OTC_EXCHANGES = ("OTC", "OTCBB", "OTCQX", "OTCQB", "OTCMKTS", "PINK")
TRADINGVIEW_WATCHLIST_ROW_LIMIT = 1500
TRADINGVIEW_TUI_REFRESH_SECONDS = 15.0
TRADINGVIEW_ANALYTICS_FIELDS = [
    "name",
    "description",
    "exchange",
    "sector",
    "industry",
    "close",
    "change",
    "change_abs",
    "volume",
    "market_cap_basic",
    "fundamental_currency_code",
    "high",
    "low",
    "open",
    "Perf.W",
    "Perf.1M",
    "Perf.3M",
    "Perf.6M",
    "Perf.YTD",
    "Perf.Y",
    "Recommend.All",
    "Recommend.MA",
    "Recommend.Other",
    "RSI",
    "MACD.macd",
    "MACD.signal",
    "Stoch.K",
    "Stoch.D",
]
TRADINGVIEW_DEEP_ANALYTICS_CANDIDATE_FIELDS = [
    "name",
    "description",
    "exchange",
    "type",
    "subtype",
    "sector",
    "industry",
    "country",
    "close",
    "change",
    "change_abs",
    "open",
    "high",
    "low",
    "volume",
    "Value.Traded",
    "average_volume_10d_calc",
    "relative_volume_10d_calc",
    "market_cap_basic",
    "fundamental_currency_code",
    "currency",
    "price_earnings_ttm",
    "price_book_fq",
    "price_sales_current",
    "earnings_per_share_basic_ttm",
    "earnings_per_share_diluted_ttm",
    "earnings_per_share_diluted_yoy_growth_ttm",
    "dividends_yield_current",
    "dividend_payout_ratio_ttm",
    "beta_1_year",
    "shares_outstanding_current",
    "float_shares_outstanding",
    "number_of_employees",
    "52_week_high",
    "52_week_low",
    "Perf.W",
    "Perf.1M",
    "Perf.3M",
    "Perf.6M",
    "Perf.YTD",
    "Perf.Y",
    "Recommend.All",
    "Recommend.MA",
    "Recommend.Other",
    "RSI",
    "RSI[1]",
    "MACD.macd",
    "MACD.signal",
    "Stoch.K",
    "Stoch.D",
    "ADX",
    "CCI20",
    "Mom",
    "AO",
    "UO",
    "VWMA",
    "EMA20",
    "EMA50",
    "EMA100",
    "EMA200",
    "SMA20",
    "SMA50",
    "SMA100",
    "SMA200",
    "Volatility.W",
    "update_mode",
    "premarket_close",
    "premarket_high",
    "premarket_low",
    "postmarket_close",
    "postmarket_high",
    "postmarket_low",
    "earnings_release_next_date",
    "earnings_release_next_time",
]
TRADINGVIEW_RECOMMENDATION_THRESHOLDS = (
    (-0.5, "Strong Sell"),
    (-0.1, "Sell"),
    (0.1, "Neutral"),
    (0.5, "Buy"),
    (1.0, "Strong Buy"),
)
TRADINGVIEW_WATCHLIST_SYMBOL_PATTERN = re.compile(r"/symbols/([A-Z0-9_.-]+)/")
TRADINGVIEW_WATCHLIST_ID_PATTERN = re.compile(r"/watchlists/(\d+)", re.IGNORECASE)
TRADINGVIEW_NUMERIC_ID_PATTERN = re.compile(r"(\d{4,})")
TRADINGVIEW_UNKNOWN_FIELD_PATTERN = re.compile(r'Unknown field "([^"]+)"')
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SEC_SUBMISSIONS_URL_TEMPLATE = "https://data.sec.gov/submissions/CIK{cik}.json"
FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
FMP_ANALYST_RECOMMENDATIONS_URL_TEMPLATE = "https://financialmodelingprep.com/api/v3/analyst-stock-recommendations/{symbol}"
POLYGON_ANALYST_INSIGHTS_URL = "https://api.polygon.io/benzinga/v1/analyst-insights"


def load_project_version(default: str = "0.0.0-dev") -> str:
    pyproject_path = _REPO_ROOT.joinpath("pyproject.toml")
    try:
        raw = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except Exception:
        return default
    project = raw.get("project")
    if not isinstance(project, dict):
        return default
    version = str(project.get("version", "") or "").strip()
    return version or default


APP_VERSION = load_project_version()
APP_NAME = "Avanza-MCP"
TUI_TITLE = f"{APP_NAME} v{APP_VERSION}"
GITHUB_RELEASE_REPO = os.getenv("AVANZA_GITHUB_REPO", "Hamid-K/avanza-mcp")
UPDATE_CHECK_INTERVAL_SECONDS = float(os.getenv("AVANZA_UPDATE_CHECK_INTERVAL_SECONDS", "1800"))
UPDATE_CHECK_TIMEOUT_SECONDS = float(os.getenv("AVANZA_UPDATE_CHECK_TIMEOUT_SECONDS", "10"))
UPDATE_BLINK_INTERVAL_SECONDS = float(os.getenv("AVANZA_UPDATE_BLINK_INTERVAL_SECONDS", "0.7"))
SESSION_ACCENT_COLORS: tuple[str, ...] = (
    "#3b82f6",
    "#22c55e",
    "#f59e0b",
    "#ef4444",
    "#a855f7",
    "#14b8a6",
    "#f97316",
    "#84cc16",
    "#06b6d4",
    "#eab308",
)
