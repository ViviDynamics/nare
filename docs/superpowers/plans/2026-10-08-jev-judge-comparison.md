# Comparing Jev Against the LLM Judge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every benchmark repetition keeps the diff and closing message its judge read, and a new `bin/bench compare-judges` measures how TypeSafe's Jev and the current LLM judge each agree with a reference model on claim judging and failure triage, at what cost and latency.

**Architecture:** PR 1 changes only `run_rep`: it writes `diff.patch` and `closing.txt` beside the evidence it already keeps. PR 2 adds four modules under `benchmarks/runner/`: `jev.py`, an httpx2 client for the proxy's TypeSafe pass-through; `triage.py`, the failure labels, the code-decided causes, and the reference's triage call; `agreement.py`, the row type and every summary metric, all pure; and `compare.py`, which reads kept evidence back and runs Jev, the LLM judge and the reference one repetition at a time. `__main__.py` wires the `compare-judges` subcommand. Nothing under `src/nare/` changes, and neither does `judge.py`.

**Tech Stack:** Python 3.12+, dataclasses, argparse, httpx2 (`MockTransport` in tests), pytest with `asyncio_mode = "auto"`, ruff, mypy strict, uv.

**Spec:** `docs/superpowers/specs/2026-10-08-jev-judge-comparison-design.md`. Issue: #46.

Every code block in this plan was run in a scratch worktree before the plan was written: with all six tasks applied, `bin/build` passed (ruff format, ruff check, mypy strict, and 704 tests). Copy the code as written.

## Global Constraints

- Nothing under `src/nare/` changes. Grading, `min_met` gating and baselines do not change.
- `RepRecord` and the results format do not change. `compare-judges` finds evidence through the existing `artifacts` field.
- `judge.py` is not changed. Usage is recorded by a wrapping transport.
- No new dependency and no TypeSafe SDK: `httpx2`, which already arrives with `anthropic`.
- Jev is reached only through the proxy: `POST {base}/typesafe/v1/systemone`, where `base` is `NARE_BASE_URL` with a trailing `/v1` removed. The bearer key is the one `resolve_config` resolved for the provider. The TypeSafe key never leaves the proxy.
- `JEV_PRICE_IN = 0.042` dollars per million input tokens. Output tokens are free.
- Size: four characters per token. Jev's limit is 32,000 tokens of state plus the longest question. Nothing is truncated to fit. The triage state budget is 24,000.
- Jev retries once on 429 or 529 after `Retry-After` seconds (capped at 30, default 2). Nothing else is retried.
- Sequential: repetitions one at a time; within one, Jev, then the LLM judge, then the reference.
- Exit codes: 0 when the comparison finished, even with judge errors; 1 when there is nothing to compare; 2 for configuration errors, before any call.
- Every summary says: `Agreement is with the reference model, not ground truth.`
- No live test. Task 0 is the live check.
- Prose we publish (docs, PR bodies, comments) uses no em dashes.
- `bin/build` passes before every push.
- Commit style `type(scope): summary`, ending with the `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` trailer.

## Decisions the spec leaves open

These are settled here so no implementer has to guess:

- **Jev's token count** is read from `usage.input_tokens`, the field LiteLLM's TypeSafe pass-through documents. Task 0 confirms it on a real response.
- **`JevError`** takes keyword-only `attempts` and `latency_s` besides the spec's `(message, body)`, so an error row still reports calls made and wall clock.
- **No cost header and no token count** makes the cost unknown (`null`), never an estimated zero.
- **A `choice` answer outside the question's labels** is a `JevError`, the same as a wrongly typed answer.
- **Transcript entries** are `{"role", "type", "text"}`, or `{"role", "type": "tool_use", "tool", "args"}`. The omission marker is `{"type": "omitted", "text": "[N messages omitted]"}`.
- **Skip reasons** are `no evidence files`, `errored before its diff was taken`, and `case no longer exists`.
- **`--case`** repeats, as `run`'s does. **`--results`** naming a missing file, or an empty results file, exits 1.
- **Bands** leave claims strictly between `1 - t` and `t` unanswered, with `1 - t` rounded to nine places so `0.1` at `t = 0.9` counts as answered.
- **p95** is nearest rank; the median is `statistics.median`.
- **Code rows** (`judge: "code"`) have `model: null`.
- **The `.jsonl`** is written one row at a time and flushed, so an interrupted comparison keeps the rows it paid for.

## Review Focus

1. **Results written before PR 1 have no evidence files.** `compare-judges` must exit 1 with a message saying why, having called nothing. Pinned in Task 6 (`test_results_without_evidence_exit_one`).
2. **A diff or closing message containing `\r`.** It must be read back byte for byte; `read_text`'s universal newlines would silently turn it into `\n`, and the judges would no longer see what the original judge saw. Pinned in Task 5 (`test_evidence_is_read_back_exactly`).
3. **A missing or truncated `session.json`**, as left by a container killed mid-write. Triage must get an empty transcript, not crash the whole comparison. Pinned in Task 5 (`test_a_missing_or_broken_session_leaves_the_transcript_empty`).
4. **A Jev response with no `usage` and no cost header.** The cost must be unknown, never an estimated $0 that reads as free. Pinned in Task 2 (`test_cost_is_unknown_without_the_header_or_a_count`).
5. **A band edge in floating point.** `1 - 0.9` is `0.0999...`, so P(yes) = 0.1 at `t = 0.9` must still count as answered. Pinned in Task 4 (`test_a_band_edge_counts_as_answered`).

## Delivery

- **Task 0** is operational. Nothing proceeds until it passes.
- **PR 1** is the current branch, `spec/jev-judge-comparison`, which already carries the spec. It takes this plan and Task 1.
- **PR 2** is Tasks 2 to 6, on `feat/compare-judges`, cut from PR 1's branch after Task 1's commit. After PR 1 squash-merges, rebase it onto `main` (the `rebase-main` skill) before opening the PR.
- **Task 7** is the full-tier run and the report on #46. It spends money and posts publicly, so it needs Sam's go-ahead.

---

### Task 0: Prove access to Jev through the proxy

Operational, not code. It needs the proxy running a LiteLLM with the TypeSafe pass-through and `TYPESAFE_API_KEY` set, and the TypeSafe account to have Jev access.

- [ ] **Step 1: Call the pass-through with the benchmark's own key**

With `NARE_BASE_URL` set to the proxy. Use `$ANTHROPIC_API_KEY` in place of `$OPENAI_API_KEY` when `NARE_PROVIDER=anthropic`:

```bash
curl -sS -D - "${NARE_BASE_URL%/v1}/typesafe/v1/systemone" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "jev-latest",
       "state": "def add(a, b):\n    return a - b",
       "questions": {"bug": {"type": "noul",
                             "instructions": "Does this function have a bug?"}}}'
```

Expected: status `200`; a body with `answers.bug.type == "noul"`, a numeric `answers.bug.noul`, a pinned `model` such as `jev-1.13.0`, and `usage.input_tokens`.

- [ ] **Step 2: Record the facts the code depends on**

Write down, for the PR 2 description:
- Whether the headers carry `x-litellm-response-cost`. If they do, Jev's cost comes from the header; if not, it is estimated from tokens and marked `~`.
- Whether the body carries `usage.input_tokens`. If it does not, Jev's cost will read as unknown, and `_input_tokens` in `benchmarks/runner/jev.py` must be pointed at the field the body does carry before PR 2 ships.

- [ ] **Step 3: Stop if it failed**

If the status is not 200, or the answer is missing, stop and report the status and body. Tasks 1 to 7 are not built.

---

### Task 1: Keep the judge's inputs with every repetition (PR 1)

**Files:**
- Modify: `benchmarks/runner/__main__.py` (`run_rep`, the block after `diff = git_diff(workdir)`, around lines 265-291)
- Modify: `benchmarks/README.md` ("What a run leaves behind")
- Test: `benchmarks/tests/test_main.py` (append)

**Interfaces:**
- Consumes: `line_text(stdout, questions) -> str`, `git_diff(workdir) -> str`, `keep_evidence` (unchanged; it already clears the directory).
- Produces: `<evidence>/<case>-<rep>/diff.patch` and `closing.txt`, UTF-8, exactly the strings the judge receives. Task 5 reads them back.

- [ ] **Step 1: Write the failing tests**

Append to `benchmarks/tests/test_main.py`. `OUTPUT`, `DONE_LINE`, `CONFIG`, `sandboxed` and `fake_sandbox` already exist in that file.

```python
async def test_a_judged_rep_keeps_the_diff_and_closing_message_it_judged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_sandbox(monkeypatch, RunArtifacts(f"{OUTPUT}\n{DONE_LINE}", "", 0, False, 1.0))
    judge = FakeJudge('{"met": [true], "why": "ok"}')
    evidence = tmp_path / "results" / "stamp"
    await run_rep(sandboxed(tmp_path, Judge(("x",), 1)), CONFIG, judge, 0, evidence)
    kept = evidence / "demo-0"
    assert (kept / "diff.patch").read_text() == "a diff"
    assert (kept / "closing.txt").read_text() == "Done: raised it to 60."
    prompt = judge.calls[0][0].content[0]["text"]
    assert "a diff" in prompt
    assert "Done: raised it to 60." in prompt


async def test_an_unjudged_rep_keeps_them_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Triage reads the diff of every failure, judged case or not."""
    fake_sandbox(monkeypatch, RunArtifacts(DONE_LINE, "", 0, False, 1.0))
    evidence = tmp_path / "results" / "stamp"
    await run_rep(sandboxed(tmp_path), CONFIG, cast(Transport, None), 0, evidence)
    kept = evidence / "demo-0"
    assert (kept / "diff.patch").read_text() == "a diff"
    assert (kept / "closing.txt").read_text() == ""
    assert not (kept / "judge.txt").exists()


async def test_a_rep_without_a_result_line_keeps_neither(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_sandbox(monkeypatch, RunArtifacts('{"type": "prog', "", 124, True, 600.0))
    evidence = tmp_path / "results" / "stamp"
    await run_rep(sandboxed(tmp_path), CONFIG, cast(Transport, None), 0, evidence)
    assert not (evidence / "demo-0" / "diff.patch").exists()
    assert not (evidence / "demo-0" / "closing.txt").exists()


async def test_an_infrastructure_error_keeps_neither(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    line = json.dumps(
        {"type": "result", "status": "error", "stop_reason": None, "turns": 1}
    )
    fake_sandbox(monkeypatch, RunArtifacts(line, "", 1, False, 1.0))
    evidence = tmp_path / "results" / "stamp"
    rec = await run_rep(
        sandboxed(tmp_path), CONFIG, cast(Transport, None), 0, evidence
    )
    assert rec.outcome == "error"
    assert not (evidence / "demo-0" / "diff.patch").exists()
    assert not (evidence / "demo-0" / "closing.txt").exists()


async def test_a_rerun_rep_clears_the_first_passs_diff_and_closing_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_sandbox(monkeypatch, RunArtifacts("", "", 124, True, 600.0))
    evidence = tmp_path / "results" / "stamp"
    (evidence / "demo-0").mkdir(parents=True)
    (evidence / "demo-0" / "diff.patch").write_text("from the first pass")
    (evidence / "demo-0" / "closing.txt").write_text("from the first pass")
    await run_rep(sandboxed(tmp_path), CONFIG, cast(Transport, None), 0, evidence)
    assert not (evidence / "demo-0" / "diff.patch").exists()
    assert not (evidence / "demo-0" / "closing.txt").exists()
```

- [ ] **Step 2: Run them and watch two fail**

