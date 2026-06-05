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
| Tracker status | Every one-share/unit tracker must be classified as `NO BUY-BACK`, `GLIDE/DEEP ONLY`, `HAS PERSISTENT BUY STOP`, `HAS FIXED BUY ORDER`, `HOLD TRACKER ONLY`, or `THESIS BROKEN / AVOID`. Prefer `HAS PERSISTENT BUY STOP` for fixed buy-back ladders that should survive market close. |
| Cash drift | If buying power is high while market stance is constructive, deep/gliding-only buy-backs are not enough. Add or propose near-current, below-sale, and deeper stages unless the thesis is broken. |
| Recent sells | Every material recent sell needs an active same-account buy-back plan or an explicit no-reentry reason. |
| Stops | Sell-side stop coverage normally should equal current holding minus one tracker unit. Status `ERROR` is unprotected. |
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
