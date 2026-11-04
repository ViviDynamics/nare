# Budget: cap the tool calls in a run with --max-tool-calls

Issue #147

## Scope

In: `nare run --max-tool-calls N` and `run(max_tool_calls=N)`, counted per
invocation over every call nare runs (built-in and MCP). At the cap a call
gets an error result telling the model to answer, and does not run. A model
that calls again after being told ends the run `error` with `stop_reason`
`tool_calls`. `result.budget` reports the cap and the calls used.

Out: per-tool caps (a caller allows fewer tools instead), and the TUI, which
has no unattended caller to bound.

## Assumptions

- A call counts when nare runs it, which is when it reaches `dispatch`
  (approval and argument checks included, since those are nare doing work on
  the call's behalf). A call nare never runs does not count: one refused at
  the cap, an `ask` refused for not being alone, one from a truncated turn.
- The cap is per invocation, as `--max-turns` is: a resume gets the full
  allowance again. It is not saved in the session, unlike `--budget-tokens`.
- One grace turn. The first turn that meets the cap has its extra calls
  refused, and the model is told to answer. If a later turn of the same
  invocation calls a tool again, those calls are refused too and the run ends
  `error`/`tool_calls`; an answer instead ends the run as it would have.
  Tools stay offered on the grace turn, so "keeps calling" is a real case and
  not one only a nonconforming provider can reach.
- A token or USD budget stop in the same turn wins, as it already does over
  the turn limit: the stop reason names the first limit checked.
- `Session.tool_calls` counts the calls a session has run over its life, as
  `turns` and `retries` do: a session field with a default of 0, so older
  files load. `result.budget` gains `tool_calls` (the cap, present only when
  set, as `tokens` and `usd` are) and `used_tool_calls` (this invocation's
  count, which is what the cap counts).
- `tool_calls` is kind `limit`. Flag, stop reason, budget keys and session
  field are additive and keep contract version 2.

## Tasks

- [x] 1. With `--max-tool-calls 3`, a fourth call gets an error result and
  does not run (a `write` that would create a file leaves no file).
- [x] 2. A model that answers after the refusal ends `done`, with
  `used_tool_calls` 3 and `tool_calls` 3 in `result.budget`.
- [x] 3. A model that calls again after the refusal ends `error`/`tool_calls`
  with an `error` event, and its calls are answered and not run.
- [x] 4. The cap counts per invocation: a resume gets the full allowance, and
  `Session.tool_calls` keeps the lifetime count.
- [x] 5. MCP calls count against the cap.
- [x] 6. Invalid values never start; without the flag nothing changes and
  `result.budget` reports `used_tool_calls` only.
- [x] 7. Contract: `tool_calls` in the stop reason table, `--max-tool-calls`
  in the run flags table and in `nare contract`, budget keys documented.
