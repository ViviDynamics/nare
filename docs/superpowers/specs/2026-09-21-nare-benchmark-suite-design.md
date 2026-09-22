# nare — Benchmark Suite

**Date:** 2026-09-21
**Status:** Approved, not yet implemented
**Scope:** Development tooling, not a slice. Adds a top-level `benchmarks/`
tree that measures whether a change to nare made the harness better. Nothing
under `src/nare/` changes, and nothing here ships in the wheel.

## 1. The question this answers

Every test in `tests/` asks the same question: does the harness behave
correctly given a scripted model? `FakeProvider` replays canned replies, so
the answer is deterministic, free, and gateable in CI. That is the right
question for a contract and the wrong one for quality.

No test in the repo asks the question this suite exists for: **does a real
task actually get done, and at what cost?** A better tool description, a
sharper system prompt, a smarter turn budget, and a worse version of any of
them all score identically under `FakeProvider`, because the model's replies
are frozen.

Improvement is defined here as, in order:

1. The task is completed correctly, verified programmatically.
2. The work is of good quality, rated against a per-case rubric.
3. Fewer tokens and fewer dollars to get there.

The best change lowers cost while raising quality. A change that raises the
pass rate and the token count is still an improvement; a change that lowers
the token count and the pass rate is not.

## 2. What nare can actually improve

nare does not contain the model. Holding the model fixed, the levers are the
tool set, the tool descriptions and schemas, the system prompt, the loop's
turn accounting and error handling, `MAX_TOOL_OUTPUT`, the approval seam, and
later compaction. The benchmark measures those levers and nothing else, which
is why the baseline pins the model and refuses to compare across models.

## 3. Decisions

Seven decisions were settled before this spec. Only the first earns an ADR.

1. **Benchmarks are unshipped dev tooling, isolated by container.** ADR 0007.
2. **Both tiers are live.** `smoke` is small and cheap and runs often; `full`
   is the real suite and runs on change. There is no deterministic tier —
   `tests/` already owns that question and answers it better.
3. **Programmatic checks gate, the judge grades.** A rubric-scored judge can
   fail a rep that passed its checks. It can never rescue one that failed.
4. **A case is a directory of data, not code.** `case.toml` plus a `fixture/`
   tree. TOML reads with stdlib `tomllib`, so the suite adds no dependency to
   read its own cases.
5. **Committed baseline, ephemeral results.** The blessed numbers are a
   reviewable file; per-run output is gitignored.
6. **A container per rep.** The README already says containment is the
   caller's job. The benchmark is a caller that runs model-generated shell
   commands unattended, in a loop, so it provides that containment.
7. **Few reps with automatic re-confirmation.** Sampling cannot be pinned, so
   noise is managed by re-running suspect cases rather than by pretending
   three reps are conclusive.

### Why sampling cannot be pinned

`anthropic` 1.6.0 removed `temperature` from `messages.create`, and nare's
transport refuses the flag at construction rather than silently dropping it.
There is no knob that makes a rep deterministic. Repetition is therefore not
a refinement of this design; it is the only available instrument.

## 4. Layout

    benchmarks/
      cases/<case-id>/
        case.toml               the prompt, checks, rubric
        fixture/                the repo the agent is dropped into
      runner/
        __main__.py             CLI: run | compare | bless | verify
        case.py                 load and validate case.toml       (pure)
        grade.py                run the checks                    (pure)
        report.py               aggregate, diff baseline, render  (pure)
        sandbox.py              docker: build, run one rep        (impure)
        judge.py                rubric scoring via the API        (impure)
      tests/test_runner.py
      Dockerfile
      .dockerignore
      baselines/<tier>.toml     committed, blessed numbers
      results/                  gitignored, one JSONL per run
    bin/bench

Three pure modules and two that touch the world. That split is the whole
testability story: `case`, `grade` and `report` are unit-tested with no Docker
and no network, exactly as `FakeProvider` does for the harness.

One baseline file per tier, because the tiers run different models and a
single file would have to nest them.

### What is committed

