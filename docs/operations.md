# Operations

## Login

The tools prompt for:

- Avanza username
- Avanza password
- current TOTP code

The password and TOTP prompts are masked. The TUI clears secret fields after a successful login.

## Portfolio

Use the CLI for JSON output:

```bash
python avanza_cli.py portfolio summary
python avanza_cli.py portfolio positions
```

Use the TUI for a table view:

```bash
python avanza_cli.py tui
```

After login, the TUI loads all accounts. Select an account row and press `Use Selected Account` to switch context. Portfolio and stop-loss views are filtered to the active account.

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
  --trigger-value-type percentage \
  --valid-until 2026-05-28 \
  --order-type sell \
  --order-price 1 \
  --order-price-type percentage \
  --volume 10
```

Add `--confirm` only after reviewing the request.
