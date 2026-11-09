"""`bin/bench` -- a thin adapter over the runner modules.

Everything decidable is decided in the pure modules; this file parses
arguments, orders the work, and prints.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from benchmarks.runner.ab import (
    CaseAB,
    dollars_per_rep_pairs,
    mantel_haenszel,
    paired_ratio,
    pass_rate_effect,
    token_pairs,
)
from benchmarks.runner.ab import render as render_ab
from benchmarks.runner.agreement import Header, Skip, render_summary
from benchmarks.runner.calibration import (
    LABELS_FILE,
    CalibrationError,
    calibrate,
    export_candidates,
    load_labels,
    problems,
    render_calibration,
)
from benchmarks.runner.calibration_import import ImportRefused, import_reviews
from benchmarks.runner.case import TIER_CHOICES, Case, CaseError, load_cases
from benchmarks.runner.compare import (
    Judges,
    RecordingTransport,
    run_comparison,
    select_work,
)
from benchmarks.runner.config import (
    KEY_VARS,
    MODEL_ALIASES,
    Config,
    ConfigError,
    baseline_config,
    require_judge,
    resolve_config,
    unset_aliases,
)
from benchmarks.runner.grade import (
    CheckResult,
    ContextUse,
    ResultLine,
    context_use,
    grade_bash_check,
    grade_result_check,
    infra_error,
    parse_stdout,
    rep_outcome,
)
from benchmarks.runner.jev import JevClient
from benchmarks.runner.judge import (
    JudgeAttempt,
    JudgeError,
    judge_transport,
    render_attempts,
    score,
)
from benchmarks.runner.models import (
    ListingError,
    baseline_tiers,
    list_models,
    render_models,
)
from benchmarks.runner.nare_args import (
    ArgsError,
    RunArgs,
    check_invocation,
    file_digest,
    nare_contract,
    stage_inputs,
    variant,
)
from benchmarks.runner.provenance import (
    case_hash,
    image_id,
    judge_cause,
    session_tool_calls,
)
from benchmarks.runner.reference import DockerRuntime, judge_from, verify_references
from benchmarks.runner.report import (
    Baseline,
    BaselineMeta,
    CaseDelta,
    CaseSummary,
    Comparison,
    RepRecord,
    baseline_path,
    changed_cases,
    compare,
    dumps_baseline,
    load_baseline,
    render,
    render_matrix,
    render_variant,
    slugify,
    summarize,
)
from benchmarks.runner.sandbox import (
    DID_NOT_RUN,
    IMAGE,
    RunArtifacts,
    SandboxError,
    build_image,
    git_checkpoint,
    git_diff,
    idle_artifacts,
    repo_root,
    run_agent,
    run_check,
    sandbox,
    write_closing,
)
from benchmarks.runner.toolhealth import (
    model_health,
    read_evidence,
    render_health,
    rep_health,
)
from benchmarks.runner.triage import triage_transport
from nare import __version__ as NARE_VERSION
from nare.contract import NEVER_STARTED
from nare.transport import Transport

CheckRunner = Callable[[Case, str, int], tuple[int, str]]
CONFIRM_REPS = 10
# 124 is the sandbox timeout; 125-127 are docker's own (daemon, permissions,
# command not found). A check that exits one of these never ran, so it proves
# nothing about the fixture and must not read as a failing check.


def split_run_args(argv: Sequence[str]) -> tuple[list[str], list[str] | None]:
    """Bench's own arguments, and what follows the first `--` for nare.

    Split before argparse, which would otherwise take nare's flags as bench's.
    The second part is None when there is no `--`, and empty after a bare one.
    """
    tokens = list(argv)
    if "--" not in tokens:
        return tokens, None
    at = tokens.index("--")
    return tokens[:at], tokens[at + 1 :]


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
        if name == "run":
            # split_run_args takes what follows -- before argparse sees it,
            # so argparse cannot list it on its own.
            child = sub.add_parser(
                name,
                help=help_text,
                usage="%(prog)s [options] [-- NARE_ARGS ...]",
                epilog="Everything after -- goes to nare run as the run "
                "arguments, for every selected case: for example "
                "-- --effort low. See 'Arguments to nare' in benchmarks/README.md.",
            )
        else:
            child = sub.add_parser(name, help=help_text)
        child.add_argument(
            "--tier",
            choices=TIER_CHOICES,
            # verify is free, so it checks every case unless asked otherwise:
            # no tier at all, extended included.
            default=None if name == "verify" else "smoke",
            help="which tier; full is smoke and full, extended runs only when "
            "named (verify without --tier checks every case)",
        )
        child.add_argument("--case", action="append", help="limit to this case id")
        if name == "verify":
            child.add_argument(
                "--judge",
                action="store_true",
                help="also judge each solution's claims, and each exploit a judge "
                "catches (one judge call per judged solution, and one per exploit "
                "the judge decides; needs a key)",
            )
            child.add_argument(
                "--provider", help="judge's provider (env: NARE_PROVIDER)"
            )
            child.add_argument("--base-url", help="endpoint (env: NARE_BASE_URL)")
            child.add_argument(
                "--judge-model", help="judge (env: NARE_BENCH_JUDGE_MODEL)"
            )
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
            help="compare against, or bless on top of, a baseline blessed on a "
            "different provider, model, judge or output cap",
        )
        child.add_argument(
            "--allow-arg-change",
            action="store_true",
            help="compare against, or bless on top of, a baseline blessed with "
            "different run arguments (what follows -- on run)",
        )
        child.add_argument(
            "--allow-case-change",
            action="store_true",
            help="compare against a baseline whose case (case.toml or its fixture) "
            "has changed since it was blessed",
        )
        if name == "run":
            child.add_argument(
                "--matrix",
                action="store_true",
                help="run every model with a baseline in this tier, each against "
                "its own",
            )
        if name == "compare":
            child.add_argument(
                "--matrix",
                action="store_true",
                help="print the last matrix run's case-by-model table",
            )
            child.add_argument(
                "--baselines",
                action="store_true",
                help="with --matrix: the table of the tier's baselines, not a run",
            )
    judges = sub.add_parser(
        "compare-judges",
        help="measure Jev and the llm judge against a reference model",
    )
    judges.add_argument("--results", help="results file (default: the newest)")
    judges.add_argument("--case", action="append", help="limit to this case id")
    judges.add_argument("--provider", help="provider (env: NARE_PROVIDER)")
    judges.add_argument("--base-url", help="the proxy (env: NARE_BASE_URL)")
    judges.add_argument("--judge-model", help="judge (env: NARE_BENCH_JUDGE_MODEL)")
    judges.add_argument("--jev-model", help="Jev (env: NARE_BENCH_JEV_MODEL)")
    judges.add_argument(
        "--reference-model", help="reference (env: NARE_BENCH_REFERENCE_MODEL)"
    )
    health = sub.add_parser(
        "tool-health",
        help="count tool-call health per model from a results file's kept evidence",
    )
    health.add_argument("--results", help="results file (default: the newest)")
    calibration = sub.add_parser(
        "calibrate",
        help="a human-labelled set that measures the llm judge's error rates",
    )
    calibration.add_argument(
        "action",
        choices=["export", "import", "check", "score"],
        help="export blind rows to label; import reviewed verdicts; check the "
        "labels file; score the judge",
    )
    calibration.add_argument(
        "file", nargs="?", help="import: the reviewed CSV or JSON file"
    )
    calibration.add_argument(
        "--export", help="import: the rows `calibrate export --out` wrote"
    )
    calibration.add_argument(
        "--reviewer-a",
        help="import: the name that is labeller a, as the page records it",
    )
    calibration.add_argument(
        "--reviewer-b",
        help="import: the name that is labeller b, as the page records it",
    )
    calibration.add_argument(
        "--panel-provenance",
        help="import: a JSON file of the model panel's passes (name, date, models "
        "and effort), needed when the file holds panel verdicts",
    )
    calibration.add_argument(
        "--adjudicate",
        action="store_true",
        help="import: accept the decisions in the CSV label column for disagreements",
    )
    calibration.add_argument("--results", help="results file (default: the newest)")
    calibration.add_argument("--labels", help=f"labels file (default: {LABELS_FILE})")
    calibration.add_argument("--out", help="export: where to write the rows")
    calibration.add_argument(
        "--sample", type=int, help="export: at most this many rows, chosen at random"
    )
    calibration.add_argument("--seed", type=int, default=0, help="export: sample seed")
    ab = sub.add_parser(
        "ab",
        help="paired comparison of two nare commits, pooled across cases",
        description="Run both commits on the same cases, reps interleaved, and "
        "pool the pass rates with a stratified test. See 'bin/bench ab' in "
        "benchmarks/README.md.",
    )
    ab.add_argument("--base", required=True, help="the base commit or ref")
    ab.add_argument("--head", required=True, help="the head commit or ref")
    ab.add_argument("--tier", choices=TIER_CHOICES, default="smoke")
    ab.add_argument(
        "--case",
        action="append",
        help="add this case; it is always compared, whatever its baseline says",
    )
    ab.add_argument(
        "--all-cases",
        action="store_true",
        help="every case in the tier, not only the informative ones",
    )
    ab.add_argument(
        "--reps", type=int, default=AB_REPS, help="repetitions per arm per case"
    )
    ab.add_argument("--model", help="the model (env: NARE_MODEL)")
    ab.add_argument("--provider", help="the agent's provider (env: NARE_PROVIDER)")
    ab.add_argument("--base-url", help="endpoint (env: NARE_BASE_URL)")
    ab.add_argument("--judge-model", help="judge (env: NARE_BENCH_JUDGE_MODEL)")
    models = sub.add_parser(
        "models", help="list what the endpoint serves, with window, price, baselines"
    )
    models.add_argument("--provider", help="whose key to send (env: NARE_PROVIDER)")
    models.add_argument("--base-url", help="the proxy (env: NARE_BASE_URL)")
    return parser


def _select(root: Path, tier: str | None, only: Sequence[str] | None) -> list[Case]:
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
    agent would, and the idle run's artifacts so a check that reads /out
    grades a run that did nothing instead of crashing on a missing file.
    """
    with sandbox(case) as (workdir, artifacts):
        git_checkpoint(workdir)
        idle_artifacts(case, workdir, artifacts)
        return run_check(workdir, cmd, timeout, artifacts=artifacts)


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
        stalled = [code for code in outcomes if code in DID_NOT_RUN]
        if stalled:
            failed = True
            lines.append(
                f"{case.id:<24} ERROR: a check exited {stalled[0]}, so it timed "
                "out or never ran; this case was not verified"
            )
        elif any(code != 0 for code in outcomes):
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


