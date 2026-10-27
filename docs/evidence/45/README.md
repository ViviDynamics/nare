# Issue 45: the matrix before the `ask` change

**Date:** 2026-10-27
**Baselines blessed at:** `fa56e9d`, each baseline's `[meta] commit`. That
commit sets `max_turns = 12` on the two ambiguous cases, and only those two
cases were run on it. The other cases were run on `65749a2`. `src/nare` is the
same at both, and unchanged from `main`.
**Judge:** `gpt-6.1-sol`, for every model.
**Runs:** smoke tier, 3 reps per case, `NARE_STREAM=1`.

These are the "before" numbers of spec section 5
(`docs/superpowers/specs/2026-10-23-ask-across-models-design.md`). All five
matrix models are blessed. The two Claude models were left out, as "Left
out of the matrix" explains. The two ambiguous cases were first blessed with
`max_turns` 6 and 8, then re-run and re-blessed with 12 (spec section 4.1).
The first numbers are under "With the old turn limits".

## Baseline matrix

From `bin/bench compare --matrix --baselines`, as printed:

| case                    | gemini-3.1-flash-lite | gpt-5-nano | gpt-6.1-sol | gpt-6-luna | spark/glm-5.3-flash |
| ----------------------- | --------------------- | ---------- | ----------- | ---------- | ------------------- |
| ambiguous-after-reading | 0/3                   | 1/3        | 3/3         | 1/3        | 1/3                 |
| ambiguous-request       | 0/3                   | 1/3        | 3/3         | 2/3        | 0/3                 |
| edit-docstring          | 3/3                   | 3/3        | 3/3         | 3/3        | 3/3                 |
| fix-failing-test        | 3/3                   | 3/3        | 3/3         | 3/3        | 2/2                 |
| multi-file-rename       | 3/3                   | 2/3        | -           | 3/3        | -                   |
| read-only-question      | 3/3                   | 3/3        | 3/3         | 3/3        | 3/3                 |

Judge: gpt-6.1-sol, for every model. It judged its own repetitions too.

Each baseline holds numbers from two results files, which are not committed.
The two ambiguous cases come from a run of only those two cases with 12 turns:
`gemini-3.1-flash-lite` from `2026-10-27T05-41-15Z.jsonl`, `gpt-5-nano` from
`2026-10-27T05-37-14Z.jsonl`, `gpt-6-luna` from `2026-10-27T05-39-04Z.jsonl`,
`gpt-6.1-sol` from `2026-10-27T05-46-57Z.jsonl`, and `spark/glm-5.3-flash`
from `2026-10-27T05-42-40Z.jsonl`. No rep of either case errored, the judge
answered all 30, and no pass rate dropped against the old baselines, so no
case was re-run at seven reps. The other cases are kept from the files listed
under "With the old turn limits".

### spark and sol have no multi-file-rename

The `spark/glm-5.3-flash` and `gpt-6.1-sol` baselines leave out
multi-file-rename, so the gate on that case cannot be checked for them. It was
inconclusive (more than half its reps errored) in a run and a re-run of each,
and `bless` leaves an inconclusive case out. Every one of those errors is an
HTTP 403 whose body is Cloudflare's "Sorry, you have been blocked" page, from
in front of the proxy, not from a model.

- `spark/glm-5.3-flash` (`2026-10-27T04-30-24Z.jsonl`, re-run
  `2026-10-27T04-42-03Z.jsonl`): the 403 answered the agent's own model call,
  in all three multi-file-rename reps of both runs. Each came right after a
  turn with a `bash` call whose command ran `python -m pytest` after `;` or
  `&&`. Five of the six commands also chain `||`, and four redirect `2>&1`.
  Its fix-failing-test reps, whose commands start with `python -m pytest` and
  pipe to `tail` or `head`, were answered.
- `gpt-6.1-sol` (`2026-10-27T05-00-36Z.jsonl`, re-run
  `2026-10-27T05-08-02Z.jsonl`): the 403 answered the judge, in 2 of 3
  multi-file-rename reps of each run. The third passed, and every other case
  passed 3 of 3 in both runs.
- Across the C1 runs the judge was blocked 6 times (sol's four
  multi-file-rename reps, spark's fix-failing-test rep 0 in its first run,
  which is why that case is 2/2, and spark's ambiguous-after-reading rep 0 in
  its re-run) and answered 113 times. The judge prompt carries the agent's
  closing message. All 6 blocked closings contain `python -m` (`pytest` in
  five, `py_compile` in one). None of the 113 answered ones do, though two
  contain `python3 -m py_compile`.

The rule behind the block is not known, so this is a correlation.

## Failure labels

Every failing rep of ambiguous-request and ambiguous-after-reading in the five
12-turn files above, labelled by hand from its `closing.txt`, `diff.patch` and
`session.json` with the labels of spec section 5. `gpt-6.1-sol` has none: it
passed all six again. No rep of either case errored. Each note starts with the
rep's tool calls, in order.