Everything above except `results/`. Cases grow deliberately, one reviewable
PR each. `baselines/<tier>.toml` is rewritten in place, so its history grows
and the file does not. Nothing machine-generated enters the tree, which is
the property that keeps the suite pleasant at two hundred cases.

### Why not in `src/nare/`

`[tool.hatch.build.targets.wheel] packages = ["src/nare"]` already means the
wheel contains only `src/nare`. A top-level `benchmarks/` is excluded from
the install by configuration that is already there.

Shipping the runner would put Docker orchestration and a judge client into
nare's public surface and its dependency list, for every user of `nare run`
forever. It would also contradict architecture.md's first line — the core is
a library and every surface is a thin adapter over it. A benchmark runner is
not a thin adapter. If the bench ever grades harnesses other than nare, it
graduates to its own package; that is a later decision with a clear trigger,
not a reason to build a workspace today.

## 5. The case format

```toml
id     = "fix-failing-test"
tier   = "smoke"                          # smoke | full
prompt = """test_parse fails. Fix bar.py so the suite passes."""
max_turns = 12
model  = "claude-haiku-4-5-20251001"      # optional; tier default otherwise
reps   = 3                                # optional; tier default otherwise

[[check]]
kind = "bash"
cmd  = "pytest -q"

[[check]]
kind = "result"
status = "done"
max_turns = 10

[judge]
rubric    = """Fixed the cause in bar.py. Did not delete or weaken the test."""
min_score = 3
```

Two check kinds, and no more:

| Kind | Asserts |
|---|---|
| `bash` | The command exits 0, run in the container after the agent stops. |
| `result` | Fields on nare's terminal `result` line: `status`, `max_turns`. |

A `file` kind was considered and rejected: it is `grep` with extra steps.

`[judge]` is optional. Without `min_score` the score is reported and does not
gate. `case.py` rejects a case with no checks, an unknown `kind`, a `tier`
outside the two, a missing `fixture/`, or an `id` that does not match its
directory name — the directory name is the identity, and the field exists so
a case file read on its own says what it is.

`model`, `reps` and `timeout` are optional per case and fall back to the tier:

| Tier | Model | Reps | Rep timeout |
|---|---|---|---|
| `smoke` | `claude-haiku-4-5-20251001` | 3 | 600s |
| `full` | `claude-sonnet-5` | 5 | 900s |

A case that overrides `model` overrides it in the baseline comparison too,
since the baseline records the value actually used.

**A rep passes** if and only if every check passes and either there is no
judge or the score is at least `min_score`.

## 6. Running one rep

Preflight runs once per invocation and fails before anything is spent:
Docker present, `ANTHROPIC_API_KEY` set, then
`docker build -t nare-bench:local -f benchmarks/Dockerfile .`. The image
installs the working tree's nare, so the suite measures local changes rather
than a published release. Layer caching makes rebuilds near-free.

Then, per rep:

1. Copy `cases/<id>/fixture/` into a fresh host temp directory.
2. Run the container:

       docker run --rm -v <tmp>:/work -v <artifacts>:/out -w /work \
                  -e ANTHROPIC_API_KEY nare-bench:local \
                  sh -c 'git init -q && git add -A && git commit -qm fixture &&
                         nare run --yes --jsonl --session /out/session.json \
                                  --max-turns N --model M -- "<prompt>"'

   under the rep timeout from the tier table, capturing stdout.
3. Parse stdout into typed events plus the one `result` line.
4. Grade `result` checks from that line, on the host.
5. Grade `bash` checks in a second `docker run` over the same temp directory.
6. If the case has a judge, call the API from the host with the prompt, the
   rubric, `git diff HEAD`, and the final assistant text. The judge is asked
   for JSON — `{"score": 1-5, "reason": "..."}` — and a reply that does not
   parse, or scores outside the range, is a judge failure under section 8
   rather than a zero. A judge that cannot answer must not look like a bad
   answer.
7. Record the rep to the results file and delete the temp directory.

Three details carry weight:

**`git init` inside the sandbox** produces a clean `git diff HEAD` to hand the
judge, and gives the agent an environment it recognizes. The session is
written to `/out`, outside the graded tree, so it never appears in the diff
and still survives for debugging a failed rep.

