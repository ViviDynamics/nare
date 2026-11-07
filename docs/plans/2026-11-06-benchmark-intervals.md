# Benchmark: report intervals, store pass counts, guard-only gate

Issue #154

## Scope
In: a Wilson interval on every cell; baselines store passes and n; a gate
that applies only to guard cells (blessed at 0.9 or more); the error rates
written into `benchmarks/README.md`.
Out: the cost and provenance fields and the token ratio test (#155); the
paired A/B mode (#153); blessed cells below 0.1 remain ungated.

## Assumptions
- Guard rule: the first pass runs at the case's reps. Any failure there
  triggers CONFIRM_REPS more reps; a regression is 2 or more failures among
  them. At 3 first-pass reps and a true rate of 0.95 that is 1.2% per cell.
- Capability cells (0.1 to 0.9) and floor cells (below 0.1) have no per-cell
  gate and are reported with their intervals. A drop there is a note, never a
  failure.
- "improved" requires the two Wilson intervals not to overlap.
- Old baselines (`pass_rate` and `reps`) load unchanged; new ones write
  `passes` and `reps`.

## Tasks
- [ ] 1. Wilson interval and cell classes: tests pin 2/3 -> [0.21, 0.94] and 3/3 -> [0.44, 1.00].
- [ ] 2. Baseline counts round trip; old `pass_rate` baselines still load.
- [ ] 3. Guard rule in `compare`: suspect, then regression at 2 of 10; non-guard cells never fail on rate.
- [ ] 4. Render intervals in the per-case table and the matrix cells; CONFIRM_REPS = 10.
- [ ] 5. Simulation test: a no-change 28-cell matrix reproduces the documented per-run false-alarm rate.
- [ ] 6. README: the rule, its error rates, and the first-pass reps dependence.
