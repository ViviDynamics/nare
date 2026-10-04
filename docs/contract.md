# The machine contract

This is contract version 1.

A caller drives nare as a subprocess and turns three things into decisions: the
JSONL event stream on stdout, the session file, and the exit code. This document
is what a caller may rely on, and what nare promises not to change without
saying so.

Read the contract as data instead of parsing this page:

    nare contract

It prints the version, the exit codes, the statuses, the event types and the
redaction rule set this build speaks.

## Refusing a contract you do not speak

    nare run --contract 1 --yes "..."

When the caller's number and nare's differ, nare exits 2 before starting and
writes nothing to stdout. A caller that pins the version it understands never
mis-reads a stream from a newer nare; it fails on the first run instead, with
both numbers named on stderr.

The session file carries the same number. Resuming a session written under a
different contract is refused for the same reason.

## The redaction rule set

Every event's `text`, and whatever text a caller pipes through:

    nare redact < its-own-log.txt > its-redacted-log.txt

is redacted by the same rule set. The rules, in the order they are applied, are
`anthropic-key`, `openai-key`, `github-token` and `credential-assignment`.
Matching is replaced with `[redacted]`.

The rule set has an identity, printed by `nare contract` under `redaction`: a
`version` and the rule names. A caller that also redacts its own published
output runs `nare redact` instead of copying the patterns, and reads the
identity back so a drift between its nare and its assumptions is detected
rather than silently ignored.

The route is a filter: text on stdin, redacted text on stdout, no arguments
and no state. It exits 0 with the redacted text on stdout. When stdin is not
readable UTF-8 text it exits 1, explains on stderr, and writes nothing to
stdout. It is linear in the input size: a megabyte of command output is
redacted in milliseconds, not minutes.

## Exit codes

| Status | Exit | Meaning |
| --- | --- | --- |
| `done` | exit 0 | The run finished. `output` carries the answer when `--schema` was used. |
| `blocked` | exit 0 | The model used `ask`. `questions` carries what it needs. This is an outcome, not a failure. |
| `error` | exit 1 | The run started and failed. `error` says how, `stop_reason` says what ended it. |
| never started | exit 2 | No session existed: a bad invocation, an unreadable session file, or a refused contract. Nothing is written to stdout, so there is no `result` line. |

Every terminal status maps to exactly one code, and a test asserts this table
matches the code.

## The event stream

One JSON object per line on stdout, each with `type`, `text`, `detail` and
`timestamp`. The types are `progress`, `tool_use`, `thinking`, `cost`, `error`
and `output`. They match Conductor's `BackendEventType` so its adapter is a
constructor call rather than a mapping table.

The last line is not an event. It is the `result` object, carrying
`session_id`, `status`, `questions`, `usage`, `stop_reason`, `turns`,
`contract`, `nare`, `output`, `budget` and `error`. A caller that reads only one line should read
that one.

Secrets are redacted when an event is constructed, not when it is written, so
an unredacted event never exists.

## What changes the version

Contract version 1 covers the event types and their fields, the `result`
object's fields, the session file's shape, the exit codes above, and the
redaction rule set.

**These keep the version:** a new event type, a new field on an event or on the
`result` object, a new session field with a default, a new flag, a new status
value's behaviour being described more precisely without changing it.

A caller must therefore ignore fields and event types it does not recognise.
That is the price of additive changes not bumping the number.

**These bump it:** removing or renaming a field or an event type, changing what
an existing field means, changing an exit code, and changing when a status is
reported. Changing or removing a redaction rule, or changing what a rule
matches, bumps it too: the rule set is part of what an event's `text` means.

nare speaks one contract version at a time. There is no negotiation and no
compatibility mode: a caller pins a version, and a nare that speaks another one
refuses to run.

## Stop reasons

