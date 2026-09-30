# Ask Only When the Request Is Ambiguous Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A session ends `blocked` only when a turn was nothing but `ask` calls that actually succeeded, the `ask` description says when not to ask, and the contract version becomes 2.

**Architecture:** `step()` in `src/nare/loop.py` computes `only_asks` before dispatching. In a mixed turn each allowed `ask` gets a fixed error result instead of being dispatched; `blocked` is set only when `only_asks` holds and no result came back `is_error` (which covers policy refusals, approval refusals and malformed calls in one condition). `CONTRACT_VERSION` goes to 2 with `docs/contract.md` updated to match. The `ask` schema's two descriptions are replaced verbatim from the spec. Then a smoke benchmark run measures it.

**Tech Stack:** Python 3.14, pytest (`asyncio_mode = "auto"`), ruff, mypy strict, `uv`, Docker (bench runs only).

**Spec:** `docs/superpowers/specs/2026-09-24-ask-only-when-ambiguous-design.md`. Issue: #34.

## Global Constraints

- The branch `feat/ask-only-when-ambiguous` is 5 commits behind `origin/main` (bench fixes #35 landed there, with the re-blessed baseline). Rebase onto `origin/main` before any code change. Always compare against `origin/main`, not local `main`.
- `ask`'s input shape is unchanged: `questions` is a list of strings. `questions_from` is untouched.
- No field, event type or exit code changes. No system prompt is added.
- Only same-turn asks are rejected. An `ask` on its own in a later turn is honoured.
- The rejection text is exactly: `ask was not recorded: call ask on its own, before making any change. The other calls in this turn ran.`
- `CONTRACT_VERSION = 2`, declared only in `src/nare/contract.py`.
- No test asserts on the `ask` description's prose.
- Prose we publish (docs, PR body, comments) uses no em dashes.
- `bin/build` (ruff format --check, ruff check, mypy strict, pytest) passes.
- Commit style `type(scope): summary`, for example `feat(loop): ...`.

## Review Focus

1. **`ask` beside another call, in a policy that excludes `ask`.** The model must get the policy refusal ("ask is not allowed in this session"), not "call ask on its own", or it retries `ask` alone and burns a turn on a second refusal. Pinned in Task 1 (`test_a_refused_ask_beside_other_calls_gets_the_policy_refusal`).
2. **An `ask` whose own call fails.** Missing `questions` (a `TypeError` from `ask()`) or an `approve` that says no must leave the session `working`, not `blocked` with no questions. Pinned in Task 1 (`test_an_ask_that_fails_never_blocks`, `test_an_unapproved_ask_never_blocks`).
3. **`ask` alone in a later turn, after changes.** Must still block through `run()`, since the spec deliberately honours it. Pinned in Task 1 (`test_an_ask_alone_after_changes_is_honoured`).
4. **The rejected `ask` stays visible.** It must still emit a `tool_use` event, and every call must get a `tool_result` in call order with matching ids, or a resume is rejected by the vendor. Pinned in Task 1 (`test_an_ask_beside_an_edit_is_rejected_and_the_edit_runs`).
5. **A truncated turn that is only `ask`.** `max_tokens` must still end `error`, not `blocked`: the questions may be cut off. Pinned in Task 1 (`test_a_truncated_ask_is_an_error_not_blocked`).

---

### Task 1: The loop blocks only on a turn of successful asks, and the contract is version 2

**Files:**
- Modify: `tests/fake_provider.py` (add `calls_reply` after `tool_reply`)
- Modify: `src/nare/loop.py` (constant near `_UNFINISHED`; dispatch loop and final status in `step()`)
- Modify: `src/nare/contract.py:23`
- Modify: `docs/contract.md`
- Test: `tests/test_loop.py`, `tests/test_contract.py` (existing, unchanged)

**Interfaces:**
- Consumes: `tool_result(call_id, text, *, is_error=False) -> dict[str, Any]` and `dispatch(call, policy, approve)` from `nare.tools`; `Reply`, `ToolCall` from `nare.transport`.
- Produces: `calls_reply(*calls: tuple[str, dict[str, Any]], stop_reason: StopReason = "tool_use") -> Reply` in `tests/fake_provider.py`, ids `call_1`, `call_2`, ... in order. `ASK_NOT_ALONE: str` in `nare.loop`. `CONTRACT_VERSION == 2`.

- [ ] **Step 1: Rebase onto origin/main**

```bash
git fetch origin
git rebase origin/main
git log --oneline -3
```

Expected: the spec commit sits on top of `3503041` (or whatever `origin/main` now is). `docs/superpowers/specs/2026-09-24-bench-fixes-design.md` exists.

- [ ] **Step 2: Add the multi-call reply helper**

In `tests/fake_provider.py`, after `tool_reply`:

```python
def calls_reply(
    *calls: tuple[str, dict[str, Any]], stop_reason: StopReason = "tool_use"
) -> Reply:
    """One turn carrying several tool calls, with ids call_1, call_2, ..."""
    tool_calls = [
        ToolCall(id=f"call_{n}", name=name, args=args)
        for n, (name, args) in enumerate(calls, start=1)
    ]
    return Reply(
        content=[
            {"type": "tool_use", "id": c.id, "name": c.name, "input": c.args}
            for c in tool_calls
        ],
        tool_calls=tool_calls,
        usage=Usage(input=10, output=5),
        stop_reason=stop_reason,
    )
```

- [ ] **Step 3: Write the failing loop tests**

In `tests/test_loop.py`, change the import line to
`from fake_provider import Exploding, FakeProvider, calls_reply, text_reply, tool_reply`
and add `from nare.loop import ASK_NOT_ALONE, MAX_TURNS_DEFAULT, run, step`. Keep `test_ask_blocks_the_session_with_questions` as it is. Add after it:

```python
async def test_two_asks_alone_block_with_every_question() -> None:
    session = new_session("do the thing")
    fake = FakeProvider(
        [
            calls_reply(
                ("ask", {"questions": ["which file?"]}),
                ("ask", {"questions": ["which value?", "which branch?"]}),
            )
        ]
    )
    await step(session, fake, approve_all, Policy())
    assert session.status == "blocked"
    assert session.questions == ["which file?", "which value?", "which branch?"]


async def test_an_ask_beside_an_edit_is_rejected_and_the_edit_runs(
    tmp_path: Path,
) -> None:
    target = tmp_path / "shape.py"
    target.write_text("def calc_area(): ...\n")
    session = new_session("rename calc_area")
    fake = FakeProvider(
        [
            calls_reply(
                ("edit", {"path": str(target), "old": "calc_area", "new": "area"}),
                ("ask", {"questions": ["should I also run the tests?"]}),
            ),
            text_reply("renamed"),
        ]
    )
    await step(session, fake, approve_all, Policy())

    assert target.read_text() == "def area(): ...\n"
    assert session.status == "working"
    assert session.questions == []
    edit_result, ask_result = session.messages[-1].content
    assert [edit_result["tool_use_id"], ask_result["tool_use_id"]] == [
        "call_1",
        "call_2",
    ]
    assert edit_result["is_error"] is False
    assert ask_result["is_error"] is True
    assert ask_result["content"] == ASK_NOT_ALONE
    # The rejected ask is still visible to a reader of the stream.
    assert [e.text for e in session.events if e.type == "tool_use"] == ["edit", "ask"]

    await step(session, fake, approve_all, Policy())
    assert session.status == "done"


async def test_an_ask_the_policy_refuses_never_blocks() -> None:
    session = new_session("do the thing")
    fake = FakeProvider([tool_reply("ask", {"questions": ["which file?"]})])
    await step(session, fake, approve_all, Policy(tools=frozenset({"read"})))
    assert session.status == "working"
    assert session.questions == []
    assert "not allowed" in session.messages[-1].content[0]["content"]


async def test_a_refused_ask_beside_other_calls_gets_the_policy_refusal(
    tmp_path: Path,
) -> None:
    target = tmp_path / "a.txt"
    target.write_text("hi")
    session = new_session("go")
    fake = FakeProvider(
        [
            calls_reply(
                ("read", {"path": str(target)}),
                ("ask", {"questions": ["which?"]}),
            )
        ]
    )
    await step(session, fake, approve_all, Policy(tools=frozenset({"read"})))
    ask_result = session.messages[-1].content[1]
    assert ask_result["is_error"] is True
    assert "not allowed" in ask_result["content"]
    assert session.status == "working"


async def test_an_ask_that_fails_never_blocks() -> None:
    # No questions argument: ask() raises, dispatch returns an error, and a
    # blocked session with nothing to ask would tell the caller nothing.
    session = new_session("go")
    await step(session, FakeProvider([tool_reply("ask", {})]), approve_all, Policy())
    assert session.status == "working"
    assert session.messages[-1].content[0]["is_error"] is True


async def test_an_unapproved_ask_never_blocks() -> None:
    session = new_session("go")
    fake = FakeProvider([tool_reply("ask", {"questions": ["which?"]})])
    await step(session, fake, lambda tool, args: False, Policy())
    assert session.status == "working"
    assert session.questions == []


async def test_an_ask_alone_after_changes_is_honoured(tmp_path: Path) -> None:
    # A model may find the ambiguity while working; nothing here can tell that
    # apart from a follow-up offer, so a later lone ask blocks.
    target = tmp_path / "out.txt"
    session = new_session("go")
    fake = FakeProvider(
        [
            tool_reply("write", {"path": str(target), "content": "hi"}),
            tool_reply("ask", {"questions": ["which format?"]}),
        ]
    )
    await drain(session, fake)
    assert target.read_text() == "hi"
    assert session.status == "blocked"
    assert session.questions == ["which format?"]


async def test_a_truncated_ask_is_an_error_not_blocked() -> None:
    session = new_session("go")
    fake = FakeProvider(
        [tool_reply("ask", {"questions": ["which"]}, stop_reason="max_tokens")]
    )
    await step(session, fake, approve_all, Policy())
    assert session.status == "error"
    assert session.questions == []
```

`drain` is defined further down the file; it is a module-level coroutine, so calling it from a test defined above it works.

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/test_loop.py -q`
Expected: collection error, `ImportError: cannot import name 'ASK_NOT_ALONE' from 'nare.loop'`.

- [ ] **Step 5: Implement the loop change**

In `src/nare/loop.py`, after the `_UNFINISHED` dict:

```python
# An ask beside other calls is not a question about the request: the turn has
# already acted on one reading of it. Honouring it told the caller that work
# was waiting on them when it was finished.
ASK_NOT_ALONE = (
    "ask was not recorded: call ask on its own, before making any change. "
    "The other calls in this turn ran."
)
```

In `step()`, directly above `results: list[dict[str, Any]] = []`, add:

```python
    only_asks = all(call.name == "ask" for call in reply.tool_calls)
```

Replace the `else:` dispatch branch:

```python
        else:
            # A refused ask (the policy excludes it) still goes to dispatch, so
            # the model learns it cannot ask at all rather than being told to
            # retry it alone.
            # ponytail: tool calls dispatch sequentially. Models do emit
            # parallel tool calls; asyncio.gather is the upgrade once a run is
            # measurably slow because of it, and not before.
            for call in reply.tool_calls:
                if call.name == "ask" and not only_asks and "ask" in policy.tools:
                    results.append(tool_result(call.id, ASK_NOT_ALONE, is_error=True))
                else:
                    results.append(await dispatch(call, policy, approve))
```

and replace the final `elif any(call.name == "ask" ...)` branch:

```python
    elif only_asks and not any(r["is_error"] for r in results):
        # Blocked on what happened, not on what the model called: a refused,
        # unapproved or malformed ask came back as an error and asked nothing.
        s.status = "blocked"
        s.questions = questions_from(reply.tool_calls)
```

- [ ] **Step 6: Run the loop tests to verify they pass**

Run: `uv run pytest tests/test_loop.py -q`
Expected: all pass, including the existing `test_ask_blocks_the_session_with_questions`, `test_run_stops_at_blocked` and the resume test.

- [ ] **Step 7: Bump the contract version and watch the doc test fail**

In `src/nare/contract.py`: `CONTRACT_VERSION = 2`.

Run: `uv run pytest tests/test_contract.py -q`
Expected: FAIL in `test_the_documented_contract_matches_the_code` (`"contract version 2" in text`).

- [ ] **Step 8: Update docs/contract.md**

- Line 3: `This is contract version 2.`
- The refusal example: `nare run --contract 2 --yes "..."`
- The `blocked` row in the exit code table:
  `| \`blocked\` | exit 0 | The model used \`ask\` on its own, in a session whose policy allows it. \`questions\` carries what it needs. This is an outcome, not a failure. |`
- In "What changes the version": `Contract version 2 covers the event types ...`
- Append at the end of the file:

```markdown
## Changes in version 2

A turn that called `ask` alongside other tools ended `blocked` under version 1.
It now keeps working: the other calls run, each `ask` gets an error result
telling the model to ask on its own, and the loop takes another turn. A turn
of only `ask` calls still ends `blocked`, but only when every one of them
succeeded. An `ask` the policy refuses, or that fails, never blocks.

This changes when `blocked` is reported, so the version moved. No field, event
type or exit code changed.

A session written under version 1 cannot be resumed by this nare. A caller
holding a `blocked` version 1 session has to start that task again.
```

Run: `uv run pytest tests/test_contract.py tests/test_cli.py -q`
Expected: PASS.

- [ ] **Step 9: Run the whole build**

Run: `bin/build`
Expected: format, lint, mypy and pytest all clean. If `ruff format --check` complains, run `uv run ruff format src tests` and re-run.

- [ ] **Step 10: Commit**

```bash
git add tests/fake_provider.py tests/test_loop.py src/nare/loop.py src/nare/contract.py docs/contract.md
git commit -m "feat(loop): block only on a turn of successful asks; contract 2"
```

---

### Task 2: The ask description says when not to ask

**Files:**
- Modify: `src/nare/tools.py` (the `ask` entry in `TOOL_SCHEMAS`, around line 173)

**Interfaces:**
- Consumes: nothing new.
- Produces: the `ask` schema's `description` and `questions.description`, verbatim from spec section 3. Name, `input_schema` shape and `required` unchanged.

No new test: spec section 6 rules out asserting on the prose. The existing schema tests (`tests/test_policy.py`, `tests/test_schema*.py`) guard the shape.

- [ ] **Step 1: Replace the ask schema**

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
    },
