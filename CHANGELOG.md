# Changelog

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
- Added project documentation, packaging metadata, and tests.