class GitError(Exception):
    """git could not say whether it tracks a file: not installed, or no
    checkout to ask. Neither answer may be assumed."""


def git_tracks(root: Path, path: str) -> bool:
    """Whether git tracks `path`, relative to the checkout at `root`.

    The pathspec is literal, so a name with a `*` in it matches only itself.
    """
    try:
        proc = subprocess.run(
            ["git", "--literal-pathspecs", "ls-files", "--error-unmatch", "--", path],
            cwd=root,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise GitError("git is not on PATH") from exc
    except OSError as exc:
        raise GitError(
            f"git could not run: {exc.strerror or type(exc).__name__}"
        ) from exc
    # 1 is a path git does not track; anything else is git failing to answer.
    if proc.returncode in (0, 1):
        return proc.returncode == 0
    raise GitError(proc.stderr.strip() or f"git exited {proc.returncode}")


def unblessable(root: Path, files: Mapping[str, str]) -> list[str]:
    """Why each run file would make a baseline other checkouts cannot run.

    `run --matrix` reads every file a baseline names again, on whatever
    checkout runs it, and exits 2 for that model when one is missing or has
    another digest, which stops the whole matrix. So a file must be inside
    the checkout, still hold the digest the run measured, and be tracked by
    git. Empty when every file passes, and git is asked only about a file
    that got that far.
    """
    problems: list[str] = []
    for path, digest in sorted(files.items()):
        if Path(path).is_absolute():
            problems.append(
                f"the run arguments name {path}, which is outside the checkout; "
                "a baseline must name files anyone with the checkout can read, "
                "so move it into the checkout and run again"
            )
            continue
        try:
            now = file_digest((root / path).read_bytes())
        except OSError:
            problems.append(
                f"{path}, which the run measured, cannot be read; restore it, "
                "or run again"
            )
            continue
        if now != digest:
            problems.append(
                f"{path} changed since the run: the run measured {digest[:19]}, "
                f"and it is now {now[:19]}; restore the file the run measured, "
                "or run again"
            )
            continue
        try:
            known = git_tracks(root, path)
        except GitError as exc:
            problems.append(
                f"cannot ask git whether it tracks {path} ({exc}); a baseline "
                "must name files every checkout has, so bless from a git "
                "checkout, with git on PATH"
            )
            continue
        if not known:
            problems.append(
                f"git does not track {path}, so no other checkout has it; "
                "commit it first"
            )
    return problems


def line_text(stdout: str, questions: Sequence[str] = ()) -> str:
    """What the judge reads as the agent's closing message.

    The `output` event's text, which nare emits only on `done`, then any
    questions a blocked run asked: without them no claim can be about them.
    """
    text = ""
    for raw in reversed(stdout.splitlines()):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("type") == "output":
            text = str(payload.get("text", ""))
            break
    if questions:
        asked = "\n".join(f"- {q}" for q in questions)
        text = f"{text}\n\nThe agent stopped to ask:\n{asked}".lstrip()
    return text


def render_first_run(summaries: Sequence[CaseSummary]) -> str:
    return render(
        Comparison(
            deltas=tuple(
                CaseDelta(s.case, "new", s, None, None, ()) for s in summaries
            ),
            exit_code=0,
        )
    )


def new_stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")


class NeverStarted(Exception):
    """nare refused its invocation, so no rep of this run can start.

    Exit 2 with no result line and no timeout is nare refusing to start: its
    argument parser, its validation of the run arguments, or the construction
    of its transport (a key it cannot use, say), whether a run argument, a
    case argument or the environment is to blame. Every later rep would be
    refused the same way, so the run stops instead of recording an error per
    rep. `log` is the file that keeps the reason, when one was written.
    `kept` gains each file of graded records the stopped pass left on its way
    out, which the stop then names.
    """

    def __init__(
        self, case: str, rep: int, stderr: str, log: Path | None = None
    ) -> None:
        self.case = case
        self.rep = rep
        self.stderr = stderr
        self.log = log
        self.kept: list[Path] = []
        message = (
            f"{case} rep {rep}: nare refused its invocation and never started "
            f"(exit {NEVER_STARTED}), so the run stopped here. Its stderr:\n"
            f"{stderr.rstrip()}"
        )
        if log is not None:
            message += f"\nThe reason is kept in {log}"
        super().__init__(message)


def _listed(tokens: Sequence[str]) -> str:
    return shlex.join(tokens) or "(none)"


def _section(title: str, body: str) -> str:
    """A titled block of verbatim text, ended by exactly one newline."""
    return f"{title}\n{body}" + ("" if body.endswith("\n") else "\n")


def stopped_log(
    case: Case, rep: int, config: Config, result: RunArtifacts, kept_ref: str
) -> str:
    """The text of stopped.txt: why a run stopped at a rep whose nare never started.

    Nothing is redacted or shortened. The arguments carry no credential (the
    key reaches nare in the environment), and a shortened stderr can lose the
    one line that says why. The run args are the paths nare was given, under
    /in, so a run with files also names the host file each came from.
    """
    lines = [
        "nare never started, so the run stopped.",
        f"case: {case.id}",
        f"rep: {rep}",
        f"model: {config.model} ({config.provider})",
        f"exit: {result.exit_code} (never_started), no result line, not timed out",
        f"run args: {_listed(config.run_args.passed)}",
        f"case args: {_listed(case.args)}",
    ]
    if config.run_args.files:
        files = "; ".join(
            f"{path} {digest}" for path, digest in config.run_args.files.items()
        )
        lines.append(f"run files: {files}")
    lines.append(f"evidence: {kept_ref}")
    return (
        "\n".join(lines)
        + "\n\n"
        + _section("nare's stderr:", result.stderr)
        + "\n"
        + _section("nare's stdout:", result.stdout or "(empty)")
    )


def keep_evidence(dest: Path, artifacts: Path, result: RunArtifacts) -> None:
    """Copy what a rep produced out of its sandbox before the sandbox goes.

    Cleared first: a confirmation re-run reuses the same case-rep names, and a
    judge.txt left from the first pass would describe a different run. The
    session, and what the container held after nare, can be missing when the
    container died first.
    """
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    for name in ("session.json", "after.json"):
        if (artifacts / name).is_file():
            shutil.copyfile(artifacts / name, dest / name)
    (dest / "stdout.jsonl").write_text(result.stdout, encoding="utf-8")
    (dest / "stderr.txt").write_text(result.stderr, encoding="utf-8")


def _errored_rep(
    case: Case,
    rep: int,
    config: Config,
    result: RunArtifacts,
    line: ResultLine | None,
    use: ContextUse,
    kept_ref: str,
    why: str,
    health: dict[str, int] | None,
    cause: str,
    provenance: Mapping[str, Any],
    tool_calls: dict[str, int] | None,
) -> RepRecord:
    """A repetition that could not be graded. It is counted in neither rate."""
    empty = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    return RepRecord(
        case=case.id,
        rep=rep,
        outcome="error",
        checks=(),
        judge_met=None,
        judge_why=why,
        status=None if line is None else line.status,
        stop_reason=None,
        turns=None if line is None else line.turns,
        usage=empty if line is None else line.usage,
        duration_s=result.duration_s,
        model=config.model,
        artifacts=kept_ref,
        max_tokens=config.max_tokens,
        provider=config.provider,
        judge_model=config.judge_model or "",
        case_args=list(case.args),
        nare_args=list(config.run_args.recorded),
        nare_files=dict(config.run_args.files),
        context_window=use.window,
        compactions=use.compactions,
        tool_health=health,
        cost=None if line is None else line.cost,
        cost_estimated=False if line is None else line.cost_estimated,
        tool_calls=tool_calls,
        error_cause=cause,
        **provenance,
    )


async def run_rep(
    case: Case, config: Config, transport: Transport, rep: int, evidence: Path
) -> RepRecord:
    """One repetition, from a clean fixture to a graded record.

    `evidence` is this run's directory under benchmarks/results/; the rep's
    session, output and judge replies are kept in a subdirectory of it.
    """
    kept = evidence / f"{case.id}-{rep}"
    kept_ref = kept.relative_to(evidence.parent).as_posix()
    # What produced this line, so a later comparison can tell whether the
    # numbers are the same measurement. Taken before the rep runs.
    provenance: dict[str, Any] = {
        "nare_version": NARE_VERSION,
        "nare_commit": config.nare_commit or _commit(),
        "case_hash": case_hash(case),
        "image_id": image_id(config.image or IMAGE),
        "started": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    with sandbox(case) as (workdir, artifacts):
        result = run_agent(case, workdir, artifacts, config)
        keep_evidence(kept, artifacts, result)
        # Read from the kept copies, so every rep is counted, stopped or not.
        health = rep_health(kept)
        tool_calls = session_tool_calls(kept)
        line = parse_stdout(result.stdout)
        if line is None and not result.timed_out and result.exit_code == NEVER_STARTED:
            # nare refused to start (its parser, its argument checks or its
            # transport): every rep would be.
            # The log is written first, and is the record of why.
            log = evidence / "stopped.txt"
            log.write_text(
                stopped_log(case, rep, config, result, kept_ref), encoding="utf-8"
            )
            raise NeverStarted(case.id, rep, result.stderr, log)
        use = context_use(result.stdout)
        if line is None or infra_error(line):
            # No result line means the run never produced one: a crash, a
            # timeout, a Docker fault. A transport failure nare caught is the
            # same thing with a result line. Either is an error, never a failure.
            if line is None:
                why = "timed out" if result.timed_out else "no result line"
                cause = "timeout" if result.timed_out else "no_result_line"
            else:
                why = line.error or "run errored"
                cause = "agent_http_error"
            return _errored_rep(
                case,
                rep,
                config,
                result,
                line,
                use,
                kept_ref,
                why,
                health,
                cause,
                provenance,
                tool_calls,
            )

        # Before the bash checks, so nothing a check writes reaches the judge.
        diff = git_diff(workdir)
        closing = line_text(result.stdout, line.questions)
        # Kept for every rep, judged or not, so any judge can be re-run against
        # exactly what this one read. compare-judges reads them back.
        (kept / "diff.patch").write_text(diff, encoding="utf-8")
        (kept / "closing.txt").write_text(closing, encoding="utf-8")
        # Under the name the checks read, at /out/closing.txt. Written by the
        # container after the agent exits, so nothing the agent wrote there is seen.
        try:
            write_closing(workdir, artifacts, closing)
        except SandboxError as exc:
            # The agent has finished and its work is in the tree. A rep that
            # cannot be graded is an error, as a dead container is, so the
            # run's other repetitions are kept.
            return _errored_rep(
                case,
                rep,
                config,
                result,
                line,
                use,
                kept_ref,
                str(exc),
                health,
                "sandbox_error",
                provenance,
                tool_calls,
            )

        graded = [
            grade_result_check(check, line)
            for check in case.checks
            if check.kind == "result"
        ]
        for check in case.bash_checks:
            code, output = run_check(workdir, check.cmd or "", 120, artifacts=artifacts)
            graded.append(grade_bash_check(check, code, output))

        met: list[bool | None] | None = None
        reasons: list[str] = []
        why = ""
        judge_cause_text: str | None = None
        if case.judge is not None:
            attempts: tuple[JudgeAttempt, ...]
            try:
                met, reasons, attempts = await score(
                    case, diff, closing, transport=transport
                )
                why = " ".join(
                    f"{n}. {reason}" for n, reason in enumerate(reasons, start=1)
                )
            except JudgeError as exc:
                why = f"judge failed: {exc}"
                judge_cause_text = judge_cause(exc)
                attempts = exc.attempts
            (kept / "judge.txt").write_text(render_attempts(attempts), encoding="utf-8")

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
        duration_s=result.duration_s,
        model=config.model,
        artifacts=kept_ref,
        max_tokens=config.max_tokens,
        provider=config.provider,
        judge_model=config.judge_model or "",
        case_args=list(case.args),
        nare_args=list(config.run_args.recorded),
        nare_files=dict(config.run_args.files),
        context_window=use.window,
        compactions=use.compactions,
        judge_failed=gating and outcome == "error",
        judge_claims=met,
        judge_reasons=reasons,
        judge_max_tokens=config.judge_max_tokens if case.judge is not None else None,
        diff_sha256=hashlib.sha256(diff.encode("utf-8")).hexdigest(),
        tool_health=health,
        cost=line.cost,
        cost_estimated=line.cost_estimated,
        tool_calls=tool_calls,
        error_cause=judge_cause_text,
        **provenance,
    )


async def run_case(
    case: Case, config: Config, transport: Transport, reps: int, evidence: Path
) -> list[RepRecord]:
    # Sequential on purpose: repetitions share one rate limit and one Docker
    # daemon, and a benchmark that saturates either measures the machine.
    return [
        await run_rep(case, config, transport, rep, evidence) for rep in range(reps)
    ]


def _write_records(path: Path, records: Sequence[RepRecord]) -> None:
    """One JSON line per rep: a results file, and what a stop keeps."""
    with path.open("w", encoding="utf-8") as handle:
        for rec in records:
            payload = asdict(rec)
            payload["checks"] = [asdict(c) for c in rec.checks]
            handle.write(json.dumps(payload) + "\n")


def write_results(
    root: Path, records: Sequence[RepRecord], stamp: str | None = None
) -> Path:
    directory = root / "benchmarks" / "results"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stamp or new_stamp()}.jsonl"
    _write_records(path, records)
    return path


