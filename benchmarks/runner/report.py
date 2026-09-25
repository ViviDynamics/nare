"""Aggregation, baselines, and comparison. Pure: arithmetic and policy only."""

from __future__ import annotations

import json
import re
import statistics
import tomllib
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from benchmarks.runner.grade import CheckResult, Outcome


@dataclass(frozen=True)
class RepRecord:
    case: str
    rep: int
    outcome: Outcome
    checks: tuple[CheckResult, ...]
    judge_met: int | None
    judge_why: str
    status: str | None
    stop_reason: str | None
    turns: int | None
    usage: dict[str, int] = field(default_factory=dict)
    duration_s: float = 0.0
    model: str = ""
    # The rep's evidence directory, relative to benchmarks/results/. Empty in
    # results files written before evidence was kept.
    artifacts: str = ""


@dataclass(frozen=True)
class CaseSummary:
    case: str
    passes: int
    fails: int
    errors: int
    pass_rate: float | None
    inconclusive: bool
    tokens_median: int | None
    turns_median: int | None
    judge_met_median: int | None


def median_int(values: Sequence[int]) -> int | None:
    if not values:
        return None
    return int(round(statistics.median(values)))


def summarize(records: Sequence[RepRecord]) -> list[CaseSummary]:
    """Collapse repetitions into one summary per case.

    Errors are excluded from the pass rate rather than counted against it: a
    dead container says nothing about whether the task is achievable. When
    errors take the majority the case is inconclusive and has no pass rate at
    all, so it can never be silently scored.
    """
    grouped: dict[str, list[RepRecord]] = defaultdict(list)
    for rec in records:
        grouped[rec.case].append(rec)

    summaries: list[CaseSummary] = []
    for case in sorted(grouped):
        reps = grouped[case]
        passes = sum(1 for r in reps if r.outcome == "pass")
        fails = sum(1 for r in reps if r.outcome == "fail")
        errors = sum(1 for r in reps if r.outcome == "error")
        judged = [r for r in reps if r.outcome != "error"]
        inconclusive = errors > len(reps) / 2
        summaries.append(
            CaseSummary(
                case=case,
                passes=passes,
                fails=fails,
                errors=errors,
                pass_rate=(
                    None
                    if inconclusive or not (passes + fails)
                    else passes / (passes + fails)
                ),
                inconclusive=inconclusive,
                # usage holds exactly grade's four fields, cached input included.
                tokens_median=median_int([sum(r.usage.values()) for r in judged]),
                turns_median=median_int(
                    [r.turns for r in judged if r.turns is not None]
                ),
                judge_met_median=median_int(
                    [r.judge_met for r in judged if r.judge_met is not None]
                ),
            )
        )
    return summaries


@dataclass(frozen=True)
class BaselineMeta:
    blessed: str
    commit: str
    provider: str
    model: str
    judge_model: str


@dataclass(frozen=True)
class Baseline:
    meta: BaselineMeta
    cases: dict[str, CaseSummary]


def slugify(model: str) -> str:
    """Model names reach the filesystem, and they contain slashes and dots."""
    return re.sub(r"[^a-zA-Z0-9]+", "-", model).strip("-").lower()


def baseline_path(root: Path, model: str, tier: str) -> Path:
    return root / f"{slugify(model)}.{tier}.toml"


def _toml_str(value: str) -> str:
    # TOML basic strings and JSON strings share an escape syntax for every
    # character these values can contain, so json.dumps is a correct encoder
    # and a dependency-free one.
    return json.dumps(value)


def dumps_baseline(meta: BaselineMeta, summaries: Sequence[CaseSummary]) -> str:
    """Render a baseline file.

    Inconclusive cases are omitted: a baseline states what should happen, and
    "we could not tell" is not such a statement.
    """
    lines = [
        "# Blessed benchmark numbers. Reviewed like any other committed file.",
        "",
        "[meta]",
        f"blessed     = {_toml_str(meta.blessed)}",
        f"commit      = {_toml_str(meta.commit)}",
        f"provider    = {_toml_str(meta.provider)}",
        f"model       = {_toml_str(meta.model)}",
        f"judge_model = {_toml_str(meta.judge_model)}",
    ]
    for summary in summaries:
        if summary.inconclusive or summary.pass_rate is None:
            continue
        lines += [
            "",
            f"[case.{summary.case}]",
            f"pass_rate        = {summary.pass_rate}",
            f"reps             = {summary.passes + summary.fails}",
        ]
        for key, value in (
            ("tokens_median", summary.tokens_median),
            ("turns_median", summary.turns_median),
            ("judge_met_median", summary.judge_met_median),
        ):
            if value is not None:
                lines.append(f"{key:<16} = {value}")
    return "\n".join(lines) + "\n"