Run: `uv run pytest -q benchmarks/tests/test_main.py`
Expected: `2 failed`: `test_a_judged_rep_keeps_the_diff_and_closing_message_it_judged` and `test_an_unjudged_rep_keeps_them_too`, each on a missing `diff.patch`. The other three pin behaviour that already holds (the early return and `keep_evidence`'s clear) and pass now.

- [ ] **Step 3: Write the two files in `run_rep`**

In `benchmarks/runner/__main__.py`, replace:

```python
        # Before the bash checks, so nothing a check writes reaches the judge.
        diff = git_diff(workdir)
```

with:

```python
        # Before the bash checks, so nothing a check writes reaches the judge.
        diff = git_diff(workdir)
        closing = line_text(result.stdout, line.questions)
        # Kept for every rep, judged or not, so any judge can be re-run against
        # exactly what this one read. compare-judges reads them back.
        (kept / "diff.patch").write_text(diff, encoding="utf-8")
        (kept / "closing.txt").write_text(closing, encoding="utf-8")
```

and in the judge call further down, replace:

```python
                met, why, attempts = await score(
                    case,
                    diff,
                    line_text(result.stdout, line.questions),
                    transport=transport,
                )
```

with:

```python
                met, why, attempts = await score(
                    case, diff, closing, transport=transport
                )
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q benchmarks/tests/test_main.py`
Expected: all pass (33 at the time of writing).

- [ ] **Step 5: Document the two files**

In `benchmarks/README.md`, under "What a run leaves behind", replace the layout block with:

```
    benchmarks/results/<stamp>.jsonl          one line per repetition
    benchmarks/results/<stamp>/<case>-<rep>/
      session.json                            nare's session
      stdout.jsonl                            the event stream
      stderr.txt
      judge.txt                               judged cases: every judge reply
      diff.patch                              the diff, as the judge read it
      closing.txt                             the closing message, as the judge read it
```

and add this paragraph directly after the one that begins "Each results line names its directory in `artifacts`":

```markdown
`diff.patch` and `closing.txt` are kept for every repetition that produced a
result line and did not break underneath the agent, judged or not. They are
what lets `compare-judges`, or any other judge, be re-run against a past
repetition.
```

- [ ] **Step 6: Build and commit**

Run: `bin/build`
Expected: ruff, mypy and pytest all pass.

```bash
git add benchmarks/runner/__main__.py benchmarks/tests/test_main.py benchmarks/README.md docs/superpowers/plans/2026-10-08-jev-judge-comparison.md
git commit -m "feat(bench): keep the diff and closing message with each rep

Every rep with a result line, judged or not, now keeps diff.patch and
closing.txt exactly as the judge received them, so any judge can be re-run
against past evidence. First half of #46.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 7: Open PR 1**

Push `spec/jev-judge-comparison` and open a PR against `main` titled `Keep each rep's diff and closing message (#46)`. Its body names the spec and this plan, and says PR 2 (compare-judges) follows. Then follow the `watch-ci` and `merge-pr` skills. PR 2's tasks need not wait for the merge; only opening PR 2 does.

---

### Task 2: The Jev client

**Files:**
- Create: `benchmarks/runner/jev.py`
- Test: `benchmarks/tests/test_jev.py`

**Interfaces:**
- Consumes: `reported_cost(headers) -> float | None` from `nare.transport`.
- Produces:
  - `JEV_PRICE_IN: float = 0.042`, `JEV_STATE_LIMIT: int = 32_000`
  - `estimated_tokens(state: Any, longest_question: str = "") -> float`
  - `Noul(p_yes: float)`, `Choice(choice: str, probabilities: dict[str, float], confidence: float)`, `Answer = Noul | Choice`
  - `JevReply(model, answers: dict[str, Answer], input_tokens: int, cost: float | None, cost_estimated: bool, latency_s: float, attempts: int)`
  - `JevError(message: str, body: str = "", *, attempts: int = 1, latency_s: float = 0.0)` with attributes `body`, `attempts`, `latency_s`
  - `class Jev(Protocol)`: `model: str`; `async def ask(self, state: Any, questions: Mapping[str, Mapping[str, Any]]) -> JevReply`
  - `JevClient(*, base_url: str, api_key: str, model: str, http_client=None, sleep=asyncio.sleep, clock=time.monotonic)`, which satisfies `Jev`
  - `retry_after(headers: Mapping[str, str]) -> float`

- [ ] **Step 1: Branch for PR 2**

```bash
git switch -c feat/compare-judges
```

- [ ] **Step 2: Write the failing tests**

Create `benchmarks/tests/test_jev.py`:

```python
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx2
import pytest

from benchmarks.runner.jev import (
    JEV_PRICE_IN,
    Choice,
    Jev,
    JevClient,
    JevError,
    Noul,
    estimated_tokens,
    retry_after,
)

QUESTIONS: dict[str, dict[str, Any]] = {
    "bug": {"type": "noul", "instructions": "Does this function have a bug?"},
    "team": {
        "type": "choice",
        "instructions": "Which team?",
        "criteria": {"billing": "Payments", "technical": "Bugs"},
    },
}
ANSWERS = {
    "bug": {"type": "noul", "noul": 0.93},
    "team": {
        "type": "choice",
        "choice": "technical",
        "probabilities": {"billing": 0.1, "technical": 0.9},
        "confidence": 0.82,
    },
}


def ok(answers: Any = None, **extra: Any) -> httpx2.Response:
    body = {
        "model": "jev-1.13.0",
        "answers": ANSWERS if answers is None else answers,
        "usage": {"input_tokens": 312, "output_tokens": 48},
    }
    return httpx2.Response(200, json=body, **extra)


class Script:
    """Answers each POST with the next scripted response, keeping the requests."""

    def __init__(self, *responses: httpx2.Response | Exception) -> None:
        self.responses: Iterator[httpx2.Response | Exception] = iter(responses)
        self.requests: list[httpx2.Request] = []
        self.slept: list[float] = []

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        step = next(self.responses)
        if isinstance(step, Exception):
            raise step
        return step

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def client(self, base_url: str = "https://proxy.example/v1") -> JevClient:
        ticks = iter(float(n) for n in range(100))
        return JevClient(
            base_url=base_url,
            api_key="sk-proxy",
            model="jev-latest",
            http_client=httpx2.AsyncClient(
                transport=httpx2.MockTransport(self.handler)
            ),
            sleep=self.sleep,
            clock=lambda: next(ticks),
        )


_check: Jev = Script().client()


async def test_posts_to_the_pass_through_with_the_proxy_key() -> None:
    script = Script(ok())
    await script.client().ask("def add(a, b): ...", QUESTIONS)
    (request,) = script.requests
    assert str(request.url) == "https://proxy.example/typesafe/v1/systemone"
    assert request.headers["authorization"] == "Bearer sk-proxy"
    assert json.loads(request.content) == {
        "model": "jev-latest",
        "state": "def add(a, b): ...",
        "questions": QUESTIONS,
    }


async def test_a_base_url_without_v1_is_used_as_it_is() -> None:
    script = Script(ok())
    await script.client("https://proxy.example/").ask("s", QUESTIONS)
    assert str(script.requests[0].url) == "https://proxy.example/typesafe/v1/systemone"


async def test_parses_noul_and_choice_answers() -> None:
    reply = await Script(ok()).client().ask("s", QUESTIONS)
    assert reply.model == "jev-1.13.0"
    assert reply.answers == {
        "bug": Noul(0.93),
        "team": Choice("technical", {"billing": 0.1, "technical": 0.9}, 0.82),
    }
    assert reply.input_tokens == 312
    assert reply.attempts == 1


async def test_a_429_is_retried_once_after_retry_after() -> None:
    script = Script(httpx2.Response(429, headers={"retry-after": "7"}), ok())
    reply = await script.client().ask("s", QUESTIONS)
    assert script.slept == [7.0]
    assert reply.attempts == 2
    assert reply.latency_s > 0


async def test_a_second_529_is_an_error() -> None:
    script = Script(httpx2.Response(529), httpx2.Response(529, text="overloaded"))
    with pytest.raises(JevError) as info:
        await script.client().ask("s", QUESTIONS)
    assert script.slept == [2.0]
    assert info.value.attempts == 2
    assert info.value.body == "overloaded"


@pytest.mark.parametrize(
    ("header", "seconds"),
    [(None, 2.0), ("7", 7.0), ("600", 30.0), ("-3", 0.0), ("soon", 2.0), ("nan", 2.0)],
)
def test_retry_after_is_bounded(header: str | None, seconds: float) -> None:
    headers = {} if header is None else {"retry-after": header}
    assert retry_after(httpx2.Headers(headers)) == seconds


async def test_a_422_raises_with_its_body_and_is_not_retried() -> None:
    script = Script(httpx2.Response(422, text='{"detail": "questions: required"}'))
    with pytest.raises(JevError, match="422") as info:
        await script.client().ask("s", QUESTIONS)
    assert "questions: required" in info.value.body
    assert len(script.requests) == 1


async def test_a_401_raises_with_its_body() -> None:
    script = Script(httpx2.Response(401, text="unknown key"))
    with pytest.raises(JevError) as info:
        await script.client().ask("s", QUESTIONS)
    assert info.value.body == "unknown key"


async def test_a_missing_answer_raises() -> None:
    script = Script(ok({"bug": ANSWERS["bug"]}))
    with pytest.raises(JevError, match="team"):
        await script.client().ask("s", QUESTIONS)


async def test_a_wrongly_typed_answer_raises() -> None:
    script = Script(ok({**ANSWERS, "bug": {"type": "score", "score": 3}}))
    with pytest.raises(JevError, match="bug"):
        await script.client().ask("s", QUESTIONS)


@pytest.mark.parametrize("p_yes", [1.5, "0.9", True, None])
async def test_a_noul_outside_zero_to_one_raises(p_yes: Any) -> None:
    script = Script(ok({**ANSWERS, "bug": {"type": "noul", "noul": p_yes}}))
    with pytest.raises(JevError, match="bug"):
        await script.client().ask("s", QUESTIONS)


async def test_a_choice_outside_the_labels_raises() -> None:
    team = {**ANSWERS["team"], "choice": "sales"}
    with pytest.raises(JevError, match="sales"):
        await Script(ok({**ANSWERS, "team": team})).client().ask("s", QUESTIONS)


async def test_a_body_that_is_not_json_raises() -> None:
    script = Script(httpx2.Response(200, text="<html>bad gateway</html>"))
    with pytest.raises(JevError) as info:
        await script.client().ask("s", QUESTIONS)
    assert "bad gateway" in info.value.body


async def test_a_dropped_connection_raises() -> None:
    script = Script(httpx2.ConnectError("refused"))
    with pytest.raises(JevError, match="refused"):
        await script.client().ask("s", QUESTIONS)


async def test_cost_comes_from_the_proxy_header() -> None:
    script = Script(ok(headers={"x-litellm-response-cost": "0.0000131"}))
    reply = await script.client().ask("s", QUESTIONS)
    assert reply.cost == pytest.approx(0.0000131)
    assert not reply.cost_estimated


async def test_cost_is_estimated_from_tokens_without_the_header() -> None:
    reply = await Script(ok()).client().ask("s", QUESTIONS)
    assert reply.cost == pytest.approx(312 * JEV_PRICE_IN / 1e6)
    assert reply.cost_estimated


async def test_cost_is_unknown_without_the_header_or_a_count() -> None:
    body = {"model": "jev-1.13.0", "answers": ANSWERS}
    reply = await Script(httpx2.Response(200, json=body)).client().ask("s", QUESTIONS)
    assert reply.cost is None
    assert not reply.cost_estimated


def test_size_is_four_characters_per_token() -> None:
    state = {"diff": "x" * 390}
    assert estimated_tokens(state, "q" * 2) == (len(json.dumps(state)) + 2) / 4
```

- [ ] **Step 3: Run them and watch them fail**

Run: `uv run pytest -q benchmarks/tests/test_jev.py`
Expected: collection error, `ModuleNotFoundError: No module named 'benchmarks.runner.jev'`.

- [ ] **Step 4: Write the client**

Create `benchmarks/runner/jev.py`:

```python
"""TypeSafe's Jev, reached through the LiteLLM proxy, for compare-judges only.

A second client, which judge.py exists to avoid: it runs through nare's own
transport so judging reaches every provider nare reaches, with no second
credential path. Jev is not a nare provider and has no Chat Completions form,
so there is nothing to reuse. This client stays in benchmarks/, is reached only
through the proxy's TypeSafe pass-through, and sends the run's own proxy key;
the TypeSafe key never leaves the proxy. Like the openai rail, it uses httpx2,
which already arrives with anthropic, so it adds no dependency.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx2

from nare.transport import reported_cost

# Dollars per million input tokens. Output tokens are free.
JEV_PRICE_IN = 0.042
# Jev takes at most this many tokens of state plus its longest question.
JEV_STATE_LIMIT = 32_000
RETRY_STATUSES = frozenset({429, 529})
RETRY_AFTER_DEFAULT = 2.0
RETRY_AFTER_CAP = 30.0
TIMEOUT = 120.0


def estimated_tokens(state: Any, longest_question: str = "") -> float:
    """Four characters per token, as compact.py estimates a transcript."""
    return (len(json.dumps(state)) + len(longest_question)) / 4


@dataclass(frozen=True)
class Noul:
    p_yes: float


@dataclass(frozen=True)
class Choice:
    choice: str
    probabilities: dict[str, float]
    confidence: float


Answer = Noul | Choice


@dataclass(frozen=True)
class JevReply:
    model: str  # the pinned version that answered
    answers: dict[str, Answer]
    input_tokens: int
    cost: float | None
    cost_estimated: bool
    latency_s: float  # wall clock, retries included
    attempts: int


class JevError(Exception):
    """Jev could not answer. `body` is the response body, when there was one."""

    def __init__(
        self,
        message: str,
        body: str = "",
        *,
        attempts: int = 1,
        latency_s: float = 0.0,
    ) -> None:
        super().__init__(message)
        self.body = body
        self.attempts = attempts
        self.latency_s = latency_s


class Jev(Protocol):
    """What compare-judges needs from Jev. Structural, like Transport."""

    model: str

    async def ask(
        self, state: Any, questions: Mapping[str, Mapping[str, Any]]
    ) -> JevReply: ...


def endpoint(base_url: str) -> str:
    """The pass-through hangs off the proxy root, not off its /v1."""
    root = base_url.rstrip("/").removesuffix("/v1")
    return f"{root}/typesafe/v1/systemone"


def retry_after(headers: Mapping[str, str]) -> float:
    raw = headers.get("retry-after")
    try:
        seconds = float(raw) if raw is not None else RETRY_AFTER_DEFAULT
    except ValueError:
        # An HTTP date, or garbage. Neither is worth parsing for one retry.
        seconds = RETRY_AFTER_DEFAULT
    if not math.isfinite(seconds):
        seconds = RETRY_AFTER_DEFAULT
    return min(max(seconds, 0.0), RETRY_AFTER_CAP)


def _probability(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if 0.0 <= number <= 1.0 else None


def _input_tokens(usage: Any) -> int | None:
    raw = usage.get("input_tokens") if isinstance(usage, dict) else None
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw


def parse_answer(qid: str, question: Mapping[str, Any], raw: Any) -> Answer:
    """One answer, checked against the question it answers."""
    wanted = question.get("type")
    if not isinstance(raw, dict) or raw.get("type") != wanted:
        got = raw.get("type") if isinstance(raw, dict) else raw
        raise JevError(f"answer {qid!r} is {got!r}, expected a {wanted} answer")
    if wanted == "noul":
        p_yes = _probability(raw.get("noul"))
        if p_yes is None:
            raise JevError(f"answer {qid!r} has no probability: {raw!r}")
        return Noul(p_yes)
    if wanted == "choice":
        labels = question.get("criteria", {})
        choice = raw.get("choice")
        probabilities = raw.get("probabilities")
        confidence = _probability(raw.get("confidence"))
        if not isinstance(choice, str) or choice not in labels:
            raise JevError(f"answer {qid!r} chose {choice!r}, not one of its labels")
        if not isinstance(probabilities, dict) or confidence is None:
            raise JevError(f"answer {qid!r} is missing probabilities or confidence")
        parsed = {str(k): _probability(v) for k, v in probabilities.items()}
        clean = {k: v for k, v in parsed.items() if v is not None}
        if len(clean) != len(parsed):
            raise JevError(f"answer {qid!r} has a probability outside 0 to 1")
        return Choice(choice, clean, confidence)
    raise JevError(f"question {qid!r} has type {wanted!r}, which is not parsed here")


class JevClient:
    """One POST per ask. Retries once on 429 or 529, and never on anything else."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        http_client: httpx2.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.model = model
        self._url = endpoint(base_url)
        self._key = api_key
        self._client = http_client or httpx2.AsyncClient(timeout=TIMEOUT)
        self._sleep = sleep
        self._clock = clock

    async def ask(
        self, state: Any, questions: Mapping[str, Mapping[str, Any]]
    ) -> JevReply:
        started = self._clock()
        attempts = 0
        while True:
            attempts += 1
            try:
                response = await self._client.post(
                    self._url,
                    headers={"authorization": f"Bearer {self._key}"},
                    json={"model": self.model, "state": state, "questions": questions},
                    timeout=TIMEOUT,
                )
            except httpx2.HTTPError as exc:
                raise JevError(
                    f"{type(exc).__name__}: {exc}",
                    attempts=attempts,
                    latency_s=self._clock() - started,
                ) from exc
            if response.status_code in RETRY_STATUSES and attempts == 1:
                await self._sleep(retry_after(response.headers))
                continue
            break
        latency = self._clock() - started
        try:
            return self._reply(response, questions, attempts, latency)
        except JevError as exc:
            exc.attempts, exc.latency_s = attempts, latency
            raise

    def _reply(
        self,
        response: httpx2.Response,
        questions: Mapping[str, Mapping[str, Any]],
        attempts: int,
        latency: float,
    ) -> JevReply:
        if response.status_code != 200:
            # A 422 is our request malformed: a bug to fix, so never retried.
            raise JevError(f"jev returned HTTP {response.status_code}", response.text)
        try:
            payload = response.json()
        except ValueError as exc:
            raise JevError(
                "jev returned a body that is not JSON", response.text
            ) from exc
        if not isinstance(payload, dict) or not isinstance(
            payload.get("answers"), dict
        ):
            raise JevError("jev returned no answers object", response.text)
        try:
            answers = {
                qid: parse_answer(qid, question, payload["answers"].get(qid))
                for qid, question in questions.items()
            }
        except JevError as exc:
            exc.body = response.text
            raise
        counted = _input_tokens(payload.get("usage"))
        # Unknown, not zero, when there is neither a header nor a count.
        cost = reported_cost(response.headers)
        estimated = cost is None and counted is not None
        if cost is None and counted is not None:
            cost = counted * JEV_PRICE_IN / 1e6
        return JevReply(
            model=str(payload.get("model") or self.model),
            answers=answers,
            input_tokens=counted or 0,
            cost=cost,
            cost_estimated=estimated,
            latency_s=latency,
            attempts=attempts,
        )
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest -q benchmarks/tests/test_jev.py`
Expected: `26 passed`.

- [ ] **Step 6: Lint, type-check and commit**

Run: `uv run ruff format benchmarks && uv run ruff check benchmarks && uv run mypy benchmarks`
Expected: no changes, `All checks passed!`, `Success: no issues found`.

```bash
git add benchmarks/runner/jev.py benchmarks/tests/test_jev.py
git commit -m "feat(bench): add a Jev client for the proxy's TypeSafe route

Typed noul and choice answers, one retry on 429 or 529, and cost from the
proxy header or estimated from input tokens. No new dependency. Part of #46.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Failure triage

**Files:**
- Create: `benchmarks/runner/triage.py`
- Test: `benchmarks/tests/test_triage.py`

**Interfaces:**
- Consumes: `estimated_tokens` (Task 2); `JUDGE_ATTEMPTS`, `JUDGE_MAX_TOKENS`, `JudgeAttempt`, `JudgeError`, `_extract_json` from `judge.py`; `RepRecord`; `Config`; `make_transport`.
- Produces:
  - `LABELS: dict[str, str]` (the seven model-labelled causes, in the spec's order), `RAN_OUT`, `CLIP = 1_000`, `STATE_BUDGET = 24_000`, `TRIAGE_SYSTEM: str`
  - `code_cause(rec: RepRecord, min_met: int | None) -> str | None`, which returns `"ran_out"`, `"judge_gate"` or `None`
  - `claimed_done(rec: RepRecord) -> bool`
  - `message_entries(message: Any) -> list[dict[str, str]]`
  - `triage_state(prompt: str, rec: RepRecord, diff: str, closing: str, messages: Sequence[Any]) -> dict[str, Any] | None` (`None` means too large)
  - `cause_question() -> dict[str, Any]`
  - `triage_transport(config: Config, model: str) -> Transport`
  - `async triage_reference(state: dict[str, Any], *, transport: Transport) -> tuple[str, tuple[JudgeAttempt, ...]]`, which raises `JudgeError`

- [ ] **Step 1: Write the failing tests**

Create `benchmarks/tests/test_triage.py`:

```python
from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from benchmarks.runner.grade import CheckResult
from benchmarks.runner.jev import estimated_tokens
from benchmarks.runner.judge import JudgeError
from benchmarks.runner.report import RepRecord
from benchmarks.runner.triage import (
    LABELS,
    STATE_BUDGET,
    TRIAGE_SYSTEM,
    cause_question,
    claimed_done,
    code_cause,
    message_entries,
    triage_reference,
    triage_state,
)
from benchmarks.tests.test_judge import FakeJudge

PASSED = CheckResult("bash: pytest -q", True, "")
FAILED = CheckResult("bash: pytest -q", False, "exit 1\n1 failed")


def failure(**changes: Any) -> RepRecord:
    base = RepRecord(
        case="fix-failing-test",
        rep=0,
        outcome="fail",
        checks=(FAILED,),
        judge_met=1,
        judge_why="",
        status="done",
        stop_reason="end_turn",
        turns=4,
    )
    return replace(base, **changes)


@pytest.mark.parametrize("stop_reason", ["max_turns", "budget", "context"])
def test_running_out_is_labelled_by_code(stop_reason: str) -> None:
    assert code_cause(failure(stop_reason=stop_reason), 3) == "ran_out"


def test_a_judge_gate_failure_is_labelled_by_code() -> None:
    rec = failure(checks=(PASSED,), judge_met=2)
    assert code_cause(rec, 3) == "judge_gate"


def test_a_failed_check_is_left_for_the_models() -> None:
    assert code_cause(failure(judge_met=0), 3) is None


def test_an_ungated_case_is_never_a_judge_gate_failure() -> None:
    assert code_cause(failure(checks=(PASSED,), judge_met=0), None) is None


def test_claimed_done_is_done_with_a_failing_check() -> None:
    assert claimed_done(failure())
    assert not claimed_done(failure(status="blocked"))
    assert not claimed_done(failure(checks=(PASSED,)))


def test_the_transcript_clips_text_and_tool_arguments() -> None:
    message = {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "x" * 1500},
            {
                "type": "tool_use",
                "id": "t1",
                "name": "bash",
                "input": {"cmd": "y" * 1500},
            },
        ],
    }
    text, call = message_entries(message)
    assert text == {"role": "assistant", "type": "text", "text": "x" * 1000}
    assert call["tool"] == "bash"
    assert call["args"].startswith('{"cmd": "yyy')
    assert len(call["args"]) == 1000


