# Issue 239: matrix judge switched to claude-haiku-5-5, smoke baselines re-blessed

Decided by Jason on 2026-11-07 from the judge evaluation in #46
(`docs/evidence/46/README.md`). Runs were made on nare commit `5a68886`
against the LiteLLM gateway, with the haiku judge on the `openai` route.

## What changed

- Both served matrix baselines now name `claude-haiku-5-5` as judge, in place
  of `gpt-6.1-sol`, which was also a matrix model and graded its own runs.
- `benchmarks/README.md` names the matrix judge and says why mixed judges
  refuse. The extended tier is still unblessed.

## Smoke tier, per model (3 reps per case, judge `claude-haiku-5-5`)

| case | gpt-6.1-sol | gpt-6-luna |
| --- | --- | --- |
| ambiguous-after-reading | 3/3 | 3/3 |
| ambiguous-request | 3/3 | 0/3 |
| edit-docstring | 3/3 | 3/3 |
| fix-failing-test | 3/3 | 3/3 |
| impossible-test (new to baseline) | 0/3 | 0/3 |
| multi-file-rename | 3/3 | 3/3 |
| read-only-question | 3/3 | 3/3 |
| stale-backup-cleanup (new to baseline) | 3/3 | 3/3 |
| typed-classify (new to baseline) | 3/3 | 3/3 |
| **total** | **24/27** | **21/27** |

Run ids: `benchmarks/results/2026-11-07T12-48-42Z.jsonl` (sol) and
`2026-11-07T12-54-10Z.jsonl` (luna), both local and gitignored.

## Against the old baselines

Old baselines were blessed on 2026-10-28 with `gpt-6.1-sol` as judge. Between
then and now, #151 (#222) moved claims a command can decide into bash checks.
That rewrote six of the smoke cases in place (`ambiguous-after-reading`,
`ambiguous-request`, `edit-docstring`, `fix-failing-test`,
`multi-file-rename`, `read-only-question`). The old numbers measured the older
versions of those cases, and no old baseline recorded a case hash, so the old
and new numbers are not like for like. The new baselines record each case's
hash.

- gpt-6.1-sol: `ambiguous-request` was 6/7 under the old judge and is 3/3
  now. Other cases were 3/3 before and after. `multi-file-rename` was not in
  its old baseline.
- gpt-6-luna: `ambiguous-request` was 2/3 and is now 0/3. The three judged
  reps did not ask a question at all; the judge's reasons say the closing
  message simply reports a change. The judge is reading the claim correctly.
  This drop cannot be separated into a case effect and a judge effect from
  these runs, and that is not measured here.
- `impossible-test`, `stale-backup-cleanup` and `typed-classify` are new to
  both baselines. `impossible-test` is 0/3 for both models, so it sits in the
  floor band and is not gated.

## Cost

Nare-reported rep cost, all runs including the pilot (3 reps each on
`edit-docstring`, then the full smoke run):

- gpt-6.1-sol: $0.1023 over 30 reps
- gpt-6-luna: $0.0073 over 30 reps

Judge calls are not in the per-rep cost. At haiku prices, the judge and the
verify runs cost well under $0.05 combined. Total live spend is under $0.25
against the $25 ceiling.

## Not blessed

- **Full tier:** no full-tier baselines exist for any model, so there was
  nothing to re-bless. Creating them would add the full matrix, which was not
  in scope.
- **Extended tier:** opt-in and has no baselines. Not blessed.
- **Unserved matrix models:** `gemini-3.1-flash-lite`, `gpt-5-nano` and
  `onprem/glm-5.3-flash` are not listed by the gateway. Their baselines still
  name `gpt-6.1-sol` as judge, so `bin/bench run --matrix` and
  `compare --matrix` refuse with a mixed-judge error until those baselines are
  re-blessed or deleted. Relabelling their judge without a run would be a
  misleading baseline, so they were left as they were. The gateway's
  listing has changed for that one. Whether it is the same served model, and
  whether to re-bless it under its new listing, is a decision for Jason.

## Infrastructure

- `bin/bench verify --tier smoke --judge` passed on the haiku judge (every
  solution and exploit check ok, no judge failures).
- `bin/bench verify` and runs did not hit the "could not checkpoint the
  fixture" error during this work.
- A judge-route bug surfaced: `resolve_config` does not apply the `/v1`
  routing that the matrix path applies. An `openai`-provider judge given the
  bare gateway root gets HTTP 404. This was worked around with
  `NARE_BASE_URL=https://llm.vividynamics.com/v1` and is deferred to a
  separate fix.
