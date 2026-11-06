"""Aggregation, baselines, and comparison. Pure: arithmetic and policy only."""

from __future__ import annotations

import math
import re
import shlex
import statistics
import tomllib
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from benchmarks.runner.grade import USAGE_FIELDS, CheckResult, Outcome

# A cell blessed at or above this rate is a guard: the model should pass it, so
# a failure is a signal to look, and only a confirmed run of failures gates.
GUARD_FLOOR = 0.9
# Failures among the confirmation reps that make a guard regression. With the
# first pass at 3 reps and a true rate of 0.95, 2 of 10 is a 1.2% false alarm.
CONFIRM_FAILURES = 2
_Z95 = 1.959963984540054


def wilson_interval(passes: int, n: int) -> tuple[float, float] | None:
    """The 95% Wilson score interval for a pass count out of n, or None."""
    if n <= 0:
        return None
    z2 = _Z95 * _Z95
    p = passes / n
    denom = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = _Z95 * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def is_guard(blessed_rate: float | None) -> bool:
    """Whether a blessed rate is gated per cell. Every other cell is reported only."""
    return blessed_rate is not None and blessed_rate >= GUARD_FLOOR


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
    # The agent's --max-tokens; None is nare's own default, which every run
    # used before the cap could be set.
    max_tokens: int | None = None
    # The agent's provider and the judge, so bless and compare read them from
    # the results as they read the cap. Empty in results written before.
    provider: str = ""
    judge_model: str = ""
    # What the rep ran with and saw. Each defaults to what a results line
    # written before #156 means: no arguments, and a window and a compaction
    # count nobody read. A rep whose stream never reached nare's first turn
    # records both as None too: unknown, not a rep that never compacted.
    case_args: list[str] = field(default_factory=list)
    nare_args: list[str] = field(default_factory=list)
    nare_files: dict[str, str] = field(default_factory=dict)
    context_window: dict[str, int | str] | None = None
    compactions: int | None = None
    # The judge could not settle this rep (no answer, a refused reply, or an
    # "unknown" verdict that decides nothing). Such a rep is an error, and it
    # counts against the pass rate; an infrastructure error does not.
    judge_failed: bool = False
    # One verdict per claim (None for "unknown") and one reason per claim,
    # or None when the rep was not judged.
    judge_claims: list[bool | None] | None = None
    judge_reasons: list[str] = field(default_factory=list)
    # The judge and cap this rep was judged with, and the digest of the diff it
    # read: what calibration matches a human label against.
    judge_max_tokens: int | None = None
    diff_sha256: str = ""
    # Tool-call health from the rep's kept evidence (toolhealth.py): counts per
    # kind, with `session` and `events` saying which files were read. None when
    # neither was there, or the results predate the counts.
    tool_health: dict[str, int] | None = None


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
    # Reps the judge could not settle. They are inside the pass rate's
    # denominator, so a judge that fails on the reps the agent did worst
    # cannot make the rate look better.
    judge_errors: int = 0


def median_int(values: Sequence[int]) -> int | None:
    if not values:
        return None
    return int(round(statistics.median(values)))


