# Terminal outcomes a caller can branch on

Issue #8

## Scope

In: every terminal `(status, stop_reason)` pair names one documented cause,
and `nare contract` lists the stop reasons with the kind each one is, so a
caller tells a run that ran out of room from one that broke without reading
prose.

Out: automatic retry with a larger budget (the caller's policy, per the
issue). Timeout and interrupt reasons, which are #143, and the tool call cap,
which is #147; both add rows to the same table.

## What is already delivered

The output cap already ends `error` with `stop_reason` `max_tokens`, and a
provider exception ends `error` with a null `stop_reason`, so the issue's
first case can be branched on today. The gaps are the error paths that report
a provider stop reason, or the wrong nare one, for a cause nare recognised:

| Path | Today | After |
| --- | --- | --- |
| Reasoning with no answer | `error` / `end_turn` | `error` / `no_answer` |
| `--session` write failed after the run | `error` / the last turn's reason (`end_turn` on a finished run) | `error` / `session_write` |
| A library transport's discovery (`context_window`, `prices`) raised | `error` / `mcp`, "MCP connection failed" | `error` / null, as any provider exception |
| MCP startup or cleanup failure | `error` / `mcp`, absent from the stop reason table | unchanged, now in the table |

## Assumptions

- New stop reason values are additive and keep contract version 2, as the
  version policy states ("New stop reasons ... keep the contract version").
- Null keeps its documented meaning (no completed response: the provider or
  nare raised). Replacing it with a named value would change what an existing
  value means, which bumps the version; the kind `failure` names it instead.
- Kinds: `finished` (the model ended the run: `end_turn`, `stop_sequence`,
  `tool_use`), `limit` (a limit ended it and the task is unjudged:
  `max_tokens`, `max_turns`, `context`, `budget`), `answer` (the model gave no
  acceptable answer: `refusal`, `schema_violation`, `no_answer`) and `failure`
  (something broke, so nothing was checked: null, `mcp`, `session_write`).
- The kind lives in `nare contract` and the docs, not on the result line: the
  result keeps its shape, and a caller reads the table once.

## Tasks

- [x] 1. `nare contract` lists the stop reasons with kind and statuses: a
  contract test fails until `describe()["stop_reasons"]` exists and matches
  the doc table row for row.
- [x] 2. Reasoning with no answer ends `no_answer`: the existing loop test
  asserts the stop reason and fails on `end_turn`.
- [x] 3. A failed `--session` write ends `session_write`: the existing CLI test
  asserts it and fails on `end_turn`.
- [x] 4. A library transport whose discovery raises ends with a null stop
  reason and no MCP wording: a loop test fails on `mcp`.
- [x] 5. Every error path the loop and CLI produce lands in the table: a test
  drives each one and checks its pair against `describe()`.
- [x] 6. Docs: the stop reason table in `docs/contract.md` gains the kind
  column and the new rows; README's contract paragraph names the table.