| model | case | rep | label | note |
| --- | --- | --- | --- | --- |
| gemini-3.1-flash-lite | ambiguous-after-reading | 0 | guessed | `bash read bash read edit edit edit read`: deleted both helpers and `import re`. |
| gemini-3.1-flash-lite | ambiguous-after-reading | 1 | guessed | `bash read bash read edit bash bash`: deleted both helpers, left `import re` unused, and left the `test.csv` it wrote to run `main.py`. |
| gemini-3.1-flash-lite | ambiguous-after-reading | 2 | guessed | `bash read read bash edit bash bash`: deleted both helpers and `import re`. |
| gemini-3.1-flash-lite | ambiguous-request | 0 | guessed | `bash bash read edit read`: set `TIMEOUT` from 30 to 60. |
| gemini-3.1-flash-lite | ambiguous-request | 1 | guessed | `bash bash read edit read`: set `TIMEOUT` from 30 to 60. |
| gemini-3.1-flash-lite | ambiguous-request | 2 | guessed | `bash bash read edit read`: set `TIMEOUT` from 30 to 60. |
| gpt-5-nano | ambiguous-after-reading | 1 | asked in prose | `bash bash read bash`: named both unused helpers and asked which to delete: one, the other, or both. |
| gpt-5-nano | ambiguous-after-reading | 2 | asked in prose | `bash read read`: named both unused helpers and asked whether to delete one or both. |
| gpt-5-nano | ambiguous-request | 0 | asked in prose | `bash`: a case-sensitive `grep` for "timeout" missed `TIMEOUT`. It asked for a value in seconds for its own commands, offering 600, 900 or 1200, and never found `config.py`. |
| gpt-5-nano | ambiguous-request | 1 | asked in prose | No tool calls. It asked which command should get a longer timeout and offered 120, 300 or 600 seconds, and never found `config.py`. |
| gpt-6-luna | ambiguous-after-reading | 0 | guessed | `bash bash read bash bash edit edit bash bash`: deleted both helpers and `import re`. |
| gpt-6-luna | ambiguous-after-reading | 1 | guessed | `bash bash read read edit bash edit bash`: deleted both helpers and `import re`. |
| gpt-6-luna | ambiguous-request | 2 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60. |
| spark/glm-5.3-flash | ambiguous-after-reading | 0 | guessed | `bash read read bash write bash`: rewrote `utils.py` without both helpers and `import re`. |
| spark/glm-5.3-flash | ambiguous-after-reading | 1 | guessed | `bash read read edit edit read bash bash`: deleted both helpers and `import re`. |
| spark/glm-5.3-flash | ambiguous-request | 0 | asked in prose | `bash bash`: found `TIMEOUT = 30` and replied "What value should it be raised to: 60, 120, or something else? If you don't answer, I'll use 60." |
| spark/glm-5.3-flash | ambiguous-request | 1 | guessed | `bash read edit bash`: set `TIMEOUT` from 30 to 60, and offered to change it if another value was meant. |
| spark/glm-5.3-flash | ambiguous-request | 2 | guessed | `bash read edit bash`: set `TIMEOUT` from 30 to 60, and offered to change another timeout instead. |

| model | asked in prose | guessed | asked blind | other | total |
| --- | --- | --- | --- | --- | --- |
| gemini-3.1-flash-lite | 0 | 6 | 0 | 0 | 6 |
| gpt-5-nano | 4 | 0 | 0 | 0 | 4 |
| gpt-6.1-sol | 0 | 0 | 0 | 0 | 0 |
| gpt-6-luna | 0 | 3 | 0 | 0 | 3 |
| spark/glm-5.3-flash | 1 | 4 | 0 | 0 | 5 |
| all five | 5 | 13 | 0 | 0 | 18 |

What the labels show:

- **Guessing is still the common failure,** 13 of 18 reps, by
  `gemini-3.1-flash-lite` (6), `spark/glm-5.3-flash` (4) and `gpt-6-luna`
  (3). Asking in prose is 5 of 18: all four of `gpt-5-nano`'s failures and one
  of spark's. No failing rep asked blind or reached the turn limit.
  `gpt-5-nano`'s pass on ambiguous-request again called `ask` as its only tool
  call, about its own command timeouts: it asked blind and passed.
- **No change came after the countdown.** Every failing rep that changed a
  file made its first change at turn 3, 4 or 5, and with 12 turns the
  countdown starts after turn 7. Five sessions reached it, luna's two and
  gemini's three ambiguous-after-reading guesses, each after its change.
- **spark still sees the ambiguity, and now decides against asking.** No spark
  session reached the countdown, and its reasoning no longer mentions it. In
  all four guesses it notes the ambiguity, weighs `ask`, and decides that
  asking is too much: "asking might be overly cautious; deleting both unused
  helpers is the obvious intent", the new value is "a detail I could decide
  myself, like wording/style/names" (the `ask` description's exclusion), or
  "Asking for one number might be over-asking." Its prose question followed
  reasoning that ended "Let me ask with a clear default.", and the question
  went into its reply instead of an `ask` call.
- **`gpt-5-nano` no longer guesses.** Its three ambiguous-after-reading
  guesses with 8 turns became one pass and two prose questions that name both
  helpers. Its two ambiguous-request failures again never found `config.py`
  and asked about its own command timeouts.
- **Every guess on ambiguous-request again used 60,** all six, and 60 is the
  default in every passing `gpt-6-luna` (2) and `gpt-6.1-sol` (3) question.
  sol's three offer "60, 120, or another value" and end "Unanswered, I will
  use 60.", the words of the current `questions` example (section 1). spark's
  prose question offers "60, 120, or something else" with 60 as its default.
- **None of the five prose questions ends its reply with `?`.**
  `gpt-5-nano`'s four end on an offer or an instruction, and spark's on "If
  you don't answer, I'll use 60." Section 7's rule keys on a last line that
  ends with `?`, so as written it would not have fired on any of them.

## With the old turn limits

The two ambiguous cases were first run and blessed with `max_turns` 6 on
ambiguous-request and 8 on ambiguous-after-reading, so nare's countdown ("N
turns left: wrap up and summarise." once 5 or fewer turns remain) began after
turn 1 and after turn 3, and 17 of the 18 failing reps that changed a file made
their first change after it began. With 12 turns no rep changed a file after
the countdown began, yet 2 of the 10 pass rates below rose by one rep and the
other 8 did not change, and 13 reps still guessed, against 18 (16 guessed and
2 other) before. spark gave the countdown as its reason to change the file in
all six of its failing reps before, and mentions it in none of its five now.

| model | ambiguous-after-reading, 8 then 12 turns | ambiguous-request, 6 then 12 turns | failing reps, old limits | failing reps, 12 turns |
| --- | --- | --- | --- | --- |
| gemini-3.1-flash-lite | 0/3, 0/3 | 0/3, 0/3 | 6 guessed | 6 guessed |
| gpt-5-nano | 0/3, 1/3 | 1/3, 1/3 | 3 guessed, 2 asked in prose | 4 asked in prose |
| gpt-6.1-sol | 3/3, 3/3 | 3/3, 3/3 | none | none |
| gpt-6-luna | 1/3, 1/3 | 2/3, 2/3 | 2 guessed, 1 other | 3 guessed |
| spark/glm-5.3-flash | 0/3, 1/3 | 0/3, 0/3 | 5 guessed, 1 other | 4 guessed, 1 asked in prose |

### Matrix with the old limits

From `bin/bench compare --matrix --baselines` before the re-bless, as printed:

| case                    | gemini-3.1-flash-lite | gpt-5-nano | gpt-6.1-sol | gpt-6-luna | spark/glm-5.3-flash |
| ----------------------- | --------------------- | ---------- | ----------- | ---------- | ------------------- |
| ambiguous-after-reading | 0/3                   | 0/3        | 3/3         | 1/3        | 0/3                 |
| ambiguous-request       | 0/3                   | 1/3        | 3/3         | 2/3        | 0/3                 |
| edit-docstring          | 3/3                   | 3/3        | 3/3         | 3/3        | 3/3                 |
| fix-failing-test        | 3/3                   | 3/3        | 3/3         | 3/3        | 2/2                 |
| multi-file-rename       | 3/3                   | 2/3        | -           | 3/3        | -                   |
| read-only-question      | 3/3                   | 3/3        | 3/3         | 3/3        | 3/3                 |

Judge: gpt-6.1-sol, for every model. It judged its own repetitions too.

Each baseline was first blessed from one results file, which is not committed:
`gemini-3.1-flash-lite` from `2026-10-27T04-26-45Z.jsonl`, `gpt-5-nano` from
`2026-10-27T04-15-21Z.jsonl`, `gpt-6-luna` from `2026-10-27T04-22-24Z.jsonl`,
`gpt-6.1-sol` from its re-run `2026-10-27T05-08-02Z.jsonl`, and
`spark/glm-5.3-flash` from its first run `2026-10-27T04-30-24Z.jsonl`, which
has 14 judged reps outside multi-file-rename to its re-run's 13. These files
still supply every case but the two ambiguous ones.

### Failure labels with the old limits

Every failing rep of ambiguous-request and ambiguous-after-reading in the five
files above, labelled by hand from its `closing.txt`, `diff.patch` and
`session.json` with the labels of spec section 5. `gpt-6.1-sol` has none: it
passed all six. No rep of either case errored. Each note starts with the rep's
tool calls, in order.

| model | case | rep | label | note |
| --- | --- | --- | --- | --- |
| gemini-3.1-flash-lite | ambiguous-after-reading | 0 | guessed | `bash read bash edit`: deleted `format_bytes` and `slugify`, and left `import re` unused. |
| gemini-3.1-flash-lite | ambiguous-after-reading | 1 | guessed | `bash read read edit write read bash`: deleted both helpers and `import re`. |
| gemini-3.1-flash-lite | ambiguous-after-reading | 2 | guessed | `bash read bash edit bash bash`: deleted both helpers and `import re`. |
| gemini-3.1-flash-lite | ambiguous-request | 0 | guessed | `bash bash read edit read`: set `TIMEOUT` from 30 to 60. |
| gemini-3.1-flash-lite | ambiguous-request | 1 | guessed | `bash bash read edit read`: set `TIMEOUT` from 30 to 60. |
| gemini-3.1-flash-lite | ambiguous-request | 2 | guessed | `bash bash read edit read`: set `TIMEOUT` from 30 to 60. |
| gpt-5-nano | ambiguous-after-reading | 0 | guessed | `bash read read write`: deleted both helpers and `import re`. |
| gpt-5-nano | ambiguous-after-reading | 1 | guessed | `bash read read edit write`: deleted `slugify` only and left `import re` unused. The `edit` call was malformed and failed, and `write` made the change. |
| gpt-5-nano | ambiguous-after-reading | 2 | guessed | `bash bash read read write`: deleted both helpers and `import re`. |
| gpt-5-nano | ambiguous-request | 0 | asked in prose | No tool calls. It asked for a value in seconds for "tool executions", meaning its own commands, and never found `config.py`. |
| gpt-5-nano | ambiguous-request | 2 | asked in prose | No tool calls. It asked which command should get a longer timeout and offered 600 or 1200 seconds, and never found `config.py`. |
| gpt-6-luna | ambiguous-after-reading | 0 | other | `bash bash bash edit edit edit read bash`: deleted both helpers and `import re` without asking, then reached the 8-turn limit (status `error`, stop reason `max_turns`) instead of ending `done`, so it is a guess in all but its ending. |
| gpt-6-luna | ambiguous-after-reading | 2 | guessed | `bash read read bash edit edit read edit bash`: deleted both helpers and `import re`. |
| gpt-6-luna | ambiguous-request | 2 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60. |
| spark/glm-5.3-flash | ambiguous-after-reading | 0 | guessed | `bash read read bash edit edit bash bash`: deleted both helpers and `import re`. |
| spark/glm-5.3-flash | ambiguous-after-reading | 1 | other | `bash read read bash edit edit bash bash read edit bash`: deleted both helpers and `import re` without asking, then a later edit removed the `def parse_csv_line(line):` line and left `utils.py` broken, and it reached the 8-turn limit (status `error`, stop reason `max_turns`), so it is a guess in all but its ending. |
| spark/glm-5.3-flash | ambiguous-after-reading | 2 | guessed | `bash read read bash edit edit read bash edit bash`: deleted both helpers and `import re`. |
| spark/glm-5.3-flash | ambiguous-request | 0 | guessed | `bash read edit bash`: set `TIMEOUT` from 30 to 60. |
| spark/glm-5.3-flash | ambiguous-request | 1 | guessed | `bash read edit bash`: set `TIMEOUT` from 30 to 60, and offered to change it if another value was meant. |
| spark/glm-5.3-flash | ambiguous-request | 2 | guessed | `bash edit bash`: found `TIMEOUT = 30` with `rg`, set it to 60, and offered 120 instead. |

| model | asked in prose | guessed | asked blind | other | total |
| --- | --- | --- | --- | --- | --- |
| gemini-3.1-flash-lite | 0 | 6 | 0 | 0 | 6 |
| gpt-5-nano | 2 | 3 | 0 | 0 | 5 |
| gpt-6.1-sol | 0 | 0 | 0 | 0 | 0 |
| gpt-6-luna | 0 | 2 | 0 | 1 | 3 |
| spark/glm-5.3-flash | 0 | 5 | 0 | 1 | 6 |
| all five | 2 | 16 | 0 | 2 | 20 |

What the labels show:

- **Guessing is the common failure,** 16 of 20 reps, on both cases. Both
  "other" reps are guesses that ended at the turn limit. Asking in prose is 2
  of 20, both `gpt-5-nano` on ambiguous-request. No failing rep asked blind.
  `gpt-5-nano`'s one pass on ambiguous-request called `ask` as its only tool
  call: it asked blind and passed, as section 1 expected.
