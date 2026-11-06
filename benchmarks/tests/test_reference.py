from __future__ import annotations

import json
import re
import shutil
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from benchmarks.runner.case import Case, load_cases
from benchmarks.runner.case import Judge as CaseJudge
from benchmarks.runner.reference import (
    JudgeFn,
    Reference,
    ReferenceError,
    load_reference,
    result_line,
    run_reference,
    verify_references,
)
from benchmarks.runner.sandbox import repo_root

ROOT = repo_root() / "benchmarks" / "cases"


def case_named(case_id: str) -> Case:
    return {c.id: c for c in load_cases(ROOT)}[case_id]


class FakeRuntime:
    """A sandbox in a temporary directory. Checks run on the host, with /out
    pointed at the artifacts, as they would in a container."""

    def __init__(
        self,
        *,
        apply_code: int = 0,
        failing: Callable[[str, str], bool] = lambda kind, cmd: False,
    ) -> None:
        self.apply_code = apply_code
        self.failing = failing
        self.kind = ""
        self.written: dict[str, str] = {}
        self.ran: list[str] = []

    @contextmanager
    def sandbox(self, case: Case) -> Iterator[tuple[Path, Path]]:
        holder = Path(tempfile.mkdtemp(prefix="ref-test-"))
        try:
            work, out = holder / "work", holder / "out"
            shutil.copytree(case.fixture, work)
            out.mkdir()
            yield work, out
        finally:
            shutil.rmtree(holder, ignore_errors=True)

    def checkpoint(self, workdir: Path) -> None:
        return None

    def apply(self, workdir: Path, artifacts: Path, directory: Path) -> int:
        self.kind = directory.name
        return self.apply_code

    def write_out(self, workdir: Path, artifacts: Path, name: str, text: str) -> None:
        self.written[name] = text
        (artifacts / name).write_text(text, encoding="utf-8")

    def diff(self, workdir: Path) -> str:
        return "diff --git a/x b/x\n"

    def check(self, workdir: Path, cmd: str, artifacts: Path) -> tuple[int, str]:
        self.ran.append(cmd)
        return (1, "boom") if self.failing(self.kind, cmd) else (0, "")


def reference(tmp_path: Path, **files: str) -> Path:
    directory = tmp_path / "ref"
    directory.mkdir(exist_ok=True)
    for name, text in files.items():
        (directory / name.replace("__", ".")).write_text(text, encoding="utf-8")
    return directory


def judged(met: list[bool | None]) -> JudgeFn:
    """A judge that gives these verdicts, whatever it is asked."""

    def judge(case: Case, diff: str, closing: str) -> Sequence[bool | None]:
        return list(met)

    return judge


def test_a_missing_result_file_means_done_in_one_turn(tmp_path: Path) -> None:
    ref = load_reference(reference(tmp_path), "solution")
    assert (ref.status, ref.turns, ref.caught_by, ref.closing) == (
        "done",
        1,
        "checks",
        "",
    )
    assert ref.session is None


def test_a_malformed_reference_file_is_a_reference_error(tmp_path: Path) -> None:
    with pytest.raises(ReferenceError):
        load_reference(reference(tmp_path, result__toml="status = [\n"), "solution")
    with pytest.raises(ReferenceError):
        load_reference(reference(tmp_path, session__json="[1]"), "solution")
    with pytest.raises(ReferenceError):
        load_reference(
            reference(tmp_path, exploit__toml='caught_by = "vibes"'), "exploit"
        )


def test_a_passing_solution_passes_its_checks(tmp_path: Path) -> None:
    case = case_named("edit-docstring")
    verdict = run_reference(case, reference(tmp_path), "solution", FakeRuntime())
    assert verdict.ok
    assert "2/2 checks pass" in verdict.lines[0]
    assert "judge: not run (pass --judge)" in verdict.lines


def test_a_solution_fails_when_a_bash_check_fails(tmp_path: Path) -> None:
    case = case_named("fix-failing-test")
    runtime = FakeRuntime(failing=lambda kind, cmd: cmd == "pytest -q")
    verdict = run_reference(case, reference(tmp_path), "solution", runtime)
    assert not verdict.ok
    assert any(line.startswith("failed: bash: pytest -q") for line in verdict.lines)


def test_a_solution_fails_when_its_result_is_not_the_one_the_case_wants(
    tmp_path: Path,
) -> None:
    # The ambiguous case wants a blocked run; a solution that reports done fails.
    case = case_named("ambiguous-after-reading")
    directory = reference(tmp_path, result__toml='status = "done"\nturns = 3\n')
    verdict = run_reference(case, directory, "solution", FakeRuntime())
    assert not verdict.ok
    assert any("status 'done', wanted 'blocked'" in line for line in verdict.lines)


