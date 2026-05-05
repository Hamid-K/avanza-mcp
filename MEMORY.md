# Trading Assistant Memory

| Timestamp | Metadata |
|---|---|
| 2026-05-05 23:51 CEST | This file is the durable memory ledger for Avanza trading-assistant work. |
| 2026-05-05 23:51 CEST | Use it to preserve lessons, mistakes, strategy updates, and workflow changes across clean sessions. |
| 2026-05-05 23:51 CEST | Do not use it as live portfolio state. Account IDs, holdings, order IDs, stop-loss IDs, prices, and open orders must always be refreshed from Avanza MCP in the current session. |

## How to Use This File

| Timestamp | Rule |
|---|---|
| 2026-05-05 23:51 CEST | Read this file after `INSTRUCTIONS.md` and before trading analysis. |
| 2026-05-05 23:51 CEST | Treat entries as historical lessons and operating context, not as current account data. |
| 2026-05-05 23:51 CEST | Every lesson, example, experience, checklist item, or strategy note in this file must carry a timestamp or explicit as-of timestamp. |
| 2026-05-05 23:51 CEST | Update this file after meaningful trading sessions, user corrections, missed opportunities, strategy changes, automation changes, or MCP/tooling lessons. |
| 2026-05-05 23:51 CEST | Add new entries with local Stockholm time, newest first. |
| 2026-05-05 23:51 CEST | Keep account-specific details out of standing rules. If a historical example names an asset, label it as an example and verify current holdings live before applying it. |
| 2026-05-05 23:51 CEST | Never let this file override the live-thread safety rule: no live or paper order mutations unless the user explicitly authorizes them in the current thread. |

## Current Standing Lessons

| Timestamp | Topic | Standing Lesson |
|---|---|---|
| 2026-05-05 23:46 CEST | Account context | The selected Avanza account is the only active context. After any account switch, discard prior holdings/orders/stops/transactions and refresh live data. |
| 2026-05-05 23:46 CEST | Live action safety | Default to read-only. Live or paper order mutations require explicit live-thread authorization and MCP safety verification. |
| 2026-05-05 23:46 CEST | Code ownership | Do not modify repository code for trading tasks. Another agent owns code changes. Documentation may be edited only when explicitly requested. |
| 2026-05-05 23:48 CEST | Memory maintenance | Keep `MEMORY.md` current after meaningful sessions, corrections, missed opportunities, strategy updates, and automation changes. |
| 2026-05-05 23:46 CEST | Stop-loss convention | Sell-side protection usually covers `total holding - 1`, leaving one tracker share/unit. |
| 2026-05-05 23:46 CEST | Profit protection | Always calculate effective drop and whether the stop can sell below entry. Do not call a stop profit protection if it can sell below entry. |
| 2026-05-05 23:46 CEST | Post-stop workflow | A triggered sell stop creates a buy-back decision state. If thesis remains intact, evaluate staged/trailing re-entry for the sold volume. |
| 2026-05-05 23:46 CEST | Earnings prep | Earnings preparation is both opportunity and protection. Start several trading days ahead where possible, not only on report day. |
| 2026-05-05 23:46 CEST | Pre-position sizing | A tracker or `10-20k SEK` is low exposure. Strong positive setups should propose `30-40k SEK` exposure, ideally around `50k SEK`, subject to account cash, concentration, liquidity, volatility, and gap risk. |
| 2026-05-05 23:46 CEST | Earnings risk | Stop-losses may not protect through after-hours or pre-market gaps. Pair pre-report adds with staged sizing, gap-risk notes, and a harvest/protection plan after spikes. |
| 2026-05-05 23:46 CEST | Data quality | Use current Avanza MCP data and current market/news research. Mark stale, blocked, missing, or low-confidence sources clearly. |
| 2026-05-05 23:51 CEST | Timestamp discipline | Any memory remark without a timestamp loses context. Future updates must timestamp even examples, experiences, checklists, and strategy notes. |

