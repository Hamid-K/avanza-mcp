
# Avanza-MCP

> [!WARNING]
> This is an experimental project. Use it at your own risk.
>
> Always start in **read-only** mode and **paper trading** mode first. Do not enable live write/trading until you have validated behavior end-to-end in paper mode.
>
> Notes below explain what paper trading mode is for and how to use it safely.

Single-script CLI + Textual TUI for Avanza portfolio monitoring, regular buy/sell orders, stop-loss management, MCP integration, and paper trading.

Trading-assistant context lives in:

- `INSTRUCTIONS/INSTRUCTIONS.md`: standing operating rules and safety constraints.
- `INSTRUCTIONS/MEMORY.md`: timestamped lessons, mistakes, strategy updates, and checklist changes. This is historical context, not live portfolio state.
- `INSTRUCTIONS/WARMUP.md`: prompt for starting a fresh Codex trading session with the right context.

The `INSTRUCTIONS/` folder is kept visible in git, but its private contents are ignored and should remain local-only.

For trading analysis, always refresh live Avanza MCP data. Do not treat markdown memory as current holdings, orders, prices, account IDs, or stop-loss IDs.

Credentials are prompted at runtime:

- username: visible prompt, unless passed with `--username`
- password: masked
- current TOTP code: masked

Alternatively, pass `--onepassword-item ITEM` and optional `--onepassword-vault VAULT` to read the Avanza username, password, and current TOTP code through the 1Password CLI (`op`). The TUI has a matching `Login with 1Password` path. The tool does not store these secrets; `op` will ask you to authorize access through the local 1Password app.

The current TOTP code is passed to `avanza-api` as `totpToken`, which is the field name expected by the installed library version.

**TUI Demo:**

<img width="2935" height="1507" alt="TUI" src="https://github.com/user-attachments/assets/c905313c-d719-4c42-b546-78d116cfda2d" />



## Setup

```bash
uv sync --dev
chmod +x scripts/verify.sh .githooks/pre-commit .githooks/pre-push
git config core.hooksPath .githooks
```

If `uv` is not installed yet, install it first:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Command examples below can be run either inside an activated environment or by prefixing with `uv run`.
For example: `uv run python avanza_cli.py tui`.

Run the full quality gate at any time:

```bash
scripts/verify.sh
```

## Commands

Console commands print human-readable Rich tables and summaries, not raw API payloads.

Show running app version:

```bash
python avanza_cli.py --version
```

Show account overview:

```bash
python avanza_cli.py accounts
```

Show portfolio summary:

```bash
python avanza_cli.py portfolio summary
```

Show detailed portfolio positions:

```bash
python avanza_cli.py portfolio positions
```

Show transaction history (executed orders by default):

```bash
python avanza_cli.py transactions list
```

Pull broader history:

```bash
python avanza_cli.py transactions list --all --max-elements 5000
```

Search for a stock/order book:

```bash
python avanza_cli.py search-stock "VOLV B"
```

List active stop-loss orders:

```bash
python avanza_cli.py stoploss list
```

Dry-run a trailing/gliding sell stop-loss:

```bash
python avanza_cli.py stoploss set \
  --account-id ACCOUNT_ID \
  --order-book-id ORDER_BOOK_ID \
  --trigger-type follow-upwards \
  --trigger-value 5 \
  --trigger-value-type % \
  --order-type sell \
  --order-price 1 \
  --order-price-type % \
  --volume 10
```

Place the order for real by adding `--confirm` after reviewing the dry-run output.
If `--valid-until` is omitted, `avanza_cli` automatically uses the longest currently allowed date (today + 90 days).
If `--order-valid-days` is omitted, `avanza_cli` uses the current Avanza-safe default (`1`).
Dry-run/preview now shows both trigger validity and derived triggered-order expiry (`if triggered today`).
For live non-SEK/foreign instruments, `order_valid_days > 1` is blocked to prevent Avanza `Ogiltigt giltighetsdatum` trigger failures.

Delete a stop-loss order dry-run:

```bash
python avanza_cli.py stoploss delete \
  --account-id ACCOUNT_ID \
  --stop-loss-id STOP_LOSS_ID
```

Delete for real by adding `--confirm`.

List open regular orders:

```bash
python avanza_cli.py orders list
```

Dry-run a regular buy/sell order:

```bash
python avanza_cli.py orders set \
  --account-id ACCOUNT_ID \
  --order-book-id ORDER_BOOK_ID \
  --order-type buy \
  --price 100 \
  --valid-until 2026-05-28 \
  --volume 10 \
  --condition normal
```

Delete an order dry-run:

```bash
python avanza_cli.py orders delete \
  --account-id ACCOUNT_ID \
  --order-id ORDER_ID
```

## Textual TUI

Run the terminal UI from the same script:

```bash
python avanza_cli.py tui
```

The TUI masks password and TOTP inputs, clears those fields after a successful login, and hides the login screen. You can also enter a 1Password item name/ID and optional vault, then use `Login with 1Password` to let the local `op` CLI fetch username, password, and TOTP after your 1Password approval. Use `Review Only` first to validate and log an order request without creating a paper or live order.

Use the `Reload TUI` top-button to hard-restart `python avanza_cli.py tui` with the same arguments and load latest code changes without manually quitting/relaunching.
The bottom status bar includes an automatic GitHub update checker indicator; when a newer release exists, it flashes a warning status.
Controls: `AVANZA_UPDATE_CHECK_ENABLED=0` disables checks, `AVANZA_GITHUB_REPO=owner/repo` overrides source.

After login, the largest account by total value is selected by default. The top panel groups account metrics into colored cards, keeps action buttons together, and shows a live clock plus a weekday OMXS open/close countdown. The P/L metric cycles through `1D P/L`, `1W P/L`, `1M P/L`, `1Y P/L`, `Since Start P/L`, and `Total P/L`, with SEK and % values colored separately. The main table shows the selected account's stocks with day movement, profit state, a distinct header row, and a real-time quote indicator: green dot for real-time, yellow dot for delayed or unresolved status. The order ticket searches as you type by stock name, ticker, or ISIN, so it supports opening new positions as well as trading current holdings. The lower table shows stop-losses and open orders for the selected account, with trigger and price values labeled as `SEK` or `%`; its cancel column opens a guarded cancellation ticket. Buy/sell side cells are color-coded green/red. Click any table column header to sort by that column; click the same header again to reverse the order. Drag the horizontal divider between tables, the vertical divider beside Active Trades, or the left edge of the order/stop-loss ticket to resize panes. Position and order state refreshes live every 5 seconds.

Multi-session mode: use **Login extra account** to add more authenticated Avanza sessions without leaving TUI. The session selector lets you switch tenant context quickly; account drop-down and all tables follow the selected session.

## MCP Server Registration & Run

This project exposes MCP through `python avanza_cli.py mcp` (stdio transport).

### 1) Start and authenticate the TUI

```bash
python avanza_cli.py tui
```

Log in, then enable the `MCP` tick box in the TUI. This starts the localhost bridge and writes `.avanza_mcp_session.json`.
By default, the MCP bridge token is saved to macOS Keychain (`security` CLI) and only metadata is written to the dotfile; if keychain is unavailable, it falls back to file storage. Override with `AVANZA_MCP_SESSION_BACKEND=keychain|file|auto` (default `auto`).

### 2) Register the MCP server in Codex/Codex CLI

Add this to `~/.codex/config.toml`:

```toml
[mcp_servers.avanza-mcp]
command = "python"
args = ["/ABSOLUTE/PATH/TO/avanza_cli.py", "mcp"]
```

Use the absolute path to your local `avanza_cli.py`.

### 3) Run from your MCP client

After registration, start/reload Codex or Codex CLI. It will launch:

```bash
python avanza_cli.py mcp
```

The MCP proxy forwards tool calls to the authenticated TUI session through the localhost bridge. MCP starts read-only. Enable `Live R/W` in the TUI for live mutations; live stop-loss/order placement, edit, or deletion still requires MCP arguments to include `confirm: true`. MCP activity is shown in the lower-right log console.

