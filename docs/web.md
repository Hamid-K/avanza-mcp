# Web UI

`python avanza_cli.py web` serves a local single-page trading console with
feature parity to the TUI: portfolio, order/stop-loss tickets with dry-run
and typed confirmation, cancel flows, multi-tenant Avanza sessions with
re-authentication, MCP bridge management, paper trading (including a
dedicated Paper workspace), TradingView lists, and orders/transactions
history.

The Web UI and the TUI are **mutually exclusive**: both acquire
`.avanza_ui.lock` at startup and refuse to start while the other is
running (stale locks from dead processes are reclaimed automatically).

## Running

```bash
python avanza_cli.py web                 # default port 8787, opens the browser
python avanza_cli.py web --port 9000 --no-browser
```

At startup the server prints a one-time access token (also written to
`.avanza_web_session.json`, chmod 600). Paste it into the browser login
form. The server only ever binds `127.0.0.1`; reach it from another machine
with an SSH tunnel (`ssh -L 8787:127.0.0.1:8787 host`).

## Security model

- **Access token → cookie session.** The startup token is exchanged once
  for an HttpOnly, SameSite=Strict session cookie. Failed attempts are
  rate-delayed.
- **CSRF double-submit.** Every mutating request must echo the session
  value in the `X-Avanza-Web-Token` header; Origin and Host are validated
  against the local bind. The WebSocket handshake validates Origin too.
- **Strict CSP.** `default-src 'none'`; scripts/styles from self plus the
  two pinned jsdelivr CDN files (Vue, lightweight-charts) with subresource
  integrity hashes. No inline scripts. Offline fallback copies are
  committed under `avanza_mcp/web/static/vendor/` — swap the import map
  and script src in `index.html` to use them.
- **Trading gates mirror the TUI's human path.** Paper mode (default ON)
  routes tickets to the local paper ledger. Live placement requires paper
  mode off **and** the exact typed `PLACE` (cancel: `CANCEL`) validated
  server-side. Additionally — a web-only hardening the TUI doesn't have —
  placement is two-step: the dry-run response carries a single-use,
  120-second `review_id`, and the place call executes the **stored**
  reviewed payload, so a blind one-shot POST cannot trade and what you
  reviewed is exactly what runs.
- **MCP gates are separate.** The MCP R/W toggle and per-session
  live-trading authorization (typed `LIVE`) gate MCP tool calls only,
  exactly as in the TUI; mutating MCP calls additionally need
  `confirm: true` per call.

## MCP from the web

The MCP panel manages the same bridge the TUI manages: an opt-in localhost
HTTP bridge with an ephemeral token, written to `.avanza_mcp_session.json`
with the same contract, consumable by the same `python avanza_cli.py mcp`
stdio proxy. One kernel, one bridge — web-managed MCP is identical to
TUI-managed MCP.

## Endpoint overview

| Area | Endpoints |
|---|---|
| Auth | `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me`, `GET /api/meta` |
| Sessions | `POST /api/sessions` (login / extra / re-auth), `GET /api/sessions`, `POST /api/sessions/{id}/activate`, `DELETE /api/sessions/{id}`, `DELETE /api/sessions` |
| Data | `GET /api/accounts`, `POST /api/accounts/{id}/select`, `GET /api/portfolio`, `GET /api/orders/open`, `GET /api/stoplosses`, `GET /api/transactions?from_date&to_date&types`, `GET /api/search?q=`, `GET /api/quote/{order_book_id}`, `GET /api/performance?period=`, `GET /api/market/status`, `POST /api/refresh` |
| Trading | `POST /api/orders/dry-run`, `POST /api/orders/place`, `POST /api/stoplosses/dry-run`, `POST /api/stoplosses/place`, `POST /api/orders/cancel`, `POST /api/paper/mode` |
| Paper / TV | `GET /api/paper/state`, `GET /api/tv/lists?list_id=` |
| MCP | `GET /api/mcp/status`, `GET /api/mcp/log`, `POST /api/mcp/bridge`, `POST /api/mcp/read-write`, `POST /api/mcp/live-trading` |
| Live updates | `GET /ws` — channels: portfolio, sessions, orders, stoplosses, paper, mcp_status, mcp_log, notice, login_progress, update_check |

## Manual smoke checklist

1. `python avanza_cli.py web --no-browser` → token printed; browser login
   with a wrong token is rejected, right token unlocks.
2. Add an Avanza session (credentials + TOTP or 1Password) — staged
   progress streams in the modal; portfolio, metrics, and clock populate.
3. Paper mode ON: place an order via the ticket (dry-run → review →
   create) — it appears in the Paper tab and Active Stop-Losses (for
   stop-losses); cancel it from the table.
4. MCP tab: enable bridge → `.avanza_mcp_session.json` appears; run
   `python avanza_cli.py mcp` in another terminal and call `avanza_status`
   from an MCP client; toggle R/W and watch the log pane.
5. Start `python avanza_cli.py tui` while the web server runs — it must
   refuse with a message naming the web process.
6. DevTools network tab: no requests other than self and cdn.jsdelivr.net.
