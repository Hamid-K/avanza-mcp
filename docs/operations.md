# Operations

All commands below may be run with `uv run` when using the `uv` workflow.
Example: `uv run python avanza_cli.py tui`.

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

### Available MCP tools

| Tool | Purpose |
|---|---|
| `avanza_status` | MCP bridge status, safety mode, and selected account. |
| `avanza_accounts` | List accounts visible in the active TUI session. |
| `avanza_portfolio` | Portfolio positions for selected/specified account. |
| `avanza_stoplosses` | Stop-loss list for selected/specified account. |
| `avanza_open_orders` | Live open/pending regular orders for selected/specified account. |
| `avanza_ongoing_orders` | Ongoing orders view: live stop-losses + live open orders (+ optional paper active orders). |
| `avanza_transactions` | Executed order/transaction history with account/date/type filters. |
| `avanza_live_snapshot` | Full polling snapshot for trading loops. |
| `avanza_realtime_quotes` | Real-time quote snapshot for holdings. |
| `avanza_search_stock` | Search stocks/order books by name, ticker, or ISIN. |
| `tv_auth_custom_lists` | TradingView authenticated custom tracking lists (list inventory + rows, optional list switch by id/name). |
| `avanza_paper_stoploss_set` | Create paper stop-loss order. |
| `avanza_paper_order_set` | Create paper regular order. |
| `avanza_paper_orders` | List paper orders and events. |
| `avanza_paper_cancel` | Cancel paper order. |
| `avanza_stoploss_set` | Dry-run or place stop-loss. |
| `avanza_stoploss_edit` | Dry-run or edit/replace stop-loss. |
| `avanza_stoploss_delete` | Dry-run or delete stop-loss. |
| `avanza_order_set` | Dry-run or place regular order. |
| `avanza_order_edit` | Dry-run or edit regular order. |
| `avanza_order_delete` | Dry-run or delete regular order. |
| `avanza_open_order_edit` | Dry-run or edit an existing open/pending regular order (alias of `avanza_order_edit`). |
| `avanza_open_order_cancel` | Dry-run or cancel an existing open/pending regular order (alias of `avanza_order_delete`). |

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
