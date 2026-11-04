# Schema corrections: allow several, and keep earlier fixes

Issue #182

## Scope

In: `nare run --max-corrections N` and `run(max_corrections=N)`, the number of
schema corrections a run may spend. A correction is free when the failing
answer has fewer distinct errors than any earlier failing answer in the run,
and only when N is greater than 1. Each correction names the errors an earlier
answer made that this one no longer makes (`earlier_corrections`). The result
reports the budget, the charged count and each correction's codes, and each
correction is a progress event.

Out: the progress rule for the constrained answer turn beyond what the shared
budget already does (the constrained turn is a correction turn and follows the
same charge), the structured error codes and caller `check` callable (#184), and
the repetition and length cut-offs (#183). Both build on the API this adds.

## Assumptions

- Default N is 1, and N=1 is today's behaviour exactly: one charged correction,
  then `schema_violation`. Free corrections are granted only when N is greater
  than 1, because with N=1 "free" would mean a second correction the caller did
  not allow.
- "Distinct errors" are the validator's messages (`path: message`) with
  duplicates removed, in the order they were reported. The codes compared
  across replies are those strings.
- "Fewer than any earlier reply" compares against the lowest distinct count
  seen so far in the run. Every free correction strictly lowers that minimum,
  so a run cannot loop on free corrections.
- `earlier_corrections` is the set of errors that any earlier failing answer in
  the run made and the current answer does not. It is the list the model must
  keep correct.
- The budget is per run, like today's `schema_retried`: a session file records
  the charged count, and `reopen()` clears it, as it clears `schema_retried`
  for a resumed run. A file saved before this change with `schema_retried`
  true counts as one charged correction.
- `schema_retried` stays true after any correction, free or charged, so the
  turns after a correction remain constrained exactly as before.
- Result: `budget` gains `schema_corrections` (the limit) and
  `used_schema_corrections` (charged), present only with `--schema`. The
  top-level `corrections` list (`{"charged", "codes"}` per correction) is
  present only when a correction happened. Flag, budget keys and result key are
  additive and keep contract version 2.
- The logic lives in a new module, `src/nare/corrections.py`, so the loop edits
  stay small for the parallel changes to `loop.py` (#183, #184).

## Tasks

- [ ] 1. A scripted run with N=2 and failing answers of 3, 2, 2 and 1 distinct
  errors, then a valid answer: the second correction is free, the third is
  charged, and the run completes. Test: `tests/test_schema_corrections.py`.
- [ ] 2. With N=1, two failing answers (3, then 2 errors) still end
  `schema_violation` after one correction, as today.
- [ ] 3. A correction message lists `earlier_corrections`: an answer that fixes
  error A and breaks error B is corrected with A named as kept-correct.
- [ ] 4. Each correction is a progress event with `charged`, `codes`,
  `earlier_corrections` and the limit.
- [ ] 5. The result JSON reports `schema_corrections`, `used_schema_corrections`
  and the `corrections` list, through the CLI.
- [ ] 6. Invalid values never start (`0`, a bool, no `--schema`).
- [ ] 7. A session file from before this change loads with one charged
  correction when `schema_retried` was true. `reopen()` clears the budget.
- [ ] 8. Contract: `--max-corrections` in the run flags table and in
  `nare contract`, the `schema_violation` row names the budget.