# In a stopped run's evidence directory. The first is what the pass that stopped
# had finished, or a whole results file `set_aside_partial_results` moved;
# never both, since a pass writes its results file only when it finishes. The
# second is what a confirmation re-run had finished, beside its first pass.
PARTIAL = "results.partial.jsonl"
CONFIRM_PARTIAL = "results.confirm.partial.jsonl"


def keep_partial(exc: NeverStarted, path: Path, records: Sequence[RepRecord]) -> None:
    """On a stop, write the graded records of each case the pass finished.

    Judge verdicts are paid for, and the developer's rule is that a stopped run
    keeps every log. Written in the evidence directory, never as a results
    file, which `bless` would read as the newest run. Nothing when no case
    finished.
    """
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_records(path, records)
    exc.kept.append(path)


def set_aside_partial_results(root: Path, stamp: str) -> None:
    """Move every results file this run wrote into its evidence directory.

    A stopped run leaves no results file as the newest run, which `bless`
    would read as if it were whole: a matrix that stopped on its second model,
    or a first pass whose confirmation never ran. The file is moved, never
    deleted, to `<evidence>/results.partial.jsonl`, and each move is printed.
    """
    directory = root / "benchmarks" / "results"
    for path in sorted(
        (*directory.glob(f"{stamp}.jsonl"), *directory.glob(f"{stamp}.*.jsonl"))
    ):
        evidence = path.with_suffix("")
        evidence.mkdir(parents=True, exist_ok=True)
        target = evidence / PARTIAL
        path.replace(target)
        print(
            f"bench: the partial results of the stopped run are in {target}",
            file=sys.stderr,
        )


def stop_run(exc: NeverStarted, root: Path, stamp: str) -> int:
    """`run` stopped because nare never started: say why, and exit 2."""
    print(f"bench: {exc}", file=sys.stderr)
    for path in exc.kept:
        print(
            f"bench: the records of each case the stopped pass finished are in {path}",
            file=sys.stderr,
        )
    set_aside_partial_results(root, stamp)
    return 2


def latest_results(root: Path) -> Path | None:
    directory = root / "benchmarks" / "results"
    if not directory.is_dir():
        return None
    files = sorted(directory.glob("*.jsonl"))
    return files[-1] if files else None


def stamp_of(path: Path) -> str:
    """The run a results file belongs to.

    A run writes `<stamp>.jsonl`. A matrix run writes one file per model under
    one stamp, `<stamp>.<model slug>.jsonl`, so the matrix stays one run.
    """
    return path.name.split(".", 1)[0]


def is_matrix_file(path: Path) -> bool:
    return path.name.count(".") == 2


def latest_run(root: Path, *, matrix: bool = False) -> list[Path]:
    """Every results file of the newest run, or with `matrix`, of the newest
    matrix run."""
    directory = root / "benchmarks" / "results"
    if not directory.is_dir():
        return []
    files = sorted(directory.glob("*.jsonl"))
    if matrix:
        files = [p for p in files if is_matrix_file(p)]
    if not files:
        return []
    newest = stamp_of(files[-1])
    return [p for p in files if stamp_of(p) == newest]


def results_for(files: Sequence[Path], model: str) -> Path | None:
    """One model's results in a run: its own file from a matrix run, or the
    run's single file, which the caller still checks was this model's."""
    for path in files:
        if is_matrix_file(path) and path.name.split(".")[1] == slugify(model):
            return path
    single = [p for p in files if not is_matrix_file(p)]
    return single[0] if single else None


def read_results(path: Path) -> list[RepRecord]:
    records: list[RepRecord] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        payload = json.loads(raw)
        payload["checks"] = tuple(
            CheckResult(**check) for check in payload.get("checks", [])
        )
        records.append(RepRecord(**payload))
    return records


def _bench(where: str | None) -> str:
    """The start of a refusal: `bench: `, and what it is about when given."""
    return f"bench: {where}: " if where else "bench: "


def model_changed(
    baseline: Baseline, config: Config, allow: bool, *, where: str | None = None
) -> bool:
    """Numbers are comparable within a model and nowhere else.

    Pinning the judge model too is the non-obvious half: if it changes under
    the suite, every score shifts at once and reads as a harness regression.
    The agent's output cap is pinned for the same reason: a reasoning model
    cut off at nare's default fails reps it passes with room to think. So is
    the provider: the same model on the other route can lose a reasoning
    model's thinking on the way. An empty `config.provider` is results that
    never recorded theirs, which is unknown, not a mismatch. `where` names
    the baseline in the message when there is more than one.
    """
    if allow:
        return False
    if (
        baseline.meta.model == config.model
        and (not config.provider or baseline.meta.provider == config.provider)
        and baseline.meta.judge_model == config.judge_model
        and baseline.meta.max_tokens == config.max_tokens
    ):
        return False

    def cap(value: int | None) -> str:
        return "nare's default" if value is None else str(value)

    provider = repr(config.provider) if config.provider else "an unrecorded provider"
    print(
        f"{_bench(where)}baseline was blessed on provider "
        f"{baseline.meta.provider!r} / "
        f"model {baseline.meta.model!r} / judge {baseline.meta.judge_model!r} / "
        f"output cap {cap(baseline.meta.max_tokens)}; this run used {provider} / "
        f"{config.model!r} / {config.judge_model!r} / {cap(config.max_tokens)}. "
        "Numbers are not comparable across providers, models or caps. Pass "
        "--allow-model-change to override.",
        file=sys.stderr,
    )
    return True


