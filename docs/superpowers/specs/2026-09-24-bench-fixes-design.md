# nare - Benchmark Fixes From the First Runs

**Date:** 2026-09-24
**Status:** Draft for review
**Scope:** Development tooling. Amends the benchmark suite,
`2026-09-21-nare-benchmark-suite-design.md`, as merged in #30. Nothing under
`src/nare/` changes. Lands before
`2026-09-24-ask-only-when-ambiguous-design.md`, so that change is measured
against a baseline that already scores what it changes.

## 1. What the first runs showed

Three smoke runs on `ada/qwen3-14b` (`benchmarks/results/2026-09-22T22-38-55Z`,
`2026-09-22T23-10-18Z`, `2026-09-23T22-50-09Z`): 39 repetitions, 16 failures,
2 errors.

| Cause | Reps | What it says about the suite |
| --- | --- | --- |
| The agent called `ask` on a request with one reading | 9 | A harness problem, measured correctly. The harness spec covers it. |
| The agent reported `done` with a test still failing | 3 | A harness problem, measured correctly. Out of scope here. |
| `greet()` returned `hello SAM` against a check wanting `HELLO SAM` | 3 | Fixed in c9291a6 by making the prompt say what the check demands. |
| `min_met = 3` gated a rep whose checks passed | 1 | Fixed in c9291a6. |
| The judge returned an empty reply | 1 | Cannot be diagnosed: the reply is not kept. |
| A rep hit the 600 s case timeout | 1 | Cannot be diagnosed: the session is not kept. |

The suite is doing its job: every failure traces to something real, and no
rep was misgraded (the one lenient judge verdict, section 5, came on reps
that failed their checks anyway). What it cannot do is explain its two errors, or show *why*
an agent asked, because everything a rep produced is deleted with its sandbox.

### Already fixed by #30

Found in the same review and fixed before merge, so not repeated here:

- The judge now sees files the agent created (`git_diff` stages everything
  and diffs against the fixture commit). Before, bash-timeout-recovery could
  not pass: its first claim is about a new file.
- A transport failure nare caught, and a judge transport failure, are
  `error`, not `fail`.
- `bless` merges into the existing baseline instead of replacing it.

## 2. Keep what each rep produced

`sandbox()` removes the rep's directory on exit, and `session.json` lives in
it. The results line records the outcome but not the transcript, the
questions, or the judge's raw reply.

**Change.** Each rep's evidence is copied out before the sandbox closes:

    benchmarks/results/
      2026-09-24T10-00-00Z.jsonl          # unchanged: one line per rep
      2026-09-24T10-00-00Z/
        fix-failing-test-0/
          session.json                    # nare's session, from /out
          stdout.jsonl                    # the event stream
          stderr.txt
          judge.txt                       # judged cases only

- The stamp is fixed when `run` starts and passed to both the evidence copy
  and `write_results`. Today the confirmation re-run calls `write_results`
  again and gets a second stamp, so one run leaves two results files; with a
  fixed stamp the second write replaces the first.
- `judge.txt` holds every judge attempt (see section 3): its stop reason,
  then its raw text.
- `RepRecord` gains `artifacts: str`, the directory relative to
  `benchmarks/results/`, defaulting to `""` so results files written before
  this change still load.
- `latest_results` globs `*.jsonl` and is untouched: directories are not
  results files.
- `benchmarks/results/` is already gitignored. A session is a few kilobytes,
  so no pruning.

## 3. A judge that survives one bad reply

The 09-23 run lost a rep to `no JSON object in the judge's reply: ''`. The
likely cause is that `qwen3-14b` reasons before answering, and
`JUDGE_MAX_TOKENS = 1024` ends the turn before the answer. That is a guess
until section 2 keeps the reply.

**Change.**

- `score` retries once when an attempt raises `JudgeError`, which covers an
  empty reply, an unparseable one, a wrong verdict count, and a transport
  failure. Two failures still raise, and the rep is still `error`. One retry
  bounds the cost of a flaky judge without letting it loop.
- `score` returns every attempt's stop reason and raw text alongside the
  verdicts, and `JudgeError` carries the same, so `judge.txt` is written on
  both paths.
- `JUDGE_MAX_TOKENS` does not change in this spec. If the kept replies show
  `max_tokens`, raising it is a one-line follow-up with evidence behind it.

