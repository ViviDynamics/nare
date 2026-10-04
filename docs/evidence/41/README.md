# Issue 41: actual CLI evidence

These are captured subprocess outputs, not hand-written expected results. Run
from the repository root to regenerate them:

    uv run python tests/budget_evidence.py

The entry point `tests/budget_probe.py SCRIPT.json` substitutes only the
transport factory and then invokes the production `nare.cli.main` with the
recorded argv. Parsing, real read/bash tools, the loop, persistence, rendering
and exit codes run unchanged. No live model, network request or credential
change is involved. `invocation.json` records exact CLI argv, exit and call
count; `script.calls.json` records each model request. Development captures
identify their editable source build in `result.nare`; the shipped version is
assigned automatically after merge. UUIDs/timestamps change on regeneration.

| Capture | Model calls | Terminal result | Actual usage |
| --- | --- | --- | --- |
| [baseline](baseline/stdout.jsonl) | 3 | done, exit 0, only `--max-tokens 5` | input 30, output 15, total 45 |
| [budget](budget/stdout.jsonl) | 2 | budget, exit 1, `--budget-tokens 25` | input 20, output 10, total 30; limit 25 |
| [resume-exhausted](resume-exhausted/stdout.jsonl) | 0 | budget, exit 1; omitted flag inherits 25 | unchanged total 30 |
| [resume-raised](resume-raised/stdout.jsonl) | 1 | done, exit 0; new total ceiling 60 | total 45 |
| [done-crossing](done-crossing/stdout.jsonl) | 3 | done, exit 0; ceiling 31 | total 45 and structured output retained |
| [invalid](invalid/invocation.json) | 0 | exit 2, empty stdout | no session/result |
| [cache](cache/stdout.jsonl) | 1 | budget, exit 1 | 3 input + 2 output + 7 read + 4 write = 16 |
| [partial](partial/stdout.jsonl) | 1 | budget, exit 1 | total 15; valid output retained |
| [overflow](overflow/stdout.jsonl) | 9 | done, exit 0; 32000 window | eight real large tool outputs, elision observed |

The baseline repeats the Scrutare probe: max_tokens=5 on all three requests,
with cumulative input=30/output=15. The budget case enforces cumulatively after
two complete tool turns and makes no third request. The saved
[budget session](budget/session.json) retains both tool-use/result pairs and
actual usage; the original stop is also in `budget/stopped.session.json`.
Resume captures each have their own session snapshot.

For available partial findings, read
[partial result](partial/stdout.jsonl) or
[partial session](partial/session.json): both contain
`output={"findings":["confirmed partial finding"]}`, while status remains error
and stop_reason is budget. This data passed
[the supplied schema](findings.schema.json). Callers mark it partial, retain
stdout/session artifacts and decide whether to raise the budget.

The overflow probe runs the new benchmark fixture's eight actual bash commands,
which produce over 232000 characters in aggregate, exceeding a 32000-token
window at four characters per token without elision. The fake reports measured
serialized input at that ratio. The harness compacts and completes nine calls.
This demonstrates harness mechanics only. The approved design's live local-model
benchmark and tool-call quality probe were not run, in accordance with the
request for no live model calls. They remain unverified.

Scrutare compatibility caveat: budget 25 stopping at actual usage 30 and budget
31 completing at 45 are intentional post-step overshoots. Any Scrutare SPEC
requirement for a strict no-overspending whole-review ceiling remains unmet.
Scrutare owns allocation across personas; nare exposes actual usage and retains
available findings, and makes no subsequent model call after exhaustion.
