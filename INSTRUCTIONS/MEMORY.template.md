# Trading Assistant Memory Template

Use this file as a public-safe template for the private local file `INSTRUCTIONS/MEMORY.md`.

Rules:
- Keep `MEMORY.md` local-only (ignored by git).
- Do not store account IDs, order IDs, stop-loss IDs, usernames, hostnames, file paths, or personal notes here.
- Keep entries generalized and strategy-focused.

## Format

Newest-first table:

| Timestamp (Stockholm) | Topic | Observation | Operational rule |
|---|---|---|---|
| YYYY-MM-DD HH:MM CEST | Example: Stop-loss validity | Some markets rejected long triggered-order validity windows. | Default `order_valid_days = 1` unless explicitly proven safe. |
| YYYY-MM-DD HH:MM CEST | Example: Multi-account routing | Selected account may not represent all loaded sessions. | Always call `avanza_sessions` and scope reads with `tenant_session_id` + `account_id`. |
| YYYY-MM-DD HH:MM CEST | Example: Event risk | Earnings after close can gap through stops. | Run event-first gate before stop changes; treat stops as normal-session controls only. |

## Suggested Sections (optional)

- Workflow lessons
- MCP/tooling lessons
- Risk-management lessons
- Data-quality lessons
- Strategy adjustments

## Redaction Checklist

Before committing template updates:
- No account-specific numbers.
- No instrument-specific live action logs with exact quantities/prices.
- No personal directory paths.
- No credentials/session tokens.
