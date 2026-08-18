# Changelog

## Unreleased

## 0.2.36 - 2026-08-19

- Added a fail-closed validator for the latest variable-size buy-back coverage
  universe. It derives all account, one-share, low-exposure, exit, state, and
  percentage totals from exact rows and rejects copied percentage vectors
  across different instruments.
- Preserved the August 6 44-row ledger as a historical stamped snapshot while
  linking the latest dynamic live coverage separately into the objective audit;
  fixed 44/18/26/42/43 counts are no longer treated as current truth.
- Added regression coverage for variable universe sizes, stale filename
  selection, count drift, cross-instrument vector reuse, and fail-closed goal
  linkage. All artifacts remain review-only and grant no broker authority.

## 0.2.35 - 2026-08-17

- Preserved raw BUY reachability failures while adding a separate plan-aware
  governance result for explicit named exceptions, locked residuals, secondary
  review inventory, and dormant review inventory.
- Kept missing or active-BUY-stale plans, `REPAIR_REQUIRED`, unclassifiable
  rows, unresolved broker cleanup, and plan-versus-distance contradictions fail
  closed. Explained raw issues remain visible and never become practical
  coverage or trade authority.
- Added focused regression coverage and documented the distinction between
  mechanical reachability and governed review inventory.

## 0.2.34 - 2026-08-14

- Added a required, explicit protection classification and instrument-specific
  reason to every reviewed account-position plan. Audits now fail closed on
  missing, invalid, contradictory, or `REPAIR_REQUIRED` protection states and
  distinguish strict fingerprint equality from governance-complete intentional
  holding-only drift.
- Added a runtime capability and MCP contract revision for the protection
  schema, including exact dry-run visibility and restart-durable registry
  coverage without granting broker or paper authority.
- Added a fail-closed twice-daily governance-streak ledger and verifier. A
  completion claim now requires ten chronological eligible reviews spanning
  paired morning/evening windows across five regular sessions, both exact
  accounts, every required gate, fresh evidence, zero blockers, and live
  authorization off.

## 0.2.33 - 2026-08-14

- Added an explicit exception-preserving semantic update mode to
  `avanza_position_strategy_register_batch`. It retains the reviewed live
  fingerprint and holding-only audit exception while updating plan semantics,
  and refuses missing or changed exception metadata, no actual holding drift,
  every stop/open-order mismatch, and any attempted rebaseline.
- Validated dry runs through the same atomic registry preparation path used by
  confirmed private metadata writes and exposed the post-write recorded
  holding in the preview. Added the
  `position_strategy_exception_preserve` runtime capability flag so clients
  can verify the loaded contract. Broker and paper state remain untouched.
- Added restart-durability, schema, exact Shopify-shaped, and fail-closed
  regression coverage.

## 0.2.32 - 2026-08-14

- Enforced requested transaction date bounds locally after broker retrieval so
  nearest-prior, future, missing-date, and malformed-date rows cannot leak into
  exact transaction reads, same-day sold-slice reconstruction, raw evidence,
  instrument state, or recent-fill protection review.
- Added regression coverage for broker responses that ignore exact day and
  since-date filters.

## 0.2.31 - 2026-08-14

- Fixed `sec_filings_recent` HTTP 403 failures by separating SEC's declared
  application/contact identity from the browser user agent used by TradingView.
- Cached the SEC ticker-to-CIK index for 24 hours, serialized SEC requests below
  the fair-access ceiling, and added actionable identity and rate-limit errors.
- Scoped `avanza_transactions(include_raw=true)` raw transaction envelopes to
  the requested exact account while preserving broker structure and exposing
  raw source/scope counts; added a multi-account regression test.
- Added read-only event-protection triage for material moves and
  event-sensitive holdings without turning review flags into broker authority.
- Added fail-closed metadata for intentional holding-only drift, while keeping
  stop, order, and other exposure drift unresolved.
- Added `avanza_transactions(include_raw=true)` evidence preservation,
  `contract_features`, and `mcp_contract_revision` runtime fingerprints so
  historical raw-source gates can distinguish a loaded bridge from an older
  long-running process.
- Enforced matching `strategy_intent` and `strategy_reason` metadata for live
  and paper stop workflows, including stop deletion reason checks.
- Bounded TradingView pre-open batches and preserved per-symbol failures for
  large or mixed-exchange reviews.
- Added read-only artifact builders and validators for buy-back coverage,
  catalysts, forward KPIs, transactions, scheduler state, strategy scope,
  portfolio controls, and objective completion; expanded the verification
  workflow and regression tests accordingly.
