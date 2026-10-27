# Issue 45: the matrix before the `ask` change

**Date:** 2026-10-27
**Baselines blessed at:** `65749a2`, each baseline's `[meta] commit`. Its
`src/nare` is unchanged from `main`.
**Judge:** `gpt-6.1-sol`, for every model.
**Runs:** smoke tier, 3 reps per case, `NARE_STREAM=1`.

These are the "before" numbers of spec section 5
(`docs/superpowers/specs/2026-10-23-ask-across-models-design.md`). Five of
the seven matrix models are blessed so far. The two Claude models are under
"Not yet in the matrix".

## Baseline matrix

From `bin/bench compare --matrix --baselines`, as printed:

| case                    | gemini-3.1-flash-lite | gpt-5-nano | gpt-6.1-sol | gpt-6-luna | spark/glm-5.3-flash |
| ----------------------- | --------------------- | ---------- | ----------- | ---------- | ------------------- |
| ambiguous-after-reading | 0/3                   | 0/3        | 3/3         | 1/3        | 0/3                 |
| ambiguous-request       | 0/3                   | 1/3        | 3/3         | 2/3        | 0/3                 |
| edit-docstring          | 3/3                   | 3/3        | 3/3         | 3/3        | 3/3                 |
| fix-failing-test        | 3/3                   | 3/3        | 3/3         | 3/3        | 2/2                 |
| multi-file-rename       | 3/3                   | 2/3        | -           | 3/3        | -                   |
| read-only-question      | 3/3                   | 3/3        | 3/3         | 3/3        | 3/3                 |

Judge: gpt-6.1-sol, for every model. It judged its own repetitions too.

Each baseline was blessed from one results file, which is not committed:
`gemini-3.1-flash-lite` from `2026-10-27T04-26-45Z.jsonl`, `gpt-5-nano` from
`2026-10-27T04-15-21Z.jsonl`, `gpt-6-luna` from `2026-10-27T04-22-24Z.jsonl`,
`gpt-6.1-sol` from its re-run `2026-10-27T05-08-02Z.jsonl`, and
`spark/glm-5.3-flash` from its first run `2026-10-27T04-30-24Z.jsonl`, which
has 14 judged reps outside multi-file-rename to its re-run's 13.

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
