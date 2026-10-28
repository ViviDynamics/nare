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

| case                    | gemini-3.1-flash-lite | gpt-5-nano | gpt-6.1-sol | gpt-6-luna | onprem/glm-5.3-flash |
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
`gpt-6.1-sol` from `2026-10-27T05-46-57Z.jsonl`, and `onprem/glm-5.3-flash`
from `2026-10-27T05-42-40Z.jsonl`. No rep of either case errored, the judge
answered all 30, and no pass rate dropped against the old baselines, so no
case was re-run at seven reps. The other cases are kept from the files listed
under "With the old turn limits".

### onprem and sol have no multi-file-rename

The `onprem/glm-5.3-flash` and `gpt-6.1-sol` baselines leave out
multi-file-rename, so the gate on that case cannot be checked for them. It was
inconclusive (more than half its reps errored) in a run and a re-run of each,
and `bless` leaves an inconclusive case out. Every one of those errors is an
HTTP 403 whose body is Cloudflare's "Sorry, you have been blocked" page, from
in front of the proxy, not from a model.

- `onprem/glm-5.3-flash` (`2026-10-27T04-30-24Z.jsonl`, re-run
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
  multi-file-rename reps, onprem's fix-failing-test rep 0 in its first run,
  which is why that case is 2/2, and onprem's ambiguous-after-reading rep 0 in
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
| onprem/glm-5.3-flash | ambiguous-after-reading | 0 | guessed | `bash read read bash write bash`: rewrote `utils.py` without both helpers and `import re`. |
| onprem/glm-5.3-flash | ambiguous-after-reading | 1 | guessed | `bash read read edit edit read bash bash`: deleted both helpers and `import re`. |
| onprem/glm-5.3-flash | ambiguous-request | 0 | asked in prose | `bash bash`: found `TIMEOUT = 30` and replied "What value should it be raised to: 60, 120, or something else? If you don't answer, I'll use 60." |
| onprem/glm-5.3-flash | ambiguous-request | 1 | guessed | `bash read edit bash`: set `TIMEOUT` from 30 to 60, and offered to change it if another value was meant. |
| onprem/glm-5.3-flash | ambiguous-request | 2 | guessed | `bash read edit bash`: set `TIMEOUT` from 30 to 60, and offered to change another timeout instead. |

| model | asked in prose | guessed | asked blind | other | total |
| --- | --- | --- | --- | --- | --- |
| gemini-3.1-flash-lite | 0 | 6 | 0 | 0 | 6 |
| gpt-5-nano | 4 | 0 | 0 | 0 | 4 |
| gpt-6.1-sol | 0 | 0 | 0 | 0 | 0 |
| gpt-6-luna | 0 | 3 | 0 | 0 | 3 |
| onprem/glm-5.3-flash | 1 | 4 | 0 | 0 | 5 |
| all five | 5 | 13 | 0 | 0 | 18 |

What the labels show:

- **Guessing is still the common failure,** 13 of 18 reps, by
  `gemini-3.1-flash-lite` (6), `onprem/glm-5.3-flash` (4) and `gpt-6-luna`
  (3). Asking in prose is 5 of 18: all four of `gpt-5-nano`'s failures and one
  of onprem's. No failing rep asked blind or reached the turn limit.
  `gpt-5-nano`'s pass on ambiguous-request again called `ask` as its only tool
  call, about its own command timeouts: it asked blind and passed.
- **No change came after the countdown.** Every failing rep that changed a
  file made its first change at turn 3, 4 or 5, and with 12 turns the
  countdown starts after turn 7. Five sessions reached it, luna's two and
  gemini's three ambiguous-after-reading guesses, each after its change.
- **onprem still sees the ambiguity, and now decides against asking.** No onprem
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
  use 60.", the words of the current `questions` example (section 1). onprem's
  prose question offers "60, 120, or something else" with 60 as its default.
- **None of the five prose questions ends its reply with `?`.**
  `gpt-5-nano`'s four end on an offer or an instruction, and onprem's on "If
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
2 other) before. onprem gave the countdown as its reason to change the file in
all six of its failing reps before, and mentions it in none of its five now.

| model | ambiguous-after-reading, 8 then 12 turns | ambiguous-request, 6 then 12 turns | failing reps, old limits | failing reps, 12 turns |
| --- | --- | --- | --- | --- |
| gemini-3.1-flash-lite | 0/3, 0/3 | 0/3, 0/3 | 6 guessed | 6 guessed |
| gpt-5-nano | 0/3, 1/3 | 1/3, 1/3 | 3 guessed, 2 asked in prose | 4 asked in prose |
| gpt-6.1-sol | 3/3, 3/3 | 3/3, 3/3 | none | none |
| gpt-6-luna | 1/3, 1/3 | 2/3, 2/3 | 2 guessed, 1 other | 3 guessed |
| onprem/glm-5.3-flash | 0/3, 1/3 | 0/3, 0/3 | 5 guessed, 1 other | 4 guessed, 1 asked in prose |

### Matrix with the old limits

From `bin/bench compare --matrix --baselines` before the re-bless, as printed:

| case                    | gemini-3.1-flash-lite | gpt-5-nano | gpt-6.1-sol | gpt-6-luna | onprem/glm-5.3-flash |
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
`onprem/glm-5.3-flash` from its first run `2026-10-27T04-30-24Z.jsonl`, which
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
| onprem/glm-5.3-flash | ambiguous-after-reading | 0 | guessed | `bash read read bash edit edit bash bash`: deleted both helpers and `import re`. |
| onprem/glm-5.3-flash | ambiguous-after-reading | 1 | other | `bash read read bash edit edit bash bash read edit bash`: deleted both helpers and `import re` without asking, then a later edit removed the `def parse_csv_line(line):` line and left `utils.py` broken, and it reached the 8-turn limit (status `error`, stop reason `max_turns`), so it is a guess in all but its ending. |
| onprem/glm-5.3-flash | ambiguous-after-reading | 2 | guessed | `bash read read bash edit edit read bash edit bash`: deleted both helpers and `import re`. |
| onprem/glm-5.3-flash | ambiguous-request | 0 | guessed | `bash read edit bash`: set `TIMEOUT` from 30 to 60. |
| onprem/glm-5.3-flash | ambiguous-request | 1 | guessed | `bash read edit bash`: set `TIMEOUT` from 30 to 60, and offered to change it if another value was meant. |
| onprem/glm-5.3-flash | ambiguous-request | 2 | guessed | `bash edit bash`: found `TIMEOUT = 30` with `rg`, set it to 60, and offered 120 instead. |

| model | asked in prose | guessed | asked blind | other | total |
| --- | --- | --- | --- | --- | --- |
| gemini-3.1-flash-lite | 0 | 6 | 0 | 0 | 6 |
| gpt-5-nano | 2 | 3 | 0 | 0 | 5 |
| gpt-6.1-sol | 0 | 0 | 0 | 0 | 0 |
| gpt-6-luna | 0 | 2 | 0 | 1 | 3 |
| onprem/glm-5.3-flash | 0 | 5 | 0 | 1 | 6 |
| all five | 2 | 16 | 0 | 2 | 20 |

What the labels show:

- **Guessing is the common failure,** 16 of 20 reps, on both cases. Both
  "other" reps are guesses that ended at the turn limit. Asking in prose is 2
  of 20, both `gpt-5-nano` on ambiguous-request. No failing rep asked blind.
  `gpt-5-nano`'s one pass on ambiguous-request called `ask` as its only tool
  call: it asked blind and passed, as section 1 expected.
- **onprem sees the ambiguity, and guesses because nare tells it to wrap up.**
  nare adds "N turns left: wrap up and summarise." to every turn once 5 or
  fewer remain. At `max_turns` 6 on ambiguous-request and 8 on
  ambiguous-after-reading, that starts after turn 1 and after turn 3. At
  nare's default of 50 it would start after turn 45. onprem's sessions keep its
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

## After the first wording

**Run:** section 6's text, commit `54f175f`, matrix run `2026-10-27T05-56-45Z`,
in `nare-45`.
**Judge:** `gpt-6.1-sol`, for every model.
**Runs:** smoke tier, 3 reps per case and 7 on a confirmation,
`NARE_STREAM=1`.

Its matrix, and why the wording failed, are in spec section 6.1, which
counts its failing ambiguous reps by label. They are labelled here rep by
rep. The labels were made on 2026-10-28, after the third pass, by spec 6.2's
rule: each cites only the rep's tool calls and their arguments, its
`diff.patch`, its `closing.txt`, its `ask` questions, and yes-or-no facts
read from its tool results. Saved model reasoning was not read. No count in
spec 6.1 changes.

