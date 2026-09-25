# nare - Ask Only When the Request Is Ambiguous

**Date:** 2026-09-24
**Status:** Draft for review
**Scope:** Changes the `ask` tool's description, when the loop reports
`blocked`, and the machine contract's version (1 to 2). Lands after
`2026-09-24-bench-fixes-design.md`, whose re-blessed baseline is what this
change is measured against.

## 1. The problem

On `ada/qwen3-14b`, 9 of 16 benchmark failures across three smoke runs were the
agent calling `ask` on a request that had one reading:

| Case | Prompt | What the agent did |
| --- | --- | --- |
| edit-docstring | "Add a docstring to foo() in bar.py." | Asked on turn 1, before reading `bar.py`. 6 of 9 reps. |
| multi-file-rename | "Rename the function calc_area to rectangle_area everywhere it appears, and make sure the test suite still passes." | Did the rename, the tests passed, and asked in the same turn (2 reps). Explored for four turns, changed nothing, asked (1 rep). |

Meanwhile ambiguous-request ("Increase the timeout.", against a fixture that
holds `TIMEOUT = 30`) asked correctly in 9 of 9 reps.

Two causes, both in nare:

- **The description does not say when not to ask.** "Use this when the task
  cannot be completed without a decision only the caller can make" leaves a
  small model to decide that the wording of a docstring is such a decision.
  nare sends no system prompt of its own, so the description is the only
  guidance the model gets.
- **The loop honours an `ask` whatever else the turn did.** `step()` sets
  `blocked` if any call in the turn is `ask`. A turn that renames a function,
  passes the tests and also asks ends `blocked`, and the caller is told the
  work is waiting on it when it is finished.

A third, found reading the same line: `blocked` is set even when the policy
does not allow `ask`. `dispatch` refuses the call with an error result, and
the session still ends `blocked`, with questions the caller never allowed the
model to ask.

## 2. The rule

**`ask` is for a request that can reasonably be read more than one way, where
the readings lead to different changes.** Everything else the model decides
or finds out for itself.

| Request | Ambiguous? | Why |
| --- | --- | --- |
| "Increase the timeout." | Yes | By how much has no conventional answer, and 60 and 300 are different changes. |
| "Add a docstring to foo() in bar.py." | No | One reading. The wording is the model's call. |
| "Rename calc_area to rectangle_area everywhere." | No | One reading. Finding "everywhere" is `rg`'s job. |

What the rule excludes, named because a small model reaches for each:

- facts it can find by reading files or running commands
- details it can decide: wording, style, names, where a helper goes
- permission or confirmation for work the request already asked for
- offers of follow-up work ("should I also run the tests?")

## 3. The description

The `ask` schema becomes:

```python
{
    "name": "ask",
    "description": (
        "Stop and ask the caller to resolve an ambiguous request. Use this "
        "only when the request can reasonably be read more than one way and "
        "the readings lead to different changes, and only after reading the "
        "files involved. Do not use it for anything you can find out by "
        "reading files or running commands, for details you can decide "
        "yourself such as wording, style or names, to ask permission, or to "
        "offer follow-up work. Call ask on its own, before making any "
        "change. This ends the session."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "One question per item, naming the readings you are "
                    "choosing between, e.g. 'Raise TIMEOUT from 30 to what: "
                    "60, 120, or another value?'"
                ),
            }
        },
        "required": ["questions"],
    },
}
```

Naming the readings does two jobs. It is a self-test: a model that cannot
name two readings has found no ambiguity. And it gives the caller a question
it can answer without opening the repository.

The input shape is unchanged: `questions` is still a list of strings, and
`questions_from` is untouched.

### Why the description and not a system prompt

