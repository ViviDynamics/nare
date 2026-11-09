"""`calibrate judge`, offline: the judge is a fake and nothing is captured in a
sandbox. It re-judges the designed solution and exploit diffs a label names."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from benchmarks.runner.calibration import Label, calibrate, load_labels
from benchmarks.runner.calibration_judge import judge_references, labelled_cases
from benchmarks.runner.case import Case, Check, Judge
from benchmarks.runner.compare import Evidence
from benchmarks.runner.judge import JudgeError

CLAIMS = ("The fix is in bar.py.", "The test was not weakened.")
CASE = Case(
    id="fix",
    tier="smoke",
    prompt="p",
    directory=Path("/nowhere"),
    max_turns=1,
    reps=1,
    timeout=1,
    checks=(Check(kind="bash", cmd="true"),),
    judge=Judge(assertions=CLAIMS, min_met=1),
)
UNJUDGED = Case(
    id="plain",
    tier="smoke",
    prompt="p",
    directory=Path("/nowhere"),
    max_turns=1,
    reps=1,
    timeout=1,
    checks=(Check(kind="bash", cmd="true"),),
    judge=None,
)
CASES = {CASE.id: CASE, UNJUDGED.id: UNJUDGED}
SOLUTION = Evidence("diff --git a/bar.py b/bar.py\n+fixed\n", "Fixed bar.", [])
EXPLOIT = Evidence("diff --git a/t.py b/t.py\n-assert x\n", "Done.", [])


def digest(evidence: Evidence) -> str:
    return hashlib.sha256(evidence.diff.encode("utf-8")).hexdigest()


def panel_label(diff_sha256: str, claim: int, value: bool) -> Label:
    row = {
        "case": "fix",
        "claim": claim,
        "claim_text": CLAIMS[claim - 1],
        "diff_sha256": diff_sha256,
        "labels": {"a": None, "b": None},
        "label": None,
        "adjudicated": False,
        "status": "pending",
        "panel": {
            "label": value,
            "passes": [
                {
                    "name": "quick",
                    "date": "2026-11-08",
                    "models": [{"model": "m", "effort": "high"}],
                }
            ],
        },
    }
    return _load([row])[0]


def _load(rows: Sequence[Mapping[str, object]]) -> list[Label]:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "labels.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return load_labels(path)


class FakeJudge:
    def __init__(
        self, answers: Mapping[str, Sequence[bool | None] | Exception]
    ) -> None:
        self.answers = answers
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, case: Case, diff: str, closing: str) -> Sequence[bool | None]:
        self.calls.append((case.id, diff, closing))
        answer = self.answers[diff]
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_labelled_cases_are_the_judged_cases_a_label_names() -> None:
    labels = [panel_label("a" * 64, 1, True)]
    assert labelled_cases(labels, CASES) == ["fix"]
    assert labelled_cases([], CASES) == []


def test_each_labelled_reference_is_judged_and_recorded_per_repeat() -> None:
    labels = [
        panel_label(digest(SOLUTION), 1, True),
        panel_label(digest(EXPLOIT), 2, False),
    ]
    judge = FakeJudge({SOLUTION.diff: [True, True], EXPLOIT.diff: [False, True]})
    captured = {("fix", "solution"): SOLUTION, ("fix", "exploit"): EXPLOIT}
    result = judge_references(
        captured,
        labels,
        CASES,
        judge,
        judge_model="judge-x",
        judge_max_tokens=4096,
        repeats=2,
    )
    assert len(judge.calls) == 4
    assert ("fix", SOLUTION.diff, SOLUTION.closing) in judge.calls
    assert result.skipped == []
    by_kind = {(r.model, r.rep): r for r in result.records}
    assert set(by_kind) == {
        ("reference:solution", 1),
        ("reference:solution", 2),
        ("reference:exploit", 1),
        ("reference:exploit", 2),
    }
    sol = by_kind[("reference:solution", 1)]
    assert sol.case == "fix"
    assert sol.diff_sha256 == digest(SOLUTION)
    assert sol.judge_model == "judge-x"
    assert sol.judge_max_tokens == 4096
    assert sol.judge_claims == [True, True]
    assert sol.judge_met == 2
    assert not sol.judge_failed
    # What score reads: the exploit's claim 2 was labelled false, judged true.
    rates = calibrate(labels, result.records).panel_by_judge["judge-x"]
    assert (rates.false_pos, rates.negatives) == (2, 2)
    assert (rates.false_neg, rates.positives) == (0, 2)


def test_a_reference_no_label_names_is_not_judged() -> None:
    labels = [panel_label(digest(SOLUTION), 1, True)]
    judge = FakeJudge({SOLUTION.diff: [True, True]})
    captured = {("fix", "solution"): SOLUTION, ("fix", "exploit"): EXPLOIT}
    result = judge_references(captured, labels, CASES, judge, judge_model="judge-x")
    assert [c[1] for c in judge.calls] == [SOLUTION.diff]
    assert result.skipped == [("fix", "exploit")]
    assert [r.model for r in result.records] == ["reference:solution"]


def test_a_judge_failure_is_a_failed_rep_with_no_verdicts() -> None:
    labels = [panel_label(digest(SOLUTION), 1, True)]
    judge = FakeJudge({SOLUTION.diff: JudgeError("no reply")})
    result = judge_references(
        {("fix", "solution"): SOLUTION}, labels, CASES, judge, judge_model="judge-x"
    )
    (record,) = result.records
    assert record.judge_failed
    assert record.judge_claims is None
    assert record.outcome == "error"
    assert "no reply" in record.judge_why
    # A failed rep contributes nothing to the rates.
    assert calibrate(labels, result.records).panel_by_judge == {}


def test_repeats_must_be_positive() -> None:
    with pytest.raises(ValueError):
        judge_references({}, [], CASES, FakeJudge({}), judge_model="j", repeats=0)


# --- bin/bench calibrate judge ---------------------------------------------


def _write_labels(path: Path, labels_rows: Sequence[Mapping[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(r) + "\n" for r in labels_rows), encoding="utf-8"
    )


def test_calibrate_judge_needs_a_judge_model_before_anything_is_built(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import benchmarks.runner.__main__ as bench

    monkeypatch.delenv("NARE_BENCH_JUDGE_MODEL", raising=False)

    def never(*_a: object, **_k: object) -> None:
        raise AssertionError("nothing may be built without a judge model")

    monkeypatch.setattr(bench, "build_image", never)
    monkeypatch.setattr(bench, "capture_reference", never)
    code = bench.main(["calibrate", "judge", "--out", str(tmp_path / "o.jsonl")])
    assert code == 2
    assert "judge model" in capsys.readouterr().err


def test_calibrate_judge_writes_records_score_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import benchmarks.runner.__main__ as bench
    from benchmarks.runner.case import load_cases

    real = {c.id: c for c in load_cases(Path("benchmarks/cases"))}
    case = real["edit-docstring"]
    assert case.judge is not None
    solution = Evidence("diff --git a/foo.py b/foo.py\n+doc\n", "Added it.", [])
    labels = tmp_path / "labels.jsonl"
    _write_labels(
        labels,
        [
            {
                "case": case.id,
                "claim": n,
                "claim_text": text,
                "diff_sha256": digest(solution),
                "labels": {"a": None, "b": None},
                "label": None,
                "adjudicated": False,
                "status": "pending",
                "panel": {
                    "label": True,
                    "passes": [
                        {
                            "name": "quick",
                            "date": "2026-11-08",
                            "models": [{"model": "m", "effort": "high"}],
                        }
                    ],
                },
            }
            for n, text in enumerate(case.judge.assertions, start=1)
        ],
    )
    monkeypatch.setenv("NARE_BENCH_JUDGE_MODEL", "judge-x")
    monkeypatch.setenv("NARE_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(bench, "build_image", lambda _root: None)
    asked: list[Sequence[str]] = []

    def capture(
        cases: Mapping[str, Case], case_ids: Sequence[str]
    ) -> dict[tuple[str, str], Evidence]:
        asked.append(tuple(case_ids))
        return {(case.id, "solution"): solution}

    monkeypatch.setattr(bench, "capture_reference", capture)
    claims = [False] + [True] * (len(case.judge.assertions) - 1)
    monkeypatch.setattr(bench, "judge_from", lambda _factory: lambda _c, _d, _t: claims)
    out = tmp_path / "judged.jsonl"
    code = bench.main(
        ["calibrate", "judge", "--labels", str(labels), "--out", str(out)]
    )
    assert code == 0
    assert asked == [(case.id,)]
    printed = capsys.readouterr().out
    assert "judge-x" in printed
    assert str(out) in printed
    (line,) = out.read_text(encoding="utf-8").splitlines()
    assert json.loads(line)["diff_sha256"] == digest(solution)

    # calibrate score reads the file back and gives the same report.
    assert (
        bench.main(
            ["calibrate", "score", "--labels", str(labels), "--results", str(out)]
        )
        == 0
    )
    scored = capsys.readouterr().out
    assert "judge-x" in scored
    assert f"1/{len(case.judge.assertions)}" in scored  # one false negative
