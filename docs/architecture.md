# Architecture

This project is intentionally small and explicit.

## Surfaces

- `avanza_cli.py` is the single user-facing entry point. It provides scriptable console subcommands and the Textual terminal UI via `python avanza_cli.py tui`.

The console and TUI surfaces currently call `avanza-api` directly. As the project grows toward MCP-style usage, shared account operations should move into a service module so CLI, TUI, and MCP tools use the same tested functions.

## Credentials

Credentials are entered at runtime. Password and TOTP fields are masked and must not be committed or pasted into issue logs, transcripts, or documentation.

## Trading Safety

Read operations may run after login. Mutating operations must remain explicit:

- dry-run by default where practical
- live stop-loss placement requires a confirmation action
- deletion requires explicit confirmation

Future order placement features should follow the same pattern.