def args_changed(
    baseline: Baseline,
    config: Config,
    allow: bool,
    *,
    where: str | None = None,
    match: Literal["run", "matrix"] | None = "run",
) -> bool:
    """Numbers are comparable within one variant of the run arguments.

    Compared by the key, not the text: `--effort=low` is `--effort low`, and a
    prompt file is its digest, so a moved prompt is the same variant and an
    edited one is not. Only `--allow-arg-change` overrides this, and it
    overrides nothing else. With `match`, the refusal ends with how to match
    the baseline: for a single run, the arguments to pass, on a line of their
    own so they copy cleanly (a baseline's file paths are relative to the
    checkout's root); for the matrix, which takes no `--`, to run it again.
    """
    if allow:
        return False
    meta = baseline.meta
    blessed = variant(meta.nare_args, meta.nare_files)
    if blessed == variant(config.run_args.recorded, config.run_args.files):
        return False
    print(
        f"{_bench(where)}baseline was blessed with run arguments "
        f"{render_variant(meta.nare_args, meta.nare_files)}; "
        "this run used "
        f"{render_variant(config.run_args.recorded, config.run_args.files)}. "
        "Numbers are not comparable across run arguments. Pass "
        "--allow-arg-change to override.",
        file=sys.stderr,
    )
    if match is None:
        return True
    if match == "matrix":
        hint = "run the matrix again, which runs each baseline's own arguments"
    elif not meta.nare_args:
        hint = "drop the -- and the arguments after it"
    else:
        root = " from the checkout's root" if meta.nare_files else ""
        hint = f"run{root} with: -- {shlex.join(meta.nare_args)}"
    print(f"{_bench(where)}to match the baseline, {hint}", file=sys.stderr)
    return True


def case_changed(
    baseline: Baseline,
    hashes: Mapping[str, str],
    args: argparse.Namespace,
    *,
    where: str | None = None,
) -> bool:
    """Whether a case changed since the baseline measured it, refused unless
    `--allow-case-change`. A changed task's old numbers are not its new ones."""
    changed = changed_cases(hashes, baseline)
    if not changed or getattr(args, "allow_case_change", False):
        return False
    named = f" ({where})" if where else ""
    print(
        f"bench: {', '.join(changed)} changed since the baseline{named} measured "
        "it, so its numbers are not comparable; re-bless the baseline, or pass "
        "--allow-case-change",
        file=sys.stderr,
    )
    return True


def incomparable(
    baseline: Baseline,
    config: Config,
    args: argparse.Namespace,
    *,
    where: str | None = None,
    hashes: Mapping[str, str] | None = None,
) -> bool:
    """Whether a model change or a variant change refuses the comparison.

    Both are checked and each prints its refusal, so an operator who needs
    both overrides learns so at once rather than one at a time. `where`, the
    baseline's file name, is given for the matrix, where there are several.
    """
    model = model_changed(baseline, config, args.allow_model_change, where=where)
    arguments = args_changed(
        baseline,
        config,
        args.allow_arg_change,
        where=where,
        match="run" if where is None else "matrix",
    )
    case = case_changed(baseline, hashes or {}, args, where=where)
    return model or arguments or case


def variant_note(baseline: Baseline, config: Config) -> str | None:
    """Both sides' run arguments, printed above a table that compares across
    variants, so a pasted table says so. None when they are one variant."""
    blessed = (baseline.meta.nare_args, baseline.meta.nare_files)
    ran = (config.run_args.recorded, config.run_args.files)
    if variant(*blessed) == variant(*ran):
        return None
    return (
        f"baseline run arguments: {render_variant(*blessed)}\n"
        f"results run arguments:  {render_variant(*ran)}"
    )


def merge_baseline(
    kept: dict[str, CaseSummary],
    fresh: Sequence[CaseSummary],
    tier_ids: set[str],
) -> list[CaseSummary]:
    """What a bless writes: the fresh results on top of the existing baseline.

    Merged, not replaced, so blessing after `run --case X` updates X and keeps
    the rest. An inconclusive fresh result never displaces a good number, and a
    case no longer in the tier drops out.
    """
    merged = {name: s for name, s in kept.items() if name in tier_ids}
    merged |= {s.case: s for s in fresh if s.pass_rate is not None}
    return [merged[name] for name in sorted(merged)]


def nothing_to_compare(
    results_file: Path,
    records: Sequence[RepRecord],
    skips: Sequence[Skip],
    only: Sequence[str] | None,
) -> str:
    """Why select_work found no work: the cause the user can act on."""
    name = results_file.name
    if only and not any(r.case in only for r in records):
        return f"bench: no repetition in {name} is of {', '.join(only)}"
    if skips:
        counts = Counter(s.reason for s in skips)
        listed = ", ".join(f"{reason} {n}" for reason, n in sorted(counts.items()))
        message = f"bench: nothing in {name} could be compared ({listed})"
        if "no evidence files" in counts:
            message += "; runs keep diff.patch and closing.txt from this version on"
        return message
    return (
        f"bench: {name} has no judged repetition and no failure, "
        "so there is nothing to compare"
    )


def tool_health_command(args: argparse.Namespace, root: Path) -> int:
    """Tool-call health from the kept evidence of an existing results file. Reads
    files only: no model is called, and no run is started."""
    results_file = Path(args.results) if args.results else latest_results(root)
    if results_file is None or not results_file.is_file():
        print("bench: no results to read; run `bin/bench run` first", file=sys.stderr)
        return 2
    records = read_evidence(root / "benchmarks" / "results", read_results(results_file))
    print(render_health(model_health(records)))
    return 0


def calibration_text(root: Path, records: Sequence[RepRecord]) -> str:
    """The judge's error rates against the labelled set, for this run's reps."""
    source = root / LABELS_FILE
    try:
        labels = load_labels(source)
    except CalibrationError as exc:
        return f"calibration: {source} is not readable: {exc}"
    return render_calibration(calibrate(labels, records), LABELS_FILE)


def import_command(
    args: argparse.Namespace, cases: Mapping[str, Case], labels_path: Path
) -> int:
    """`calibrate import FILE`: load reviewed verdicts into the labels file. A
    file is refused as a whole, with every row that is wrong, and writes nothing."""
    missing = [
        flag
        for flag, value in (("FILE", args.file), ("--export", args.export))
        if not value
    ]
    if not (args.reviewer_a or args.reviewer_b or args.panel_provenance):
        missing.append("--reviewer-a, --reviewer-b or --panel-provenance")
    if missing:
        print(
            f"bench: import needs {'; '.join(missing)}: the reviewed file, the export "
            "it was reviewed against, and the reviewers' names or the panel's "
            "provenance",
            file=sys.stderr,
        )
        return 2
    reviewers = {
        name: value
        for name, value in (("a", args.reviewer_a), ("b", args.reviewer_b))
        if value
    }
    try:
        result = import_reviews(
            source=Path(args.file),
            export_path=Path(args.export),
            labels_path=labels_path,
            reviewers=reviewers,
            adjudicate=args.adjudicate,
            cases=cases,
            panel_provenance=(
                Path(args.panel_provenance) if args.panel_provenance else None
            ),
        )
    except ImportRefused as exc:
        print(f"bench: {args.file} refused, nothing was written:", file=sys.stderr)
        for text in exc.found:
            print(f"bench:   {text}", file=sys.stderr)
        return 2
    except CalibrationError as exc:
        print(f"bench: {exc}", file=sys.stderr)
        return 2
    counts = ", ".join(f"{n} {status}" for status, n in sorted(result.statuses.items()))
    panel = f", {result.panel} panel verdict(s)" if result.panel else ""
    print(
        f"calibration: imported {result.verdicts} verdict(s){panel} and "
        f"{result.decisions} decision(s) from {args.file}; {result.skipped} row(s) "
        "not reviewed yet, skipped"
    )
    labelled = (
        f"; {result.panel_labelled} panel-labelled" if result.panel_labelled else ""
    )
    print(
        f"calibration: {labels_path}: {counts or 'no rows'}{labelled}"
        + ("" if result.changed else " (unchanged)")
    )
    return 0


