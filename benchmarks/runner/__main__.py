"""`bin/bench` -- a thin adapter over the runner modules.

Everything decidable is decided in the pure modules; this file parses
arguments, orders the work, and prints.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

from benchmarks.runner.agreement import Header, Skip, render_summary
from benchmarks.runner.case import Case, CaseError, load_cases
from benchmarks.runner.compare import (
    Judges,
    RecordingTransport,
    run_comparison,
    select_work,
)
from benchmarks.runner.config import (
    KEY_VARS,
    Config,
    ConfigError,
    baseline_config,
    resolve_config,
)
from benchmarks.runner.grade import (
    CheckResult,
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
from benchmarks.runner.report import (
    Baseline,
    BaselineMeta,
    CaseDelta,
    CaseSummary,
    Comparison,
    RepRecord,
    baseline_path,
    compare,
    dumps_baseline,
    load_baseline,
    render,
    render_matrix,
    slugify,
    summarize,
)
from benchmarks.runner.sandbox import (
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
)
from benchmarks.runner.triage import triage_transport
from nare.transport import Transport

CheckRunner = Callable[[Case, str, int], tuple[int, str]]
CONFIRM_REPS = 7
# 124 is the sandbox timeout; 125-127 are docker's own (daemon, permissions,
# command not found). A check that exits one of these never ran, so it proves
# nothing about the fixture and must not read as a failing check.
DID_NOT_RUN = range(124, 128)
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
            help="compare against a baseline blessed on a different model or "
            "output cap",
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
    models = sub.add_parser(
        "models", help="list what the endpoint serves, with window, price, baselines"
    )
    models.add_argument("--provider", help="whose key to send (env: NARE_PROVIDER)")
    models.add_argument("--base-url", help="the proxy (env: NARE_BASE_URL)")
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


async def run_rep(
    case: Case, config: Config, transport: Transport, rep: int, evidence: Path
) -> RepRecord:
    """One repetition, from a clean fixture to a graded record.

    `evidence` is this run's directory under benchmarks/results/; the rep's
    session, output and judge replies are kept in a subdirectory of it.
    """
    empty = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    kept = evidence / f"{case.id}-{rep}"
    kept_ref = kept.relative_to(evidence.parent).as_posix()
    with sandbox(case) as (workdir, artifacts):
        result = run_agent(case, workdir, artifacts, config)
        keep_evidence(kept, artifacts, result)
        line = parse_stdout(result.stdout)
        if line is None or infra_error(line):
            # No result line means the run never produced one: a crash, a
            # timeout, a Docker fault. A transport failure nare caught is the
            # same thing with a result line. Either is an error, never a failure.
            if line is None:
                why = "timed out" if result.timed_out else "no result line"
            else:
                why = line.error or "run errored"
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
                judge_model=config.judge_model,
            )

        # Before the bash checks, so nothing a check writes reaches the judge.
        diff = git_diff(workdir)
        closing = line_text(result.stdout, line.questions)
        # Kept for every rep, judged or not, so any judge can be re-run against
        # exactly what this one read. compare-judges reads them back.
        (kept / "diff.patch").write_text(diff, encoding="utf-8")
        (kept / "closing.txt").write_text(closing, encoding="utf-8")

        graded = [
            grade_result_check(check, line)
            for check in case.checks
            if check.kind == "result"
        ]
        for check in case.bash_checks:
            code, output = run_check(workdir, check.cmd or "", 120, artifacts=artifacts)
            graded.append(grade_bash_check(check, code, output))

        met: list[bool] | None = None
        why = ""
        if case.judge is not None:
            attempts: tuple[JudgeAttempt, ...]
            try:
                met, why, attempts = await score(
                    case, diff, closing, transport=transport
                )
            except JudgeError as exc:
                why = f"judge failed: {exc}"
                attempts = exc.attempts
            (kept / "judge.txt").write_text(render_attempts(attempts), encoding="utf-8")

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
        artifacts=kept_ref,
        max_tokens=config.max_tokens,
        provider=config.provider,
        judge_model=config.judge_model,
    )


async def run_case(
    case: Case, config: Config, transport: Transport, reps: int, evidence: Path
) -> list[RepRecord]:
    # Sequential on purpose: repetitions share one rate limit and one Docker
    # daemon, and a benchmark that saturates either measures the machine.
    return [
        await run_rep(case, config, transport, rep, evidence) for rep in range(reps)
    ]


def write_results(
    root: Path, records: Sequence[RepRecord], stamp: str | None = None
) -> Path:
    directory = root / "benchmarks" / "results"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stamp or new_stamp()}.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for rec in records:
            payload = asdict(rec)
            payload["checks"] = [asdict(c) for c in rec.checks]
            handle.write(json.dumps(payload) + "\n")
    return path


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


def model_changed(baseline: Baseline, config: Config, allow: bool) -> bool:
    """Numbers are comparable within a model and nowhere else.

    Pinning the judge model too is the non-obvious half: if it changes under
    the suite, every score shifts at once and reads as a harness regression.
    The agent's output cap is pinned for the same reason: a reasoning model
    cut off at nare's default fails reps it passes with room to think. So is
    the provider: the same model on the other route can lose a reasoning
    model's thinking on the way. An empty `config.provider` is results that
    never recorded theirs, which is unknown, not a mismatch.
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
        f"bench: baseline was blessed on provider {baseline.meta.provider!r} / "
        f"model {baseline.meta.model!r} / judge {baseline.meta.judge_model!r} / "
        f"output cap {cap(baseline.meta.max_tokens)}; this run used {provider} / "
        f"{config.model!r} / {config.judge_model!r} / {cap(config.max_tokens)}. "
        "Numbers are not comparable across providers, models or caps. Pass "
        "--allow-model-change to override.",
        file=sys.stderr,
    )
    return True


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
    try:
        config = resolve_config(
            # The model under test comes from the records, so NARE_MODEL is
            # not needed.
            model=models[0],
            provider=args.provider,
            base_url=base_url,
            judge_model=args.judge_model,
            env=env,
        )
        cases = {c.id: c for c in load_cases(root / "benchmarks" / "cases")}
    except (ConfigError, CaseError) as exc:
        print(f"bench: {exc}", file=sys.stderr)
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
    """The cap, provider and judge the results were run with, not the ones in
    this shell: compare and bless never run the agent.

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
    return replace(
        config,
        max_tokens=next((r.max_tokens for r in records), None),
        provider=ran_provider or config.provider,
        judge_model=ran_judge or config.judge_model,
    )


def run_model(
    root: Path, cases: Sequence[Case], config: Config, name: str
) -> tuple[Transport, list[RepRecord]]:
    """Every case at its own reps: results/<name>.jsonl, and each rep's
    evidence under results/<name>/."""
    transport = judge_transport(config)
    evidence = root / "benchmarks" / "results" / name
    records: list[RepRecord] = []
    for case in cases:
        records += asyncio.run(run_case(case, config, transport, case.reps, evidence))
    print(f"results: {write_results(root, records, name)}")
    return transport, records


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
    """Re-run each case whose pass rate dropped at CONFIRM_REPS, then compare.

    The re-run rewrites results/<name>.jsonl and its evidence instead of
    starting a second of each.
    """
    first = compare(
        summarize(records),
        baseline,
        token_band=TOKEN_BAND,
        strict_tokens=strict_tokens,
    )
    suspects = [d.case for d in first.deltas if d.verdict == "suspect"]
    if suspects:
        print(f"re-confirming {', '.join(suspects)} at {CONFIRM_REPS} reps...")
        evidence = root / "benchmarks" / "results" / name
        for case in [c for c in cases if c.id in suspects]:
            extra = asyncio.run(
                run_case(case, config, transport, CONFIRM_REPS, evidence)
            )
            records = [r for r in records if r.case != case.id] + extra
        write_results(root, records, name)
    return compare(
        summarize(records),
        baseline,
        confirmed=True,
        token_band=TOKEN_BAND,
        strict_tokens=strict_tokens,
    )


def load_matrix(root: Path, tier: str) -> list[tuple[Path, Baseline]]:
    """The matrix for a tier is its set of baselines."""
    directory = root / "benchmarks" / "baselines"
    if not directory.is_dir():
        return []
    return [(p, load_baseline(p)) for p in sorted(directory.glob(f"*.{tier}.toml"))]


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
        if config.model not in names:
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
    """`run --matrix`: each baseline's model in turn, against its own baseline.

    Everything that can refuse the run does so before the image is built: an
    empty matrix, a second judge, a missing key, a cap above the non-streaming
    ceiling, a model or judge the endpoint does not list. Sequential, never
    parallel:
    every run tags its image nare-bench:local.
    """
    matrix = load_matrix(root, args.tier)
    if not matrix:
        print(
            f"bench: no {args.tier} baselines, so the matrix is empty; bless one "
            f"with bin/bench bless --tier {args.tier} --model M",
            file=sys.stderr,
        )
        return 2
    why = mixed_judges(matrix)
    if why is not None:
        print(f"bench: {why}", file=sys.stderr)
        return 2
    base_url = args.base_url or os.environ.get("NARE_BASE_URL") or None
    configs: list[tuple[Path, Config]] = []
    for path, baseline in matrix:
        try:
            config = baseline_config(
                model=baseline.meta.model,
                provider=baseline.meta.provider,
                judge_model=baseline.meta.judge_model,
                max_tokens=baseline.meta.max_tokens,
                base_url=base_url,
                env=os.environ,
            )
        except ConfigError as exc:
            print(f"bench: {path.name}: {exc}", file=sys.stderr)
            return 2
        configs.append((path, config))
    code = served_check(configs, base_url)
    if code:
        return code
    try:
        build_image(root)
    except SandboxError as exc:
        print(f"bench: {exc}", file=sys.stderr)
        return 2

    stamp = new_stamp()
    columns: list[tuple[str, Comparison]] = []
    for (_, baseline), (_, config) in zip(matrix, configs, strict=True):
        print(f"\n== {config.model} ({config.provider}) ==")
        name = f"{stamp}.{slugify(config.model)}"
        transport, records = run_model(root, cases, config, name)
        final = confirm(
            root, cases, config, transport, name, records, baseline, args.strict_tokens
        )
        print(render(final))
        columns.append((config.model, final))
    print()
    print(render_matrix(columns, matrix[0][1].meta.judge_model))
    return max(comparison.exit_code for _, comparison in columns)


def baselines_table(root: Path, tier: str, selected: set[str]) -> int:
    """`compare --matrix --baselines`: the tier's baselines as the matrix
    table, with no results needed."""
    matrix = load_matrix(root, tier)
    if not matrix:
        print(f"bench: no {tier} baselines", file=sys.stderr)
        return 2
    why = mixed_judges(matrix)
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
    print(render_matrix(columns, matrix[0][1].meta.judge_model))
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
        if model_changed(baseline, config, args.allow_model_change):
            return 2
        judges.add(config.judge_model)
        summaries = [s for s in summarize(records) if s.case in selected]
        columns.append(
            (
                model,
                compare(
                    summaries,
                    baseline,
                    confirmed=True,
                    token_band=TOKEN_BAND,
                    strict_tokens=args.strict_tokens,
                ),
            )
        )
    if not columns:
        print(f"bench: {stamp_of(files[0])} holds no results", file=sys.stderr)
        return 2
    print(render_matrix(columns, ", ".join(sorted(judges))))
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
    args = build_parser().parse_args(argv)
    if args.command is None:
        print(
            "bench needs a subcommand: run, compare, bless, verify, "
            "compare-judges, or models",
            file=sys.stderr,
        )
        return 2

    root = repo_root()
    if args.command == "compare-judges":
        # No tier: it compares whatever cases the results file holds.
        return compare_judges(args, root)
    if args.command == "models":
        return show_models(args, root)
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

    try:
        config = resolve_config(
            model=args.model,
            provider=args.provider,
            base_url=args.base_url,
            judge_model=args.judge_model,
            env=os.environ,
            # compare and bless read local files only; they never spend a key.
            need_key=args.command == "run",
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
        stamp = new_stamp()
        transport, records = run_model(root, cases, config, stamp)
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
        if model_changed(baseline, config, args.allow_model_change):
            return 2
        final = confirm(
            root, cases, config, transport, stamp, records, baseline, args.strict_tokens
        )
        print(render(final))
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
        baselines.mkdir(parents=True, exist_ok=True)
        kept = load_baseline(baseline_file).cases if baseline_file.exists() else {}
        tier_ids = {c.id for c in _select(root, args.tier, None)}
        meta = BaselineMeta(
            blessed=datetime.now(UTC).isoformat(timespec="seconds"),
            commit=_commit(),
            provider=config.provider,
            model=config.model,
            judge_model=config.judge_model,
            max_tokens=config.max_tokens,
        )
        baseline_file.write_text(
            dumps_baseline(meta, merge_baseline(kept, summaries, tier_ids)),
            encoding="utf-8",
        )
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
    if model_changed(baseline, config, args.allow_model_change):
        return 2
    comparison = compare(
        summaries,
        baseline,
        confirmed=True,
        token_band=TOKEN_BAND,
        strict_tokens=args.strict_tokens,
    )
    print(render(comparison))
    return comparison.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