def test_the_transcript_drops_thinking_and_replaces_images() -> None:
    message = {
        "role": "user",
        "content": [
            {"type": "thinking", "thinking": "secret plan"},
            {
                "type": "tool_result",
                "tool_use_id": "t1",
                "content": "read 1 image",
                "image": {"source": {"data": "iVBORw0KGgo="}},
            },
            {"type": "image", "source": {"data": "iVBORw0KGgo="}},
        ],
    }
    entries = message_entries(message)
    assert entries == [
        {"role": "user", "type": "tool_result", "text": "read 1 image\n[image]"},
        {"role": "user", "type": "image", "text": "[image]"},
    ]
    assert "secret plan" not in str(entries)
    assert "iVBOR" not in str(entries)


def text_message(role: str, text: str) -> dict[str, Any]:
    return {"role": role, "content": [{"type": "text", "text": text}]}


def test_a_small_state_keeps_the_whole_transcript() -> None:
    messages = [text_message("user", "Fix it."), text_message("assistant", "Done.")]
    state = triage_state("Fix it.", failure(), "a diff", "Done.", messages)
    assert state is not None
    assert state["failed_checks"] == [{"label": FAILED.label, "detail": FAILED.detail}]
    assert [e["text"] for e in state["transcript"]] == ["Fix it.", "Done."]


def test_a_long_transcript_keeps_the_first_user_message_and_drops_the_oldest() -> None:
    filler = "z" * 900
    messages = [text_message("user", "Fix it.")] + [
        text_message("assistant" if n % 2 else "user", f"{n} {filler}")
        for n in range(1, 200)
    ]
    state = triage_state("Fix it.", failure(), "a diff", "Done.", messages)
    assert state is not None
    assert estimated_tokens(state) <= STATE_BUDGET
    transcript = state["transcript"]
    assert transcript[0]["text"] == "Fix it."
    omitted = transcript[1]
    assert omitted["type"] == "omitted"
    dropped = int(omitted["text"].strip("[]").split()[0])
    assert transcript[2]["text"].startswith(f"{dropped + 1} ")
    assert transcript[-1]["text"].startswith("199 ")


def test_a_diff_too_large_to_fit_is_none() -> None:
    messages = [text_message("user", "Fix it.")]
    huge = "+" * (STATE_BUDGET * 4)
    assert triage_state("Fix it.", failure(), huge, "", messages) is None


def test_no_session_means_an_empty_transcript() -> None:
    state = triage_state("Fix it.", failure(), "a diff", "", [])
    assert state is not None
    assert state["transcript"] == []


def test_jev_and_the_reference_get_the_same_labels() -> None:
    assert cause_question()["criteria"] == LABELS
    for label, text in LABELS.items():
        assert f"- {label}: {text}" in TRIAGE_SYSTEM


async def test_the_reference_reply_is_parsed() -> None:
    judge = FakeJudge('Here you go: {"cause": "wrong_change"}')
    cause, attempts = await triage_reference({"task": "t"}, transport=judge)
    assert cause == "wrong_change"
    assert len(attempts) == 1
    assert '"task": "t"' in judge.calls[0][0].content[0]["text"]


async def test_an_unknown_label_is_a_judge_error_after_two_attempts() -> None:
    # The second reply's cause is a list: unhashable, so a bare `in LABELS`
    # would raise TypeError instead of JudgeError.
    judge = FakeJudge('{"cause": "flaky"}', '{"cause": ["no_change"]}')
    with pytest.raises(JudgeError, match="unknown cause") as info:
        await triage_reference({"task": "t"}, transport=judge)
    assert len(info.value.attempts) == 2


async def test_an_empty_reply_is_retried_once() -> None:
    judge = FakeJudge("", '{"cause": "no_change"}')
    cause, attempts = await triage_reference({"task": "t"}, transport=judge)
    assert cause == "no_change"
    assert [a.text for a in attempts] == ["", '{"cause": "no_change"}']
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest -q benchmarks/tests/test_triage.py`
Expected: collection error, `ModuleNotFoundError: No module named 'benchmarks.runner.triage'`.

- [ ] **Step 3: Write the module**

Create `benchmarks/runner/triage.py`:

```python
"""Why a failed repetition failed.

Two causes are exact and decided here by code, since counting is a documented
Jev weakness. The rest are labelled by Jev and by the reference model, from one
state both of them see.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from benchmarks.runner.config import Config
from benchmarks.runner.jev import estimated_tokens
from benchmarks.runner.judge import (
    JUDGE_ATTEMPTS,
    JUDGE_MAX_TOKENS,
    JudgeAttempt,
    JudgeError,
    _extract_json,
)
from benchmarks.runner.report import RepRecord
from nare.session import Message
from nare.transport import Transport, make_transport

LABELS: dict[str, str] = {
    "asked_when_clear": (
        "Stopped to ask, though the request had one reasonable reading"
    ),
    "did_not_ask": "Went ahead on a request that needed a question first",
    "no_change": (
        "Ended without making the change the task required, and without asking"
    ),
    "wrong_change": (
        "Changed the right place, but the change does not do what was asked"
    ),
    "incomplete_change": (
        "Did part of the task, such as renaming in some files and not others"
    ),
    "off_target_edit": (
        "Edited the wrong file, or one the task said to leave alone, such as the tests"
    ),
    "other": "None of the above",
}
RAN_OUT = frozenset({"max_turns", "budget", "context"})
CLIP = 1_000
# Leaves room under Jev's 32k for the question itself.
STATE_BUDGET = 24_000

TRIAGE_SYSTEM = (
    "You decide why an agent's attempt at a coding task failed.\n\n"
    "You are given, as JSON: the task, the agent's final status, the checks "
    "that failed, the diff it produced, its closing message, and a transcript "
    "of the session.\n\n"
    "Pick the one label that best explains the failure:\n"
    + "\n".join(f"- {label}: {text}" for label, text in LABELS.items())
    + '\n\nReply with JSON only, in exactly this shape:\n{"cause": "<label>"}'
)


def code_cause(rec: RepRecord, min_met: int | None) -> str | None:
    """The causes code can decide exactly, or None for a model to label."""
    if rec.stop_reason in RAN_OUT:
        return "ran_out"
    if (
        all(check.passed for check in rec.checks)
        and min_met is not None
        and rec.judge_met is not None
        and rec.judge_met < min_met
    ):
        return "judge_gate"
    return None


def claimed_done(rec: RepRecord) -> bool:
    """A fact beside the cause, not a cause: it can accompany any of them."""
    return rec.status == "done" and any(not check.passed for check in rec.checks)


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            "[image]" if part.get("type") == "image" else str(part.get("text", ""))
            for part in content
            if isinstance(part, dict)
        )
    return ""


def _entry(role: str, block: dict[str, Any]) -> dict[str, str] | None:
    kind = block.get("type")
    if kind == "text":
        return {"role": role, "type": "text", "text": str(block.get("text", ""))[:CLIP]}
    if kind == "tool_result":
        text = _text_of(block.get("content"))[:CLIP]
        if "image" in block:
            text = f"{text}\n[image]".lstrip()
        return {"role": role, "type": "tool_result", "text": text}
    if kind == "tool_use":
        return {
            "role": role,
            "type": "tool_use",
            "tool": str(block.get("name", "")),
            "args": json.dumps(block.get("input", {}))[:CLIP],
        }
    if kind == "image":
        return {"role": role, "type": "image", "text": "[image]"}
    # thinking, redacted_thinking, and anything newer: not evidence.
    return None


def message_entries(message: Any) -> list[dict[str, str]]:
    if not isinstance(message, dict):
        return []
    role = str(message.get("role", ""))
    blocks = message.get("content")
    if isinstance(blocks, str):
        return [{"role": role, "type": "text", "text": blocks[:CLIP]}]
    if not isinstance(blocks, list):
        return []
    entries = (_entry(role, b) for b in blocks if isinstance(b, dict))
    return [e for e in entries if e is not None]


def triage_state(
    prompt: str,
    rec: RepRecord,
    diff: str,
    closing: str,
    messages: Sequence[Any],
) -> dict[str, Any] | None:
    """The state both triage judges see, or None when it cannot fit.

    Over budget, the first user message is kept and the oldest of the rest are
    dropped, whole, until it fits.
    """
    first = next(
        (
            n
            for n, m in enumerate(messages)
            if isinstance(m, dict) and m.get("role") == "user"
        ),
        None,
    )
    head = [] if first is None else message_entries(messages[first])
    rest = [message_entries(m) for n, m in enumerate(messages) if n != first]
    base: dict[str, Any] = {
        "task": prompt,
        "status": rec.status,
        "failed_checks": [
            {"label": c.label, "detail": c.detail} for c in rec.checks if not c.passed
        ],
        "diff": diff,
        "closing_message": closing,
    }
    for dropped in range(len(rest) + 1):
        omitted = (
            [{"type": "omitted", "text": f"[{dropped} messages omitted]"}]
            if dropped
            else []
        )
        kept = [entry for entries in rest[dropped:] for entry in entries]
        state = {**base, "transcript": head + omitted + kept}
        if estimated_tokens(state) <= STATE_BUDGET:
            return state
    return None


def cause_question() -> dict[str, Any]:
    """Jev's one triage question. The criteria are the same descriptions the
    reference reads in TRIAGE_SYSTEM."""
    return {
        "type": "choice",
        "instructions": (
            "Why did the agent's attempt at the task fail? Pick the one label "
            "that best explains it."
        ),
        "criteria": dict(LABELS),
    }


def triage_transport(config: Config, model: str) -> Transport:
    return make_transport(
        config.provider,
        model=model,
        base_url=config.base_url,
        api_key=config.api_key,
        max_tokens=JUDGE_MAX_TOKENS,
        system=TRIAGE_SYSTEM,
    )


def parse_cause(text: str) -> str:
    cause = _extract_json(text).get("cause")
    if not isinstance(cause, str) or cause not in LABELS:
        raise JudgeError(f"triage returned an unknown cause: {cause!r}")
    return cause


async def _ask(prompt: str, transport: Transport) -> tuple[str, JudgeAttempt]:
    """One attempt. Every failure raises JudgeError carrying this attempt."""
    try:
        reply = await transport.turn(
            [Message(role="user", content=[{"type": "text", "text": prompt}])], []
        )
    except Exception as exc:
        why = f"{type(exc).__name__}: {exc}"
        raise JudgeError(why, [JudgeAttempt("transport error", why)]) from exc
    text = "\n".join(
        block.get("text", "") for block in reply.content if block.get("type") == "text"
    )
    attempt = JudgeAttempt(reply.stop_reason, text)
    try:
        return parse_cause(text), attempt
    except JudgeError as exc:
        raise JudgeError(str(exc), [attempt]) from exc


async def triage_reference(
    state: dict[str, Any], *, transport: Transport
) -> tuple[str, tuple[JudgeAttempt, ...]]:
    """The reference's label, with the same two attempts score() makes."""
    prompt = json.dumps(state, indent=2)
    attempts: list[JudgeAttempt] = []
    last: JudgeError | None = None
    for _ in range(JUDGE_ATTEMPTS):
        try:
            cause, attempt = await _ask(prompt, transport)
        except JudgeError as exc:
            attempts += exc.attempts
            last = exc
            continue
        return cause, (*attempts, attempt)
    raise JudgeError(str(last), attempts) from last
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q benchmarks/tests/test_triage.py`
Expected: `17 passed`.

- [ ] **Step 5: Lint, type-check and commit**

Run: `uv run ruff format benchmarks && uv run ruff check benchmarks && uv run mypy benchmarks`
Expected: clean.

```bash
git add benchmarks/runner/triage.py benchmarks/tests/test_triage.py
git commit -m "feat(bench): label why a failed rep failed

