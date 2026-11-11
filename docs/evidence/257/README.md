# Issue 257: full-tier baselines for the matrix models

**Date:** 2026-11-08
**Judge:** `claude-haiku-5-5` on the `openai` route, for every model (#239).
**Runs:** full tier, five reps per case, `bin/bench run --tier full`, one run per model.
**Baselines:** `benchmarks/baselines/gpt-6-1-sol.full.toml` and `gpt-6-luna.full.toml`, both blessed at commit `d0042b4`.
`benchmarks/baselines/glm-5-3-flash.full.toml` is blessed at commit `66c42f3` at a timeout scale of 2, see the GLM section (#262).

## Per-case pass counts

| case | gpt-6.1-sol | gpt-6-luna |
| --- | --- | --- |
| ambiguous-after-reading | 3/3 | 3/3 |
| ambiguous-request | 3/3 | 0/3 |
| bash-timeout-recovery | 5/5 | 5/5 |
| bootstrap-without-database | 4/5 | 5/5 |
| context-overflow | 5/5 | 5/5 |
| context-overflow-small-window | 5/5 | 5/5 |
| edit-docstring | 3/3 | 3/3 |
| expense-tracker-cli | 4/5 | 3/5 |
| fix-failing-test | 3/3 | 3/3 |
| impossible-test | 0/3 | 0/3 |
| long-test-output | 5/5 | 5/5 |
| mcp-fanout | 4/5 | 5/5 |
| mcp-large-result | 5/5 | 5/5 |
| multi-file-rename | 3/3 | 3/3 |
| read-only-question | 3/3 | 3/3 |
| stale-backup-cleanup | 3/3 | 3/3 |
| typed-classify | 3/3 | 3/3 |
| **total** | **61/67** | **59/67** |

Cases with three reps (the smoke cases) are shown as `n/3`. The rest have five. `impossible-test` is 0/3 for both models, the known floor case from #239, and is not gated.

Results files, not committed: `benchmarks/results/2026-11-08T05-44-16Z.jsonl` (sol) and `2026-11-08T06-08-37Z.jsonl` (luna). No rep errored on either run.

## Cost

Nare-reported cost, summed over every rep of each run (no rep lacked a reported cost, so none counted as unknown):

- gpt-6.1-sol full: $2.2539 over 67 reps, about $0.034 per rep.
- gpt-6-luna full: $0.1325 over 67 reps, about $0.002 per rep.

Judge calls are not in the per-rep cost. This lane's total spend, every run included (the two full runs, the smoke matrix run, and the smoke runs for the three matrix models), is $3.21 by reported cost, against the $15 ceiling. The GLM full run and its two retries add $0.33, $0.002 and $0.006. The pilot on the sol full tier projected the cost: it ran first, and the luna cost was projected from the earlier smoke ratio.

## Third matrix model: full tier blessed at timeout scale 2

`glm-5.3-flash` (its gateway name is held outside this repository, see #260) now has a full-tier baseline, `benchmarks/baselines/glm-5-3-flash.full.toml`. It was blessed from one full-tier run with the `claude-haiku-5-5` judge, five reps per case, and a per-case timeout scale of 2 (#262).

### Why a timeout scale

Two cases were too slow for their timeouts through the public gateway. `bootstrap-without-database` has a 1800 s timeout in its case file, and `expense-tracker-cli` has the full-tier default of 900 s. On 2026-11-08 both returned HTTP 524 on every rep (the first attempt, not used again).

The LAN path below removed the 524s. But on 2026-11-10 every bootstrap rep then reached its 1800 s limit, and one expense rep reached its 900 s limit. The forwarder logged all 524 requests as HTTP 200, so the gateway was slow rather than failing. That run (`2026-11-10T13-35-36Z`) was not blessed.

#285 added `bin/bench run --timeout-scale N`, which multiplies each case's own timeout for that run. The case files and their hashes are unchanged. Each rep records the scale it ran under, and a baseline records it per case when it is not 1.0.

Bless reads one results file, so the two slow cases could not be merged into a full baseline without moving files by hand. The whole tier was rerun at scale 2 instead, so every case in the baseline was measured in one run at one scale.

### Network path

- A host-side forwarder, a stdlib Python HTTP server on `127.0.0.1:18766`, forwards every request, unchanged apart from hop-by-hop headers, to the LAN gateway, and streams each response back. It logs the method, path, status, bytes and duration, never headers or bodies.
- No runner change. The agent container and the host-side judge and endpoint checks all use `NARE_BASE_URL=http://host.docker.internal:18766`. On this host `host.docker.internal` resolves to 127.0.0.1, and the container reaches the forwarder through it. The LAN gateway itself is not reachable from the container.
- Settings: `NARE_PROVIDER=openai`, `NARE_STREAM=1`, `NARE_BENCH_JUDGE_MAX_TOKENS=4096`, judge `claude-haiku-5-5`, nare commit `66c42f3` (origin/main at the run), `--tier full --timeout-scale 2`.
- Run `2026-11-11T01-07-05Z`, exit 0, finished 06:31Z. The forwarder logged all requests as HTTP 200.

### Per-case pass counts, blessed run

| case | pass | notes |
| --- | --- | --- |
| ambiguous-after-reading | 0/3 | |
| ambiguous-request | 0/3 | |
| bash-timeout-recovery | 5/5 | |
| bootstrap-without-database | 4/5 | 1 fail; all five reps completed |
| context-overflow | 5/5 | |
| context-overflow-small-window | 5/5 | |
| edit-docstring | 3/3 | |
| expense-tracker-cli | 1/4 graded | 3 fail, 1 pass, 1 rep hit the 1800 s limit and is not graded |
| fix-failing-test | 3/3 | |
| impossible-test | 1/3 | the known floor case, not gated |
| long-test-output | 5/5 | |
| mcp-fanout | 5/5 | |
| mcp-large-result | 5/5 | |
| multi-file-rename | 3/3 | |
| read-only-question | 3/3 | |
| stale-backup-cleanup | 3/3 | |
| typed-classify | 3/3 | |

Infrastructure errors (timeouts) are left out of the pass rate, as `bless` always does. The expense case's 1/4 rests on four graded reps, so it is a low-count capability cell, not gated. `impossible-test` was 0/3 on the 2026-11-10 run, which is the same floor case.

`bin/bench compare --tier full --model glm-5.3-flash --judge-model claude-haiku-5-5 --provider openai` reads the baseline back with no regressions.

### Cost

- This run: $0.5772 by reported cost over 67 reps. One rep reported no cost and is not counted.
- The 2026-11-10 run: $0.3733 over 67 reps, seven without a reported cost.
- Judge calls are not in either figure.

## Not blessed

- Extended tier: opt-in, no baselines, unchanged.