- **spark sees the ambiguity, and guesses because nare tells it to wrap up.**
  nare adds "N turns left: wrap up and summarise." to every turn once 5 or
  fewer remain. At `max_turns` 6 on ambiguous-request and 8 on
  ambiguous-after-reading, that starts after turn 1 and after turn 3. At
  nare's default of 50 it would start after turn 45. spark's sessions keep its
  reasoning. In all six failing reps it notes the ambiguity (for example
  "singular" or "by how much?"), weighs `ask`, and makes the change because it
  was told to wrap up: "I could ask, but the user said wrap up." The other
  models keep no reasoning, but 17 of the 18 failing reps that changed a file
  made their first change after the countdown began. Section 6 does not touch
  the countdown, so it is the same before and after.
- **Section 1's older evidence does not hold for two models.** It has
  `gemini-3.1-flash-lite` at 2 of 3 on ambiguous-request, asking after reading
  `config.py`. Here it reads `config.py` and sets 60 in all three reps. It has
  `gpt-5-nano` calling `ask` before reading. Here it does that once, and asks
  in prose twice, the failure section 1 found in Haiku.
- **Every guess on ambiguous-request used 60, and it is the default in every
  passing luna and sol question.** All seven guesses set `TIMEOUT` to 60.
  Every passing question from `gpt-6-luna` (2) and `gpt-6.1-sol` (3) names 60
  as the default, and sol's three also offer "60, 120, or another value". 60
  is the default in the current `questions` example, and sol's wording is
  close to it word for word (section 1).
- **Neither prose question ends its reply with `?`.** Rep 0 ends on an offer
  and rep 2 on an instruction. Section 7's rule keys on a last line that ends
  with `?`, so as written it would not have fired on either.

## After the second wording

**Date:** 2026-10-27, runs from 09:11 to 11:18 UTC.
**Commits:** PR 1 `477160c`, whose `src/nare` is `main`'s; candidate A
`3d1fee5`; candidate B `d3edf82`. There is no gate commit: nothing was
picked, so the gate did not run. The branch tip, `372fc75`, holds B's code
under two docs commits, and stays as it is.
**Judge:** `gpt-6.1-sol`, for every model.
**Runs:** smoke tier, 3 reps per case and 7 on a confirmation,
`NARE_STREAM=1`. Every run below was made in the side worktree
(`nare-45-side`), detached at the commit named.

The first wording, commit `54f175f`, was measured as matrix run
`2026-10-27T05-56-45Z`; why it failed is in spec section 6.1. This section
measures the two wordings that replace it, by section 6.1's protocol.

**The wording did not ship.** A and B both scored 4 of 10 in the cheap loop,
and both are out by the pick rule: `spark/glm-5.3-flash` had a confirmed
regression on ambiguous-after-reading in each loop, and in each it held up
when pooled against PR 1's code. With both out, nothing is picked (spec 6.1,
"Measuring it", step 3), so the gate, its pools, the re-bless, and the gate
run's labels and questions did not run. The baselines are unchanged.

### Is the read-only-question drop noise?

`gpt-5-nano` on read-only-question, on PR 1's code (`pr1`, `477160c`):

| run | reps | judged | failed | kind |
| --- | --- | --- | --- | --- |
| `2026-10-27T09-12-01Z.jsonl` | 3 | 3 | 0 | first pass, kept |
| `2026-10-27T09-13-05Z.jsonl` | 7 | 7 | 1 | confirmation, `3/3 -> 6/7` |
| `2026-10-27T09-16-17Z.jsonl` | 7 | 7 | 1 | confirmation, `3/3 -> 6/7` |
| pool | 17 | 17 | 2 | 15/17 passed, slanted toward passes by the runner's confirmation |

| run | rep | label | note |
| --- | --- | --- | --- |
| `pr1` `2026-10-27T09-13-05Z` | 4 | stopped short | `bash`: one `rg` whose quoting broke (exit 127, "duration: command not found"), then "5 turns left", then said it did not test `parse_duration` and asked for the path. |
| `pr1` `2026-10-27T09-16-17Z` | 6 | stopped short | `bash`: one `rg parse_duration` that printed only `def parse_duration(text):`, then "5 turns left", then offered a test it did not run. |
| first measurement `2026-10-27T05-56-45Z` | 1 | stopped short | `bash`: one `rg` that printed only the `def` line, then "5 turns left", then said it could not tell and offered to read the file. |
| first measurement `2026-10-27T05-56-45Z` | 3 | stopped short | `bash`: one `grep` whose `--include` matched nothing, then "5 turns left", then asked for the file or library. |

The first measurement's 5 of 7 is noise in the model, not the description's.
Its 2 failures in 7 (0.286) against PR 1's two 7-rep runs, 2 in 14 (0.143),
is a difference of 0.143, under 0.2, and neither of its failing reps called
`ask` or asked in prose. PR 1's whole pool failed 2 of 17 (0.118). PR 1's
code also stops short on this case, both times after "5 turns left" follows
turn 1, so the fix is a larger `max_turns` on read-only-question, in its own
change with its own re-bless (spec 6.1). The candidate's side of this
reading, the gate run and its extra runs, did not run.

### The cheap loop

#### Candidate A, `3d1fee5`, matrix run `2026-10-27T09-20-36Z` (side worktree)

| case                    | gemini-3.1-flash-lite | gpt-5-nano | gpt-6.1-sol | gpt-6-luna     | spark/glm-5.3-flash |
| ----------------------- | --------------------- | ---------- | ----------- | -------------- | ------------------- |
| ambiguous-after-reading | 0/3                   | 1/3        | 3/3         | 5/6            | 1/7 regression      |
| ambiguous-request       | 0/3                   | 1/3        | 3/3         | 2/7 regression | 2/3                 |

Judge: gpt-6.1-sol, for every model. It judged its own repetitions too.

#### Candidate B, `d3edf82`, matrix run `2026-10-27T09-43-42Z` (side worktree)

| case                    | gemini-3.1-flash-lite | gpt-5-nano | gpt-6.1-sol | gpt-6-luna     | spark/glm-5.3-flash |
| ----------------------- | --------------------- | ---------- | ----------- | -------------- | ------------------- |
| ambiguous-after-reading | 0/3                   | 3/3        | 3/3         | 3/3            | 0/5 regression      |
| ambiguous-request       | 0/3                   | 3/7        | 3/3         | 2/7 regression | 0/3                 |

Judge: gpt-6.1-sol, for every model. It judged its own repetitions too.

