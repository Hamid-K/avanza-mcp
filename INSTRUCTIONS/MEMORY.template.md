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
| YYYY-MM-DD HH:MM CEST | Example: Transaction identity sensitivity | A broker ledger exposed no stable transaction ID, so raw rows could include API duplicates while exact-text deduplication could also remove genuinely separate identical fills. | Retain and label both raw and conservative-floor views. Use the floor for churn grades, preserve raw values as the upper-bound source record, and block any decision that changes materially between interpretations rather than presenting either as exact. |
| YYYY-MM-DD HH:MM CEST | Example: Whole-position drift control | A static plan remained described as current after a holding fill or order change altered the live exposure. | Persist the reviewed account/orderbook plan separately from broker orders and compare exact holding plus aggregate stop/open-order exposure every run. Missing or mismatched state blocks a clean result; a fill requires review before rebaselining. Local metadata never authorizes a trade. |
| YYYY-MM-DD HH:MM CEST | Example: Downstream order-plan drift | A recovery or displacement report reused stale estimated-order data and revived a superseded keep/replace/cancel decision. | Make the exact active-order implementation ledger authoritative for stop IDs, sides, row counts, statuses, and modeled notional. All downstream artifacts must share its live-source timestamp and reconcile exactly; any contradiction blocks clean state and never authorizes mutation. |
| YYYY-MM-DD HH:MM CEST | Example: Forward-ranked tracker review | A tracker/recent sold slice was treated as automatic same-ticker recovery because price bounced or cash was available. | Classify rebuild, partial participation, deep residual, hold current exposure, or thesis-broken/no-reentry from current evidence. Historical loss and missed upside do not authorize a BUY; preserve the friction hurdle and post-conditional capacity floor. |
| YYYY-MM-DD HH:MM CEST | Example: Tracker-state discipline | Sell activity built high displayed buying power while conditional buy-stop exposure was not clearly accounted for. | Maintain `INSTRUCTIONS/TRACKER_STATE.md` as a current working ledger for stop coverage, buy-back coverage, tracker rows, recent sold slices, and cash-drift warnings. Estimate conditional buy-stop notional before calling cash idle/free. |
| YYYY-MM-DD HH:MM CEST | Example: MCP tool-name discipline | An assistant guessed an obsolete MCP tool name and hit `Unknown MCP tool`. | Check the canonical README `Available MCP Tools` table or `avanza_cli.py` tool catalog before using unfamiliar tools. Use canonical names such as `avanza_open_orders`, `avanza_stoplosses`, `avanza_search_stock`, and `avanza_orderbook_quotes`. |
| YYYY-MM-DD HH:MM CEST | Example: Marker momentum miss | A one-share/tiny tracker became a top mover and was missed because small exposure was treated as low priority. | Scan notable movers before stop repairs. Every tracker/tiny residual moving `>= 8%` intraday or showing on top-mover/heatmap/news/abnormal-volume screens must get an action choice: controlled rebuild now, close pullback/continuation ladder, deeper crash buy-backs with concrete reason, or thesis-broken avoid. |
| YYYY-MM-DD HH:MM CEST | Example: Triggered-sale re-entry audit | Multiple stop/manual sales happened during a fast session and some sold slices risked being left without buy-back plans. | Before ending an action turn, scan today's `SELL` transactions and verify each sold instrument has a same-account buy-back ladder, close tactical ladder, or explicit thesis-broken/exit reason sized to sold `Antal`. |
| YYYY-MM-DD HH:MM CEST | Example: Momentum/squeeze tracker gate | A weak-fundamental tracker with abnormal volume and narrative catalysts was dismissed as too speculative and then squeezed higher. | Separate investment thesis from tactical trade setup; propose a small tightly protected tranche or close ladder when a tracker is actively squeezing, unless no-buy has exact invalidation and max chase levels. |
| YYYY-MM-DD HH:MM CEST | Example: Coordinated sell/buy-back bands | A volatile tracker had sell stops and shallow buy-back stops that could trigger too close together. | Treat sell and buy stops on the same instrument as one strategy; require a dead-zone and staged deeper re-entry tied to recent sold volume or target exposure. |
| YYYY-MM-DD HH:MM CEST | Example: Pre-event marker miss | A marker/tiny position was left unchanged before an after-close or before-open report, then moved sharply after the event. | Force a current-account buy / pullback-entry / hold-marker-only / avoid decision before binary events. `Hold marker only` needs concrete risk and valuation reasoning plus a later trigger. |
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
