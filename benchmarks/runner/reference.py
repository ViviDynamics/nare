"""Reference solutions and exploits: what `verify` runs against each case.

A case's `solution/` is a correct attempt. It must pass every check, and with
a judge, every judge claim. Its `exploit/` is a cheap wrong attempt, and it
must be caught: by a check, or, with `caught_by = "judge"`, by the judge.

A reference directory holds any of these, all optional:

    patch.diff    applied to the fixture with git apply
    solve.sh      run with sh in the working directory, after the patch
    session.json  the transcript the case reads; default: an idle run
    closing.txt   the closing message; default: none
    result.toml   status and turns for the result checks; default: done, 1
    exploit.toml  caught_by = "checks" (default) or "judge"

Nothing here runs a container itself. A runtime does, so the grading policy
is testable with a fake one. The judge's gate is the benchmark's own
(grade.rep_outcome), so a reference is graded the way a run is.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, Protocol

from benchmarks.runner.case import Case
from benchmarks.runner.grade import (
    CheckResult,
    ResultLine,
    grade_bash_check,
    grade_result_check,
    rep_outcome,
)
from benchmarks.runner.sandbox import (
    DID_NOT_RUN,
    SandboxError,
    apply_reference,
    git_checkpoint,
    git_diff,
    run_check,
    write_out,
)
from benchmarks.runner.sandbox import sandbox as sandbox_context
from nare.session import dumps, new_session

Kind = Literal["solution", "exploit"]
CaughtBy = Literal["checks", "judge"]
_CAUGHT_BY: tuple[str, ...] = ("checks", "judge")
_FILES = frozenset(
    {
        "patch.diff",
        "solve.sh",
        "session.json",
        "closing.txt",
        "result.toml",
        "exploit.toml",
    }
)


class ReferenceError(Exception):
    """A reference directory that cannot be read. A verify failure, not a crash."""


@dataclass(frozen=True)
class Reference:
    directory: Path
    kind: Kind
    session: dict[str, Any] | None
    closing: str
    status: str
    turns: int
    caught_by: CaughtBy


def load_reference(directory: Path, kind: Kind) -> Reference:
    """Read one reference directory. Malformed files raise ReferenceError."""
    unknown = sorted(p.name for p in directory.iterdir() if p.name not in _FILES)
    if unknown:
        raise ReferenceError(
            f"{directory}: unknown file(s) {', '.join(unknown)}; a patch is "
            "patch.diff and a script is solve.sh"
        )
    session_path = directory / "session.json"
    session: dict[str, Any] | None = None
    if session_path.is_file():
        try:
            loaded = json.loads(session_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ReferenceError(f"{session_path}: not JSON: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ReferenceError(f"{session_path}: a session is a JSON object")
        session = loaded
    closing_path = directory / "closing.txt"
    closing = closing_path.read_text(encoding="utf-8") if closing_path.is_file() else ""
    result = _toml(directory / "result.toml")
    status = result.get("status", "done")
    turns = result.get("turns", 1)
    if not isinstance(status, str) or not isinstance(turns, int):
        raise ReferenceError(
            f"{directory / 'result.toml'}: status is a string, turns an integer"
        )
    exploit = _toml(directory / "exploit.toml")
    caught_by = exploit.get("caught_by", "checks")
    if caught_by not in _CAUGHT_BY:
        raise ReferenceError(
            f"{directory / 'exploit.toml'}: caught_by is checks or judge"
        )
    return Reference(
        directory=directory,
        kind=kind,
        session=session,
        closing=closing,
        status=status,
        turns=turns,
        caught_by=caught_by,
    )


def _toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ReferenceError(f"{path}: malformed TOML: {exc}") from exc


def _session_text(case: Case, ref: Reference) -> str:
    if ref.session is None:
        return dumps(new_session(case.prompt))
    return json.dumps(ref.session)


def result_line(ref: Reference, session: dict[str, Any]) -> ResultLine:
    """The result line nare would have written for this reference."""
    questions = session.get("questions", [])
    return ResultLine(
        status=ref.status,
        turns=ref.turns,
        usage={"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
        stop_reason=None,
        error=None,
        questions=[str(q) for q in questions] if isinstance(questions, list) else [],
    )


class Runtime(Protocol):
    """What a reference needs from a sandbox. DockerRuntime is the real one."""

    def sandbox(self, case: Case) -> AbstractContextManager[tuple[Path, Path]]: ...

    def checkpoint(self, workdir: Path) -> None: ...

    def apply(self, workdir: Path, artifacts: Path, directory: Path) -> int: ...

    def write_out(
        self, workdir: Path, artifacts: Path, name: str, text: str
    ) -> None: ...

    def diff(self, workdir: Path) -> str: ...

    def check(self, workdir: Path, cmd: str, artifacts: Path) -> tuple[int, str]: ...


# The judge: one verdict per claim, in order, None where it said unknown. It
# raises when it could not answer.
JudgeFn = Callable[[Case, str, str], Sequence[bool | None]]


@dataclass(frozen=True)
class Verdict:
    case: str
    kind: str
    ok: bool
    lines: tuple[str, ...] = field(default=())
    # Judged items this run did not judge (without --judge), so verify can say
    # what it did not verify.
    not_run: int = 0


def run_reference(
    case: Case,
    directory: Path,
    kind: Kind,
    runtime: Runtime,
    judge: JudgeFn | None = None,
) -> Verdict:
    """Apply one reference to a fresh sandbox and grade it against the case."""
    try:
        ref = load_reference(directory, kind)
    except ReferenceError as exc:
        return Verdict(case.id, kind, False, (str(exc),))
    try:
        return _graded(case, ref, directory, runtime, judge)
    except SandboxError as exc:
        return Verdict(case.id, kind, False, (f"the sandbox failed: {exc}",))


def _graded(
    case: Case,
    ref: Reference,
    directory: Path,
    runtime: Runtime,
    judge: JudgeFn | None,
) -> Verdict:
    kind = ref.kind
    with runtime.sandbox(case) as (workdir, artifacts):
        runtime.checkpoint(workdir)
        code = runtime.apply(workdir, artifacts, directory)
        if code != 0:
            return Verdict(
                case.id, kind, False, (f"the {kind} did not apply (exit {code})",)
            )
        if not (artifacts / "after.json").is_file():
            raise SandboxError("the leftovers snapshot was not written")
        session_text = _session_text(case, ref)
        runtime.write_out(workdir, artifacts, "session.json", session_text)
        runtime.write_out(workdir, artifacts, "closing.txt", ref.closing)
        # Before the checks, so nothing a check writes reaches the judge.
        diff = runtime.diff(workdir)
        session = json.loads(session_text)
        graded: list[CheckResult] = [
            grade_result_check(check, result_line(ref, session))
            for check in case.checks
            if check.kind == "result"
        ]
        for check in case.bash_checks:
            exit_code, output = runtime.check(workdir, check.cmd or "", artifacts)
            if exit_code in DID_NOT_RUN:
                raise SandboxError(
                    f"a check did not run (exit {exit_code}): "
                    f"{_short(f'bash: {check.cmd}')}"
                )
            graded.append(grade_bash_check(check, exit_code, output))
    if kind == "solution":
        return _solution_verdict(case, ref, graded, diff, judge)
    return _exploit_verdict(case, ref, graded, diff, judge)


def _solution_verdict(
    case: Case,
    ref: Reference,
    graded: list[CheckResult],
    diff: str,
    judge: JudgeFn | None,
) -> Verdict:
    failed = [c for c in graded if not c.passed]
    lines = [f"{len(graded) - len(failed)}/{len(graded)} checks pass"]
    lines += _failures(failed)
    if case.judge is None or judge is None:
        if case.judge is not None:
            lines.append("judge: not run (pass --judge)")
        outcome = rep_outcome(graded, None, None)
        not_run = 1 if case.judge is not None else 0
        return Verdict(case.id, "solution", outcome == "pass", tuple(lines), not_run)
    verdicts = _claims(case, diff, ref.closing, judge)
    if isinstance(verdicts, str):
        return Verdict(case.id, "solution", False, (*lines, verdicts))
    # A solution must meet every claim, so the gate is all of them.
    everything = replace(case.judge, min_met=len(case.judge.assertions))
    outcome = rep_outcome(graded, everything, verdicts)
    met = sum(v is True for v in verdicts)
    lines.append(f"judge: {met}/{len(verdicts)} claims met")
    lines += [
        f"unmet: {a}"
        for a, v in zip(case.judge.assertions, verdicts, strict=True)
        if v is not True
    ]
    return Verdict(case.id, "solution", outcome == "pass", tuple(lines))


def _exploit_verdict(
    case: Case,
    ref: Reference,
    graded: list[CheckResult],
    diff: str,
    judge: JudgeFn | None,
) -> Verdict:
    failed = [c for c in graded if not c.passed]
    if ref.caught_by == "checks":
        if failed:
            labels = "; ".join(_short(c.label) for c in failed)
            return Verdict(case.id, "exploit", True, (f"caught by {labels}",))
        return Verdict(
            case.id,
            "exploit",
            False,
            ("the exploit passes every check; the case does not measure this cheat",),
        )
    if case.judge is None or case.judge.min_met is None:
        return Verdict(
            case.id,
            "exploit",
            False,
            ("this case's judge does not gate it, so it cannot catch an exploit",),
        )
    if judge is None:
        return Verdict(
            case.id,
            "exploit",
            True,
            ("caught by the judge: not run (pass --judge to check it)",),
            not_run=1,
        )
    verdicts = _claims(case, diff, ref.closing, judge)
    if isinstance(verdicts, str):
        return Verdict(case.id, "exploit", False, (verdicts,))
    # The benchmark's own gate: a fail here is the judge catching the exploit.
    outcome = rep_outcome([], case.judge, verdicts)
    met = sum(v is True for v in verdicts)
    gate = case.judge.min_met
    if outcome == "fail":
        unmet = next(
            a
            for a, v in zip(case.judge.assertions, verdicts, strict=True)
            if v is not True
        )
        return Verdict(
            case.id,
            "exploit",
            True,
            (
                f"caught by the judge: {met} of {len(verdicts)} claims met, "
                f"gate {gate}; {unmet}",
            ),
        )
    if outcome == "pass":
        return Verdict(
            case.id,
            "exploit",
            False,
            (
                f"the exploit meets the judge gate: {met} of {len(verdicts)} "
                f"claims, gate {gate}",
            ),
        )
    return Verdict(
        case.id,
        "exploit",
        False,
        (f"the judge could not settle the gate: {met} met, gate {gate}",),
    )


def _failures(failed: Sequence[CheckResult]) -> list[str]:
    lines = []
    for c in failed:
        detail = c.detail.splitlines()[0] if c.detail else ""
        lines.append(f"failed: {_short(c.label)} {detail}".rstrip())
    return lines


def _short(label: str) -> str:
    """A check's label, on one line: its first line, cut to a screenful."""
    first = label.splitlines()[0] if label else ""
    return first if len(first) <= 100 else first[:97] + "..."