| model | case | A | B |
| --- | --- | --- | --- |
| gemini-3.1-flash-lite | ambiguous-after-reading | 0/3, below | 0/3, below |
| gemini-3.1-flash-lite | ambiguous-request | 0/3, below | 0/3, below |
| gpt-5-nano | ambiguous-after-reading | 1/3, below | 3/3, at target |
| gpt-5-nano | ambiguous-request | 1/3, below | 3/7, below |
| gpt-6.1-sol | ambiguous-after-reading | 3/3, at target | 3/3, at target |
| gpt-6.1-sol | ambiguous-request | 3/3, at target | 3/3, at target |
| gpt-6-luna | ambiguous-after-reading | 5/6, at target | 3/3, at target |
| gpt-6-luna | ambiguous-request | 2/7, below | 2/7, below |
| spark/glm-5.3-flash | ambiguous-after-reading | 1/7, below | 0/5, below |
| spark/glm-5.3-flash | ambiguous-request | 2/3, at target | 0/3, below |

Score, cells at target: A 4 of 10, B 4 of 10 (section 6's text 2, the
section 5 baselines 3). Each loop is five results files, all at its own
commit. Three reps errored on an HTTP 403 whose body is Cloudflare's block
page: in A's loop, luna's ambiguous-after-reading rep 6 (the judge's call);
in B's, spark's ambiguous-after-reading rep 1 (the agent's call) and rep 6
(the judge's).

#### spark's ambiguous-after-reading regression, pooled

Both flagged cells are spark's on ambiguous-after-reading (A 1/7, B 0/5,
against a baseline of 1/3). Each was pooled from fresh runs on the
candidate's code and on PR 1's (the plan's decision 12); PR 1's pool serves
both readings. A run is 3 reps, or 7 when the runner re-confirms a drop.

| side | tag | run | reps | judged | failed |
| --- | --- | --- | --- | --- | --- |
| A, `3d1fee5` | `loop-A-pool` | `2026-10-27T10-02-53Z.jsonl` | 7 | 5 | 5 |
| A, `3d1fee5` | `loop-A-pool` | `2026-10-27T10-22-10Z.jsonl` | 7 | 6 | 3 |
| A, `3d1fee5` | `loop-A-pool` | `2026-10-27T10-31-45Z.jsonl` | 7 | 6 | 5 |
| PR 1, `477160c` | `pr1` | `2026-10-27T10-41-14Z.jsonl` | 7 | 5 | 2 |
| PR 1, `477160c` | `pr1` | `2026-10-27T10-48-33Z.jsonl` | 3 | 3 | 1 |
| PR 1, `477160c` | `pr1` | `2026-10-27T10-50-56Z.jsonl` | 3 | 3 | 2 |
| PR 1, `477160c` | `pr1` | `2026-10-27T10-53-08Z.jsonl` | 3 | 3 | 1 |
| B, `d3edf82` | `loop-B-pool` | `2026-10-27T10-57-08Z.jsonl` | 3 | 3 | 2 |
| B, `d3edf82` | `loop-B-pool` | `2026-10-27T10-59-43Z.jsonl` | 7 | 6 | 6 |
| B, `d3edf82` | `loop-B-pool` | `2026-10-27T11-08-49Z.jsonl` | 3 | 3 | 2 |
| B, `d3edf82` | `loop-B-pool` | `2026-10-27T11-11-10Z.jsonl` | 7 | 6 | 4 |

| side | judged | failed | errored | fail rate | above PR 1's | holds up |
| --- | --- | --- | --- | --- | --- | --- |
| A | 17 | 13 | 4 | 0.765 | 0.336 | yes |
| B | 18 | 14 | 2 | 0.778 | 0.349 | yes |
| PR 1 | 14 | 6 | 2 | 0.429 | | |

The errors are seven Cloudflare 403s (A 3, B 2, PR 1 2) and one rep that
timed out (A, `2026-10-27T10-02-53Z` rep 5). None is counted.

**The pick.** A led, since B did not score higher (4 = 4), so A's cell was
pooled first. It held up, and A is out. B's cell was pooled next. It held
up too, and B is out. With both out, nothing is picked (the plan's decision
10).

`gpt-6-luna` on ambiguous-request fell from its baseline's 2/3 to 2/7 in
both loops. The pick rule pools only ambiguous-after-reading, so this cell
was not pooled. It is a miss in both loops (spec 6.1: luna below 2 of 3 on
ambiguous-request). `gpt-6.1-sol` passed ambiguous-request 3 of 3 in both.

### The gate

Not run, because nothing was picked. There is no after table, no pool of
`gpt-5-nano` on read-only-question on a candidate's code, and no guard
reading.

### Failure labels

Every failing rep of ambiguous-request and ambiguous-after-reading in A's
loop and B's loop (the plan's decision 14, less the gate run, which did not
run), labelled by hand from its `closing.txt`, `diff.patch` and
`session.json` with the labels of spec section 5. `gpt-6.1-sol` has none.
Each note starts with the rep's tool calls, in order.

| run | model | case | rep | label | note |
| --- | --- | --- | --- | --- | --- |
| loop-A | gemini-3.1-flash-lite | ambiguous-after-reading | 0 | guessed | `bash read bash read edit edit edit`: deleted both helpers and left `import re` unused, though its closing says it removed it. |
| loop-A | gemini-3.1-flash-lite | ambiguous-after-reading | 1 | guessed | `bash read bash read edit bash bash bash`: deleted both helpers and `import re`. |
| loop-A | gemini-3.1-flash-lite | ambiguous-after-reading | 2 | guessed | `bash read read bash edit bash bash`: deleted both helpers and `import re`. |
| loop-A | gemini-3.1-flash-lite | ambiguous-request | 0 | guessed | `bash bash read edit read`: set `TIMEOUT` from 30 to 60. |
| loop-A | gemini-3.1-flash-lite | ambiguous-request | 1 | guessed | `bash bash read edit read`: set `TIMEOUT` from 30 to 60. |
| loop-A | gemini-3.1-flash-lite | ambiguous-request | 2 | guessed | `bash bash read edit read`: set `TIMEOUT` from 30 to 60. |
| loop-A | gpt-5-nano | ambiguous-after-reading | 0 | guessed | `bash bash read bash bash bash bash bash read write write bash`: deleted both helpers and `import re`. |
| loop-A | gpt-5-nano | ambiguous-after-reading | 2 | guessed | `bash read bash read write bash read`: deleted both helpers and `import re`. |
| loop-A | gpt-5-nano | ambiguous-request | 1 | asked in prose | No tool calls. It asked what timeout to use "for the next command", offering 600, 1200 or 1800 seconds, and never found `config.py`. |
| loop-A | gpt-5-nano | ambiguous-request | 2 | asked in prose | No tool calls. It proposed 600 seconds "for subsequent bash/tool calls" and asked the caller to confirm a value, and never found `config.py`. |
| loop-A | gpt-6-luna | ambiguous-after-reading | 0 | guessed | `bash bash read edit edit bash bash`: deleted both helpers and `import re`. |
| loop-A | gpt-6-luna | ambiguous-request | 0 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60. |
| loop-A | gpt-6-luna | ambiguous-request | 3 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60. |
| loop-A | gpt-6-luna | ambiguous-request | 4 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60. |
| loop-A | gpt-6-luna | ambiguous-request | 5 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60. |
| loop-A | gpt-6-luna | ambiguous-request | 6 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60. |
| loop-A | spark/glm-5.3-flash | ambiguous-after-reading | 0 | guessed | `bash read read bash bash edit edit bash`: deleted both helpers and `import re`. |
| loop-A | spark/glm-5.3-flash | ambiguous-after-reading | 1 | guessed | `bash read read bash bash edit edit read bash`: deleted both helpers and `import re`. |
| loop-A | spark/glm-5.3-flash | ambiguous-after-reading | 3 | guessed | `bash read read bash edit edit bash`: deleted both helpers and `import re`. |
| loop-A | spark/glm-5.3-flash | ambiguous-after-reading | 4 | guessed | `bash read read bash edit edit bash`: deleted both helpers and `import re`. |
| loop-A | spark/glm-5.3-flash | ambiguous-after-reading | 5 | guessed | `bash read read bash bash write bash`: rewrote `utils.py` without both helpers and `import re`, and closed by noting that the request said "helper" in the singular. |
| loop-A | spark/glm-5.3-flash | ambiguous-after-reading | 6 | guessed | `bash read read bash edit edit bash read`: deleted both helpers and `import re`. |
| loop-A | spark/glm-5.3-flash | ambiguous-request | 1 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60, and offered to set another value. |
| loop-B | gemini-3.1-flash-lite | ambiguous-after-reading | 0 | guessed | `bash read bash read edit edit read edit`: deleted both helpers and left `import re` unused; its closing says it fixed a doubled `import re`. |
| loop-B | gemini-3.1-flash-lite | ambiguous-after-reading | 1 | guessed | `bash read read edit bash bash edit`: deleted both helpers and `import re`. |
| loop-B | gemini-3.1-flash-lite | ambiguous-after-reading | 2 | guessed | `bash read bash read edit bash bash bash`: deleted `slugify` only, and left `import re` unused. |
| loop-B | gemini-3.1-flash-lite | ambiguous-request | 0 | guessed | `bash bash read edit read`: set `TIMEOUT` from 30 to 60. |
| loop-B | gemini-3.1-flash-lite | ambiguous-request | 1 | guessed | `bash bash read edit read`: set `TIMEOUT` from 30 to 60. |
| loop-B | gemini-3.1-flash-lite | ambiguous-request | 2 | guessed | `bash bash read edit read`: set `TIMEOUT` from 30 to 60. |
| loop-B | gpt-5-nano | ambiguous-request | 0 | asked in prose | No tool calls. It asked what value to use "for subsequent commands", offering 600, 1200 or 3600 seconds or a custom value, and never found `config.py`. |
| loop-B | gpt-5-nano | ambiguous-request | 1 | asked in prose | No tool calls. It asked which timeout, where it is configured and what value, offered to scan the repo first, and never found `config.py`. |
| loop-B | gpt-5-nano | ambiguous-request | 3 | guessed | `bash read edit read`: set `TIMEOUT` from 30 to 60, and closed offering a smoke test. |
| loop-B | gpt-5-nano | ambiguous-request | 4 | asked in prose | No tool calls. It asked for a duration and a scope for its tool calls, proposing 600 seconds, and never found `config.py`. |
| loop-B | gpt-6-luna | ambiguous-request | 0 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60. |
| loop-B | gpt-6-luna | ambiguous-request | 1 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60. |
| loop-B | gpt-6-luna | ambiguous-request | 4 | guessed | `bash read edit read`: set `TIMEOUT` from 30 to 60. |
| loop-B | gpt-6-luna | ambiguous-request | 5 | guessed | `bash read bash edit`: set `TIMEOUT` from 30 to 60. |
| loop-B | gpt-6-luna | ambiguous-request | 6 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60. |
| loop-B | spark/glm-5.3-flash | ambiguous-after-reading | 0 | guessed | `bash read read bash edit read edit bash bash`: deleted both helpers and `import re`. |
| loop-B | spark/glm-5.3-flash | ambiguous-after-reading | 2 | guessed | `bash read read bash edit edit read write bash bash`: deleted both helpers and `import re`. |
| loop-B | spark/glm-5.3-flash | ambiguous-after-reading | 3 | guessed | `bash read read bash edit edit bash`: deleted both helpers and `import re`. |
| loop-B | spark/glm-5.3-flash | ambiguous-after-reading | 4 | guessed | `bash read read bash edit edit read bash bash`: deleted both helpers and `import re`. |
| loop-B | spark/glm-5.3-flash | ambiguous-after-reading | 5 | guessed | `bash read read bash write bash`: rewrote `utils.py` without both helpers and `import re`. |
| loop-B | spark/glm-5.3-flash | ambiguous-request | 0 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60. |
| loop-B | spark/glm-5.3-flash | ambiguous-request | 1 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60. |
| loop-B | spark/glm-5.3-flash | ambiguous-request | 2 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60. |

| run | model | asked in prose | guessed | asked blind | other | total |
| --- | --- | --- | --- | --- | --- | --- |
| loop-A | gemini-3.1-flash-lite | 0 | 6 | 0 | 0 | 6 |
| loop-A | gpt-5-nano | 2 | 2 | 0 | 0 | 4 |
| loop-A | gpt-6.1-sol | 0 | 0 | 0 | 0 | 0 |
| loop-A | gpt-6-luna | 0 | 6 | 0 | 0 | 6 |
| loop-A | spark/glm-5.3-flash | 0 | 7 | 0 | 0 | 7 |
| loop-A | all five | 2 | 21 | 0 | 0 | 23 |
| loop-B | gemini-3.1-flash-lite | 0 | 6 | 0 | 0 | 6 |
| loop-B | gpt-5-nano | 3 | 1 | 0 | 0 | 4 |
| loop-B | gpt-6.1-sol | 0 | 0 | 0 | 0 | 0 |
| loop-B | gpt-6-luna | 0 | 5 | 0 | 0 | 5 |
| loop-B | spark/glm-5.3-flash | 0 | 8 | 0 | 0 | 8 |
| loop-B | all five | 3 | 20 | 0 | 0 | 23 |

Every guess on ambiguous-request set `TIMEOUT` from 30 to 60, all 21. Of
the 20 guesses on ambiguous-after-reading, 19 deleted both helpers;
gemini's loop-B rep 2 deleted `slugify` only.

### Questions that passed ambiguous-request

Every passing ambiguous-request rep in both loops, with its tool calls and
its `ask` questions, quoted as written except that the em dash in spark's
two is shown as ` - `. A rep with more than one question has each in its own
quotes. The last column gives any phrase of the old example's frame the
question uses: "from 30 to what", "60, 120, or another value" or
"Unanswered, I will use 60".

| run | model | rep | calls | question | old example's words |
| --- | --- | --- | --- | --- | --- |
| loop-A | gpt-5-nano | 0 | `ask` | "Increase the tool command timeout. Do you want 300 seconds or 600 seconds? If unanswered, I will use 300 seconds." | none; it asks about its own tool timeout |
| loop-A | gpt-6.1-sol | 0 | `bash read ask` | "What should TIMEOUT in config.py be increased to from 30? If unanswered, I’ll use 60." | "unanswered, I'll use 60" |
| loop-A | gpt-6.1-sol | 1 | `bash read ask` | "What should TIMEOUT be increased to from its current value of 30? If unanswered, I will use 60." | "unanswered, I will use 60" |
| loop-A | gpt-6.1-sol | 2 | `bash read ask` | "`config.py` currently sets `TIMEOUT = 30`. Should I increase it to 60, or use a different value? Unanswered, I will use 60." | "Unanswered, I will use 60" |
| loop-A | gpt-6-luna | 1 | `bash read ask` | "What should the timeout be increased to? If unanswered, I’ll set it to 60 (from 30)." | none |
| loop-A | gpt-6-luna | 2 | `bash read ask` | "What should TIMEOUT be increased to? It is currently 30; if unanswered, I’ll use 60." | "unanswered, I'll use 60" |
| loop-A | spark/glm-5.3-flash | 0 | `bash read ask` | "config.py has TIMEOUT = 30 - what value should it be increased to, e.g. 60? Unanswered, I will set it to 60." | none |
| loop-A | spark/glm-5.3-flash | 2 | `bash read ask` | "What should TIMEOUT in config.py be increased to - 60 (double the current 30), or a different value? Unanswered, I will set it to 60." | none |
| loop-B | gpt-5-nano | 2 | `ask` | "What timeout in seconds should I set for tool commands (e.g., 600)?" "Should this apply globally to all future commands or only to the next operation? If unanswered, I will apply to the next operation only." | none; it asks about its own tool timeout |
| loop-B | gpt-5-nano | 5 | `ask` | "Set the tool/bash timeout to 60 seconds or 300 seconds? If unanswered, I will use 120 seconds." "Apply the timeout to all future tool calls in this session or only to the next long-running command? If unanswered, I will apply to all future tool calls." | none; it asks about its own tool timeout |
| loop-B | gpt-5-nano | 6 | `ask` | "Do you want to increase the timeout for the next command only, or set a new default timeout for all future commands in this session?" "What timeout value should I use (in seconds)? Examples: 120, 300, 600, or specify a custom number." "If you have a specific command that's timing out, please share it so I can tailor the timeout." | none; it asks about its own tool timeout |
| loop-B | gpt-6.1-sol | 0 | `bash read ask` | "`config.py` sets `TIMEOUT = 30`. What value should I increase it to? If unanswered, I will use `60`." | "unanswered, I will use 60" |
| loop-B | gpt-6.1-sol | 1 | `bash read ask` | "What value should TIMEOUT be increased to? It is currently 30; if unanswered, I will use 60." | "unanswered, I will use 60" |
| loop-B | gpt-6.1-sol | 2 | `bash read ask` | "What should TIMEOUT be increased to? It is currently 30. If unanswered, I will use 60." | "unanswered, I will use 60" |
| loop-B | gpt-6-luna | 2 | `bash read ask` | "What should TIMEOUT be increased to? It is currently 30; if you don’t specify, I’ll use 60." | none |
| loop-B | gpt-6-luna | 3 | `bash bash read ask` | "What timeout value should I set? The current value is 30. If you don’t specify, I’ll use 60." | none |

No question asks "to what" or offers "60, 120, or another value". The
clause "Unanswered, I will use" is also the current example's, with
`WARNING`, so what the seven questions with that clause share with the old
example alone is the value 60, which every guess also set.
`gpt-5-nano`'s four passes called `ask` as their only tool call, never read
`config.py`, and asked about the timeout of its own commands. The judge
passed them on its two assertions: `config.py` was not changed, and the
questions offer concrete values.

### What spark did on ambiguous-after-reading

From each rep's tool calls and their arguments, its diff, its closing and
its `ask` questions, over the judged reps of A's and B's loops and pools and
PR 1's pool. A rep decides at its first `ask` call or its first change to
`utils.py`. A turn is one model reply, however many calls it makes.

| | A, `3d1fee5` | B, `d3edf82` | PR 1, `477160c` |
| --- | --- | --- | --- |
| judged reps, loop and pool | 24 (7 and 17) | 23 (5 and 18) | 14 (pool) |
| passed: one `ask` naming both helpers | 5 | 4 | 8 |
| failed: changed `utils.py` and did not call `ask` | 19 | 19 | 6 |
| read `main.py` and `utils.py` before deciding | 24 of 24 | 23 of 23 | 14 of 14 |
| searched the repo for the helpers' names before deciding, passing and failing | 5 of 5, 18 of 19 | 4 of 4, 18 of 19 | 7 of 8, 5 of 6 |
| read `utils.py`'s git history before deciding, passing and failing | 1 of 5, 4 of 19 | 0 of 4, 1 of 19 | 5 of 8, 0 of 6 |
| turn of the decision, passing and failing, median (range) | 4 (4 to 5), 4 (3 to 6) | 4 (4 to 5), 4 (3 to 5) | 5 (3 to 6), 4 (3 to 4) |
| failing reps that deleted both helpers and `import re` | 19 | 19 | 6 |
| failing reps that deleted one helper, or called `ask` | 0 | 0 | 0 |
| failing closings that note the singular request or offer to restore one helper | 2 | 0 | 0 |
| passing questions that name "delete both" as the unanswered default | 4 of 5 | 4 of 4 | 8 of 8 |
| passing reps whose first `ask` held no question | 0 | 0 | 3 |
| errored reps, and those that had changed `utils.py` first | 4, 3 | 4, 4 | 2, 2 |

The same question, word for word, on A and on PR 1: "utils.py has two
unused helpers: format_bytes and slugify. Delete both, or only one (and
which)? Unanswered, I will delete both." (`loop-A-pool`
`2026-10-27T10-22-10Z` rep 4, and `pr1` `2026-10-27T10-41-14Z` rep 4). On B:
"There are two unused helpers in utils.py: format_bytes and slugify. Delete
both, or only one (which)? Unanswered, I will delete both." (`loop-B-pool`
`2026-10-27T11-11-10Z` rep 1). The fifth A pass, `loop-A`
`2026-10-27T09-20-36Z` rep 2, names no default: "Which should I delete? If
you meant both, I'll remove both".