ran_out and judge_gate are decided by code; the rest get one label from the
same seven descriptions, given to Jev as a choice and to the reference as
TRIAGE_SYSTEM. The transcript is clipped and trimmed to fit Jev. Part of #46.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Agreement and the summary

**Files:**
- Create: `benchmarks/runner/agreement.py`
- Test: `benchmarks/tests/test_agreement.py`

**Interfaces:**
- Consumes: `Case` (`case.py`), `rep_outcome` (`grade.py`), `RepRecord` (`report.py`). Nothing that touches the network.
- Produces:
  - `TaskName = Literal["claims", "triage"]`, `JudgeName = Literal["jev", "llm", "reference", "code"]`
  - `CUTOFFS = (0.5, 0.6, 0.7, 0.8, 0.9)`, `BANDS = (0.6, 0.7, 0.8, 0.9)`, `TOO_LARGE = "too_large"`, `GROUND_TRUTH: str`
  - `Row(case, rep, task, judge, model, verdicts=None, cause=None, probabilities=None, confidence=None, claimed_done=None, tokens=0, cost=None, cost_estimated=False, latency_s=0.0, attempts=0, error=None)`: one `.jsonl` line, its fields exactly the spec's section 8 table
  - `Skip(case: str, rep: int, reason: str)`, `Header(stamp, model, judge_model, reference_model)`
  - `render_summary(header: Header, rows: Sequence[Row], skips: Sequence[Skip], cases: Mapping[str, Case], records: Sequence[RepRecord]) -> str`
  - Metric functions, each tested on its own: `claim_pairs`, `agreement`, `band`, `outcome_flips`, `triage_pairs`, `confusion`, `confidence_split`, `percentile`, `spend`

This task is independent of Tasks 2 and 3.

- [ ] **Step 1: Write the failing tests**

Create `benchmarks/tests/test_agreement.py`:

```python
from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from benchmarks.runner.agreement import (
    GROUND_TRUTH,
    TOO_LARGE,
    ClaimPair,
    Header,
    Row,
    Skip,
    agreement,
    band,
    claim_pairs,
    confidence_split,
    confusion,
    outcome_flips,
    percentile,
    render_summary,
    spend,
)
from benchmarks.runner.grade import CheckResult
from benchmarks.runner.report import RepRecord
from benchmarks.tests.test_judge import CASE

PASSED = CheckResult("bash: pytest -q", True, "")
FAILED = CheckResult("bash: pytest -q", False, "exit 1")
CASES = {CASE.id: CASE}  # two claims, min_met 2


def claims(
    judge: Any, rep: int, verdicts: Any, model: str | None = "m", **extra: Any
) -> Row:
    return Row(CASE.id, rep, "claims", judge, model, verdicts=verdicts, **extra)


def record(rep: int, checks: tuple[CheckResult, ...] = (PASSED,)) -> RepRecord:
    return RepRecord(CASE.id, rep, "pass", checks, 2, "", "done", "end_turn", 3)


def test_claim_pairs_line_up_each_claim_with_the_reference() -> None:
    rows = [
        claims("reference", 0, [True, False]),
        claims("jev", 0, [0.9, 0.7]),
    ]
    assert claim_pairs(rows, "jev") == [
        ClaimPair(CASE.id, 0, 0.9, True),
        ClaimPair(CASE.id, 1, 0.7, False),
    ]


def test_a_judge_error_drops_only_that_judges_pairs() -> None:
    rows = [
        claims("reference", 0, [True, True]),
        claims("jev", 0, None, error=TOO_LARGE),
        claims("llm", 0, [True, False]),
    ]
    assert claim_pairs(rows, "jev") == []
    assert len(claim_pairs(rows, "llm")) == 2


def test_a_reference_error_drops_the_rep_for_every_judge() -> None:
    rows = [
        claims("reference", 0, None, error="judge failed"),
        claims("jev", 0, [0.9, 0.1]),
        claims("llm", 0, [True, False]),
    ]
    assert claim_pairs(rows, "jev") == []
    assert claim_pairs(rows, "llm") == []


PAIRS = [
    ClaimPair("c", 0, 0.95, True),
    ClaimPair("c", 1, 0.65, True),
    ClaimPair("c", 2, 0.55, False),
    ClaimPair("c", 3, 0.10, False),
]


def test_agreement_at_a_cutoff() -> None:
    assert agreement(PAIRS, 0.5) == (3, 4)  # 0.55 reads as true, wrongly
    assert agreement(PAIRS, 0.6) == (4, 4)
    assert agreement(PAIRS, 0.7) == (3, 4)  # 0.65 now reads as false


def test_the_llm_judges_booleans_agree_at_one_half() -> None:
    pairs = [ClaimPair("c", 0, 1.0, True), ClaimPair("c", 1, 0.0, True)]
    assert agreement(pairs) == (1, 2)


def test_a_band_leaves_unsure_claims_unanswered() -> None:
    # At 0.7, 0.65 and 0.55 sit between 0.3 and 0.7.
    assert band(PAIRS, 0.7) == (2, 4, 2)
    # At 0.6, only 0.55 is unsure; 0.65 answers true and agrees.
    assert band(PAIRS, 0.6) == (3, 4, 3)


def test_a_band_edge_counts_as_answered() -> None:
    """1 - 0.9 is 0.0999... in floating point; 0.1 must still answer false."""
    assert band([ClaimPair("c", 0, 0.1, False)], 0.9) == (1, 1, 1)


def test_an_outcome_flips_when_the_gate_reads_differently() -> None:
    rows = [
        claims("reference", 0, [True, True]),  # 2 met: pass
        claims("jev", 0, [0.9, 0.55]),  # at 0.5: pass; at 0.6: fail
        claims("llm", 0, [True, False]),  # fail
    ]
    records = [record(0)]
    assert outcome_flips(rows, "llm", CASES, records) == (1, 1)
    assert outcome_flips(rows, "jev", CASES, records, 0.5) == (0, 1)
    assert outcome_flips(rows, "jev", CASES, records, 0.6) == (1, 1)


def test_a_rep_whose_checks_failed_cannot_flip() -> None:
    rows = [claims("reference", 0, [True, True]), claims("llm", 0, [False, False])]
    assert outcome_flips(rows, "llm", CASES, [record(0, (FAILED,))]) == (0, 1)


def test_an_ungated_case_has_no_flips_to_count() -> None:
    assert CASE.judge is not None
    ungated = {CASE.id: replace(CASE, judge=replace(CASE.judge, min_met=None))}
    rows = [claims("reference", 0, [True, True]), claims("llm", 0, [False, False])]
    assert outcome_flips(rows, "llm", ungated, [record(0)]) == (0, 0)


def triage(
    judge: Any, rep: int, cause: str | None, model: str | None = "m", **extra: Any
) -> Row:
    return Row(CASE.id, rep, "triage", judge, model, cause=cause, **extra)


TRIAGE = [
    triage("reference", 0, "no_change"),
    triage("jev", 0, "no_change", confidence=0.9),
    triage("reference", 1, "wrong_change"),
    triage("jev", 1, "no_change", confidence=0.4),
    triage("reference", 2, "wrong_change"),
    triage("jev", 2, "wrong_change", confidence=0.8),
    triage("reference", 3, None, error="judge failed"),
    triage("jev", 3, "other", confidence=0.7),
]


def test_the_confusion_table_counts_reference_against_jev() -> None:
    assert confusion(TRIAGE) == {
        ("no_change", "no_change"): 1,
        ("wrong_change", "no_change"): 1,
        ("wrong_change", "wrong_change"): 1,
    }


def test_confidence_is_split_by_agreement() -> None:
    assert confidence_split(TRIAGE) == ([0.9, 0.8], [0.4])


def test_percentile_is_nearest_rank() -> None:
    values = [float(n) for n in range(1, 21)]
    assert percentile(values, 0.95) == 19.0
    assert percentile([3.0], 0.95) == 3.0
    assert percentile([], 0.95) is None


def test_spend_sums_calls_tokens_and_dollars() -> None:
    rows = [
        claims("jev", 0, [0.9], tokens=300, cost=0.01, latency_s=0.2, attempts=1),
        claims("jev", 1, [0.9], tokens=100, cost=0.02, latency_s=0.4, attempts=2),
        claims("jev", 2, None, error=TOO_LARGE),
    ]
    s = spend(rows, "jev")
    assert (s.calls, s.tokens, s.unknown) == (3, 400, 0)
    assert s.dollars == pytest.approx(0.03)
    assert s.median_s == pytest.approx(0.3)
    assert s.p95_s == 0.4


def test_an_unknown_cost_is_counted_not_zeroed() -> None:
    rows = [claims("llm", 0, [True], cost=None, attempts=1, latency_s=1.0)]
    assert spend(rows, "llm").unknown == 1


def test_the_summary_names_every_section_with_its_n() -> None:
    rows = [
        claims("jev", 0, [0.9, 0.55], model="jev-1.13.0", attempts=1, latency_s=0.3),
        claims("llm", 0, [True, False], attempts=1, latency_s=2.0),
        claims("reference", 0, [True, True], attempts=1, latency_s=4.0),
        claims("jev", 1, None, error=TOO_LARGE),
        triage("code", 2, "ran_out", model=None, claimed_done=False),
        *[replace(r, claimed_done=True) for r in TRIAGE[:2]],
    ]
    text = render_summary(
        Header("2026-10-08T00-00-00Z", "m", "m", "opus"),
        rows,
        [Skip(CASE.id, 4, "no evidence files")],
        CASES,
        [record(0)],
    )
    assert "compare-judges over results 2026-10-08T00-00-00Z" in text
    assert "jev: jev-1.13.0" in text
    assert "skipped, no evidence files: 1" in text
    assert "too_large: jev claims 1" in text
    assert GROUND_TRUTH in text
    assert "llm judge                          50% (1/2)" in text
    assert "jev, P(yes) >= 0.5                 100% (2/2)" in text
    assert "coverage 50% (1/2), agreement 100% (1/1)" in text
    assert "The fix is in bar.py." in text
    assert "labelled by code: ran_out 1" in text
    assert "claimed done with a failing check: 1 of 2 triaged failures" in text
    assert "~ estimated" not in text


def test_an_estimated_cost_is_marked() -> None:
    rows = [claims("jev", 0, [0.9], cost=0.00004, cost_estimated=True, attempts=1)]
    text = render_summary(Header("s", "m", "m", "opus"), rows, [], CASES, [])
    assert "~$0.000040" in text
    assert "~ estimated from Jev's input tokens" in text
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest -q benchmarks/tests/test_agreement.py`
Expected: collection error, `ModuleNotFoundError: No module named 'benchmarks.runner.agreement'`.

- [ ] **Step 3: Write the module**

Create `benchmarks/runner/agreement.py`:

```python
"""Every number compare-judges prints. Pure: handed rows, does arithmetic.

Agreement is with the reference model, not with ground truth: nobody
hand-labels anything, so every figure here says how close a judge gets to the
reference, never whether either is right.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from benchmarks.runner.case import Case
from benchmarks.runner.grade import rep_outcome
from benchmarks.runner.report import RepRecord

TaskName = Literal["claims", "triage"]
JudgeName = Literal["jev", "llm", "reference", "code"]
CUTOFFS = (0.5, 0.6, 0.7, 0.8, 0.9)
BANDS = (0.6, 0.7, 0.8, 0.9)
TOO_LARGE = "too_large"
GROUND_TRUTH = "Agreement is with the reference model, not ground truth."


@dataclass(frozen=True)
class Row:
    """One line of compare-<cstamp>.jsonl: one repetition, task, and judge."""

    case: str
    rep: int
    task: TaskName
    judge: JudgeName
    model: str | None  # for Jev, the pinned version it reported
    verdicts: list[float] | list[bool] | None = None  # claims: P(yes), or booleans
    cause: str | None = None  # triage
    probabilities: dict[str, float] | None = None  # triage, Jev only
    confidence: float | None = None  # triage, Jev only
    claimed_done: bool | None = None  # triage
    tokens: int = 0
    cost: float | None = None  # None when unknown
    cost_estimated: bool = False
    latency_s: float = 0.0
    attempts: int = 0
    error: str | None = None


@dataclass(frozen=True)
class Skip:
    case: str
    rep: int
    reason: str


@dataclass(frozen=True)
class Header:
    stamp: str
    model: str
    judge_model: str
    reference_model: str


@dataclass(frozen=True)
class ClaimPair:
    """One claim both a judge and the reference answered."""

    case: str
    index: int
    value: float  # P(yes) for Jev; 1.0 or 0.0 for the LLM judge
    reference: bool


def _answered(
    rows: Sequence[Row], task: TaskName, judge: JudgeName
) -> dict[tuple[str, int], Row]:
    return {
        (r.case, r.rep): r
        for r in rows
        if r.task == task and r.judge == judge and r.error is None
    }


def claim_pairs(rows: Sequence[Row], judge: JudgeName) -> list[ClaimPair]:
    """A judge that could not answer is dropped, never counted as wrong. A
    reference that could not answer drops the repetition for every judge."""
    reference = _answered(rows, "claims", "reference")
    other = _answered(rows, "claims", judge)
    pairs: list[ClaimPair] = []
    for key in sorted(reference.keys() & other.keys()):
        mine, theirs = other[key].verdicts or [], reference[key].verdicts or []
        for index, (value, truth) in enumerate(zip(mine, theirs, strict=True)):
            pairs.append(ClaimPair(key[0], index, float(value), bool(truth)))
    return pairs


def agreement(pairs: Sequence[ClaimPair], cutoff: float = 0.5) -> tuple[int, int]:
    """(agreeing, n), reading P(yes) >= cutoff as true."""
    return sum((p.value >= cutoff) == p.reference for p in pairs), len(pairs)


def band(pairs: Sequence[ClaimPair], t: float) -> tuple[int, int, int]:
    """(answered, n, agreeing among answered). Claims strictly between 1 - t
    and t are left unanswered. Rounded so 1 - 0.9 is 0.1, not 0.0999..."""
    low = round(1 - t, 9)
    answered = [p for p in pairs if p.value >= t or p.value <= low]
    agreed = sum((p.value >= t) == p.reference for p in answered)
    return len(answered), len(pairs), agreed


def outcome_flips(
    rows: Sequence[Row],
    judge: JudgeName,
    cases: Mapping[str, Case],
    records: Sequence[RepRecord],
    cutoff: float = 0.5,
) -> tuple[int, int]:
    """(flipped, n): gated repetitions whose rep_outcome, from the recorded
    checks and this judge's verdicts, differs from the reference's."""
    reference = _answered(rows, "claims", "reference")
    other = _answered(rows, "claims", judge)
    recorded = {(r.case, r.rep): r for r in records}
    flipped = n = 0
    for key in sorted(reference.keys() & other.keys()):
        case, rec = cases.get(key[0]), recorded.get(key)
        if rec is None or case is None or case.judge is None:
            continue
        if case.judge.min_met is None:
            continue
        mine = [float(v) >= cutoff for v in other[key].verdicts or []]
        theirs = [bool(v) for v in reference[key].verdicts or []]
        n += 1
        if rep_outcome(rec.checks, case.judge, mine) != rep_outcome(
            rec.checks, case.judge, theirs
        ):
            flipped += 1
    return flipped, n


def triage_pairs(rows: Sequence[Row]) -> list[tuple[Row, Row]]:
    """(jev, reference) for every failure both labelled."""
    reference = _answered(rows, "triage", "reference")
    jev = _answered(rows, "triage", "jev")
    return [(jev[k], reference[k]) for k in sorted(jev.keys() & reference.keys())]


def confusion(rows: Sequence[Row]) -> Counter[tuple[str, str]]:
    """Counts keyed (reference label, Jev label)."""
    return Counter((str(r.cause), str(j.cause)) for j, r in triage_pairs(rows))


def confidence_split(rows: Sequence[Row]) -> tuple[list[float], list[float]]:
    """Jev's confidence where it agrees with the reference, and where it does not."""
    agree: list[float] = []
    disagree: list[float] = []
    for jev, reference in triage_pairs(rows):
        if jev.confidence is not None:
            (agree if jev.cause == reference.cause else disagree).append(jev.confidence)
    return agree, disagree


def percentile(values: Sequence[float], q: float) -> float | None:
    """Nearest rank: the smallest value with at least q of the values at or below."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(1, math.ceil(q * len(ordered))) - 1]


@dataclass(frozen=True)
class Spend:
    calls: int
    tokens: int
    dollars: float
    estimated: bool
    unknown: int  # rows that made a call but whose cost is unknown
    median_s: float | None
    p95_s: float | None


def spend(rows: Sequence[Row], judge: JudgeName) -> Spend:
    """Rows that made no call, such as too_large, cost nothing and take no time."""
    called = [r for r in rows if r.judge == judge and r.attempts]
    latencies = [r.latency_s for r in called]
    return Spend(
        calls=sum(r.attempts for r in called),
        tokens=sum(r.tokens for r in called),
        dollars=sum(r.cost for r in called if r.cost is not None),
        estimated=any(r.cost_estimated for r in called),
        unknown=sum(r.cost is None for r in called),
        median_s=statistics.median(latencies) if latencies else None,
        p95_s=percentile(latencies, 0.95),
    )


def pct(k: int, n: int) -> str:
    return f"{k / n:.0%} ({k}/{n})" if n else "- (0/0)"


def _seconds(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def _dollars(s: Spend) -> str:
    if s.unknown and not s.dollars:
        return "-"
    return f"{'~' if s.estimated else ''}${s.dollars:.6f}"


def _header(header: Header, rows: Sequence[Row], skips: Sequence[Skip]) -> list[str]:
    pinned = sorted({str(r.model) for r in rows if r.judge == "jev" and not r.error})
    compared = {(r.case, r.rep) for r in rows}
    lines = [
        f"compare-judges over results {header.stamp}",
        f"model under test: {header.model}",
        f"llm judge: {header.judge_model}",
        f"reference: {header.reference_model}",
        f"jev: {', '.join(pinned) or 'no answer'}",
        f"repetitions compared: {len(compared)}",
    ]
    for reason, count in sorted(Counter(s.reason for s in skips).items()):
        lines.append(f"skipped, {reason}: {count}")
    too_large = Counter(f"{r.judge} {r.task}" for r in rows if r.error == TOO_LARGE)
    failed = Counter(
        f"{r.judge} {r.task}" for r in rows if r.error and r.error != TOO_LARGE
    )
    for what, counts in (("too_large", too_large), ("judge errors", failed)):
        if counts:
            listed = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
            lines.append(f"{what}: {listed}")
    lines.append(
        "A judge's error drops that repetition from its own figures; "
        "a reference error drops it from every judge's."
    )
    lines.append(GROUND_TRUTH)
    return lines


def _claims(rows: Sequence[Row], cases: Mapping[str, Case]) -> list[str]:
    jev, llm = claim_pairs(rows, "jev"), claim_pairs(rows, "llm")
    lines = ["", "Claim agreement with the reference"]
    lines.append(f"  {'llm judge':<34} {pct(*agreement(llm))}")
    for t in CUTOFFS:
        lines.append(f"  {f'jev, P(yes) >= {t}':<34} {pct(*agreement(jev, t))}")
    for t in BANDS:
        answered, n, agreed = band(jev, t)
        label = f"jev, unsure between {round(1 - t, 9)} and {t}"
        lines.append(
            f"  {label:<34} coverage {pct(answered, n)}, "
            f"agreement {pct(agreed, answered)}"
        )
    lines.append("  Jev gives no reason for a verdict; that is not counted against it.")
    lines.append("")
    lines.append("  per claim: jev at 0.5, then the llm judge")
    keys = sorted({(p.case, p.index) for p in jev + llm})
    for case_id, index in keys:
        mine = [p for p in jev if (p.case, p.index) == (case_id, index)]
        theirs = [p for p in llm if (p.case, p.index) == (case_id, index)]
        case = cases.get(case_id)
        text = case.judge.assertions[index] if case and case.judge else "?"
        lines.append(
            f"  {case_id} #{index + 1}  jev {pct(*agreement(mine))}  "
            f"llm {pct(*agreement(theirs))}"
        )
        lines.append(f"    {text}")
    return lines


def _flips(
    rows: Sequence[Row], cases: Mapping[str, Case], records: Sequence[RepRecord]
) -> list[str]:
    lines = ["", "Outcome flips against the reference, gated repetitions only"]
    flipped, n = outcome_flips(rows, "llm", cases, records)
    lines.append(f"  {'llm judge':<34} {flipped} of {n}")
    for t in CUTOFFS:
        flipped, n = outcome_flips(rows, "jev", cases, records, t)
        lines.append(f"  {f'jev, P(yes) >= {t}':<34} {flipped} of {n}")
    return lines


def _triage(rows: Sequence[Row]) -> list[str]:
    lines = ["", "Triage"]
    by_code = Counter(str(r.cause) for r in rows if r.judge == "code")
    coded = ", ".join(f"{k} {v}" for k, v in sorted(by_code.items()))
    lines.append(f"  labelled by code: {coded or 'none'}")
    triaged = {
        (r.case, r.rep): bool(r.claimed_done) for r in rows if r.task == "triage"
    }
    lines.append(
        f"  claimed done with a failing check: "
        f"{sum(triaged.values())} of {len(triaged)} triaged failures"
    )
    table = confusion(rows)
    labels = sorted({label for pair in table for label in pair})
    if labels:
        lines.append("  jev (columns) against the reference (rows), counts:")
        lines.append(
            f"    {'':<20}" + "".join(f"{n:>4}" for n in range(1, len(labels) + 1))
        )
        for n, ref in enumerate(labels, start=1):
            counts = "".join(f"{table[(ref, j)]:>4}" for j in labels)
            lines.append(f"  {n:>2} {ref:<19}{counts}")
    agree, disagree = confidence_split(rows)
    lines.append(
        "  jev's median confidence: "
        f"{_median(agree)} agreeing (n={len(agree)}), "
        f"{_median(disagree)} disagreeing (n={len(disagree)})"
    )
    return lines


def _median(values: Sequence[float]) -> str:
    return f"{statistics.median(values):.2f}" if values else "-"


def _spend(rows: Sequence[Row]) -> list[str]:
    lines = ["", "Cost and speed"]
    lines.append(
        f"  {'judge':<10}{'calls':>6}{'tokens':>10}{'dollars':>12}"
        f"{'no cost':>9}{'median s':>10}{'p95 s':>7}"
    )
    estimated = unknown = False
    judges: tuple[JudgeName, ...] = ("jev", "llm", "reference")
    for judge in judges:
        s = spend(rows, judge)
        estimated = estimated or s.estimated
        unknown = unknown or bool(s.unknown)
        lines.append(
            f"  {judge:<10}{s.calls:>6}{s.tokens:>10,}{_dollars(s):>12}"
            f"{s.unknown:>9}{_seconds(s.median_s):>10}{_seconds(s.p95_s):>7}"
        )
    if unknown:
        lines.append("  no cost: answers whose cost the backend did not report")
    if estimated:
        lines.append(
            "  ~ estimated from Jev's input tokens: the proxy reported no cost"
        )
    return lines


def render_summary(
    header: Header,
    rows: Sequence[Row],
    skips: Sequence[Skip],
    cases: Mapping[str, Case],
    records: Sequence[RepRecord],
) -> str:
    return "\n".join(
        _header(header, rows, skips)
        + _claims(rows, cases)
        + _flips(rows, cases, records)
        + _triage(rows)
        + _spend(rows)
    )
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q benchmarks/tests/test_agreement.py`
Expected: `17 passed`.

- [ ] **Step 5: Lint, type-check and commit**

Run: `uv run ruff format benchmarks && uv run ruff check benchmarks && uv run mypy benchmarks`
Expected: clean.

```bash
git add benchmarks/runner/agreement.py benchmarks/tests/test_agreement.py
git commit -m "feat(bench): compute judge agreement and render the summary

Pure, like grade.py and report.py: agreement with the reference at each
cutoff and band, outcome flips, the triage confusion table, and cost and
latency per judge, every percentage with its n. Part of #46.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Running every judge over kept evidence

**Files:**
- Create: `benchmarks/runner/compare.py`
- Test: `benchmarks/tests/test_compare.py`

**Interfaces:**
- Consumes: `Row`, `Skip`, `JudgeName`, `TOO_LARGE` (Task 4); `Jev`, `JevError`, `Noul`, `Choice`, `Answer`, `JEV_STATE_LIMIT`, `estimated_tokens` (Task 2); `cause_question`, `claimed_done`, `code_cause`, `triage_reference`, `triage_state` (Task 3); `score`, `JudgeError` (`judge.py`); the files Task 1 writes.
- Produces:
  - `CLAIM_RULES: str`, `CLAIM_CRITERIA: dict[str, str]`
  - `Evidence(diff: str, closing: str, messages: list[Any])`
  - `load_evidence(results_dir: Path, rec: RepRecord) -> Evidence | None`
  - `select_work(records, cases: Mapping[str, Case], results_dir: Path, only: Collection[str] | None = None) -> tuple[list[tuple[Case, RepRecord, Evidence]], list[Skip]]`
  - `claims_state(case, evidence) -> dict[str, str]`, `claims_questions(case) -> dict[str, dict[str, Any]]`, `too_large(state, questions) -> bool`
  - `RecordingTransport(inner: Transport)`, a `Transport` with `take() -> tuple[int, float | None]`
  - `Judges(jev, llm, llm_model, reference, reference_triage, reference_model, clock=time.monotonic)`
  - `async compare_rep(case, rec, evidence, judges) -> list[Row]`
  - `async run_comparison(work, judges, out: Path) -> list[Row]`

- [ ] **Step 1: Write the failing tests**

Create `benchmarks/tests/test_compare.py`. Task 6 adds the end-to-end tests and three imports to this file.

```python
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from benchmarks.runner.agreement import TOO_LARGE, Row, Skip
from benchmarks.runner.compare import (
    CLAIM_RULES,
    Evidence,
    Judges,
    RecordingTransport,
    claims_questions,
    claims_state,
    compare_rep,
    load_evidence,
    run_comparison,
    select_work,
    too_large,
)
from benchmarks.runner.grade import CheckResult
from benchmarks.runner.jev import (
    JEV_STATE_LIMIT,
    Answer,
    Choice,
    Jev,
    JevError,
    JevReply,
    Noul,
)
from benchmarks.runner.report import RepRecord
from benchmarks.tests.test_judge import CASE, FakeJudge
from nare.session import Message, Usage
from nare.transport import Reply

PASSED = CheckResult("bash: pytest -q", True, "")
FAILED = CheckResult("bash: pytest -q", False, "exit 1\n1 failed")
EVIDENCE = Evidence("diff --git a/bar.py", "Fixed it.", [])