**The key is passed as `-e`, never in argv**, for the reason the README
already gives for refusing an `--api-key` flag: argv is world-readable.

**Containment is filesystem and process, not network.** The container must
reach the API, so `--network none` is not available. The docs say this plainly
rather than implying the box is sealed.

## 7. Aggregation and comparison

A rep's outcome is a three-value enum, never a boolean:

| Outcome | Means | Counts |
|---|---|---|
| `pass` | checks passed, judge gate cleared | numerator |
| `fail` | the agent ran, the checks say no | denominator |
| `error` | the rep could not be judged at all | neither |

`pass_rate = passes / (passes + fails)`. Tokens, turns and judge score
aggregate by **median**, so one runaway rep cannot swing a case. If more than
half a case's reps `error`, the case is **inconclusive**: the run exits 1
naming it, and it is never scored as a pass or a regression.

`results/<timestamp>.jsonl` holds one line per rep: `case`, `rep`, `outcome`,
`checks` (name and pass/fail each), `judge_score`, `judge_reason`, `status`,
`stop_reason`, `turns`, `usage`, `duration_s`, and `model`. `compare` and
`bless` both read this file and nothing else, so a run can be re-compared
without re-running it.

The boundary between `fail` and `error` is drawn by a contract nare already
has — a run that never started emits no result line. A well-formed result
line is graded normally, so `status = "error"` from a turn limit is a genuine
task failure. No result line at all, from a crash, a timeout, or a Docker
fault, is an `error`. No heuristic is required.

### The baseline

```toml
[meta]
blessed     = "2026-09-21T14:02:00Z"
commit      = "237a678"
model       = "claude-haiku-4-5-20251001"
judge_model = "claude-sonnet-5"

[case.fix-failing-test]
pass_rate     = 1.0
reps          = 3
tokens_median = 31100
judge_median  = 4.0
turns_median  = 6
```

Pinning `judge_model` is the non-obvious guard. If the judge model changes
underneath the suite, every score shifts at once and reads as a harness
regression.

### Comparison rules

| Condition | Outcome |
|---|---|
| `model` or `judge_model` differs from baseline | exit 2, refuse. `--allow-model-change` overrides. |
| `pass_rate` below baseline | suspect; re-run at 7 reps (`--confirm-reps`); still below is a **regression**, exit 1 |
| `pass_rate` above baseline | improvement, reported |
| `tokens_median` outside ±10% | flagged; above the band warns and exits 0 unless `--strict-tokens` |
| `judge_median` down more than 0.5 | warns |
| case present in the run, absent from the baseline | reported as new, never fails |
| case present in the baseline, absent from the run | reported as missing, never fails |

Re-confirmation is what makes three reps affordable: the extra reps are spent
only on cases that already look wrong. Token deltas get a tolerance band
because ordinary variance is not a change.

`bench bless` rewrites `baselines/<tier>.toml` from the most recent results
file and stamps commit, model and time, so every baseline change arrives as a
reviewable diff rather than a silent overwrite.

Exit codes follow `cli.py`: **0** clean, **1** regression or inconclusive,
**2** setup or infrastructure failure.

## 8. Failure handling

The governing rule: **infrastructure failure must never read as task
failure.** Section 7's enum is how that rule is enforced; the rest is
plumbing.

| Failure | Handling |
|---|---|
| Docker or key missing | exit 2 in preflight; nothing runs, nothing is spent |
| Image build fails | exit 2 |
| Container exceeds the wall clock | killed; rep is `error` |
| No result line on stdout | rep is `error` |
| Judge call fails | score is null; `error` only if the case set `min_score`, otherwise graded on checks with the score reported missing |

### `bin/bench verify`

The worst bug a benchmark can have is a check that already passes before the
agent does anything, because it silently scores every future run as a success.
`bin/bench verify` runs each case's checks against the pristine, untouched
fixture and requires at least one to fail.

It needs Docker and no API key, which makes it the one benchmark job that can
run in CI on every pull request at no cost.

