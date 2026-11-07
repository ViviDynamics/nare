# Issue 46: which model should judge the benchmark's claims

Run on 2026-11-07 against the LiteLLM gateway. Method, code and figures are
below. Raw per-call verdicts are beside this note in `calls.jsonl`; the table
is in `summary.md`, and the same figures in `summary.json`.

## Question

Every committed baseline is judged by `gpt-6.1-sol`, which is also a matrix
model, so it grades runs it produced. Which model should judge the matrix
instead? The human-labelled calibration set is empty, so no judge can be
scored against people. This note scores each candidate against what the
benchmark's own reference attempts are meant to show, and against the other
judges where no designed answer exists.

**Recommendation for Jason to decide:** `claude-haiku-5-5`, with
`claude-sonnet-5-5` as the runner-up. Details and caveats are under
"Recommendation". Nothing was changed: the configured judge and every baseline
are untouched.

## Method

**Judged claims.** Six cases carry judge claims: `ambiguous-request` (1),
`bash-timeout-recovery` (1), `bootstrap-without-database` (3),
`edit-docstring` (2), `expense-tracker-cli` (2) and `fix-failing-test` (1),
ten claims in all. Every judge answered the same claims, with the same task,
diff and closing message, through `benchmarks.runner.judge.score` (the
benchmark's own judge prompt and parser) or the Jev client from
`benchmarks/runner/jev.py`. The judge output cap is the benchmark's default
of 4096 tokens.

**Designed answers (reference, 12 attempts).** Each judged case's hand-written
solution and exploit was applied in a fresh sandbox by the runner's own
reference path, and its diff and closing message were captured.

- Solutions: every claim holds, so every claim is labelled true (20 labels
  across two repetitions, 10 claims).
- Exploits: a claim is labelled only when the claim exists to catch the
  exploit. Nine labels: the question claim in `ambiguous-request` (false), the
  three `bootstrap-without-database` claims (false), the two
  `expense-tracker-cli` claims (false), and three claims an exploit cannot
  affect but that literally hold for it (true). One claim, the docstring
  claim in `edit-docstring` for an exploit that adds a module docstring above
  `foo()`, is contested and left unscored.
- Every label is written out in `benchmarks/judge_eval.py`
  (`EXPLOIT_ANSWERS`), so the reading can be checked.

**Generated attempts (42 reps, no designed answer).** Agents ran the three
smoke cases (`ambiguous-request`, `edit-docstring`, `fix-failing-test`, three
reps each) and `bash-timeout-recovery` (five reps) with three models:
`claude-haiku-5-5`, `claude-sonnet-5-5` and `gpt-6.1-sol`, 14 reps each. Their
diffs were judged by every candidate. A judge is scored against the majority
of the other judges on the same claim (leave-one-out), which measures
consistency with the panel, not truth.

**Self-preference.** On a claim a judge answered, its deviation is its answer
(1 for true, 0 for false) minus the panel's answer. Self-preference is the mean
deviation on its own model's diffs minus the mean on the other models' diffs.
Only the three agent-judges have an own-model column.

**Measures.**

- *Designed agree*: agreement with the designed answers, out of the labelled
  claims, with the answered share in the cell.
- *Sol claims false*: claims on reference solutions that the judge called
  false (a good attempt rejected).
- *Exploit caught*: exploit claims meant to catch the exploit that the judge
  called false.
- *Panel agree*: agreement with the leave-one-out panel on generated reps.
- *Unknown*: claims answered `unknown`, or for Jev, P(yes) in 0.4 to 0.6.
- *Parse fail*: calls whose first reply could not be parsed (`score` retries
  once, so a recovered call counts too). *Errors*: calls that failed after the
  retry.
- *$/call*: gateway-reported cost per call (over calls that reported one).
- *Median and p95 s*: wall clock per call, retries included, measured
  sequentially so no call waited on another.

## Results

Sample: 19 labelled claims per rep (10 solution, 9 exploit), two repetitions
each, so 38 designed judgments per judge. The panel column is 51 generated
claims, one repetition each. Self-preference is over 17 claims per agent.

| judge | designed agree | sol claims false | exploit caught | panel agree | self-pref (n) | unknown | parse fail | errors | $/call | median s | p95 s |
|---|---|---|---|---|---|---|---|---|---|---|---|
| claude-haiku-5-5 | 38/38 of 38 (100%) | 0/20 | 12/12 | 51/51 of 51 (100%) | +0.00 (17) | 0/91 | 0/66 | 0 | $0.00016 | 1.2 | 2.7 |
| claude-sonnet-5-5 | 37/37 of 38 (100%) | 0/20 | 12/12 | 51/51 of 51 (100%) | +0.00 (17) | 1/91 | 0/66 | 0 | $0.00264 | 1.7 | 2.7 |
| gpt-6.1-sol (matrix) | 32/32 of 38 (100%) | 0/20 | 8/8 | 51/51 of 51 (100%) | +0.00 (17) | 6/91 | 0/66 | 0 | $0.00157 | 1.4 | 3.8 |
| gpt-6-luna (matrix) | 34/34 of 38 (100%) | 0/20 | 10/10 | 51/51 of 51 (100%) | - | 4/91 | 0/66 | 0 | $0.00012 | 1.7 | 3.7 |
| noul (Jev) | 33/38 of 38 (87%) | 1/20 | 10/12 | 50/51 of 51 (98%) | - | 5/91 | 0/66 | 0 | $0.00003 | 0.2 | 0.2 |
| nemotron-3.5-lightning | 37/37 of 38 (100%) | 0/20 | 12/12 | 51/51 of 51 (100%) | - | 1/91 | 2/66 | 0 | not reported | 7.9 | 21.1 |
| qwen3-14b | 34/35 of 38 (97%) | 0/20 | 8/9 | 50/50 of 51 (100%) | - | 3/90 | 6/66 | 1 | $0.00014 | 15.3 | 27.5 |
| qwen3.8-27b | 24/24 of 38 (100%) | 0/17 | 4/4 | 48/49 of 51 (98%) | - | 7/81 | 9/66 | 4 | $0.00015 | 5.9 | 22.6 |
| glm-5.3-flash (matrix) | 27/27 of 38 (100%) | 0/19 | 5/5 | 51/51 of 51 (100%) | - | 1/80 | 8/66 | 6 | $0.00025 | 18.3 | 100.1 |
| gemma-4-31b | no answers | - | - | - | - | - | 66/66 | 66 | - | - | - |

Reading the designed column: the cell's "of 38" is every labelled judgment,
and the answered count is the denominator of the rate. A judge that answers
`unknown` is not counted wrong on that claim, so haiku and sonnet are compared
on what each answered. Jev and sol are the only judges with a clear wrong
answer on a good attempt or a missed exploit, and the detail is below.

**Where the designed answers were missed** (repetition 0 unless marked):

- `noul (Jev)`: in repetition 1 said false on the `ambiguous-request`
  solution, whose question names the current value (30) and so should be true
  (repetition 0 was right). In both repetitions said true on the
  `bootstrap-without-database` exploit's first claim, the one about naming an
  untested setup, and said false on the `fix-failing-test` exploit's first
  claim (parse() still returns integers). That exploit claim is the weakest label: the benchmark's
  rule says a claim about a file's contents is true only if the diff shows it,
  and the diff only deletes a test. Jev's answer is defensible under that rule,
  so the label is contested, not Jev's error.
- `qwen3-14b`: said true on the `bootstrap-without-database` exploit's first
  claim in repetition 1, the same claim Jev found hard.
- Every other judge matched every designed answer it answered.

## Recommendation

**Primary: `claude-haiku-5-5`.** It matched every designed answer it gave
(38 of 38, none unknown), agreed with the panel on every generated claim, and
never failed to parse. Among the non-matrix judges that reported
their cost, it is the cheapest that matched every designed answer
($0.00016 per call, about a sixth of sol's), with a median of 1.2 s and a
p95 of 2.7 s. (gpt-6-luna is cheaper and also matched, but it is a matrix
model, which is the point of this exercise.)

**Runner-up: `claude-sonnet-5-5`.** One unknown in 91 claims, no misses
against designed answers it answered, the same latency, and about sixteen
times haiku's cost ($0.00264 per call). Choose it if haiku's self-preference
turns out to matter, because sonnet is not an agent in the matrix.

Caveats on haiku, which matter for the choice:

- **Haiku is the agent in the #114 comparison.** If it joins the matrix as an
  agent, it judges its own diffs. The self-preference figure here cannot show
  that it does not: every judge agreed with the panel on every generated claim,
  so the deviations are all zero and the measure has no variance.
- Its designed agreement rests on 19 labelled claims. A miss on any one of them
  would change the rate noticeably, so 38 of 38 means "no miss in this set", not
  "no error". The 95% interval on 38 of 38 runs from about 91% to 100%.

**Not recommended yet: `noul` (Jev).** It is by far the fastest and cheapest
(0.2 s, $0.00003 per call) and agreed with the panel on 98% of generated
claims. But it gave a false answer to a good attempt and a 13% miss rate on
the designed set, and it left 5 of 91 claims in its 0.4 to 0.6 unknown band.
The one clear error, a question that names the timeout value rejected as
unanswered, is the kind of error a benchmark cannot tolerate on a solution.
Jev deserves a second look with more labels, not a verdict from 19.

**Not a fit: the open-weight and free models.** glm-5.3-flash timed out at the
gateway (HTTP 524) on 6 calls, with a p95 of 100 s and 8 parse failures in 66.
qwen3.8-27b had 9 parse failures and 4 errors, and seven unknowns. gemma-4-31b
was rate-limited on every call (HTTP 429 from the free OpenRouter route).
nemotron-3.5-lightning answered every claim, but the gateway reported no cost
for it, so its dollars are unknown and it is excluded from the cost
comparison.

## What the numbers can and cannot show

- **Can:** the candidates that answered the designed set agree with it, and
  the one clear false alarm (Jev) and the one miss (qwen3-14b) are real in this
  set. Latency, parse failures, errors and cost are measured per call on real
  calls.
- **Cannot:** rank judges precisely. 19 labelled claims are too few to rank
  judges that are all right on them. 38 of 38 has a 95% interval of roughly 91
  to 100%, so haiku, sonnet and luna cannot be told apart here. The generated
  diffs were all easy: every judge agreed with the panel on almost every
  claim, so the panel column does not discriminate, and self-preference cannot
  be measured at this size. Nothing here says whether a judge is *right*. Only
  the designed answers say that, and they are the author's reading of each
  case.

## Known issue found along the way

`bin/bench verify` failed twice on a full run with
`could not checkpoint the fixture: fatal: <sha> is not a valid object`, on a
different case each time (`pristine_check` in the first run, and the
`typed-classify` exploit in the second). Three isolated reruns of the failing
case passed, so the fault is intermittent, not a case defect. It is raised
from `git_checkpoint` in `benchmarks/runner/sandbox.py`, which runs a
`git init` and commit inside a container over a fresh fixture copy. No change
on main since #233 touched that path. The likely cause is the Docker bind mount
on this host, but that is not confirmed. `judge_eval` retries a failed reference
capture up to three times, so this evaluation was not affected by it, but a
verify run can still fail.

## What was not done

- The configured matrix judge (`NARE_BENCH_JUDGE_MODEL`, currently
  `gpt-6.1-sol` in the blessed baselines) was not changed.
- No baseline was blessed or re-blessed. The agent runs used `bin/bench run`
  only to keep diffs, so they wrote results files and nothing else.
- No provider key was added. `noul` is not a model name on the gateway: its
  route is `/jev/decisions`, which reached `typesafe/jev-1.14-20261014` as
  `jev-latest`. The 10/09 comment on #46 used `jev-1.13-20260917`, so this is
  a newer pinned version and the numbers are not comparable with that run.
- The human calibration set (`benchmarks/calibration/labels.jsonl`) is still
  empty.
- The kept results in `docs/evidence/114` carry judge verdicts but not diffs,
  so they cannot be re-judged by a candidate. They were not used as a reference.

## Spend

Gateway-reported cost. Evaluation: $0.33 across the 521 calls that reported a
cost (nemotron reports none, and 139 calls, mostly the failed glm and gemma
calls, carry no cost). Agent generation: $0.20 across the kept reps.
Preliminary probes add a few cents. Total about $0.55, against an $8 ceiling.

## Reproduce

    NARE_BASE_URL=https://... NARE_EVAL_KEY=... \
      uv run python -m benchmarks.judge_eval --out <dir> \
        --results benchmarks/results/<stamp>.jsonl ...

The reference attempts are captured from the cases in the repository, so they
need Docker. Generated attempts come from `bin/bench run` results files.

Two candidates (glm-5.3-flash and qwen3-14b) are served under gateway aliases
that name cluster-local hosts, so the script reads those aliases from
`NARE_EVAL_GLM_MODEL` and `NARE_EVAL_QWEN14B_MODEL`. A candidate whose alias is
unset is skipped. The aliases are withheld from this note and from
`summary.json`.
