# Tracker State Template

Use this file as a public-safe template for the private local file `INSTRUCTIONS/TRACKER_STATE.md`.

Rules:
- Keep `TRACKER_STATE.md` local-only (ignored by git).
- Do not commit account IDs, account names, stop-loss IDs, order IDs, session IDs, usernames, holdings, live quantities, live prices, or personal notes.
- Treat tracker state as a stale-prone snapshot. Always refresh Avanza MCP before any live mutation.
- Use placeholder account labels in templates, such as `<ACCOUNT_LABEL_A>` and `<ACCOUNT_LABEL_B>`.

Last refreshed: `<YYYY-MM-DD HH:MM TZ>` from `<MCP/status source>`.

This is the live working ledger for stop-loss coverage, buy-back state, recent sold slices, cash drift, and one-share/unit trackers. It is intentionally separate from `MEMORY.md`: memory stores lessons; this file stores the latest portfolio state snapshot.

## Maintenance Rules

| Rule | Requirement |
|---|---|
| Refresh scope | Update this file after every material portfolio review, heartbeat repair pass, stop-loss mutation, buy order mutation, triggered sell, filled buy-back, or tracker-state change. |
| Account independence | Track each loaded account separately. A buy-back in one account does not cover another account. |
| Tracker status | Every one-share/unit tracker must be classified as `REBUILD`, `PARTIAL PARTICIPATION`, `DEEP RESIDUAL`, `HOLD CURRENT EXPOSURE`, `REVIEW ONLY`, or `THESIS BROKEN / NO REENTRY`. A tracker never authorizes a BUY by itself. |
| Cash drift | Compare buying power with all conditional BUY notional. Optional growth must preserve at least `2%` post-conditional account-capital headroom unless an equal-or-larger lower-ranked commitment is displaced first. |
| Recent sells | Every material recent sell needs a forward-looking classification. Historical loss, missed upside, and the prior sale price never make full same-ticker recovery mandatory. |
| Stops | Record the exact approved tactical/profit-harvest `Antal` and retained-core floor. `Holding - 1` is only a mechanical marker-preserving maximum. Recovered/current shares default to core and receive no automatic SELL. Status `ERROR` is unprotected. |
| Whole-position plans | Require an exact `avanza_position_strategy_audit` after live refresh. Any holding, stop, open-order, missing-plan, stale-plan, or registry drift remains review-blocking until the position plan is explicitly reconciled. |
| Active-order plans | When an exact implementation ledger exists, every recovery, factor, capacity, and displacement artifact must use the same live-source timestamp and reconcile exact stop IDs, sides, counts, statuses, and notional. Any contradiction or stale estimated-order source remains review-blocking. |
| Historical transaction identity | If transaction IDs are unavailable, retain raw and exact-text-deduplicated floor views. Label the identity caveat, use the floor for churn grading, preserve the raw upper-bound view, and block any decision that changes materially between the two. |
| Crypto-linked products | Crypto-linked buy-backs and sell stops must be reviewed as one combined strategy. Avanza stops/orders cannot guarantee closed-market or gap protection. |

## Session Snapshot

| Item | Value |
|---|---|
| MCP status | `<ok/read_write/paper/live state>` |
| Loaded tenant sessions | `<session labels only, no IDs in template>` |
| Selected/default context | `<selected tenant/account label>` |
| Account summary | `<per-account total value / buying power summary, or omit in public template>` |
| Strategy note | `<short current-state note>` |
| Repair readback | `<stop-loss/open-order counts by account>` |
| Strategy-plan audit | `<per-account recorded/missing/mismatch/stale counts>` |
| Cash caveat | Buy-side stop-losses do not reserve buying power the way regular buy orders do. Displayed buying power must be compared with conditional buy-stop notional. |

## Account `<ACCOUNT_LABEL_A>`

### Single Trackers

| Holding | Unit Value | Last Sell Value | Vs Last Sell | Buy-Back State | Active Buy-Back |
|---|---:|---:|---:|---|---|
| `<HOLDING_NAME>` | `<value>` | `<value or ->` | `<% or ->` | `<classification>` | `<short sanitized ladder summary>` |

### Recent Sold Slices Repair Status

| Holding | Last Sell Date | Last Sell Qty | Last Sell Value | Sold Value In Window | Current Holding | Repair State |
|---|---:|---:|---:|---:|---:|---|
| `<HOLDING_NAME>` | `<YYYY-MM-DD>` | `<qty>` | `<value>` | `<value>` | `<qty>` | `<HAS PERSISTENT BUY STOP / HOLD TRACKER ONLY / THESIS BROKEN>` |

### Stop-Loss Coverage Issues

| Holding | Current Antal | Sell Stop Antal | Expected | Issue |
|---|---:|---:|---:|---|
| `<HOLDING_NAME>` | `<qty>` | `<qty>` | `<qty>` | `<UNDERPROTECTED / ERROR / OVERPROTECTED / stale validity>` |

### Buy-Back State

| Type | Antal | Level |
|---|---:|---|
| Fixed buy stop-loss | `<qty>` | `LESS_OR_EQUAL <price>; BUY @ <price>; valid until <date>; ID omitted in template` |
| Gliding buy stop | `<qty>` | `FOLLOW_DOWN <percent>; Kurs <percent>` |

## Account `<ACCOUNT_LABEL_B>`

Repeat the same account sections for every loaded account that needs tracker-state coverage.

## Current Strategy Gaps

| Priority | Gap |
|---:|---|
| 1 | `<example: open sell slice has no same-account buy-back decision>` |
| 2 | `<example: tracker is GLIDE/DEEP ONLY during constructive market>` |
| 3 | `<example: stop-loss ERROR row leaves slice unprotected>` |

## Implemented Repairs

Use this section only in the private file. In public templates, keep examples generic and omit exact order IDs, account IDs, and live prices.

| Timestamp | Account | Action | Readback |
|---|---|---|---|
| `<YYYY-MM-DD HH:MM TZ>` | `<ACCOUNT_LABEL>` | `<created/edited/deleted protected order>` | `<counts/status after refresh>` |