- Established the repository-wide rule that agents require explicit
  current-thread user approval before modifying code, tests, scripts,
  configuration, documentation, or automation.

## 0.2.30 - 2026-08-04

- Added one canonical provider-neutral agent entry point, linked from Codex,
  Claude Code, and Gemini CLI instruction files, plus an ignored shared session
  handoff for durable cross-client continuity.
- Expanded the stdio MCP proxy to negotiate legacy initialized protocol
  revisions and the stateless discovery protocol while preserving both newline
  and `Content-Length` framing.
- Documented registration, switching, safety, and context boundaries for all
  three supported local MCP clients.

## 0.2.29 - 2026-08-04

- Tightened recovery reachability semantics: fixed BUY rows within `8%` are practical participation, `8-15%` rows are secondary review only, and rows beyond `15%` are deep review. Secondary- or deep-only recovery now fails closed.

## 0.2.28 - 2026-08-04

- Added a read-only frozen-starting-holdings attribution replay using authenticated Avanza daily charts, current holdings, transactions, dividends, and external cash events.
- The replay reconstructs start volumes from exact account-scoped trades and fails closed on missing order-book identity, unavailable prices, unsupported non-zero cash events, or truncated transaction history.

## 0.2.27 - 2026-08-04

- Added a read-only authenticated `avanza_instrument_chart` MCP tool for normalized OHLC history by order book, period, and resolution so portfolio attribution does not substitute third-party or guessed prices.
- Corrected numeric Avanza chart timestamps to Europe/Stockholm calendar dates, preventing one-day cash-flow and transaction misalignment during CET/CEST.
- Added a fail-closed `avanza_account_cost_attribution` replay that preserves Avanza's cash-flow-adjusted actual path while separately adding back posted commission and explicitly modeled FX; it reports monthly and worst-day damage without granting trade authority.

## 0.2.26 - 2026-08-03

- Added a read-only recovery-reachability audit that flags fixed BUY rows beyond a configurable review distance, overly wide reversal gliders, and deep-only designs without treating mechanical thresholds as entry advice.
- Made deep residuals fail closed as practical recovery coverage unless a separate reachable participation path exists; every result remains analysis-only and carries no broker mutation or trade authority.
- Replaced the obsolete crash-only ladder convention with instrument-calibrated reachable participation, secondary residual, and dormant event/reversal tranches, plus an economically constrained one-third SELL framework for sufficiently large positions.

## 0.2.25 - 2026-08-03

- Added a read-only, fail-closed semantic audit across the instrument master, account clean sheet, and private position registry, including exact 65-instrument/107-position coverage, live-fingerprint checks, source hashes, and field-level contradiction evidence.
- Added a guarded analysis-only semantic synchronizer that requires matching live fingerprints and a documented reason before copying later reviewed account semantics into the clean sheet and master; it never changes the broker or private registry and never grants trade authority.
- Added an explicit account-level semantic-plan schema to the strategy master so account-specific gates, statuses, theses, risk rules, and next reviews remain auditable without forcing aggregate prose onto both accounts.

## 0.2.24 - 2026-08-03

- Made `avanza_sold_today_buyback_state` fail closed on economic sold-slice recovery: pre-existing or generic BUY stops and unattributed regular BUY orders remain visible as conditional exposure but no longer conceal a missing recovery row.
- Added per-stop recovery attribution evidence and regression coverage for the ETH failure mode where an older named-exception deep BUY was incorrectly counted against a later same-day sale.

## 0.2.23 - 2026-08-03

- Canonicalized legacy `EPA` and broker `Euronext Paris` exchange labels to TradingView's current `EURONEXT` scanner symbol so Airbus participates in complete portfolio evidence batches.

## 0.2.22 - 2026-08-03

- Normalized common U.S. TradingView market aliases to the scanner's canonical `america` route so portfolio pre-open and regular-session batches no longer fail with HTTP 404 when callers supply `US`.
- Marked partial or total TradingView batch failures unsafe for execution instead of reporting failed evidence as usable.

## 0.2.21 - 2026-08-03

