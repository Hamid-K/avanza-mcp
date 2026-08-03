# Avanza-MCP Agent Entry Point

This is the canonical provider-neutral entry point for repository agents.
`AGENTS.md`, `CLAUDE.md`, and `GEMINI.md` resolve to this file so Codex,
Claude, and Gemini receive the same operating context.

## Select The Role

- Repository sessions default to the **developer** role for implementation,
  review, testing, documentation, and release work. Developer sessions do not
  place, edit, or cancel live or paper trades.
- Use the **trading assistant** role only when the user explicitly assigns that
  role in the current session. Trading sessions must load and follow the full
  private instruction stack below before account analysis or mutation.
- A request sent to the wrong role must be identified before acting. Preserve
  the shared handoff state and direct the request to the intended role.

## Shared Context

Start every session by reading the files that apply to the selected role.

All roles:

1. `INSTRUCTIONS/SESSION_HANDOFF.md`, when present. This is the provider-neutral
   work-in-progress handoff. It may be stale and never overrides live state.
2. `README.md` for supported workflows and canonical MCP tool names.
3. `docs/architecture.md`, `docs/development.md`, and `docs/operations.md` as
   needed for the task.
4. Current Git branch, status, and recent history before editing.

Trading assistant role only:

1. `INSTRUCTIONS/INSTRUCTIONS.md` - private operating contract and safety rules.
2. `INSTRUCTIONS/MEMORY.md` - durable lessons, never current account state.
3. `INSTRUCTIONS/TRACKER_STATE.md` - private working ledger, requiring live
   MCP refresh before use.
4. `INSTRUCTIONS/PRIORITY_ACTION_PLAN.md` - unresolved work and blockers.
5. `INSTRUCTIONS/SCHEDULER.md` - review schedule, never mutation authority.
6. `INSTRUCTIONS/AUTOMATION_HOURLY_MONITORING.md` when the task concerns the
   monitoring automation.

Files ending in `.template.md` are public examples. They are not live context.
If a private file is absent, report that context is unavailable rather than
substituting its template.

## Handoff Contract

- Before pausing, switching providers, or ending substantial work, update
  `INSTRUCTIONS/SESSION_HANDOFF.md` with the current objective, completed work,
  remaining steps, verification results, branch/commit, referenced artifacts,
  and unresolved risks.
- Keep the handoff concise. Link to authoritative files instead of copying large
  logs, payloads, portfolios, or instruction documents into it.
- Record facts, not hidden reasoning. Never store credentials, session tokens,
  passwords, TOTP values, cookies, or raw personal data in the handoff.
- A receiving provider must verify Git and runtime state instead of assuming the
  previous provider's process, MCP connection, or data freshness survived.

## MCP Contract

- Every supported client starts the same local server command:
  `python avanza_cli.py mcp` (or `uv run python avanza_cli.py mcp`).
- The authenticated TUI or Web UI owns Avanza sessions. It must be running with
  MCP enabled before account tools can execute.
- Begin account work with `avanza_status` or `avanza_capabilities`, then inspect
  `avanza_sessions` and use explicit `tenant_session_id` plus `account_id` when
  account identity matters.
- Read-only and paper mode are the defaults. Live broker mutations require all
  server-side gates, explicit current-session user authorization, and exact
  post-mutation readback. Client trust or auto-approval does not grant trading
  authority.
- Do not guess tool names. Use the current MCP `tools/list` result or the
  canonical tool table in `README.md`.

## Local State And Logs

- Private ledgers, MCP/session metadata, strategy registries, `output/`, `tmp/`,
  and `avanza-cli/logs/` remain local and ignored by Git.
- Logs are diagnostic history, not current broker state. Read only the relevant
  recent slice and redact account or order details from public artifacts.
- `.avanza_stoploss_strategy.json` and `.avanza_position_strategy.json` are
  audit metadata. They do not authorize a trade.
- Never commit local session files, credentials, cookies, account identifiers,
  portfolio snapshots, or private instruction contents.

## Verification

- Preserve unrelated working-tree changes.
- Keep implementation, tests, public templates, docs, and versioning aligned.
- Run the repository verification described in `docs/development.md` before a
  commit or handoff, and record any tests that could not be run.
