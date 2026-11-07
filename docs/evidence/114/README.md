# Issue 114: nare against Claude Code, after prompt caching

Run on 2026-11-07, between 00:56 and 01:50 UTC. Raw results are beside this
note: `nare-results.jsonl` (the nare arm, six `bin/bench run` files) and
`claude-code-results.jsonl` (the Claude Code arm, smoke then full), each one
line per rep, with the two manifests.

## Question

Prompt caching landed in #84 after the last full-stack comparison. Does nare
now spend fewer tokens and dollars per solved task than Claude Code, on the
same tasks and model, and did result quality move with it?

## Versions measured

| | Version or commit |
| --- | --- |
| nare | `d69f018` (origin/main at the start), `nare_version` `2026.11.57.dev1+gd69f01838` |
| Claude Code | `2.1.319` (pinned by `benchmarks/Dockerfile.claude-code`, `CLAUDE_CODE_VERSION`) |
| Agent model, both arms | `claude-haiku-5-5`, through the same gateway root |
| Judge, both arms | `claude-sonnet-5-5`, never the model under test |
| Gateway list prices, $ per million tokens | input 0.10, output 0.50, cache read 0.01, cache write 0.125 |

## Method

- **Cases.** Nine cases both arms run on the same fixture, prompt and checks:
  five smoke (read-only-question, edit-docstring, fix-failing-test,
  multi-file-rename, stale-backup-cleanup) and four full (bash-timeout-recovery,
  bootstrap-without-database, expense-tracker-cli, long-test-output). The other
  eight are excluded, each with a reason in `benchmarks/runner/claude_code.py`:
  the two ambiguous cases and impossible-test read nare's ask channel and
  session file; the two context-overflow cases measure nare's elision and
  `--context-window`; the two MCP cases need per-harness wiring that is not done;
  typed-classify uses nare's `--schema`.
- **Reps.** Smoke cases: 12 per arm. Full cases: 10 per arm. nare's runner has
  no `--reps`, so the nare arm ran `bin/bench run` four times for the smoke
  cases and twice for the full cases. Both arms got the same counts.
- **Grading.** The nare arm is `bin/bench run` on origin/main, unchanged. The
  Claude arm runs `claude -p` in its own image, then the same checks, judge and
  outcome rules as nare's runner (`grade_result_check`, `grade_bash_check`,
  `rep_outcome`, `score`). Judge failures count against the pass rate and infra
  errors do not, both as nare's runner does.
- **Tools and prompts are each harness's own.** nare has its default tools.
  Claude Code runs in print mode with its own system prompt, tool definitions
  (19 in the init message, plus built-in skills and slash commands), and with
  subagents and web tools disallowed. Its harness overhead is part of the
  result, and it is large (see the cache columns).
- **Dollars.** Both arms are recomputed from their usage at the gateway list
  prices above. Claude Code's own `total_cost_usd` matches that figure exactly
  on all 99 results that report one: the largest difference is $0.0. Both sum
  to $0.3627. The per-result figures are in `claude-code-reported-cost.csv`.
- **Tokens.** Input, output, cache read and cache write, from each rep's final
  usage. Claude Code's streamed messages undercount output, so the final result
  is used.
- **Runs.** Both arms ran at the same time, from the same nare checkout, so
  endpoint drift hits both alike. Wall times are therefore comparable with each
  other but not with an idle machine.

## Results

Pass counts are passes over decided reps, with a 95% Wilson interval. Judge
failures are inside the denominator. Infrastructure errors are not. Medians
are over decided reps. Dollars are per rep, and per solved task.