- Tightened the reusable portfolio instruction template with thesis-invalidation position sizing, volatility-scaled risk caps, and lower rolling turnover/full-friction brakes to prevent stop/rebuy churn from replacing mid-term investment intent.
- Added a non-sensitive portfolio-governance audit trail covering per-instrument theses, intended exposure, catalysts, entry/exit/invalidation gates, risk and friction rules, dated reviews, factor/capacity controls, and transaction-lifecycle KPIs; private account snapshots and generated analysis remain excluded from Git.
- Added an exact current-stop reconciliation layer that supersedes stale historical row arrays, requires every live conditional order to map to durable strategy intent and a named next action, and keeps unresolved broker hygiene explicitly fail-closed rather than bypassing MCP controls.
- Added a reusable immutable live-order protocol: urgent complete tuples bypass redundant analysis but never hard caps, exact quantities, account scope, order-kind fields, same-account readback, or immediate per-tenant authorization revocation; complaints and missed-price observations remain analysis-only.
- Required MCP stop-loss deletion to carry strategy intent and reason matching the exact durable live-stop fingerprint, with pre-delete target verification, post-delete absence readback, and fail-closed metadata retention when deletion cannot be verified.
- Added instrument-currency enrichment to stop-loss previews so absolute USD/EUR/etc. trigger and child prices are no longer mislabeled as SEK.

## 0.2.20 - 2026-07-31

- Added account-scoped, file-backed position and stop-loss strategy registries with atomic persistence, exact broker fingerprints, drift/mismatch detection, and fail-closed audit states.
- Added MCP audit and batch-registration tools for durable position plans and stop-loss intent metadata without granting broker mutation authority.
- Required explicit strategy intent and rationale for stop-loss creation and editing across MCP, TUI, and Web flows, including fixed-price enforcement for deep residual recovery orders.
- Reworked SELL coverage checks around explicit strategy-classified targets instead of automatically treating nearly an entire holding as disposable stop volume.
- Expanded portfolio, stop-loss, and order snapshots with strategy metadata, exact exposure reconciliation, and improved instrument/orderbook identity handling.
- Fixed the MCP stdio proxy to use newline-delimited JSON-RPC required by the MCP specification while retaining compatibility with legacy `Content-Length` clients.
- Added registry, strategy-validation, coverage-policy, Web API, kernel, and MCP transport regression tests and updated operating documentation.
- Excluded local portfolio-analysis output and temporary runtime artifacts from Git tracking.

## 0.2.19 - 2026-07-24

- Suppressed expected MCP bridge `BrokenPipeError`/connection-reset tracebacks when a local client cancels or times out while a response is being written.
- Prevented the bridge from attempting a second HTTP error response on an already-closed client socket and added compact `client_disconnected` session-log events.

## 0.2.18 - 2026-07-13

- Replaced the Web Research Candidates generic partial-enrichment warning with per-source attempted/succeeded/failed health counts on the source filters.
- Moved the research-only disclaimer into the existing `Research input only` tooltip and logged exact source failures to the timestamped Web session log for diagnosis.

## 0.2.17 - 2026-07-13

- Turned the Web Research Candidates source labels into instant client-side filters with selected state, per-source counts, and no network request when toggling TradingView heatmap, TradingView technicals, Zacks, or optional sources.

## 0.2.16 - 2026-07-09

- Fixed false Web/MCP Avanza tenant-session expiry by confirming generic Avanza `403 Forbidden` errors against the baseline account overview endpoint before marking a session expired.
- Prevented optional performance-chart endpoint failures from pausing live refresh for an otherwise healthy authenticated session.

## 0.2.15 - 2026-07-09

- Added a Zacks quote-feed fallback so `zacks_scrape_symbol`, TradingView hot lists, and Web `Research candidates` can still show Zacks Rank when Zacks HTML pages are blocked by bot protection.

## 0.2.14 - 2026-07-09

- Fixed Web `Research candidates` rows with blank `Why` values by adding base heatmap/mover reasons for every row, even when deeper TradingView/Zacks enrichment is not run.
- Made Zacks enrichment failures/no-data results visible as row-level warnings instead of silently marking Zacks as a successful source.

## 0.2.13 - 2026-07-09

- Fixed Web saved 1Password login profiles so selecting a saved Personal/DarkCell profile submits that profile's exact item/vault and does not reuse a stale previously selected profile ID.
- Added saved-profile normalization to repair duplicate browser-local profile IDs created by earlier modal state leakage.

## 0.2.12 - 2026-07-09

- Added bounded, cached Zacks rank enrichment to the Web UI TradingView hot/list rows and exposed the result in the TradingView Lists table.

## 0.2.11 - 2026-07-09

- Fixed the Web UI Performance chart period buttons so period changes force a fresh account-specific load, ignore stale responses, and redraw the chart cleanly.