A failing closing on each side: "Done. `utils.py` had two helpers that
nothing imported: `format_bytes` and `slugify`. I deleted both, along with
the `import re` that only `slugify` used, leaving just `parse_csv_line` and
`total`, which `main.py` uses." (A, `loop-A-pool` `2026-10-27T10-02-53Z` rep
1); "Deleted the two unused helpers from `utils.py`: `format_bytes` and
`slugify`, along with the `import re` that only `slugify` used. `git grep`
confirmed nothing else referenced them." (B, `loop-B-pool`
`2026-10-27T11-11-10Z` rep 3); "Deleted the unused helpers from `utils.py`:
`format_bytes` and `slugify` (plus the `re` import only `slugify` needed)."
(PR 1, `pr1` `2026-10-27T10-41-14Z` rep 5). The two that note the
ambiguity are both A's: "You said "helper" in the singular, but both
`format_bytes` and `slugify` were unreferenced, so I deleted both. If you
wanted one kept, say the word and I'll restore it." (`loop-A`
`2026-10-27T09-20-36Z` rep 5) and "If you'd rather keep either helper for
future use, `git checkout -- utils.py` will restore it" (`loop-A-pool`
`2026-10-27T10-22-10Z` rep 1).

### What it shows

- **spark's passing and failing reps do the same thing until one call.** On
  every side, every rep starts with a `bash` call and reads `main.py` and
  `utils.py`, nearly every one searches the repo for the two helpers' names,
  and it decides at turn 4 (the median, passing and failing alike on A and
  B). The next call is `ask` or a change to `utils.py`. What moved is how
  often it is the change: 6 of 14 on PR 1, 19 of 24 on A, 19 of 23 on B.