| Case | Arm | Reps | Pass (n) [95% Wilson] | Errors | Input | Output | Cache read | Cache write | Turns | Wall s | $/rep | $/solved |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| bash-timeout-recovery | nare | 10 | 10/10 [0.72, 1.00] | 0 | 14 | 1,741 | 18,223 | 1,948 | 6 | 21.32 | $0.0014 | $0.0014 |
| bash-timeout-recovery | Claude Code | 10 | 10/10 [0.72, 1.00] | 0 | 10 | 1,404 | 74,530 | 4,704 | 7 | 18.95 | $0.0020 | $0.0020 |
| bootstrap-without-database | nare | 10 | 9/10 [0.60, 0.98] | 0 | 17 | 13,266 | 50,888 | 14,412 | 8 | 76.09 | $0.0091 | $0.0101 |
| bootstrap-without-database | Claude Code | 10 | 3/9 [0.12, 0.65] | 2 | 45 | 22,800 | 561,568 | 30,008 | 31 | 133.65 | $0.0181 | $0.0604 |
| edit-docstring | nare | 12 | 12/12 [0.76, 1.00] | 0 | 16 | 668 | 8,994 | 523 | 4 | 8.30 | $0.0005 | $0.0005 |
| edit-docstring | Claude Code | 12 | 10/12 [0.55, 0.95] | 2 | 8 | 498 | 50,966 | 3,715 | 4 | 6.39 | $0.0014 | $0.0017 |
| expense-tracker-cli | nare | 10 | 10/10 [0.72, 1.00] | 0 | 16 | 6,624 | 32,160 | 7,142 | 7 | 30.86 | $0.0045 | $0.0045 |
| expense-tracker-cli | Claude Code | 10 | 10/10 [0.72, 1.00] | 0 | 12 | 8,015 | 99,686 | 12,540 | 7 | 35.27 | $0.0067 | $0.0067 |
| fix-failing-test | nare | 12 | 12/12 [0.76, 1.00] | 0 | 12 | 995 | 13,202 | 1,646 | 5 | 10.68 | $0.0008 | $0.0008 |
| fix-failing-test | Claude Code | 12 | 12/12 [0.76, 1.00] | 0 | 8 | 716 | 58,045 | 4,442 | 5 | 7.51 | $0.0015 | $0.0015 |
| long-test-output | nare | 10 | 10/10 [0.72, 1.00] | 0 | 10 | 608 | 29,356 | 10,828 | 4 | 8.76 | $0.0020 | $0.0020 |
| long-test-output | Claude Code | 10 | 10/10 [0.72, 1.00] | 0 | 8 | 575 | 58,503 | 4,568 | 4 | 8.07 | $0.0014 | $0.0014 |
| multi-file-rename | nare | 12 | 12/12 [0.76, 1.00] | 0 | 8 | 480 | 6,975 | 852 | 3 | 7.74 | $0.0005 | $0.0005 |
| multi-file-rename | Claude Code | 12 | 12/12 [0.76, 1.00] | 0 | 8 | 580 | 57,212 | 4,232 | 4 | 8.20 | $0.0014 | $0.0014 |
| read-only-question | nare | 12 | 12/12 [0.76, 1.00] | 0 | 12 | 438 | 6,870 | 0 | 3 | 6.63 | $0.0003 | $0.0003 |
| read-only-question | Claude Code | 12 | 12/12 [0.76, 1.00] | 0 | 6 | 438 | 41,700 | 3,596 | 3 | 5.87 | $0.0011 | $0.0011 |
| stale-backup-cleanup | nare | 12 | 12/12 [0.76, 1.00] | 0 | 8 | 472 | 6,913 | 776 | 3 | 7.86 | $0.0004 | $0.0004 |
| stale-backup-cleanup | Claude Code | 12 | 12/12 [0.76, 1.00] | 0 | 6 | 508 | 42,142 | 4,076 | 4 | 7.25 | $0.0012 | $0.0012 |

| Case | Arm | Stop reasons (count) |
| --- | --- | --- |
| bash-timeout-recovery | nare | end_turn 10 |
| bash-timeout-recovery | Claude Code | end_turn 10 |
| bootstrap-without-database | nare | end_turn 10 |
| bootstrap-without-database | Claude Code | end_turn 7, max_turns 2, none 1 |
| edit-docstring | nare | end_turn 12 |
| edit-docstring | Claude Code | end_turn 12 |
| expense-tracker-cli | nare | end_turn 10 |
| expense-tracker-cli | Claude Code | end_turn 10 |
| fix-failing-test | nare | end_turn 12 |
| fix-failing-test | Claude Code | end_turn 12 |
| long-test-output | nare | end_turn 10 |
| long-test-output | Claude Code | end_turn 10 |
| multi-file-rename | nare | end_turn 12 |
| multi-file-rename | Claude Code | end_turn 12 |
| read-only-question | nare | end_turn 12 |
| read-only-question | Claude Code | end_turn 12 |
| stale-backup-cleanup | nare | end_turn 12 |
| stale-backup-cleanup | Claude Code | end_turn 12 |

Mantel-Haenszel odds ratio, nare over Claude Code: 24.33 [1.89, 313.76], p = 0.013, 9 strata.
Tokens, nare over Claude Code (geometric mean of per-case medians): 0.223 [0.162, 0.317].


