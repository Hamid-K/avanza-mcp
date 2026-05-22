# Operations

All commands below may be run with `uv run` when using the `uv` workflow.
Example: `uv run python avanza_cli.py tui`.

## Trading Assistant Context

For Codex trading-assistant sessions, read the local-only context files before analysis:

- `INSTRUCTIONS/INSTRUCTIONS.md` for standing safety, account-switching, stop-loss, re-entry, and earnings pre-positioning rules.
- `INSTRUCTIONS/MEMORY.md` for timestamped lessons, mistakes, strategy updates, and checklist changes.
- `INSTRUCTIONS/WARMUP.md` for a clean-session prompt that summarizes the expected workflow.

The `INSTRUCTIONS/` folder is kept visible in git, but its private contents are ignored. `INSTRUCTIONS/MEMORY.md` is historical context only. It must not be used as current account state. Always verify Avanza MCP health, selected account, live holdings, open orders, active stop-losses, and transactions before drawing account-specific conclusions.

## Login

The tools prompt for:

- Avanza username
- Avanza password
- current TOTP code

The password and TOTP prompts are masked. The TUI clears secret fields after a successful login.

TradingView authenticated session cookies are stored in macOS Keychain by default when available (`security` CLI), with metadata in `.avanza_tradingview_session.json`. Set `AVANZA_TV_SESSION_BACKEND=file` to force dotfile-only storage.

As an alternative, use 1Password CLI integration:

```bash
python avanza_cli.py portfolio summary --onepassword-item Avanza --onepassword-vault Private
```

The item must contain username and password fields and a one-time password field. The script runs `op item get ITEM --format json` for username/password and `op item get ITEM --otp` for the current TOTP code. 1Password authorization is handled by the local 1Password app/CLI; the tool does not persist the returned secrets.

## Portfolio

Use the CLI for human-readable terminal output:

```bash
python avanza_cli.py portfolio summary
python avanza_cli.py portfolio positions
```

Use the TUI for a table view:

```bash
python avanza_cli.py tui
```

After login, the TUI hides the credential screen and loads the trading workspace. Use the account selector in the top bar to switch accounts. The position and stop-loss/open-order tables are filtered to the selected account and refresh live every 5 seconds. Use `Reload TUI` in the top control bar to hard-restart the app process with the same CLI arguments so local code changes are reloaded without manual quit/relaunch.
Use `Login extra account` to add additional authenticated Avanza sessions. Switch sessions from the session selector; account list and table data follow the active session.
The top-left app label includes the running version (`Avanza vX.Y.Z`) so the active build is always visible during trading sessions.
The bottom status bar includes an automatic GitHub release check; if your build is outdated it flashes an update warning. Set `AVANZA_UPDATE_CHECK_ENABLED=0` to disable, or `AVANZA_GITHUB_REPO=owner/repo` to change the repository source.

## Stop-Loss

List open stop-loss orders:

```bash
python avanza_cli.py stoploss list
```

Dry-run a trailing stop-loss before placing it:

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

Add `--confirm` only after reviewing the request.
If `--valid-until` is omitted, `avanza_cli` auto-fills the longest currently allowed date (today + 90 days).
If `--order-valid-days` is omitted, `avanza_cli` uses the current Avanza-safe default (`8`).

### Gap and Catalyst Limits

Stop-losses are trigger-based controls, not guaranteed exits. A `FOLLOW_UPWARDS` sell stop with `Kurs 99%` can be useful in normal trading because it avoids accepting a price far below the trigger, but the same tight `Kurs` can fail, remain unfilled, or show `ERROR` when price gaps through the trigger after hours, before open, during a halt, or in a fast market.

For holdings with earnings or another binary catalyst before the next tradable session, review event risk separately from ordinary stop coverage:

- exact report or catalyst timing,
- current quote freshness,
- current exposure and `Antal`,
- active sell-stop `Antal`,
- every `Max ned / Kurs` pair,
- stop status and any `ERROR` rows,
- whether to reduce before the event, hold and accept gap risk, or avoid new exposure.

Treat stop-loss rows with status `ERROR` as unprotected until they are verified and replaced or deleted after explicit authorization. Lower `Kurs` values may improve fill probability but accept worse execution and still do not guarantee a fill.

In the TUI, the largest account by total value is selected after login. The account panel shows total value, buying power, status, and profit in colored metric cards. The P/L metric cycles through `1D P/L`, `1W P/L`, `1M P/L`, `1Y P/L`, and `Total P/L` when clicked, with SEK and % values colored separately. The top-right panel shows current time with seconds and a weekday OMXS open/close countdown. The stocks table has a distinct header row and includes a real-time quote indicator: green dot for real-time, yellow dot for delayed or unresolved status. If the position payload does not include that flag, the TUI resolves it from Avanza market/orderbook/instrument details and caches it per order book for five minutes. The order ticket searches as you type by stock name, ticker, or ISIN, and searched symbols stay selected during live portfolio refreshes. `Review Only` validates and logs the request without creating a paper or live order; the submit button follows the `Paper` tick box. Buy/sell side cells are color-coded green/red. The stop-loss/open-orders list and Active Trades panel include a cancel column; paper cancellation is local, while live Avanza cancellation opens a confirmation ticket that requires typing `CANCEL`. Click any table column header to sort by that column; click the same header again to reverse the order. Stop-loss trigger and order price values are labeled with `SEK` or `%`, and the positions/activity divider, Active Trades divider, and order/stop-loss ticket edge can be dragged to resize panes. Selected table rows are restored after live refreshes when the row still exists.

