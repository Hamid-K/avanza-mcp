# Avanza Multi-Account Hourly Monitoring (Template)

Public-safe template for local/private:
- `INSTRUCTIONS/AUTOMATION_HOURLY_MONITORING.md` (ignored by git)

## Prompt

Run a concise read-only Avanza hourly monitoring pass across every loaded/visible Avanza account in authenticated TUI/MCP sessions. First read `INSTRUCTIONS/INSTRUCTIONS.md`, `INSTRUCTIONS/MEMORY.md`, and `INSTRUCTIONS/TRACKER_STATE.md` if they exist. If a current instrument strategy master is named by the tracker or priority ledger, read it before analysis. Memory contains lessons and strategy updates only, not live portfolio state. Tracker state is a working snapshot that must be refreshed from MCP before action.

Start by verifying Avanza MCP health/status, available capabilities/tools, TradingView/auth/session status if exposed, read/write state, paper trading state, live mutation state, account list, and all active tenant sessions. Use the multi-account workflow: call `avanza_sessions` and `avanza_accounts`, then read each relevant account explicitly by `tenant_session_id` and `account_id`. Do not rely on the selected/default account except as fallback context. Do not assume any account ID, account name, holding, stop-loss ID, order ID, or prior account-specific state.

Use canonical Avanza MCP names only:
- `avanza_open_orders`
- `avanza_open_orders_raw`
- `avanza_stoplosses`
- `avanza_stoploss_strategy_audit`
- `avanza_transactions`
- `avanza_live_snapshot`
- `avanza_realtime_quotes`
- `avanza_account_performance`

This automation is read-only and proposal-only. Do not create, edit, cancel, delete, or place live or paper orders. Live read/write being enabled is not authorization for mutations. Any proposed action must be returned for the live thread/user to approve.

For each account, refresh portfolio/positions, active stop-losses, open/ongoing orders, paper orders if available, recent transactions, buying power/cash where exposed, realtime quotes, and relevant market/news/earnings/crypto-linked context. Keep output compact and avoid raw tool dumps.

Event-first earnings and catalyst gate: scan every holding for same-day, after-close, before-open, next-session, and near-term catalysts. For every upcoming or recent report, unusual move, or materially news-sensitive holding, assess the entity before proposing trim/add/protection: exact report timing; prior guidance versus consensus; estimate revisions; analyst target/rating changes; prior-quarter beat/miss and guide quality; product/customer/partnership announcements; management pre-signals; peer and sector read-throughs; macro/geopolitical/FX/oil/rates sensitivity; TradingView trend, relative strength, extension, and volume; short interest/options-implied move where available. Classify as bullish pre-position, mixed hold, overextended protect/harvest, or bearish reduce. Do not default to merely protecting or trimming; strong clue clusters can justify staged pre-positioning before the event.

Critical gap-risk rule: never describe a holding as protected through earnings, after-hours, pre-market, halted-market, geopolitical shock, or other binary catalyst solely because sell stop-losses exist. Avanza stop-losses are trigger-based controls, not guaranteed fills. Tight `Kurs` values can avoid bad normal-session fills but can fail, remain unfilled, or show `ERROR` if price gaps through trigger/order price. Treat any stop-loss status `ERROR` as unprotected for that slice until verified and replaced/deleted in a user-authorized live thread.

Tracker and buy-back review: one-share/tiny residual positions are decision markers, not automatic buy-back authority. If a sell stop has triggered or a manual sale occurred, classify the forward choice as rebuild, partial participation, deep residual, hold current exposure, or thesis-broken/no-reentry. Historical loss, missed upside, or the old sale price is diagnostic evidence only. Propose exact `Antal`/price/`Max ned`/`Kurs` only when thesis, event, technical, friction, duplicate, and capacity gates pass.

Whole-position and stop review per account: run `avanza_position_strategy_audit` after the live refresh and require every tracked account/orderbook to be `RECORDED` with zero holding, active-stop, regular-open-order, missing-plan, stale-plan, or registry drift. A fill or approved order change creates a review event and must not be silently rebaselined. Run `avanza_stoploss_strategy_audit` and require every active row to have `strategy_metadata_status=RECORDED`; missing, stale-mismatch, or unavailable metadata blocks a clean result and any mutation for that row. Then check `ERROR` rows, stop volume exceeding current holding or the exact approved tactical/profit-harvest `Antal`, retained-core violations, duplicate/conflicting stops, stale validity dates, missing stop-loss/order-book identifiers, stops that can sell below entry when intended as profit protection, too-tight noise-prone stops, too-wide crash-only stops, and open buy orders whose limit/trigger no longer matches the thesis. Call SELL-coverage helpers with exact strategy-classified targets for actionable rows; default/current-active-baseline output must not convert a mechanical full-holding diagnostic into a missing core SELL stop. `Holding - 1` is only a mechanical marker-preserving maximum, never the strategic SELL target. Retain at least `75%` of quality/core exposure and `50%` of high-beta/recovery exposure unless an explicit named decision overrides the floor. Recovered/current shares default to core and receive no automatic SELL. Both local strategy registries are evidence, never trade authorization.

Independent instrument strategy review: require every held instrument to have a named catalyst, current decision, add gate, sell/exit gate, thesis invalidation, risk-budget rule, factor/theme context, friction rule, loss-recovery rule, and next review. Generic “keep/monitor/review” recommendations are incomplete and block a clean result. Refresh official evidence and live market/account state when a gate is due; the strategy record is audit context, never mutation authority.

Active-order implementation consistency: when an exact pending-order implementation ledger exists, treat it as the active-row source for exact stop IDs, sides, counts, statuses, and modeled notional. Require every recovery, factor, capacity, and displacement artifact to use the same live-source timestamp and reconcile to that ledger. A stale estimated-order source, count/notional mismatch, or revived older keep/replace/cancel decision blocks a clean result. The ledger never authorizes mutation.

Open order monitoring: for every open buy order, verify current quote, day range/pre-market context if available, validity date, order size versus intended exposure, and whether the order is stale, too far below market, too aggressive, or conflicts with current capacity. A fill requires a fresh strategy classification; propose SELL protection only for an explicitly approved tactical/profit-harvest slice. Expected benefit must clear modeled commission, spread, FX, slippage, and recovery risk by at least `3x`, and optional growth must preserve at least `2%` post-conditional account-capital headroom unless lower-ranked capacity is displaced first.

Crypto-linked tracker monitoring: review active sell stops, buy-back orders, recent transactions, quote freshness, and whether sell/buy stops conflict. Do not cancel protection simply because the asset bounced; compare thesis, support/resistance, flows, rates, geopolitical/oil risk, and relevant market reopen effects. State clearly that avoiding all downside with Avanza stops cannot be guaranteed and may require accepting whipsaw/fill risk or reducing exposure.

Output behavior: if nothing material changed, return a quiet concise status. If action is needed, return separate compact sections per account with notable market/news/earnings changes, holdings requiring attention, active stop-loss/protection issues, open order review, and priority proposals with exact `Antal`, `Max ned`, `Kurs`, and rationale. Include IDs only when useful for user-approved repair. If a meaningful new lesson or checklist gap is discovered, propose an instruction/memory update for the live thread rather than silently changing trading behavior.