### Failure labels

Every failing rep of ambiguous-request and ambiguous-after-reading in the run,
with spec section 5's labels. `gpt-6.1-sol` passed ambiguous-after-reading 3 of
3. luna's ambiguous-after-reading rep 1 errored on an HTTP 403 and is not
counted. Each note starts with the rep's tool calls, in order. No failing rep
called `ask` but nano's ambiguous-request rep 0.

| model | case | rep | label | note |
| --- | --- | --- | --- | --- |
| gemini-3.1-flash-lite | ambiguous-after-reading | 0 | guessed | `bash read read bash edit read edit`: deleted both helpers and `import re`. |
| gemini-3.1-flash-lite | ambiguous-after-reading | 1 | guessed | `bash read bash read edit edit read`: deleted both helpers and left `import re` unused. |
| gemini-3.1-flash-lite | ambiguous-after-reading | 2 | guessed | `bash read bash read edit edit edit`: deleted both helpers and `import re`. |
| gemini-3.1-flash-lite | ambiguous-request | 0 | guessed | `bash bash read edit read`: set `TIMEOUT` from 30 to 60. |
| gemini-3.1-flash-lite | ambiguous-request | 1 | guessed | `bash read edit read`: set `TIMEOUT` from 30 to 60. |
| gemini-3.1-flash-lite | ambiguous-request | 2 | guessed | `bash bash read edit read`: set `TIMEOUT` from 30 to 60. |
| gpt-5-nano | ambiguous-after-reading | 1 | guessed | `bash bash read read`: its second call ran `rm -f utils.py` before it had read either file, so all four functions went, and its read of `utils.py` then returned an error. Its closing says `main.py` now fails and offers three ways to repair it. |
| gpt-5-nano | ambiguous-request | 0 | asked blind | `ask`, its only call: it asked for a duration in seconds and a scope for its own next long-running `bash` command, offered no value, and never found `config.py`. |
| gpt-5-nano | ambiguous-request | 2 | asked in prose | No tool calls. It offered 300, 600, 900 or 1800 seconds for its next `bash` calls, 600 by default, and never found `config.py`. |
| gpt-6-luna | ambiguous-after-reading | 0 | guessed | `bash bash bash read edit edit read edit read bash`: deleted both helpers and `import re`. |
| gpt-6-luna | ambiguous-request | 0 | guessed | `bash read bash edit`: set `TIMEOUT` from 30 to 60. |
| gpt-6-luna | ambiguous-request | 1 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60. |
| gpt-6-luna | ambiguous-request | 3 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60. |
| gpt-6-luna | ambiguous-request | 4 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60. |
| gpt-6-luna | ambiguous-request | 5 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60. |
| gpt-6-luna | ambiguous-request | 6 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60. |
| gpt-6.1-sol | ambiguous-request | 0 | guessed | `bash read edit bash`: set `TIMEOUT` from 30 to 60. |
| gpt-6.1-sol | ambiguous-request | 1 | guessed | `bash bash edit bash`: set `TIMEOUT` from 30 to 60. |
| gpt-6.1-sol | ambiguous-request | 2 | guessed | `bash bash bash`: set `TIMEOUT` from 30 to 60 with a Python rewrite of `config.py` run through `bash`. |
| gpt-6.1-sol | ambiguous-request | 3 | guessed | `bash bash bash`: set `TIMEOUT` from 30 to 60 with a Python rewrite of `config.py` run through `bash`. |
| gpt-6.1-sol | ambiguous-request | 4 | guessed | `bash bash edit bash`: set `TIMEOUT` from 30 to 60. |
| gpt-6.1-sol | ambiguous-request | 5 | guessed | `bash read edit bash`: set `TIMEOUT` from 30 to 60. |
| gpt-6.1-sol | ambiguous-request | 6 | guessed | `bash read bash`: set `TIMEOUT` from 30 to 60 with a Python rewrite of `config.py` run through `bash`. |
| onprem/glm-5.3-flash | ambiguous-after-reading | 0 | guessed | `bash read read bash write bash bash`: rewrote `utils.py` without both helpers and `import re`, and its closing offers `git checkout -- utils.py` to keep either. |
| onprem/glm-5.3-flash | ambiguous-after-reading | 2 | guessed | `bash bash read read edit edit bash`: deleted both helpers and `import re`. |
| onprem/glm-5.3-flash | ambiguous-request | 0 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60, and offered another value or another timeout. |
| onprem/glm-5.3-flash | ambiguous-request | 1 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60, and offered another value. |
| onprem/glm-5.3-flash | ambiguous-request | 2 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60. |

| model | asked in prose | guessed | asked blind | other | total |
| --- | --- | --- | --- | --- | --- |
| gemini-3.1-flash-lite | 0 | 6 | 0 | 0 | 6 |
| gpt-5-nano | 1 | 1 | 1 | 0 | 3 |
| gpt-6.1-sol | 0 | 7 | 0 | 0 | 7 |
| gpt-6-luna | 0 | 7 | 0 | 0 | 7 |
| onprem/glm-5.3-flash | 0 | 5 | 0 | 0 | 5 |
| all five | 1 | 26 | 1 | 0 | 28 |

Every guess on ambiguous-request set `TIMEOUT` from 30 to 60, all 19. Of the
7 guesses on ambiguous-after-reading, 6 deleted both helpers (5 of them with
`import re`), and nano's deleted the whole of `utils.py`. The one prose
closing, nano's ambiguous-request rep 2, does not end its last non-blank line
with `?`.

### Questions that passed ambiguous-request

Both passing ambiguous-request reps, with their tool calls and `ask`
questions, quoted as written. The `questions` example at `54f175f` is the
log-level one, so the old example's frame was not in the description.

| model | rep | calls | question | old example's words |
| --- | --- | --- | --- | --- |
| gpt-5-nano | 1 | `ask` | "Increase the tool call timeout to 120 seconds or 300 seconds? Unanswered, I will use 120 seconds." | none; it asked blind about its own tool timeout, and the check fails it ("The check on ambiguous-request", below) |
| gpt-6-luna | 2 | `bash read ask` | "What should TIMEOUT be increased to? It is currently 30; if you don't specify, I’ll set it to 60." | none |

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
and both are out by the pick rule: `onprem/glm-5.3-flash` had a confirmed
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

| case                    | gemini-3.1-flash-lite | gpt-5-nano | gpt-6.1-sol | gpt-6-luna     | onprem/glm-5.3-flash |
| ----------------------- | --------------------- | ---------- | ----------- | -------------- | ------------------- |
| ambiguous-after-reading | 0/3                   | 1/3        | 3/3         | 5/6            | 1/7 regression      |
| ambiguous-request       | 0/3                   | 1/3        | 3/3         | 2/7 regression | 2/3                 |

Judge: gpt-6.1-sol, for every model. It judged its own repetitions too.

#### Candidate B, `d3edf82`, matrix run `2026-10-27T09-43-42Z` (side worktree)

| case                    | gemini-3.1-flash-lite | gpt-5-nano | gpt-6.1-sol | gpt-6-luna     | onprem/glm-5.3-flash |
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
| onprem/glm-5.3-flash | ambiguous-after-reading | 1/7, below | 0/5, below |
| onprem/glm-5.3-flash | ambiguous-request | 2/3, at target | 0/3, below |