def calibrate_command(args: argparse.Namespace, root: Path) -> int:
    """`calibrate export|check|score`. Reads the labelled set and results, and
    never calls a model: labelling is a person's job, and so is the scoring's
    input."""
    labels_path = Path(args.labels) if args.labels else root / LABELS_FILE
    try:
        cases = {c.id: c for c in load_cases(root / "benchmarks" / "cases")}
    except CaseError as exc:
        print(f"bench: {exc}", file=sys.stderr)
        return 2
    if args.action == "check":
        try:
            labels = load_labels(labels_path)
        except CalibrationError as exc:
            print(f"bench: {labels_path}: {exc}", file=sys.stderr)
            return 2
        found = problems(labels, cases)
        for text in found:
            print(f"bench: {labels_path}: {text}", file=sys.stderr)
        if found:
            return 1
        print(f"calibration: {len(labels)} labelled claim instance(s) check out")
        held = Counter(lab.status for lab in labels if lab.label is None)
        human = "a human label (out of the human rates)"
        if held:
            print(
                "calibration: kept without "
                + (
                    human
                    if any(lab.panel for lab in labels)
                    else "a label (out of the rates)"
                )
                + ": "
                + ", ".join(f"{n} {status}" for status, n in sorted(held.items()))
            )
        panelled = Counter(
            {True: "true", False: "false", None: "unknown"}[lab.panel.label]
            for lab in labels
            if lab.panel is not None
        )
        if panelled:
            print(
                "calibration: panel labels (a model panel, not human): "
                + ", ".join(f"{n} {value}" for value, n in sorted(panelled.items()))
            )
        return 0
    if args.action == "import":
        return import_command(args, cases, labels_path)
    results_file = Path(args.results) if args.results else latest_results(root)
    if results_file is None or not results_file.is_file():
        print("bench: no results to read; run `bin/bench run` first", file=sys.stderr)
        return 2
    records = read_results(results_file)
    if args.action == "export":
        if not args.out:
            print(
                "bench: export needs --out, where the rows are written", file=sys.stderr
            )
            return 2
        rows = export_candidates(records, cases, sample=args.sample, seed=args.seed)
        Path(args.out).write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )
        print(f"calibration: {len(rows)} claim instance(s) to label -> {args.out}")
        return 0
    try:
        labels = load_labels(labels_path)
    except CalibrationError as exc:
        print(f"bench: {labels_path}: {exc}", file=sys.stderr)
        return 2
    print(render_calibration(calibrate(labels, records), labels_path))
    return 0


def compare_judges(args: argparse.Namespace, root: Path) -> int:
    """Run Jev, the llm judge and the reference over one run's kept evidence.

    Every configuration error exits 2 before any model is called. Nothing to
    compare exits 1. A judge failing on some repetitions still exits 0: those
    failures are in the report.
    """
    env = os.environ
    base_url = args.base_url or env.get("NARE_BASE_URL")
    if not base_url:
        print(
            "bench: compare-judges reaches Jev only through the proxy; "
            "pass --base-url or set NARE_BASE_URL",
            file=sys.stderr,
        )
        return 2
    reference = args.reference_model or env.get("NARE_BENCH_REFERENCE_MODEL")
    if not reference:
        print(
            "bench: no reference model: pass --reference-model or set "
            "NARE_BENCH_REFERENCE_MODEL (its alias depends on the proxy)",
            file=sys.stderr,
        )
        return 2
    jev_model = args.jev_model or env.get("NARE_BENCH_JEV_MODEL") or "jev-latest"

    results_file = Path(args.results) if args.results else latest_results(root)
    if not args.results and results_file is not None and is_matrix_file(results_file):
        print(
            f"bench: the newest run, {stamp_of(results_file)}, is a matrix run "
            "with one results file per model; pass one with --results",
            file=sys.stderr,
        )
        return 2
    records = (
        read_results(results_file)
        if results_file is not None and results_file.is_file()
        else []
    )
    if results_file is None or not records:
        print("bench: no results to compare", file=sys.stderr)
        return 1
    models = sorted({r.model for r in records})
    if len(models) > 1:
        print(
            f"bench: {results_file.name} names {len(models)} models "
            f"({', '.join(models)}); compare-judges reads one run's model",
            file=sys.stderr,
        )
        return 2
    # The judge the run used is the default, not the model under test.
    recorded_judge = next((r.judge_model for r in records if r.judge_model), None)
    try:
        config = resolve_config(
            # The model under test comes from the records, so NARE_MODEL is
            # not needed.
            model=models[0],
            provider=args.provider,
            base_url=base_url,
            judge_model=args.judge_model
            or env.get("NARE_BENCH_JUDGE_MODEL")
            or recorded_judge,
            env=env,
        )
        cases = {c.id: c for c in load_cases(root / "benchmarks" / "cases")}
    except (ConfigError, CaseError) as exc:
        print(f"bench: {exc}", file=sys.stderr)
        return 2
    if config.judge_model is None:
        print(
            "bench: no judge model: pass --judge-model or set NARE_BENCH_JUDGE_MODEL",
            file=sys.stderr,
        )
        return 2
    assert config.api_key is not None, "resolve_config demands a key by default"

    work, skips = select_work(records, cases, results_file.parent, args.case)
    if not work:
        print(
            nothing_to_compare(results_file, records, skips, args.case), file=sys.stderr
        )
        return 1

    judges = Judges(
        jev=JevClient(base_url=base_url, api_key=config.api_key, model=jev_model),
        llm=RecordingTransport(judge_transport(config)),
        llm_model=config.judge_model,
        reference=RecordingTransport(
            judge_transport(replace(config, judge_model=reference))
        ),
        reference_triage=RecordingTransport(triage_transport(config, reference)),
        reference_model=reference,
    )
    # Beside the compared run's evidence, under the comparison's own stamp,
    # so a second comparison never overwrites a first.
    evidence = results_file.parent / results_file.stem
    evidence.mkdir(parents=True, exist_ok=True)
    cstamp = new_stamp()
    rows = asyncio.run(
        run_comparison(work, judges, evidence / f"compare-{cstamp}.jsonl")
    )
    summary = render_summary(
        Header(results_file.stem, config.model, config.judge_model, reference),
        rows,
        skips,
        cases,
        records,
    )
    (evidence / f"compare-{cstamp}.txt").write_text(summary + "\n", encoding="utf-8")
    print(summary)
    return 0


def as_recorded(
    config: Config,
    records: Sequence[RepRecord],
    *,
    provider: str | None = None,
    judge_model: str | None = None,
) -> Config:
    """The cap, provider, judge and run arguments the results were run with,
    not the ones in this shell: compare and bless never run the agent.

    `provider` and `judge_model` are the flags. One that contradicts what the
    results recorded raises ConfigError naming both values, since bless would
    write it over what was measured. Results written before the provider and
    judge were recorded keep the flags' values, or the shell's.
    """
    ran_provider = next((r.provider for r in records if r.provider), "")
    ran_judge = next((r.judge_model for r in records if r.judge_model), "")
    for name, flag, given, ran in (
        ("provider", "--provider", provider, ran_provider),
        ("judge", "--judge-model", judge_model, ran_judge),
    ):
        if given and ran and given != ran:
            raise ConfigError(
                f"the results were run with {name} {ran!r}, and {flag} says "
                f"{given!r}; drop {flag}, or pass the one they were run with"
            )
    recorded = replace(
        config,
        max_tokens=next((r.max_tokens for r in records), None),
        provider=ran_provider or config.provider,
        judge_model=ran_judge or config.judge_model,
    )
    if records:
        # One run's lines share its arguments. A line from before they were
        # recorded reads as none, the default variant.
        recorded = replace(
            recorded,
            run_args=RunArgs(
                recorded=tuple(records[0].nare_args),
                files=dict(records[0].nare_files),
            ),
        )
    return recorded


AB_REPS = 10
AB_TAG = {"base": "nare-bench:ab-base", "head": "nare-bench:ab-head"}
# Cases the baseline blessed between these rates: the ones a harness change can
# move. Guards at the top and floor cases at the bottom are left out by default.
AB_LOW, AB_HIGH = 0.1, 0.9


def resolve_commit(root: Path, ref: str) -> str | None:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        capture_output=True,
        text=True,
        cwd=root,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def informative(
    root: Path,
    tier: str,
    model: str,
    cases: Sequence[Case],
    named: set[str],
    all_cases: bool,
) -> tuple[list[Case], str]:
    """The cases an A/B run measures, and how they were chosen, for the report."""
    if all_cases:
        return list(cases), "every case in the tier"
    baseline_file = baseline_path(root / "benchmarks" / "baselines", model, tier)
    if not baseline_file.exists():
        return list(cases), "no baseline for this model, so every case in the tier"
    blessed = load_baseline(baseline_file).cases

    def informative_here(case_id: str) -> bool:
        rate = blessed[case_id].pass_rate if case_id in blessed else None
        return rate is not None and AB_LOW <= rate <= AB_HIGH

    chosen = [c for c in cases if c.id in named or informative_here(c.id)]
    return chosen, f"blessed between {AB_LOW} and {AB_HIGH}, plus any named"


def ab_stratum(
    case: str, base: Sequence[RepRecord], head: Sequence[RepRecord]
) -> CaseAB | None:
    """One case's two arms as a stratum, or None when an arm was mostly errors."""
    arms = {}
    for label, records in (("base", base), ("head", head)):
        summary = summarize(records)[0]
        if summary.inconclusive:
            return None
        reps = summary.passes + summary.fails + summary.errors
        arms[label] = (summary, reps)
    (bs, _), (hs, _) = arms["base"], arms["head"]
    return CaseAB(
        case=case,
        head_pass=hs.passes,
        head_n=hs.passes + hs.fails + hs.judge_errors,
        base_pass=bs.passes,
        base_n=bs.passes + bs.fails + bs.judge_errors,
        head_tokens=hs.tokens_median,
        base_tokens=bs.tokens_median,
        head_cost=hs.cost,
        base_cost=bs.cost,
        head_cost_reps=arms["head"][1] - hs.cost_unknown,
        base_cost_reps=arms["base"][1] - bs.cost_unknown,
    )


