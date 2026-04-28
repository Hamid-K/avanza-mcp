# Operations

## Login

The tools prompt for:

- Avanza username
- Avanza password
- current TOTP code

The password and TOTP prompts are masked. The TUI clears secret fields after a successful login.

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

After login, the TUI hides the credential screen and loads the trading workspace. Use the account selector in the top bar to switch accounts. The position and stop-loss/open-order tables are filtered to the selected account and refresh live every 5 seconds.

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
  --valid-until 2026-05-28 \
  --order-type sell \
  --order-price 1 \
  --order-price-type % \
  --volume 10
```

Add `--confirm` only after reviewing the request.

In the TUI, the largest account by total value is selected after login. The account bar shows total value, buying power, account status, and a colored profit summary for the selected account. The positions table includes a real-time quote indicator: green dot for real-time, yellow dot for delayed or unresolved status. If the position payload does not include that flag, the TUI resolves it from Avanza market/orderbook/instrument details and caches it per order book for five minutes. Click any table column header to sort by that column; click the same header again to reverse the order. Stop-loss trigger and order price values are labeled with `SEK` or `%`, and the positions/activity divider can be dragged to resize the panes. Selected table rows are restored after live refreshes when the row still exists.

## MCP Mode

Start the TUI and log in first:

```bash
python avanza_cli.py tui
```

Enable the `MCP` switch in the TUI. This starts a localhost bridge that reuses the TUI's authenticated Avanza client and writes a local `.avanza_mcp_session.json` file. The file contains only localhost connection details and an ephemeral token; it is ignored by git.

Configure Codex or another MCP client to run:

```bash
python avanza_cli.py mcp
```

The MCP proxy exposes account, portfolio, stop-loss, and stock-search tools. MCP starts read-only. To allow live stop-loss placement or deletion, enable the TUI `R/W` switch and require the MCP tool call to include `confirm: true`. Dry-run stop-loss previews do not require R/W mode. MCP tool activity is logged in the lower-right TUI console.

Codex and Codex CLI can run this local stdio MCP command from `~/.codex/config.toml`. ChatGPT developer mode supports remote MCP apps/connectors over SSE or streaming HTTP; it does not currently connect directly to local stdio MCP servers. To use this from ChatGPT, expose a remote streaming HTTP/SSE MCP server with appropriate authentication instead of the local `python avanza_cli.py mcp` proxy.

For live monitoring loops, poll `avanza_live_snapshot` no faster than the TUI refresh interval. The snapshot includes positions, stop-losses, open orders, paper orders, safety mode, and `poll_interval_seconds`. MCP does not push unsolicited events to Codex; polling keeps sequencing explicit and auditable.

Paper trading tools are available even while MCP is read-only:

- `avanza_paper_stoploss_set` creates a local paper stop-loss.
- `avanza_paper_orders` lists the local paper session.
- `avanza_paper_cancel` cancels a local paper order.

Paper tools never call Avanza mutation endpoints. State is stored in `.avanza_paper_session.json`, which is ignored by git.

## Logs

Every TUI run creates a timestamped JSONL session log under `avanza-cli/logs/`. Persistent category logs are kept next to it:

- `app.jsonl` for TUI and console activity.
- `mcp.jsonl` for MCP tool calls and results.
- `trading.jsonl` for live and paper stop-loss/order changes.

The lower-right MCP console also shows timestamped MCP activity while the TUI is running.
