# Avanza Multi-Account Hourly Monitoring (Template)

Public-safe template for local/private:
- `INSTRUCTIONS/AUTOMATION_HOURLY_MONITORING.md` (ignored by git)

## Prompt

Run a concise read-only Avanza hourly monitoring pass across every loaded/visible Avanza account in authenticated TUI/MCP sessions. First read `INSTRUCTIONS/INSTRUCTIONS.md`, `INSTRUCTIONS/MEMORY.md`, and `INSTRUCTIONS/TRACKER_STATE.md` if they exist. Memory contains lessons and strategy updates only, not live portfolio state. Tracker state is a working snapshot that must be refreshed from MCP before action.

Start by verifying Avanza MCP health/status, available capabilities/tools, TradingView/auth/session status if exposed, read/write state, paper trading state, live mutation state, account list, and all active tenant sessions. Use the multi-account workflow: call `avanza_sessions` and `avanza_accounts`, then read each relevant account explicitly by `tenant_session_id` and `account_id`. Do not rely on the selected/default account except as fallback context. Do not assume any account ID, account name, holding, stop-loss ID, order ID, or prior account-specific state.

Use canonical Avanza MCP names only:
- `avanza_open_orders`
- `avanza_open_orders_raw`
- `avanza_stoplosses`
- `avanza_transactions`
- `avanza_live_snapshot`
- `avanza_realtime_quotes`
- `avanza_account_performance`

This automation is read-only and proposal-only. Do not create, edit, cancel, delete, or place live or paper orders. Live read/write being enabled is not authorization for mutations. Any proposed action must be returned for the live thread/user to approve.

For each account, refresh portfolio/positions, active stop-losses, open/ongoing orders, paper orders if available, recent transactions, buying power/cash where exposed, realtime quotes, and relevant market/news/earnings/crypto-linked context. Keep output compact and avoid raw tool dumps.

Event-first earnings and catalyst gate: scan every holding for same-day, after-close, before-open, next-session, and near-term catalysts. For every upcoming or recent report, unusual move, or materially news-sensitive holding, assess the entity before proposing trim/add/protection: exact report timing; prior guidance versus consensus; estimate revisions; analyst target/rating changes; prior-quarter beat/miss and guide quality; product/customer/partnership announcements; management pre-signals; peer and sector read-throughs; macro/geopolitical/FX/oil/rates sensitivity; TradingView trend, relative strength, extension, and volume; short interest/options-implied move where available. Classify as bullish pre-position, mixed hold, overextended protect/harvest, or bearish reduce. Do not default to merely protecting or trimming; strong clue clusters can justify staged pre-positioning before the event.

Critical gap-risk rule: never describe a holding as protected through earnings, after-hours, pre-market, halted-market, geopolitical shock, or other binary catalyst solely because sell stop-losses exist. Avanza stop-losses are trigger-based controls, not guaranteed fills. Tight `Kurs` values can avoid bad normal-session fills but can fail, remain unfilled, or show `ERROR` if price gaps through trigger/order price. Treat any stop-loss status `ERROR` as unprotected for that slice until verified and replaced/deleted in a user-authorized live thread.

Tracker and buy-back review: one-share/tiny residual positions are active buy-back decision markers, not ignorable leftovers. If a sell stop has triggered or a manual sale occurred, create a buy-back decision state: thesis intact or broken, current price versus sale price, whether to use fixed buy levels or gliding `FOLLOW_DOWNWARDS`, proposed `Antal`/price/`Max ned`/`Kurs` if re-entry is attractive, and whether the asset is already too extended to chase. Do not miss fast recoveries/spikes merely because only a tracker remains.

Stop-loss/protection review per account: check `ERROR` rows, stop volume exceeding current holding, underprotected holdings, stops that sell all instead of leaving one tracker share/unit, single blunt stops where split ladders better match volatility, duplicate/conflicting stops, stale validity dates, missing stop-loss/order-book identifiers, stops that can sell below entry when intended as profit protection, too-tight noise-prone stops, too-wide crash-only stops, and open buy orders whose limit/trigger no longer matches the thesis. Apply the tracker convention: proposed sell stops should usually protect total holding minus one share/unit, unless there is a documented reason.

Open order monitoring: for every open buy order, verify current quote, day range/pre-market context if available, validity date, order size versus intended exposure, and whether the order is stale, too far below market, too aggressive, or needs a paired protection proposal if filled. Pay special attention to new purchases from recent sessions; if filled, propose immediate protection but do not mutate.

Crypto-linked tracker monitoring: review active sell stops, buy-back orders, recent transactions, quote freshness, and whether sell/buy stops conflict. Do not cancel protection simply because the asset bounced; compare thesis, support/resistance, flows, rates, geopolitical/oil risk, and relevant market reopen effects. State clearly that avoiding all downside with Avanza stops cannot be guaranteed and may require accepting whipsaw/fill risk or reducing exposure.

Output behavior: if nothing material changed, return a quiet concise status. If action is needed, return separate compact sections per account with notable market/news/earnings changes, holdings requiring attention, active stop-loss/protection issues, open order review, and priority proposals with exact `Antal`, `Max ned`, `Kurs`, and rationale. Include IDs only when useful for user-approved repair. If a meaningful new lesson or checklist gap is discovered, propose an instruction/memory update for the live thread rather than silently changing trading behavior.