For multi-session setups:
- use `avanza_sessions` to inspect loaded tenant sessions,
- use `avanza_select_session` to switch active context,
- account/session-context `avanza_*` tools accept optional `tenant_session_id` for explicit tenant routing.
- for non-paper tools, `session_id` is kept as a legacy alias for tenant routing.
- or pass `account_id` and the bridge auto-scopes to the session owning that account.
- paper-ledger tools use `session_id` for paper ledger grouping; use `tenant_session_id` there only when explicit tenant routing is required.

### Available MCP Tools

| Tool | Purpose |
|---|---|
| `avanza_status` | Show TUI MCP bridge status, selected account, and current safety mode. |
| `avanza_capabilities` | Return consolidated MCP safety/capability status for automation loops (paper/live guards, account context, and tool availability). |
| `avanza_live_session_authorize` | Explicitly enable live mutation permission for this active MCP or TUI session. |
| `avanza_live_session_revoke` | Disable live mutation permission for this MCP or TUI session and force paper-only mode. |
| `avanza_accounts` | List Avanza accounts currently visible to the authenticated TUI session. |
| `avanza_sessions` | List loaded authenticated Avanza tenant sessions in the running TUI. |
| `avanza_select_session` | Switch active MCP/TUI tenant session context. |
| `avanza_select_account` | Safely switch MCP or TUI selected account context. |
| `avanza_account_performance` | Read Avanza account performance/development for the selected or supplied account_id over a chosen period. |
| `tv_scrape_symbol_analytics` | Fetch TradingView symbol analytics and technical recommendation barometers from public scanner data. |
| `tv_scrape_symbol_full` | Fetch rich TradingView symbol payload (scanner analytics + technical labels + symbol profile metadata) in LLM-friendly JSON. |
| `tv_auth_session_start` | Open TradingView login page in browser and show session setup instructions for authenticated MCP usage. |
| `tv_auth_session_set` | Persist TradingView session cookie for authenticated tv_auth_* MCP tools. |
| `tv_auth_session_login_auto` | Open instrumented browser, let user log in normally, and automatically capture/save TradingView session cookies. |
| `tv_auth_session_status` | Show saved TradingView authenticated session status used by tv_auth_* tools. |
| `tv_auth_session_clear` | Delete saved TradingView authenticated session cookie. |
| `tv_auth_symbol_analytics` | Fetch TradingView symbol analytics in authenticated mode (inherits account entitlements from supplied TradingView cookie/session). |
| `tv_auth_symbol_full` | Fetch rich TradingView symbol payload in authenticated mode (scanner analytics + technical labels + profile metadata + entitlement context). |
| `tv_scrape_heatmap` | Fetch TradingView market heatmap rows (top movers) using free scanner data. |
| `tv_auth_watchlist` | Best-effort TradingView watchlist monitor in authenticated mode (cookie/session required for private list context). |
| `tv_auth_custom_lists` | Load authenticated TradingView custom tracking lists and rows from your TradingView profile session. |
| `zacks_scrape_symbol` | Scrape Zacks symbol page for rank and quick analytics (best effort; may be blocked without valid browser session/cookies). |
| `fmp_analyst_recommendations` | Fetch analyst recommendation history for a symbol from Financial Modeling Prep (requires FMP API key). |
| `polygon_analyst_insights` | Fetch analyst insights/ratings for a symbol from Polygon Benzinga feed (requires Polygon API key). |
| `sec_filings_recent` | Fetch recent SEC EDGAR filings by ticker or CIK (official SEC data). |
| `fred_series` | Fetch FRED macro observations (requires a free FRED API key via FRED_API_KEY or api_key input). |
| `data_source_status` | Return current health, freshness, and safety flags for Avanza, TradingView, Zacks, FMP, Polygon, SEC, and FRED source integrations. |
| `signal_context_bundle` | Build a compact cross-source signal bundle (TradingView technicals + SEC filings + optional Zacks/FMP/Polygon + optional FRED macro). |
| `avanza_portfolio` | List portfolio positions for the selected account, or a supplied account_id. |
| `avanza_stoplosses` | List stop-loss orders for the selected account, or a supplied account_id. |
| `avanza_open_orders` | List live open/pending regular orders for the selected account, or a supplied account_id, with stable IDs for edit/cancel flows. |
| `avanza_open_orders_raw` | Debug tool: return normalized open orders plus raw Avanza order payload for schema diagnostics. |
| `avanza_ongoing_orders` | List ongoing orders for the selected account: live stop-losses + live open orders, with optional paper active orders. |
| `avanza_transactions` | List executed orders/history (BUY/SELL by default) with optional account/date/type filters. |
| `avanza_live_snapshot` | Read a decision-ready snapshot for polling loops: positions, live stop-losses/orders, paper orders, and safety mode. |
| `avanza_realtime_quotes` | Fetch real-time quote snapshot for selected account holdings (best with a 5s polling loop). |
| `avanza_orderbook_quotes` | Fetch arbitrary quote snapshots for supplied orderbook IDs (supports 5s polling loops for 20-50 symbols). |
| `avanza_market_movers` | Fetch Avanza market movers (gainers/losers) with optional country/market/turnover filters. |
| `avanza_index_constituents` | Fetch index constituents (default OMXS30) with optional quote/spread enrichment for building a liquid scalp universe. |
| `avanza_fee_estimate` | Estimate courtage/FX costs and break-even move for a planned trade (conservative assumptions when exact class data is unavailable). |
| `avanza_search_stock` | Search Avanza stock/order book data by name, ticker, or ISIN. |
| `avanza_paper_stoploss_set` | Create a local paper stop-loss order. |
| `avanza_paper_orders` | List local paper-trading orders and events for the selected account, or a supplied account_id. |
| `avanza_paper_positions` | List paper positions for a selected account/session, with optional active-only filter. |
| `avanza_paper_trades` | List completed paper trades (entry+exit ledger rows) for account/session. |
| `avanza_paper_session_summary` | Return P/L summary for a paper trading session/account. |
| `avanza_paper_order_set` | Create a local paper buy/sell order. |
| `avanza_paper_order_exit` | Close an open paper position by position_id or orderbook_id and create a completed paper trade entry. |
| `avanza_paper_risk_state` | Evaluate paper-session guardrails before allowing a new trade entry. |
| `avanza_scalp_watchlist_set` | Store/update a named scalp watchlist (orderbook IDs + optional labels) in local paper session state. |
| `avanza_scalp_watchlist_get` | Load a named scalp watchlist and optionally include current quotes for all members. |
| `avanza_paper_cancel` | Cancel a local paper order. |
| `avanza_stoploss_set` | Dry-run or place a stop-loss order. |
| `avanza_order_set` | Dry-run or place a regular buy/sell order. |
| `avanza_order_edit` | Dry-run or update an existing open order (price/volume/valid_until). |
| `avanza_open_order_edit` | Dry-run or update an existing open/pending regular order (alias of avanza_order_edit). |
| `avanza_order_delete` | Dry-run or delete a regular open order. |
| `avanza_open_order_cancel` | Dry-run or cancel an existing open/pending regular order (alias of avanza_order_delete). |
| `avanza_stoploss_delete` | Dry-run or delete a stop-loss order. |
| `avanza_stoploss_edit` | Dry-run or edit an existing stop-loss (delete old + place new). |

