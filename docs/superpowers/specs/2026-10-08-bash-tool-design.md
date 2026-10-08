# nare - Bash Tool: Honest Results, Nothing Left Running

**Date:** 2026-10-08
**Status:** Draft for review
**Size:** M
**Scope:** `run_bash` and the bash description in `tools.py`, plus a quit prompt
in `tui/app.py`.
**Waits on:** nothing

`bash` should report what really happened and leave nothing running behind it.
Covers A1 to A3. A5's interrupt is already fixed: c70c84b runs bash as an async
subprocess and kills its process group on cancel.

## 1. The problem

In the practice run, three of four builds failed while the tool said `exit 0`:
`npm run build 2>&1 | tail -30` ran under dash, which has no pipefail. A
Postgres started with `pg_ctl` was still running hours later.
`pkill -f next-server` killed the tool's own shell, returned `exit -15`, and
cost a turn of about 50k tokens.

| ID | Severity | Issue |
| --- | --- | --- |
| A1 | High | `bash` runs `/bin/sh` (dash): pipe exit codes are lost (three failed builds reported `exit 0`), `[[ ]]` fails, and nothing says each call is a fresh shell. |
| A2 | High | Processes outlive the call: a Postgres started with `pg_ctl` was still running hours later, and the summary didn't say so. |
| A3 | Medium | Signal exits print as `exit -15` and aren't errors; `pkill -f` matches the tool's own shell. |

Letters are IDs from the [practice-run
evidence](../../evidence/2026-10-08-tui-practice-run.md), which has the line
references at 89b4aa2. V numbers are from the visual review of 177e8fe, whose
screenshots are in
[2026-10-08-tui-visual-run](../../evidence/2026-10-08-tui-visual-run/). Where
both reviews found the same problem, the row carries both IDs.

## 2. Changes

1. Real bash with pipefail (A1). When `shutil.which("bash")` finds it, run
   `bash -o pipefail -c <command>` through `create_subprocess_exec`; otherwise
   keep sh. The description says: "Runs in bash with pipefail. Each call is a
   fresh shell: `cd` and `export` do not persist."
2. Report what is still running (A2). After `communicate`, probe
   `os.killpg(pgid, 0)`. If the group is alive, append
   `[still running: process group P (cmd)]` to the result and record P in a new
   session field (it has a default, so no contract bump). At turn end and on
   quit, the TUI lists recorded groups that are still alive and offers **k**ill
   or **l**eave. `ponytail:` a process that calls setsid itself (`pg_ctl` does)
   escapes the group; the upgrade is `prctl(PR_SET_CHILD_SUBREAPER)`, then
   listing `/proc/self/task/*/children`.
3. Signal exits (A3). For a negative return code, print
   `killed by SIGTERM (signal 15)` and mark the result as an error. If the
   command uses `pkill`, `killall` or `kill`, add: "pkill -f also matches the
   shell running this command; use a pattern like `nex[t]-server`, or kill by
   `$!`." Optional: keep the command out of argv with
   `bash -c 'eval "$NARE_CMD"'`.

## 3. Acceptance

- [ ] `run_bash("false | true")` starts with `exit 1`, and
      `run_bash("[[ 1 == 1 ]]")` with `exit 0`.
- [ ] `run_bash("(sleep 2 >/dev/null 2>&1 &)")` reports a live group, and
      quitting the TUI with a live group prompts.
- [ ] A command that pkills its own unique tag returns `killed by SIGTERM` as an
      error.

## 4. Related specs

The System prompt spec copies this spec's shell semantics into the bash
description, and its rule 4 makes the model report what it left running. The
Transcript spec renders signal exits red.