def ab_command(args: argparse.Namespace, root: Path, config: Config) -> int:
    """`bin/bench ab`: both commits, interleaved rep by rep on the same cases.

    Each commit is checked out to a temporary worktree and built as its own
    image tag, so the arms differ in nare and in nothing else. Results go to
    results/ab/<stamp>/, not results/*.jsonl, so compare and bless never read
    an A/B run as the newest run.
    """
    commits = {}
    for arm, ref in (("base", args.base), ("head", args.head)):
        sha = resolve_commit(root, ref)
        if sha is None:
            print(f"bench: {ref!r} is not a commit in this checkout", file=sys.stderr)
            return 2
        commits[arm] = sha
    if args.reps < 1:
        print("bench: --reps must be at least 1", file=sys.stderr)
        return 2
    try:
        every = load_cases(root / "benchmarks" / "cases", tier=args.tier)
    except CaseError as exc:
        print(f"bench: {exc}", file=sys.stderr)
        return 2
    cases, how = informative(
        root, args.tier, config.model, every, set(args.case or ()), args.all_cases
    )
    if not cases:
        print(f"bench: no cases to compare ({how})", file=sys.stderr)
        return 2
    why = require_judge(config, cases)
    if why is not None:
        print(f"bench: {why}", file=sys.stderr)
        return 2

    stamp = new_stamp()
    out = root / "benchmarks" / "results" / "ab" / stamp
    out.mkdir(parents=True)
    arms = {
        arm: replace(config, image=AB_TAG[arm], nare_commit=commits[arm])
        for arm in ("base", "head")
    }
    scratch = Path(tempfile.mkdtemp(prefix="nare-ab-"))
    trees: list[str] = []
    try:
        for arm in ("base", "head"):
            tree = scratch / arm
            proc = subprocess.run(
                ["git", "worktree", "add", "--detach", str(tree), commits[arm]],
                capture_output=True,
                text=True,
                cwd=root,
            )
            if proc.returncode != 0:
                print(f"bench: {proc.stderr.strip()}", file=sys.stderr)
                return 2
            trees.append(str(tree))
            try:
                build_image(tree, tag=AB_TAG[arm])
            except SandboxError as exc:
                print(f"bench: {arm}: {exc}", file=sys.stderr)
                return 2
        transport = judge_transport(config)
        records: dict[str, list[RepRecord]] = {"base": [], "head": []}
        for case in cases:
            for rep in range(args.reps):
                for arm in ("base", "head"):
                    evidence = out / arm
                    evidence.mkdir(exist_ok=True)
                    records[arm].append(
                        asyncio.run(run_rep(case, arms[arm], transport, rep, evidence))
                    )
    except NeverStarted as exc:
        print(f"bench: a rep never started: {exc.log}", file=sys.stderr)
        return 2
    finally:
        for worktree in trees:
            subprocess.run(
                ["git", "worktree", "remove", "--force", worktree],
                capture_output=True,
                cwd=root,
            )
        shutil.rmtree(scratch, ignore_errors=True)

    for arm in ("base", "head"):
        _write_records(out / f"{arm}.jsonl", records[arm])
    strata = []
    skipped = []
    for case in cases:
        base = [r for r in records["base"] if r.case == case.id]
        head = [r for r in records["head"] if r.case == case.id]
        stratum = ab_stratum(case.id, base, head)
        if stratum is None:
            skipped.append(case.id)
        else:
            strata.append(stratum)
    text = render_ab(
        base_label=f"{args.base} ({commits['base'][:12]})",
        head_label=f"{args.head} ({commits['head'][:12]})",
        reps=args.reps,
        how=how,
        strata=strata,
        skipped=skipped,
        mantel=mantel_haenszel(strata),
        effect=pass_rate_effect(strata),
        tokens=paired_ratio(token_pairs(strata)),
        dollars=paired_ratio(dollars_per_rep_pairs(strata)),
    )
    (out / "summary.txt").write_text(text + "\n", encoding="utf-8")
    print(text)
    print(f"results: {out}")
    return 0


def run_model(
    root: Path, cases: Sequence[Case], config: Config, name: str
) -> tuple[Transport, list[RepRecord]]:
    """Every case at its own reps: results/<name>.jsonl, and each rep's
    evidence under results/<name>/. A stop writes no results file: the cases
    it finished are kept in results/<name>/results.partial.jsonl."""
    transport = judge_transport(config)
    evidence = root / "benchmarks" / "results" / name
    records: list[RepRecord] = []
    try:
        for case in cases:
            records += asyncio.run(
                run_case(case, config, transport, case.reps, evidence)
            )
    except NeverStarted as exc:
        keep_partial(exc, evidence / PARTIAL, records)
        raise
    print(f"results: {write_results(root, records, name)}")
    return transport, records


def check_and_stage(
    tokens: Sequence[str],
    cases: Sequence[Case],
    *,
    base: Path,
    root: Path,
    dest: Path,
) -> RunArgs:
    """A run's arguments checked against nare's contract and the selected
    cases, then each prompt file copied to `dest`: what one model is run under.

    `base` is where a relative path is read from, `root` the checkout that
    recorded paths are relative to. `dest` must be absolute, since docker reads
    a relative `-v` source as a named volume. Every failure is an `ArgsError`,
    a copy that cannot be written included, so the caller refuses the run with
    exit 2 before the image is built.
    """
    check_invocation(tokens, {c.id: c.args for c in cases}, *nare_contract())
    try:
        return stage_inputs(tokens, base=base, root=root, dest=dest)
    except OSError as exc:
        raise ArgsError(
            f"run arguments: cannot copy the prompt files into {dest} "
            f"({exc.strerror or type(exc).__name__})"
        ) from exc


def confirm(
    root: Path,
    cases: Sequence[Case],
    config: Config,
    transport: Transport,
    name: str,
    records: list[RepRecord],
    baseline: Baseline,
    strict_tokens: bool,
) -> Comparison:
    """Re-run each guard cell that failed at CONFIRM_REPS, then compare.

    Only `suspect` cells are re-run: a guard cell with a failure on the first
    pass. Their confirmation reps replace the first pass, as they always have.

    The re-run rewrites results/<name>.jsonl and its evidence instead of
    starting a second of each. A stop keeps the re-run's finished cases in
    results/<name>/results.confirm.partial.jsonl, beside the first pass.
    """
    first = compare(
        summarize(records),
        baseline,
        strict_tokens=strict_tokens,
    )
    suspects = [d.case for d in first.deltas if d.verdict == "suspect"]
    if suspects:
        print(f"re-confirming {', '.join(suspects)} at {CONFIRM_REPS} reps...")
        evidence = root / "benchmarks" / "results" / name
        rerun: list[RepRecord] = []
        try:
            for case in [c for c in cases if c.id in suspects]:
                extra = asyncio.run(
                    run_case(case, config, transport, CONFIRM_REPS, evidence)
                )
                rerun += extra
                records = [r for r in records if r.case != case.id] + extra
        except NeverStarted as exc:
            keep_partial(exc, evidence / CONFIRM_PARTIAL, rerun)
            raise
        write_results(root, records, name)
    return compare(
        summarize(records),
        baseline,
        confirmed=True,
        strict_tokens=strict_tokens,
    )


def load_matrix(root: Path, tier: str) -> list[tuple[Path, Baseline]]:
    """The matrix for a tier is its set of baselines."""
    directory = root / "benchmarks" / "baselines"
    if not directory.is_dir():
        return []
    return [(p, load_baseline(p)) for p in sorted(directory.glob(f"*.{tier}.toml"))]


def misnamed(matrix: Sequence[tuple[Path, Baseline]], tier: str) -> str | None:
    """Why a baseline file is not at its model's baseline name, or None.

    bless and compare find a model's baseline by that name, and a matrix run
    names each model's results by it, so a renamed file, or a second file for
    one model, would be compared with nothing or run twice under one name.
    """
    wrong = [
        f"{path.name} names {baseline.meta.model!r}, whose {tier} baseline is "
        f"{expected.name}"
        for path, baseline in matrix
        if path != (expected := baseline_path(path.parent, baseline.meta.model, tier))
    ]
    return "; ".join(wrong) or None


def mixed_judges(matrix: Sequence[tuple[Path, Baseline]]) -> str | None:
    """Why the matrix has more than one judge, or None when it has one.

    When each model judges itself, a cross-model table measures the judges as
    much as the agents.
    """
    judges: dict[str, list[str]] = defaultdict(list)
    for path, baseline in matrix:
        judges[baseline.meta.judge_model].append(path.name)
    if len(judges) < 2:
        return None
    named = "; ".join(
        f"{judge or '(none)'} in {', '.join(names)}"
        for judge, names in sorted(judges.items())
    )
    return f"the matrix needs one judge, and its baselines name {len(judges)}: {named}"


def served_check(matrix: Sequence[tuple[Path, Config]], base_url: str | None) -> int:
    """The preflight: 2 when the endpoint does not list a matrix model or the
    judge.

    Each key lists once, since a proxy key can be limited to some models, and
    the judge is checked under every key, since it answers on each baseline's
    provider. A key whose listing fails warns and leaves only its own models
    unchecked, and a run with no base URL, so no proxy to ask, warns and
    checks nothing: an outage of the listing should not block a measurement.
    """
    if not base_url:
        print(
            "bench: warning: no base URL, so the matrix's models were not checked "
            "against the endpoint's list",
            file=sys.stderr,
        )
        return 0
    served: dict[str, set[str] | None] = {}
    missing: list[str] = []
    for path, config in matrix:
        key = config.api_key or ""
        first = key not in served
        if first:
            try:
                served[key] = list_models(base_url, key).names
            except ListingError as exc:
                served[key] = None
                print(
                    "bench: warning: could not list the endpoint's models for "
                    f"{config.api_key_var} ({exc}); the models under that key "
                    "were not checked",
                    file=sys.stderr,
                )
        names = served[key]
        if names is None:
            continue
        if config.gateway_model not in names:
            missing.append(f"{config.model!r} ({path.name})")
        if first and config.judge_model not in names:
            missing.append(f"{config.judge_model!r} (the judge, in {path.name})")
    if missing:
        print(
            f"bench: the endpoint does not list {', '.join(missing)}; "
            "`bin/bench models` shows what it serves",
            file=sys.stderr,
        )
        return 2
    return 0


