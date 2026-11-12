# Command-line route for a caller check

Issue #296

## Scope
In: a `--check-command` flag that runs a caller's argv as the run's check, with
`--check-timeout`; the check's contract entry and docs; awaitable checks in the
library, which the command check is built on.
Out: loading caller Python into `nare run`; any change to how a synchronous
Python `check` behaves; a check for the TUI.

## Assumptions
- `--check-command` takes the argv as a JSON array of strings, so there is no
  shell and no quoting question, e.g. `--check-command '["python3", "check.py"]'`.
  A malformed or empty array, or one with a non-string, never starts (exit 2).
- `--check-timeout SECONDS` defaults to the command tools' default, 30 seconds,
  and must be above 0. It needs `--check-command`.
- `--check-command` requires `--schema` (exit 2 otherwise), as `check=` does.
- The command runs in `--root` when there is one, otherwise the current
  directory, with nare's environment minus the provider credentials (as bash and
  command tools, and `--bash-credentials` does not reach it), in its own process
  group, killed on timeout, deadline or signal.
- stdin is one JSON object: `{"answer": <parsed answer>, "reads": [<read records>]}`.
- stdout is a JSON list of errors. Each is a mapping with `code` and `message`,
  and optional `location` (a list) and `guidance`, or a plain string, exactly
  what a Python check may return. An empty list passes.
- A non-zero exit, a timeout, stdout that is not a JSON list, or an item that is
  not an error ends the run as a raising check does today: `status` `error`,
  `stop_reason` null, `check raised CheckCommandFailed: <why>`. stderr is quoted
  in the message, clipped.
- The library accepts a check that returns an awaitable and awaits it. That is
  what lets the command check honor the deadline and signals; a synchronous
  check is called exactly as before. Additive.
- Contract: a new `check_command` entry (flags, default timeout, requires,
  stdin and stdout shape, cwd, credentials, failure). Additive, contract stays 3.

## Tasks
- [x] 1. Awaitable checks: a test where `check=` returns a coroutine and its
      errors drive a correction; another where it raises.
- [x] 2. `command_check(argv, timeout, root)`: tests for pass, errors, string
      errors, non-zero exit, bad JSON, non-list, timeout kills the group,
      cancellation kills the group, cwd is the root, credentials withheld.
- [ ] 3. CLI: `--check-command` and `--check-timeout` parse, refuse bad values
      and a missing `--schema` (exit 2), and an end-to-end fake-provider run
      whose first answer the command refuses and whose corrected answer passes.
- [ ] 4. Contract entry and docs: `check_command` in `describe()`, flags table
      rows, the caller-check section no longer says the command line takes no
      check.
