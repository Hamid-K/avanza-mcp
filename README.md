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
  --trigger-value-type percentage \
  --valid-until 2026-05-28 \
  --order-type sell \
  --order-price 1 \
  --order-price-type percentage \
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

The TUI masks password and TOTP inputs, clears those fields after a successful login, and requires typing `PLACE` before the live placement button will send an order. Use `Dry Run` first and review the request in the log panel.

After login, the TUI enumerates all accounts in an accounts table. Select an account row and press `Use Selected Account` to switch the active account at any time. Portfolio and stop-loss tables are filtered to the selected account, and live stop-loss placement uses that selected account.

## Safety

This uses the unofficial `avanza-api` package. Start with `stoploss list` and dry-runs. Verify Avanza's live interpretation of percentage and gliding stop-loss fields with very small size before trusting it for meaningful orders.
