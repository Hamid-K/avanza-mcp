# Changelog

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
