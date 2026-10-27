# Issue 45: the matrix before the `ask` change

**Date:** 2026-10-27
**Baselines blessed at:** `fa56e9d`, each baseline's `[meta] commit`. That
commit sets `max_turns = 12` on the two ambiguous cases, and only those two
cases were run on it. The other cases were run on `65749a2`. `src/nare` is the
same at both, and unchanged from `main`.
**Judge:** `gpt-6.1-sol`, for every model.
**Runs:** smoke tier, 3 reps per case, `NARE_STREAM=1`.

These are the "before" numbers of spec section 5
(`docs/superpowers/specs/2026-10-23-ask-across-models-design.md`). Five of
the seven matrix models are blessed so far. The two Claude models are under
"Not yet in the matrix". The two ambiguous cases were first blessed with
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

## Not yet in the matrix

**`claude-haiku-5-5` and `claude-sonnet-5-5`.** The Anthropic account behind
the proxy is out of quota. A `claude-haiku-5-5` run on 2026-10-27
(`2026-10-27T04-01-49Z.jsonl`) errored on every rep with the upstream reply
"You have reached your specified API usage limits. You will regain access on
2026-11-01 at 00:00 UTC." Both models are run and blessed after then, on this
branch's code and with the same judge.
