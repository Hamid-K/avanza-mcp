# Architecture

This project is intentionally small and explicit.

## Surfaces

- `avanza_cli.py` is the single user-facing entry point: a thin shim over the `avanza_mcp` package. It provides scriptable console subcommands, the Textual terminal UI via `python avanza_cli.py tui`, and the Web UI via `python avanza_cli.py web` (the two UIs are mutually exclusive per checkout, enforced by `.avanza_ui.lock`).
- `python avanza_cli.py mcp` is a provider-neutral stdio MCP proxy. It does not authenticate to Avanza itself; it forwards MCP tool calls to the localhost bridge started by the authenticated TUI or Web UI. Codex, Claude Code, and Gemini CLI use this same process and tool catalog.

## Package layout

The implementation lives in the `avanza_mcp` package:

- `core/` — the UI-agnostic **trading kernel** (`TradingKernel`): tenant
  sessions, caches, MCP bridge lifecycle and tool dispatch, snapshot
  providers, trading submission bodies, and refresh workers. Both front-ends
  are views over this kernel; `avanza_mcp.core` never imports `textual`
  (guard-tested). Hosts customize via seams (`write_log`, `write_mcp_log`,
  `on_state_changed`, `call_from_thread`, ...).
- `web/` — the FastAPI Web UI: token/cookie/CSRF auth, REST + WebSocket API
  over the kernel, and the no-build Vue 3 frontend under `web/static/`.

- `config.py` — constants, session/log file paths, choice tables, external URLs
- `models.py`, `utils.py`, `auth.py`, `stoploss_rules.py` — shared dataclasses, generic helpers, credentials/1Password/connect, stop-loss validation rules
- `rendering.py`, `records.py`, `market_data.py`, `paper.py`, `avanza_ext.py` — row builders and previews, payload normalization, quote/metadata/performance helpers, the paper-trading engine, private-API pokes and fee estimation
- `external/` — outbound integrations: generic HTTP (`http.py`), TradingView session and data (`tradingview_session.py`, `tradingview_data.py`), Zacks, and SEC/FRED/FMP/Polygon feeds (`feeds.py`)
- `mcp/` — the MCP tool catalog (`catalog.py`), the localhost HTTP bridge and session storage (`server.py`), and the stdio proxy (`proxy.py`)
- `tui/` — layout widgets plus the trading app (`app.py`), split across mixins: MCP snapshots, MCP bridge/dispatch, login, tenant sessions, trading actions, and data refresh
- `cli.py` — subcommands, argument parser, and `main()`

Within the package, functions that tests monkeypatch are always called through their defining module (`module.name(...)`, never `from module import name`), so a single patch point works everywhere.

The console and UI surfaces call `avanza-api` directly. MCP uses the UI-owned authenticated client through a local bridge so credentials and TOTP remain handled by the TUI or Web UI.

## Provider-Neutral Agent Context

`AGENTS.md`, `CLAUDE.md`, and `GEMINI.md` are symbolic links to
`INSTRUCTIONS/PROVIDER_ENTRYPOINT.md`. This prevents provider instructions from
drifting. The ignored local `INSTRUCTIONS/SESSION_HANDOFF.md` carries explicit
work-in-progress state between clients, while Git, the existing private ledgers,
runtime files, and logs remain authoritative in their original locations.

The MCP proxy accepts newline-delimited and legacy `Content-Length` stdio
framing. It negotiates initialized MCP protocol revisions and implements the
stateless discovery path for newer clients. See `docs/clients.md` for the
supported revisions and client registration commands.

Transaction history retrieval (`avanza_transactions`) is exposed on CLI and MCP as a read-only path for audit/review workflows (executed orders by default, optional broader transaction types, date filters, and an explicit `include_raw` payload for evidence preservation). Normalized and raw transaction rows are filtered to the requested exact account; the raw broker envelope structure is preserved while its transaction array is scoped and the response records source/returned/foreign-row counts. Runtime capability fingerprints must confirm raw support before a historical raw-source gate can close.

## Credentials

Credentials are entered at runtime. Password and TOTP fields are masked and must not be committed or pasted into issue logs, transcripts, or documentation.

## Trading Safety

The [strategy-review contract](strategy-governance.md) is adopted in agent
instructions, not a new kernel feature. Its four-axis economic state, campaign
floors, typed continuation, semantic freshness and lifecycle requirements have
an explicit implementation backlog. Current structural audits remain required
but must not be presented as enforcing these additional policy checks.

Position-protection contract revision
`2026-09-12.position-protection-v2` adds closed, expiring no-stop decision
evidence, dated broker-capability evidence for `NON_STOP_ELIGIBLE`, and exact
strategy SELL target/retained-core Antal. The registry remains version `1` so
legacy rows load, but missing new evidence stays fail-closed and is never
inferred from prose. Runtime reconciliation compares every active SELL target
to the same orderbook's exact live Antal; undercoverage, overcoverage, and a
token SELL without a calibrated target all require review. The bridge only
previews and persists local metadata. It cannot synthesize a broker order,
protected Antal, or mutation authority.

Read operations may run after login. Mutating operations must remain explicit:

- dry-run by default where practical
- live order and stop-loss placement require an explicit confirmation action
- live edits/deletions require explicit confirmation
- MCP starts read-only; the TUI `R/W` switch must be enabled before live MCP mutations are accepted
- live MCP mutations also require `confirm: true` in the tool arguments
- stop-losses are trigger-based controls, not guaranteed fills through after-hours, pre-market, halted, or fast-gap markets
- Avanza Stop-loss and Limit-on-Close are unavailable during its U.S. pre-market and after-hours windows; extended-hours orders require an explicit session selector and must fail closed while the MCP contract cannot represent and read it back
- catalyst gap risk must be handled through explicit sizing, trim, sell, hedge, or hold-and-accept decisions before relying on stop-loss rows

Future order placement features should follow the same pattern.
