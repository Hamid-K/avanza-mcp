# Changelog

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