class FakeJev:
    """Answers every noul with `p_yes` and every choice with `cause`. The
    call numbered `fail_on`, counting from 1, raises instead."""

    model = "jev-latest"

    def __init__(
        self, p_yes: float = 0.9, cause: str = "no_change", fail_on: int | None = None
    ) -> None:
        self.p_yes, self.cause, self.fail_on = p_yes, cause, fail_on
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    async def ask(
        self, state: Any, questions: Mapping[str, Mapping[str, Any]]
    ) -> JevReply:
        self.calls.append((state, dict(questions)))
        if len(self.calls) == self.fail_on:
            raise JevError("jev returned HTTP 500", "upstream down", latency_s=0.5)
        answers: dict[str, Answer] = {
            qid: (
                Noul(self.p_yes)
                if q["type"] == "noul"
                else Choice(self.cause, {self.cause: 0.8}, 0.8)
            )
            for qid, q in questions.items()
        }
        return JevReply("jev-1.13.0", answers, 120, 0.000005, True, 0.1, 1)


_check: Jev = FakeJev()


def judges(
    jev: FakeJev | None = None,
    llm: str = '{"met": [true, false], "why": "x"}',
    reference: str = '{"met": [true, true], "why": "x"}',
    triage: str = '{"cause": "wrong_change"}',
) -> Judges:
    return Judges(
        jev=jev or FakeJev(),
        llm=RecordingTransport(FakeJudge(llm)),
        llm_model="qwen",
        reference=RecordingTransport(FakeJudge(reference)),
        reference_triage=RecordingTransport(FakeJudge(triage)),
        reference_model="opus",
    )


def rec(rep: int = 0, **changes: Any) -> RepRecord:
    base = RepRecord(
        case=CASE.id,
        rep=rep,
        outcome="pass",
        checks=(PASSED,),
        judge_met=2,
        judge_why="",
        status="done",
        stop_reason="end_turn",
        turns=3,
        model="qwen",
        artifacts=f"stamp/{CASE.id}-{rep}",
    )
    return replace(base, **changes)


def keep(results: Path, rep: int, diff: str = "d", closing: str = "c") -> Path:
    kept = results / "stamp" / f"{CASE.id}-{rep}"
    kept.mkdir(parents=True)
    (kept / "diff.patch").write_bytes(diff.encode("utf-8"))
    (kept / "closing.txt").write_bytes(closing.encode("utf-8"))
    return kept


def test_the_claims_state_is_the_task_the_diff_and_the_closing_message() -> None:
    assert claims_state(CASE, EVIDENCE) == {
        "task": CASE.prompt,
        "diff": "diff --git a/bar.py",
        "closing_message": "Fixed it.",
    }


def test_one_noul_per_claim_in_order() -> None:
    questions = claims_questions(CASE)
    assert list(questions) == ["c1", "c2"]
    first = questions["c1"]
    assert first["type"] == "noul"
    assert first["instructions"] == f"The fix is in bar.py.\n\n{CLAIM_RULES}"
    assert set(first["criteria"]) == {"true", "false"}
    assert "says nothing about it" in first["criteria"]["false"]


def test_the_rules_restate_the_llm_judges_against_the_states_fields() -> None:
    assert "true only if `diff` shows it" in CLAIM_RULES
    assert "`closing_message` is evidence only" in CLAIM_RULES


def test_the_size_estimate_tips_into_too_large() -> None:
    questions = claims_questions(CASE)
    fits = claims_state(CASE, replace(EVIDENCE, diff="+" * 100_000))
    huge = claims_state(CASE, replace(EVIDENCE, diff="+" * JEV_STATE_LIMIT * 4))
    assert not too_large(fits, questions)
    assert too_large(huge, questions)


def test_evidence_is_read_back_exactly(tmp_path: Path) -> None:
    kept = keep(tmp_path, 0, diff="-a\r\n+b\r\n", closing="done\r")
    (kept / "session.json").write_text(json.dumps({"messages": [{"role": "user"}]}))
    evidence = load_evidence(tmp_path, rec())
    assert evidence == Evidence("-a\r\n+b\r\n", "done\r", [{"role": "user"}])


def test_a_rep_without_both_files_has_no_evidence(tmp_path: Path) -> None:
    kept = keep(tmp_path, 0)
    (kept / "closing.txt").unlink()
    assert load_evidence(tmp_path, rec()) is None
    assert load_evidence(tmp_path, rec(artifacts="")) is None


def test_a_missing_or_broken_session_leaves_the_transcript_empty(
    tmp_path: Path,
) -> None:
    keep(tmp_path, 0)
    evidence = load_evidence(tmp_path, rec())
    assert evidence is not None
    assert evidence.messages == []
    (tmp_path / "stamp" / f"{CASE.id}-0" / "session.json").write_text("{trunc")
    evidence = load_evidence(tmp_path, rec())
    assert evidence is not None
    assert evidence.messages == []


def test_work_is_selected_and_every_skip_has_a_reason(tmp_path: Path) -> None:
    keep(tmp_path, 0)
    records = [
        rec(0),
        rec(1),
        rec(2, outcome="error"),
        rec(3, case="retired-case"),
        rec(4, case="other"),
    ]
    work, skips = select_work(records, {CASE.id: CASE}, tmp_path, only=[CASE.id])
    assert [(c.id, r.rep) for c, r, _ in work] == [(CASE.id, 0)]
    assert skips == [
        Skip(CASE.id, 1, "no evidence files"),
        Skip(CASE.id, 2, "errored before its diff was taken"),
    ]
    _, skips = select_work(records, {CASE.id: CASE}, tmp_path)
    assert Skip("retired-case", 3, "case no longer exists") in skips


def reply(text: str, cost: float | None) -> Reply:
    return Reply(
        [{"type": "text", "text": text}], [], Usage(10, 5, 2, 1), "end_turn", cost
    )


class Replies:
    def __init__(self, *replies: Reply) -> None:
        self.replies = list(replies)

    async def context_window(self) -> int | None:
        return None

    async def turn(self, messages: list[Message], tools: list[dict[str, Any]]) -> Reply:
        return self.replies.pop(0)


async def test_the_recording_transport_sums_usage_and_forgets_on_take() -> None:
    recording = RecordingTransport(Replies(reply("a", 0.01), reply("b", 0.02)))
    await recording.turn([], [])
    await recording.turn([], [])
    tokens, cost = recording.take()
    assert tokens == 36
    assert cost is not None and abs(cost - 0.03) < 1e-9
    assert recording.take() == (0, 0)


async def test_one_reply_without_a_cost_makes_the_cost_unknown() -> None:
    recording = RecordingTransport(Replies(reply("a", 0.01), reply("b", None)))
    await recording.turn([], [])
    await recording.turn([], [])
    assert recording.take() == (36, None)


async def test_a_judged_pass_gets_three_claim_rows_in_order() -> None:
    rows = await compare_rep(CASE, rec(), EVIDENCE, judges())
    assert [(r.task, r.judge) for r in rows] == [
        ("claims", "jev"),
        ("claims", "llm"),
        ("claims", "reference"),
    ]
    jev, llm, reference = rows
    assert jev.verdicts == [0.9, 0.9]
    assert jev.model == "jev-1.13.0"
    assert jev.cost_estimated
    assert llm.verdicts == [True, False]
    assert llm.model == "qwen"
    assert (llm.tokens, llm.cost, llm.attempts) == (15, None, 1)
    assert reference.verdicts == [True, True]
    assert reference.model == "opus"


async def test_too_large_skips_jev_and_the_others_still_run() -> None:
    fake = FakeJev()
    huge = replace(EVIDENCE, diff="+" * JEV_STATE_LIMIT * 4)
    rows = await compare_rep(CASE, rec(), huge, judges(fake))
    assert rows[0] == Row(CASE.id, 0, "claims", "jev", "jev-latest", error=TOO_LARGE)
    assert fake.calls == []
    assert rows[1].verdicts == [True, False]
    assert rows[2].verdicts == [True, True]


async def test_a_jev_error_is_a_row_carrying_the_body() -> None:
    rows = await compare_rep(CASE, rec(), EVIDENCE, judges(FakeJev(fail_on=1)))
    assert rows[0].error == "jev returned HTTP 500: upstream down"
    assert rows[0].latency_s == 0.5
    assert rows[1].error is None


async def test_a_reference_that_cannot_answer_is_an_error_row() -> None:
    rows = await compare_rep(CASE, rec(), EVIDENCE, judges(reference="no json"))
    assert rows[2].error is not None
    assert rows[2].error.startswith("judge failed")
    assert rows[2].attempts == 2


async def test_a_failure_is_judged_and_then_triaged_by_both() -> None:
    failed = rec(outcome="fail", checks=(FAILED,), judge_met=0)
    rows = await compare_rep(CASE, failed, EVIDENCE, judges())
    jev, reference = rows[3:]
    assert (jev.task, jev.judge, jev.cause) == ("triage", "jev", "no_change")
    assert jev.confidence == 0.8
    assert jev.claimed_done
    assert (reference.judge, reference.cause) == ("reference", "wrong_change")
    assert reference.claimed_done


async def test_jev_and_the_reference_triage_from_the_same_state() -> None:
    fake = FakeJev()
    reference = FakeJudge('{"cause": "wrong_change"}')
    bundle = replace(judges(fake), reference_triage=RecordingTransport(reference))
    failed = rec(outcome="fail", checks=(FAILED,), judge_met=0)
    await compare_rep(CASE, failed, EVIDENCE, bundle)
    state, questions = fake.calls[-1]
    assert list(questions) == ["cause"]
    assert json.loads(reference.calls[0][0].content[0]["text"]) == state


async def test_a_failure_code_can_label_gets_one_code_row() -> None:
    ran_out = rec(
        outcome="fail", checks=(FAILED,), status="error", stop_reason="max_turns"
    )
    rows = await compare_rep(CASE, ran_out, EVIDENCE, judges())
    assert rows[3:] == [
        Row(CASE.id, 0, "triage", "code", None, cause="ran_out", claimed_done=False)
    ]


async def test_rows_are_written_one_json_line_each(tmp_path: Path) -> None:
    out = tmp_path / "compare.jsonl"
    work = [(CASE, rec(0), EVIDENCE), (CASE, rec(1), EVIDENCE)]
    rows = await run_comparison(work, judges(FakeJev(fail_on=1)), out)
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(lines) == len(rows) == 6
    assert lines[0]["error"] == "jev returned HTTP 500: upstream down"
    assert lines[3]["verdicts"] == [0.9, 0.9]
    assert lines[4]["verdicts"] == [True, False]
    assert set(lines[0]) >= {
        "case",
        "rep",
        "task",
        "judge",
        "model",
        "verdicts",
        "cause",
        "probabilities",
        "confidence",
        "claimed_done",
        "tokens",
        "cost",
        "cost_estimated",
        "latency_s",
        "attempts",
        "error",
    }
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest -q benchmarks/tests/test_compare.py`
Expected: collection error, `ModuleNotFoundError: No module named 'benchmarks.runner.compare'`.

- [ ] **Step 3: Write the module**

Create `benchmarks/runner/compare.py`:

```python
"""Running every judge over kept evidence, one repetition at a time.

The judges see the same task, diff and closing message the benchmark's own
judge saw, read back from the files run_rep keeps. Nothing here decides what
the answers mean: that is agreement.py's job.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from benchmarks.runner.agreement import TOO_LARGE, JudgeName, Row, Skip
from benchmarks.runner.case import Case
from benchmarks.runner.jev import (
    JEV_STATE_LIMIT,
    Answer,
    Choice,
    Jev,
    JevError,
    Noul,
    estimated_tokens,
)
from benchmarks.runner.judge import JudgeError, score
from benchmarks.runner.report import RepRecord
from benchmarks.runner.triage import (
    cause_question,
    claimed_done,
    code_cause,
    triage_reference,
    triage_state,
)
from nare.session import Message
from nare.transport import Reply, Transport

# JUDGE_SYSTEM's rules, restated against the state's field names.
CLAIM_RULES = (
    "Judge only what `diff` shows. A claim about a file's contents is true only "
    "if `diff` shows it. `closing_message` is evidence only for claims about "
    "what the agent said or asked."
)
CLAIM_CRITERIA = {
    "true": "The evidence shows the claim holds.",
    "false": (
        "The evidence does not show the claim holds, including when it says "
        "nothing about it."
    ),
}


@dataclass(frozen=True)
class Evidence:
    diff: str
    closing: str
    messages: list[Any]  # session.json's messages; empty without one


def _exact(path: Path) -> str:
    # Bytes, not read_text: universal newlines would turn a \r the judge read
    # into a \n, and the evidence would no longer be what it judged.
    return path.read_bytes().decode("utf-8")


def _messages(path: Path) -> list[Any]:
    """A missing or unreadable session leaves the transcript empty."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    messages = raw.get("messages") if isinstance(raw, dict) else None
    return messages if isinstance(messages, list) else []


def load_evidence(results_dir: Path, rec: RepRecord) -> Evidence | None:
    """`results_dir` is benchmarks/results/, which `artifacts` is relative to."""
    if not rec.artifacts:
        return None
    kept = results_dir / rec.artifacts
    diff, closing = kept / "diff.patch", kept / "closing.txt"
    if not (diff.is_file() and closing.is_file()):
        return None
    return Evidence(_exact(diff), _exact(closing), _messages(kept / "session.json"))


def select_work(
    records: Sequence[RepRecord],
    cases: Mapping[str, Case],
    results_dir: Path,
    only: Collection[str] | None = None,
) -> tuple[list[tuple[Case, RepRecord, Evidence]], list[Skip]]:
    """The repetitions with something to judge or triage, and why the rest
    were skipped. A passing rep of an unjudged case has neither, so it is
    neither compared nor skipped."""
    work: list[tuple[Case, RepRecord, Evidence]] = []
    skips: list[Skip] = []
    for rec in records:
        if only and rec.case not in only:
            continue
        case = cases.get(rec.case)
        if case is None:
            skips.append(Skip(rec.case, rec.rep, "case no longer exists"))
            continue
        if case.judge is None and rec.outcome != "fail":
            continue
        evidence = load_evidence(results_dir, rec)
        if evidence is None:
            reason = (
                "errored before its diff was taken"
                if rec.outcome == "error"
                else "no evidence files"
            )
            skips.append(Skip(rec.case, rec.rep, reason))
            continue
        work.append((case, rec, evidence))
    return work, skips


def claims_state(case: Case, evidence: Evidence) -> dict[str, str]:
    return {
        "task": case.prompt,
        "diff": evidence.diff,
        "closing_message": evidence.closing,
    }


def claims_questions(case: Case) -> dict[str, dict[str, Any]]:
    """One noul per claim, in order, with ids c1 to cN."""
    assert case.judge is not None, "only judged cases have claims"
    return {
        f"c{n}": {
            "type": "noul",
            "instructions": f"{claim}\n\n{CLAIM_RULES}",
            "criteria": dict(CLAIM_CRITERIA),
        }
        for n, claim in enumerate(case.judge.assertions, start=1)
    }


def too_large(state: Any, questions: Mapping[str, Mapping[str, Any]]) -> bool:
    """Nothing is truncated to fit: a truncated diff asks a different question."""
    longest = max(
        (str(q.get("instructions", "")) for q in questions.values()),
        key=len,
        default="",
    )
    return estimated_tokens(state, longest) > JEV_STATE_LIMIT


