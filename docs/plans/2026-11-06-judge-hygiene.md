# Benchmark: judge hygiene, and a small human-labelled calibration set

Issue #157

## Scope
In: the judge no longer defaults to the model under test; its output cap is
configurable with a larger default; `unknown` per claim and one reason per claim;
judge failures stay in the pass-rate denominator; a labelled calibration set with
its format, validation, blind export and FP/FN scoring.
Out: the labels themselves (people label them, after this lands); re-blessing the
matrix with a judge from outside it (paid runs, and a choice of model that is
Jason's); pinning the judge cap in baselines (a follow-up, see the PR body).

## Assumptions
- A judged run with no judge named refuses to start. A judge equal to the model under
  test is allowed, and warned about. A matrix judge that is also a matrix model is
  warned about and not refused, because refusing would stop the current matrix until
  it is re-blessed.
- Default judge cap 4096 for every judge, not a model-specific guess at "reasoning".
- An `unknown` decides a rep only when the threshold is already settled either way;
  otherwise the rep is a judge failure.
- A judge failure is a rep that did not pass: it is in the pass-rate denominator.
  Infrastructure errors stay out, as before.
- The calibration rates are per judge model and matched on case, claim and the diff's
  SHA-256, so a judge change is measured again rather than assumed.

## Tasks
- [x] 1. Judge config: no default judge; `judge_max_tokens` from NARE_BENCH_JUDGE_MAX_TOKENS.
- [x] 2. Judge reply: `unknown` per claim and one reason per claim; the old shared
  `why` is refused.
- [x] 3. Grading: an unknown is never a false; undecidable reps are errors.
- [x] 4. Reporting: judge failures in the denominator, shown beside the case.
- [x] 5. Calibration module and `calibrate export|check|score`; `run` prints the rates.