## Regular Orders

List open orders:

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

Delete an order:

```bash
python avanza_cli.py orders delete \
  --account-id ACCOUNT_ID \
  --order-id ORDER_ID
```

## Transactions History

List executed orders history (BUY/SELL default):

```bash
python avanza_cli.py transactions list
```

Pull broader history:

```bash
python avanza_cli.py transactions list --all --max-elements 5000
```

Filter by account/date/types:

```bash
python avanza_cli.py transactions list \
  --account-id ACCOUNT_ID \
  --from 2026-01-01 \
  --to 2026-05-01 \
  --types BUY,SELL,DIVIDEND
```

## MCP Mode

### Register and run the MCP server

1. Start the TUI and log in:

```bash
python avanza_cli.py tui
```

2. Enable the `MCP` tick box in the TUI. This starts a localhost bridge that reuses the authenticated Avanza client and writes `.avanza_mcp_session.json`. By default, the ephemeral bridge token is stored in macOS Keychain (`security` CLI) and the dotfile stores metadata/connection details; if keychain is unavailable it falls back to file storage. Set `AVANZA_MCP_SESSION_BACKEND=file` to force dotfile-only storage.

3. Register the MCP server in `~/.codex/config.toml`:

```toml
[mcp_servers.avanza-mcp]
command = "python"
args = ["/ABSOLUTE/PATH/TO/avanza_cli.py", "mcp"]
```

4. Start/reload Codex or Codex CLI. It should run:

```bash
python avanza_cli.py mcp
```

The MCP proxy exposes account, portfolio, regular buy/sell order, stop-loss, paper-trading, and stock-search tools. MCP starts read-only. To allow live order or stop-loss placement/deletion, enable the TUI `Live R/W` tick box and require the MCP tool call to include `confirm: true`. Dry-run previews do not require R/W mode. MCP tool activity is logged in the lower-right TUI console.

Multi-session MCP behavior:
- `avanza_sessions` lists loaded tenant sessions.
- `avanza_select_session` switches active tenant context.
- All `avanza_*` tools accept optional `tenant_session_id` for explicit tenant routing.
- Non-paper tools also accept `session_id` as a legacy alias for tenant routing.
- `account_id` routing still works and auto-routes to the matching tenant session.
- Paper-ledger tools reserve `session_id` for paper strategy sessions; use `tenant_session_id` there for explicit tenant scoping.

### Available MCP tools

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

### MCP transaction history examples

Most recent 15 executed rows:

```json
{"tool":"avanza_transactions","arguments":{"maxElements":15}}
```

Include additional transaction types:

```json
{"tool":"avanza_transactions","arguments":{"types":["BUY","SELL","DIVIDEND","INTEREST"],"maxElements":200}}
```

Longer period export:

```json
{"tool":"avanza_transactions","arguments":{"fromDate":"2026-01-01","toDate":"2026-12-31","allTransactions":true,"maxElements":5000}}
```

`avanza_transactions` is read-only and can run with MCP `read_only=true`.

Codex and Codex CLI can run this local stdio MCP command from `~/.codex/config.toml`. ChatGPT developer mode supports remote MCP apps/connectors over SSE or streaming HTTP; it does not currently connect directly to local stdio MCP servers. To use this from ChatGPT, expose a remote streaming HTTP/SSE MCP server with appropriate authentication instead of the local `python avanza_cli.py mcp` proxy.

For live monitoring loops, poll `avanza_live_snapshot` no faster than the TUI refresh interval. The snapshot includes positions, stop-losses, open orders, paper orders, safety mode, and `poll_interval_seconds`. MCP does not push unsolicited events to Codex; polling keeps sequencing explicit and auditable.

Paper trading tools are available even while MCP is read-only:

- `avanza_paper_stoploss_set` creates a local paper stop-loss.
- `avanza_paper_order_set` creates a local paper buy/sell order.
- `avanza_paper_orders` lists the local paper session.
- `avanza_paper_cancel` cancels a local paper order.

Paper tools never call Avanza mutation endpoints. State is stored in `.avanza_paper_session.json`, which is ignored by git.

Regular live order tools are `avanza_order_set` and `avanza_order_delete`. They follow the same safety model as stop-losses: dry-run by default, and live mutation only with TUI R/W mode plus `confirm: true`.

## Logs

Every TUI run creates a timestamped JSONL session log under `avanza-cli/logs/`. Persistent category logs are kept next to it:

- `app.jsonl` for TUI and console activity.
- `mcp.jsonl` for MCP tool calls and results.
- `trading.jsonl` for live and paper stop-loss/order changes.

The lower-right MCP console also shows timestamped MCP activity while the TUI is running.