| stop_reason | Meaning |
| --- | --- |
| `end_turn`, `stop_sequence` | Provider ended the response; status determines completion |
| `tool_use` | Provider requested tools; may be working or blocked on ask |
| `max_tokens` | Provider truncated the response (including rejected context) |
| `refusal` | Provider refused to continue |
| `schema_violation` | Second invalid structured answer |
| `max_turns` | Invocation's turn count limit reached |
| `context` | Estimated context still exceeds the window after eligible elision |
| `budget` | Session token/USD ceiling reached, or USD accounting is unknown |
| null | No completed response for this failure, e.g. provider exception |

New stop reasons and additive usage/result/session fields keep contract 1.

## Budgets and partial results

`usage` contains cumulative `input`, `output`, `cache_read`, `cache_write`, and
`cost` (dollars or null). Token categories are disjoint. For OpenAI,
`input=prompt_tokens-cached_tokens`, `cache_read=cached_tokens`, and cache_write
is zero. Anthropic reports uncached input, cache reads and cache creation
separately. Completion/reasoning tokens reported in output are not counted
again as a separate category. The cumulative total is the sum of all four token
fields, not the last turn and not the size of the stored transcript. Missing
provider counters normalize to zero; this is reported accounting, not proof of
a strict cap when a provider omits usage.

Every result carries `budget`: configured `tokens` and/or `usd` limits (keys
absent when unset), plus `used_tokens` and `used_usd` (null when cost is unknown).
A budget `error` event has `detail.budget` with the same fields and `detail.usage`
with actual cumulative counters. The result is authoritative for terminal
status. In particular, done/blocked crossing a ceiling still exit 0 and retain
actual usage, even above the limit.

A nonterminal crossing step finishes tool dispatch before stopping. Its tool
uses/results, progress, thinking and cost events remain in the emitted JSONL;
the final error and result follow them. `--session` saves the transcript,
usage, limits and output atomically. Events are not stored inside the session;
the caller retains stdout. Context compaction replaces eligible old tool-result
bodies with stubs, preserving pairs and assistant text; prior streamed events
are unaffected.

Budget exhaustion exits 1 with `status=error`, `stop_reason=budget`. Callers
must branch on that pair, not on the free-text error or exit 1 alone. Provider
exceptions also exit 1 but use null stop_reason. After exhaustion there is no
further model call in that invocation.

`--resume` reopens a session for work but does not reset usage. Flag overrides
environment, environment overrides a persisted limit, and omission retains the
persisted limit. There is no flag to clear a persisted budget. If already at or
above a limit, resume emits a budget error/result and calls no model, including
when no flag was supplied. Raising one limit does not remove the other. All
active limits must allow continuation. USD usage with a historical unknown
cannot be repaired by adding prices later. `--max-turns` remains per invocation.
A completed/blocked crossing result is successful; reopening it with resume
still applies the cumulative pre-call guard.

When a schema was supplied, nare retains the latest complete JSON document that
passes that schema in `result.output` and session `output`, including a document
emitted alongside tool calls. This represents available validated data, not a
complete review when status is error. Invalid later text does not discard an
earlier valid document. There is no invented empty findings list, automatic
merging of separate documents, or promotion of incomplete JSON. Without a
schema, output remains unset unless retained from a previously validated run;
callers can recover raw assistant text from the transcript/progress events and
validate it themselves. Pass the schema again on continuation. A caller retains
all documents it wants to aggregate from its own event stream.

For example:

```python
result = json.loads(events[-1])
if result["status"] == "error" and result["stop_reason"] == "budget":
    partial_findings = result["output"]  # may be None; already validated if present
    actual_tokens = result["budget"]["used_tokens"]
    effective_limit = result["budget"].get("tokens")
    # Store available findings as partial and keep the session/JSONL artifacts.
```

The approved design enforces budgets after steps, so a crossing step can
overshoot by its actual token/USD usage. `--max-tokens` bounds only response
output, not input/cache spend. If Scrutare SPEC requires a strict whole-review
ceiling, this remains a mismatch: Scrutare must account for overshoot and
success on crossing turns in its own allocation policy. nare does not allocate
budgets across personas or orchestrate reviews.