The rule is about `ask`, and the description reaches every caller, including
one that passes its own `--system`. A default system prompt would also force
a decision about whether `--system` appends to it or replaces it, and it
would be nare's first persona text, which sits badly with keeping nare
caller-agnostic (#13). If edit-docstring still asks on turn 1 after this
change, a system prompt is the next lever, and the benchmark will have shown
it is needed.

## 4. The loop

`step()` decides `blocked` from what actually happened, not from what the
model called:

- **`ask` alone:** every call in the turn is `ask`, and the policy allows
  `ask`. Status `blocked`, questions recorded. Unchanged from today apart
  from the policy condition.
- **`ask` with other calls:** the other calls dispatch as normal. Each `ask`
  call gets an error result instead of being dispatched:

  > ask was not recorded: call ask on its own, before making any change. The
  > other calls in this turn ran.

  The status stays `working` and the loop takes another turn. The model sees
  its results and either finishes or asks again, on its own.
- **`ask` not allowed by the policy:** `dispatch` already refuses it with an
  error result. The status stays `working`.

The rejected `ask` still appears as a `tool_use` event, and its result is in
the transcript, so a reader can see what the model tried. It costs a turn
from the budget like any other.

Only same-turn asks are rejected. An `ask` on its own in a later turn, after
changes, is honoured: the model may have discovered the ambiguity while
working, and nothing here can tell that apart from a follow-up offer.

## 5. The contract

`docs/contract.md` states that "changing when a status is reported" bumps the
version. A turn with `ask` and other calls ends `blocked` under contract 1 and
keeps working under this change, so `CONTRACT_VERSION` becomes 2.

- `docs/contract.md` says "This is contract version 2", and the `blocked`
  row reads: "The model used `ask` on its own, in a session whose policy
  allows it. `questions` carries what it needs." A short "Changes in
  version 2" section says what changed and why.
- A caller running `--contract 1` is refused at start, with both numbers
  named. That is the mechanism working: the caller learns about the change
  on its first run instead of mis-reading one.
- **A session written under contract 1 cannot be resumed.** nare already
  refuses to resume a session from another contract version. A caller
  holding a `blocked` v1 session when it upgrades has to start that task
  again. The release notes say so.

No field, event type or exit code changes.

## 6. Testing

In `tests/`, with `FakeProvider`:

- `test_loop.py`:
  - `ask` alone blocks with its questions (the existing test, kept).
  - Two `ask` calls alone block with both sets of questions.
  - `ask` plus `edit`: the edit is applied, the `ask` result is an error
    naming the rule, the status is `working`, and a following text reply
    ends `done`.
  - `ask` under a policy that excludes it: the status is not `blocked`.
- `test_contract.py`: already asserts against `CONTRACT_VERSION`, and that
  `docs/contract.md` names it, so it covers the bump once both change.
- No test asserts on the description's prose. Whether the wording works is
  the benchmark's question, not a unit test's.

## 7. Measuring it

After `2026-09-24-bench-fixes-design.md` lands and the smoke baseline is
re-blessed, run `bin/bench run --tier smoke` on this change:

| Case | Expected |
| --- | --- |
| edit-docstring | `improved`: from 0 or 1 of 3 to at least 2 of 3 |
| multi-file-rename | `ok` or `improved` |
| ambiguous-request | `ok`: still `blocked`, and its new judge claims met |
| fix-failing-test | `ok`: this change does not touch it |

The description is roughly 100 tokens longer, sent on every turn, so a
token-band note on the short cases is expected and acceptable. The PR states
the before and after table.

If edit-docstring does not improve, that is a finding, not a failure of this
spec: the description is not enough on this model, and a system prompt is
the next thing to measure.

## 8. Out of scope

- **A default system prompt.** Section 3 says when it becomes worth doing.
- **Reporting `done` with a test still failing** (fix-failing-test, 3 reps).
  A separate question about verifying before finishing.
- **Rejecting an `ask` in a later turn after changes.** Section 4 says why.
- **Changing `ask`'s input shape**, for example structured options. A list of
  strings is enough for the rule, and a shape change is a second contract
  change for no measured gain.

## 9. Acceptance criteria

- [ ] The `ask` description and `questions` description are as in section 3
- [ ] A turn with `ask` and other calls runs the other calls, returns an
      error result for `ask`, and keeps the session working
- [ ] A turn of only `ask` calls, under a policy that allows `ask`, blocks
      with every question
- [ ] An `ask` the policy refuses never blocks the session
- [ ] `CONTRACT_VERSION` is 2 and `docs/contract.md` describes version 2
- [ ] A smoke run against the re-blessed baseline shows edit-docstring
      improved and ambiguous-request still passing, reported in the PR
- [ ] `bin/build` passes