def _claims(
    case: Case, diff: str, closing: str, judge: JudgeFn
) -> list[bool | None] | str:
    """One verdict per claim, or the reason the judge did not give them."""
    assert case.judge is not None
    try:
        verdicts = list(judge(case, diff, closing))
    except Exception as exc:
        return f"judge failed: {type(exc).__name__}: {exc}"
    wanted = len(case.judge.assertions)
    if len(verdicts) != wanted:
        return f"judge gave {len(verdicts)} verdicts for {wanted} claims"
    return verdicts


class DockerRuntime:
    """The real runtime: each step is a container, as an agent's would be."""

    def sandbox(self, case: Case) -> AbstractContextManager[tuple[Path, Path]]:
        return sandbox_context(case)

    def checkpoint(self, workdir: Path) -> None:
        git_checkpoint(workdir)

    def apply(self, workdir: Path, artifacts: Path, directory: Path) -> int:
        return apply_reference(workdir, artifacts, directory)

    def write_out(self, workdir: Path, artifacts: Path, name: str, text: str) -> None:
        write_out(workdir, artifacts, name, text)

    def diff(self, workdir: Path) -> str:
        return git_diff(workdir)

    def check(self, workdir: Path, cmd: str, artifacts: Path) -> tuple[int, str]:
        return run_check(workdir, cmd, 120, artifacts=artifacts)