def run_matrix(args: argparse.Namespace, root: Path, cases: Sequence[Case]) -> int:
    """`run --matrix`: each baseline's model in turn, against its own baseline,
    under that baseline's own run arguments.

    Everything that can refuse the run does so before the image is built: an
    empty matrix, a baseline file not at its model's name, a second judge, a
    missing key, a cap above the non-streaming ceiling, run arguments nare
    would now refuse, a prompt file that cannot be read or no longer has the
    digest its baseline measured, a model or judge the endpoint does not
    list. No flag lifts the digest refusal: an edited prompt is a new
    experiment, which a single run with `--` measures. Sequential, never
    parallel: every run tags its image nare-bench:local.
    """
    matrix = load_matrix(root, args.tier)
    if not matrix:
        print(
            f"bench: no {args.tier} baselines, so the matrix is empty; bless one "
            f"with bin/bench bless --tier {args.tier} --model M",
            file=sys.stderr,
        )
        return 2
    # A model whose gateway name is withheld runs only when its variable is set;
    # otherwise it is skipped with a note, and the rest of the matrix still runs.
    unset = set(unset_aliases([b.meta.model for _, b in matrix], os.environ))
    for model in sorted(unset):
        print(
            f"bench: skipped {model}: set {MODEL_ALIASES[model]} to its gateway "
            "name to run it",
            file=sys.stderr,
        )
    matrix = [(path, b) for path, b in matrix if b.meta.model not in unset]
    if not matrix:
        print("bench: no matrix model left to run", file=sys.stderr)
        return 0
    why = misnamed(matrix, args.tier) or mixed_judges(matrix)
    if why is not None:
        print(f"bench: {why}", file=sys.stderr)
        return 2
    judge = matrix[0][1].meta.judge_model
    graded = sorted(b.meta.model for _, b in matrix if b.meta.model == judge)
    if graded:
        print(
            f"bench: warning: the matrix's judge, {judge!r}, is also a matrix model, "
            "so it grades its own reps; a judge from outside the matrix needs every "
            "baseline blessed again",
            file=sys.stderr,
        )
    base_url = args.base_url or os.environ.get("NARE_BASE_URL") or None
    # Taken before staging: each model's prompt copies live under its name.
    stamp = new_stamp()
    configs: list[tuple[Path, Config]] = []
    for path, baseline in matrix:
        meta = baseline.meta
        evidence = root / "benchmarks" / "results" / f"{stamp}.{slugify(meta.model)}"
        try:
            config = baseline_config(
                model=meta.model,
                provider=meta.provider,
                judge_model=meta.judge_model,
                max_tokens=meta.max_tokens,
                base_url=base_url,
                env=os.environ,
            )
            # A baseline records its paths relative to the checkout. nare may
            # have dropped or renamed a flag since the bless, so they are
            # checked again.
            staged = check_and_stage(
                meta.nare_args, cases, base=root, root=root, dest=evidence / "inputs"
            )
        except (ConfigError, ArgsError) as exc:
            print(f"bench: {path.name}: {exc}", file=sys.stderr)
            return 2
        moved = sorted(
            file
            for file in set(staged.files) | set(meta.nare_files)
            if staged.files.get(file) != meta.nare_files.get(file)
        )
        for file in moved:
            print(
                f"bench: {path.name}: {file} no longer has the digest the baseline "
                "measured; restore it, or bless that model again on the new file",
                file=sys.stderr,
            )
        if moved:
            return 2
        if case_changed(
            baseline, {c.id: case_hash(c) for c in cases}, args, where=path.name
        ):
            return 2
        configs.append((path, replace(config, run_args=staged)))
    code = served_check(configs, base_url)
    if code:
        return code
    try:
        build_image(root)
    except SandboxError as exc:
        print(f"bench: {exc}", file=sys.stderr)
        return 2

    columns: list[tuple[str, Comparison]] = []
    for (_, baseline), (_, config) in zip(matrix, configs, strict=True):
        print(f"\n== {config.model} ({config.provider}) ==")
        name = f"{stamp}.{slugify(config.model)}"
        try:
            transport, records = run_model(root, cases, config, name)
            final = confirm(
                root,
                cases,
                config,
                transport,
                name,
                records,
                baseline,
                args.strict_tokens,
            )
        except NeverStarted as exc:
            return stop_run(exc, root, stamp)
        print(render(final))
        columns.append((config.model, final))
    # Each model ran its baseline's own variant, so the note names one per
    # model and there is no second side to print.
    variants = {
        config.model: render_variant(config.run_args.recorded, config.run_args.files)
        for _, config in configs
    }
    print()
    print(render_matrix(columns, matrix[0][1].meta.judge_model, variants=variants))
    return max(comparison.exit_code for _, comparison in columns)


def baselines_table(root: Path, tier: str, selected: set[str]) -> int:
    """`compare --matrix --baselines`: the tier's baselines as the matrix
    table, with no results needed."""
    matrix = load_matrix(root, tier)
    if not matrix:
        print(f"bench: no {tier} baselines", file=sys.stderr)
        return 2
    why = misnamed(matrix, tier) or mixed_judges(matrix)
    if why is not None:
        print(f"bench: {why}", file=sys.stderr)
        return 2
    columns = [
        (
            baseline.meta.model,
            compare(
                [s for s in baseline.cases.values() if s.case in selected],
                baseline,
                confirmed=True,
            ),
        )
        for _, baseline in matrix
    ]
    variants = {
        baseline.meta.model: render_variant(
            baseline.meta.nare_args, baseline.meta.nare_files
        )
        for _, baseline in matrix
    }
    print(render_matrix(columns, matrix[0][1].meta.judge_model, variants=variants))
    return 0


def compare_matrix(args: argparse.Namespace, root: Path, cases: Sequence[Case]) -> int:
    """`compare --matrix`: the last matrix run's table, without running.

    Each model's results are compared with its own baseline under the rules
    `compare` uses, so the exit code is what `run --matrix` returned.
    """
    selected = {c.id for c in cases}
    if args.baselines:
        return baselines_table(root, args.tier, selected)
    files = latest_run(root, matrix=True)
    if not files:
        print(
            "bench: no matrix run yet; run `bin/bench run --matrix` first",
            file=sys.stderr,
        )
        return 2
    columns: list[tuple[str, Comparison]] = []
    judges: set[str] = set()
    variants: dict[str, str] = {}
    notes: list[str] = []
    # Every model's refusals, each naming its baseline, before exit 2: the
    # overrides are global, so stopping at the first would hide the rest.
    refused = False
    for path in files:
        records = read_results(path)
        if not records:
            continue
        model = records[0].model
        baseline_file = baseline_path(
            root / "benchmarks" / "baselines", model, args.tier
        )
        if not baseline_file.exists():
            print(f"bench: no baseline at {baseline_file}", file=sys.stderr)
            return 2
        baseline = load_baseline(baseline_file)
        config = as_recorded(
            resolve_config(
                model=model,
                provider=None,
                base_url=None,
                judge_model=None,
                env={},
                need_key=False,
            ),
            records,
        )
        if incomparable(
            baseline,
            config,
            args,
            where=baseline_file.name,
            hashes={s.case: s.case_hash for s in summarize(records)},
        ):
            refused = True
            continue
        judges.add(config.judge_model or "")
        variants[model] = render_variant(
            config.run_args.recorded, config.run_args.files
        )
        note = variant_note(baseline, config)
        if note is not None:
            indented = "\n".join(f"  {line}" for line in note.splitlines())
            notes.append(f"{model}:\n{indented}")
        summaries = [s for s in summarize(records) if s.case in selected]
        columns.append(
            (
                model,
                compare(
                    summaries,
                    baseline,
                    confirmed=True,
                    strict_tokens=args.strict_tokens,
                ),
            )
        )
    if refused:
        return 2
    if not columns:
        print(f"bench: {stamp_of(files[0])} holds no results", file=sys.stderr)
        return 2
    if all(d.summary is None for _, c in columns for d in c.deltas):
        print(
            f"bench: the matrix run {stamp_of(files[0])} holds none of the "
            "selected cases",
            file=sys.stderr,
        )
        return 2
    # Each model whose results ran another variant than its baseline, both
    # sides, above the table they qualify.
    for note in notes:
        print(note)
    print(render_matrix(columns, ", ".join(sorted(judges)), variants=variants))
    return max(comparison.exit_code for _, comparison in columns)