### TradingView/Zacks scrape mode notes

- These tools are intentionally marked experimental.
- `tv_scrape_*` runs in free anonymous mode.
- `tv_auth_*` supports three auth paths:
  - explicit tool input (`cookie` or `sessionid` + `sessionid_sign`),
  - environment variables (`TRADINGVIEW_SESSIONID`, optional `TRADINGVIEW_SESSIONID_SIGN`),
  - saved local session via `tv_auth_session_set`.
- Saved TradingView session storage defaults to macOS Keychain (`security` CLI) when available, with metadata in `.avanza_tradingview_session.json` (ignored by git). Fallback is file-only storage.
- Optional override: `AVANZA_TV_SESSION_BACKEND=keychain|file|auto` (default `auto`).
- Preferred path: `tv_auth_session_login_auto` to open an instrumented browser and capture cookies automatically after login.
- Browser-assisted flow:
  1. call `tv_auth_session_login_auto`,
  2. log in normally in opened browser window,
  3. wait for auto-capture confirmation, then run `tv_auth_session_status`,
  4. use `tv_auth_*` tools with no repeated cookie input.
- The TUI `TradingView Lists` tab uses the same authenticated profile and provides a dedicated custom-list monitor with list switching.
- If auto mode is unavailable, fallback is `tv_auth_session_start` + manual `tv_auth_session_set`.
- `zacks_scrape_symbol` is best effort; Zacks can return bot-protection pages unless a valid browser session/cookie is provided.
- Treat scrape output as decision support only. Keep live mutations behind Avanza read/write + explicit `confirm: true`.
- API-key tools:
  - `fmp_analyst_recommendations`: pass `api_key` or set `FMP_API_KEY`.
  - `polygon_analyst_insights`: pass `api_key` or set `POLYGON_API_KEY`.