```

- [ ] **Step 2: Run the build**

Run: `bin/build`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add src/nare/tools.py
git commit -m "feat(tools): ask says when not to ask, and to name the readings"
```

---

### Task 3: Measure it and open the PR

**Files:**
- None committed. Results land in gitignored `benchmarks/results/`.

**Interfaces:**
- Consumes: `bin/bench run --tier smoke`, which compares against `benchmarks/baselines/ada-qwen3-14b.smoke.toml` (re-blessed in #35 at `a7580ae`) and exits 1 on a regression.

- [ ] **Step 1: Check the cases are still well-formed**

Run: `bin/bench verify`
Expected: exit 0.

- [ ] **Step 2: Run the smoke tier**

Run: `bin/bench run --tier smoke` (needs Docker and the LiteLLM env; the model ID is a proxy alias read from config, never hardcoded).
Expected against the spec's section 7 table:

| Case | Expected |
| --- | --- |
| edit-docstring | `improved` (baseline pass_rate 0.67 over 3 reps) |
| multi-file-rename | `ok` or `improved` |
| ambiguous-request | `ok`: still `blocked` (baseline pass_rate 0.0 over 7 reps, judge_met_median 1) |
| fix-failing-test | `ok` |

A token-band note on short cases is expected (the description is about 100 tokens longer). If edit-docstring does not improve, record it as a finding in the PR (spec section 7): the system prompt is the next lever. Do not change code to chase the number.

- [ ] **Step 3: Do not bless**

The baseline is the "before" number this PR is judged against. Leave `benchmarks/baselines/` untouched.

- [ ] **Step 4: Push and open the PR**

```bash
export GH_TOKEN=$(gh auth token)
git push -u origin feat/ask-only-when-ambiguous --force-with-lease
gh pr create --title "Ask only when the request is ambiguous" --body-file <body>
```

The body follows `.github/pull_request_template.md`: What changed (loop rule, description, contract 2), Why (9 of 16 smoke failures on `ada/qwen3-14b` were needless asks), Test plan (`bin/build`, plus the before and after bench table from Step 2 verbatim), a release note that a `blocked` contract 1 session cannot be resumed and callers pinning `--contract 1` are refused at start, and `Closes #34`. No em dashes.

- [ ] **Step 5: Watch CI**

Use the `watch-ci` skill. Do not merge without the `merge-pr` skill.
