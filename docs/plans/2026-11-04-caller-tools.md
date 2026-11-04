# Caller-registered tools

Issue #12

## Scope

In:

- `nare.Tool(name, description, input_schema, call)` and `nare.ToolError`, a
  public in-process registration. `call` takes the checked arguments and returns
  a str (any other JSON value is encoded). It may be sync or async. Raising
  `ToolError` ends that call with its message as an `is_error` result; any other
  exception becomes `Type: message` as an `is_error` result. Neither ends the run.
- `nare.run(..., tools=[...])` and `nare.step(..., tools=[...])`.
- `nare run --command-tools FILE`: a JSON object keyed by tool name. Each entry
  has `description`, `input_schema`, `command` (an argv, no shell) and an optional
  `timeout` (default 30 seconds). A call runs the command with the checked
  arguments as JSON on stdin, in the root when there is one. Stdout is the result.
  A non-zero exit, a timeout or an unreadable file is an error result.
- Caller tools obey the same allowlist as built-ins. With no `--tools` (or no
  `Policy`) every registered tool is offered. With an explicit allowlist, a
  caller tool is offered only when the allowlist names it.
- Caller tools go through the same approval seam, before which their arguments
  are checked against `input_schema` (unknown or missing parameters are refused
  before anybody is asked).
- Events: a caller tool's `tool_use` and `tool_result` events match a built-in's.
  Its result is kept, redacted and capped, like MCP results (`caller result: name`).
- `nare contract` describes the feature (additive: `caller_tools` section, the
  `content` and `tool_output` maps gain a `caller` key). `docs/contract.md`,
  `README.md` and ADR 0009 document it.

Out:

- Plugin discovery. Registration is explicit only.
- Timeouts for in-process callables. nare cannot stop a Python thread or coroutine,
  so a caller bounds its own function. The command form is bounded.
- Changing MCP behaviour, or argument checks for MCP tools (they stay unchecked).

## Assumptions

- Caller tools are a second source of `ExternalTool`s, next to MCP, not a new
  dispatch path. `Policy.external` already carries them.
- Allowlisting an unregistered name uses `Policy.pending`, the idiom the MCP
  docs already document (`Policy(pending=..., tools=...)`).
- A default run (no `--tools`, no `policy`) allows caller tools, as it allows
  MCP tools. Callers who pass no tools see no change.
- Names: `[A-Za-z0-9_-]{1,64}`, not a built-in name, no `__` (so a caller name can
  never equal an MCP `alias__tool` name). Duplicates are refused.
- Command tools run with nare's environment minus the provider credentials,
  as bash does, unless `--bash-credentials` is set.
- The command form is a process per call. It is not a long-lived server; that is
  what MCP is for.

## Tasks

- [x] 1. `Tool` validation: a bad name, a built-in name, `__`, an empty description, a non-object schema, a non-callable `call`, and a duplicate name each raise `ValueError` before a run starts.
- [x] 2. Dispatch: arguments are checked before approval (an unknown parameter is an `is_error` and approval is never asked); a `ToolError`, a raised exception, and a non-str result each become one ordinary result.
- [x] 3. `run(tools=...)` and `step(tools=...)`: a registered tool is offered and callable; a `Policy` allowlist that does not name it keeps it out of `schemas()` and refuses a call; `pending` allows an unregistered name to be named.
- [x] 4. Events: a caller tool gives one `tool_use` and one `tool_result` event with the same id, and a `progress` event labelled `caller result`.
- [x] 5. `--command-tools`: the file is parsed and validated before any model call (exit 2 on a bad file); a command's stdout is the result; a non-zero exit and a timeout are error results; `--tools` filters it like built-ins.
- [x] 6. `nare contract` and docs: `caller_tools` describes the feature; `content` and `tool_output` gain `caller`; `docs/contract.md`, `README.md` and ADR 0009 are updated.
