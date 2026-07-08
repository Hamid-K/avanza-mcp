# Changelog

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
