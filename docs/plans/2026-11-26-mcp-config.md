# nare mcp add|list|remove and .mcp.json, like the other agent CLIs

Issue #329 (tracking: #331)

## Scope

In: `nare mcp add [--scope user|project] [--env K=V] [--header K=V] <name> --
<command...>` and `nare mcp add <name> --url <url> [--transport http]`, with
Claude Code's `-s`, `-e`, `-H`, `-t`; `nare mcp list [--json]`, `get`,
`remove`; `nare mcp trust [--revoke]`; the user (`$NARE_HOME/mcp.json`) and
project (`.mcp.json`) files in the `mcpServers` format; the person-facing forms
loading them, project servers only once trusted; `--mcp-config` reading the
`mcpServers` format; `--strict-mcp-config`; `nare mcp login` without
`--mcp-config`.

Out: Claude Code's `local` scope and `sse` servers (nare speaks Streamable
HTTP only).

## Assumptions

- The default scope is user, so `nare mcp add` never writes into a repository
  unless asked.
- Trust is bound to the `.mcp.json`'s SHA-256, so a repository that changes the
  file after it was trusted is asked about again. A "no" is not remembered.
- Without a terminal (`nare -p`, `nare exec`) nothing asks: untrusted project
  servers are skipped and named on stderr, and `nare mcp trust` trusts them.
- `${VAR}` expansion applies to the `mcpServers` format only, so a file in
  nare's own format means exactly what it did.
- `--strict-mcp-config` is a `nare run` flag (and so in the flag table), a
  no-op there, because `nare -p` and `nare exec` take `nare run`'s flags.

## Tasks

- [x] 1. A Claude Code `.mcp.json` reads as is; nare's format unchanged; sse refused: `tests/test_mcp_config.py`.
- [x] 2. `nare mcp add|list|get|remove|trust`, files and modes.
- [x] 3. Loading: user and trusted project in person-facing forms, untrusted skipped or asked, strict, `nare run` loading none.