def show_models(args: argparse.Namespace, root: Path) -> int:
    """`bin/bench models`: free, it lists and calls no model."""
    env = os.environ
    base_url = args.base_url or env.get("NARE_BASE_URL")
    if not base_url:
        print(
            "bench: no base URL, so there is no proxy to ask; pass --base-url or "
            "set NARE_BASE_URL",
            file=sys.stderr,
        )
        return 2
    provider = args.provider or env.get("NARE_PROVIDER") or "anthropic"
    key_var = KEY_VARS.get(provider)
    if key_var is None:
        print(
            f"bench: unknown provider {provider!r}; nare supports: "
            + ", ".join(KEY_VARS),
            file=sys.stderr,
        )
        return 2
    api_key = env.get(key_var)
    if not api_key:
        print(f"bench: {key_var} is not set", file=sys.stderr)
        return 2
    try:
        listing = list_models(base_url, api_key)
    except ListingError as exc:
        print(f"bench: the endpoint did not list its models: {exc}", file=sys.stderr)
        return 1
    print(render_models(listing, baseline_tiers(root / "benchmarks" / "baselines")))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    own, nare = split_run_args(argv)
    args = build_parser().parse_args(own)
    if args.command is None:
        print(
            "bench needs a subcommand: run, compare, bless, verify, "
            "compare-judges, or models",
            file=sys.stderr,
        )
        return 2
    if nare is not None and args.command != "run":
        print(
            f"bench: {args.command} takes no arguments for nare; only run passes "
            "them, after --, and compare and bless read them from the results",
            file=sys.stderr,
        )
        return 2
    if nare is not None and args.matrix:
        print(
            "bench: --matrix runs each baseline's own run arguments; drop the -- "
            "and what follows",
            file=sys.stderr,
        )
        return 2

    root = repo_root()
    if args.command == "compare-judges":
        # No tier: it compares whatever cases the results file holds.
        return compare_judges(args, root)
    if args.command == "models":
        return show_models(args, root)
    if args.command == "calibrate":
        return calibrate_command(args, root)
    if args.command == "tool-health":
        return tool_health_command(args, root)
    try:
        cases = _select(root, args.tier, args.case)
    except CaseError as exc:
        print(f"bench: {exc}", file=sys.stderr)
        return 2
    if not cases:
        where = f"tier {args.tier}" if args.tier else "the corpus"
        print(f"bench: no cases in {where}", file=sys.stderr)
        return 2

    if args.command == "verify":
        try:
            check_invocation((), {c.id: c.args for c in cases}, *nare_contract())
        except ArgsError as exc:
            print(f"bench: {exc}", file=sys.stderr)
            return 2
        try:
            build_image(root)
        except SandboxError as exc:
            print(f"bench: {exc}", file=sys.stderr)
            return 2
        judge = None
        if args.judge:
            if not (args.judge_model or os.environ.get("NARE_BENCH_JUDGE_MODEL")):
                print(
                    "bench: verify --judge needs a judge model: pass --judge-model "
                    "or set NARE_BENCH_JUDGE_MODEL",
                    file=sys.stderr,
                )
                return 2
            try:
                # verify runs no agent, so the judge stands in for the model.
                config = resolve_config(
                    model=args.judge_model or os.environ.get("NARE_BENCH_JUDGE_MODEL"),
                    provider=args.provider,
                    base_url=args.base_url,
                    judge_model=args.judge_model,
                    env=os.environ,
                )
            except ConfigError as exc:
                print(f"bench: {exc}", file=sys.stderr)
                return 2
            judge = judge_from(lambda: judge_transport(config))
        text, code = verify(cases)
        references, reference_code = verify_references(cases, DockerRuntime(), judge)
        print(text)
        print(references)
        return max(code, reference_code)

    if getattr(args, "baselines", False) and not args.matrix:
        print("bench: --baselines goes with --matrix", file=sys.stderr)
        return 2
    if getattr(args, "matrix", False):
        if args.model or args.provider or args.judge_model:
            print(
                "bench: --matrix takes the model, provider and judge from each "
                "baseline; drop --model, --provider and --judge-model",
                file=sys.stderr,
            )
            return 2
        if args.command == "run":
            return run_matrix(args, root, cases)
        return compare_matrix(args, root, cases)

    if args.command == "run":
        # Before the key, the image and any rep, so a typo costs nothing. The
        # stamp is taken here because the prompt copies live under its name.
        stamp = new_stamp()
        try:
            run_args = check_and_stage(
                nare or (),
                cases,
                base=Path.cwd(),
                root=root,
                dest=root / "benchmarks" / "results" / stamp / "inputs",
            )
        except ArgsError as exc:
            print(f"bench: {exc}", file=sys.stderr)
            return 2

    try:
        config = resolve_config(
            model=args.model,
            provider=args.provider,
            base_url=args.base_url,
            judge_model=args.judge_model,
            env=os.environ,
            # compare and bless read local files only; they never spend a key.
            need_key=args.command in ("run", "ab"),
        )
    except ConfigError as exc:
        print(f"bench: {exc}", file=sys.stderr)
        return 2

    if args.command == "ab":
        return ab_command(args, root, config)

    if args.command == "run":
        why = require_judge(config, cases)
        if why is not None:
            print(f"bench: {why}", file=sys.stderr)
            return 2
        if config.judge_model == config.model:
            print(
                f"bench: warning: the judge is the model under test "
                f"({config.model!r}), so its reps grade themselves",
                file=sys.stderr,
            )
        config = replace(config, run_args=run_args)
        baseline_file = baseline_path(
            root / "benchmarks" / "baselines", config.model, args.tier
        )
        baseline = load_baseline(baseline_file) if baseline_file.exists() else None
        # Before the image and the reps: numbers the baseline cannot be
        # compared with would be paid for and then thrown away.
        if baseline is not None and incomparable(
            baseline, config, args, hashes={c.id: case_hash(c) for c in cases}
        ):
            return 2
        try:
            build_image(root)
        except SandboxError as exc:
            print(f"bench: {exc}", file=sys.stderr)
            return 2
        try:
            transport, records = run_model(root, cases, config, stamp)
            if baseline is None:
                print(render_first_run(summarize(records)))
                print(
                    "\nno baseline yet; bless one with: "
                    f"bin/bench bless --tier {args.tier}"
                )
                return 0
            final = confirm(
                root,
                cases,
                config,
                transport,
                stamp,
                records,
                baseline,
                args.strict_tokens,
            )
        except NeverStarted as exc:
            return stop_run(exc, root, stamp)
        note = variant_note(baseline, config)
        if note is not None:
            print(note)
        print(render(final))
        print(render_health(model_health(records)))
        print(calibration_text(root, records))
        return final.exit_code

    run_files = latest_run(root)
    if not run_files:
        print("bench: no results yet; run `bin/bench run` first", file=sys.stderr)
        return 2
    results_file = results_for(run_files, config.model)
    if results_file is None:
        print(
            f"bench: the newest run, {stamp_of(run_files[0])}, is a matrix run "
            f"with no results for {config.model!r}",
            file=sys.stderr,
        )
        return 2
    records = read_results(results_file)
    try:
        config = as_recorded(
            config, records, provider=args.provider, judge_model=args.judge_model
        )
    except ConfigError as exc:
        print(f"bench: {results_file.name}: {exc}", file=sys.stderr)
        return 2
    produced = sorted({r.model for r in records} - {config.model})
    if produced:
        print(
            f"bench: {results_file.name} was produced by {', '.join(produced)}, "
            f"not {config.model!r}; pass --model to match it",
            file=sys.stderr,
        )
        return 2
    # Only the cases this invocation selected: a full-tier results file must
    # not leak full-only cases into a smoke comparison or baseline.
    selected = {c.id for c in cases}
    summaries = [s for s in summarize(records) if s.case in selected]
    if not summaries:
        print(
            f"bench: {results_file.name} holds none of the selected cases",
            file=sys.stderr,
        )
        return 2
    baselines = root / "benchmarks" / "baselines"
    baseline_file = baseline_path(baselines, config.model, args.tier)

    if args.command == "bless":
        # Whatever the --allow flags say, and before anything is written:
        # nobody else could run such a baseline, and a file outside the
        # checkout would publish a local path.
        problems = unblessable(root, config.run_args.files)
        for problem in problems:
            print(f"bench: {results_file.name}: {problem}", file=sys.stderr)
        if problems:
            return 2
        baselines.mkdir(parents=True, exist_ok=True)
        old = load_baseline(baseline_file) if baseline_file.exists() else None
        tier_ids = {c.id for c in _select(root, args.tier, None)}
        merged = merge_baseline(old.cases if old else {}, summaries, tier_ids)
        fresh = {s.case for s in summaries if s.pass_rate is not None}
        kept = sorted({s.case for s in merged} - fresh)
        # Kept cases were measured under the old baseline's provider, judge,
        # cap and run arguments, and would be written under these results'.
        # Both are checked, so both refusals print when both apply.
        if old is not None and kept:
            relabel = model_changed(old, config, args.allow_model_change)
            if relabel:
                print(
                    f"bench: blessing would keep {', '.join(kept)} from "
                    f"{baseline_file.name} under these results' labels; run "
                    "those cases too, or pass --allow-model-change",
                    file=sys.stderr,
                )
            # No hint to match the old variant: bless says what to do.
            revariant = args_changed(old, config, args.allow_arg_change, match=None)
            if revariant:
                print(
                    f"bench: blessing would keep {', '.join(kept)} from "
                    f"{baseline_file.name} under these results' run arguments; "
                    "run those cases too, or pass --allow-arg-change",
                    file=sys.stderr,
                )
            if relabel or revariant:
                return 2
        meta = BaselineMeta(
            blessed=datetime.now(UTC).isoformat(timespec="seconds"),
            commit=_commit(),
            provider=config.provider,
            model=config.model,
            judge_model=config.judge_model or "",
            max_tokens=config.max_tokens,
            nare_args=config.run_args.recorded,
            nare_files=dict(config.run_args.files),
        )
        baseline_file.write_text(dumps_baseline(meta, merged), encoding="utf-8")
        print(
            f"blessed {baseline_file} from {results_file.name} "
            f"(provider {config.provider}, judge {config.judge_model})"
        )
        return 0

    if not baseline_file.exists():
        print(f"bench: no baseline at {baseline_file}", file=sys.stderr)
        return 2
    baseline = load_baseline(baseline_file)
    if not args.provider and not any(r.provider for r in records):
        # Results written before the provider was recorded, and no flag to
        # say: the route they took is unknown, which is not a mismatch.
        config = replace(config, provider="")
    if incomparable(
        baseline, config, args, hashes={s.case: s.case_hash for s in summaries}
    ):
        return 2
    comparison = compare(
        summaries,
        baseline,
        confirmed=True,
        strict_tokens=args.strict_tokens,
    )
    note = variant_note(baseline, config)
    if note is not None:
        print(note)
    print(render(comparison))
    return comparison.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
