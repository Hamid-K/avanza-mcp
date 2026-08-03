# LLM Client Interoperability

Avanza-MCP uses one provider-neutral repository context and one local MCP
server command. Codex, Claude Code, and Gemini CLI can therefore work against
the same checkout, handoff, ledgers, logs, authenticated UI bridge, and MCP
tool catalog.

This does not transfer hidden model reasoning or chat history. Continuity is
provided by durable state: Git, the shared handoff, project documentation,
private instruction ledgers, runtime files, and logs.

## Shared Context Layout

The provider entry points are symbolic links to one canonical file:

```text
AGENTS.md  ----\
CLAUDE.md  -----+--> INSTRUCTIONS/PROVIDER_ENTRYPOINT.md
GEMINI.md  ----/
```

The canonical entry point tells every provider to read the same project
documentation and, when present, the ignored local handoff at
`INSTRUCTIONS/SESSION_HANDOFF.md`. Create the handoff from
`INSTRUCTIONS/SESSION_HANDOFF.template.md`.

Private trading instructions and ledgers remain ignored and local. The entry
point references them by their canonical paths rather than duplicating them
for each provider. Runtime logs and session files also remain in their existing
locations; provider-specific copies are not created.

## Switching Clients

1. Before leaving a substantial session, update
   `INSTRUCTIONS/SESSION_HANDOFF.md` with completed work, current branch and
   commit, remaining actions, verification, and unresolved risks.
2. Stop the current client. Concurrent model sessions are not required and can
   create conflicting edits or stale account assumptions.
3. Start the next client in this repository. Its provider entry point resolves
   to the same canonical instructions.
4. Verify the handoff against current Git status and runtime state. For account
   work, call `avanza_status` or `avanza_capabilities` and refresh broker state;
   never inherit a previous client's claim that data is current.

## Common MCP Transport

Codex, Claude Code, and Gemini CLI all support local stdio MCP servers, so they
use the same command:

```bash
uv run --project /ABSOLUTE/PATH/TO/avanza-mcp \
  python /ABSOLUTE/PATH/TO/avanza-mcp/avanza_cli.py mcp
```

The proxy supports newline-delimited and legacy `Content-Length` stdio framing.
It negotiates the supported initialized MCP revisions and also implements the
stateless MCP discovery path. There is no provider-specific MCP implementation.

The authenticated TUI or Web UI must be running with MCP enabled. It owns the
Avanza sessions; the stdio process only forwards tool calls through the local,
token-protected bridge.

## Client Registration

Replace `/ABSOLUTE/PATH/TO/avanza-mcp` in these commands.

### Codex

```bash
codex mcp add avanza-mcp -- \
  uv run --project /ABSOLUTE/PATH/TO/avanza-mcp \
  python /ABSOLUTE/PATH/TO/avanza-mcp/avanza_cli.py mcp
codex mcp get avanza-mcp
```

### Claude Code

```bash
claude mcp add --scope project avanza-mcp -- \
  uv run --project /ABSOLUTE/PATH/TO/avanza-mcp \
  python /ABSOLUTE/PATH/TO/avanza-mcp/avanza_cli.py mcp
claude mcp get avanza-mcp
```

### Gemini CLI

```bash
gemini mcp add --scope project --transport stdio avanza-mcp \
  uv run --project /ABSOLUTE/PATH/TO/avanza-mcp \
  python /ABSOLUTE/PATH/TO/avanza-mcp/avanza_cli.py mcp
gemini mcp list
```

Use project scope for reproducible per-checkout configuration. Use user scope
only when the same absolute checkout should be available from unrelated
directories.

## Compatibility And Safety

The stdio proxy currently supports:

- initialized MCP protocol versions `2024-11-05`, `2025-03-26`,
  `2025-06-18`, and `2025-11-25`;
- stateless discovery and per-request version metadata for `2026-07-28`;
- text tool results for all clients and structured results where the negotiated
  protocol supports them.

Client trust settings do not bypass Avanza-MCP safety. Read-only and paper mode
remain the defaults. Live broker mutations still require every server-side
gate, active-session authorization, explicit confirmation, and post-mutation
verification.

Clients that accept only remote streamable HTTP MCP cannot directly use this
local stdio command. Supporting those clients would require a separately
authenticated remote transport and is intentionally outside this local
interchangeability layer.
