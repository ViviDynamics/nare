"""Judge the designed attempts a calibration label names (`calibrate judge`).

Labels are keyed on `(case, claim, diff_sha256)`, so the judge can only be
scored on the diffs that were labelled. A fresh run makes new diffs, which no
label names. A case's own `solution/` and `exploit/` are different: applied in
a fresh sandbox, they give the same diff byte for byte, so the judge can be
re-run over them whenever the judge or its model changes.

This module takes those captured attempts and a judge, and returns results
lines in the format `calibrate score --results` reads. An attempt whose diff no
label names is not judged, so no call is spent on it. A judge that cannot
answer is a failed rep with no verdicts: it is never read as a verdict, and it
contributes nothing to the rates.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path

from benchmarks.runner.calibration import Label
from benchmarks.runner.case import Case
from benchmarks.runner.compare import Evidence
from benchmarks.runner.reference import JudgeFn
from benchmarks.runner.report import RepRecord

# What a judged attempt records as its model, so its lines are never read as
# an agent's reps.
REFERENCE_MODEL = "reference:{kind}"
CAPTURE_ATTEMPTS = 3


@dataclass(frozen=True)
class JudgedReferences:
    records: list[RepRecord] = field(default_factory=list)
    # (case, kind) captured but not judged, because no label names its diff.
    skipped: list[tuple[str, str]] = field(default_factory=list)


def labelled_cases(labels: Sequence[Label], cases: Mapping[str, Case]) -> list[str]:
    """The judged cases a label names, sorted: the only ones worth capturing."""
    return sorted(
        {
            lab.case
            for lab in labels
            if lab.case in cases and cases[lab.case].judge is not None
        }
    )


def judge_references(
    captured: Mapping[tuple[str, str], Evidence],
    labels: Sequence[Label],
    cases: Mapping[str, Case],
    judge: JudgeFn,
    *,
    judge_model: str,
    judge_max_tokens: int | None = None,
    repeats: int = 1,
) -> JudgedReferences:
    """Judge each captured attempt whose diff a label names, `repeats` times."""
    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    named = {(lab.case, lab.diff_sha256) for lab in labels}
    result = JudgedReferences()
    for (case_id, kind), evidence in sorted(captured.items()):
        digest = hashlib.sha256(evidence.diff.encode("utf-8")).hexdigest()
        case = cases.get(case_id)
        if case is None or case.judge is None or (case_id, digest) not in named:
            result.skipped.append((case_id, kind))
            continue
        for rep in range(1, repeats + 1):
            claims: list[bool | None] | None
            try:
                claims = list(judge(case, evidence.diff, evidence.closing))
                why = ""
            except Exception as exc:  # any judge that cannot answer
                claims, why = None, f"the judge did not answer: {exc}"
            result.records.append(
                RepRecord(
                    case=case_id,
                    rep=rep,
                    outcome="error" if claims is None else "pass",
                    checks=(),
                    judge_met=None if claims is None else sum(1 for c in claims if c),
                    judge_why=why,
                    status=None,
                    stop_reason=None,
                    turns=None,
                    model=REFERENCE_MODEL.format(kind=kind),
                    judge_model=judge_model,
                    judge_failed=claims is None,
                    judge_claims=claims,
                    judge_max_tokens=judge_max_tokens,
                    diff_sha256=digest,
                )
            )
    return result


# --- capture (Docker) ----------------------------------------------------


class _CaptureRuntime:
    """The runner's Docker runtime, keeping the diff and closing message it
    takes. A checks-caught exploit never reaches the judge, so the judge
    callback cannot be the capture point."""

    def __init__(self) -> None:
        from benchmarks.runner.reference import DockerRuntime

        self._inner = DockerRuntime()
        self.taken: str | None = None
        self.closing: str | None = None

    def sandbox(self, case: Case) -> AbstractContextManager[tuple[Path, Path]]:
        return self._inner.sandbox(case)

    def checkpoint(self, workdir: Path) -> None:
        self._inner.checkpoint(workdir)

    def apply(self, workdir: Path, artifacts: Path, directory: Path) -> int:
        return self._inner.apply(workdir, artifacts, directory)

    def write_out(self, workdir: Path, artifacts: Path, name: str, text: str) -> None:
        self._inner.write_out(workdir, artifacts, name, text)
        if name == "closing.txt":
            self.closing = text

    def diff(self, workdir: Path) -> str:
        self.taken = self._inner.diff(workdir)
        return self.taken

    def check(self, workdir: Path, cmd: str, artifacts: Path) -> tuple[int, str]:
        return self._inner.check(workdir, cmd, artifacts)


def capture_reference(
    cases: Mapping[str, Case], case_ids: Sequence[str]
) -> dict[tuple[str, str], Evidence]:
    """Each named case's solution and exploit, applied in a fresh sandbox.

    The diff and closing message are taken the way a rep's are, by the runner's
    own reference path. No judge is called here.
    """
    from benchmarks.runner.reference import run_reference

    captured: dict[tuple[str, str], Evidence] = {}
    for case_id in case_ids:
        case = cases[case_id]
        for kind in ("solution", "exploit"):
            directory = case.directory / kind
            if not directory.is_dir():
                continue
            verdict = None
            # A checkpoint can fail transiently under Docker (seen during #46:
            # "is not a valid object" on a fresh copy), which run_reference
            # reports as a verdict instead of raising. Retry, then give up loudly.
            for _attempt in range(CAPTURE_ATTEMPTS):
                runtime = _CaptureRuntime()
                verdict = run_reference(case, directory, kind, runtime, None)
                if runtime.taken is not None and runtime.closing is not None:
                    captured[(case_id, kind)] = Evidence(
                        runtime.taken, runtime.closing, []
                    )
                    break
            if (case_id, kind) not in captured:
                raise RuntimeError(
                    f"{case_id} {kind}: no diff was captured: "
                    f"{verdict.lines if verdict else ()}"
                )
    return captured
