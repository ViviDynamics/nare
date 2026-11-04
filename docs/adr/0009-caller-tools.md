# 9. Callers register their own tools, in-process or as a command

Status: Accepted (2026-11-04)

## Context

The tool set was fixed at the built-ins. A caller whose data lives somewhere
nare does not know had one way to put that data in front of the model: all of
it in the prompt, so the prompt grew with the data rather than the question.
Registration was the extension point the architecture already named.

MCP already lets a caller add tools from the command line (`--mcp-config`). It
needs a server that speaks the protocol and a client that keeps a connection,
which is more than a caller with one lookup wants to write. It stays the route
for servers that hold state.

## Decision

1. In-process, a caller passes `nare.Tool(name, description, input_schema, call)`
   as `tools=` to `nare.run` or `nare.step`. `call` may be sync or async. It
   returns a str, or any value, which is sent as JSON.
2. From the command line, `--command-tools FILE` declares tools as an argv per
   tool. The model's checked arguments arrive as JSON on stdin, and stdout is the
   result. This is the command line's equivalent of the callable, not a second
   protocol.
3. Caller tools enter the policy's external tools beside MCP tools. They take
   the same allowlist (`--tools`, or `Policy.tools` with `pending` for a name not
   yet registered), the same argument check before approval, the same approval
   seam (ADR 0004, unchanged), the same output cap and the same events.
4. A caller tool's failure is an error result, never a raised exception. A
   `ToolError` carries its message. Any other exception is `Type: message`.
5. Names are 1 to 64 letters, digits, `_` or `-`, not a built-in name, and never
   contain `__`. That keeps them apart from MCP's `alias__tool` names.
6. A command tool runs with nare's environment minus the provider credentials,
   as `bash` does, unless `--bash-credentials` is set.

## Consequences

A caller tool runs with the caller's privileges, and `--yes` approves it as it
approves `bash`. The approval seam is the only gate; the argument schema is a
check on shape, not on what the tool does.

An in-process call cannot be stopped by nare, so its timeout is the caller's.
A command call is killed at its timeout with its process group.

A command tool starts one process per call. A caller whose tool needs a warm
connection or state should use MCP instead.

The contract gains a `caller_tools` section and a `caller` key in the tool
result and output maps. Both are additive, so the contract version does not
change.
