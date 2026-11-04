# Cut-off replies: detect repetition, and correct repetition and length stops

Issue #183

## Scope

In: a `repetition` stop reason; detection of a reply whose tail repeats one span,
while it streams (the transport stops reading) and on a finished reply (the
transport reports it); a per-invocation cut-off budget (`--max-cutoffs`, default
2) separate from turns, tool calls and schema corrections; one correction per
cut-off reply, with guidance; one progress event per cut-off; the result and
session fields; the contract and README entries; tests on both rails with fakes.

Out: the benchmark cases (#158, which Sam may own: the existing cases are live
model runs judged by a model, and a repetition case cannot be forced
deterministically); changes to the TUI (it does not pass the tool call budget
either); any caller's name in nare source (ADR 0008).

## Assumptions

- The rule is the one Coordinare measured (a span of at most 256 characters
  repeated over the last 2048, and the same test over digit-folded text over
  4096). It is ported as a rule. `nare.repetition` is a new module with no caller
  names.
- `repetition` is a `limit` stop: a larger budget or a resume may finish it. It
  sits beside `max_tokens`, which is also a `limit`.
- The library default is no budget, so an embedder that never asked sees today's
  behaviour, and the existing library tests stand unchanged. The CLI default is 2,
  as the issue asks, so a command-line run that used to end `error` at its first
  cut-off now continues. `--max-cutoffs 0` restores the old outcome.
- The budget is per invocation, as `--max-tool-calls` is. The session's
  `cutoffs` count is cumulative, so a caller can see the total across resumes.
- A cut-off turn's tool calls do not run (as before), and their results say so.
  The correction is merged into those results, so the turn stays well formed.
- An empty cut-off reply is not kept in the transcript: an empty assistant turn
  is rejected by the vendors on every later request.
- A streamed reply stopped for repetition drops its reasoning block on the
  Anthropic rail: a stopped stream has no signature for it, and a reasoning block
  without one is rejected when sent back.
- Contract version stays at 2. The stop reason, flag, event detail and result
  fields are additive. The change of default is a behaviour change on the
  command line, flagged in docs/contract.md, for the maintainers to judge whether
  it counts as "changing when a status is reported".
- Known false positive: digit folding reads a few KB of records that differ only
  in their numbers as a counting loop. Accepted, and pinned by a test.

## Tasks

- [x] 1. Detector: `degenerate`, `RepetitionWatch`, `looping`
  (`tests/test_repetition.py`)
- [x] 2. Stop reason and contract: `repetition` is a `limit`; `cut_off_replies`
  in `nare contract` (`tests/test_cutoff_replies.py`)
- [x] 3. Transports: openai and anthropic stop a looping stream early and report
  `repetition`; a finished looping reply is `repetition`; `length` and
  `max_tokens` unchanged (`tests/test_cutoff_transports.py`)
- [x] 4. Loop: correction within the budget, tool calls not run, empty reply not
  kept, spent budget ends `error` with the stop reason (`tests/test_cutoff_replies.py`)
- [x] 5. CLI: `--max-cutoffs`, result `cutoffs`, `budget.cutoffs` and
  `used_cutoffs`, session `cutoffs` (`tests/test_cutoff_cli.py`)
- [x] 6. Docs: contract stop-reason table, flag table, result and session fields,
  a Cut-off replies section, README
