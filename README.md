
# Avanza-MCP

> [!WARNING]
> This is an experimental project. Use it at your own risk.
>
> Always start in **read-only** mode and **paper trading** mode first. Do not enable live write/trading until you have validated behavior end-to-end in paper mode.
>
> Notes below explain what paper trading mode is for and how to use it safely.

Single-script CLI + Textual TUI for Avanza portfolio monitoring, regular buy/sell orders, stop-loss management, MCP integration, and paper trading.

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
If `--order-valid-days` is omitted, `avanza_cli` uses the current Avanza-safe default (`8`).

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

After login, the largest account by total value is selected by default. The top panel groups account metrics into colored cards, keeps action buttons together, and shows a live clock plus a weekday OMXS open/close countdown. The P/L metric cycles through `1D P/L`, `1W P/L`, `1M P/L`, `1Y P/L`, `Since Start P/L`, and `Total P/L`, with SEK and % values colored separately. The main table shows the selected account's stocks with day movement, profit state, a distinct header row, and a real-time quote indicator: green dot for real-time, yellow dot for delayed or unresolved status. The order ticket searches as you type by stock name, ticker, or ISIN, so it supports opening new positions as well as trading current holdings. The lower table shows stop-losses and open orders for the selected account, with trigger and price values labeled as `SEK` or `%`; its cancel column opens a guarded cancellation ticket. Buy/sell side cells are color-coded green/red. Click any table column header to sort by that column; click the same header again to reverse the order. Drag the horizontal divider between tables, the vertical divider beside Active Trades, or the left edge of the order/stop-loss ticket to resize panes. Position and order state refreshes live every 5 seconds.

## MCP Server Registration & Run

This project exposes MCP through `python avanza_cli.py mcp` (stdio transport).

### 1) Start and authenticate the TUI

```bash
python avanza_cli.py tui
```

Log in, then enable the `MCP` tick box in the TUI. This starts the localhost bridge and writes `.avanza_mcp_session.json`.

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

### Available MCP Tools

| Tool | Purpose |
|---|---|
| `avanza_status` | MCP bridge status, safety mode, and selected account. |
| `avanza_accounts` | List available Avanza accounts in the active TUI session. |
| `avanza_account_performance` | Read Avanza account development/performance for selected or specified account across common periods (including since-start). |
| `avanza_portfolio` | Portfolio positions for selected or specified account. |
| `avanza_stoplosses` | Stop-loss orders for selected or specified account. |
| `avanza_open_orders` | Live open/pending regular orders for selected or specified account. |
| `avanza_ongoing_orders` | Ongoing orders view: live stop-losses + live open orders (+ optional paper active orders). |
| `avanza_transactions` | Executed order/transaction history with account/date/type filters. |
| `avanza_live_snapshot` | Full polling snapshot (positions, orders, stop-losses, paper state). |
| `avanza_realtime_quotes` | Real-time quote snapshot for current holdings. |
| `avanza_search_stock` | Search stocks/order books by name, ticker, or ISIN. |
| `avanza_paper_stoploss_set` | Create paper stop-loss (no live Avanza mutation). |
| `avanza_paper_order_set` | Create paper buy/sell order (no live Avanza mutation). |
| `avanza_paper_orders` | List paper orders/events for selected or specified account. |
| `avanza_paper_cancel` | Cancel a paper order (local only). |
| `avanza_stoploss_set` | Dry-run or place stop-loss. |
| `avanza_stoploss_edit` | Dry-run or edit/replace stop-loss (delete + place). |
| `avanza_stoploss_delete` | Dry-run or delete stop-loss. |
| `avanza_order_set` | Dry-run or place regular buy/sell order. |
| `avanza_order_edit` | Dry-run or edit regular order. |
| `avanza_order_delete` | Dry-run or delete regular order. |
| `avanza_open_order_edit` | Dry-run or edit an existing open/pending regular order (alias of `avanza_order_edit`). |
| `avanza_open_order_cancel` | Dry-run or cancel an existing open/pending regular order (alias of `avanza_order_delete`). |

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

## Credits

Creators: **Hamid Kashfi** and **Codex (OpenAI)**.

This project builds on the Python `avanza-api` library by fama93:

- PyPI: [avanza-api](https://pypi.org/project/avanza-api/)
- Source: [github.com/fhqvst/avanza](https://github.com/fhqvst/avanza)
