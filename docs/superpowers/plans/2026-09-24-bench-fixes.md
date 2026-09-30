# Benchmark Fixes From the First Runs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every benchmark rep keep its evidence, survive one bad judge reply, show the judge a blocked run's questions, count cached tokens, and judge ambiguous-request on the quality of its question; then re-bless the smoke baseline.

**Architecture:** All changes are in `benchmarks/`. `judge.py` gains a one-retry loop and returns its raw attempts. `grade.py` reads `questions` off the result line. `__main__.py` fixes one results stamp per run and copies each rep's session, stdout, stderr and judge replies to `benchmarks/results/<stamp>/<case>-<rep>/` before the sandbox is removed. `report.py` counts all four usage fields.

**Tech Stack:** Python 3.14, pytest (`asyncio_mode = "auto"`), ruff, mypy strict, Docker (runs only), `uv`.

**Spec:** `docs/superpowers/specs/2026-09-24-bench-fixes-design.md`. Issue: #33 (Nare board, In progress).

## Global Constraints

- Nothing under `src/nare/` changes.
- `JUDGE_MAX_TOKENS` stays `1024`.
- The judge retries exactly once: two attempts at most, then the rep is `error`.
- `RepRecord.artifacts: str`, relative to `benchmarks/results/`, defaults to `""`.
- `latest_results` is untouched (it globs `*.jsonl`).
- `benchmarks/results/` stays gitignored; no pruning.
- Prose we publish (README, PR body, comments) uses no em dashes.
- `bin/build` (ruff format --check, ruff check, mypy strict, pytest) and `bin/bench verify --tier full` pass.
- Commits follow the repo's `type(scope): summary` style, scope `bench`.

## Review Focus

1. **A confirmation re-run reuses `<case>-<rep>` directory names.** A `judge.txt` from the first pass must not survive next to a second-pass rep that never reached the judge. Pinned in Task 5 (`test_a_rerun_rep_clears_what_the_first_pass_left`).
2. **A rep that timed out has no result line.** That is exactly the rep section 2 exists to diagnose, so it must still keep stdout and stderr. Pinned in Task 5 (`test_a_timed_out_rep_still_keeps_its_output`).
3. **The container can die before writing `session.json`.** A missing session must not crash the rep or the run. Pinned in the same Task 5 test.
4. **A result line whose `questions` is `null` or missing.** It must read as `[]`, not crash `parse_stdout`. Pinned in Task 2.
5. **The judge fails on transport first and then answers.** A 429 on the first attempt must be retried like an empty reply, and both attempts land in `judge.txt`. Pinned in Task 1 (`test_a_transport_failure_then_an_answer_scores`).

## Spec note

Section 5 says "one sentence added to `JUDGE_SYSTEM`". The existing line "Judge only what the diff shows" would then contradict section 7's claim about a question, which only the closing message can show. Task 3's sentence carves that out explicitly ("use it for claims about what the agent said or asked") so the two do not fight. Still one added sentence.

---

### Task 1: The judge retries once and keeps every attempt

**Files:**
- Modify: `benchmarks/runner/judge.py` (imports, `JudgeError`, `score`; add `JudgeAttempt`, `JUDGE_ATTEMPTS`, `render_attempts`, `_ask`)
- Modify: `benchmarks/runner/__main__.py:236` (unpack the new third return value)
- Test: `benchmarks/tests/test_judge.py`

**Interfaces:**
- Produces:
  - `JudgeAttempt(stop_reason: str | None, text: str)`: frozen dataclass.
  - `JudgeError(message: str, attempts: Sequence[JudgeAttempt] = ())`, with `.attempts: tuple[JudgeAttempt, ...]`.
  - `async score(case, diff, final_text, *, transport) -> tuple[list[bool], str, tuple[JudgeAttempt, ...]]`.
  - `render_attempts(attempts: Sequence[JudgeAttempt]) -> str`: the `judge.txt` body.
  - A transport failure's attempt has `stop_reason="transport error"` and the exception text as `text`.

- [ ] **Step 1: Make `FakeJudge` scriptable and write the failing tests**

In `benchmarks/tests/test_judge.py`, replace the `FakeJudge` class with:

```python
class FakeJudge:
    """A scripted transport, in the FakeProvider idiom the repo already uses.

    Each call takes the next scripted reply, and the last one repeats. An
    exception in the script is raised instead of answered.
    """

    def __init__(self, *script: str | Exception) -> None:
        self.script = script
        self.calls: list[list[Message]] = []

    async def turn(self, messages: list[Message], tools: list[dict[str, Any]]) -> Reply:
        self.calls.append(messages)
        assert tools == [], "the judge asks for no tools"
        step = self.script[min(len(self.calls), len(self.script)) - 1]
        if isinstance(step, Exception):
            raise step
        return Reply(
            content=[{"type": "text", "text": step}],
            tool_calls=[],
            usage=Usage(input=10, output=5),
            stop_reason="end_turn",
        )
```