### MCP Transaction History Quick Use

Use `avanza_transactions` to retrieve executed order history (BUY/SELL by default).

- Most recent 15 rows:
  - `{"maxElements": 15}`
- Include dividends and interest:
  - `{"types": ["BUY", "SELL", "DIVIDEND", "INTEREST"], "maxElements": 200}`
- Full export window:
  - `{"fromDate": "2026-01-01", "toDate": "2026-12-31", "allTransactions": true, "maxElements": 5000}`

`avanza_transactions` is read-only and works while MCP remains read-only.

### 4) ChatGPT desktop note

ChatGPT developer mode currently expects remote MCP apps/connectors over SSE or streaming HTTP, so it cannot directly register this local stdio proxy.

For auto-trading experiments, use `avanza_live_snapshot` as the polling tool. It returns a decision-ready account snapshot and is safe to call every 5 seconds. Paper trading is available in read-only MCP mode through `avanza_paper_stoploss_set`, `avanza_paper_order_set`, `avanza_paper_orders`, and `avanza_paper_cancel`; paper state is stored in `.avanza_paper_session.json` and never places an Avanza order. The TUI's `Paper` tick box is on by default; while it is on, the order and stop-loss form submit buttons create local paper orders. Turn `Paper` off only when you intend to use live Avanza placement, which still requires typing `PLACE`. Regular live buy/sell orders are also exposed through `avanza_order_set` and `avanza_order_delete`, gated by MCP R/W mode and `confirm: true`.

TUI sessions write structured JSONL logs under `avanza-cli/logs/`: a timestamped session log plus persistent `app.jsonl`, `mcp.jsonl`, and `trading.jsonl`.

## Safety

This uses the unofficial `avanza-api` package. Start with `stoploss list` and dry-runs. Verify Avanza's live interpretation of `%` and gliding stop-loss fields with very small size before trusting it for meaningful orders.

Stop-losses are not guaranteed earnings-gap or overnight protection. A tight `Kurs 99%` can avoid a bad normal-session fill, but it can also fail, remain unfilled, or show `ERROR` if price gaps through the trigger after hours, before open, during a halt, or in a fast market. Treat `ERROR` rows as unprotected, and handle after-close/before-open catalysts with explicit sizing, trim, sell, hedge, or hold-and-accept decisions.

If an `ERROR` row reason contains `Ogiltigt giltighetsdatum`, the failure is usually triggered-order validity, not trigger logic or slippage. Set `order_valid_days=1` and replace the stop-loss.

## Credits

Creators: **Hamid Kashfi** and **Codex (OpenAI)**.

This project builds on the Python `avanza-api` library by fama93:

- PyPI: [avanza-api](https://pypi.org/project/avanza-api/)
- Source: [github.com/fhqvst/avanza](https://github.com/fhqvst/avanza)