class RecordingTransport:
    """Keeps each reply's usage, which score() does not return. judge.py is
    not changed to make room for it."""

    def __init__(self, inner: Transport) -> None:
        self.inner = inner
        self.replies: list[Reply] = []

    async def context_window(self) -> int | None:
        return await self.inner.context_window()

    async def turn(self, messages: list[Message], tools: list[dict[str, Any]]) -> Reply:
        reply = await self.inner.turn(messages, tools)
        self.replies.append(reply)
        return reply

    def take(self) -> tuple[int, float | None]:
        """Tokens and cost since the last take. Cost is None when any reply
        had none: a partial sum would read as the whole."""
        replies, self.replies = self.replies, []
        tokens = sum(r.usage.total_tokens for r in replies)
        costs = [r.cost for r in replies]
        if any(c is None for c in costs):
            return tokens, None
        return tokens, sum(c for c in costs if c is not None)


@dataclass(frozen=True)
class Judges:
    jev: Jev
    llm: RecordingTransport
    llm_model: str
    reference: RecordingTransport  # JUDGE_SYSTEM, for claims
    reference_triage: RecordingTransport  # TRIAGE_SYSTEM
    reference_model: str
    clock: Callable[[], float] = time.monotonic


def _jev_error(exc: JevError) -> str:
    return f"{exc}: {exc.body}" if exc.body else str(exc)


def _p_yes(answer: Answer) -> float:
    assert isinstance(answer, Noul), "the client checked every answer's type"
    return answer.p_yes


async def _jev_claims(
    rec: RepRecord, state: Any, questions: dict[str, dict[str, Any]], judges: Judges
) -> Row:
    model = judges.jev.model
    if too_large(state, questions):
        return Row(rec.case, rec.rep, "claims", "jev", model, error=TOO_LARGE)
    try:
        reply = await judges.jev.ask(state, questions)
    except JevError as exc:
        return Row(
            rec.case,
            rec.rep,
            "claims",
            "jev",
            model,
            latency_s=exc.latency_s,
            attempts=exc.attempts,
            error=_jev_error(exc),
        )
    return Row(
        rec.case,
        rec.rep,
        "claims",
        "jev",
        reply.model,
        verdicts=[_p_yes(reply.answers[qid]) for qid in questions],
        tokens=reply.input_tokens,
        cost=reply.cost,
        cost_estimated=reply.cost_estimated,
        latency_s=reply.latency_s,
        attempts=reply.attempts,
    )


async def _scored(
    case: Case,
    rec: RepRecord,
    evidence: Evidence,
    judge: JudgeName,
    transport: RecordingTransport,
    model: str,
    clock: Callable[[], float],
) -> Row:
    started = clock()
    try:
        met, _, attempts = await score(
            case, evidence.diff, evidence.closing, transport=transport
        )
    except JudgeError as exc:
        tokens, cost = transport.take()
        return Row(
            rec.case,
            rec.rep,
            "claims",
            judge,
            model,
            tokens=tokens,
            cost=cost,
            latency_s=clock() - started,
            attempts=len(exc.attempts),
            error=f"judge failed: {exc}",
        )
    tokens, cost = transport.take()
    return Row(
        rec.case,
        rec.rep,
        "claims",
        judge,
        model,
        verdicts=met,
        tokens=tokens,
        cost=cost,
        latency_s=clock() - started,
        attempts=len(attempts),
    )


async def judge_claims(
    case: Case, rec: RepRecord, evidence: Evidence, judges: Judges
) -> list[Row]:
    """Jev, then the LLM judge, then the reference: always in that order."""
    state = claims_state(case, evidence)
    return [
        await _jev_claims(rec, state, claims_questions(case), judges),
        await _scored(
            case, rec, evidence, "llm", judges.llm, judges.llm_model, judges.clock
        ),
        await _scored(
            case,
            rec,
            evidence,
            "reference",
            judges.reference,
            judges.reference_model,
            judges.clock,
        ),
    ]


async def _jev_cause(
    rec: RepRecord, state: dict[str, Any], done: bool, judges: Judges
) -> Row:
    try:
        reply = await judges.jev.ask(state, {"cause": cause_question()})
    except JevError as exc:
        return Row(
            rec.case,
            rec.rep,
            "triage",
            "jev",
            judges.jev.model,
            claimed_done=done,
            latency_s=exc.latency_s,
            attempts=exc.attempts,
            error=_jev_error(exc),
        )
    answer = reply.answers["cause"]
    assert isinstance(answer, Choice), "the client checked every answer's type"
    return Row(
        rec.case,
        rec.rep,
        "triage",
        "jev",
        reply.model,
        cause=answer.choice,
        probabilities=answer.probabilities,
        confidence=answer.confidence,
        claimed_done=done,
        tokens=reply.input_tokens,
        cost=reply.cost,
        cost_estimated=reply.cost_estimated,
        latency_s=reply.latency_s,
        attempts=reply.attempts,
    )


async def _reference_cause(
    rec: RepRecord, state: dict[str, Any], done: bool, judges: Judges
) -> Row:
    transport, model = judges.reference_triage, judges.reference_model
    started = judges.clock()
    try:
        cause, attempts = await triage_reference(state, transport=transport)
    except JudgeError as exc:
        tokens, cost = transport.take()
        return Row(
            rec.case,
            rec.rep,
            "triage",
            "reference",
            model,
            claimed_done=done,
            tokens=tokens,
            cost=cost,
            latency_s=judges.clock() - started,
            attempts=len(exc.attempts),
            error=f"judge failed: {exc}",
        )
    tokens, cost = transport.take()
    return Row(
        rec.case,
        rec.rep,
        "triage",
        "reference",
        model,
        cause=cause,
        claimed_done=done,
        tokens=tokens,
        cost=cost,
        latency_s=judges.clock() - started,
        attempts=len(attempts),
    )


async def triage_rep(
    case: Case, rec: RepRecord, evidence: Evidence, judges: Judges
) -> list[Row]:
    done = claimed_done(rec)
    coded = code_cause(rec, case.judge.min_met if case.judge else None)
    if coded is not None:
        return [
            Row(
                rec.case,
                rec.rep,
                "triage",
                "code",
                None,
                cause=coded,
                claimed_done=done,
            )
        ]
    state = triage_state(
        case.prompt, rec, evidence.diff, evidence.closing, evidence.messages
    )
    if state is None:
        both: tuple[tuple[JudgeName, str], ...] = (
            ("jev", judges.jev.model),
            ("reference", judges.reference_model),
        )
        return [
            Row(
                rec.case,
                rec.rep,
                "triage",
                judge,
                model,
                claimed_done=done,
                error=TOO_LARGE,
            )
            for judge, model in both
        ]
    return [
        await _jev_cause(rec, state, done, judges),
        await _reference_cause(rec, state, done, judges),
    ]


async def compare_rep(
    case: Case, rec: RepRecord, evidence: Evidence, judges: Judges
) -> list[Row]:
    rows: list[Row] = []
    if case.judge is not None:
        rows += await judge_claims(case, rec, evidence, judges)
    if rec.outcome == "fail":
        rows += await triage_rep(case, rec, evidence, judges)
    return rows


async def run_comparison(
    work: Sequence[tuple[Case, RepRecord, Evidence]], judges: Judges, out: Path
) -> list[Row]:
    """Sequential, as run_case is, so latency is measured without contention.
    Each row is written as it arrives: an interrupted comparison keeps what
    it already paid for."""
    rows: list[Row] = []
    with out.open("w", encoding="utf-8") as handle:
        for case, rec, evidence in work:
            for row in await compare_rep(case, rec, evidence, judges):
                handle.write(json.dumps(asdict(row)) + "\n")
                handle.flush()
                rows.append(row)
    return rows
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q benchmarks/tests/test_compare.py`
Expected: `18 passed`.

- [ ] **Step 5: Lint, type-check and commit**

Run: `uv run ruff format benchmarks && uv run ruff check benchmarks && uv run mypy benchmarks`
Expected: clean.

```bash
git add benchmarks/runner/compare.py benchmarks/tests/test_compare.py
git commit -m "feat(bench): run Jev, the llm judge and the reference over kept evidence

Reads diff.patch and closing.txt back byte for byte, judges claims three
ways and triages failures two ways, recording usage through a wrapping
transport so judge.py stays as it is. A judge that fails becomes an error
row and the comparison carries on. Part of #46.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The `compare-judges` subcommand

**Files:**
- Modify: `benchmarks/runner/__main__.py` (imports, `build_parser`, a new `compare_judges` before `main`, and the start of `main`)
- Modify: `benchmarks/README.md` (a new "Comparing judges" section)
- Test: `benchmarks/tests/test_compare.py` (append end-to-end tests), `benchmarks/tests/test_main.py` (append a parser test)

**Interfaces:**
- Consumes: everything from Tasks 2 to 5; `resolve_config`, `judge_transport`, `latest_results`, `read_results`, `new_stamp`, `load_cases`.
- Produces: `compare_judges(args: argparse.Namespace, root: Path) -> int` and `bin/bench compare-judges [--results PATH] [--case ID]... [--provider P] [--base-url URL] [--judge-model M] [--jev-model M] [--reference-model M]`, writing `benchmarks/results/<stamp>/compare-<cstamp>.jsonl` and `.txt`.

- [ ] **Step 1: Write the failing tests**

Append to `benchmarks/tests/test_main.py`:

```python
def test_compare_judges_accepts_its_flags() -> None:
    args = build_parser().parse_args(
        [
            "compare-judges",
            "--results",
            "r.jsonl",
            "--case",
            "a",
            "--case",
            "b",
            "--jev-model",
            "jev-1.13.0",
            "--reference-model",
            "opus",
        ]
    )
    assert args.command == "compare-judges"
    assert args.case == ["a", "b"]
    assert (args.jev_model, args.reference_model) == ("jev-1.13.0", "opus")
```

In `benchmarks/tests/test_compare.py`, add three imports so the import block reads:

```python
from typing import Any

import pytest

from benchmarks.runner.__main__ import main, write_results
from benchmarks.runner.agreement import TOO_LARGE, Row, Skip
```

and

```python
from benchmarks.runner.config import Config
from benchmarks.runner.grade import CheckResult
```

then append:

```python
ENV_VARS = (
    "NARE_PROVIDER",
    "NARE_MODEL",
    "NARE_BENCH_JUDGE_MODEL",
    "NARE_BENCH_JEV_MODEL",
    "NARE_BENCH_REFERENCE_MODEL",
    "NARE_BASE_URL",
)


class Built:
    """What compare-judges built, in place of a proxy."""

    def __init__(self) -> None:
        self.jev = FakeJev(fail_on=2)
        self.transports: dict[str, FakeJudge] = {}


def bench_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, records: list[RepRecord]
) -> Built:
    import benchmarks.runner.__main__ as bench
    from benchmarks.runner.sandbox import repo_root

    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "benchmarks" / "cases").symlink_to(repo_root() / "benchmarks" / "cases")
    write_results(tmp_path, records, "2026-10-08T00-00-00Z")
    built = Built()

    def jev(*, base_url: str, api_key: str, model: str) -> FakeJev:
        assert (base_url, api_key, model) == ("https://proxy.example/v1", "k", "jev-x")
        return built.jev

    def judge(config: Config) -> FakeJudge:
        verdicts = "[true, false, true]" if config.judge_model == "opus" else "[true]"
        built.transports[config.judge_model] = FakeJudge(
            f'{{"met": {verdicts}, "why": "x"}}'
        )
        return built.transports[config.judge_model]

    def triage(config: Config, model: str) -> FakeJudge:
        built.transports[f"triage:{model}"] = FakeJudge('{"cause": "wrong_change"}')
        return built.transports[f"triage:{model}"]

    monkeypatch.setattr(bench, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(bench, "JevClient", jev)
    monkeypatch.setattr(bench, "judge_transport", judge)
    monkeypatch.setattr(bench, "triage_transport", triage)
    monkeypatch.setattr(bench, "new_stamp", lambda: "2026-10-09T00-00-00Z")
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("NARE_BASE_URL", "https://proxy.example/v1")
    monkeypatch.setenv("NARE_BENCH_REFERENCE_MODEL", "opus")
    monkeypatch.setenv("NARE_BENCH_JEV_MODEL", "jev-x")
    return built


def run_record(case: str, rep: int, **changes: Any) -> RepRecord:
    return replace(
        rec(rep, case=case, artifacts=f"2026-10-08T00-00-00Z/{case}-{rep}"), **changes
    )


def kept_evidence(tmp_path: Path, case: str, rep: int) -> None:
    kept = (
        tmp_path / "benchmarks" / "results" / "2026-10-08T00-00-00Z" / f"{case}-{rep}"
    )
    kept.mkdir(parents=True)
    (kept / "diff.patch").write_text('+    """Return 42."""\n')
    (kept / "closing.txt").write_text("Added the docstring.")
    session = {
        "messages": [{"role": "user", "content": [{"type": "text", "text": "go"}]}]
    }
    (kept / "session.json").write_text(json.dumps(session))


def test_compare_judges_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    records = [
        run_record("edit-docstring", 0),
        run_record("edit-docstring", 1, outcome="fail", checks=(FAILED,), judge_met=1),
        run_record("edit-docstring", 2, outcome="error", artifacts=""),
    ]
    built = bench_root(tmp_path, monkeypatch, records)
    for rep in (0, 1):
        kept_evidence(tmp_path, "edit-docstring", rep)

    assert main(["compare-judges", "--judge-model", "qwen"]) == 0

    out = tmp_path / "benchmarks" / "results" / "2026-10-08T00-00-00Z"
    rows = [
        json.loads(line)
        for line in (out / "compare-2026-10-09T00-00-00Z.jsonl")
        .read_text()
        .splitlines()
    ]
    assert [(r["rep"], r["task"], r["judge"]) for r in rows] == [
        (0, "claims", "jev"),
        (0, "claims", "llm"),
        (0, "claims", "reference"),
        (1, "claims", "jev"),
        (1, "claims", "llm"),
        (1, "claims", "reference"),
        (1, "triage", "jev"),
        (1, "triage", "reference"),
    ]
    # Jev's second call failed; the comparison carried on past it.
    assert rows[3]["error"] == "jev returned HTTP 500: upstream down"
    assert rows[6]["cause"] == "no_change"
    assert rows[7]["cause"] == "wrong_change"
    # The llm judge answered one verdict for three claims: an error, not a zero.
    assert rows[1]["error"].startswith("judge failed")
    assert rows[2]["verdicts"] == [True, False, True]
    assert set(built.transports) == {"qwen", "opus", "triage:opus"}

    summary = (out / "compare-2026-10-09T00-00-00Z.txt").read_text()
    assert summary == capsys.readouterr().out
    assert "compare-judges over results 2026-10-08T00-00-00Z" in summary
    assert "skipped, errored before its diff was taken: 1" in summary
    assert "judge errors: jev claims 1, llm claims 2" in summary
    assert "Agreement is with the reference model, not ground truth." in summary
    assert "jev, P(yes) >= 0.5                 67% (2/3)" in summary


def test_a_second_comparison_does_not_overwrite_the_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import benchmarks.runner.__main__ as bench

    bench_root(tmp_path, monkeypatch, [run_record("edit-docstring", 0)])
    kept_evidence(tmp_path, "edit-docstring", 0)
    stamps = iter(["2026-10-09T00-00-00Z", "2026-10-09T00-00-01Z"])
    monkeypatch.setattr(bench, "new_stamp", lambda: next(stamps))
    assert main(["compare-judges"]) == 0
    assert main(["compare-judges"]) == 0
    out = tmp_path / "benchmarks" / "results" / "2026-10-08T00-00-00Z"
    assert len(list(out.glob("compare-*.jsonl"))) == 2


@pytest.mark.parametrize("unset", ["NARE_BASE_URL", "NARE_BENCH_REFERENCE_MODEL"])
def test_missing_configuration_exits_two_before_any_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unset: str
) -> None:
    built = bench_root(tmp_path, monkeypatch, [run_record("edit-docstring", 0)])
    kept_evidence(tmp_path, "edit-docstring", 0)
    monkeypatch.delenv(unset)
    assert main(["compare-judges"]) == 2
    assert built.jev.calls == []
    assert built.transports == {}


def test_a_missing_key_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench_root(tmp_path, monkeypatch, [run_record("edit-docstring", 0)])
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert main(["compare-judges"]) == 2


def test_records_from_two_models_exit_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = [
        run_record("edit-docstring", 0),
        run_record("edit-docstring", 1, model="other"),
    ]
    bench_root(tmp_path, monkeypatch, records)
    assert main(["compare-judges"]) == 2


def test_results_without_evidence_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = bench_root(tmp_path, monkeypatch, [run_record("edit-docstring", 0)])
    assert main(["compare-judges"]) == 1
    assert built.jev.calls == []


def test_no_results_at_all_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench_root(tmp_path, monkeypatch, [])
    assert main(["compare-judges"]) == 1  # the newest results file is empty
    missing = tmp_path / "nowhere.jsonl"
    assert main(["compare-judges", "--results", str(missing)]) == 1
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest -q benchmarks/tests/test_compare.py benchmarks/tests/test_main.py`
Expected: the parser test fails with `invalid choice: 'compare-judges'` (argparse exits 2), and every new `test_compare.py` test fails on `monkeypatch.setattr(bench, "JevClient", ...)`: `AttributeError: <module 'benchmarks.runner.__main__'> has no attribute 'JevClient'`.