## 9. Testing the runner

`benchmarks/tests/test_runner.py` unit-tests the three pure modules: case
parsing and its rejections, grading against synthetic result lines, and
`report`'s median arithmetic, tolerance bands, and regression detection. No
Docker, no network, runs inside `bin/build` with everything else.

One `@pytest.mark.docker` test proves `sandbox.py` can run a container.
Deselected by default, in the idiom `live` already establishes. `judge.py` is
tested against a fake client, the same duck-typed double pattern
`FakeProvider` uses.

## 10. The seed cases

Five smoke cases, chosen to hit nare's own seams rather than generic coding.

| Case | Exercises | Check |
|---|---|---|
| `edit-docstring` | read and edit, one file | `__doc__` is set; judge rates whether it describes behavior |
| `fix-failing-test` | bash, read and edit | `pytest -q` exits 0; judge checks the cause was fixed, not the test |
| `multi-file-rename` | search across three files | `rg` finds no old names and the suite passes |
| `ambiguous-request` | the `ask` tool | `status == "blocked"` with non-empty questions |
| `bash-timeout-recovery` | timeout and adaptation | a hanging command; the agent must survive the kill and still finish |

Two of these measure things nothing else in the repo measures.
`multi-file-rename` exists because nare has no grep tool — the question is
whether bash plus `rg` is genuinely enough, and only a live run answers it.
`ambiguous-request` exercises the blocked-plus-questions terminal state, which
is the capability nare was built for and which `tests/` verifies only against
scripted replies.

## 11. Changes to existing files

| File | Change |
|---|---|
| `pyproject.toml` | add a `docker` marker beside `live`; extend `testpaths`; `addopts = "-m 'not live and not docker'"` |
| `.gitignore` | add `benchmarks/results/` |
| `bin/build` | add `benchmarks` to the ruff and mypy paths |
| `.github/workflows/ci.yml` | add a `bin/bench verify` job |

The runner is held to the same strict-mypy bar as `src/`. Nothing under
`src/nare/` changes.

## 12. Deferred

Named so that none of it gets built by accident.

| Deferred | To |
|---|---|
| A live benchmark job in CI | When a key and a budget exist for it; `workflow_dispatch` first |
| Cost in dollars rather than tokens | Slice 2, which is where pricing lands |
| Per-case container images | When a fixture needs a dependency the one image lacks |
| Trend plots across many runs | When more than two baselines are worth comparing |
| Benchmarking harnesses other than nare | The trigger that promotes the bench to its own package |
| A deterministic replay tier | Decided against, not pending |

The replay tier was considered and rejected. It would replay recorded
transcripts through the graders for free, in CI, on every PR — but with the
model's replies frozen it cannot detect a better tool description or a worse
system prompt, which is the entire point. The machinery it would protect is
already protected by section 9's unit tests and section 8's `bin/bench verify`,
both of which are cheaper.

## 13. Acceptance criteria

1. `bin/bench verify` exits 0 on all five seed cases and exits non-zero on a
   deliberately broken case whose checks pass against the pristine fixture.
2. `bin/bench run --tier smoke` runs five cases at three reps each, writes one
   JSONL results file, and leaves no temp directory behind.
3. A rep whose container is killed at the wall clock is recorded as `error`,
   and a case that errors on two of three reps is reported `inconclusive` with
   exit 1.
4. A rep whose agent finishes with `status = "error"` at the turn limit is
   recorded as `fail`, not `error`.
5. `bin/bench compare` against a baseline with a different `model` exits 2
   without running a case.
6. A case whose pass rate drops below baseline triggers a seven-rep re-run
   before the report names it a regression, and a case that recovers exits 0.
7. A token median 3% above baseline exits 0; the same delta at 15% warns, and
   exits 1 under `--strict-tokens`.
8. `bin/bench bless` rewrites `baselines/smoke.toml` with the current commit,
   model and timestamp, and the diff is reviewable.
9. `bin/build` passes with `benchmarks` added to ruff and mypy.
10. The built wheel contains no `benchmarks` path.
