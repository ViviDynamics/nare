# Issue 257: full-tier baselines for the matrix models

**Date:** 2026-11-08
**Judge:** `claude-haiku-5-5` on the `openai` route, for every model (#239).
**Runs:** full tier, five reps per case, `bin/bench run --tier full`, one run per model.
**Baselines:** `benchmarks/baselines/gpt-6-1-sol.full.toml` and `gpt-6-luna.full.toml`, both blessed at commit `d0042b4`.

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

## Third matrix model: full tier not blessed

`glm-5.3-flash` (its gateway name is held outside this repository, see #260) is the third matrix model. Its full tier is still not blessed. Issue #262 stays open.

### The first attempt (2026-11-08)

Run `2026-11-08T13-59-17Z`, judge `claude-haiku-5-5`, five reps per case, public gateway. Fifteen of seventeen cases completed. `bootstrap-without-database` and `expense-tracker-cli` returned HTTP 524 on every rep, and so did their retries. The archived results of that run were not used again.

### The network path and the second attempt (2026-11-10)

The public gateway has a roughly 100-second limit, and the bootstrap case sends prompts of up to about 118k tokens. The LAN gateway has no such limit, but Docker containers on the host cannot reach it. The path that worked:

- A host-side forwarder, a stdlib Python HTTP server on `127.0.0.1:18766`, forwards every request, unchanged apart from hop-by-hop headers, to the LAN gateway, and streams each response back as it arrives. It logs method, path, status, bytes and duration only, never headers or bodies.
- The runner was not changed. Both the agent container and the host-side judge and endpoint checks use `NARE_BASE_URL=http://host.docker.internal:18766`. On this host `host.docker.internal` resolves to 127.0.0.1, and the container reaches the forwarder through it. The same nare build, image, cases, judge, reps and settings were used as the other runs in this note, with `NARE_PROVIDER=openai`, `NARE_STREAM=1` and `NARE_BENCH_JUDGE_MAX_TOKENS=4096`.
- nare commit: `a70e48f` (origin/main at the run). Run `2026-11-10T13-35-36Z`, the full tier, 67 reps, judge `claude-haiku-5-5`. The run finished with exit 0.

The forwarder logged 524 requests, every one answered with HTTP 200. Twelve took longer than 300 seconds, and the slowest took about 1670 seconds. So the gateway answered slowly and did not fail.

Per-case results of this run:

| case | pass | notes |
| --- | --- | --- |
| ambiguous-after-reading | 0/3 | (1/3 on the first attempt) |
| ambiguous-request | 0/3 | |
| bash-timeout-recovery | 5/5 | |
| bootstrap-without-database | no graded rep | 5 of 5 reps hit the case's 1800 s timeout |
| context-overflow | 5/5 | |
| context-overflow-small-window | 4/4 graded | 1 rep hit the 900 s timeout |
| edit-docstring | 3/3 | |
| expense-tracker-cli | 2/4 graded | 2 fail, 2 pass, 1 rep hit the 900 s timeout |
| fix-failing-test | 3/3 | |
| impossible-test | 0/3 | the known floor case |
| long-test-output | 5/5 | |
| mcp-fanout | 5/5 | |
| mcp-large-result | 5/5 | |
| multi-file-rename | 3/3 | |
| read-only-question | 3/3 | |
| stale-backup-cleanup | 3/3 | |
| typed-classify | 3/3 | |

Cost: nare reported $0.3733 across 67 reps. Seven reps reported no cost, so they count as unknown. Judge calls are not in that figure.

### Why no baseline

The failure has moved from the gateway to the rep wall clock. Through the LAN path, bootstrap reps ran past the case's 1800-second timeout, so the case has no graded rep. Blessing now would commit a baseline of sixteen cases with one missing, and the rule above is that such a baseline does not measure the full tier. The run was not repeated, per the coordinator's instruction. #262 remains open until bootstrap-without-database has a graded full-tier run, either with a longer rep timeout (a change to the case, which needs its own review) or with a model or path that finishes within the timeout.

## Not blessed

- Extended tier: opt-in, no baselines, unchanged.
- The third matrix model's full tier: see above. Its bootstrap case has no graded rep under the current timeout, so the baseline waits on #262.