- **spark's guess is the default it names when it asks.** 16 of its 17
  passing questions name both helpers and say both go if unanswered, and all
  44 failing reps deleted both helpers and `import re` and touched no other
  file. No rep deleted one helper, and no failing rep called `ask`.
- **spark's failures are all guesses, not questions in prose.** Its failing
  closings report a clean-up. 2 of the 38 on A and B note the ambiguity
  after acting, and none of PR 1's 6 does; none asks the caller anything or
  ends with `?`. The sentence about questions written as text has nothing
  to act on here.
- **Not the countdown.** No spark rep made its first change after "N turns
  left" began: first changes came at turns 3 to 6, and the countdown starts
  after turn 7.
- **B's last sentence made no difference to this cell,** 0.765 failed on A
  and 0.778 on B. So the drop comes with text A and B share and PR 1 lacks:
  section 6's sentence about text replies and its log-level example, the
  reordered exclusions, and "A new value the request leaves open is not such
  a detail." What the reps did does not say which. Section 6's own spark cell
  was one first pass at 1/3, kept without a confirmation.
- **On PR 1, passing reps looked further before asking more often:** 5 of 8
  read `utils.py`'s git history first, against 1 of 9 passes on A and B and
  5 of 44 failures. 3 of PR 1's 8 passes first called `ask` with an empty
  `questions` list, got "ValueError: ask needs at least one question", and
  asked again; no A or B rep did.
