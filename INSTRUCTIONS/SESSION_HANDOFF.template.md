# Provider-Neutral Session Handoff

Copy this file to `INSTRUCTIONS/SESSION_HANDOFF.md` for local use. The local
handoff is intentionally ignored by Git because it may contain private runtime
context. Do not put credentials, tokens, cookies, TOTP values, or unnecessary
personal/account data in it.

## Session

- Updated at: `YYYY-MM-DD HH:MM TZ`
- Updated by: `Codex | Claude | Gemini | human`
- Role: `developer | trading assistant | analysis`
- Branch/commit: `branch @ commit`

## Objective

Describe the active objective in one or two sentences.

## Completed

- List completed work and authoritative file or commit references.

## In Progress

- List partially completed work and its exact state.

## Next Actions

1. List the next concrete action.

## Verification

- Tests/checks run:
- Result:
- Checks still required:

## Runtime Freshness

- MCP/TUI/Web state checked at:
- Account or market data freshness:
- Required revalidation before action:

Runtime details here are a handoff hint only. The receiving provider must
refresh live state before account conclusions or mutations.

## Artifacts And Logs

- Link only the files or compact log ranges needed to continue.

## Decisions, Risks, And Blockers

- Record explicit decisions and unresolved blockers without hidden reasoning.
