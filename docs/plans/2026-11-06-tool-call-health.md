# Benchmark: count tool-call health per model

Issue #159

## Scope
In: per model, on every run, the counts of argument errors, unknown tool names,
empty arguments to a tool with required parameters, reasoning with no answer and no
call, and call markup left in reply text; printed under the run's table; and a
`tool-health` command that counts the same from an existing results file's evidence.
Out: scoring the kept #45 sessions (not on this machine or in git; see the PR body);
a per-model block in the matrix table (the counts print per model for each run).

## Assumptions
- The counts read what nare wrote: `session.json` for calls and their results, and the
  event stream for turns (a turn is the events up to a `cost` event). Production never
  streams deltas to the event stream's absence, so reasoning is always in the stream.
- An argument error is a result nare gave for a call it refused before running it: the
  transport's malformed-JSON messages, or the argument checks, which open with the
  tool's own name.
- A rep whose evidence is missing is counted as unread, not as clean.

## Tasks
- [x] 1. Scorer: `score_session` and `score_events` over fixtures of each kind.
- [x] 2. Every rep records its counts from its kept evidence, on the infra-error path too.
- [x] 3. `run` prints the counts per model; `tool-health` reads existing evidence.