- [ ] **Step 3: Add the imports**

In `benchmarks/runner/__main__.py`, change `from dataclasses import asdict` to:

```python
from dataclasses import asdict, replace
```

replace `from benchmarks.runner.case import Case, CaseError, load_cases` with:

```python
from benchmarks.runner.agreement import Header, render_summary
from benchmarks.runner.case import Case, CaseError, load_cases
from benchmarks.runner.compare import (
    Judges,
    RecordingTransport,
    run_comparison,
    select_work,
)
```

add directly above `from benchmarks.runner.judge import (`:

```python
from benchmarks.runner.jev import JevClient
```

and add directly above `from nare.transport import Transport`:

```python
from benchmarks.runner.triage import triage_transport
```

- [ ] **Step 4: Add the subparser**

In `build_parser`, directly before its final `return parser`, add:

```python
    judges = sub.add_parser(
        "compare-judges",
        help="measure Jev and the llm judge against a reference model",
    )
    judges.add_argument("--results", help="results file (default: the newest)")
    judges.add_argument("--case", action="append", help="limit to this case id")
    judges.add_argument("--provider", help="provider (env: NARE_PROVIDER)")
    judges.add_argument("--base-url", help="the proxy (env: NARE_BASE_URL)")
    judges.add_argument("--judge-model", help="judge (env: NARE_BENCH_JUDGE_MODEL)")
    judges.add_argument("--jev-model", help="Jev (env: NARE_BENCH_JEV_MODEL)")
    judges.add_argument(
        "--reference-model", help="reference (env: NARE_BENCH_REFERENCE_MODEL)"
    )
```

- [ ] **Step 5: Add `compare_judges`**

Directly above `def main(`, add:

```python
def compare_judges(args: argparse.Namespace, root: Path) -> int:
    """Run Jev, the llm judge and the reference over one run's kept evidence.

    Every configuration error exits 2 before any model is called. Nothing to
    compare exits 1. A judge failing on some repetitions still exits 0: those
    failures are in the report.
    """
    env = os.environ
    base_url = args.base_url or env.get("NARE_BASE_URL")
    if not base_url:
        print(
            "bench: compare-judges reaches Jev only through the proxy; "
            "pass --base-url or set NARE_BASE_URL",
            file=sys.stderr,
        )
        return 2
    reference = args.reference_model or env.get("NARE_BENCH_REFERENCE_MODEL")
    if not reference:
        print(
            "bench: no reference model: pass --reference-model or set "
            "NARE_BENCH_REFERENCE_MODEL (its alias depends on the proxy)",
            file=sys.stderr,
        )
        return 2
    jev_model = args.jev_model or env.get("NARE_BENCH_JEV_MODEL") or "jev-latest"

    results_file = Path(args.results) if args.results else latest_results(root)
    records = (
        read_results(results_file)
        if results_file is not None and results_file.is_file()
        else []
    )
    if results_file is None or not records:
        print("bench: no results to compare", file=sys.stderr)
        return 1
    models = sorted({r.model for r in records})
    if len(models) > 1:
        print(
            f"bench: {results_file.name} names {len(models)} models "
            f"({', '.join(models)}); compare-judges reads one run's model",
            file=sys.stderr,
        )
        return 2
    try:
        config = resolve_config(
            # The model under test comes from the records, so NARE_MODEL is
            # not needed.
            model=models[0],
            provider=args.provider,
            base_url=base_url,
            judge_model=args.judge_model,
            env=env,
        )
        cases = {c.id: c for c in load_cases(root / "benchmarks" / "cases")}
    except (ConfigError, CaseError) as exc:
        print(f"bench: {exc}", file=sys.stderr)
        return 2
    assert config.api_key is not None, "resolve_config demands a key by default"

    work, skips = select_work(records, cases, results_file.parent, args.case)
    if not work:
        print(
            f"bench: no repetition in {results_file.name} kept diff.patch and "
            "closing.txt; runs keep them from this version on",
            file=sys.stderr,
        )
        return 1

    judges = Judges(
        jev=JevClient(base_url=base_url, api_key=config.api_key, model=jev_model),
        llm=RecordingTransport(judge_transport(config)),
        llm_model=config.judge_model,
        reference=RecordingTransport(
            judge_transport(replace(config, judge_model=reference))
        ),
        reference_triage=RecordingTransport(triage_transport(config, reference)),
        reference_model=reference,
    )
    # Beside the compared run's evidence, under the comparison's own stamp,
    # so a second comparison never overwrites a first.
    evidence = results_file.parent / results_file.stem
    evidence.mkdir(parents=True, exist_ok=True)
    cstamp = new_stamp()
    rows = asyncio.run(
        run_comparison(work, judges, evidence / f"compare-{cstamp}.jsonl")
    )
    summary = render_summary(
        Header(results_file.stem, config.model, config.judge_model, reference),
        rows,
        skips,
        cases,
        records,
    )
    (evidence / f"compare-{cstamp}.txt").write_text(summary + "\n", encoding="utf-8")
    print(summary)
    return 0
```

- [ ] **Step 6: Dispatch before the tier is read**

`compare-judges` has no `--tier`, so it must branch before `main` calls `_select`. In `main`, replace:

```python
        print(
            "bench needs a subcommand: run, compare, bless, or verify", file=sys.stderr
        )
        return 2

    root = repo_root()
```

with:

```python
        print(
            "bench needs a subcommand: run, compare, bless, verify, or compare-judges",
            file=sys.stderr,
        )
        return 2

    root = repo_root()
    if args.command == "compare-judges":
        # No tier: it compares whatever cases the results file holds.
        return compare_judges(args, root)
```

- [ ] **Step 7: Run the tests**

Run: `uv run pytest -q benchmarks/tests`
Expected: all pass (233 passed, 7 deselected at the time of writing).

- [ ] **Step 8: Document it**

In `benchmarks/README.md`, add this section after "What a run leaves behind" and before "Writing a case":

````markdown
## Comparing judges

`compare-judges` measures how TypeSafe's Jev and the benchmark's own LLM judge
each agree with a stronger reference model, on the evidence a past run kept.
It changes no grade, gate or baseline. It produces the numbers for deciding
whether Jev should judge benchmark runs (#46).

**Agreement is with the reference model, not ground truth.** Nobody labels
anything by hand, so every figure says how close a judge gets to the
reference, never whether either is right.

Jev is reached only through the LiteLLM proxy's TypeSafe pass-through, so
`NARE_BASE_URL` must name the proxy. The proxy holds the TypeSafe key; the
benchmark sends the same proxy key it uses for `run`.

    export ANTHROPIC_API_KEY=...        # OPENAI_API_KEY for NARE_PROVIDER=openai
    export NARE_BASE_URL=https://...    # the proxy; required
    export NARE_BENCH_REFERENCE_MODEL=claude-opus-5-5   # the proxy's alias; required
    export NARE_BENCH_JUDGE_MODEL=...   # optional: defaults to the model under test
    export NARE_BENCH_JEV_MODEL=...     # optional: defaults to jev-latest

    bin/bench compare-judges                       # the newest results
    bin/bench compare-judges --results benchmarks/results/<stamp>.jsonl \
        --case edit-docstring

The model under test is read from the results file, so `NARE_MODEL` is not
needed. Only runs that kept `diff.patch` and `closing.txt` can be compared.
The reference has no default because its alias depends on the proxy.

Two jobs are measured:

- **Claims.** Each judged repetition's claims, judged by Jev (one yes or no
  question per claim, answered as P(yes)), by the LLM judge, and by the
  reference.
- **Triage.** Why each failed repetition failed. `ran_out` and `judge_gate`
  are decided by code; every other failure is labelled by Jev and by the
  reference, from the same seven labels.

It writes two files into the compared run's evidence directory, under the
comparison's own stamp, so a second comparison never overwrites a first:

    benchmarks/results/<stamp>/
      compare-<cstamp>.jsonl      one line per repetition, task and judge
      compare-<cstamp>.txt        the summary, as printed

Reading the summary:

- **Claim agreement.** The LLM judge's, and Jev's at each cutoff, where
  P(yes) at or above the cutoff reads as true. Each band leaves claims
  between `1 - t` and `t` unanswered and reports coverage, the share
  answered, and agreement on the rest: the numbers a confidence gate needs.
  Per-claim lines show a claim Jev handles badly instead of averaging it away.
- **Outcome flips.** Gated repetitions whose pass or fail would change if
  that judge replaced the reference. This is the number that changes what
  the benchmark reports.
- **Triage.** The code-labelled counts, a confusion table of Jev against the
  reference in counts, and Jev's median confidence where it agreed and where
  it did not.
- **Cost and speed.** Calls, tokens, dollars, and median and p95 latency per
  judge. `~` marks a cost estimated from Jev's input tokens because the proxy
  reported none.

Every percentage carries its n. A judge that could not answer is never
counted as wrong: it drops out of its own figures, and a reference that could
not answer drops the repetition from every judge's. Jev gives no reason for a
verdict, and that is not counted against it.

It exits 0 when the comparison finished, even if some judges failed on some
repetitions, since those failures are in the report; 1 when there is nothing
to compare; and 2 for a configuration error, before any model is called.
````

- [ ] **Step 9: Build and commit**

Run: `bin/build`
Expected: ruff, mypy and pytest pass (704 passed at the time of writing).

```bash
git add benchmarks/runner/__main__.py benchmarks/tests/test_compare.py benchmarks/tests/test_main.py benchmarks/README.md
git commit -m "feat(bench): add compare-judges

bin/bench compare-judges runs Jev, the llm judge and a reference model over
a past run's kept evidence and prints how each agrees with the reference,
what flips, and what it cost. Agreement is with the reference model, not
ground truth. Closes the tooling half of #46.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 10: Open PR 2**

Once PR 1 has merged, rebase `feat/compare-judges` onto `main` with the `rebase-main` skill, which drops the squash-merged Task 1 commit. Then run `bin/build` again, push, and open a PR titled `Compare Jev against the LLM judge (#46)`. Its body says what Task 0 found: whether the cost header is present, and whether `usage.input_tokens` is. Then follow `watch-ci` and `merge-pr`.

---

### Task 7: One full-tier run, and the report on #46

Operational. It spends model and Jev budget and posts on a public issue, so ask Sam before starting it.

- [ ] **Step 1: A full-tier run on `main` with both PRs merged**

```bash
bin/bench run --tier full
```

Expected: a new `benchmarks/results/<stamp>.jsonl`, and `diff.patch` and `closing.txt` in every rep directory that produced a result line.

- [ ] **Step 2: Compare the judges over it**

```bash
NARE_BENCH_REFERENCE_MODEL=claude-opus-5-5 bin/bench compare-judges
```

Use the proxy's own alias for the reference if it differs. Expected: exit 0, a summary on stdout, and `compare-<cstamp>.jsonl` and `.txt` beside the run's evidence.

- [ ] **Step 3: Report on #46**

Post the `.txt` summary to #46 as a comment, in a code block, with one line naming the model under test, the LLM judge, the reference, and Jev's pinned version. Adoption is decided there, not in this plan. If triage counts are thin, say so: one run yields few failures.

---

## Self-review

- **Spec coverage.** Section 3 is Task 0. Section 4 is Task 1. Section 5 is Task 2. Section 6 is Task 5 (claims state, questions, size, the LLM judge, the reference, the recording transport). Section 7 is Task 3, with its orchestration in Task 5. Section 8 is Task 6 (flags, defaults, exit codes, output paths) and Task 4 (the row fields). Section 9 is Task 4. Section 10's table is covered by tests in Tasks 2, 5 and 6. Section 11's layout is followed file for file. Section 12's tests are all present; the end-to-end tests live in `test_compare.py`, as the spec says. Section 13 is Task 6, Step 8. Section 14 is the Delivery list and Task 7.
- **Placeholders.** None. Every code step carries its code, and every command its expected result.
- **Type consistency.** `Row`, `Skip` and `JudgeName` are defined once, in `agreement.py`, and imported by `compare.py`. `JevError`'s `attempts` and `latency_s` are set by the client and read by `compare.py`. `triage_transport(config, model)` has the same signature in `triage.py`, `__main__.py`, and the test's monkeypatch.
