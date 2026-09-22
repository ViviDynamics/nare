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
from typing import Any

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
                tokens_median=median_int(
                    [r.usage.get("input", 0) + r.usage.get("output", 0) for r in judged]
                ),
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