## 0.2.10 - 2026-07-09

- Fixed the Web UI `Research candidates` panel returning no rows when TradingView's first scanner slice was dominated by OTC/outlier symbols before local exchange filtering.
- Added a safe Avanza market-movers fallback for research candidates when TradingView heatmap data is empty or shape-shifted.

## 0.2.9 - 2026-07-08

- Added live-refresh behavior to Web Completed Orders and Transactions overlays: they reload after relevant WebSocket order/portfolio/stop-loss updates and poll every 10 seconds while open as a fallback.

## 0.2.8 - 2026-07-08

- Reload active Web overlay panels after session or account dropdown changes so Orders, Transactions, Stop-Losses, TradingView lists, and Research candidates reflect the newly selected context.

## 0.2.7 - 2026-07-08

- Added a visible Web Transactions `P/L SEK` column backed by Avanza's transaction result field, with gain/loss coloring for realized trade damage checks.

## 0.2.6 - 2026-07-08

- Added a full-page Web `Stop-Losses` overlay for configured live and paper stop-losses, with refresh, edit, and guarded cancel actions.
- Added browser-local saved 1Password login profiles for primary and extra-session Web logins; profiles store only the item name, optional vault, and display label.

## 0.2.5 - 2026-07-08

- Disabled browser caching for all local Web UI static assets to avoid stale Vue modules after Web UI updates.
- Added compact Web transaction load/failure events to the app/session logs with account, date range, type filters, and fetched/returned row counts.

## 0.2.4 - 2026-07-08

- Fixed Web Transactions filtering so non-order transaction rows such as dividends, deposits, withdrawals, and unknown/service rows are not discarded after being fetched.
- Hardened transaction account matching for Avanza payloads that provide `accountName` or top-level account fields instead of nested `account.id`.

## 0.2.3 - 2026-07-08

- Fixed the Web Transactions/Completed Orders overlay so blank date fields default to the past calendar month, both in the visible date inputs and in `/api/transactions` backend calls.

## 0.2.2 - 2026-07-08

- Added a Web UI `Research candidates` overlay next to TradingView lists. It assembles read-only source-ranked stock candidates from TradingView movers/technicals and Zacks rank/analysis summaries, with optional FMP analyst history when `FMP_API_KEY` is configured.
- Added `/api/recommendations/stocks`, a bounded research aggregation endpoint with per-symbol source errors, source provenance, transparent scores, and a clear research-only disclaimer.
- Added Web API/static tests covering research candidate aggregation and toolbar/overlay wiring.

## 0.2.1 - 2026-07-08

- Fixed Web UI completed-order history filtering so `BUY,SELL` transaction filters are parsed once and sent to Avanza with valid enum values.
- Restored Web UI CSRF tokens after authenticated page reloads via `/api/auth/me`, so mutating requests do not fail after refresh.
- Improved Web UI live data freshness: login, account switching, and live refresh now update portfolio, ongoing orders, and stop-loss panes together.
- Reworked the Web dashboard layout:
  - moved Activity and MCP Live logs directly under Ongoing Orders,
  - moved Orders, Transactions, TradingView lists, Order, and Stop-Loss actions into a second top-toolbar row,
  - removed the fixed bottom-right floating action row,
  - added persisted splitters for main/side panes, portfolio/ongoing/log panes, and Activity/MCP Live logs.
- Simplified Web MCP live authorization into a compact warning strip and removed duplicate tick boxes; authorizing live MCP trading now also disables paper mode.
- Removed the browser confirmation popup when toggling paper mode in the Web UI.
- Fixed Web Transactions/Completed Orders table rendering so API rows with title-case fields display real dates, accounts, types, descriptions, and amounts instead of blank `-` cells; Transactions now requests all Avanza transaction categories.
- Made Web TradingView lists degrade to public TradingView scanner movers when authenticated custom-list scraping is unavailable, so the overlay still presents TradingView data without Playwright/profile setup.
- Added focused Web API/static regression tests for transaction filters, CSRF reload recovery, stale order/stop-loss refreshes, toolbar placement, log scrolling, and live authorization UX.

## 0.2.0 - 2026-07-07