Change the import line to:

```python
from benchmarks.runner.judge import (
    JudgeAttempt,
    JudgeError,
    build_prompt,
    parse_reply,
    render_attempts,
    score,
)
```

Update `test_score_returns_the_booleans_and_the_reason` to unpack three values:

```python
async def test_score_returns_the_booleans_and_the_reason() -> None:
    transport = FakeJudge('{"met": [true, false], "why": "the test was edited"}')
    met, why, attempts = await score(CASE, "a diff", "final text", transport=transport)
    assert met == [True, False]
    assert why == "the test was edited"
    assert len(transport.calls) == 1
    assert len(attempts) == 1
```

Append:

```python
async def test_an_empty_reply_is_retried_once() -> None:
    answer = '{"met": [true, true], "why": "ok"}'
    transport = FakeJudge("", answer)
    met, _, attempts = await score(CASE, "d", "f", transport=transport)
    assert met == [True, True]
    assert len(transport.calls) == 2
    assert [a.text for a in attempts] == ["", answer]


async def test_two_bad_replies_raise_carrying_both() -> None:
    transport = FakeJudge("", "still not json")
    with pytest.raises(JudgeError) as info:
        await score(CASE, "d", "f", transport=transport)
    assert len(transport.calls) == 2
    assert [a.text for a in info.value.attempts] == ["", "still not json"]


async def test_a_transport_failure_then_an_answer_scores() -> None:
    transport = FakeJudge(
        ConnectionError("reset"), '{"met": [true, false], "why": "x"}'
    )
    met, _, attempts = await score(CASE, "d", "f", transport=transport)
    assert met == [True, False]
    assert attempts[0].stop_reason == "transport error"
    assert "reset" in attempts[0].text


def test_judge_txt_holds_each_stop_reason_then_its_text() -> None:
    text = render_attempts(
        [JudgeAttempt("max_tokens", ""), JudgeAttempt("end_turn", "{}")]
    )
    assert text == (
        "attempt 1: stop_reason=max_tokens\n"
        "\n\n"
        "attempt 2: stop_reason=end_turn\n{}\n"
    )
```

The existing `test_score_raises_on_an_unusable_reply` and `test_a_transport_failure_is_a_judge_failure` stay as they are: both scripts repeat, so both attempts fail and `score` still raises.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest benchmarks/tests/test_judge.py -q`
Expected: collection error, `ImportError: cannot import name 'JudgeAttempt'`.

- [ ] **Step 3: Implement the retry in `judge.py`**

Add to the imports:

```python
from collections.abc import Sequence
from dataclasses import dataclass
```

Replace the `JudgeError` class with:

```python
@dataclass(frozen=True)
class JudgeAttempt:
    """One call to the judge, kept whole so a bad reply can be read later."""

    stop_reason: str | None
    text: str


class JudgeError(Exception):
    """The judge could not answer. Never a zero -- see grade.rep_outcome."""

    def __init__(self, message: str, attempts: Sequence[JudgeAttempt] = ()) -> None:
        super().__init__(message)
        self.attempts = tuple(attempts)


# One retry: enough to survive a single empty or garbled reply, few enough
# that a judge which cannot answer costs two calls rather than a loop.
JUDGE_ATTEMPTS = 2


def render_attempts(attempts: Sequence[JudgeAttempt]) -> str:
    """The body of judge.txt: each attempt's stop reason, then its raw text."""
    body = "\n\n".join(
        f"attempt {n}: stop_reason={a.stop_reason}\n{a.text}"
        for n, a in enumerate(attempts, start=1)
    )
    return body + "\n"
```

Replace `score` with `_ask` plus a new `score`:

```python
async def _ask(
    prompt: str, expected: int, transport: Transport
) -> tuple[list[bool], str, JudgeAttempt]:
    """One attempt. Every failure raises JudgeError carrying this attempt."""
    try:
        reply = await transport.turn(
            [Message(role="user", content=[{"type": "text", "text": prompt}])],
            [],
        )
    except Exception as exc:
        # A 429 or a dropped connection is the judge failing to answer. Raised
        # as JudgeError so the rep records an error instead of crashing the
        # run and discarding every rep already paid for.
        why = f"{type(exc).__name__}: {exc}"
        raise JudgeError(why, [JudgeAttempt("transport error", why)]) from exc
    text = "\n".join(
        block.get("text", "") for block in reply.content if block.get("type") == "text"
    )
    attempt = JudgeAttempt(reply.stop_reason, text)
    try:
        met = parse_reply(text, expected)
    except JudgeError as exc:
        raise JudgeError(str(exc), [attempt]) from exc
    return met, str(_extract_json(text).get("why", "")), attempt


