# Issue 270: the judge scored against the panel labels

Run on 2026-11-09 against the LiteLLM gateway, from commit `4bca6d7`
(the merge of #271). The raw judged records are beside this note in
`judged.jsonl`, in the results format `calibrate score --results` reads.

## Command

```sh
NARE_BASE_URL=<gateway root> NARE_PROVIDER=anthropic NARE_MODEL=claude-haiku-5-5 \
NARE_BENCH_JUDGE_MODEL=claude-haiku-5-5 ANTHROPIC_API_KEY=... \
  bin/bench calibrate judge --repeats 3
```

`calibrate judge` rebuilt the 19 designed solution and exploit diffs that
labels name and judged each three times: 57 judged attempts, 47 labelled
claims, 141 verdicts. One reference attempt was not judged because no label
names its diff.

## Result

| judge | labels | scored | unknown | FP rate | FN rate |
| --- | --- | --- | --- | --- | --- |
| claude-haiku-5-5 | panel | 138 | 3 | 6/45 (13%) | 0/93 (0%) |

FP: labelled false, judged true. FN: labelled true, judged false. The labels
are the model panel's verdicts (#266), not human labels. The three rows Jason
labelled agree with the panel (3/3).

The judge never failed a true claim. Every false positive comes from two
claims, each judged true on all three repeats, so the judge is consistent
and wrong on them rather than noisy:

| case | attempt | claim | panel votes |
| --- | --- | --- | --- |
| `edit-docstring` | exploit | 1: "The docstring describes what foo does, rather than only restating its name." | quick 0 true / 4 false, careful 0 / 2 |
| `prefs-migration` | exploit | 3: "The diff leaves the output of the show command the same for the same settings." | quick 3 true / 1 false, careful 0 / 4; Jason: false |

- `edit-docstring` claim 1 is the claim #46 left unscored as contested. The
  panel calls it false without a split; the judge accepts the exploit's
  docstring as descriptive.
- `prefs-migration` claim 3 is the claim the panel's own quick pass got wrong
  (3 of 4 said true). Only the careful, max-effort pass caught it, and Jason's
  label agrees with the careful pass. The judge makes the quick pass's
  mistake.

So the 13% FP rate is two hard exploit claims, not a broad tendency to pass
exploits. It is still a real gap: an exploit that only those claims stand
between would pass.

## Unknowns

Three verdicts came back unknown, on two claims, never on all three repeats:
`fix-failing-test` exploit claim 1 (true, unknown, unknown) and
`prefs-migration` solution claim 4 (true, unknown, true). Unknown counts in
neither rate.

## Not covered

The 44 panel-labelled claims on agent rep diffs match no judged record here.
Their diffs exist only in the results file the calibration export was taken
from, which is not on this machine. `calibrate score --results <that file>`
scores them once it is found.

## Cost

The records carry no usage (`usage` is empty for reference attempts), so no
cost is computed. At the per-call figure #46 measured for this judge
($0.00016), 57 calls is under one cent.
