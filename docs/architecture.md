# Architecture

This project is intentionally small and explicit.

## Surfaces

- `avanza_cli.py` is the single user-facing entry point. It provides scriptable console subcommands and the Textual terminal UI via `python avanza_cli.py tui`.
- `python avanza_cli.py mcp` is a stdio MCP proxy. It does not authenticate to Avanza itself; it forwards MCP tool calls to the localhost bridge started by the authenticated TUI.

The console and TUI surfaces call `avanza-api` directly. MCP uses the TUI-owned authenticated client through a local bridge so credentials and TOTP remain handled by the TUI.

## Credentials

Credentials are entered at runtime. Password and TOTP fields are masked and must not be committed or pasted into issue logs, transcripts, or documentation.

## Trading Safety

Read operations may run after login. Mutating operations must remain explicit:

- dry-run by default where practical
- live order and stop-loss placement require an explicit confirmation action
- live edits/replacements/deletions require explicit confirmation
- MCP starts read-only; the TUI `R/W` switch must be enabled before live MCP mutations are accepted
- live MCP mutations also require `confirm: true` in the tool arguments

Future order placement features should follow the same pattern.
