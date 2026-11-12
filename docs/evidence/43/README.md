# #43: compaction point sweep on context-overflow-small-window

Part 2 of #43. Part 1 (the configurable trigger and target, PR #283) is merged as `a8c9d30`. This evidence is PR #286 (`5bf40d7`) plus the GLM points added after it.

## Result

**No change to the default is supported.** The two gpt models passed every rep at every trigger, so the case is at its ceiling and cannot rank the points. For GLM, the lower triggers removed the context stop that the default produced, but at the cost of a per-rep timeout. The sample is too small to say which effect wins.

| model | trigger / target | reps | passed | rate, 95% Wilson | context stops | timeouts | pass, timeouts as fails [95% Wilson] |
| --- | --- | --- | --- | --- | --- | --- | --- |
| gpt-6-luna | 0.2 / 0.15 | 15 | 15 | 1.00 [0.80, 1.00] | 0 | 0 | |
| gpt-6-luna | 0.3 / 0.225 | 15 | 15 | 1.00 [0.80, 1.00] | 0 | 0 | |
| gpt-6-luna | 0.5 / 0.375 | 15 | 15 | 1.00 [0.80, 1.00] | 0 | 0 | |
| gpt-6-luna | 0.8 / 0.6 (default) | 15 | 15 | 1.00 [0.80, 1.00] | 0 | 0 | |
| gpt-6.1-sol | 0.2 / 0.15 | 15 | 15 | 1.00 [0.80, 1.00] | 0 | 0 | |
| gpt-6.1-sol | 0.3 / 0.225 | 20 | 20 | 1.00 [0.84, 1.00] | 0 | 0 | |
| gpt-6.1-sol | 0.5 / 0.375 | 15 | 15 | 1.00 [0.80, 1.00] | 0 | 0 | |
| gpt-6.1-sol | 0.8 / 0.6 (default) | 15 | 15 | 1.00 [0.80, 1.00] | 0 | 0 | |
| glm-5.3-flash | 0.3 / 0.225 | 5 | 4 | 1.00 [0.51, 1.00] (n = 4) | 0 | 1 | 4/5 [0.38, 0.96] |
| glm-5.3-flash | 0.2 / 0.15 | 5 | 4 | 1.00 [0.51, 1.00] (n = 4) | 0 | 1 | 4/5 [0.38, 0.96] |
| glm-5.3-flash | 0.8 / 0.6 (default) | 5 | 3 | 0.60 [0.23, 0.88] | 2 | 0 | 3/5 [0.23, 0.88] |

"Reps" counts the five planned per point. The bench excludes an errored rep from n, so the GLM lower points show n = 4 in the rate column. A timeout is an errored rep (`error_cause=timeout`, the 900-second per-rep limit), and the bench does not grade it. The last column counts each timeout as a failure, which is the view for a question about whether a rep finishes the task.

The full per-cell figures are in `summary.md` and `cells.csv`, with the rep-level data in `reps.csv`. Every gpt rep and every GLM rep that finished recorded the eight sentinels, with no fabricated sentinels in any of them.

Fisher's exact test of each point against the default (uncorrected for the number of points) gives p = 1.00 for every gpt point and p = 0.44 for each GLM lower point against the default. Those tests use the bench's n, so they are not comparable to the timeout-as-failure view.

## The GLM finding

At the default trigger GLM hit `stop_reason=context` in 2 of 5 reps. Nare stopped with the estimate at the window and no findings written. At triggers 0.3 and 0.2 no rep hit the context stop. That is the failure a lower trigger is meant to prevent, and it happened, in direction, as intended.

The lower triggers introduced a different failure: one rep at each point ran past the 900-second per-rep limit. The timed-out reps are the 2nd rep at 0.3 and the 5th at 0.2. The reps that finished at these points took 215 to 520 seconds, so the 900-second limit is about twice the slowest finished rep. The timeout is not a context failure, and it is the first thing to check before reading these numbers as a gain. The per-call timings for those reps were not captured in this sweep.

So the GLM evidence is a trade: a lower trigger removes the context stops and adds a time-budget risk. On four and five reps, neither effect is distinguishable from the default.

## Why the case cannot rank the gpt points