## Timestamped Ledger

| Timestamp | Type | Note | Strategy / Action Update |
|---|---|---|---|
| 2026-05-05 23:51 CEST | Memory standard update | User required all memory remarks, including examples and experiences, to be timestamped. | Rewrote `MEMORY.md` so all standing lessons, examples, session notes, checklists, and guides include timestamps or as-of timestamps. |
| 2026-05-05 23:48 CEST | Memory update | User asked to fill this memory file with the current session's progress before context rotation/compaction. | Added the session action log below, including strategy changes, automation changes, MCP/tooling lessons, stop-loss/re-entry actions, and key research conclusions. |
| 2026-05-05 23:46 CEST | Strategy update | User clarified that earnings preparation must include pre-positioning, not only downside protection. Strong likely-positive reports should not be approached with only a tracker or tiny position. | Added explicit earnings pre-positioning protocol: scan several trading days ahead; run clue checklist; classify exposure as too low/adequate/too high; for strong setups propose staged adds toward `30-40k SEK`, ideally `50k SEK`, with matching `Max ned / Kurs / Antal` protection and gap-risk warning. |
| 2026-05-05 23:46 CEST | Mistake / lesson | Missed a significant upside opportunity by treating a strong earnings setup mainly as a stop-management problem after the move. Historical examples discussed: DigitalOcean and Fastly. These examples are not standing holdings or a permanent watchlist. | Future reviews must run a pre-earnings clue checklist: guidance vs consensus, estimate revisions, prior beat/guide quality, ARR/RPO/backlog/customer/usage signals, management pre-signals, product/customer/partnership news, peer read-throughs, sector moves, relative strength, volume, short interest/options-implied move where available. |
| 2026-05-05 23:46 CEST | Automation update | Existing recurring reviews needed to catch opportunity setups before report day. | Updated `avanza-pre-market-review` and `avanza-hourly-monitoring` prompts to include earnings pre-positioning, exposure adequacy checks, clue clusters, target SEK exposure proposals, staged entry logic, and gap-risk warnings. |
| 2026-05-05 23:46 CEST | Documentation update | Fresh sessions need one durable place for learned mistakes and strategy changes. | Created `MEMORY.md` and linked it from the master instruction/warm-up/operations docs. |
| 2026-05-05 23:46 CEST | Operating rule | Account switching caused context risk earlier in the workflow. | Reinforced that account-specific state never carries across account switches. Only market/asset research lessons can carry forward, and only after confirming the asset exists in the currently selected account. |
| 2026-05-05 23:46 CEST | Stop-loss lesson | Prior sell stops protected downside but created missed-upside risk when no buy-back plan followed the trigger. | Triggered sells must immediately create a buy-back decision state. If thesis remains intact, propose staged/trailing buy-back for the sold volume with explicit cap and renewed sell-side protection after fills. |
| 2026-05-05 23:46 CEST | Tooling lesson | MCP/TUI tool availability can differ between native Codex tool discovery and the stdio bridge. | Always verify `avanza_status`, selected account, safety mode, and tool availability first. If native tools are not exposed, verify `codex mcp get avanza_cli` and use the configured stdio/TUI bridge. |
| 2026-05-05 23:46 CEST | Tooling lesson | Open-order side/ID semantics were previously ambiguous enough to require developer feedback. | Use `avanza_open_orders_raw` before edit/cancel decisions when side/id mapping is unclear. Do not mutate uncertain orders. |
| 2026-05-05 23:46 CEST | Safety correction | User explicitly objected to code modification during trading-assistant work. | No code edits in trading tasks. If tool behavior needs improvement, write a prompt for the developer LLM instead of changing code. |

## Session Action Log - 2026-05-05

| Timestamp | Scope Note |
|---|---|
| 2026-05-05 23:51 CEST | These entries summarize historical work from the current long-running session. They are examples and context only. Before using any account, holding, order, stop, price, or transaction detail, refresh live Avanza MCP state. |

