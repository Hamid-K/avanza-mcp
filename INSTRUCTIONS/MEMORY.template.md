# Trading Assistant Memory Template

Use this file as a public-safe template for the private local file `INSTRUCTIONS/MEMORY.md`.

Rules:
- Keep `MEMORY.md` local-only (ignored by git).
- Do not store account IDs, order IDs, stop-loss IDs, usernames, hostnames, file paths, or personal notes here.
- Keep entries generalized and strategy-focused.

## Format

Newest-first table:

| Timestamp (Stockholm) | Topic | Observation | Operational rule |
|---|---|---|---|
| YYYY-MM-DD HH:MM CEST | Example: Marker momentum miss | A one-share/tiny tracker became a top mover and was missed because small exposure was treated as low priority. | Scan notable movers before stop repairs. Every tracker/tiny residual moving `>= 8%` intraday or showing on top-mover/heatmap/news/abnormal-volume screens must get an action choice: controlled rebuild now, close pullback/continuation ladder, deeper crash buy-backs with concrete reason, or thesis-broken avoid. |
| YYYY-MM-DD HH:MM CEST | Example: Triggered-sale re-entry audit | Multiple stop/manual sales happened during a fast session and some sold slices risked being left without buy-back plans. | Before ending an action turn, scan today's `SELL` transactions and verify each sold instrument has a same-account buy-back ladder, close tactical ladder, or explicit thesis-broken/exit reason sized to sold `Antal`. |
| YYYY-MM-DD HH:MM CEST | Example: Momentum/squeeze tracker gate | A weak-fundamental tracker with abnormal volume and narrative catalysts was dismissed as too speculative and then squeezed higher. | Separate investment thesis from tactical trade setup; propose a small tightly protected tranche or close ladder when a tracker is actively squeezing, unless no-buy has exact invalidation and max chase levels. |
| YYYY-MM-DD HH:MM CEST | Example: Coordinated sell/buy-back bands | A volatile tracker had sell stops and shallow buy-back stops that could trigger too close together. | Treat sell and buy stops on the same instrument as one strategy; require a dead-zone and staged deeper re-entry tied to recent sold volume or target exposure. |
| YYYY-MM-DD HH:MM CEST | Example: Stop-loss validity | Some markets rejected long triggered-order validity windows. | Default `order_valid_days = 1` unless explicitly proven safe. |
| YYYY-MM-DD HH:MM CEST | Example: Multi-account routing | Selected account may not represent all loaded sessions. | Always call `avanza_sessions` and scope reads with `tenant_session_id` + `account_id`. |
| YYYY-MM-DD HH:MM CEST | Example: Event risk | Earnings after close can gap through stops. | Run event-first gate before stop changes; treat stops as normal-session controls only. |

## Suggested Sections (optional)

- Workflow lessons
- MCP/tooling lessons
- Risk-management lessons
- Data-quality lessons
- Strategy adjustments

## Redaction Checklist

Before committing template updates:
- No account-specific numbers.
- No instrument-specific live action logs with exact quantities/prices.
- No personal directory paths.
- No credentials/session tokens.