async def score(
    case: Case, diff: str, final_text: str, *, transport: Transport
) -> tuple[list[bool], str, tuple[JudgeAttempt, ...]]:
    assert case.judge is not None, "score is only called for judged cases"
    prompt = build_prompt(case, diff, final_text)
    attempts: list[JudgeAttempt] = []
    while True:
        try:
            met, why, attempt = await _ask(
                prompt, len(case.judge.assertions), transport
            )
        except JudgeError as exc:
            attempts += exc.attempts
            if len(attempts) >= JUDGE_ATTEMPTS:
                raise JudgeError(str(exc), attempts) from exc
            continue
        return met, why, (*attempts, attempt)
```

In `benchmarks/runner/__main__.py`, in `run_rep`, change `met, why = await score(` to `met, why, _ = await score(` (Task 5 uses the attempts).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run ruff format benchmarks && uv run pytest benchmarks/tests/test_judge.py -q && bin/build`
Expected: all pass; `bin/build` green.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/runner/judge.py benchmarks/runner/__main__.py benchmarks/tests/test_judge.py
git commit -m "fix(bench): retry the judge once and keep every attempt it made"
```

---

### Task 2: A blocked run's questions reach the judge

**Files:**
- Modify: `benchmarks/runner/grade.py` (`ResultLine`, add `_questions_from`, `parse_stdout`)
- Modify: `benchmarks/runner/__main__.py:166-175` (`line_text`) and the `score(...)` call in `run_rep`
- Test: `benchmarks/tests/test_grade.py`, `benchmarks/tests/test_main.py`

**Interfaces:**
- Consumes: `score(...)` from Task 1 (three return values).
- Produces:
  - `ResultLine.questions: list[str]`, last field, default `[]` (existing positional constructions keep working).
  - `line_text(stdout: str, questions: Sequence[str] = ()) -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `benchmarks/tests/test_grade.py`:

```python
def test_reads_the_questions_a_blocked_run_asked() -> None:
    blocked = json.loads(RESULT) | {
        "status": "blocked",
        "questions": ["Raise TIMEOUT from 30 to what?"],
    }
    line = parse_stdout(json.dumps(blocked))
    assert line is not None
    assert line.questions == ["Raise TIMEOUT from 30 to what?"]


def test_a_result_line_without_questions_reads_as_none_asked() -> None:
    for questions in ({}, {"questions": None}):
        payload = {k: v for k, v in json.loads(RESULT).items() if k != "questions"}
        line = parse_stdout(json.dumps(payload | questions))
        assert line is not None
        assert line.questions == []
```

In `benchmarks/tests/test_main.py`, add `line_text` to the import from `benchmarks.runner.__main__`, then append:

```python
OUTPUT = json.dumps({"type": "output", "text": "Done: raised it to 60."})


def test_the_closing_message_is_the_output_event() -> None:
    assert line_text(f"{OUTPUT}\n") == "Done: raised it to 60."


def test_the_closing_message_carries_a_blocked_runs_questions() -> None:
    """nare emits no output event on a blocked run, only the questions."""
    text = line_text("", ["Raise TIMEOUT from 30 to what?", "Which file?"])
    assert text == (
        "The agent stopped to ask:\n"
        "- Raise TIMEOUT from 30 to what?\n"
        "- Which file?"
    )


def test_questions_follow_the_output_when_there_is_both() -> None:
    text = line_text(f"{OUTPUT}\n", ["Which file?"])
    assert text == (
        "Done: raised it to 60.\n\nThe agent stopped to ask:\n- Which file?"
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest benchmarks/tests/test_grade.py benchmarks/tests/test_main.py -q`
Expected: FAIL, `AttributeError: 'ResultLine' object has no attribute 'questions'` and `TypeError: line_text() takes 1 positional argument but 2 were given`.

- [ ] **Step 3: Implement**

In `benchmarks/runner/grade.py`, change `from dataclasses import dataclass` to `from dataclasses import dataclass, field`, and add the field as the last one of `ResultLine`:

```python
    error: str | None
    questions: list[str] = field(default_factory=list)
```

Below `_usage_from`, add:

```python
def _questions_from(raw: Any) -> list[str]:
    return [str(q) for q in raw] if isinstance(raw, list) else []
```

In `parse_stdout`, add to the `ResultLine(...)` call:

```python
            error=payload.get("error"),
            questions=_questions_from(payload.get("questions")),
        )
```

In `benchmarks/runner/__main__.py`, replace `line_text` with:

```python
def line_text(stdout: str, questions: Sequence[str] = ()) -> str:
    """What the judge reads as the agent's closing message.

    The `output` event's text, which nare emits only on `done`, then any
    questions a blocked run asked: without them no claim can be about them.
    """
    text = ""
    for raw in reversed(stdout.splitlines()):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("type") == "output":
            text = str(payload.get("text", ""))
            break
    if questions:
        asked = "\n".join(f"- {q}" for q in questions)
        text = f"{text}\n\nThe agent stopped to ask:\n{asked}".lstrip()
    return text
```

In `run_rep`, change the argument `line_text(result.stdout),` to `line_text(result.stdout, line.questions),`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run ruff format benchmarks && uv run pytest benchmarks/tests -q && bin/build`
Expected: all pass; `bin/build` green.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/runner/grade.py benchmarks/runner/__main__.py benchmarks/tests/test_grade.py benchmarks/tests/test_main.py
git commit -m "fix(bench): show the judge the questions a blocked run asked"
```

---

### Task 3: The diff decides file claims; ambiguous-request judges its question

**Files:**
- Modify: `benchmarks/runner/judge.py` (`JUDGE_SYSTEM`)
- Modify: `benchmarks/cases/ambiguous-request/case.toml`
- Test: `benchmarks/tests/test_judge.py`, `benchmarks/tests/test_cases.py`

**Interfaces:**
- Consumes: the questions in the closing message (Task 2), which claim 2 below is judged from.
- Produces: nothing code-level.

- [ ] **Step 1: Write the failing tests**

Append to `benchmarks/tests/test_judge.py` (and add `JUDGE_SYSTEM` to its import from `benchmarks.runner.judge`):

```python
def test_the_diff_not_the_closing_message_decides_file_claims() -> None:
    """Before #30 the judge credited a file claim the diff could not show."""
    assert "true only if the diff shows it" in JUDGE_SYSTEM
```

Append to `benchmarks/tests/test_cases.py`:

```python
def test_the_ask_case_judges_the_question_not_just_the_status() -> None:
    """A vague "what should I do?" must not score like a concrete question."""
    cases = {c.id: c for c in load_cases(repo_root() / "benchmarks" / "cases")}
    judge = cases["ambiguous-request"].judge
    assert judge is not None
    assert judge.min_met == 2
    assert len(judge.assertions) == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest benchmarks/tests/test_judge.py benchmarks/tests/test_cases.py -q`
Expected: 2 FAIL (`assert ... in JUDGE_SYSTEM`; `assert judge is not None`).

- [ ] **Step 3: Implement**

In `benchmarks/runner/judge.py`, replace the last line of `JUDGE_SYSTEM` so it reads:

```python
The "met" array must have exactly one boolean per claim, in the same order.
Judge only what the diff shows. Do not reward effort or intent.
The closing message is context: use it for claims about what the agent said \
or asked, but a claim about a file's contents is true only if the diff shows it.\
"""
```

Append to `benchmarks/cases/ambiguous-request/case.toml`:

```toml

[judge]
assertions = [
  "config.py was not changed.",
  "A question names the current timeout value or offers concrete values to choose from.",
]
min_met = 2
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run ruff format benchmarks && uv run pytest benchmarks/tests -q && bin/build`
Expected: all pass. Also run `bin/bench verify --tier full` (needs Docker, no key): ambiguous-request still prints `exempt`.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/runner/judge.py benchmarks/cases/ambiguous-request/case.toml benchmarks/tests/test_judge.py benchmarks/tests/test_cases.py
git commit -m "fix(bench): judge ambiguous-request on its question; the diff decides file claims"
```

---

### Task 4: Tokens include cached input

**Files:**
- Modify: `benchmarks/runner/report.py` (`summarize`, the `tokens_median=` argument)
- Modify: `benchmarks/README.md` (the "Running it" section)
- Test: `benchmarks/tests/test_report.py:106-109`

**Interfaces:**
- Produces: `CaseSummary.tokens_median` = median of `input + output + cache_read + cache_write`.

- [ ] **Step 1: Write the failing test**

In `benchmarks/tests/test_report.py`, replace `test_tokens_median_sums_input_and_output` with:

```python
def test_tokens_median_counts_cached_input() -> None:
    """The proxy serves most input from cache; leaving it out hides a longer
    prompt, which is exactly what a harness change tends to add."""
    rec = record(tokens=1)
    usage = {"input": 1, "output": 50, "cache_read": 640, "cache_write": 9}
    rec = RepRecord(**{**rec.__dict__, "usage": usage})
    assert summarize([rec])[0].tokens_median == 700
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest benchmarks/tests/test_report.py -q`
Expected: FAIL, `assert 51 == 700`.

- [ ] **Step 3: Implement**

In `benchmarks/runner/report.py`, in `summarize`, replace the `tokens_median=median_int(...)` argument with:

```python
                # usage holds exactly grade's four fields, cached input included.
                tokens_median=median_int([sum(r.usage.values()) for r in judged]),
```

In `benchmarks/README.md`, after the paragraph ending "three repetitions are not conclusive on their own.", add:

```markdown
Tokens are `input + output + cache_read + cache_write` from nare's result
line. A proxy serves most input from cache, so counting only `input` would
hide a longer prompt.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run ruff format benchmarks && uv run pytest benchmarks/tests -q && bin/build`
Expected: all pass. The committed baseline's `tokens_median` numbers are now stale; Task 6 re-blesses them.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/runner/report.py benchmarks/README.md benchmarks/tests/test_report.py
git commit -m "fix(bench): count cached input in tokens_median"
```

---

### Task 5: Keep each rep's evidence; one stamp per run

**Files:**
- Modify: `benchmarks/runner/report.py` (`RepRecord`: add `artifacts`)
- Modify: `benchmarks/runner/__main__.py` (imports; add `new_stamp`, `keep_evidence`; `run_rep`, `run_case`, `write_results`, the `run` branch of `main`)
- Modify: `benchmarks/README.md` (new "What a run leaves behind" section)
- Test: `benchmarks/tests/test_main.py`

**Interfaces:**
- Consumes: `score -> (met, why, attempts)`, `JudgeError.attempts`, `render_attempts` (Task 1); `line.questions`, `line_text(stdout, questions)` (Task 2).
- Produces:
  - `RepRecord.artifacts: str = ""`, last field, e.g. `"2026-09-24T10-00-00Z/fix-failing-test-0"`.
  - `new_stamp() -> str`, format `%Y-%m-%dT%H-%M-%SZ`, UTC.
  - `keep_evidence(dest: Path, artifacts: Path, result: RunArtifacts) -> None`.
  - `async run_rep(case, config, transport, rep, evidence: Path) -> RepRecord`, where `evidence` is `benchmarks/results/<stamp>`.
  - `async run_case(case, config, transport, reps, evidence: Path) -> list[RepRecord]`.
  - `write_results(root, records, stamp: str | None = None) -> Path`; `None` means `new_stamp()`.

- [ ] **Step 1: Write the failing tests**

In `benchmarks/tests/test_main.py`:

Add `from dataclasses import asdict, replace` (replacing the `replace`-only import), add `Judge` to the `benchmarks.runner.case` import, and add these imports:

```python
from benchmarks.tests.test_judge import FakeJudge
```

Update the existing `run_rep` call in `test_a_transport_failure_in_the_agent_is_an_error_not_a_fail`:

```python
    rec = await run_rep(case, config, cast(Transport, None), 0, tmp_path / "r" / "s")
```

Append:

```python
DONE_LINE = json.dumps(
    {
        "type": "result",
        "status": "done",
        "stop_reason": "end_turn",
        "turns": 1,
        "usage": {},
        "error": None,
        "questions": [],
    }
)
CONFIG = Config("m", "anthropic", None, "m", "k", "ANTHROPIC_API_KEY")


def sandboxed(tmp_path: Path, judge: Judge | None = None) -> Case:
    (tmp_path / "fixture").mkdir(exist_ok=True)
    return replace(
        case_with(Check(kind="bash", cmd="true")), directory=tmp_path, judge=judge
    )


def fake_sandbox(monkeypatch: pytest.MonkeyPatch, result: RunArtifacts) -> None:
    import benchmarks.runner.__main__ as bench

    def agent(case: Case, workdir: Path, artifacts: Path, config: Config) -> RunArtifacts:
        if not result.timed_out:
            (artifacts / "session.json").write_text('{"id": "s1"}')
        return result

    monkeypatch.setattr(bench, "run_agent", agent)
    monkeypatch.setattr(bench, "run_check", lambda w, c, t: (0, ""))
    monkeypatch.setattr(bench, "git_diff", lambda w: "a diff")


async def test_a_rep_keeps_its_session_output_and_judge_replies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_sandbox(monkeypatch, RunArtifacts(DONE_LINE, "a warning", 0, False, 1.0))
    case = sandboxed(tmp_path, Judge(("x",), 1))
    evidence = tmp_path / "results" / "stamp"
    judge = FakeJudge('{"met": [true], "why": "ok"}')
    rec = await run_rep(case, CONFIG, judge, 0, evidence)
    kept = evidence / "demo-0"
    assert rec.outcome == "pass"
    assert rec.artifacts == "stamp/demo-0"
    assert json.loads((kept / "session.json").read_text()) == {"id": "s1"}
    assert (kept / "stdout.jsonl").read_text() == DONE_LINE
    assert (kept / "stderr.txt").read_text() == "a warning"
    assert "stop_reason=end_turn" in (kept / "judge.txt").read_text()


async def test_a_judge_that_fails_twice_still_leaves_both_replies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_sandbox(monkeypatch, RunArtifacts(DONE_LINE, "", 0, False, 1.0))
    case = sandboxed(tmp_path, Judge(("x",), 1))
    evidence = tmp_path / "results" / "stamp"
    rec = await run_rep(case, CONFIG, FakeJudge("", "not json"), 0, evidence)
    judged = (evidence / "demo-0" / "judge.txt").read_text()
    assert rec.outcome == "error"
    assert "attempt 1" in judged
    assert "attempt 2: stop_reason=end_turn\nnot json" in judged


async def test_a_timed_out_rep_still_keeps_its_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No result line and no session: the rep section 2 exists to explain."""
    fake_sandbox(monkeypatch, RunArtifacts('{"type": "prog', "killed", 124, True, 600.0))
    evidence = tmp_path / "results" / "stamp"
    rec = await run_rep(sandboxed(tmp_path), CONFIG, cast(Transport, None), 0, evidence)
    kept = evidence / "demo-0"
    assert rec.outcome == "error"
    assert rec.judge_why == "timed out"
    assert rec.artifacts == "stamp/demo-0"
    assert (kept / "stdout.jsonl").read_text() == '{"type": "prog'
    assert (kept / "stderr.txt").read_text() == "killed"
    assert not (kept / "session.json").exists()


async def test_a_rerun_rep_clears_what_the_first_pass_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_sandbox(monkeypatch, RunArtifacts("", "", 124, True, 600.0))
    evidence = tmp_path / "results" / "stamp"
    (evidence / "demo-0").mkdir(parents=True)
    (evidence / "demo-0" / "judge.txt").write_text("from the first pass")
    await run_rep(sandboxed(tmp_path), CONFIG, cast(Transport, None), 0, evidence)
    assert not (evidence / "demo-0" / "judge.txt").exists()


def test_a_results_line_without_artifacts_still_loads(tmp_path: Path) -> None:
    payload = asdict(record("demo", model="m"))
    del payload["artifacts"]
    path = tmp_path / "old.jsonl"
    path.write_text(json.dumps(payload) + "\n")
    assert read_results(path)[0].artifacts == ""


def test_a_confirmation_rerun_leaves_one_results_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two stamps would mean two files, and `compare` would read the first."""
    import benchmarks.runner.__main__ as bench
    from benchmarks.runner.report import baseline_path
    from benchmarks.runner.sandbox import repo_root

    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "benchmarks" / "cases").symlink_to(repo_root() / "benchmarks" / "cases")
    baselines = tmp_path / "benchmarks" / "baselines"
    baselines.mkdir()
    baseline_path(baselines, "m", "smoke").write_text(
        dumps_baseline(
            BaselineMeta("t", "abc1234", "anthropic", "m", "m"),
            [summary("edit-docstring", 1.0)],
        )
    )

    async def failing(
        case: Case, config: Config, transport: Transport, reps: int, evidence: Path
    ) -> list[RepRecord]:
        base = replace(record(case.id, model="m"), outcome="fail")
        return [replace(base, rep=n) for n in range(reps)]

    # Distinct stamps on every call, so a second stamp cannot hide by landing
    # in the same second as the first.
    stamps = iter(f"2026-09-24T00-00-0{n}Z" for n in range(10))
    monkeypatch.setattr(bench, "new_stamp", lambda: next(stamps))
    monkeypatch.setattr(bench, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(bench, "build_image", lambda root: None)
    monkeypatch.setattr(bench, "judge_transport", lambda config: None)
    monkeypatch.setattr(bench, "run_case", failing)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    for var in ("NARE_PROVIDER", "NARE_BASE_URL", "NARE_BENCH_JUDGE_MODEL"):
        monkeypatch.delenv(var, raising=False)

    code = main(
        ["run", "--case", "edit-docstring", "--model", "m", "--provider", "anthropic"]
    )
    assert code == 1  # the drop was confirmed at seven reps
    results = tmp_path / "benchmarks" / "results"
    assert [p.name for p in results.glob("*.jsonl")] == ["2026-09-24T00-00-00Z.jsonl"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest benchmarks/tests/test_main.py -q`
Expected: FAIL; `run_rep() takes 4 positional arguments but 5 were given`, `KeyError: 'artifacts'`, and `AttributeError: ... has no attribute 'new_stamp'`.

- [ ] **Step 3: Add `artifacts` to `RepRecord`**

In `benchmarks/runner/report.py`, add as the last field of `RepRecord`:

```python
    model: str = ""
    # The rep's evidence directory, relative to benchmarks/results/. Empty in
    # results files written before evidence was kept.
    artifacts: str = ""
```

- [ ] **Step 4: Keep the evidence in `__main__.py`**

Add `import shutil` to the stdlib imports. Change the judge import to:

```python
from benchmarks.runner.judge import (
    JudgeAttempt,
    JudgeError,
    judge_transport,
    render_attempts,
    score,
)
```

Add `RunArtifacts,` to the `benchmarks.runner.sandbox` import list (alphabetical, after `SandboxError`).

Above `run_rep`, add:

```python
def new_stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")


def keep_evidence(dest: Path, artifacts: Path, result: RunArtifacts) -> None:
    """Copy what a rep produced out of its sandbox before the sandbox goes.

    Cleared first: a confirmation re-run reuses the same case-rep names, and a
    judge.txt left from the first pass would describe a different run. The
    session can be missing when the container died before nare wrote it.
    """
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    session = artifacts / "session.json"
    if session.is_file():
        shutil.copyfile(session, dest / "session.json")
    (dest / "stdout.jsonl").write_text(result.stdout, encoding="utf-8")
    (dest / "stderr.txt").write_text(result.stderr, encoding="utf-8")
```

Replace `run_rep` with:

```python
async def run_rep(
    case: Case, config: Config, transport: Transport, rep: int, evidence: Path
) -> RepRecord:
    """One repetition, from a clean fixture to a graded record.

    `evidence` is this run's directory under benchmarks/results/; the rep's
    session, output and judge replies are kept in a subdirectory of it.
    """
    empty = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    kept = evidence / f"{case.id}-{rep}"
    kept_ref = kept.relative_to(evidence.parent).as_posix()
    with sandbox(case) as (workdir, artifacts):
        result = run_agent(case, workdir, artifacts, config)
        keep_evidence(kept, artifacts, result)
        line = parse_stdout(result.stdout)
        if line is None or infra_error(line):
            # No result line means the run never produced one: a crash, a
            # timeout, a Docker fault. A transport failure nare caught is the
            # same thing with a result line. Either is an error, never a failure.
            if line is None:
                why = "timed out" if result.timed_out else "no result line"
            else:
                why = line.error or "run errored"
            return RepRecord(
                case=case.id,
                rep=rep,
                outcome="error",
                checks=(),
                judge_met=None,
                judge_why=why,
                status=None if line is None else line.status,
                stop_reason=None,
                turns=None if line is None else line.turns,
                usage=empty if line is None else line.usage,
                duration_s=result.duration_s,
                model=config.model,
                artifacts=kept_ref,
            )

        # Before the bash checks, so nothing a check writes reaches the judge.
        diff = git_diff(workdir)

        graded = [
            grade_result_check(check, line)
            for check in case.checks
            if check.kind == "result"
        ]
        for check in case.bash_checks:
            code, output = run_check(workdir, check.cmd or "", 120)
            graded.append(grade_bash_check(check, code, output))

        met: list[bool] | None = None
        why = ""
        if case.judge is not None:
            attempts: tuple[JudgeAttempt, ...]
            try:
                met, why, attempts = await score(
                    case,
                    diff,
                    line_text(result.stdout, line.questions),
                    transport=transport,
                )
            except JudgeError as exc:
                why = f"judge failed: {exc}"
                attempts = exc.attempts
            (kept / "judge.txt").write_text(render_attempts(attempts), encoding="utf-8")

    return RepRecord(
        case=case.id,
        rep=rep,
        outcome=rep_outcome(graded, case.judge, met),
        checks=tuple(graded),
        judge_met=None if met is None else sum(met),
        judge_why=why,
        status=line.status,
        stop_reason=line.stop_reason,
        turns=line.turns,
        usage=line.usage,
        duration_s=result.duration_s,
        model=config.model,
        artifacts=kept_ref,
    )
```

Replace `run_case` with:

```python
async def run_case(
    case: Case, config: Config, transport: Transport, reps: int, evidence: Path
) -> list[RepRecord]:
    # Sequential on purpose: repetitions share one rate limit and one Docker
    # daemon, and a benchmark that saturates either measures the machine.
    return [
        await run_rep(case, config, transport, rep, evidence) for rep in range(reps)
    ]
```

In `write_results`, change the signature and the stamp line:

```python
def write_results(
    root: Path, records: Sequence[RepRecord], stamp: str | None = None
) -> Path:
    directory = root / "benchmarks" / "results"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stamp or new_stamp()}.jsonl"
```

In `main`'s `run` branch, fix the stamp once and pass it everywhere:

```python
        transport = judge_transport(config)
        # One stamp per run: the confirmation re-run rewrites this run's
        # results file and evidence instead of starting a second of each.
        stamp = new_stamp()
        evidence = root / "benchmarks" / "results" / stamp
        records: list[RepRecord] = []
        for case in cases:
            records += asyncio.run(
                run_case(case, config, transport, case.reps, evidence)
            )

        path = write_results(root, records, stamp)
```

and in the confirmation block:

```python
            for case in [c for c in cases if c.id in suspects]:
                extra = asyncio.run(
                    run_case(case, config, transport, CONFIRM_REPS, evidence)
                )
                records = [r for r in records if r.case != case.id] + extra
            write_results(root, records, stamp)
```

- [ ] **Step 5: Document it in the README**

In `benchmarks/README.md`, after the "Running it" section (before "## Writing a case"), add:

```markdown
## What a run leaves behind

    benchmarks/results/<stamp>.jsonl          one line per repetition
    benchmarks/results/<stamp>/<case>-<rep>/
      session.json                            nare's session
      stdout.jsonl                            the event stream
      stderr.txt
      judge.txt                               judged cases: every judge reply

Each results line names its directory in `artifacts`. A confirmation re-run
rewrites the same files, so one run leaves one results file. Nothing is
pruned: a repetition is a few kilobytes, and the directory is gitignored.
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run ruff format benchmarks && uv run pytest benchmarks/tests -q && bin/build`
Expected: all pass; `bin/build` green.

- [ ] **Step 7: Commit**

```bash
git add benchmarks/runner/report.py benchmarks/runner/__main__.py benchmarks/README.md benchmarks/tests/test_main.py
git commit -m "fix(bench): keep each rep's session, output and judge replies; one results file per run"
```

---

### Task 6: Re-bless on this branch and run bash-timeout-recovery

Operational: needs Docker and a key. Run it after Tasks 1 to 5 are committed, so `bless` records a commit that contains them. Use the model named in the existing baseline's `[meta]`, never one typed from memory: the models are proxy aliases.

**Files:**
- Modify: `benchmarks/baselines/<model-slug>.smoke.toml` (rewritten by `bless`)

- [ ] **Step 1: Configure from the existing baseline**

```bash
grep -E '^(model|judge_model|provider)' benchmarks/baselines/*.smoke.toml
export NARE_MODEL=<model from [meta]>
export NARE_PROVIDER=<provider from [meta]>
export NARE_BASE_URL=<the proxy URL>        # if the baseline was blessed through one
export ANTHROPIC_API_KEY=<key>              # OPENAI_API_KEY for openai
```

`judge_model` in `[meta]` must equal `NARE_MODEL`, or also set `NARE_BENCH_JUDGE_MODEL` to it.

- [ ] **Step 2: Verify the cases, then run smoke**

```bash
bin/bench verify --tier full
bin/bench run --tier smoke 2>&1 | tee benchmarks/results/smoke-rebless.txt
```

Expected: verify all `ok` / `exempt`. The run may exit 1: tokens moved for every case (Task 4), and ambiguous-request is now judged (Task 3), so a pass-rate drop there is a real change in what is measured, not a regression to fix here. Note it for the PR.

- [ ] **Step 3: Check the evidence landed**

```bash
ls "$(ls -d benchmarks/results/*/ | sort | tail -1)"*
ls benchmarks/results/*.jsonl | tail -2
grep -h '^attempt' benchmarks/results/*/*/judge.txt | sort | uniq -c
```

Expected: one directory per rep, each holding `session.json`, `stdout.jsonl`, `stderr.txt`, and `judge.txt` for judged cases. Exactly one new `.jsonl` for this run. The `uniq -c` shows the judge's stop reasons; any `stop_reason=max_tokens` is the evidence section 3 asks for before raising `JUDGE_MAX_TOKENS` (a follow-up, not this branch).

- [ ] **Step 4: Bless and commit**

```bash
bin/bench bless --tier smoke
git diff benchmarks/baselines/
```

Expected: `commit` in `[meta]` is this branch's HEAD; `tokens_median` rose for every case; ambiguous-request gained `judge_met_median`.

```bash
git add benchmarks/baselines/
git commit -m "chore(bench): re-bless the smoke baseline with cached tokens and the ambiguous-request judge"
```

- [ ] **Step 5: Run bash-timeout-recovery on the full tier**

```bash
bin/bench run --tier full --case bash-timeout-recovery 2>&1 | tee benchmarks/results/btr-full.txt
```

Expected: a first-run table (no full baseline exists). Keep `benchmarks/results/btr-full.txt` (gitignored) for the PR description, with the pass count and the judge's per-rep `judge_met`. Moving the case back to smoke is a separate decision; do not change its tier here.

- [ ] **Step 6: Final gate**

```bash
bin/build
bin/bench verify --tier full
```

Expected: both green. Then finish the branch with superpowers:finishing-a-development-branch, following the repo's `watch-ci` and `merge-pr` skills. The PR body carries the smoke comparison, the bash-timeout-recovery result, and the judge stop-reason counts, and moves #33 to In review.