### Documentation and Operating Context

| Timestamp | Area | What Happened | Current Rule / Follow-Up |
|---|---|---|---|
| 2026-05-05 23:46 CEST | Warm-up continuity | Created `WARMUP.md` so a fresh Codex session can resume the Avanza trading workflow with the right safety rules and mental model. | Fresh sessions should read `INSTRUCTIONS.md`, `MEMORY.md`, and `WARMUP.md`, then verify MCP status and selected account before analysis. |
| 2026-05-05 23:46 CEST | Account switching | User corrected that every account switch changes the active context completely. | Treat selected account as the only live portfolio context. Do not carry holdings, orders, stop IDs, account IDs, or position sizes across account switches. |
| 2026-05-05 23:46 CEST | No hardcoding | User required instruction markdowns to avoid hardcoded account numbers or specific stocks as standing assumptions. | Standing rules remain account-agnostic and asset-agnostic. Historical examples may name assets but must be re-verified live. |
| 2026-05-05 23:46 CEST | Code ownership | User explicitly ordered not to touch/modify code and to revert any code changes if made. | For trading work, use tools as-is. If a tool needs improvement, ask the user and provide a developer-LLM prompt instead of editing code. |
| 2026-05-05 23:48 CEST | Memory maintenance | User requested a durable timestamped memory ledger. | Created `MEMORY.md`; update it after meaningful progress, corrections, missed opportunities, strategy updates, and automation changes. |
| 2026-05-05 23:51 CEST | Timestamp discipline | User required every memory remark, example, and experience to be timestamped. | Keep all future memory additions row-timestamped or section-as-of timestamped. Prefer row-level timestamps for anything historical. |

### Automation Work

| Timestamp | Automation | Historical Action | Current Intent |
|---|---|---|---|
| 2026-05-05 23:46 CEST | `avanza-pre-market-review` | Created/updated recurring heartbeat review for pre-market and earnings checks. Later upgraded it to all-account review with earnings pre-positioning and memory awareness. | Read-only proposal loop. Verify MCP/account state, review all visible accounts, check upcoming reports several trading days ahead, flag under-sized exposure, and propose staged adds/protection without mutating orders. |
| 2026-05-05 23:46 CEST | `avanza-hourly-monitoring` | Updated hourly monitoring prompt to include account-switching, stop coverage checks, re-entry states, earnings clue clusters, and memory awareness. | Read-only proposal loop. Surface actionable issues only; no order mutations. |
| 2026-05-05 23:46 CEST | Earnings pre-positioning | Automation prompts were expanded after the DigitalOcean/Fastly missed-opportunity discussion. | Future recurring reviews must ask: "Are we too small before a likely positive report?" not only "Are stops protective?" |

### MCP and Tooling Lessons

| Timestamp | Topic | Historical Finding | Current Rule |
|---|---|---|---|
| 2026-05-05 23:46 CEST | MCP bridge | `avanza_cli` was configured as a stdio MCP server. Native Codex Desktop discovery did not always expose callable `mcp__avanza_cli__...` tools, while the stdio/TUI bridge worked. | First verify `codex mcp get avanza_cli` if needed, then use the configured bridge path. |
| 2026-05-05 23:46 CEST | Safety status | Repeated checks found MCP enabled, paper trading enabled, live read/write off unless explicitly toggled. | Always call/read status first and report selected account, `read_write`, paper trading, and live mutation capability before analysis/action. |
| 2026-05-05 23:46 CEST | TradingView tools | TradingView authenticated session tooling was added/tested; saved session status showed configured/authenticated state. | Prefer authenticated TradingView tools when available; if stale or failing, report low confidence and fall back to other sources. |
| 2026-05-05 23:46 CEST | Source bundle | New tools included `data_source_status`, `signal_context_bundle`, `avanza_account_performance`, `avanza_open_orders_raw`, `avanza_orderbook_quotes`, and other analytical endpoints. | Use source health checks before high-confidence conclusions. Use raw open-order payloads when side/id semantics are unclear. |
| 2026-05-05 23:46 CEST | Developer prompts | User asked for prompts to pass to the MCP developer for missing/faulty tool behavior. | When blocked by MCP schema/behavior, provide a concise developer prompt rather than patching code. |

