# Architecture

This project is intentionally small and explicit.

## Surfaces

- `avanza_cli.py` is the single user-facing entry point: a thin shim over the `avanza_mcp` package. It provides scriptable console subcommands and the Textual terminal UI via `python avanza_cli.py tui`.
- `python avanza_cli.py mcp` is a stdio MCP proxy. It does not authenticate to Avanza itself; it forwards MCP tool calls to the localhost bridge started by the authenticated TUI.

## Package layout

The implementation lives in the `avanza_mcp` package:

- `config.py` — constants, session/log file paths, choice tables, external URLs
- `models.py`, `utils.py`, `auth.py`, `stoploss_rules.py` — shared dataclasses, generic helpers, credentials/1Password/connect, stop-loss validation rules
- `rendering.py`, `records.py`, `market_data.py`, `paper.py`, `avanza_ext.py` — row builders and previews, payload normalization, quote/metadata/performance helpers, the paper-trading engine, private-API pokes and fee estimation
- `external/` — outbound integrations: generic HTTP (`http.py`), TradingView session and data (`tradingview_session.py`, `tradingview_data.py`), Zacks, and SEC/FRED/FMP/Polygon feeds (`feeds.py`)
- `mcp/` — the MCP tool catalog (`catalog.py`), the localhost HTTP bridge and session storage (`server.py`), and the stdio proxy (`proxy.py`)
- `tui/` — layout widgets plus the trading app (`app.py`), split across mixins: MCP snapshots, MCP bridge/dispatch, login, tenant sessions, trading actions, and data refresh
- `cli.py` — subcommands, argument parser, and `main()`

Within the package, functions that tests monkeypatch are always called through their defining module (`module.name(...)`, never `from module import name`), so a single patch point works everywhere.

The console and TUI surfaces call `avanza-api` directly. MCP uses the TUI-owned authenticated client through a local bridge so credentials and TOTP remain handled by the TUI.

Transaction history retrieval (`avanza_transactions`) is exposed on CLI and MCP as a read-only path for audit/review workflows (executed orders by default, optional broader transaction types and date filters).

## Credentials

Credentials are entered at runtime. Password and TOTP fields are masked and must not be committed or pasted into issue logs, transcripts, or documentation.

## Trading Safety

Read operations may run after login. Mutating operations must remain explicit:

- dry-run by default where practical
- live order and stop-loss placement require an explicit confirmation action
- live edits/deletions require explicit confirmation
- MCP starts read-only; the TUI `R/W` switch must be enabled before live MCP mutations are accepted
- live MCP mutations also require `confirm: true` in the tool arguments
- stop-losses are trigger-based controls, not guaranteed fills through after-hours, pre-market, halted, or fast-gap markets
- catalyst gap risk must be handled through explicit sizing, trim, sell, hedge, or hold-and-accept decisions before relying on stop-loss rows

Future order placement features should follow the same pattern.
