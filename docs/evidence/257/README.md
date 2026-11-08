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

`glm-5.3-flash` (its gateway name is held outside this repository, see #260) ran the full tier once: judge `claude-haiku-5-5`, five reps per case, run `2026-11-08T13-59-17Z` (67 reps, $0.33 reported cost). Fifteen of its seventeen cases completed. Two did not.

- `bootstrap-without-database` and `expense-tracker-cli` returned HTTP 524 (a gateway origin timeout) on every rep of that run. Each got a retry, and each retry failed on every rep the same way. The bootstrap case sends prompts of up to about 118k tokens.
- Per-case counts for the fifteen completed cases, from that run: ambiguous-after-reading 1/3, ambiguous-request 0/3, bash-timeout-recovery 4/4, context-overflow 5/5, context-overflow-small-window 3/3, edit-docstring 3/3, fix-failing-test 2/2, impossible-test 0/3, long-test-output 5/5, mcp-fanout 4/4, mcp-large-result 5/5, multi-file-rename 3/3, read-only-question 3/3, stale-backup-cleanup 3/3, typed-classify 3/3. A count below five is a case with errored reps.

No full-tier baseline is committed for this model. A baseline of fifteen of seventeen cases would not measure the full tier, so none was written. Its smoke baseline is in place (#260). The open item is tracked in #262.

## Not blessed

- Extended tier: opt-in, no baselines, unchanged.
- The third matrix model's full tier: see above. Its two failing cases are a gateway timeout, and the baseline waits on them (#262).