### Account Performance and P/L Interpretation

| Timestamp | Topic | Historical Question | Current Rule |
|---|---|---|---|
| 2026-05-05 23:46 CEST | Avanza web UI development figures | User asked whether one-year and since-start Avanza profit figures include deposits/withdrawals. | Use `avanza_account_performance` and transaction logs to separate account development from deposits/withdrawals when auditing performance. Do not infer deposits from chart labels alone. |
| 2026-05-05 23:46 CEST | Since-start P/L | User wanted "Since Start" available in the TUI P/L cycle and asked how performance compares after deposits. | For performance analysis, use since-start return/development plus deposit/withdrawal audit. Compare against relevant benchmarks only after the actual time window and cash flows are known. |
| 2026-05-05 23:46 CEST | Full audit tooling | User asked whether more MCP tools were needed for a full return audit. | Ideal tooling includes account performance by period, cash-flow/deposit/withdrawal series, transaction history, benchmark comparison, and time-weighted/money-weighted return calculations. |

### Stop-Loss and Re-Entry Strategy Updates

| Timestamp | Topic | Lesson / Action | Current Rule |
|---|---|---|---|
| 2026-05-05 23:46 CEST | Tracker convention | The user usually wants sell stops to protect total holding minus one unit. | Proposed `Antal` should normally equal `current holding - 1`; leave one tracker unless there is a stated reason not to. |
| 2026-05-05 23:46 CEST | Profit protection | Wide stops can fail to protect profit if effective drop exceeds the current gain. | Always calculate effective drop and compare to break-even drop from current price. |
| 2026-05-05 23:46 CEST | Post-stop re-entry | Historical examples showed stops can protect downside but miss upside if no buy-back review follows. | Any triggered sell stop creates a buy-back decision state for the sold amount if thesis remains intact. |
| 2026-05-05 23:46 CEST | Buy-back style | User asked about downward-tracing/gliding buy-back orders. | Prefer staged buy-side gliding orders when appropriate, after verifying current Avanza semantics. Use caps and do not stack conflicting fixed/gliding buy orders. |
| 2026-05-05 23:46 CEST | Earnings stops | Stops alone may not protect through earnings gaps. | For earnings, combine sizing, staged entries, trims, and post-spike harvest plans with stops. |

### Historical Live Actions From This Session

| Timestamp | Scope Note |
|---|---|
| 2026-05-05 23:51 CEST | These are historical examples from the session, not current state. Re-verify every account and order live before relying on them. |

