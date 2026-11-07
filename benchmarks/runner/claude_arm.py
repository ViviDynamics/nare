"""`python -m benchmarks.runner.claude_arm`: the Claude Code arm of #114, run
by hand. It is not part of CI: it needs a key and costs money.

Each repetition is the nare arm's repetition, with the agent swapped: the same
fixture copy, the same checks in the same image, the same judge, and the same
grading. Only the container that runs the agent differs, and what it runs. The
nare arm's code path is not touched, so what was measured is what shipped.

Results go to benchmarks/results/claude-code/<stamp>/, which `compare` and
`bless` never read as the newest nare run.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import statistics
import subprocess
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2

from benchmarks.runner.ab import CaseAB, mantel_haenszel, paired_ratio, token_pairs
from benchmarks.runner.case import TIER_CHOICES, Case, load_cases
from benchmarks.runner.claude_code import (
    COMPARABLE_CASES,
    DISALLOWED_TOOLS,
    EXCLUDED_CASES,
    ModelPrice,
    agent_script,
    dollars,
    parse_stream,
    price_from_model_info,
    to_result_line,
)
from benchmarks.runner.config import ConfigError, require_judge, resolve_config
from benchmarks.runner.grade import (
    CheckResult,
    ResultLine,
    grade_bash_check,
    grade_result_check,
    infra_error,
    rep_outcome,
)
from benchmarks.runner.judge import JudgeError, judge_transport, render_attempts, score
from benchmarks.runner.models import proxy_root
from benchmarks.runner.provenance import case_hash, image_id, judge_cause
from benchmarks.runner.report import RepRecord, median_int, summarize, wilson_interval
from benchmarks.runner.sandbox import (
    _LEFTOVERS,
    DID_NOT_RUN,
    IMAGE,
    SandboxError,
    _docker_run,
    build_image,
    git_diff,
    repo_root,
    run_check,
    sandbox,
    write_closing,
)
from nare.transport import Transport

ARM = "claude-code"
CLAUDE_IMAGE = "nare-bench-claude:local"
DOCKERFILE = "benchmarks/Dockerfile.claude-code"
DEFAULT_MODEL = "claude-haiku-5-5"
RESULTS_DIR = "claude-code"
# The agent's environment. Telemetry, updates and non-essential traffic are off
# so a rep makes only the model calls the run is about; the small-fast model is
# pinned to the model under test, so no call goes to a model the arm did not name.
AGENT_ENV = {
    "DISABLE_AUTOUPDATER": "1",
    "DISABLE_TELEMETRY": "1",
    "DISABLE_ERROR_REPORTING": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
}


class ArmError(Exception):
    """A configuration fault the arm refuses to run through. Exit 2."""


def gateway_root(base_url: str) -> str:
    """The proxy's root, with any trailing /v1 removed, as `models` reads it."""
    return proxy_root(base_url)


def fetch_model_info(base_url: str, api_key: str, model: str) -> dict[str, Any]:
    """The gateway's entry for one model, prices included. Read at run time."""
    response = httpx2.get(
        f"{gateway_root(base_url)}/v1/model/info",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=60,
    )
    response.raise_for_status()
    for entry in response.json().get("data", []):
        if entry.get("model_name") == model:
            info = entry.get("model_info")
            return info if isinstance(info, dict) else {}
    raise ArmError(f"the endpoint does not list {model}")


