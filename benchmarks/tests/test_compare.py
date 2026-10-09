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