- Restructured the 16.7k-line `avanza_cli.py` monolith into the `avanza_mcp` package (config, domain modules, external integrations, MCP server, TUI); the root file is now a thin shim and all documented invocations keep working.
- Extracted a UI-agnostic trading kernel (`avanza_mcp/core`): tenant sessions, caches, MCP bridge + tool dispatch, snapshot providers, trading submission bodies, and refresh workers shared by every front-end.
- Added a full Web UI (`python avanza_cli.py web`): dark single-page trading console with portfolio + live WebSocket updates, order/stop-loss tickets (dry-run → single-use review nonce → typed PLACE), guarded cancellations, multi-tenant sessions with re-auth, MCP management (bridge/R-W/live-arming, token + proxy command, streaming log), a dedicated Paper workspace, TradingView lists, performance charts, and orders/transactions history.
- Web security: 127.0.0.1-only bind, startup access token → HttpOnly SameSite=Strict cookie, double-submit CSRF header, Origin validation, strict CSP with SRI-pinned CDN assets (offline vendor fallbacks committed).
- The TUI and Web UI are mutually exclusive per checkout via a pid lock; both manage the same MCP bridge and session-file contract.
- New dependencies: fastapi, uvicorn, websockets, rich (previously transitive).

## 0.1.12 - 2026-07-02

- Optimized TradingView pre-open batch snapshots to use one scanner request for normal multi-symbol calls, with per-symbol fallback/error isolation only for missing rows.
- Cached TradingView unsupported scanner fields per market to avoid repeated field-negotiation retries.
- Added a short per-account Avanza read cache for MCP portfolio, stop-loss, and open-order list pulls to reduce repeated full-list requests during focused workflows.
- Reduced quote polling overhead by deduplicating orderbook IDs, coalescing rapid repeated quote refreshes, and skipping remote metadata enrichment for price-only field projections.

## 0.1.11 - 2026-07-02

- Added TradingView pre-open MCP snapshots:
  - `tv_preopen_symbol_snapshot` for one symbol,
  - `tv_preopen_batch_snapshot` for ordered batch reviews with per-symbol errors.
- Added read-only `avanza_tv_preopen_portfolio_bundle` to merge Avanza position/protection state with TradingView pre-open technical and extended-hours context.
- Improved `tv_scrape_heatmap` with exchange, OTC, market-cap, price, volume, sector/industry, premarket, and sort filters for cleaner U.S. trading reviews.
- Hardened `signal_context_bundle` so TradingView/source failures are returned under `errors`, and added `symbols` batch input support.
- Documented the TradingView pre-open workflow and local MCP bridge fallback path for agents when direct `tv_*` tool exposure is missing.

## 0.1.10 - 2026-06-18

- Added per-tenant, per-account snapshot caching for positions, stop-losses, and open orders.
- Refactored inactive multi-session refresh to update tenant caches in the background without activating or visibly switching TUI sessions/accounts.
- Updated active-session refresh so one full Avanza fetch refreshes cached data for all accounts in the selected tenant while rendering only the selected account.
- Account switching now paints from cached account data immediately, then live refresh fills in fresh quote/status data.
- Hardened background refresh worker shutdown/inflight cleanup and added regression coverage for invisible background refresh behavior.

## 0.1.4 - 2026-05-26

- Fixed critical live stop-loss default/config bug for triggered-order validity:
  - changed default `order_valid_days` from `8` to `1`,
  - applied consistently across CLI, TUI, MCP, and paper stop-loss preview paths.
- Added stop-loss validity safety guardrails:
  - dry-run/preview now warns when `order_valid_days > 1` can fail on foreign/non-SEK instruments,
  - live non-SEK/foreign stop-loss placement/edit is blocked when `order_valid_days > 1`.
- Expanded stop-loss request previews/logging to include:
  - trigger valid-until,
  - triggered order valid-days,
  - derived triggered-order expiry (`if triggered today`).
- Added focused tests for stop-loss defaulting/safety and updated docs for `Ogiltigt giltighetsdatum` failure handling.

## 0.1.3 - 2026-05-04

- Added TUI bottom-right update indicator with automatic GitHub release/tag checks against `Hamid-K/avanza-mcp`.
- Added outdated-version warning state with blinking highlight in the status bar.
- Added configurable update-check controls:
  - `AVANZA_UPDATE_CHECK_ENABLED` (`1`/`0`)
  - `AVANZA_UPDATE_CHECK_INTERVAL_SECONDS`
  - `AVANZA_UPDATE_CHECK_TIMEOUT_SECONDS`
  - `AVANZA_UPDATE_BLINK_INTERVAL_SECONDS`
  - `AVANZA_GITHUB_REPO`
- Added version comparison and GitHub update-check tests.

