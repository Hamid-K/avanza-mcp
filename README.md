# Avanza Trading Tools

Small CLI and Textual TUI for viewing Avanza portfolio data and managing stop-loss orders with interactive credentials.

Credentials are prompted at runtime:

- username: visible prompt, unless passed with `--username`
- password: masked
- current TOTP code: masked

The current TOTP code is passed to `avanza-api` as `totpToken`, which is the field name expected by the installed library version.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
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
  --valid-until 2026-05-28 \
  --order-type sell \
  --order-price 1 \
  --order-price-type % \
  --volume 10
```

Place the order for real by adding `--confirm` after reviewing the dry-run output.

Delete a stop-loss order dry-run:

```bash
python avanza_cli.py stoploss delete \
  --account-id ACCOUNT_ID \
  --stop-loss-id STOP_LOSS_ID
```

Delete for real by adding `--confirm`.

## Textual TUI

Run the terminal UI from the same script:

```bash
python avanza_cli.py tui
```

The TUI masks password and TOTP inputs, clears those fields after a successful login, and hides the login screen. Use `Preview` first and review the request in the log panel.

After login, the largest account by total value is selected by default. The top panel groups account metrics into colored cards, keeps action buttons together, and shows a live clock plus a weekday OMXS open/close countdown. The main table shows the selected account's stocks with day movement, profit state, and a real-time quote indicator: green dot for real-time, yellow dot for delayed or unresolved status. The order ticket can search by stock name, ticker, or ISIN, so it supports opening new positions as well as trading current holdings. The lower table shows stop-losses and open orders for the selected account, with trigger and price values labeled as `SEK` or `%`. Click any table column header to sort by that column; click the same header again to reverse the order. Drag the horizontal divider between tables or the vertical divider beside Active Trades to resize panes. Position and order state refreshes live every 5 seconds.

The TUI also has an MCP mode. Log in through the TUI, enable the `MCP` checkbox, then configure Codex or another MCP client to run:

```bash
python avanza_cli.py mcp
```

The MCP proxy forwards tool calls to the authenticated TUI session through a localhost bridge. MCP mode starts read-only. The `R/W` checkbox enables live mutations, and live stop-loss placement/deletion still requires the MCP tool call to include `confirm: true`. MCP activity is shown in the lower-right log console.

Codex and Codex CLI can use the local stdio command above. ChatGPT developer mode currently expects remote MCP apps/connectors over SSE or streaming HTTP, so it cannot directly register this local stdio proxy.

For auto-trading experiments, use `avanza_live_snapshot` as the polling tool. It returns a decision-ready account snapshot and is safe to call every 5 seconds. Paper trading is available in read-only MCP mode through `avanza_paper_stoploss_set`, `avanza_paper_order_set`, `avanza_paper_orders`, and `avanza_paper_cancel`; paper state is stored in `.avanza_paper_session.json` and never places an Avanza order. The TUI's `Paper` checkbox is on by default; while it is on, the order and stop-loss form buttons create local paper orders. Turn `Paper` off only when you intend to use live Avanza placement, which still requires typing `PLACE`. Regular live buy/sell orders are also exposed through `avanza_order_set` and `avanza_order_delete`, gated by MCP R/W mode and `confirm: true`.

TUI sessions write structured JSONL logs under `avanza-cli/logs/`: a timestamped session log plus persistent `app.jsonl`, `mcp.jsonl`, and `trading.jsonl`.

## Safety

This uses the unofficial `avanza-api` package. Start with `stoploss list` and dry-runs. Verify Avanza's live interpretation of `%` and gliding stop-loss fields with very small size before trusting it for meaningful orders.
