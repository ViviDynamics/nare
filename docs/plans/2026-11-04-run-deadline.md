# Run deadline: --timeout, and SIGTERM or SIGINT that saves the session and writes a result

Issue #143

## Scope

In: `nare run --timeout SECONDS`, and SIGTERM and SIGINT handlers in
`nare run`. On a timeout or a signal the run stops at the await it is in
(the model call, a tool call, MCP discovery), kills the bash process groups
this invocation started, saves the session, emits an `error` event, writes a
`result` with `stop_reason` `timeout` or `interrupted`, and exits 1.

Out: a per-request timeout and retries inside a run (#134); scheduling, retry
policy and a SIGKILL backstop, which stay with the caller. `nare tui`, which
has its own interrupt handling and no unattended caller.

## Assumptions

- The deadline is wall clock for one invocation, counted from when the run
  starts (after startup validation), as `--max-turns` counts per invocation.
  A positive finite number of seconds; anything else never starts (exit 2).
- Stopping is task cancellation. The loop already answers every tool call a
  cancel interrupts (`step()`'s `finally`), and `bash_outcome` already kills
  the group of a command it is running when cancelled, so the saved
  transcript is one a resume accepts.
- "The bash process groups the run started" are the groups this invocation
  recorded in `Session.process_groups` (commands that left something
  running), plus the one running when the stop lands. Groups an earlier
  invocation of a resumed session left running were reported then, and are
  not this run's to kill.
- SIGINT now exits 1 with a result line instead of 130 with none, as the issue
  asks. Exit 130 was never in the contract's exit code table, and `error`
  already maps to 1, so no documented code changes. A second signal while
  stopping gets the default behaviour back (SIGINT raises, SIGTERM kills), so
  a stop that hangs can still be forced.
- `timeout` is kind `limit` (the task is unjudged, a longer deadline or a
  resume may finish it). `interrupted` needs a new kind, `stopped`: the
  caller ended the run, which says nothing about the task and is not a
  failure. Both values and the kind are additive and keep contract version 2.

## Tasks

- [x] 1. `--timeout` validation: non-positive, non-finite and non-numeric
  values exit 2 with nothing on stdout.
- [x] 2. A model call slower than `--timeout` ends promptly with
  `error`/`timeout`, an `error` event, a result line, exit 1, and a session
  that resumes to `done`.
- [x] 3. SIGTERM during a long `bash` command writes a result with
  `stop_reason` `interrupted`, exits 1, and leaves no process from that
  command running.
- [x] 4. SIGINT does the same (the existing interrupt test moves from exit
  130 to the result line).
- [x] 5. A timeout kills a process group an earlier call in the same run left
  running.
- [x] 6. Contract: `timeout` and `interrupted` in the stop reason table (code
  and docs), the `stopped` kind, a `deadline` entry in `nare contract`, and a
  docs section.