def verify_references(
    cases: Sequence[Case],
    runtime: Runtime,
    judge: JudgeFn | None = None,
) -> tuple[str, int]:
    """Every case's solution must pass, and its exploit must be caught.

    A case with no solution fails, and so does a case graded by execution with
    no exploit: each needs one. A case graded only by its result is not
    required to have an exploit. Without a judge, the judged items are reported
    as not run, and the exit status says only what was run.
    """
    lines: list[str] = []
    failed = False
    not_run = 0
    for case in cases:
        solution = case.directory / "solution"
        if not solution.is_dir():
            failed = True
            lines.append(f"{case.id:<26} FAIL: no solution/ directory")
            continue
        verdicts = [run_reference(case, solution, "solution", runtime, judge)]
        exploit = case.directory / "exploit"
        if exploit.is_dir():
            verdicts.append(run_reference(case, exploit, "exploit", runtime, judge))
        elif case.bash_checks:
            failed = True
            lines.append(f"{case.id:<26} FAIL: no exploit/ directory")
        for verdict in verdicts:
            failed = failed or not verdict.ok
            not_run += verdict.not_run
            status = "ok" if verdict.ok else "FAIL"
            lines.append(f"{case.id:<26} {verdict.kind:<9} {status}")
            lines += [f"    {line}" for line in verdict.lines]
    if not_run:
        lines.append(
            f"{not_run} judged item(s) not run: pass --judge to verify the claims "
            "and the judge-caught exploits"
        )
    return "\n".join(lines), 1 if failed else 0


def judge_from(transport_factory: Callable[[], Any]) -> JudgeFn:
    """A synchronous judge over the benchmark's own judge transport."""
    import asyncio

    from benchmarks.runner.judge import score

    transport = transport_factory()

    def judge(case: Case, diff: str, closing: str) -> Sequence[bool | None]:
        met, _reasons, _attempts = asyncio.run(
            score(case, diff, closing, transport=transport)
        )
        return met

    return judge
