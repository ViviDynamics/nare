# Benchmark: cost and provenance on every results line

Issue #155

## Scope
In: cost and `estimated` on each rep; provenance fields (nare version and
commit, case hash, image ID, start time, tool calls, typed error cause);
`render` shows dollars per rep and per solved task; a log-token ratio test
in place of the 10% band; refusal of a baseline whose case changed.
Out: the paired token ratio across cases (#153 owns the pairing); per-claim
verdicts, already on results lines from another lane.

## Assumptions
- Token test: one-sided 5% z-test on the log of the token median, with each
  side's log standard deviation over judged reps. Unpaired here; #153 pairs.
- A side with no spread (fewer than two reps, or an old baseline) is not
  tested and the note says so. Nothing untested fails a run.
- Case hash covers `case.toml` and every file under `fixture/`, by path and
  digest. A case whose files are not on disk has no hash and is never refused.
- `--allow-case-change` overrides the refusal and nothing else.

## Tasks
- [ ] 1. Parse `usage.cost` and `usage.estimated` from the result line; null is unknown.
- [ ] 2. Provenance helpers: case hash, image ID, tool calls by tool, typed causes.
- [ ] 3. Record the fields on every rep, graded or errored.
- [ ] 4. Summaries carry cost, unknown count, token range and log spread.
- [ ] 5. Baselines store the case hash and spread; old baselines load unchanged.
- [ ] 6. Token z-test replaces the band; `--strict-tokens` gates only significant rises.
- [ ] 7. Refuse a changed case in run, compare and the matrix unless `--allow-case-change`.
- [ ] 8. Render dollars per rep and per solved task; mark estimates and unknowns.