Errors by case: Claude Code, edit-docstring, 2 reps, both judge replies that
did not parse (the judge's reply used a string where a boolean was expected).
The nare arm had none there. Claude Code, bootstrap-without-database, 1 judge reply not JSON and
1 rep with no result object. In that rep the stream ends in a Bash call that
kills processes by name, and the run did not finish. It is counted as an
infrastructure error, not a pass or a fail. It is not investigated further.

Bootstrap failures, Claude Code (5 of 9 decided). Two reps stopped at the
turn limit, with Claude Code reporting 31 turns against a cap of 30. Three reps finished and failed bash checks. The checks read
`/out/after.json`. Three of the five left a Postgres or Node process running at
the end (reps 1, 6 and 8), which the case forbids. Two installed an embedded
Postgres server (reps 3 and 7), which the case also forbids. Rep 1's closing
message reported the database tests as run against a live server, and the judge
accepted that claim while the bash check showed the server left running. nare
failed one of its ten reps; that rep is not examined here.

Stop reasons, all reps: every nare rep ended `end_turn`. Claude Code ended
`end_turn` except for the two turn-limit reps and the one with no result object.

Tokens, nare over Claude Code (geometric mean of the per-case medians over the
nine cases): 0.223 [0.162, 0.317]. A Mantel-Haenszel odds ratio of nare over
Claude Code is 24.3 [1.9, 313.8], p = 0.013, over nine strata. It rests mostly
on the bootstrap case and on small counts, so read it as "the arms differ",
not as a size.

## Conclusions, as far as the numbers go

- On seven of nine cases both arms passed every rep. On edit-docstring, Claude
  Code had 10 of 12 with two judge errors, and nare 12 of 12.
- On bootstrap-without-database, nare passed 9 of 10 and Claude Code 3 of 9 with
  one infrastructure error. Claude Code's failures match what the case is for:
  leaving services running and installing a database it did not report. That is
  one case, at 10 reps per arm. It does not say how the two harnesses compare in
  general.
- Claude Code's cache-read tokens were two to eleven times nare's on every case.
  Its cache-write tokens are a large part of its total too.
  Those come from its own system prompt and tool definitions, which nare does
  not send. Caching makes that prefix cheap, but it is still billed.
- Dollars per solved task were lower for nare on eight of nine cases. The
  exception is long-test-output, where Claude Code was lower ($0.0014 against
  $0.0020). Agent spend was $0.36 for Claude Code, recomputed, and $0.20 for
  nare, at its own estimate. The judge's spend is in neither figure.
- **Turn counts.** Claude Code's turn count is its own. On two bootstrap reps
  it reported 31 turns against a cap of 30, so its counting differs from nare's
  by at least one. The turn check on expense-tracker-cli compares the two counts
  as they are.
- Nothing here measures general superiority: one model, nine cases, 10 to 12
  reps per arm, one judge, one day.

## Caveats

- **Image identity.** The Claude Code image was built twice, once per pass,
  from the same Dockerfile and version. The two builds have different image
  IDs, so a package installed without a pin (pytest) may differ between them.
  The nare check image `nare-bench:local` was rebuilt by each `bin/bench run`
  and by the Claude arm while both ran, and the image IDs vary across the nare
  records. Both builds come from the same nare source. The records keep each
  rep's image ID.
- **Concurrency.** Both arms ran together, so they shared the endpoint and its
  rate limit. No `agent_http_error` or `judge_http_error` was recorded.
- **Edits during and after the run.** The report and formatting code in
  `benchmarks/runner/claude_arm.py` changed while the Claude arm ran. After the
  run, a review round changed the record path: each rep is now written as it
  finishes, the manifest records the nare commit and the tools each rep's init
  message reported, a `max_tokens` stop maps to an error, and the median rounds
  as the bench's `median_int` does. None of these changes alters a recorded
  value apart from the median rounding, and the tables above are regenerated
  from the results with the current code. No run in this note stopped on
  `max_tokens`. The image Dockerfile also gained the pinned pip step that
  `benchmarks/Dockerfile` has. The runs used the image built before that step,
  which installed pytest under the base image's pip.
- **The judge.** Two classes of judge error occurred, both on the Claude arm
  only, both where the judge's reply was not valid JSON. The judge was not
  changed for this note (#46 covers it).
- **Cloudflare.** No HTTP-level error was recorded in any rep, so no 403 from
  the gateway appears in these results.
- **Cost of judging.** The judge's own tokens are not recorded per call. About
  120 judge attempts were made, which at sonnet's price is an estimate of under
  $2. Total spend is well under the $15 ceiling.

## Reproduce

See the section "Against Claude Code (#114)" in `benchmarks/README.md`. The
commands are `python -m benchmarks.runner.claude_arm` (the arm) and
`python -m benchmarks.runner.claude_arm report` (the tables above).