## 0.1.2 - 2026-05-04

- Added runtime app version management from `pyproject.toml` and exposed it in:
  - TUI title/header (`Avanza vX.Y.Z` and window title),
  - MCP initialize `serverInfo.version`,
  - MCP status payload (`app_version`),
  - CLI `--version`.
- Added hard `Reload TUI` process restart button for code-reload workflows.
- Added TradingView custom list monitoring (`tv_auth_custom_lists`) and TUI `TradingView Lists` view with list switching and live refresh.
- Moved TradingView session cookie storage to macOS Keychain by default with metadata dotfile + fallback to file mode.
- Moved Avanza MCP bridge session token storage to macOS Keychain by default with metadata dotfile + fallback to file mode.

## 0.1.1 - 2026-04-30

- Updated stop-loss defaults to auto-fill maximum allowed `valid_until` (today + 90 days) when omitted.
- Standardized stop-loss triggered-order `valid_days` default to `8` across CLI, TUI, and MCP schemas.
- Expanded docs to cover regular order (`orders`) CLI flows and current TUI P/L cycle timeframes (`1D/1W/1M/1Y/Total`).
- Renamed project metadata/docs title to `Avanza-MCP`.
- Hardened `.gitignore` for local state and runtime logs that can contain account/order/session metadata.

## 0.1.0 - 2026-04-28

- Added interactive CLI for account overview, portfolio positions, stock search, and stop-loss management.
- Added Textual TUI for portfolio and stop-loss viewing plus stop-loss dry-run/live placement.
- Added TUI account enumeration and account switching.
- Added masked password and TOTP entry.
- Replaced raw console/TUI API payload output with human-readable tables and summaries.
- Improved console help with examples, safety notes, and detailed stop-loss option guidance.
- Fixed TUI startup by avoiding a Textual logger name collision and added a headless TUI smoke test.
- Redesigned the TUI around a temporary login screen, top-bar account switching, live selected-account refresh, position state table, combined stop-loss/open-order table, and a cleaner stop-loss entry panel with selects and switches.
- Tightened TUI button styling to use compact one-line controls instead of Textual's default bulky beveled buttons.
- Added TUI resize handling so layout and selected-account data refresh after terminal size changes.
- Added gentle per-cell highlighting for changed live position metrics instead of highlighting entire rows.
- Added a searchable stop-loss holding selector populated from the selected account portfolio, with owned share counts and automatic volume prefill.
- Made live position cell highlights directional: muted green for positive changes and muted red for negative changes.
- Added a draggable TUI pane divider between positions and stop-loss/open-order tables.
- Rendered stop-loss relative values as `%` in UI, console output, docs, and help.
- Widened the TUI account bar, defaulted account selection to the largest account, and added colored account stats.
- Added explicit `SEK`/`%` units to stop-loss activity prices.
- Added a portfolio stock column showing whether Avanza reports real-time quote data for the instrument.
- Added clickable TUI table-header sorting with repeated clicks toggling ascending/descending order.
- Resolved missing real-time quote statuses from Avanza market/orderbook/instrument detail endpoints with a short cache.
- Rendered real-time status as green/yellow dot indicators and fixed TUI pane drag resizing.
- Added TUI-managed MCP mode with a stdio proxy command, read-only default, optional R/W toggle, and a dedicated MCP activity log.
- Preserved selected table rows across live refreshes when the selected row still exists.
- Registered `avanza_cli` with Codex MCP config while preserving existing MCP servers, and documented ChatGPT's remote-MCP limitation.
- Split the TUI top bar into separate account and action rows so account stats, buttons, and MCP switches do not crowd or clip each other.
- Hardened MCP stdio startup so Codex can initialize and list tools even before the authenticated TUI session file exists.
- Added MCP live snapshots for polling-based auto-trading loops.
- Added read-only-safe paper stop-loss tools with persisted local paper session state.
- Added a right-side TUI Active Trades panel for live and paper orders.
- Added timestamped JSONL session, app, MCP, and trading logs under `avanza-cli/logs/`.
- Added regular buy/sell order support across CLI, TUI, MCP, and paper trading.
- Made Active Trades resizable and expanded its columns for order ids, order-book ids, and validity timestamps.
- Reworked the TUI top panel into colored account metric cards, grouped action controls, compact labeled toggles, and a live clock/market countdown.
- Renamed stock-position table headers from `Instrument` to `Stock`.
- Added project documentation, packaging metadata, and tests.