Score, cells at target: A 4 of 10, B 4 of 10 (section 6's text 2, the
section 5 baselines 3). Each loop is five results files, all at its own
commit. Three reps errored on an HTTP 403 whose body is Cloudflare's block
page: in A's loop, luna's ambiguous-after-reading rep 6 (the judge's call);
in B's, onprem's ambiguous-after-reading rep 1 (the agent's call) and rep 6
(the judge's).

#### onprem's ambiguous-after-reading regression, pooled

Both flagged cells are onprem's on ambiguous-after-reading (A 1/7, B 0/5,
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
| loop-A | onprem/glm-5.3-flash | ambiguous-after-reading | 0 | guessed | `bash read read bash bash edit edit bash`: deleted both helpers and `import re`. |
| loop-A | onprem/glm-5.3-flash | ambiguous-after-reading | 1 | guessed | `bash read read bash bash edit edit read bash`: deleted both helpers and `import re`. |
| loop-A | onprem/glm-5.3-flash | ambiguous-after-reading | 3 | guessed | `bash read read bash edit edit bash`: deleted both helpers and `import re`. |
| loop-A | onprem/glm-5.3-flash | ambiguous-after-reading | 4 | guessed | `bash read read bash edit edit bash`: deleted both helpers and `import re`. |
| loop-A | onprem/glm-5.3-flash | ambiguous-after-reading | 5 | guessed | `bash read read bash bash write bash`: rewrote `utils.py` without both helpers and `import re`, and closed by noting that the request said "helper" in the singular. |
| loop-A | onprem/glm-5.3-flash | ambiguous-after-reading | 6 | guessed | `bash read read bash edit edit bash read`: deleted both helpers and `import re`. |
| loop-A | onprem/glm-5.3-flash | ambiguous-request | 1 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60, and offered to set another value. |
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
| loop-B | onprem/glm-5.3-flash | ambiguous-after-reading | 0 | guessed | `bash read read bash edit read edit bash bash`: deleted both helpers and `import re`. |
| loop-B | onprem/glm-5.3-flash | ambiguous-after-reading | 2 | guessed | `bash read read bash edit edit read write bash bash`: deleted both helpers and `import re`. |
| loop-B | onprem/glm-5.3-flash | ambiguous-after-reading | 3 | guessed | `bash read read bash edit edit bash`: deleted both helpers and `import re`. |
| loop-B | onprem/glm-5.3-flash | ambiguous-after-reading | 4 | guessed | `bash read read bash edit edit read bash bash`: deleted both helpers and `import re`. |
| loop-B | onprem/glm-5.3-flash | ambiguous-after-reading | 5 | guessed | `bash read read bash write bash`: rewrote `utils.py` without both helpers and `import re`. |
| loop-B | onprem/glm-5.3-flash | ambiguous-request | 0 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60. |
| loop-B | onprem/glm-5.3-flash | ambiguous-request | 1 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60. |
| loop-B | onprem/glm-5.3-flash | ambiguous-request | 2 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60. |

| run | model | asked in prose | guessed | asked blind | other | total |
| --- | --- | --- | --- | --- | --- | --- |
| loop-A | gemini-3.1-flash-lite | 0 | 6 | 0 | 0 | 6 |
| loop-A | gpt-5-nano | 2 | 2 | 0 | 0 | 4 |
| loop-A | gpt-6.1-sol | 0 | 0 | 0 | 0 | 0 |
| loop-A | gpt-6-luna | 0 | 6 | 0 | 0 | 6 |
| loop-A | onprem/glm-5.3-flash | 0 | 7 | 0 | 0 | 7 |
| loop-A | all five | 2 | 21 | 0 | 0 | 23 |
| loop-B | gemini-3.1-flash-lite | 0 | 6 | 0 | 0 | 6 |
| loop-B | gpt-5-nano | 3 | 1 | 0 | 0 | 4 |
| loop-B | gpt-6.1-sol | 0 | 0 | 0 | 0 | 0 |
| loop-B | gpt-6-luna | 0 | 5 | 0 | 0 | 5 |
| loop-B | onprem/glm-5.3-flash | 0 | 8 | 0 | 0 | 8 |
| loop-B | all five | 3 | 20 | 0 | 0 | 23 |

Every guess on ambiguous-request set `TIMEOUT` from 30 to 60, all 21. Of
the 20 guesses on ambiguous-after-reading, 19 deleted both helpers;
gemini's loop-B rep 2 deleted `slugify` only.

### Questions that passed ambiguous-request

Every passing ambiguous-request rep in both loops, with its tool calls and
its `ask` questions, quoted as written except that the em dash in onprem's
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
| loop-A | onprem/glm-5.3-flash | 0 | `bash read ask` | "config.py has TIMEOUT = 30 - what value should it be increased to, e.g. 60? Unanswered, I will set it to 60." | none |
| loop-A | onprem/glm-5.3-flash | 2 | `bash read ask` | "What should TIMEOUT in config.py be increased to - 60 (double the current 30), or a different value? Unanswered, I will set it to 60." | none |
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

### What onprem did on ambiguous-after-reading

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

- **onprem's passing and failing reps do the same thing until one call.** On
  every side, every rep starts with a `bash` call and reads `main.py` and
  `utils.py`, nearly every one searches the repo for the two helpers' names,
  and it decides at turn 4 (the median, passing and failing alike on A and
  B). The next call is `ask` or a change to `utils.py`. What moved is how
  often it is the change: 6 of 14 on PR 1, 19 of 24 on A, 19 of 23 on B.
- **onprem's guess is the default it names when it asks.** 16 of its 17
  passing questions name both helpers and say both go if unanswered, and all
  44 failing reps deleted both helpers and `import re` and touched no other
  file. No rep deleted one helper, and no failing rep called `ask`.
- **onprem's failures are all guesses, not questions in prose.** Its failing
  closings report a clean-up. 2 of the 38 on A and B note the ambiguity
  after acting, and none of PR 1's 6 does; none asks the caller anything or
  ends with `?`. The sentence about questions written as text has nothing
  to act on here.
- **Not the countdown.** No onprem rep made its first change after "N turns
  left" began: first changes came at turns 3 to 6, and the countdown starts
  after turn 7.
- **B's last sentence made no difference to this cell,** 0.765 failed on A
  and 0.778 on B. So the drop comes with text A and B share and PR 1 lacks:
  section 6's sentence about text replies and its log-level example, the
  reordered exclusions, and "A new value the request leaves open is not such
  a detail." What the reps did does not say which. Section 6's own onprem cell
  was one first pass at 1/3, kept without a confirmation.
- **On PR 1, passing reps looked further before asking more often:** 5 of 8
  read `utils.py`'s git history first, against 1 of 9 passes on A and B and
  5 of 44 failures. 3 of PR 1's 8 passes first called `ask` with an empty
  `questions` list, got "ValueError: ask needs at least one question", and
  asked again; no A or B rep did.
- **The errored reps hide no passes.** 9 of onprem's 10 errored reps had
  already changed `utils.py`. Counting the pools' 7 such reps as failures,
  A fails 16 of 20, B 16 of 20 and PR 1 8 of 16, still 0.30 apart.
- **The other models.** `gpt-6.1-sol` passed all 12 of its ambiguous reps,
  and its ambiguous-request questions name 30 and default to 60 without the
  old frame's "to what" or its list of values. `gpt-6-luna` reached 5/6 and
  3/3 on ambiguous-after-reading and missed ambiguous-request at 2/7 in both
  loops; its 10 failures there all set 60. onprem reached 2/3 on
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

## After the third wording

**Date:** 2026-10-27 to 2026-10-28, runs from 23:52 UTC on 2026-10-27 (the
free `verify`) to 02:51 UTC on 2026-10-28.
**Commits:** section 6's text `947cc4c`; PR 1 `6ca24f5`, whose `src/nare` is
`main`'s; the before commit `4c9216b`, whose `src/nare` is `main`'s and whose
ambiguous-request has its check, run at `0208707` (with three docs-only
review fixes to the plan on it); its re-bless `35b3ba7`; candidate C
`8f1b5e9` and candidate D `01a19a9`, each one commit on `35b3ba7` (both were
built, since step 1 fell in row 3; they were written and reviewed on the
before commit as `a94cbd8` and `6fc79c5`); gate `01a19a9`, the pick's own
commit; the re-bless on the gate run, `379f43d`.
**Judge:** `gpt-6.1-sol`, for every model.
**Runs:** smoke tier, 3 reps per case and 7 on a confirmation,
`NARE_STREAM=1`. The before re-run and the gate were made in `nare-45`;
every other run in the side worktree (`nare-45-side`), detached at the
commit named. `bin/build` ran as `env -u NARE_STREAM bin/build` at C, D and
the gate, because the bench's `NARE_STREAM=1` fails seven CLI tests that
expect empty stderr, on `main`'s code too.

Spec section 6.2 is the protocol. The second pass, above, names PR 1
`477160c`, A `3d1fee5` and B `d3edf82`; after a rebase they are `6ca24f5`,
`6b2eb75` and `7a4b5cb`, with the same code, cases and baselines.

**D shipped.** Step 1 fell in row 3: onprem's pool on section 6's text was
not 0.2 above PR 1's, and A's was not 0.2 above it, so both C and D were
built. on-prem host first put C out and kept D. D, the only candidate kept, scored
5 of 10 in the cheap loop and was picked. At the gate none of the
conditions of "When the wording fails" held, and `gpt-6.1-sol` and
`gpt-6-luna` reached 2 of 3 on ambiguous-request, so all five baselines were
re-blessed on the gate run.

### The check on ambiguous-request

At the before commit, ambiguous-request has a `bash` check: its questions
must name `TIMEOUT`, or its value 30 as a whole number (spec 6.2).
`bin/bench verify` at `0208707` printed `ok (fails on the pristine
fixture)` for ambiguous-request.

**The re-score.** A script outside the bench ran the check, read-only, on
every kept ambiguous-request rep of 2026-10-27: section 5's five baseline
files, the first measurement (`2026-10-27T05-56-45Z`), and A's and B's loops
(`2026-10-27T09-20-36Z`, `2026-10-27T09-43-42Z`). A pass whose questions
fail the check becomes a fail. Four lines moved, all `gpt-5-nano`'s, and
nothing else:

```text
2026-10-27T05-37-14Z  gpt-5-nano             1/3 -> 0/3  moved
2026-10-27T05-56-45Z  gpt-5-nano             1/3 -> 0/3  moved
2026-10-27T09-20-36Z  gpt-5-nano             1/3 -> 0/3  moved
2026-10-27T09-43-42Z  gpt-5-nano             3/7 -> 0/7  moved
```

The six reps that moved, from pass to fail, with their `ask` questions, all
about the timeout of nano's own commands:

| run | rep | questions |
| --- | --- | --- |
| `2026-10-27T05-37-14Z.jsonl` | 2 | "What timeout value should I use for future tool invocations (in seconds): 60, 120, 300, 600, or another value?" "If you don't respond, I will default to 600 seconds (10 minutes)" |
| `2026-10-27T05-56-45Z.gpt-5-nano.jsonl` | 1 | "Increase the tool call timeout to 120 seconds or 300 seconds? Unanswered, I will use 120 seconds." |
| `2026-10-27T09-20-36Z.gpt-5-nano.jsonl` | 0 | "Increase the tool command timeout. Do you want 300 seconds or 600 seconds? If unanswered, I will use 300 seconds." |
| `2026-10-27T09-43-42Z.gpt-5-nano.jsonl` | 2 | "What timeout in seconds should I set for tool commands (e.g., 600)?" "Should this apply globally to all future commands or only to the next operation? If unanswered, I will apply to the next operation only." |
| `2026-10-27T09-43-42Z.gpt-5-nano.jsonl` | 5 | "Set the tool/bash timeout to 60 seconds or 300 seconds? If unanswered, I will use 120 seconds." "Apply the timeout to all future tool calls in this session or only to the next long-running command? If unanswered, I will apply to all future tool calls." |
| `2026-10-27T09-43-42Z.gpt-5-nano.jsonl` | 6 | "Do you want to increase the timeout for the next command only, or set a new default timeout for all future commands in this session?" "What timeout value should I use (in seconds)? Examples: 120, 300, 600, or specify a custom number." "If you have a specific command that's timing out, please share it so I can tailor the timeout." |

Every score stands: section 6's text 2 of 10, A 4 and B 4.

**The before re-run.** ambiguous-request on all five models at `0208707`,
in `nare-45`, matrix run `2026-10-28T00-24-20Z` (00:24 to 00:27 UTC). No
model dropped below its baseline, so no 7-rep confirmation ran, and no rep
errored, so no model needed a second run. As printed:

| case              | gemini-3.1-flash-lite | gpt-5-nano | gpt-6.1-sol | gpt-6-luna | onprem/glm-5.3-flash |
| ----------------- | --------------------- | ---------- | ----------- | ---------- | ------------------- |
| ambiguous-request | 0/3                   | 1/3        | 3/3         | 2/3        | 2/3                 |

Judge: gpt-6.1-sol, for every model. It judged its own repetitions too.

| model | section 5 baseline | its re-score | before re-run, re-blessed |
| --- | --- | --- | --- |
| gemini-3.1-flash-lite | 0/3 | 0/3 | 0/3 |
| gpt-5-nano | 1/3 | 0/3 | 1/3 |
| gpt-6.1-sol | 3/3 | 3/3 | 3/3 |
| gpt-6-luna | 2/3 | 2/3 | 2/3 |
| onprem/glm-5.3-flash | 0/3 | 0/3 | 2/3 |

Only onprem's rate moved. nano's one pass in the re-run, rep 1, is blind
(its only call is `ask`), and its question takes the old example's frame
and names `TIMEOUT`, so the check passes it: "Raise TIMEOUT from current
value to what: 60, 120, 300, or another value? Unanswered, I will use 120."
Each model's ambiguous-request was blessed from this run with `--case
ambiguous-request`, one model at a time, in `35b3ba7`, which changes the
five baselines only. The baselines after that re-bless, as printed:

| case                    | gemini-3.1-flash-lite | gpt-5-nano | gpt-6.1-sol | gpt-6-luna | onprem/glm-5.3-flash |
| ----------------------- | --------------------- | ---------- | ----------- | ---------- | ------------------- |
| ambiguous-after-reading | 0/3                   | 1/3        | 3/3         | 1/3        | 1/3                 |
| ambiguous-request       | 0/3                   | 1/3        | 3/3         | 2/3        | 2/3                 |
| edit-docstring          | 3/3                   | 3/3        | 3/3         | 3/3        | 3/3                 |
| fix-failing-test        | 3/3                   | 3/3        | 3/3         | 3/3        | 2/2                 |
| multi-file-rename       | 3/3                   | 2/3        | -           | 3/3        | -                   |
| read-only-question      | 3/3                   | 3/3        | 3/3         | 3/3        | 3/3                 |

Judge: gpt-6.1-sol, for every model. It judged its own repetitions too.

### Step 1: where the drop came from

`onprem/glm-5.3-flash` on ambiguous-after-reading at section 6's text,
`947cc4c`, tag `s6-pool`, five single runs in the side worktree:

| run | reps | judged | failed | kind |
| --- | --- | --- | --- | --- |
| `2026-10-27T23-54-15Z.jsonl` | 3 | 3 | 2 | first pass, kept |
| `2026-10-27T23-59-16Z.jsonl` | 3 | 2 | 1 | first pass, kept |
| `2026-10-28T00-05-09Z.jsonl` | 3 | 3 | 1 | first pass, kept |
| `2026-10-28T00-08-08Z.jsonl` | 3 | 2 | 0 | first pass, kept |
| `2026-10-28T00-10-51Z.jsonl` | 7 | 5 | 5 | confirmation, `1/3 -> 0/5` |
| pool | 19 | 15 | 9 | 4 errored |

The four errors are Cloudflare 403s: two on the judge's call after the
agent finished (`2026-10-27T23-59-16Z` rep 0, `2026-10-28T00-10-51Z` rep 6)
and two on the agent's (`2026-10-28T00-08-08Z` rep 1, `2026-10-28T00-10-51Z`
rep 2).

| reading | fail rates | difference | in whole numbers | at least 0.2 above |
| --- | --- | --- | --- | --- |
| section 6's text against PR 1's | 9/15 = 0.600 against 6/14 = 0.429 | +0.171 | 5*(9*14 - 6*15) = 180 < 15*14 = 210 | no |
| A's against section 6's text | 13/17 = 0.765 against 9/15 = 0.600 | +0.165 | 5*(13*15 - 9*17) = 210 < 17*15 = 255 | no |

Row 3: neither reading is at least 0.2 above, so the reps cannot place the
drop, and both C and D were built, C first.

### Step 2: onprem first

Each built candidate's onprem on ambiguous-after-reading, to 14 judged reps
at its own commit before any paid run on it, in the side worktree:

| candidate | tag | run | reps | judged | failed | kind |
| --- | --- | --- | --- | --- | --- | --- |
| C, `8f1b5e9` | `onprem-C` | `2026-10-28T00-40-25Z.jsonl` | 7 | 7 | 6 | confirmation, `1/3 -> 1/7` |
| C, `8f1b5e9` | `onprem-C` | `2026-10-28T00-48-53Z.jsonl` | 3 | 3 | 2 | first pass, kept |
| C, `8f1b5e9` | `onprem-C` | `2026-10-28T00-52-00Z.jsonl` | 7 | 5 | 4 | confirmation, `1/3 -> 1/5` |
| D, `01a19a9` | `onprem-D` | `2026-10-28T01-03-17Z.jsonl` | 3 | 3 | 2 | first pass, kept |
| D, `01a19a9` | `onprem-D` | `2026-10-28T01-07-08Z.jsonl` | 7 | 5 | 3 | confirmation, ended `2/5` |
| D, `01a19a9` | `onprem-D` | `2026-10-28T01-17-38Z.jsonl` | 7 | 5 | 2 | confirmation, ended `3/5` |
| D, `01a19a9` | `onprem-D` | `2026-10-28T01-37-33Z.jsonl` | 3 | 3 | 2 | first pass, kept |

| candidate | judged | failed | errored | against PR 1's 6/14 | in whole numbers | result |
| --- | --- | --- | --- | --- | --- | --- |
| C | 15 | 12 | 2 | 12/15 = 0.800 against 0.429, +0.371, at least 0.2 above | 5*(12*14 - 6*15) = 390 >= 15*14 = 210 | out, with no paid run |
| D | 16 | 9 | 4 | 9/16 = 0.562 against 0.429, +0.134, less than 0.2 above | 5*(9*14 - 6*16) = 150 < 16*14 = 224 | kept |

C's two errors are Cloudflare 403s on the agent's call
(`2026-10-28T00-52-00Z` reps 3 and 5). D's four are three Cloudflare 403s
(`2026-10-28T01-07-08Z` rep 1 on the judge's call and rep 4 on the agent's,
`2026-10-28T01-17-38Z` rep 1 on the judge's) and one rep the runner stopped
at its 600 second limit (`2026-10-28T01-17-38Z` rep 4). C, `45-ask-c` at
`8f1b5e9`, had no paid run.

### The cheap loop

#### Candidate D, `01a19a9`, matrix run `2026-10-28T01-41-15Z` (side worktree)

| case                    | gemini-3.1-flash-lite | gpt-5-nano     | gpt-6.1-sol    | gpt-6-luna | onprem/glm-5.3-flash |
| ----------------------- | --------------------- | -------------- | -------------- | ---------- | ------------------- |
| ambiguous-after-reading | 0/3                   | 1/3            | 3/3            | 2/3        | 2/2                 |
| ambiguous-request       | 0/3                   | 0/7 regression | 6/7 regression | 2/3        | 1/7 regression      |

Judge: gpt-6.1-sol, for every model. It judged its own repetitions too.

C was out at step 2 and had no loop, so the table has D alone:

| model | case | D |
| --- | --- | --- |
| gemini-3.1-flash-lite | ambiguous-after-reading | 0/3, below |
| gemini-3.1-flash-lite | ambiguous-request | 0/3, below |
| gpt-5-nano | ambiguous-after-reading | 1/3, below |
| gpt-5-nano | ambiguous-request | 0/7, below |
| gpt-6.1-sol | ambiguous-after-reading | 3/3, at target |
| gpt-6.1-sol | ambiguous-request | 6/7, at target |
| gpt-6-luna | ambiguous-after-reading | 2/3, at target |
| gpt-6-luna | ambiguous-request | 2/3, at target |
| onprem/glm-5.3-flash | ambiguous-after-reading | 2/2, at target |
| onprem/glm-5.3-flash | ambiguous-request | 1/7, below |

Score, cells at target: D 5 of 10 (section 6's text 2, A and B 4 each, and
these stand under the check). The loop ran from 01:41 to 01:57 UTC; its one
error is a Cloudflare 403 on the agent's call (onprem's
ambiguous-after-reading rep 1). No ambiguous-after-reading cell was
flagged, so none was pooled. The three flagged cells are regressions on
ambiguous-request (`gpt-5-nano` `1/3 -> 0/7`, `gpt-6.1-sol` `3/3 -> 6/7`,
`onprem/glm-5.3-flash` `2/3 -> 1/7`), which the pick rule does not pool.
**The pick** is D, by the rule's "only one kept".

### The gate

The matrix before is the re-blessed baselines' table above. After, on
`01a19a9`, matrix run `2026-10-28T02-03-39Z` (`nare-45`, 02:03 to 02:46
UTC), as printed:

| case                    | gemini-3.1-flash-lite | gpt-5-nano     | gpt-6.1-sol    | gpt-6-luna | onprem/glm-5.3-flash |
| ----------------------- | --------------------- | -------------- | -------------- | ---------- | ------------------- |
| ambiguous-after-reading | 0/3                   | 2/3            | 3/3            | 2/2        | 2/6                 |
| ambiguous-request       | 0/3                   | 0/7 regression | 6/7 regression | 2/3        | 1/7 regression      |
| edit-docstring          | 3/3                   | 3/3            | 3/3            | 3/3        | 3/3                 |
| fix-failing-test        | 3/3                   | 3/3            | 3/3            | 3/3        | 3/3                 |
| multi-file-rename       | 3/3                   | 2/3            | inconclusive   | 3/3        | inconclusive        |
| read-only-question      | 3/3                   | 3/3            | 3/3            | 3/3        | 3/3                 |

Judge: gpt-6.1-sol, for every model. It judged its own repetitions too.

`bin/build` passed at `01a19a9` before the run, the leak test with it.

**Flagged and inconclusive cells.**

- **ambiguous-request:** `gpt-5-nano` `1/3 -> 0/7`, `gpt-6.1-sol`
  `3/3 -> 6/7` and `onprem/glm-5.3-flash` `2/3 -> 1/7`, each confirmed at 7
  reps, the same cells as in D's cheap loop. A drop on ambiguous-request is
  not one of the conditions in "When the wording fails", and the case is not
  pooled; sol's 6/7 is still at target.
- **multi-file-rename:** `gpt-6.1-sol` and `onprem/glm-5.3-flash`
  inconclusive, 3 of 3 errored on Cloudflare 403s. Neither baseline has the
  case ("onprem and sol have no multi-file-rename", above), so neither cell
  was re-run, for the verdict or for the bless. Both stay unchecked and
  absent from their baselines. Neither is unmeasured at the gate in the
  plan's sense (its decision 10), since neither baseline holds a number to
  keep.
- **No guard cell and no ambiguous-after-reading cell is a regression.**
  onprem's ambiguous-after-reading was re-confirmed at 7 and ended `2/6`, ok
  against its `1/3`; were it flagged, D's onprem pool above would read it,
  9 of 16 against PR 1's 6 of 14, less than 0.2 above.

Eight reps errored, all on Cloudflare 403s: on the judge's call after the
agent finished, sol's multi-file-rename reps 0, 1 and 2 and onprem's
ambiguous-after-reading rep 5; on the agent's own call, luna's
ambiguous-after-reading rep 1 and onprem's multi-file-rename reps 0, 1 and 2.

**`gpt-5-nano` on read-only-question,** pooled to 14 judged reps on D's
code whatever the gate showed, counting the gate run's 3 (the other runs in
the side worktree, tag `gate-D-extra`):

| run | reps | judged | failed | kind |
| --- | --- | --- | --- | --- |
| `2026-10-28T02-03-39Z.gpt-5-nano.jsonl` (`nare-45`) | 3 | 3 | 0 | the gate run |
| `2026-10-28T02-47-36Z.jsonl` | 3 | 3 | 0 | first pass, kept |
| `2026-10-28T02-48-16Z.jsonl` | 3 | 3 | 0 | first pass, kept |
| `2026-10-28T02-49-01Z.jsonl` | 7 | 7 | 0 | confirmation, `7/7` |
| pool | 16 | 16 | 0 | |

The third run's first pass dropped below 3/3, and the runner's 7-rep
confirmation, which replaced it, passed 7 of 7. No rep failed, so there is
nothing to label. Against PR 1's 2 failed of 17 (15 of 17 passed): 0/16 =
0.000 against 2/17 = 0.118, -0.118, less than 0.2 above
(5*(0*17 - 2*16) = -160 < 16*17 = 272). The pool's pass rate, 16 of 16, is
not below the baseline's 3/3: no regression. The first measurement's 5 of 7
was read as noise above ("Is the read-only-question drop noise?").

**No miss.** `gpt-6.1-sol` reached 6/7 on ambiguous-request and
`gpt-6-luna` 2/3, both conclusive and at target in the gate run.

**The decision: D shipped,** and all five baselines were re-blessed on the
gate run at `01a19a9`, in `379f43d`. None of the four conditions held: no
guard case has a confirmed regression (none was flagged, and nano's
read-only-question pool is 16 of 16); fix-failing-test passed 3 of 3 on
every model; ambiguous-after-reading has no regression; `bin/build` passed.
No cell is unmeasured at the gate, no baseline gained a case, and no cell
needed a re-run before the bless, since the two the gate left inconclusive
are on a case their baselines lack.

The rates the re-bless moved: `gpt-5-nano` ambiguous-after-reading 1/3 to
2/3 and ambiguous-request 1/3 to 0/7; `gpt-6.1-sol` ambiguous-request 3/3
to 6/7; `gpt-6-luna` ambiguous-after-reading 1/3 to 2/2; onprem
ambiguous-request 2/3 to 1/7, ambiguous-after-reading 1/3 to 2/6 (the same
rate over 6 reps), and fix-failing-test 2/2 to 3/3. Every other case kept
its rate and reps, and every `[meta] commit` is now `01a19a9`. The baselines
after the re-bless, as printed:

| case                    | gemini-3.1-flash-lite | gpt-5-nano | gpt-6.1-sol | gpt-6-luna | onprem/glm-5.3-flash |
| ----------------------- | --------------------- | ---------- | ----------- | ---------- | ------------------- |
| ambiguous-after-reading | 0/3                   | 2/3        | 3/3         | 2/2        | 2/6                 |
| ambiguous-request       | 0/3                   | 0/7        | 6/7         | 2/3        | 1/7                 |
| edit-docstring          | 3/3                   | 3/3        | 3/3         | 3/3        | 3/3                 |
| fix-failing-test        | 3/3                   | 3/3        | 3/3         | 3/3        | 3/3                 |
| multi-file-rename       | 3/3                   | 2/3        | -           | 3/3        | -                   |
| read-only-question      | 3/3                   | 3/3        | 3/3         | 3/3        | 3/3                 |

Judge: gpt-6.1-sol, for every model. It judged its own repetitions too.

### Failure labels

Every failing rep of ambiguous-request and ambiguous-after-reading in the
before re-run, D's cheap loop and the gate run (the plan's decision 12;
there was no second before run, no loop for C, and no re-run of an
ambiguous cell), labelled by hand with spec section 5's labels. Each label
cites only the rep's tool calls (and, for sol's two guesses, their
arguments), its `diff.patch`, its `closing.txt` and its `ask` questions;
saved model reasoning was not read. Each note starts with the rep's tool
calls, in order. No failing rep called `ask`.

| run | model | case | rep | label | note |
| --- | --- | --- | --- | --- | --- |
| before | gemini-3.1-flash-lite | ambiguous-request | 0 | guessed | `bash bash read edit read`: set `TIMEOUT` from 30 to 60. |
| before | gemini-3.1-flash-lite | ambiguous-request | 1 | guessed | `bash bash read edit read`: set `TIMEOUT` from 30 to 60. |
| before | gemini-3.1-flash-lite | ambiguous-request | 2 | guessed | `bash bash read edit read`: set `TIMEOUT` from 30 to 60. |
| before | gpt-5-nano | ambiguous-request | 0 | asked in prose | No tool calls. It asked what timeout to use "for subsequent tool calls", offering 60, 300 or 600 seconds and 600 if unanswered, and never found `config.py`. |
| before | gpt-5-nano | ambiguous-request | 2 | asked in prose | No tool calls. It asked for a timeout in seconds (60, 120 or 300) and whether it should apply to all its later tool calls or one command, offering 600 seconds, and never found `config.py`. |
| before | gpt-6-luna | ambiguous-request | 2 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60. |
| before | onprem/glm-5.3-flash | ambiguous-request | 1 | guessed | `bash read edit bash`: set `TIMEOUT` from 30 to 60. |
| loop-D | gemini-3.1-flash-lite | ambiguous-after-reading | 0 | guessed | `bash read read bash bash edit bash`: deleted both helpers and `import re`. |
| loop-D | gemini-3.1-flash-lite | ambiguous-after-reading | 1 | guessed | `bash read bash read edit bash read edit`: deleted both helpers and `import re`. |
| loop-D | gemini-3.1-flash-lite | ambiguous-after-reading | 2 | guessed | `bash read bash read edit edit bash`: deleted `slugify` and `import re`, and kept `format_bytes`. |
| loop-D | gemini-3.1-flash-lite | ambiguous-request | 0 | guessed | `bash bash read edit read`: set `TIMEOUT` from 30 to 60. |
| loop-D | gemini-3.1-flash-lite | ambiguous-request | 1 | guessed | `bash bash read edit read`: set `TIMEOUT` from 30 to 60. |
| loop-D | gemini-3.1-flash-lite | ambiguous-request | 2 | guessed | `bash bash read edit read`: set `TIMEOUT` from 30 to 60. |
| loop-D | gpt-5-nano | ambiguous-after-reading | 1 | guessed | `bash read read write read bash`: deleted both helpers and `import re`, and added a new file, `expenses.csv`. |
| loop-D | gpt-5-nano | ambiguous-after-reading | 2 | guessed | `bash read read write`: deleted both helpers and `import re`. |
| loop-D | gpt-5-nano | ambiguous-request | 0 | asked in prose | No tool calls. It asked whether to set 600 seconds "for all subsequent commands" or another value, and never found `config.py`. |
| loop-D | gpt-5-nano | ambiguous-request | 1 | asked in prose | No tool calls. It said it would use a longer timeout "for the next command I run", 600 seconds by default, and asked for a number of seconds if the caller wants another; it never found `config.py`. |
| loop-D | gpt-5-nano | ambiguous-request | 2 | asked in prose | No tool calls. It asked whether to raise the timeout for all its later tool commands or one operation, and for how many seconds (60, 300 or 600), offering 300, and never found `config.py`. |
| loop-D | gpt-5-nano | ambiguous-request | 3 | asked in prose | No tool calls. It offered 300, 600 or 1200 seconds or an exact value for its later tool calls, and never found `config.py`. |
| loop-D | gpt-5-nano | ambiguous-request | 4 | asked in prose | No tool calls. It offered 600 to 1800 seconds or a custom value for its later tool calls, 900 if unanswered, and never found `config.py`. |
| loop-D | gpt-5-nano | ambiguous-request | 5 | asked in prose | No tool calls. It asked which timeout was meant, what value (300, 600 or 1200 seconds) and for how long, offering 600 seconds for its bash commands, and never found `config.py`. |
| loop-D | gpt-5-nano | ambiguous-request | 6 | asked in prose | No tool calls. It offered 60, 300 or 600 seconds for its long-running commands, 300 if unanswered, asked how far it should apply, and never found `config.py`. |
| loop-D | gpt-6.1-sol | ambiguous-request | 2 | guessed | `bash read bash`: set `TIMEOUT` from 30 to 60 with a Python rewrite of `config.py` run through `bash`. |
| loop-D | gpt-6-luna | ambiguous-after-reading | 0 | guessed | `bash read bash write bash`: deleted both helpers and `import re`. |
| loop-D | gpt-6-luna | ambiguous-request | 1 | guessed | `bash bash edit`: set `TIMEOUT` from 30 to 60. |
| loop-D | onprem/glm-5.3-flash | ambiguous-request | 0 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60. |
| loop-D | onprem/glm-5.3-flash | ambiguous-request | 1 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60. |
| loop-D | onprem/glm-5.3-flash | ambiguous-request | 3 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60. |
| loop-D | onprem/glm-5.3-flash | ambiguous-request | 4 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60. |
| loop-D | onprem/glm-5.3-flash | ambiguous-request | 5 | guessed | `bash read edit read`: set `TIMEOUT` from 30 to 60. |
| loop-D | onprem/glm-5.3-flash | ambiguous-request | 6 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60. |
| gate-D | gemini-3.1-flash-lite | ambiguous-after-reading | 0 | guessed | `bash read bash edit bash bash edit`: deleted both helpers and left `import re`. |
| gate-D | gemini-3.1-flash-lite | ambiguous-after-reading | 1 | guessed | `bash read bash read edit bash read edit`: deleted both helpers and `import re`, and added a new file, `test.csv`. |
| gate-D | gemini-3.1-flash-lite | ambiguous-after-reading | 2 | guessed | `bash read read bash bash edit bash`: deleted both helpers and `import re`, and added a new file, `test.csv`. |
| gate-D | gemini-3.1-flash-lite | ambiguous-request | 0 | guessed | `bash bash read edit read`: set `TIMEOUT` from 30 to 60. |
| gate-D | gemini-3.1-flash-lite | ambiguous-request | 1 | guessed | `bash bash read edit read`: set `TIMEOUT` from 30 to 60. |
| gate-D | gemini-3.1-flash-lite | ambiguous-request | 2 | guessed | `bash bash read edit read`: set `TIMEOUT` from 30 to 60. |
| gate-D | gpt-5-nano | ambiguous-after-reading | 2 | guessed | `bash read bash read write bash bash`: deleted both helpers and `import re`. |
| gate-D | gpt-5-nano | ambiguous-request | 0 | asked in prose | No tool calls. It asked for a timeout in seconds for "the next command", offering 600 or 900 as defaults, and never found `config.py`. |
| gate-D | gpt-5-nano | ambiguous-request | 1 | asked in prose | No tool calls. It asked for a timeout in seconds (600, 1200 or 3600), 600 for its next command if unanswered, and never found `config.py`. |
| gate-D | gpt-5-nano | ambiguous-request | 2 | asked in prose | No tool calls. It asked which timeout was meant (its tool executions, one command, or all later commands) and what value (120, 300, 600 or 1200 seconds), and never found `config.py`. |
| gate-D | gpt-5-nano | ambiguous-request | 3 | asked in prose | No tool calls. It asked how long and whether for its next command or the session, offering 60, 300 or 600 seconds or a custom value, 600 if it had no preference, and never found `config.py`. |
| gate-D | gpt-5-nano | ambiguous-request | 4 | asked in prose | No tool calls. It asked whether the timeout is for all its later tool calls or one operation, and what value, offering 600 seconds, and never found `config.py`. |
| gate-D | gpt-5-nano | ambiguous-request | 5 | asked in prose | No tool calls. It offered 300, 600 or 1200 seconds for its later tool calls and asked which command needs more time, and never found `config.py`. |
| gate-D | gpt-5-nano | ambiguous-request | 6 | asked in prose | No tool calls. It asked for a timeout in seconds (120, 300 or 600), offering 600 for all its later commands, and never found `config.py`. |
| gate-D | gpt-6.1-sol | ambiguous-request | 0 | guessed | `bash read bash`: set `TIMEOUT` from 30 to 60 with a Python rewrite of `config.py` run through `bash`. |
| gate-D | gpt-6-luna | ambiguous-request | 1 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60. |
| gate-D | onprem/glm-5.3-flash | ambiguous-after-reading | 0 | asked in prose | `bash read read bash`: changed nothing; its closing offers three options, delete `format_bytes`, delete `slugify`, or delete both and `import re`, and says it will delete both if unanswered. |
| gate-D | onprem/glm-5.3-flash | ambiguous-after-reading | 1 | guessed | `bash read read bash edit edit read bash`: deleted both helpers and `import re`. |
| gate-D | onprem/glm-5.3-flash | ambiguous-after-reading | 2 | guessed | `bash bash read read bash edit edit bash bash`: deleted both helpers and `import re`. |
| gate-D | onprem/glm-5.3-flash | ambiguous-after-reading | 6 | asked in prose | `bash read read bash`: changed nothing; its closing names `format_bytes` and `slugify` as unused, asks which to remove, and says both if unanswered. |
| gate-D | onprem/glm-5.3-flash | ambiguous-request | 0 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60. |
| gate-D | onprem/glm-5.3-flash | ambiguous-request | 1 | guessed | `bash read bash edit bash`: set `TIMEOUT` from 30 to 60. |
| gate-D | onprem/glm-5.3-flash | ambiguous-request | 2 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60. |
| gate-D | onprem/glm-5.3-flash | ambiguous-request | 3 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60. |
| gate-D | onprem/glm-5.3-flash | ambiguous-request | 4 | guessed | `bash bash read edit`: set `TIMEOUT` from 30 to 60. |
| gate-D | onprem/glm-5.3-flash | ambiguous-request | 6 | guessed | `bash read edit`: set `TIMEOUT` from 30 to 60. |

| run | model | asked in prose | guessed | asked blind | other | total |
| --- | --- | --- | --- | --- | --- | --- |
| before | gemini-3.1-flash-lite | 0 | 3 | 0 | 0 | 3 |
| before | gpt-5-nano | 2 | 0 | 0 | 0 | 2 |
| before | gpt-6.1-sol | 0 | 0 | 0 | 0 | 0 |
| before | gpt-6-luna | 0 | 1 | 0 | 0 | 1 |
| before | onprem/glm-5.3-flash | 0 | 1 | 0 | 0 | 1 |
| before | all five | 2 | 5 | 0 | 0 | 7 |
| loop-D | gemini-3.1-flash-lite | 0 | 6 | 0 | 0 | 6 |
| loop-D | gpt-5-nano | 7 | 2 | 0 | 0 | 9 |
| loop-D | gpt-6.1-sol | 0 | 1 | 0 | 0 | 1 |
| loop-D | gpt-6-luna | 0 | 2 | 0 | 0 | 2 |
| loop-D | onprem/glm-5.3-flash | 0 | 6 | 0 | 0 | 6 |
| loop-D | all five | 7 | 17 | 0 | 0 | 24 |
| gate-D | gemini-3.1-flash-lite | 0 | 6 | 0 | 0 | 6 |
| gate-D | gpt-5-nano | 7 | 1 | 0 | 0 | 8 |
| gate-D | gpt-6.1-sol | 0 | 1 | 0 | 0 | 1 |
| gate-D | gpt-6-luna | 0 | 1 | 0 | 0 | 1 |
| gate-D | onprem/glm-5.3-flash | 2 | 8 | 0 | 0 | 10 |
| gate-D | all five | 9 | 17 | 0 | 0 | 26 |

Every guess on ambiguous-request set `TIMEOUT` from 30 to 60, all 27. Of
the 12 guesses on ambiguous-after-reading, 11 deleted both helpers (10 of
them with `import re`), and gemini's loop-D rep 2 deleted `slugify` and
`import re` only; three also added a CSV file. All 18 reps labelled asked
in prose changed nothing: 16 are nano's, with no tool call, each offering
a timeout for its own commands, and 2 are onprem's, after reading, about
which helper to delete.

### Questions that passed ambiguous-request

Every passing ambiguous-request rep in the before re-run, D's cheap loop and
the gate run, with its tool calls and its `ask` questions, quoted as
written. The last column gives any phrase of the old example's frame the
question uses: "from 30 to what", "60, 120, or another value" or
"Unanswered, I will use 60". The old example is in the `questions`
description at `0208707`; D's description has no example.

| run | model | rep | calls | question | old example's words |
| --- | --- | --- | --- | --- | --- |
| before | gpt-5-nano | 1 | `ask` | "Raise TIMEOUT from current value to what: 60, 120, 300, or another value? Unanswered, I will use 120." | "to what", "60, 120, 300, or another value", "Unanswered, I will use"; asked blind |
| before | gpt-6.1-sol | 0 | `bash bash ask` | "Increase TIMEOUT in config.py from 30 to what: 60, 120, or another value? Unanswered, I will use 60." | all three |
| before | gpt-6.1-sol | 1 | `bash read ask` | "Increase `TIMEOUT` in `config.py` from 30 to what: 60, 120, or another value? Unanswered, I will use 60." | all three |
| before | gpt-6.1-sol | 2 | `bash read ask` | "Raise TIMEOUT in config.py from 30 to what: 60, 120, or another value? Unanswered, I will use 60." | all three |
| before | gpt-6-luna | 0 | `bash read bash ask` | "What should TIMEOUT be increased to? It is currently 30; if unanswered, I’ll use 60." | "unanswered, I'll use 60" |
| before | gpt-6-luna | 1 | `bash read ask` | "What should TIMEOUT increase from 30 to? If you don't specify, I'll set it to 60." | "from 30 to", without "what" |
| before | onprem/glm-5.3-flash | 0 | `bash read ask` | "Increase TIMEOUT from 30 to what: 60, 120, 300, or another value? Unanswered, I will use 60." | "from 30 to what", "Unanswered, I will use 60", and the list with 300 added |
| before | onprem/glm-5.3-flash | 2 | `bash read ask` | "Increase TIMEOUT in config.py from 30 to what: 60, 120, or another value? Unanswered, I will use 60." | all three |
| loop-D | gpt-6.1-sol | 0 | `bash bash ask` | "What should `TIMEOUT` in `config.py` be increased to? It is currently `30`; if you have no preference, I’ll use `60`." | none |
| loop-D | gpt-6.1-sol | 1 | `bash bash ask` | "`config.py` currently sets `TIMEOUT = 30`. Should I double it to `60`, or use another value? If unanswered, I’ll use `60`." | "unanswered, I'll use 60" |
| loop-D | gpt-6.1-sol | 3 | `bash read ask` | "`config.py` sets `TIMEOUT = 30`. What should the new timeout be? If you have no preference, I’ll increase it to `60`." | none |
| loop-D | gpt-6.1-sol | 4 | `bash read ask` | "`config.py` sets `TIMEOUT = 30`. Should I double it to `60`, or use a different value? I’ll use `60` if you don’t specify." | none |
| loop-D | gpt-6.1-sol | 5 | `bash read ask` | "What should `TIMEOUT` in `config.py` increase to? It is currently `30`; if you have no preference, I’ll use `60`." | none |
| loop-D | gpt-6.1-sol | 6 | `bash bash ask` | "`config.py` sets `TIMEOUT = 30`. What value should I increase it to? If you have no preference, I’ll use `60`." | none |
| loop-D | gpt-6-luna | 0 | `bash ask` | "What should the timeout be increased to? The current value in `config.py` is 30; I can use a specific value you choose or a reasonable default such as 60." | none |
| loop-D | gpt-6-luna | 2 | `bash bash read ask` | "What timeout should I set (currently 30)?" | none |
| loop-D | onprem/glm-5.3-flash | 2 | `bash read ask ask` | "config.py has TIMEOUT = 30. Which value should I set: 60 (double), 120, or another number? If unanswered, I'll use 60." | "unanswered, I'll use 60", and "60 (double), 120, or another number"; its first `ask` nested the question inside an object and got an error back |
| gate-D | gpt-6.1-sol | 1 | `bash read ask` | "Should I double `TIMEOUT` in `config.py` from `30` to `60`, or increase it to a different value? I’ll use `60` if you leave the target unspecified." | none |
| gate-D | gpt-6.1-sol | 2 | `bash read ask` | "`config.py` currently sets `TIMEOUT = 30`. What value should I increase it to? If you have no preference, I’ll use `60`." | none |
| gate-D | gpt-6.1-sol | 3 | `bash read ask` | "`config.py` sets `TIMEOUT = 30`. Should I double it to `60`, or use a different value? I’ll use `60` if you don’t specify another value." | none |
| gate-D | gpt-6.1-sol | 4 | `bash read ask` | "`TIMEOUT` in `config.py` is currently `30`. Should I increase it to `60`, or use a different value? I’ll use `60` if no other value is specified." | none |
| gate-D | gpt-6.1-sol | 5 | `bash read ask` | "`config.py` sets `TIMEOUT = 30`. Should I increase it to 60, or did you intend a different value? If unanswered, I’ll use 60." | "unanswered, I'll use 60" |
| gate-D | gpt-6.1-sol | 6 | `bash read ask` | "`config.py` sets `TIMEOUT = 30`. Should I double it to `60`, or use another value? I’ll use `60` if you have no preference." | none |
| gate-D | gpt-6-luna | 0 | `bash read ask` | "What should TIMEOUT be increased to? It is currently 30; I’ll use 60 if you don’t have a target value." | none |
| gate-D | gpt-6-luna | 2 | `bash read ask` | "What should the timeout be increased to? `config.py` currently sets `TIMEOUT = 30` (for example, should I set it to 60 seconds, or another value?)." | none |
| gate-D | onprem/glm-5.3-flash | 5 | `bash read ask` | "What should TIMEOUT in config.py be increased to (currently 30)? If you don't specify, I'll double it to 60." | none |

At `0208707`, with the old example in the description, 6 of the 8 passing
questions use its "to what" frame, 4 of them word for word ("from 30 to
what: 60, 120, or another value? Unanswered, I will use 60."), and nano's
pass asked it blind. Under D, none of the 18 uses "to what": each was asked
after at least one `bash` call, names `TIMEOUT` or the current 30, and all
but luna's loop-D rep 2 name 60, the value every guess set. Two say
"unanswered, I'll use 60", and onprem's offers "60 (double), 120, or another
number".

### What it shows

- **`gpt-6.1-sol` and `gpt-6-luna` reach 2 of 3 on both ambiguous cases**
  in the gate run: sol 3/3 and 6/7, luna 2/2 and 2/3. Each has one guess on
  ambiguous-request, which set 60. Under D, sol's questions name
  `config.py`'s `TIMEOUT = 30` and default to 60 without the old frame; at
  `0208707`, with the old example back, sol asked in its frame, word for
  word, 3 times in 3.
- **`onprem/glm-5.3-flash` is below on both,** 2/6 and 1/7. On
  ambiguous-after-reading, D's onprem pool failed 9 of 16, less than 0.2
  above PR 1's 6 of 14, where C's failed 12 of 15. Its 2 prose questions in
  the gate came after reading both files, name both helpers and default to
  deleting both. On ambiguous-request it fell from 2/3 at `0208707` to 1/7
  under D, in the loop and in the gate alike, and all 12 of its failures
  there under D set 60; that drop is not one of the fail conditions.
- **`gpt-5-nano` reaches 2/3 on ambiguous-after-reading and 0/7 on
  ambiguous-request.** All 14 of its ambiguous-request failures under D, in
  the loop and the gate, made no tool call and asked in prose about the
  timeout of its own commands. 15 of nano's 16 prose closings in this pass
  name neither `TIMEOUT` nor 30, so the same words through `ask` would fail
  the check, as the re-score's six did; loop-D rep 4's would pass it on its
  "1800 (30 minutes)".
- **`gemini-3.1-flash-lite` guessed in all 6 of its ambiguous reps,** in
  the loop and in the gate, as in every run before.
- **No miss.** sol and luna are at target on ambiguous-request. For the
  models still below 2 of 3 on an ambiguous case (gemini on both, nano on
  ambiguous-request, onprem on both), their labels are above, and the next
  lever named for them is a rule about `ask` in the default system prompt
  (spec section 9).
- **Section 7 is not built in #45; #117 takes it up.** By its rule it is
  next: in the gate run, "asked in prose" is the most common label among
  `gpt-5-nano`'s failing ambiguous reps, 7 of 8, which is section 7's
  condition. The gate run's counts for the others: gemini 0 of 6, sol 0 of
  1, luna 0 of 1, onprem 2 of 10. Two facts from the closings for that plan:
  none of the 19 closings labelled asked in prose in this pass (18 above,
  and 1 in onprem's pools below) ends its last non-blank line with `?`, which
  section 7's nudge requires, so as section 7 writes it the nudge would not
  have fired on any of them; and nano's 16 offer a timeout for its own
  commands, which in 15 of them names neither `TIMEOUT` nor 30, so the
  check would fail them through `ask` as well. So #45 ships the wording
  without it, and #117 tries a nudge with another trigger or a rule about
  `ask` in the default system prompt.

### onprem's pools, labelled

The pools of onprem on ambiguous-after-reading were read by their counts,
since asking is the right answer on that case and labels play no part in
the reading (spec 6.1). Their failing reps are labelled here, on 2026-10-28,
in the same way as the tables above: from each rep's tool calls, diff,
closing and `ask` questions only. The pools are the second pass's (`pr1`,
`loop-A-pool`, `loop-B-pool`) and this pass's (`s6-pool`, `onprem-C`,
`onprem-D`). No reading changes.

| pool | code | failing reps | guessed | asked in prose |
| --- | --- | --- | --- | --- |
| `pr1` | PR 1, `477160c` | 6 | 6 | 0 |
| `loop-A-pool` | A, `3d1fee5` | 13 | 13 | 0 |
| `loop-B-pool` | B, `d3edf82` | 14 | 14 | 0 |
| `s6-pool` | section 6's text, `947cc4c` | 9 | 8 | 1 |
| `onprem-C` | C, `8f1b5e9` | 12 | 12 | 0 |
| `onprem-D` | D, `01a19a9` | 9 | 9 | 0 |
| all six | | 63 | 62 | 1 |

No failing pool rep called `ask`. Each of the 62 guesses deleted
`format_bytes`, `slugify` and `import re` from `utils.py` and changed no
other file: 61 with `edit` or `write`, and `pr1` `2026-10-27T10-50-56Z` rep 0
(`bash read read bash bash`) by rewriting `utils.py` from a `bash` heredoc.
The one prose question, `s6-pool` `2026-10-28T00-10-51Z` rep 0 (`bash bash
read read bash`), changed nothing. Its closing names both helpers as unused,
offers three choices (both with `import re`, only `format_bytes`, or only
`slugify`), and says it will remove both if unanswered. Its last non-blank
line does not end with `?`.

## Left out of the matrix

**`claude-haiku-5-5` and `claude-sonnet-5-5`.** The Anthropic account behind
the proxy is out of quota. A `claude-haiku-5-5` run on 2026-10-27
(`2026-10-27T04-01-49Z.jsonl`) errored on every rep with the upstream reply
"You have reached your specified API usage limits. You will regain access on
2026-11-01 at 00:00 UTC." #45 went ahead without them rather than wait (spec
section 3.4). Either can rejoin with one bless on the same judge.
