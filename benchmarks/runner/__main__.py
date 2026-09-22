"""`bin/bench` -- a thin adapter over the runner modules.

Everything decidable is decided in the pure modules; this file parses
arguments, orders the work, and prints.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from benchmarks.runner.case import Case, CaseError, load_cases
from benchmarks.runner.config import Config, ConfigError, resolve_config
from benchmarks.runner.grade import (
    grade_bash_check,
    grade_result_check,
    parse_stdout,
    rep_outcome,
)
from benchmarks.runner.judge import JudgeError, judge_transport, score
from benchmarks.runner.report import (
    CaseDelta,
    CaseSummary,
    Comparison,
    RepRecord,
    baseline_path,
    compare,
    load_baseline,
    render,
    summarize,
)
from benchmarks.runner.sandbox import (
    SandboxError,
    build_image,
    git_checkpoint,
    git_diff,
    repo_root,
    run_agent,
    run_check,
    sandbox,
)
from nare.transport import Transport

CheckRunner = Callable[[Case, str, int], tuple[int, str]]
CONFIRM_REPS = 7
TOKEN_BAND = 0.10


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bench", description="Measure whether a change to nare helped."
    )
    sub = parser.add_subparsers(dest="command")

    for name, help_text in (
        ("run", "run a tier and compare it against the baseline"),
        ("compare", "compare the latest results against the baseline"),
        ("bless", "write the latest results to the baseline"),
        ("verify", "check that each case's checks fail on its pristine fixture"),
    ):
        child = sub.add_parser(name, help=help_text)
        child.add_argument(
            "--tier", choices=["smoke", "full"], default="smoke", help="which tier"
        )
        child.add_argument("--case", action="append", help="limit to this case id")
        if name == "verify":
            continue
        child.add_argument("--model", help="model under test (env: NARE_MODEL)")
        child.add_argument("--provider", help="provider (env: NARE_PROVIDER)")
        child.add_argument("--base-url", help="endpoint (env: NARE_BASE_URL)")
        child.add_argument("--judge-model", help="judge (env: NARE_BENCH_JUDGE_MODEL)")
        child.add_argument(
            "--strict-tokens",
            action="store_true",
            help="fail the run when tokens rise beyond the tolerance band",
        )
        child.add_argument(
            "--allow-model-change",
            action="store_true",
            help="compare against a baseline blessed on a different model",
        )
    return parser


def _select(root: Path, tier: str, only: Sequence[str] | None) -> list[Case]:
    cases = load_cases(root / "benchmarks" / "cases", tier=tier)
    if only:
        wanted = set(only)
        cases = [c for c in cases if c.id in wanted]
    return cases


def pristine_check(case: Case, cmd: str, timeout: int) -> tuple[int, str]:
    """Run one command against a fresh, untouched copy of a case's fixture.

    A sandbox per check rather than per case: a check that writes must not
    change what the next check sees, or verify stops measuring the pristine
    tree. The git checkpoint is there so checks see the same repository the
    agent would.
    """
    with sandbox(case) as (workdir, _):
        git_checkpoint(workdir)
        return run_check(workdir, cmd, timeout)


def verify(
    cases: Sequence[Case], *, run_check: CheckRunner = pristine_check
) -> tuple[str, int]:
    """Prove each case's checks measure something the agent has to do.

    Only `bash` checks are exercised: a `result` check reads nare's result
    line, and verify never runs the agent. A case whose checks are all
    `result` kind is exempt rather than failed.
    """
    lines: list[str] = []
    failed = False
    for case in cases:
        if not case.bash_checks:
            lines.append(f"{case.id:<24} exempt (no bash checks to verify)")
            continue
        outcomes = [
            run_check(case, check.cmd or "", 120)[0] for check in case.bash_checks
        ]
        if any(code != 0 for code in outcomes):
            lines.append(f"{case.id:<24} ok (fails on the pristine fixture)")
        else:
            failed = True
            lines.append(
                f"{case.id:<24} BROKEN: every bash check already passes before "
                "the agent runs, so this case measures nothing"
            )
    return "\n".join(lines), 1 if failed else 0


def _commit() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True
    )
    return proc.stdout.strip() or "unknown"


def line_text(stdout: str) -> str:
    """The final assistant text, which nare emits as the `output` event."""
    for raw in reversed(stdout.splitlines()):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("type") == "output":
            return str(payload.get("text", ""))
    return ""


def render_first_run(summaries: Sequence[CaseSummary]) -> str:
    return render(
        Comparison(
            deltas=tuple(
                CaseDelta(s.case, "new", s, None, None, ()) for s in summaries
            ),
            exit_code=0,
        )
    )


async def run_rep(
    case: Case, config: Config, transport: Transport, rep: int
) -> RepRecord:
    """One repetition, from a clean fixture to a graded record."""
    empty = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    with sandbox(case) as (workdir, artifacts):
        result = run_agent(case, workdir, artifacts, config)
        line = parse_stdout(result.stdout)
        if line is None:
            # No result line means the run never produced one: a crash, a
            # timeout, a Docker fault. That is an error, never a failure.
            return RepRecord(
                case=case.id,
                rep=rep,
                outcome="error",
                checks=(),
                judge_met=None,
                judge_why="timed out" if result.timed_out else "no result line",
                status=None,
                stop_reason=None,
                turns=None,
                usage=empty,
                duration_s=result.duration_s,
                model=config.model,
            )

        graded = [
            grade_result_check(check, line)
            for check in case.checks
            if check.kind == "result"
        ]
        for check in case.bash_checks:
            code, output = run_check(workdir, check.cmd or "", 120)
            graded.append(grade_bash_check(check, code, output))

        met: list[bool] | None = None
        why = ""
        if case.judge is not None:
            try:
                met, why = await score(
                    case,
                    git_diff(workdir),
                    line_text(result.stdout),
                    transport=transport,
                )
            except JudgeError as exc:
                why = f"judge failed: {exc}"

    return RepRecord(
        case=case.id,
        rep=rep,
        outcome=rep_outcome(graded, case.judge, met),
        checks=tuple(graded),
        judge_met=None if met is None else sum(met),
        judge_why=why,
        status=line.status,
        stop_reason=line.stop_reason,
        turns=line.turns,
        usage=line.usage,
        duration_s=result.duration_s,
        model=config.model,
    )


async def run_case(
    case: Case, config: Config, transport: Transport, reps: int
) -> list[RepRecord]:
    # Sequential on purpose: repetitions share one rate limit and one Docker
    # daemon, and a benchmark that saturates either measures the machine.
    return [await run_rep(case, config, transport, rep) for rep in range(reps)]


def write_results(root: Path, records: Sequence[RepRecord]) -> Path:
    directory = root / "benchmarks" / "results"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    path = directory / f"{stamp}.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for rec in records:
            payload = asdict(rec)
            payload["checks"] = [asdict(c) for c in rec.checks]
            handle.write(json.dumps(payload) + "\n")
    return path


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command is None:
        print(
            "bench needs a subcommand: run, compare, bless, or verify", file=sys.stderr
        )
        return 2

    root = repo_root()
    try:
        cases = _select(root, args.tier, args.case)
    except CaseError as exc:
        print(f"bench: {exc}", file=sys.stderr)
        return 2
    if not cases:
        print(f"bench: no cases in tier {args.tier}", file=sys.stderr)
        return 2

    if args.command == "verify":
        try:
            build_image(root)
        except SandboxError as exc:
            print(f"bench: {exc}", file=sys.stderr)
            return 2
        text, code = verify(cases)
        print(text)
        return code

    try:
        config = resolve_config(
            model=args.model,
            provider=args.provider,
            base_url=args.base_url,
            judge_model=args.judge_model,
            env=os.environ,
        )
    except ConfigError as exc:
        print(f"bench: {exc}", file=sys.stderr)
        return 2

    if args.command == "run":
        try:
            build_image(root)
        except SandboxError as exc:
            print(f"bench: {exc}", file=sys.stderr)
            return 2
        transport = judge_transport(config)
        records: list[RepRecord] = []
        for case in cases:
            records += asyncio.run(run_case(case, config, transport, case.reps))

        path = write_results(root, records)
        print(f"results: {path}")

        baseline_file = baseline_path(
            root / "benchmarks" / "baselines", config.model, args.tier
        )
        if not baseline_file.exists():
            print(render_first_run(summarize(records)))
            print(
                f"\nno baseline yet; bless one with: bin/bench bless --tier {args.tier}"
            )
            return 0

        baseline = load_baseline(baseline_file)
        if not args.allow_model_change and (
            baseline.meta.model != config.model
            or baseline.meta.judge_model != config.judge_model
        ):
            print(
                f"bench: baseline was blessed on model {baseline.meta.model!r} / "
                f"judge {baseline.meta.judge_model!r}; this run used "
                f"{config.model!r} / {config.judge_model!r}. Numbers are not "
                "comparable across models. Pass --allow-model-change to override.",
                file=sys.stderr,
            )
            return 2

        first = compare(
            summarize(records),
            baseline,
            token_band=TOKEN_BAND,
            strict_tokens=args.strict_tokens,
        )
        suspects = [d.case for d in first.deltas if d.verdict == "suspect"]
        if suspects:
            print(f"re-confirming {', '.join(suspects)} at {CONFIRM_REPS} reps...")
            for case in [c for c in cases if c.id in suspects]:
                extra = asyncio.run(run_case(case, config, transport, CONFIRM_REPS))
                records = [r for r in records if r.case != case.id] + extra
            write_results(root, records)

        final = compare(
            summarize(records),
            baseline,
            confirmed=True,
            token_band=TOKEN_BAND,
            strict_tokens=args.strict_tokens,
        )
        print(render(final))
        return final.exit_code

    print(f"bench: {args.command} is not wired yet", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