def test_a_solution_must_meet_every_judge_claim(tmp_path: Path) -> None:
    case = case_named("bash-timeout-recovery")
    met = run_reference(
        case, reference(tmp_path), "solution", FakeRuntime(), judged([True])
    )
    assert met.ok
    unmet = run_reference(
        case, reference(tmp_path), "solution", FakeRuntime(), judged([False])
    )
    assert not unmet.ok
    assert any(line.startswith("unmet: notes.txt records") for line in unmet.lines)


def test_a_judge_that_gives_the_wrong_count_fails_the_solution(tmp_path: Path) -> None:
    case = case_named("bash-timeout-recovery")
    verdict = run_reference(
        case, reference(tmp_path), "solution", FakeRuntime(), judged([True, True])
    )
    assert not verdict.ok
    assert any("judge gave 2 verdicts for 1 claims" in line for line in verdict.lines)


def test_a_judge_that_raises_fails_the_solution_with_its_reason(tmp_path: Path) -> None:
    case = case_named("bash-timeout-recovery")

    def broken(case: Case, diff: str, closing: str) -> Sequence[bool | None]:
        raise RuntimeError("upstream down")

    verdict = run_reference(
        case, reference(tmp_path), "solution", FakeRuntime(), broken
    )
    assert not verdict.ok
    assert "judge failed: RuntimeError: upstream down" in verdict.lines


def test_a_solution_that_does_not_apply_fails(tmp_path: Path) -> None:
    case = case_named("edit-docstring")
    verdict = run_reference(
        case, reference(tmp_path), "solution", FakeRuntime(apply_code=1)
    )
    assert not verdict.ok
    assert verdict.lines == ("the solution did not apply (exit 1)",)


def test_an_exploit_is_caught_when_a_check_fails(tmp_path: Path) -> None:
    case = case_named("bash-timeout-recovery")
    runtime = FakeRuntime(failing=lambda kind, cmd: "bar.greet('ann')" in cmd)
    verdict = run_reference(case, reference(tmp_path), "exploit", runtime)
    assert verdict.ok
    assert verdict.lines[0].startswith("caught by bash:")


def test_an_exploit_that_passes_every_check_is_a_failure(tmp_path: Path) -> None:
    case = case_named("bash-timeout-recovery")
    verdict = run_reference(case, reference(tmp_path), "exploit", FakeRuntime())
    assert not verdict.ok
    assert "passes every check" in verdict.lines[0]


def test_an_exploit_caught_by_the_judge_needs_the_judge(tmp_path: Path) -> None:
    case = case_named("expense-tracker-cli")
    directory = reference(tmp_path, exploit__toml='caught_by = "judge"')
    skipped = run_reference(case, directory, "exploit", FakeRuntime())
    assert skipped.ok
    assert "not run (pass --judge" in skipped.lines[0]
    caught = run_reference(
        case, directory, "exploit", FakeRuntime(), judged([False, True])
    )
    assert caught.ok
    assert "caught by the judge" in caught.lines[0]
    missed = run_reference(
        case, directory, "exploit", FakeRuntime(), judged([True, True])
    )
    assert not missed.ok


def test_a_session_and_closing_message_reach_the_checks(tmp_path: Path) -> None:
    case = case_named("ambiguous-request")
    session = {
        "messages": [],
        "questions": ["Raise TIMEOUT to 60?"],
        "status": "blocked",
    }
    directory = reference(
        tmp_path,
        session__json=json.dumps(session),
        closing__txt="TIMEOUT is 30. Raise it to 60?",
        result__toml='status = "blocked"\nturns = 2\n',
    )
    runtime = FakeRuntime()
    verdict = run_reference(case, directory, "solution", runtime)
    assert json.loads(runtime.written["session.json"]) == session
    assert runtime.written["closing.txt"] == "TIMEOUT is 30. Raise it to 60?"
    assert verdict.ok


def test_an_idle_session_is_the_one_nare_starts_with(tmp_path: Path) -> None:
    case = case_named("edit-docstring")
    runtime = FakeRuntime()
    run_reference(case, reference(tmp_path), "solution", runtime)
    assert json.loads(runtime.written["session.json"])["messages"]


def test_a_result_line_takes_its_questions_from_the_session() -> None:
    ref = Reference(
        directory=Path("."),
        kind="solution",
        session=None,
        closing="",
        status="blocked",
        turns=3,
        caught_by="checks",
    )
    line = result_line(ref, {"questions": ["Which?"]})
    assert (line.status, line.turns, line.questions) == ("blocked", 3, ["Which?"])


def test_verify_fails_a_case_with_no_solution(tmp_path: Path) -> None:
    case = case_named("edit-docstring")
    copy = Case(**{**case.__dict__, "directory": tmp_path})
    text, code = verify_references([copy], FakeRuntime())
    assert code == 1
    assert "no solution/ directory" in text