| Timestamp | Context | Historical Action / Observation | Follow-Up Rule |
|---|---|---|---|
| 2026-05-05 23:46 CEST | Larger/company account example | Tightened or rebuilt stop ladders on several high-gain/high-move holdings. Examples discussed included DigitalOcean, Fastly, Intel, Gilat, Cloudflare, AMD, TSMC, Alphabet, and SoundHound at different points. | Future sessions must not assume these stops still exist. Pull `avanza_stoplosses` and reconcile `Antal`, `Max ned`, `Kurs`, stop IDs, and current holdings. |
| 2026-05-05 23:46 CEST | Personal account example | Adjusted protection for example holdings such as Fastly and Intel after switching accounts. | Account-specific stop plans must be recalibrated from that account's live position size and P/L. |
| 2026-05-05 23:46 CEST | Example buy orders | Historical buy actions included adding/re-entering examples such as Broadcom, Palo Alto, and Akamai in one account context. | Always verify `avanza_open_orders`, transactions, and filled status before making follow-up changes. |
| 2026-05-05 23:46 CEST | POET example | Reviewed recent POET transactions in a selected personal-account context. Historical interpretation: large sells had occurred well above later prices, so the main issue was staged buy-back rather than "missed" upside. A first-stage downward-following buy-back was later placed after explicit user authorization. | For future POET-like cases, calculate all staged buy-back SEK exposure before placing any stage; then place only the explicitly authorized stage. |
| 2026-05-05 23:46 CEST | Shopify example | Stop(s) triggered after earnings reaction; later bounce risk was reviewed. Historical conclusion: do not blindly chase; consider staged buy-back only if reclaim/stabilization supports thesis. | A stopped-out earnings mover needs both current quote and thesis review; compare stop-out price to current price before deciding whether upside was truly missed. |
| 2026-05-05 23:46 CEST | Palantir example | Strong reported fundamentals but negative price reaction; user has longer-term view but does not want excessive red. | For long-term holdings after volatile earnings reactions, balance thesis strength against valuation/technical weakness; re-enter/add on reclaim or calmer base, not automatically on headline beat. |
| 2026-05-05 23:46 CEST | SoundHound example | Spike was investigated as likely short squeeze/AI momentum/pre-earnings positioning rather than clearly fresh company-specific proof. | For heavily shorted volatile names before earnings, treat spike risk and give-back risk symmetrically: protect recovery, but do not over-interpret squeeze as fundamental confirmation. |
| 2026-05-05 23:46 CEST | XBT Ether example | User wanted upward chase without losing current profits or triggering on normal spikes. | For volatile trackers, use noise-tolerant ladders and review intraday 15m/1h behavior before tightening. |

### Market and Earnings Research Notes From This Session

| Timestamp | Scope Note |
|---|---|
| 2026-05-05 23:51 CEST | These are historical research conclusions only. Re-check dates, prices, and news live. |

| Timestamp | Asset / Theme Example | Historical Research Read | Future Checklist Impact |
|---|---|---|---|
| 2026-05-05 23:46 CEST | DigitalOcean example | Major positive report and spike exposed that we had not pre-positioned enough before a strong clue cluster. | Run pre-earnings opportunity scan several days before reports; do not wait until post-spike stop tightening. |
| 2026-05-05 23:46 CEST | Fastly example | Strong momentum before its report raised the same pre-positioning issue. | If evidence is favorable and exposure is low, propose meaningful pre-report exposure plus protection. |
| 2026-05-05 23:46 CEST | AMD example | Q1 report showed strong revenue/EPS and guidance; AI/data-center demand and guidance quality were key. | For semis/AI infrastructure names, check hyperscaler commitments, product roadmaps, guidance, margins, peer read-throughs, and supply/capacity signals. |
| 2026-05-05 23:46 CEST | Shopify example | Q1 results were strong on headline growth, but guidance/market reaction drove downside. | For high-growth software/commerce names, compare headline beat with forward guide and market expectations. |
| 2026-05-05 23:46 CEST | Palantir example | Very strong growth/guidance can still sell off when valuation/expectations are extreme. | Earnings analysis must include expectations and valuation/technical setup, not just whether numbers beat. |
| 2026-05-05 23:46 CEST | Astera/Akamai/Coinbase/SoundHound examples | Upcoming/recent report dates mattered for stop and exposure review. | Always verify exact timing, before-open/after-close status, and whether stops can work through the relevant trading session. |

### Current Open Process Items

| Timestamp | Item | What To Preserve |
|---|---|---|
| 2026-05-05 23:46 CEST | Future clean session startup | Read `INSTRUCTIONS.md`, `MEMORY.md`, and `WARMUP.md`; verify MCP status and selected account; refresh live portfolio/stops/orders/transactions. |
| 2026-05-05 23:46 CEST | Future earnings preparation | Look ahead several trading days; flag under-sized exposure; propose staged adds toward meaningful SEK exposure when evidence is strong; pair with explicit protection and gap-risk explanation. |
| 2026-05-05 23:46 CEST | Future post-market reviews | After high-gain days, tighten or split stops for profit harvest, especially where gains are unusually large or event-driven. |
| 2026-05-05 23:46 CEST | Future post-stop reviews | For every triggered sell, create a buy-back decision state and assess same-volume staged/trailing re-entry if thesis remains intact. |
| 2026-05-05 23:46 CEST | Future tool blockers | Do not edit code. Ask the user and/or write a developer-LLM prompt with exact missing schema/behavior. |

