# #43: compaction point sweep on context-overflow-small-window

Part 2 of #43. Part 1 (the configurable trigger and target, PR #283) is merged as `a8c9d30`.

## Result

**No trigger point separated the models on this case, and the evidence does not support changing the default.** The gpt models passed every rep at every trigger, so the case is at its ceiling and cannot rank the points. The one model where the default is weak (GLM) was run at one point only, and its sweep is incomplete.

| model | trigger / target | reps | passed | rate, 95% Wilson | Fisher p vs 0.8 |
| --- | --- | --- | --- | --- | --- |
| gpt-6-luna | 0.2 / 0.15 | 15 | 15 | 1.00 [0.80, 1.00] | 1.00 |
| gpt-6-luna | 0.3 / 0.225 | 15 | 15 | 1.00 [0.80, 1.00] | 1.00 |
| gpt-6-luna | 0.5 / 0.375 | 15 | 15 | 1.00 [0.80, 1.00] | 1.00 |
| gpt-6-luna | 0.8 / 0.6 (default) | 15 | 15 | 1.00 [0.80, 1.00] | |
| gpt-6.1-sol | 0.2 / 0.15 | 15 | 15 | 1.00 [0.80, 1.00] | 1.00 |
| gpt-6.1-sol | 0.3 / 0.225 | 20 | 20 | 1.00 [0.84, 1.00] | 1.00 |
| gpt-6.1-sol | 0.5 / 0.375 | 15 | 15 | 1.00 [0.80, 1.00] | 1.00 |
| gpt-6.1-sol | 0.8 / 0.6 (default) | 15 | 15 | 1.00 [0.80, 1.00] | |
| glm-5.3-flash | 0.8 / 0.6 (default) | 5 | 3 | 0.60 [0.23, 0.88] | |

The sol 0.3 point has 20 reps because one of its invocations ran ten reps, not five. All ten passed, and the rows are kept.

The full per-cell figures are in `summary.md` and `cells.csv`, with the rep-level data in `reps.csv`. Every cell has zero fabricated sentinels. Each gpt rep recorded all eight sentinels in order.

GLM stopped after its first invocation (see "What did not run"). Its two failures both ended the run with `stop_reason=context`, meaning the estimate reached the window and nare stopped without writing `findings.json`. At the default trigger GLM hit that stop in 2 of 5 reps. That is the failure a lower trigger is meant to prevent, so it is the most important open question, and this sweep has not answered it.

## Why the case cannot rank the points

The case is `context-overflow-small-window` (48,000-token window, eight hex logs, one read per turn). It does force compaction at every point: the elided result counts are 6 to 9 per rep, and at lower triggers the token count falls (sol: 330k at 0.8, 297k at 0.5, 281k at 0.3, 280k at 0.2).

Compaction is not the same as lost information, though. An elided result leaves a stub that says "Rerun the tool if you need it," and `log.py` is deterministic, so a model that needs a log again can simply run it again. The check counts a re-read as a read. The case therefore measures whether the model recovers by rereading, not whether it retains what was elided. Both gpt models do that reliably at every point, so the outcome is flat.

A case that can separate trigger points needs a result that cannot be regenerated, or a window small enough that rereading itself runs out of room. Both are follow-ups (see below).

## Design

- **Sweep.** `bin/bench run --tier full --case context-overflow-small-window -- --compaction-trigger T --compaction-target G`, with `G = 0.75 T` so each point keeps the default ratio of target to trigger (0.6 / 0.8). Points: 0.2, 0.3, 0.5 and the default 0.8. A tier run gives five reps, so each point was three invocations of five reps (fifteen reps), except for the extra invocation noted above.
- **Outcome.** The case's own checks: every log's sentinel must be in a result, or a rep must have read it, and `findings.json` must hold the eight sentinels in order. Pass rate is the check result.
- **Quality.** `analyze.py` reads each rep's kept session and counts sentinels written in position (`correct`), entries that are not one of the eight (`fabricated`), reads, elided results and the stop reason.
- **Statistics.** Wilson 95% intervals per cell. Fisher's exact test (two-sided, uncorrected for the four points) of each point against the default.
- **Cost.** Reported per rep by nare; `cost_estimated` is true where nare estimated it from prices.
- **Settings.** `NARE_STREAM=1` for the gateway, `NARE_BENCH_JUDGE_MODEL=claude-haiku-5-5` (the case has no judged checks, so the judge was not called). The case's hash differs from the one in the blessed full baseline, so the runs used `--allow-case-change` and `--allow-arg-change`. Nothing was blessed, and the default is unchanged.
- **Elision only.** Every run used the default `--compaction elide`. The summary tier was not swept.

## Spend

About $5.67 in total, across 130 reps: gpt-6-luna $0.56 (60 reps), gpt-6.1-sol $4.88 (65 reps), GLM $0.23 (5 reps). Sol's cost is dominated by the first rep of each invocation, which pays for a cold prompt cache ($0.5 to $0.7), while warm reps cost about $0.035. The cap was $15.

## What did not run

- **GLM, triggers 0.5, 0.3 and 0.2, and the rest of the default point.** The GLM sweep was stopped on the coordinator's instruction, after its first invocation finished at 01:05 UTC (the runner was stopped at 01:06:43 UTC). A GLM invocation took 37 minutes, so the full GLM sweep would have run about seven and a half hours on the shared forwarder that #262 needs. Invocation two started by mistake and was killed mid-rep; it wrote no results file and is not counted.
- **A case that separates the points.** Not built. See "Why the case cannot rank the points".

## Recommendation

1. **Do not change the default** on this evidence. The gpt models are at the ceiling, and GLM's single point shows a failure but no comparison.
2. **Finish GLM on the lower points** (0.3 and 0.2 first, one invocation each, about 75 minutes) once the forwarder is free, and confirm with the coordinator first.
3. **Build a case that a lower trigger can actually help**: a result that cannot be regenerated and must be used after it has been elided, or a window small enough that rereading runs out. Judge the outcome on that case.
4. Revisit the default only after (2) and (3). If GLM passes more reps at 0.5 or below with the context stop gone, that would support a per-model setting, not a global change, and that is a design decision to put to the team first.

## Files

- `analyze.py`: the analyzer. Run it with `-I`: `python3 -I analyze.py benchmarks/results OUT_DIR benchmarks/results/*.jsonl`.
- `reps.csv`, `cells.csv`, `summary.md`: the analyzer's output for these runs.
- `results/*.jsonl`: the 25 bench results files for these runs. The kept session transcripts are not committed; they are reproduced by the analyzer from `benchmarks/results`.