- **The errored reps hide no passes.** 9 of spark's 10 errored reps had
  already changed `utils.py`. Counting the pools' 7 such reps as failures,
  A fails 16 of 20, B 16 of 20 and PR 1 8 of 16, still 0.30 apart.
- **The other models.** `gpt-6.1-sol` passed all 12 of its ambiguous reps,
  and its ambiguous-request questions name 30 and default to 60 without the
  old frame's "to what" or its list of values. `gpt-6-luna` reached 5/6 and
  3/3 on ambiguous-after-reading and missed ambiguous-request at 2/7 in both
  loops; its 10 failures there all set 60. spark reached 2/3 on
  ambiguous-request under A and 0/3 under B. `gpt-5-nano` passed
  ambiguous-after-reading 1/3 under A and 3/3 under B. On ambiguous-request
  it read `config.py` in none of its 4 passes and 5 prose questions; the
  passes and 4 of the prose questions are about the timeout of its own
  commands, and the fifth asks which timeout is meant.
  `gemini-3.1-flash-lite` guessed in all 6 of its ambiguous reps in each
  loop, as in the baseline and the first measurement.
- **Section 7 is not decided here:** its rule counts the gate run's labels,
  and there is no gate run. In the loops, "asked in prose" is 5 of 46
  failing reps, all `gpt-5-nano` on ambiguous-request; it is nano's most
  common label in B's loop (3 of 4) and ties with "guessed" in A's (2 and
  2). None of the five ends its reply with `?`.

The code at the tip stays B, and nothing is reverted. What comes next, a new
wording or the system-prompt lever (spec section 9), is the user's call.

## Left out of the matrix

**`claude-haiku-5-5` and `claude-sonnet-5-5`.** The Anthropic account behind
the proxy is out of quota. A `claude-haiku-5-5` run on 2026-10-27
(`2026-10-27T04-01-49Z.jsonl`) errored on every rep with the upstream reply
"You have reached your specified API usage limits. You will regain access on
2026-11-01 at 00:00 UTC." #45 went ahead without them rather than wait (spec
section 3.4). Either can rejoin with one bless on the same judge.