def summarize(records: Sequence[RepRecord]) -> list[CaseSummary]:
    """Collapse repetitions into one summary per case.

    Infrastructure errors are excluded from the pass rate: a dead container
    says nothing about whether the task is achievable. Judge failures are not
    excluded: a rep the judge could not settle is a rep we could not verify as
    passing, and dropping those would make the rate depend on what the agent
    wrote. When errors take the majority the case is inconclusive and has no
    pass rate at all, so it can never be silently scored.
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
        judge_errors = sum(1 for r in reps if r.judge_failed)
        judged = [r for r in reps if r.outcome != "error"]
        decided = passes + fails + judge_errors
        inconclusive = errors > len(reps) / 2
        summaries.append(
            CaseSummary(
                case=case,
                passes=passes,
                fails=fails,
                errors=errors,
                pass_rate=(None if inconclusive or not decided else passes / decided),
                inconclusive=inconclusive,
                # Cached input included: a proxy serves most input from cache.
                tokens_median=median_int(
                    [sum(r.usage.get(f, 0) for f in USAGE_FIELDS) for r in judged]
                ),
                turns_median=median_int(
                    [r.turns for r in judged if r.turns is not None]
                ),
                judge_met_median=median_int(
                    [r.judge_met for r in judged if r.judge_met is not None]
                ),
                judge_errors=judge_errors,
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
    # The agent's output cap, None for nare's default. It changes what a run
    # measures, so numbers from another cap are not comparable.
    max_tokens: int | None = None
    # The run arguments the numbers were measured under, and each run file's
    # digest. Empty is the default variant, which every baseline blessed before
    # #156 is.
    nare_args: tuple[str, ...] = ()
    nare_files: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Baseline:
    meta: BaselineMeta
    cases: dict[str, CaseSummary]


def slugify(model: str) -> str:
    """Model names reach the filesystem, and they contain slashes and dots."""
    return re.sub(r"[^a-zA-Z0-9]+", "-", model).strip("-").lower()


def baseline_path(root: Path, model: str, tier: str) -> Path:
    return root / f"{slugify(model)}.{tier}.toml"


_TOML_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


def _toml_str(value: str) -> str:
    """A TOML basic string for any text, written to the file as UTF-8.

    Hand-written because json.dumps is not a TOML encoder for these values. By
    default it writes a character above U+FFFF as a surrogate pair, which TOML
    refuses to read back; with ensure_ascii=False it leaves DEL literal, which
    TOML also refuses. Run arguments and prompt paths are operator text, so
    either could reach here. Quote, backslash and the control characters are
    escaped, and every other character stays literal.
    """
    out: list[str] = []
    for char in value:
        if char in _TOML_ESCAPES:
            out.append(_TOML_ESCAPES[char])
        elif char < " " or char == "\x7f":
            out.append(f"\\u{ord(char):04x}")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


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
    if meta.max_tokens is not None:
        lines.append(f"max_tokens  = {meta.max_tokens}")
    if meta.nare_args:
        lines.append(
            "nare_args   = [" + ", ".join(_toml_str(a) for a in meta.nare_args) + "]"
        )
    if meta.nare_files:
        lines += ["", "[meta.nare_files]"]
        lines += [
            f"{_toml_str(path)} = {_toml_str(digest)}"
            for path, digest in meta.nare_files.items()
        ]
    for summary in summaries:
        if summary.inconclusive or summary.pass_rate is None:
            continue
        lines += [
            "",
            f"[case.{summary.case}]",
            f"passes           = {summary.passes}",
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
        max_tokens=meta_raw.get("max_tokens"),
        nare_args=tuple(str(a) for a in meta_raw.get("nare_args", ())),
        nare_files={str(k): str(v) for k, v in meta_raw.get("nare_files", {}).items()},
    )
    cases: dict[str, CaseSummary] = {}
    for name, body in raw.get("case", {}).items():
        reps = int(body.get("reps", 0))
        if "passes" in body:
            passes = int(body["passes"])
            if not 0 <= passes <= reps:
                raise ValueError(f"{name}: passes {passes} is not within {reps} reps")
            rate: float | None = passes / reps if reps else None
        else:
            # Baselines blessed before counts were stored hold a rate.
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

    Only guard cells (blessed at GUARD_FLOOR or more) are gated on pass rate.
    On the first pass, a guard cell with any failure is `suspect`, and the
    caller re-runs it. Its confirmation reps are then judged alone: at
    CONFIRM_FAILURES or more failures it is a `regression`. Every other cell
    is reported with its interval and never fails on rate, since a rate
    measured in a handful of reps cannot separate a drop from noise.
    `confirmed` says the caller has re-run whatever was `suspect`.
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
            if is_guard(before.pass_rate) and current.fails > 0:
                if not confirmed:
                    verdict = "suspect"
                    notes.append(
                        f"{current.fails} of {current.passes + current.fails} "
                        "failed; re-running to confirm"
                    )
                elif current.fails >= CONFIRM_FAILURES:
                    verdict = "regression"
                    failed = True
                else:
                    notes.append(
                        f"{current.fails} failure(s) in "
                        f"{current.passes + current.fails} reps, below the "
                        f"{CONFIRM_FAILURES} that confirm a regression"
                    )
            elif (
                not is_guard(before.pass_rate) and current.pass_rate < before.pass_rate
            ):
                notes.append(f"below the blessed {_rate(before)}; not a gated cell")
            if verdict == "ok" and _beats(current, before):
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


def _beats(current: CaseSummary, before: CaseSummary) -> bool:
    """Whether current's interval lies wholly above the baseline's."""
    now = wilson_interval(current.passes, current.passes + current.fails)
    then = wilson_interval(before.passes, before.passes + before.fails)
    return now is not None and then is not None and now[0] > then[1]


def _rate(summary: CaseSummary | None, *, interval: bool = False) -> str:
    """`passes/n`, and with `interval` the 95% Wilson interval beside it."""
    if summary is None or summary.pass_rate is None:
        return "-"
    n = summary.passes + summary.fails
    text = f"{summary.passes}/{n}"
    if interval:
        lo, hi = wilson_interval(summary.passes, n) or (0.0, 1.0)
        text += f" [{lo:.2f}, {hi:.2f}]"
    return text


def render(comparison: Comparison) -> str:
    header = f"{'case':<24} {'pass [95% interval]':>21} {'tokens':>10} {'judge':>6}"
    lines = [f"{header}  verdict", "-" * 84]
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
            f"{delta.case:<24} {_rate(delta.summary, interval=True):>21} {tokens:>10} "
            f"{judge:>6}  {delta.verdict}"
        )
        if delta.summary is not None and delta.summary.judge_errors:
            lines.append(
                f"{'':<24} the judge could not settle {delta.summary.judge_errors} "
                "rep(s); counted as not passing"
            )
        for note in delta.notes:
            lines.append(f"{'':<24} {note}")
    lines.append("-" * 84)

    bad = [d for d in comparison.deltas if d.verdict in ("regression", "inconclusive")]
    for delta in bad:
        lines.append(
            f"{delta.verdict.upper()}: {delta.case} "
            f"{_rate(delta.baseline, interval=True)} -> "
            f"{_rate(delta.summary, interval=True)}"
        )
    if not bad:
        lines.append("no regressions")
    return "\n".join(lines)


def render_variant(args: Sequence[str], files: Mapping[str, str]) -> str:
    """A run's variant as one line: `none`, or its command line.

    Each run file follows with the first 12 hex digits of its digest, so an
    edited prompt at one path reads as different from the one blessed.
    """
    if not args:
        return "none"
    text = shlex.join(args)
    if files:
        # "sha256:" is 7 characters, and 12 hex digits follow it.
        digests = ", ".join(f"{path} {digest[:19]}" for path, digest in files.items())
        text += f" ({digests})"
    return text


def render_matrix(
    columns: Sequence[tuple[str, Comparison]],
    judge: str,
    variants: Mapping[str, str] | None = None,
) -> str:
    """Cases down the side, models across: the table a PR pastes.

    Markdown, so it pastes into a PR or docs/evidence as printed. A cell is
    the pass rate with its n, marked when it is a confirmed regression or
    inconclusive, and `-` when that model did not run the case. `variants`
    maps a model to its `render_variant` text, and the note names each one
    when any is not the default.
    """
    cells: dict[str, dict[str, str]] = defaultdict(dict)
    for model, comparison in columns:
        for delta in comparison.deltas:
            if delta.summary is None:
                continue
            if delta.verdict == "inconclusive":
                cell = "inconclusive"
            elif delta.verdict == "regression":
                cell = f"{_rate(delta.summary, interval=True)} regression"
            else:
                cell = _rate(delta.summary, interval=True)
            cells[delta.case][model] = cell
    models = [model for model, _ in columns]
    rows = [["case", *models]]
    rows += [
        [case, *(cells[case].get(m, "-") for m in models)] for case in sorted(cells)
    ]
    widths = [max(3, *(len(row[i]) for row in rows)) for i in range(len(rows[0]))]

    def line(row: Sequence[str]) -> str:
        padded = (cell.ljust(width) for cell, width in zip(row, widths, strict=True))
        return "| " + " | ".join(padded) + " |"

    lines = [line(rows[0]), line(["-" * width for width in widths])]
    lines += [line(row) for row in rows[1:]]
    note = f"Judge: {judge}, for every model."
    if judge in models:
        note += " It judged its own repetitions too."
    if variants and any(text != "none" for text in variants.values()):
        named = "; ".join(f"{m}: {variants.get(m, 'none')}" for m in models)
        note += f" Run arguments: {named}."
    return "\n".join([*lines, "", note])