def build_arm_image(root: Path) -> None:
    proc = subprocess.run(
        [
            "docker",
            "build",
            "-q",
            "-f",
            str(root / DOCKERFILE),
            "-t",
            CLAUDE_IMAGE,
            str(root),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SandboxError(f"docker build failed:\n{proc.stderr.strip()}")


def claude_version() -> str:
    """The Claude Code version the image reports. Refused if it cannot say:
    the note names a version, and an empty one would leave it unpinned."""
    proc = subprocess.run(
        ["docker", "run", "--rm", CLAUDE_IMAGE, "claude", "--version"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise ArmError(f"could not read the Claude Code version: {proc.stderr.strip()}")
    return proc.stdout.strip()


@dataclass(frozen=True)
class _Agent:
    code: int
    timed_out: bool
    duration_s: float


def _run_agent(
    case: Case, workdir: Path, artifacts: Path, model: str, base_url: str
) -> _Agent:
    started = time.monotonic()
    code, _out, _err, timed_out = _docker_run(
        workdir,
        ["sh", "-c", agent_script(), "claude-arm"],
        case.timeout,
        artifacts=artifacts,
        image=CLAUDE_IMAGE,
        env={
            "BENCH_PROMPT": case.prompt,
            "BENCH_MODEL": model,
            "BENCH_MAX_TURNS": str(case.max_turns),
            "BENCH_LEFTOVERS": _LEFTOVERS,
            "BENCH_DISALLOWED": DISALLOWED_TOOLS,
            "ANTHROPIC_BASE_URL": gateway_root(base_url),
            "ANTHROPIC_SMALL_FAST_MODEL": model,
            **AGENT_ENV,
        },
        # The key reaches the container by name, never in argv.
        passthrough=("ANTHROPIC_API_KEY",),
    )
    return _Agent(code, timed_out, time.monotonic() - started)


def _errored(
    case: Case,
    rep: int,
    model: str,
    line: ResultLine | None,
    why: str,
    cause: str,
    duration_s: float,
    kept_ref: str,
    tool_calls: dict[str, int],
    provenance: dict[str, Any],
    judge_model: str,
) -> RepRecord:
    empty = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    return RepRecord(
        case=case.id,
        rep=rep,
        outcome="error",
        checks=(),
        judge_met=None,
        judge_why=why,
        status=None if line is None else line.status,
        stop_reason=None if line is None else line.stop_reason,
        turns=None if line is None else line.turns,
        usage=empty if line is None else line.usage,
        duration_s=duration_s,
        model=model,
        artifacts=kept_ref,
        provider=ARM,
        judge_model=judge_model,
        case_args=list(case.args),
        tool_calls=tool_calls,
        cost=None if line is None else line.cost,
        cost_estimated=True,
        error_cause=cause,
        **provenance,
    )


async def run_claude_rep(
    case: Case,
    *,
    rep: int,
    model: str,
    base_url: str,
    price: ModelPrice,
    transport: Transport,
    judge_model: str,
    evidence: Path,
    image: str,
) -> RepRecord:
    kept = evidence / f"{case.id}-{rep}"
    kept_ref = kept.relative_to(evidence.parent).as_posix()
    provenance: dict[str, Any] = {
        "nare_version": "",
        "nare_commit": "",
        "case_hash": case_hash(case),
        "image_id": image,
        "started": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    with sandbox(case) as (workdir, artifacts):
        try:
            agent = _run_agent(case, workdir, artifacts, model, base_url)
            stream_path = artifacts / "stream.jsonl"
            stream = (
                stream_path.read_text(encoding="utf-8", errors="replace")
                if stream_path.exists()
                else ""
            )
            run = parse_stream(stream)
            line = to_result_line(run, price)
            if line is None and not agent.timed_out and agent.code in DID_NOT_RUN:
                # Docker never ran the agent: every rep would be the same fault.
                raise SandboxError(
                    f"docker did not run the agent (exit {agent.code}) "
                    f"for {case.id} rep {rep}"
                )
            if line is None or infra_error(line):
                if line is None:
                    why = "timed out" if agent.timed_out else "no result object"
                    cause = "timeout" if agent.timed_out else "no_result_line"
                else:
                    why = line.error or "run errored"
                    cause = "agent_http_error"
                return _errored(
                    case,
                    rep,
                    model,
                    line,
                    why,
                    cause,
                    agent.duration_s,
                    kept_ref,
                    run.tool_calls,
                    provenance,
                    judge_model,
                )

            diff = git_diff(workdir)
            closing = run.closing
            (artifacts / "diff.patch").write_text(diff, encoding="utf-8")
            (artifacts / "closing.txt").write_text(closing, encoding="utf-8")
            try:
                write_closing(workdir, artifacts, closing)
            except SandboxError as exc:
                return _errored(
                    case,
                    rep,
                    model,
                    line,
                    str(exc),
                    "sandbox_error",
                    agent.duration_s,
                    kept_ref,
                    run.tool_calls,
                    provenance,
                    judge_model,
                )
            graded: list[CheckResult] = [
                grade_result_check(check, line)
                for check in case.checks
                if check.kind == "result"
            ]
            for check in case.bash_checks:
                code, output = run_check(
                    workdir, check.cmd or "", 120, artifacts=artifacts
                )
                graded.append(grade_bash_check(check, code, output))

            met: list[bool | None] | None = None
            reasons: list[str] = []
            why = ""
            judge_cause_text: str | None = None
            attempts: tuple[Any, ...] = ()
            if case.judge is not None:
                try:
                    met, reasons, attempts = await score(
                        case, diff, closing, transport=transport
                    )
                    why = " ".join(f"{n}. {r}" for n, r in enumerate(reasons, start=1))
                except JudgeError as exc:
                    why = f"judge failed: {exc}"
                    judge_cause_text = judge_cause(exc)
                    attempts = exc.attempts
                (artifacts / "judge.txt").write_text(
                    render_attempts(attempts), encoding="utf-8"
                )
        finally:
            shutil.copytree(artifacts, kept, dirs_exist_ok=True)

    outcome = rep_outcome(graded, case.judge, met)
    gating = case.judge is not None and case.judge.min_met is not None
    return RepRecord(
        case=case.id,
        rep=rep,
        outcome=outcome,
        checks=tuple(graded),
        judge_met=None if met is None else sum(1 for v in met if v is True),
        judge_why=why,
        status=line.status,
        stop_reason=line.stop_reason,
        turns=line.turns,
        usage=line.usage,
        duration_s=agent.duration_s,
        model=model,
        artifacts=kept_ref,
        provider=ARM,
        judge_model=judge_model if case.judge is not None else "",
        case_args=list(case.args),
        judge_failed=gating and outcome == "error",
        judge_claims=met,
        judge_reasons=reasons,
        judge_max_tokens=None,
        diff_sha256=hashlib.sha256(diff.encode("utf-8")).hexdigest(),
        tool_calls=run.tool_calls,
        cost=line.cost,
        cost_estimated=True,
        error_cause=judge_cause_text,
        **provenance,
    )


def _write_records(path: Path, records: Sequence[RepRecord]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for rec in records:
            payload = asdict(rec)
            payload["checks"] = [asdict(c) for c in rec.checks]
            handle.write(json.dumps(payload) + "\n")


def read_records(path: Path) -> list[RepRecord]:
    """A results file written by `_write_records` or by nare's runner."""
    out: list[RepRecord] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        payload = json.loads(raw)
        checks = tuple(CheckResult(**c) for c in payload.pop("checks", []))
        known = {f for f in RepRecord.__dataclass_fields__}
        out.append(
            RepRecord(checks=checks, **{k: v for k, v in payload.items() if k in known})
        )
    return out


def _stratum(
    case: str, arm_a: Sequence[RepRecord], arm_b: Sequence[RepRecord]
) -> CaseAB | None:
    """One case as a 2x2 stratum: `head` is the nare arm, `base` the Claude arm.
    A case that only one arm ran has no stratum."""
    if not arm_a or not arm_b:
        return None
    sa, sb = summarize(arm_a)[0], summarize(arm_b)[0]
    if sa.inconclusive or sb.inconclusive:
        return None
    na = sa.passes + sa.fails + sa.judge_errors
    nb = sb.passes + sb.fails + sb.judge_errors
    return CaseAB(
        case=case,
        head_pass=sa.passes,
        head_n=na,
        base_pass=sb.passes,
        base_n=nb,
        head_tokens=sa.tokens_median,
        base_tokens=sb.tokens_median,
        head_cost=sa.cost,
        base_cost=sb.cost,
        head_cost_reps=(sa.passes + sa.fails + sa.errors) - sa.cost_unknown,
        base_cost_reps=(sb.passes + sb.fails + sb.errors) - sb.cost_unknown,
    )


def compare_arms(
    nare: Sequence[RepRecord], claude: Sequence[RepRecord], price: ModelPrice
) -> dict[str, Any]:
    """Per-case and pooled figures for the two arms, in the note's terms.

    Pass rates count what `summarize` counts: a judge that could not settle a
    rep is inside the denominator. Dollars are recomputed from each arm's usage
    at the same gateway prices by the caller, so the two arms' own cost figures
    are never compared.
    """
    by_case: dict[str, dict[str, list[RepRecord]]] = defaultdict(
        lambda: {"nare": [], "claude": []}
    )
    for rec in _reprice(nare, price):
        by_case[rec.case]["nare"].append(rec)
    for rec in _reprice(claude, price):
        by_case[rec.case]["claude"].append(rec)
    rows: list[dict[str, Any]] = []
    strata: list[CaseAB] = []
    for case in sorted(by_case):
        arms = by_case[case]
        row: dict[str, Any] = {"case": case}
        for label in ("nare", "claude"):
            recs = arms[label]
            decided = [r for r in recs if r.outcome != "error"]
            passes = sum(1 for r in recs if r.outcome == "pass")
            fails = sum(1 for r in recs if r.outcome == "fail")
            judge_errors = sum(1 for r in recs if r.judge_failed)
            n = passes + fails + judge_errors
            low: float | None = None
            high: float | None = None
            if n:
                interval = wilson_interval(passes, n)
                if interval is not None:
                    low, high = interval
            row[label] = {
                "reps": len(recs),
                "errors": len(recs) - len(decided),
                "passes": passes,
                "n": n,
                "low": low,
                "high": high,
                "usage_median": {
                    f: _median([r.usage.get(f, 0) for r in decided])
                    for f in ("input", "output", "cache_read", "cache_write")
                },
                "tokens_median": _median(
                    [
                        sum(
                            r.usage.get(f, 0)
                            for f in ("input", "output", "cache_read", "cache_write")
                        )
                        for r in decided
                    ]
                ),
                "turns_median": _median(
                    [r.turns for r in decided if r.turns is not None]
                ),
                "wall_median_s": _median_f([r.duration_s for r in decided]),
                "stop_reasons": _count(
                    [r.stop_reason or r.status or "none" for r in recs]
                ),
                "dollars_total": sum(r.cost or 0.0 for r in recs),
                "dollars_per_solved": None,
            }
            if passes:
                row[label]["dollars_per_solved"] = row[label]["dollars_total"] / passes
        rows.append(row)
        stratum = _stratum(case, arms["nare"], arms["claude"])
        if stratum is not None:
            strata.append(stratum)
    mh = mantel_haenszel(strata) if strata else None
    tokens = paired_ratio(token_pairs(strata)) if strata else None
    return {
        "rows": rows,
        "mantel_haenszel": None if mh is None else asdict(mh),
        "nare_over_claude_tokens_geomean": None if tokens is None else list(tokens),
    }


def _median(values: Sequence[int]) -> int | None:
    return median_int(list(values))


def _median_f(values: Sequence[float]) -> float | None:
    return float(statistics.median(values)) if values else None


def _count(values: Sequence[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


def _reprice(records: Sequence[RepRecord], price: ModelPrice) -> list[RepRecord]:
    """The same records with cost recomputed from their usage at one price."""
    from dataclasses import replace

    return [
        replace(r, cost=dollars(r.usage, price), cost_estimated=True) for r in records
    ]


async def _run_all(
    cases: Sequence[Case],
    *,
    sink: Callable[[RepRecord], None],
    reps: dict[str, int | None],
    model: str,
    base_url: str,
    price: ModelPrice,
    transport: Transport,
    judge_model: str,
    evidence: Path,
    image: str,
) -> None:
    """Each rep goes to `sink` as it finishes, so a stop keeps what was paid for."""
    for case in cases:
        chosen = reps.get(case.id)
        n = chosen if chosen is not None else case.reps
        for rep in range(n):
            sink(
                await run_claude_rep(
                    case,
                    rep=rep,
                    model=model,
                    base_url=base_url,
                    price=price,
                    transport=transport,
                    judge_model=judge_model,
                    evidence=evidence,
                    image=image,
                )
            )


def _record_payload(record: RepRecord) -> dict[str, Any]:
    payload = asdict(record)
    payload["checks"] = [asdict(c) for c in record.checks]
    return payload


def _git_head(root: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _tools_seen(evidence: Path) -> dict[str, list[str]]:
    """The tools each rep's init message reported, by rep. Recorded so the
    subagent and web-tool exclusions can be checked against what ran."""
    seen: dict[str, list[str]] = {}
    for stream in sorted(evidence.glob("*/stream.jsonl")):
        for raw in stream.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if (
                isinstance(obj, dict)
                and obj.get("type") == "system"
                and obj.get("subtype") == "init"
            ):
                seen[stream.parent.name] = [str(t) for t in obj.get("tools", [])]
                break
    return seen


def comparable_ids(root: Path, tier: str) -> list[str]:
    """The comparable cases of `tier`, in COMPARABLE_CASES order."""
    in_tier = {c.id for c in load_cases(root / "benchmarks" / "cases", tier=tier)}
    return [case for case in COMPARABLE_CASES if case in in_tier]


def selected_ids(
    root: Path, tier: str | None, named: Sequence[str] | None
) -> list[str]:
    """Which comparable cases a run takes, or raises ValueError saying why not.

    With no --case, a run takes its tier's comparable cases: `full` when no tier
    is named, as before the extended tier existed. With --case, the named cases
    must be comparable, and inside the named tier when one is given.
    """
    if not named:
        return comparable_ids(root, tier or "full")
    refused = [c for c in named if c not in COMPARABLE_CASES]
    if refused:
        raise ValueError(f"not comparable, see --list: {', '.join(refused)}")
    if tier is not None:
        inside = set(comparable_ids(root, tier))
        outside = [c for c in named if c not in inside]
        if outside:
            raise ValueError(f"not in tier {tier}: {', '.join(outside)}")
    return list(named)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m benchmarks.runner.claude_arm",
        description=__doc__.split("\n")[0],
    )
    p.add_argument(
        "--case",
        action="append",
        help="a comparable case id (repeatable); default: all of them",
    )
    p.add_argument(
        "--tier",
        choices=TIER_CHOICES,
        help="the comparable cases of this tier; extended is the opt-in tier of "
        "#97, the arm's first user (default: full, the nine cases as before)",
    )
    p.add_argument(
        "--reps",
        type=int,
        help="repetitions per case; default: each case's own (smoke 3, full 5, "
        "extended 3)",
    )
    p.add_argument("--model", default=os.environ.get("NARE_MODEL") or DEFAULT_MODEL)
    p.add_argument("--base-url", help="the gateway root (env: NARE_BASE_URL)")
    p.add_argument(
        "--judge-model",
        help="the judge (env: NARE_BENCH_JUDGE_MODEL); never the model under test",
    )
    p.add_argument(
        "--list",
        action="store_true",
        help="print the comparable and excluded cases, and exit",
    )
    p.add_argument("--out", help="results directory name (default: a timestamp)")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = repo_root()
    if args.list:
        tiers = {c.id: c.tier for c in load_cases(root / "benchmarks" / "cases")}
        for case in COMPARABLE_CASES:
            print(f"compared  {case}  [{tiers.get(case, '?')}]")
        for case, why in EXCLUDED_CASES.items():
            print(f"excluded  {case}: {why}")
        return 0
    try:
        ids = selected_ids(root, args.tier, args.case)
    except ValueError as exc:
        print(str(exc))
        return 2
    try:
        config = resolve_config(
            model=args.model,
            provider="anthropic",
            base_url=args.base_url,
            judge_model=args.judge_model,
            env=os.environ,
        )
    except ConfigError as exc:
        print(f"config: {exc}")
        return 2
    if not config.base_url:
        print(
            "config: no gateway: pass --base-url or set NARE_BASE_URL "
            "(the root, without /v1)"
        )
        return 2
    cases = [c for c in load_cases(root / "benchmarks" / "cases") if c.id in ids]
    refusal = require_judge(config, cases)
    if refusal:
        print(f"config: {refusal}")
        return 2
    if config.max_tokens is not None:
        # The Claude agent takes no output cap from the bench, so an arm run
        # under one would be the nare arm alone under it. Refuse, do not mislead.
        print(
            "config: NARE_BENCH_MAX_TOKENS is set, and the Claude Code arm cannot "
            "apply it; unset it so both arms run uncapped"
        )
        return 2

    try:
        info = fetch_model_info(config.base_url, config.api_key or "", args.model)
        price = price_from_model_info(info)
    except (ArmError, ValueError, httpx2.HTTPError, httpx2.InvalidURL) as exc:
        print(f"prices: {exc}")
        return 2

    stamp = args.out or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    evidence = root / "benchmarks" / "results" / RESULTS_DIR / stamp
    evidence.mkdir(parents=True, exist_ok=True)
    build_image(root)
    build_arm_image(root)
    version = claude_version()
    transport = judge_transport(config)
    reps = {c.id: args.reps for c in cases}
    started = datetime.now(UTC).isoformat(timespec="seconds")
    manifest = {
        "arm": ARM,
        "claude_code_version": version,
        "image": CLAUDE_IMAGE,
        "image_id": image_id(CLAUDE_IMAGE),
        "checks_image": IMAGE,
        "checks_image_id": image_id(IMAGE),
        "nare_commit": _git_head(root),
        "model": args.model,
        "judge_model": config.judge_model,
        "gateway": gateway_root(config.base_url),
        "price_per_token": asdict(price),
        "model_info": info,
        "disallowed_tools": DISALLOWED_TOOLS,
        "agent_script": agent_script(),
        "tier": args.tier,
        "cases": ids,
        "reps": {c.id: (args.reps if args.reps is not None else c.reps) for c in cases},
        "excluded": EXCLUDED_CASES,
        "started": started,
        "finished": None,
    }
    manifest_path = evidence / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    results_path = evidence / "results.jsonl"
    with results_path.open("w", encoding="utf-8") as handle:

        def sink(record: RepRecord) -> None:
            # Written as each rep finishes, so a stop or a crash keeps the reps
            # already paid for; `report` reads whatever is there.
            handle.write(json.dumps(_record_payload(record)) + "\n")
            handle.flush()

        asyncio.run(
            _run_all(
                cases,
                sink=sink,
                reps=reps,
                model=args.model,
                base_url=config.base_url,
                price=price,
                transport=transport,
                judge_model=config.judge_model or "",
                evidence=evidence,
                image=image_id(CLAUDE_IMAGE),
            )
        )
    manifest["tools_seen"] = _tools_seen(evidence)
    manifest["finished"] = datetime.now(UTC).isoformat(timespec="seconds")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"results: {evidence / 'results.jsonl'}")
    return 0


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _ci(passes: int, n: int, low: float | None, high: float | None) -> str:
    if not n or low is None or high is None:
        return f"{passes}/{n}"
    return f"{passes}/{n} [{low:.2f}, {high:.2f}]"


def _k(value: int | None) -> str:
    return "n/a" if value is None else f"{value:,}"


def _usd(value: float | None) -> str:
    return "n/a" if value is None else f"${value:.4f}"


def render_markdown(comparison: dict[str, Any]) -> str:
    """The note's tables: pass rates, tokens, time and stop reasons per case."""
    head = (
        "| Case | Arm | Reps | Pass (n) [95% Wilson] | Errors | Input | Output "
        "| Cache read | Cache write | Turns | Wall s | $/rep | $/solved |"
    )
    rule = (
        "| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: "
        "| ---: | ---: | ---: | ---: |"
    )
    lines = [head, rule]
    arms = (("nare", "nare"), ("claude", "Claude Code"))
    for row in comparison["rows"]:
        for label, name in arms:
            arm = row[label]
            usage = arm["usage_median"]
            per_rep = arm["dollars_total"] / arm["reps"] if arm["reps"] else None
            cells = [
                row["case"],
                name,
                str(arm["reps"]),
                _ci(arm["passes"], arm["n"], arm["low"], arm["high"]),
                str(arm["errors"]),
                _k(usage["input"]),
                _k(usage["output"]),
                _k(usage["cache_read"]),
                _k(usage["cache_write"]),
                _k(arm["turns_median"]),
                _pct(arm["wall_median_s"]),
                _usd(per_rep),
                _usd(arm["dollars_per_solved"]),
            ]
            lines.append("| " + " | ".join(cells) + " |")
    lines += ["", "| Case | Arm | Stop reasons (count) |", "| --- | --- | --- |"]
    for row in comparison["rows"]:
        for label, name in arms:
            counts = sorted(row[label]["stop_reasons"].items())
            reasons = ", ".join(f"{k} {v}" for k, v in counts) or "none"
            lines.append(f"| {row['case']} | {name} | {reasons} |")
    mh = comparison.get("mantel_haenszel")
    if mh and mh.get("odds_ratio") is not None:
        lines += [
            "",
            "Mantel-Haenszel odds ratio, nare over Claude Code: "
            f"{mh['odds_ratio']:.2f} [{mh['low']:.2f}, {mh['high']:.2f}], "
            f"p = {mh['p_value']:.3f}, {mh['strata']} strata.",
        ]
    ratio = comparison.get("nare_over_claude_tokens_geomean")
    if ratio:
        lines.append(
            "Tokens, nare over Claude Code (geometric mean of per-case "
            f"medians): {ratio[0]:.3f} [{ratio[1]:.3f}, {ratio[2]:.3f}]."
        )
    return "\n".join(lines) + "\n"


def _report(argv: Sequence[str]) -> int:
    p = argparse.ArgumentParser(prog="python -m benchmarks.runner.claude_arm report")
    p.add_argument(
        "--nare", nargs="+", required=True, help="nare results files (bin/bench run)"
    )
    p.add_argument(
        "--claude", nargs="+", required=True, help="Claude Code results.jsonl files"
    )
    p.add_argument(
        "--manifest",
        nargs="+",
        required=True,
        help="the Claude Code manifest.json files",
    )
    args = p.parse_args(list(argv))
    nare: list[RepRecord] = []
    for name in args.nare:
        nare += read_records(Path(name))
    claude: list[RepRecord] = []
    for name in args.claude:
        claude += read_records(Path(name))
    prices = {
        json.dumps(json.loads(Path(m).read_text())["price_per_token"], sort_keys=True)
        for m in args.manifest
    }
    if len(prices) != 1:
        print("the manifests name different prices; the arms are not priced alike")
        return 2
    price = ModelPrice(**json.loads(prices.pop()))
    print(render_markdown(compare_arms(nare, claude, price)))
    return 0


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "report":
        raise SystemExit(_report(sys.argv[2:]))
    raise SystemExit(main())
