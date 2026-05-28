# Avanza Multi-Account Hourly Monitoring (Template)

Public-safe template for local/private:
- `INSTRUCTIONS/AUTOMATION_HOURLY_MONITORING.md` (ignored by git)

## Prompt

Run a concise read-only Avanza hourly monitoring pass across every loaded/visible Avanza account in authenticated TUI/MCP sessions.

Start by verifying:
- MCP health/status
- read/write and paper/live flags
- loaded tenant sessions
- account list per tenant session

Use scoped reads:
- call `avanza_sessions`
- call `avanza_accounts` per tenant session
- read each account with explicit `tenant_session_id` + `account_id`

Use canonical tool names:
- `avanza_open_orders`
- `avanza_stoplosses`
- `avanza_transactions`

Read-only policy:
- do not create/edit/delete/cancel live or paper orders
- do not enable live mutation mode
- return proposals only

Per-account checklist:
- portfolio/positions
- active stop-losses
- open/ongoing orders
- recent transactions
- quote freshness
- event/catalyst scan (before-open, after-close, near-term)

Risk policy:
- stops are normal-session controls; they do not guarantee protection through earnings/after-hours gaps
- treat `ERROR` stop rows as unprotected until repaired in an explicitly authorized live session

Output style:
- compact summary when no material change
- if action needed: concise per-account sections with priority proposals and explicit rationale
- no raw payload dumps
