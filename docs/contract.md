# The machine contract

This is contract version 1.

A caller drives nare as a subprocess and turns three things into decisions: the
JSONL event stream on stdout, the session file, and the exit code. This document
is what a caller may rely on, and what nare promises not to change without
saying so.

Read the contract as data instead of parsing this page:

    nare contract

It prints the version, the exit codes, the statuses and the event types this
build speaks.

## Refusing a contract you do not speak

    nare run --contract 1 --yes "..."

When the caller's number and nare's differ, nare exits 2 before starting and
writes nothing to stdout. A caller that pins the version it understands never
mis-reads a stream from a newer nare; it fails on the first run instead, with
both numbers named on stderr.

The session file carries the same number. Resuming a session written under a
different contract is refused for the same reason.

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
`contract`, `output` and `error`. A caller that reads only one line should read
that one.

Secrets are redacted when an event is constructed, not when it is written, so
an unredacted event never exists.

## What changes the version

Contract version 1 covers the event types and their fields, the `result`
object's fields, the session file's shape, and the exit codes above.

**These keep the version:** a new event type, a new field on an event or on the
`result` object, a new session field with a default, a new flag, a new status
value's behaviour being described more precisely without changing it.

A caller must therefore ignore fields and event types it does not recognise.
That is the price of additive changes not bumping the number.

**These bump it:** removing or renaming a field or an event type, changing what
an existing field means, changing an exit code, and changing when a status is
reported.

nare speaks one contract version at a time. There is no negotiation and no
compatibility mode: a caller pins a version, and a nare that speaks another one
refuses to run.