## Pre-Earnings Review Checklist

| Timestamp | Scope Note |
|---|---|
| 2026-05-05 23:46 CEST | Run this for every current holding with an upcoming report in the next several trading days, and for any watchlist candidate the user explicitly asks to evaluate. |

| Timestamp | Step | Requirement |
|---|---:|---|
| 2026-05-05 23:46 CEST | 1 | Verify exact report date and whether it is before open or after close. |
| 2026-05-05 23:46 CEST | 2 | Compare company guidance with current consensus. |
| 2026-05-05 23:46 CEST | 3 | Check recent estimate revisions, rating changes, and target changes where available. |
| 2026-05-05 23:46 CEST | 4 | Review prior-quarter beat/miss and whether guidance was raised, held, or cut. |
| 2026-05-05 23:46 CEST | 5 | Check key business indicators: ARR, RPO, backlog, usage, customer count, capacity, margins, or the metric that actually drives the business. |
| 2026-05-05 23:46 CEST | 6 | Look for management pre-signals, product releases, customer wins, partnerships, pricing changes, capacity expansion, or demand commentary. |
| 2026-05-05 23:46 CEST | 7 | Check peer read-throughs from companies that already reported. |
| 2026-05-05 23:46 CEST | 8 | Check sector/theme factors and whether the move is company-specific or broad beta. |
| 2026-05-05 23:46 CEST | 9 | Check TradingView trend, relative strength, volume expansion, and technical extension. |
| 2026-05-05 23:46 CEST | 10 | Check short interest and options-implied move when available. |
| 2026-05-05 23:46 CEST | 11 | Decide whether evidence supports adding, holding, trimming, or avoiding exposure. |
| 2026-05-05 23:46 CEST | 12 | If adding is justified, propose target SEK exposure, staged entry plan, maximum chase price, and matching stop-loss/profit-harvest plan. |

## Exposure Decision Guide

| Timestamp | Evidence Quality | Default Proposal | Exposure Guidance |
|---|---|---|---:|
| 2026-05-05 23:46 CEST | Strong positive clue cluster | Build before report if account risk allows | `30-40k SEK`, ideally near `50k SEK` |
| 2026-05-05 23:46 CEST | Moderate positive setup | Starter/add with tight risk controls | `10-30k SEK` |
| 2026-05-05 23:46 CEST | Mixed or unclear setup | Keep current exposure or tracker; focus protection | No new exposure by default |
| 2026-05-05 23:46 CEST | Bearish or thesis-damaged setup | Avoid adding; protect, trim, or exit proposal | No new exposure |

## Post-Spike Harvest Guide

| Timestamp | Scope Note |
|---|---|
| 2026-05-05 23:46 CEST | Apply this after a positive report or major spike. |

| Timestamp | Step | Requirement |
|---|---:|---|
| 2026-05-05 23:46 CEST | 1 | Refresh live quote, position size, P/L, active stops, and open orders. |
| 2026-05-05 23:46 CEST | 2 | Decide whether the spike is thesis-confirming or likely short-squeeze/overextension. |
| 2026-05-05 23:46 CEST | 3 | Tighten or split stop-losses for high gains while leaving upside participation. |
| 2026-05-05 23:46 CEST | 4 | If the position was a short-term earnings trade, propose partial harvest. |
| 2026-05-05 23:46 CEST | 5 | If the thesis materially improved, keep a core/tracker position and protect the excess gain with ladders. |
| 2026-05-05 23:46 CEST | 6 | Recheck stop volumes so they never exceed current holding minus one tracker unit. |