def _case_with_references(tmp_path: Path, case_id: str, *kinds: str) -> Case:
    """A copy of a real case in a temporary directory, with these reference dirs."""
    case = case_named(case_id)
    base = tmp_path / "case"
    base.mkdir()
    shutil.copytree(case.fixture, base / "fixture")
    for kind in kinds:
        (base / kind).mkdir()
    return Case(**{**case.__dict__, "directory": base})


def test_verify_fails_when_a_solution_does_not_pass(tmp_path: Path) -> None:
    relocated = _case_with_references(
        tmp_path, "fix-failing-test", "solution", "exploit"
    )
    failing = lambda kind, cmd: kind == "exploit" or cmd == "pytest -q"  # noqa: E731
    text, code = verify_references([relocated], FakeRuntime(failing=failing))
    assert code == 1
    assert re.search(r"solution\s+FAIL", text)
    assert re.search(r"exploit\s+ok", text)
    text, code = verify_references(
        [relocated], FakeRuntime(failing=lambda kind, cmd: kind == "exploit")
    )
    assert code == 0
    assert re.search(r"solution\s+ok", text)
    assert re.search(r"exploit\s+ok", text)


def test_verify_fails_a_case_graded_by_execution_with_no_exploit(
    tmp_path: Path,
) -> None:
    relocated = _case_with_references(tmp_path, "fix-failing-test", "solution")
    text, code = verify_references([relocated], FakeRuntime())
    assert code == 1
    assert "no exploit/ directory" in text


def test_verify_fails_an_exploit_that_passes(tmp_path: Path) -> None:
    case = case_named("fix-failing-test")
    base = tmp_path / "case"
    base.mkdir()
    shutil.copytree(case.fixture, base / "fixture")
    (base / "solution").mkdir()
    (base / "exploit").mkdir()
    relocated = Case(**{**case.__dict__, "directory": base})
    text, code = verify_references([relocated], FakeRuntime())
    assert code == 1
    assert "passes every check" in text


@pytest.mark.parametrize("case_id", [c.id for c in load_cases(ROOT)])
def test_every_case_has_a_reference_solution_and_an_exploit(case_id: str) -> None:
    case = case_named(case_id)
    assert (case.directory / "solution").is_dir(), "no solution/"
    assert (case.directory / "exploit").is_dir(), "no exploit/"
    load_reference(case.directory / "solution", "solution")
    load_reference(case.directory / "exploit", "exploit")


def test_a_reference_with_an_unknown_file_is_refused(tmp_path: Path) -> None:
    # A misnamed patch would apply nothing, and the attempt would be graded on
    # the untouched fixture. That must be an error, not a pass.
    with pytest.raises(ReferenceError, match="unknown file"):
        load_reference(reference(tmp_path, fix__patch="x"), "exploit")


def test_a_misnamed_patch_fails_the_exploit_instead_of_being_caught(
    tmp_path: Path,
) -> None:
    case = case_named("bash-timeout-recovery")
    verdict = run_reference(
        case,
        reference(tmp_path, fix__patch="x"),
        "exploit",
        FakeRuntime(failing=lambda k, c: True),
    )
    assert not verdict.ok
    assert "unknown file" in verdict.lines[0]


@pytest.mark.parametrize(
    ("met", "caught"),
    [
        # A gate of two claims. Caught only when even counting every unknown as
        # true would not reach it; an unknown is never a refutation.
        ([None, None], False),
        ([None, False], True),
        ([True, None], False),
        ([True, False], True),
        ([True, True], False),
        ([False, False], True),
    ],
)
def test_a_judged_exploit_is_caught_only_by_a_failed_gate(
    tmp_path: Path, met: list[bool | None], caught: bool
) -> None:
    base = case_named("edit-docstring")  # two claims
    assert base.judge is not None
    case = replace(base, judge=CaseJudge(base.judge.assertions, 2))
    directory = reference(tmp_path, exploit__toml='caught_by = "judge"')
    verdict = run_reference(case, directory, "exploit", FakeRuntime(), judged(met))
    assert verdict.ok is caught


def test_a_judged_exploit_on_a_case_the_judge_does_not_gate_fails(
    tmp_path: Path,
) -> None:
    case = case_named("multi-file-rename")
    directory = reference(tmp_path, exploit__toml='caught_by = "judge"')
    verdict = run_reference(case, directory, "exploit", FakeRuntime(), judged([False]))
    assert not verdict.ok
    assert "does not gate it" in verdict.lines[0]


def test_a_sandbox_that_fails_is_a_failed_verdict(tmp_path: Path) -> None:
    from benchmarks.runner.sandbox import SandboxError

    class Broken(FakeRuntime):
        @contextmanager
        def sandbox(self, case: Case) -> Iterator[tuple[Path, Path]]:
            raise SandboxError("docker went away")
            yield  # pragma: no cover

    case = case_named("edit-docstring")
    verdict = run_reference(case, reference(tmp_path), "solution", Broken())
    assert not verdict.ok
    assert verdict.lines == ("the sandbox failed: docker went away",)