def load_baseline(path: Path) -> Baseline:
    raw: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
    meta_raw = raw.get("meta", {})
    meta = BaselineMeta(
        blessed=str(meta_raw.get("blessed", "")),
        commit=str(meta_raw.get("commit", "")),
        provider=str(meta_raw.get("provider", "")),
        model=str(meta_raw.get("model", "")),
        judge_model=str(meta_raw.get("judge_model", "")),
    )
    cases: dict[str, CaseSummary] = {}
    for name, body in raw.get("case", {}).items():
        reps = int(body.get("reps", 0))
        rate = float(body["pass_rate"])
        passes = int(round(rate * reps))
        cases[name] = CaseSummary(
            case=name,
            passes=passes,
            fails=reps - passes,
            errors=0,
            pass_rate=rate,
            inconclusive=False,
            tokens_median=body.get("tokens_median"),
            turns_median=body.get("turns_median"),
            judge_met_median=body.get("judge_met_median"),
        )
    return Baseline(meta=meta, cases=cases)


Verdict = Literal[
    "ok", "improved", "suspect", "regression", "inconclusive", "new", "missing"
]


@dataclass(frozen=True)
class CaseDelta:
    case: str
    verdict: Verdict
    summary: CaseSummary | None
    baseline: CaseSummary | None
    token_delta: float | None
    notes: tuple[str, ...]


@dataclass(frozen=True)
class Comparison:
    deltas: tuple[CaseDelta, ...]
    exit_code: int


def compare(
    summaries: Sequence[CaseSummary],
    baseline: Baseline,
    *,
    confirmed: bool = False,
    token_band: float = 0.10,
    strict_tokens: bool = False,
) -> Comparison:
    """Diff a run against a baseline.

    `confirmed` is the difference between "this looks wrong" and "this is
    wrong". A pass-rate drop is `suspect` on the first pass and becomes a
    `regression` only after the caller has re-run the case at higher reps, so
    ordinary sampling noise never turns the build red on its own.
    """
    by_case = {s.case: s for s in summaries}
    deltas: list[CaseDelta] = []
    failed = False

    for name in sorted(set(by_case) | set(baseline.cases)):
        current = by_case.get(name)
        before = baseline.cases.get(name)
        notes: list[str] = []
        token_delta: float | None = None

        if current is None:
            deltas.append(CaseDelta(name, "missing", None, before, None, ()))
            continue
        if current.inconclusive:
            failed = True
            total = current.passes + current.fails + current.errors
            deltas.append(
                CaseDelta(
                    name,
                    "inconclusive",
                    current,
                    before,
                    None,
                    (f"{current.errors} of {total} reps errored",),
                )
            )
            continue
        if before is None:
            deltas.append(CaseDelta(name, "new", current, None, None, ()))
            continue

        verdict: Verdict = "ok"
        if current.pass_rate is not None and before.pass_rate is not None:
            if current.pass_rate < before.pass_rate:
                verdict = "regression" if confirmed else "suspect"
                if confirmed:
                    failed = True
            elif current.pass_rate > before.pass_rate:
                verdict = "improved"

        if current.tokens_median and before.tokens_median:
            token_delta = (
                current.tokens_median - before.tokens_median
            ) / before.tokens_median
            if token_delta > token_band:
                notes.append(f"tokens +{token_delta:.0%}, outside the band")
                if strict_tokens:
                    failed = True

        if (
            current.judge_met_median is not None
            and before.judge_met_median is not None
            and current.judge_met_median < before.judge_met_median
        ):
            notes.append(
                f"judge {before.judge_met_median} -> {current.judge_met_median}"
            )

        deltas.append(
            CaseDelta(name, verdict, current, before, token_delta, tuple(notes))
        )

    return Comparison(deltas=tuple(deltas), exit_code=1 if failed else 0)


def _rate(summary: CaseSummary | None) -> str:
    if summary is None or summary.pass_rate is None:
        return "-"
    return f"{summary.passes}/{summary.passes + summary.fails}"


def render(comparison: Comparison) -> str:
    lines = [
        f"{'case':<24} {'pass':>7} {'tokens':>10} {'judge':>6}  verdict",
        "-" * 70,
    ]
    for delta in comparison.deltas:
        tokens = "-"
        if delta.summary and delta.summary.tokens_median is not None:
            tokens = f"{delta.summary.tokens_median:,}"
            if delta.token_delta is not None:
                tokens += f" {delta.token_delta:+.0%}"
        judge = "-"
        if delta.summary and delta.summary.judge_met_median is not None:
            judge = str(delta.summary.judge_met_median)
        lines.append(
            f"{delta.case:<24} {_rate(delta.summary):>7} {tokens:>10} "
            f"{judge:>6}  {delta.verdict}"
        )
        for note in delta.notes:
            lines.append(f"{'':<24} {note}")
    lines.append("-" * 70)

    bad = [d for d in comparison.deltas if d.verdict in ("regression", "inconclusive")]
    for delta in bad:
        lines.append(
            f"{delta.verdict.upper()}: {delta.case} "
            f"{_rate(delta.baseline)} -> {_rate(delta.summary)}"
        )
    if not bad:
        lines.append("no regressions")
    return "\n".join(lines)
