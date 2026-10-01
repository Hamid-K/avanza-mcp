# Frozen-holdings attribution validation

`avanza_frozen_holdings_attribution` is a read-only reconstruction, not trading
authority and not an estimate of guaranteed missed profits.

## Currency and inventory contract

- Account valuation inputs must explicitly be SEK. Native instrument chart
  prices require an explicit currency; ticker, venue and country inference are
  not currency evidence.
- The pure builder accepts dated FX points under `fx_history_by_currency`.
  Each point's `close` is **SEK per one unit of the named currency**. An exact
  dated rate is required at every performance valuation, including the start.
  SEK uses unity. A current quote or transaction-time FX sample must not be
  substituted for missing historical FX.
- The native provider currently has no historical FX feed. It therefore blocks
  foreign starting holdings rather than returning a mixed-currency benchmark.
- Starting shares are reconstructed from current inventory by reversing executed
  BUY/SELL transactions through the Stockholm inventory date, including trades
  after the last performance point. The start is an end-of-day baseline; trades
  on the start date are already part of that inventory.
- Exact raw account IDs, non-truncated history, parseable dates, valid prices and
  supported inventory events are required. A second inventory read checks for
  quantity changes; a date rollover blocks the multi-call reconstruction.
  Reads are not an atomic broker snapshot and cannot prove absence of every
  intraday race or unreported corporate action.

Missing identity, currency, FX, inventory history or source validity produces
`BLOCKED_INCOMPLETE_HISTORY`. Frozen returns, their difference from actual
returns, starting holdings value and residual cash are then `null`; requested
daily benchmark rows are empty. Independently supplied actual return data is
not replaced with the partial frozen result.

## Modeling limits

Native prices may carry forward at most seven calendar days. Non-finite prices,
duplicate dates and invalid OHLC bounds block the calculation. Unsupported
corporate-action events also block rather than inventing a share adjustment.
Cash events between account-performance dates are included in the next interval.

Dividend cash is the observed account dividend, **not a recomputed dividend
entitlement for frozen shares**. The result discloses this hybrid cash-flow
assumption; `COMPLETE` validates its inputs, not a full economic counterfactual.
Splits, redenominations and other unreported adjustments need independently
validated history before a real-world opportunity-cost claim is justified.

Stop-loss readback separately exposes `currency` and `currency_status`. Monetary
stop prices with missing/conflicting explicit units display `UNKNOWN`, not an
assumed SEK label. Percentage values remain percentages. No order values are
converted or changed by this display correction.

The goal audit also preserves an explicit
`cross_instrument_antal_total_not_economic_metric=true` flag when the canonical
history omits `open_sale_quantity_exact`. Such links require a null quantity,
not a fabricated sum of shares in unrelated securities. Missing flags and
contradictory numeric totals fail validation; completion checks are unchanged.

## Runtime deployment

Tests use synthetic fixtures only. Source changes affect a running TUI/Web MCP
provider only after the operator reloads that provider. A passing test suite is
not native broker readback, portfolio protection, placement or goal completion.