The case is `context-overflow-small-window` (48,000-token window, eight hex logs, one read per turn). It does force compaction at every point: the elided result counts are 6 to 9 per rep, and at lower triggers the token count falls (sol: 330k at 0.8, 297k at 0.5, 281k at 0.3, 280k at 0.2).

Compaction is not the same as lost information. An elided result leaves a stub that says "Rerun the tool if you need it," and `log.py` is deterministic, so a model that needs a log again can run it again. The check counts a re-read as a read. The case therefore measures whether the model recovers by rereading, not whether it retains what was elided. Both gpt models do that reliably at every point, so the outcome is flat.

A case that can separate trigger points needs a result that cannot be regenerated, or a window small enough that rereading itself runs out of room.

## Design

- **Sweep.** `bin/bench run --tier full --case context-overflow-small-window -- --compaction-trigger T --compaction-target G`, with `G = 0.75 T` so each point keeps the default ratio of target to trigger (0.6 / 0.8). Points: 0.2, 0.3, 0.5 and the default 0.8. A tier run gives five reps, so each gpt point was three invocations of five reps, except for the extra invocation noted below. GLM had one invocation per point.
- **Outcome.** The case's own checks: every log's sentinel must be in a result, or a rep must have read it, and `findings.json` must hold the eight sentinels in order.
- **Quality.** `analyze.py` reads each rep's kept session and counts sentinels written in position (`correct`), entries that are not one of the eight (`fabricated`), reads, elided results, stop reason and timeouts.
- **Statistics.** Wilson 95% intervals per cell, computed on the bench's n, and on the timeout-as-failure n.
- **Cost.** Reported per rep by nare; `cost_estimated` is true where nare estimated it from prices.
- **Settings.** `NARE_STREAM=1` for the gateway, `NARE_BENCH_JUDGE_MODEL=claude-haiku-5-5` (the case has no judged checks, so the judge was not called). The case's hash differs from the one in the blessed full baseline, so the runs used `--allow-case-change` and `--allow-arg-change`. Nothing was blessed, and the default is unchanged.
- **Elision only.** Every run used the default `--compaction elide`. The summary tier was not swept.
- **GLM routing.** Through the host forwarder at 127.0.0.1:18766, with the gateway name looked up at run time and not written anywhere. The gpt models used the public gateway.

The sol 0.3 point has 20 reps because one of its invocations ran ten reps, not five. All ten passed, and the rows are kept.

## Spend

About $6.08 across 140 reps: gpt-6-luna $0.56 (60 reps), gpt-6.1-sol $4.88 (65 reps), GLM $0.64 (15 reps: 5 at the default, 5 at 0.3 and 5 at 0.2). Sol's cost is dominated by the first rep of each invocation, which pays for a cold prompt cache ($0.5 to $0.7), while warm reps cost about $0.035. The cap was $15 for the sweep and $2 for the GLM follow-up, which spent $0.41.

## What did not run, and why

- **A longer GLM run.** One invocation per lower point is the approved scope. Fifteen reps per point, the gpt design, would show whether the timeout rate and the context-stop rate are real.
- **A case that separates the points.** Not built. See "Why the case cannot rank the gpt points".
- **The summary tier.** Not swept.

## Recommendation

1. **Do not change the default**, globally or for GLM, on this evidence. The gpt models are at the ceiling, and GLM's comparison is at four and five reps with a timeout in each lower point.
2. **If a GLM-specific setting is considered later**, first find out why GLM's reps time out at the lower points (model latency, or a loop that the lower trigger starts), and run fifteen reps per point with timeouts counted as failures. A per-model setting is a design question for the team, not a default change.
3. **Build a case a lower trigger can actually help**: a result that cannot be regenerated and must be used after it has been elided, or a window small enough that rereading runs out.

## Files

- `analyze.py`: the analyzer. Run it with `-I`: `python3 -I analyze.py benchmarks/results OUT_DIR benchmarks/results/*.jsonl`.
- `reps.csv`, `cells.csv`, `summary.md`: the analyzer's output for these runs, including the GLM rows at each point.
- `results/*.jsonl`: the 27 bench results files for these runs. The kept session transcripts are not committed; the analyzer reads them from `benchmarks/results`.