## 4. Show the judge what a blocked run said

`line_text` builds the judge's "closing message" from the `output` event,
which nare emits only on `done`. A blocked run's questions never reach the
judge, so no claim can be about them.

**Change.** `ResultLine` gains `questions: list[str]`, read from the
`result` line (contract v1 already carries it). The closing message is the
`output` event's text when there is one. For a run with questions it ends
with:

    The agent stopped to ask:
    - <question>

## 5. The diff decides claims about files

The judge prompt says "judge only what the diff shows", then hands it the
agent's closing message, and before #30 it credited a claim about
`notes.txt` that the diff could not have shown. The fix to the diff removes
that instance; the wording is still loose.

**Change.** One sentence added to `JUDGE_SYSTEM`: the closing message is
context, and a claim about a file's contents is true only if the diff shows
it.

## 6. Tokens that include cached input

`tokens_median` is `input + output`. The proxy serves most input from cache,
so a rep reports `input: 1, cache_read: 640`, and the column is close to an
output count. A longer tool description, which is exactly what the harness
spec changes, barely moves it.

**Change.** Tokens are `input + output + cache_read + cache_write`. The
README says so. The number changes for every case, so the baseline is
re-blessed (section 8) in the same change; comparing old and new numbers
would read as a regression everywhere.

## 7. A judge for ambiguous-request

ambiguous-request checks only `status = "blocked"`. A run that asks "what
should I do?" passes as well as one that asks "raise TIMEOUT from 30 to
what?". The harness spec requires the second kind, so this case has to tell
them apart before that spec lands.

**Change.**

```toml
[judge]
assertions = [
  "config.py was not changed.",
  "A question names the current timeout value or offers concrete values to choose from.",
]
min_met = 2
```

The case stays `verify`-exempt: its only check is a `result` check.

## 8. Re-bless on current main

The baseline was blessed at c9291a6, before nine commits reached `main`. Once
sections 2 to 7 are in:

1. `bin/bench run --tier smoke`, then `bin/bench bless --tier smoke`.
2. `bin/bench run --tier full --case bash-timeout-recovery` once, to confirm
   it can pass now that the judge sees `notes.txt`. The result goes in the PR
   description. Whether it moves back to smoke is a separate decision.

## 9. Testing

In `benchmarks/tests/`, the way the suite already tests itself: pure
functions directly, the runner with fakes.

- `test_judge.py`: an empty reply followed by a valid one scores; two bad
  replies raise, and the error carries both raw replies.
- `test_grade.py`: `parse_stdout` reads `questions`, and a result line
  without the field reads as `[]`.
- `test_main.py`: the closing message carries a blocked run's questions; a
  rep's evidence directory holds the session, stdout and stderr, and
  `judge.txt` for a judged case; a confirmation re-run leaves one results
  file, not two; a results line without `artifacts` still loads.
- `test_report.py`: `tokens_median` counts all four usage fields.
- `test_cases.py`: already loads every case, which covers the new judge
  block.

`bin/build` and `bin/bench verify --tier full` stay green.

## 10. Out of scope

- **The 600 s timeout.** No fix until section 2 shows what the rep was
  doing.
- **Reporting `done` with a test failing.** A harness question about
  verifying before finishing, not a benchmark defect.
- **A stronger judge model.** Only small models are reachable; the judge
  stays pinned to the baseline's.
- **More repetitions per case.** The seven-rep confirmation already guards
  against three-rep noise.

## 11. Acceptance criteria

- [ ] After `bin/bench run`, every rep has a directory under
      `benchmarks/results/<stamp>/` holding its session, stdout and stderr,
      and its judge replies for a judged case
- [ ] One run, including a confirmation re-run, leaves exactly one results
      file
- [ ] A judge that fails once and then answers produces a scored rep
- [ ] A blocked rep's questions appear in the prompt the judge receives
- [ ] `tokens_median` counts cached input, and the README says so
- [ ] ambiguous-request has the judge block in section 7
- [ ] The smoke baseline is re-blessed on a commit after this change
- [ ] bash-timeout-recovery's full-tier result is reported in the PR
- [ ] `bin/build` and `bin/bench verify --tier full` pass
