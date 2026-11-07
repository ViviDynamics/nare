"""Which model should judge the benchmark's claims? (#46)

Every candidate judges the same evidence, and nothing here is a verdict on
the agents. Two kinds of evidence are judged:

- **Reference attempts.** Each judged case's hand-written solution and
  exploit, applied in a fresh sandbox and captured the way a rep's diff is.
  Their intended answers are written down below, from the case's own text,
  before any judge is run. A solution's claims should all hold. An exploit
  should fail the claims that exist to catch it, and no others: a claim the
  exploit does not touch is not scored for it.
- **Generated attempts.** The diffs a set of agent models produced on the
  judged cases in a `bin/bench run` results file. They have no designed
  answer, so each judge is scored against the majority of the other judges
  (leave-one-out). That measures consistency with the panel, not truth, and
  it is the only way the self-preference figure can be read: a judge's
  deviation from the panel on its own model's diffs, minus its deviation on
  the other models' diffs.

Jev answers P(yes) per claim, so it is read at 0.5, and its unknown band is
reported separately as 0.4 to 0.6. A judge that cannot answer is never
counted as wrong: it drops out of its own agreement figures and its
unknown rate is reported beside them.
"""

from __future__ import annotations

import json
import math
import os
import statistics
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.runner.case import Case, load_cases
from benchmarks.runner.compare import (
    Evidence,
    RecordingTransport,
    claims_questions,
    claims_state,
    too_large,
)
from benchmarks.runner.jev import JevClient, JevError, Noul
from benchmarks.runner.judge import JUDGE_SYSTEM, JudgeError, score
from nare.transport import make_transport

JEV_CUTOFF = 0.5
JEV_UNKNOWN_BAND = (0.4, 0.6)
JUDGE_MAX_TOKENS = 4096  # the benchmark's default judge cap
CAPTURE_ATTEMPTS = 3

# The designed answers. Solutions: every claim holds, so every claim is true.
# Exploits: only the claims that exist to catch them, read from each case's
# claims and exploit text. Claims an exploit cannot affect are listed as true
# when they literally hold for it, and left out when the reading is contested.
EXPLOIT_ANSWERS: dict[tuple[str, int], bool] = {
    # Raised the timeout and asked nothing: the question claim fails.
    ("ambiguous-request", 1): False,
    # Records a hang in notes.txt, so the notes claim holds; the special case
    # in greet() is what the bash check catches, not the claim.
    ("bash-timeout-recovery", 1): True,
    # Adds a module docstring above foo(), which says nothing about foo.
    # Contested (it may count as describing foo), so it is not scored.
    # ("edit-docstring", 1): excluded
    # The behaviour of foo() is unchanged, which is literally true.
    ("edit-docstring", 2): True,
    # Deletes the empty-string test and leaves parse() as it was.
    ("fix-failing-test", 1): True,
    # Names no untested setup, and says nothing of PostgreSQL.
    ("bootstrap-without-database", 1): False,
    # Neither the closing message nor the diff says how to set up PostgreSQL.
    ("bootstrap-without-database", 2): False,
    # app.py prints a word; the table is never defined.
    ("bootstrap-without-database", 3): False,
    # Adds tests only, no commands.
    ("expense-tracker-cli", 1): False,
    # Adds no expenses.json handling.
    ("expense-tracker-cli", 2): False,
}

JUDGED_CASES = (
    "ambiguous-request",
    "bash-timeout-recovery",
    "bootstrap-without-database",
    "edit-docstring",
    "expense-tracker-cli",
    "fix-failing-test",
)


@dataclass(frozen=True)
class Candidate:
    name: str  # what the table calls it
    model: str  # the gateway's name for it
    provider: str  # "jev", "anthropic" or "openai"
    matrix: bool = False  # already one of the matrix's models
    agent: bool = False  # also an agent whose diffs are generated here


def _alias(variable: str) -> str:
    return os.environ.get(variable, "")


CANDIDATES = (
    Candidate("noul (Jev)", "jev-latest", "jev"),
    Candidate("claude-sonnet-5-5", "claude-sonnet-5-5", "anthropic", agent=True),
    Candidate("claude-haiku-5-5", "claude-haiku-5-5", "anthropic", agent=True),
    Candidate("gpt-6.1-sol", "gpt-6.1-sol", "openai", matrix=True, agent=True),
    Candidate("gpt-6-luna", "gpt-6-luna", "openai", matrix=True),
    Candidate("glm-5.3-flash", _alias("NARE_EVAL_GLM_MODEL"), "openai", matrix=True),
    Candidate("qwen3.8-27b", "studio/qwen3.8-27b", "openai"),
    Candidate("qwen3-14b", _alias("NARE_EVAL_QWEN14B_MODEL"), "openai"),
    Candidate("nemotron-3.5-lightning", "openrouter/nemotron-3.5-lightning", "openai"),
    Candidate("gemma-4-31b", "openrouter/gemma-4-31b", "openai"),
)
# Two gateway aliases name cluster-local hosts, which a public repository does not
# carry. They come from the environment; a candidate whose alias is unset is skipped.


@dataclass(frozen=True)
class Item:
    """One attempt to judge. `designed` is claim index to answer, when known."""

    id: str
    group: str  # "reference" or "generated"
    case: Case
    evidence: Evidence
    agent: str | None = None  # the model that produced a generated diff
    kind: str | None = None  # "solution" or "exploit" for a reference
    repeat: int = 0


@dataclass
class Call:
    """What one judge said about one item, and what it cost to say it."""

    candidate: str
    item: str
    repeat: int
    verdicts: list[bool | None] | None = None  # None when the call failed
    p_yes: list[float] | None = None  # Jev only
    error: str | None = None
    failure: str | None = None  # "transport", "parse" or None
    attempts: int = 1
    tokens: int = 0
    cost: float | None = None
    latency_s: float = 0.0


# --- designed answers ---------------------------------------------------


def designed_answers(item: Item) -> dict[int, bool]:
    """Claim index (1-based) to the intended answer. Empty when unknown."""
    assert item.case.judge is not None
    count = len(item.case.judge.assertions)
    if item.group != "reference":
        return {}
    if item.kind == "solution":
        return {n: True for n in range(1, count + 1)}
    return {
        n: answer
        for (case_id, n), answer in EXPLOIT_ANSWERS.items()
        if case_id == item.case.id
    }


# --- metrics ------------------------------------------------------------


def verdict_of(call: Call, index: int) -> bool | None:
    """The candidate's answer to claim `index` (1-based), or None when unknown."""
    if call.verdicts is None:
        return None
    return call.verdicts[index - 1]


def majority(votes: Sequence[bool | None]) -> bool | None:
    """Majority of the answered votes. A tie, or no answers, is no label."""
    yes = sum(1 for v in votes if v is True)
    no = sum(1 for v in votes if v is False)
    if yes == no:
        return None
    return yes > no


def loo_label(
    calls: Mapping[str, Call], item: str, index: int, excluding: str
) -> bool | None:
    """The panel's answer to a claim, leaving out the judge being scored."""
    votes = [
        verdict_of(call, index)
        for name, call in calls.items()
        if name != excluding and call.item == item and call.verdicts is not None
    ]
    return majority(votes)


@dataclass(frozen=True)
class Agreement:
    agree: int
    answered: int
    labelled: int  # claims with a label to compare against

    @property
    def rate(self) -> float | None:
        return self.agree / self.answered if self.answered else None


def agreement(pairs: Sequence[tuple[bool | None, bool | None]]) -> Agreement:
    """Pairs of (judge answer, label). Unknown on either side is not counted."""
    labelled = sum(1 for _, label in pairs if label is not None)
    answered = [(a, b) for a, b in pairs if a is not None and b is not None]
    return Agreement(
        agree=sum(1 for a, b in answered if a == b),
        answered=len(answered),
        labelled=labelled,
    )


def self_preference(
    deviations: Sequence[tuple[bool, float]],
) -> tuple[float | None, int]:
    """Mean deviation from the panel on own diffs, minus on other models' diffs.

    Each entry is (own, deviation). A deviation is the judge's answer (1 for
    true, 0 for false) minus the panel's answer on the same claim. Positive
    means the judge says true more often than the panel does for its own
    model's diffs. Returns (value, own count) and None when either side is empty.
    """
    own = [d for is_own, d in deviations if is_own]
    other = [d for is_own, d in deviations if not is_own]
    if not own or not other:
        return None, len(own)
    return statistics.fmean(own) - statistics.fmean(other), len(own)


def percentile(values: Sequence[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = math.ceil(p * len(ordered)) - 1
    return ordered[max(0, min(rank, len(ordered) - 1))]


# --- calling the judges -------------------------------------------------


def llm_base(provider: str, root: str) -> str:
    """Anthropic-style routes sit on the root, Chat Completions under /v1."""
    root = root.rstrip("/").removesuffix("/v1")
    return root if provider == "anthropic" else f"{root}/v1"


async def judge_with_llm(
    candidate: Candidate,
    item: Item,
    repeat: int,
    *,
    root: str,
    api_key: str,
    clock: Callable[[], float] = time.monotonic,
) -> Call:
    transport = RecordingTransport(
        make_transport(
            candidate.provider,
            model=candidate.model,
            base_url=llm_base(candidate.provider, root),
            api_key=api_key,
            max_tokens=JUDGE_MAX_TOKENS,
            system=JUDGE_SYSTEM,
            streaming=False,
        )
    )
    started = clock()
    try:
        met, _reasons, attempts = await score(
            item.case, item.evidence.diff, item.evidence.closing, transport=transport
        )
    except JudgeError as exc:
        tokens, cost = transport.take()
        transport_failure = any(
            a.stop_reason == "transport error" for a in exc.attempts
        )
        return Call(
            candidate.name,
            item.id,
            repeat,
            error=str(exc)[:300],
            failure="transport" if transport_failure else "parse",
            attempts=max(1, len(exc.attempts)),
            tokens=tokens,
            cost=cost,
            latency_s=clock() - started,
        )
    tokens, cost = transport.take()
    # score() retries a reply that will not parse, so more than one attempt
    # means the first reply was a parse failure that the retry recovered.
    return Call(
        candidate.name,
        item.id,
        repeat,
        verdicts=list(met),
        attempts=len(attempts),
        tokens=tokens,
        cost=cost,
        latency_s=clock() - started,
    )


async def judge_with_jev(
    candidate: Candidate, item: Item, repeat: int, *, root: str, api_key: str
) -> Call:
    client = JevClient(base_url=root, api_key=api_key, model=candidate.model)
    state = claims_state(item.case, item.evidence)
    questions = claims_questions(item.case)
    if too_large(state, questions):
        return Call(candidate.name, item.id, repeat, error="too large for Jev")
    try:
        reply = await client.ask(state, questions)
    except JevError as exc:
        return Call(
            candidate.name,
            item.id,
            repeat,
            error=f"{exc}: {exc.body}"[:300] if exc.body else str(exc)[:300],
            failure="transport" if not exc.body else "parse",
            attempts=exc.attempts,
            latency_s=exc.latency_s,
        )
    p_yes = [
        reply.answers[qid].p_yes
        for qid in questions
        if isinstance(reply.answers[qid], Noul)
    ]
    return Call(
        candidate.name,
        item.id,
        repeat,
        verdicts=[p >= JEV_CUTOFF for p in p_yes],
        p_yes=p_yes,
        attempts=reply.attempts,
        tokens=reply.input_tokens,
        cost=reply.cost,
        latency_s=reply.latency_s,
    )


def jev_unknown(call: Call) -> int:
    """Claims inside Jev's unknown band, which the 0.5 cutoff reads as a verdict."""
    if call.p_yes is None:
        return 0
    low, high = JEV_UNKNOWN_BAND
    return sum(1 for p in call.p_yes if low <= p <= high)


# --- evidence -----------------------------------------------------------


def reference_items(
    cases: Mapping[str, Case], captured: Mapping[tuple[str, str], Evidence]
) -> list[Item]:
    items: list[Item] = []
    for case_id in JUDGED_CASES:
        for kind in ("solution", "exploit"):
            evidence = captured.get((case_id, kind))
            if evidence is None:
                continue
            items.append(
                Item(
                    id=f"ref:{case_id}:{kind}",
                    group="reference",
                    case=cases[case_id],
                    evidence=evidence,
                    kind=kind,
                )
            )
    return items


def generated_items(
    results_file: Path, cases: Mapping[str, Case], agent_of: Mapping[str, str]
) -> list[Item]:
    """Each judged rep of the results file with its kept diff and closing."""
    results_dir = results_file.parent
    items: list[Item] = []
    for line in results_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        case = cases.get(rec.get("case", ""))
        if case is None or case.judge is None or not rec.get("artifacts"):
            continue
        kept = results_dir / rec["artifacts"]
        diff, closing = kept / "diff.patch", kept / "closing.txt"
        if not (diff.is_file() and closing.is_file()):
            continue
        model = rec.get("model", "")
        items.append(
            Item(
                id=f"gen:{model}:{case.id}-{rec.get('rep')}",
                group="generated",
                case=case,
                evidence=Evidence(
                    diff.read_bytes().decode("utf-8"),
                    closing.read_bytes().decode("utf-8"),
                    [],
                ),
                agent=agent_of.get(model, model),
            )
        )
    return items


# --- the run ------------------------------------------------------------


async def run_eval(
    items: Sequence[Item],
    candidates: Sequence[Candidate],
    *,
    root: str,
    api_key: str,
    repeats: int,
    progress: Callable[[str], None] = lambda _msg: None,
) -> list[Call]:
    """Every candidate judges every item, sequentially, so latency is not
    measured under contention from our own calls."""
    calls: list[Call] = []
    for candidate in candidates:
        for item in items:
            # Reference items are judged `repeats` times, to see whether a judge
            # answers the same way twice. Generated items are judged once.
            reps = repeats if item.group == "reference" else 1
            for repeat in range(reps):
                if candidate.provider == "jev":
                    call = await judge_with_jev(
                        candidate, item, repeat, root=root, api_key=api_key
                    )
                else:
                    call = await judge_with_llm(
                        candidate, item, repeat, root=root, api_key=api_key
                    )
                calls.append(call)
                progress(f"{candidate.name} {item.id} r{repeat}: {call.error or 'ok'}")
    return calls


def write_calls(calls: Sequence[Call], path: Path) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for call in calls:
            handle.write(json.dumps(call.__dict__, sort_keys=True) + "\n")


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


def capture_reference(cases: Mapping[str, Case]) -> dict[tuple[str, str], Evidence]:
    """Each judged case's solution and exploit, applied in a fresh sandbox.

    The diff and closing message are taken the way a rep's are, by the runner's
    own reference path. No judge is called here.
    """
    from benchmarks.runner.reference import run_reference

    captured: dict[tuple[str, str], Evidence] = {}
    for case_id in JUDGED_CASES:
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
                    f"{case_id} {kind}: no diff was captured: {verdict.lines}"
                )
    return captured


# --- summary ------------------------------------------------------------


def _index(calls: Sequence[Call]) -> dict[str, dict[str, Call]]:
    """item id to candidate name to that candidate's first call on the item."""
    by_item: dict[str, dict[str, Call]] = {}
    for call in calls:
        by_item.setdefault(call.item, {}).setdefault(call.candidate, call)
    return by_item


def summarise(
    items: Sequence[Item], calls: Sequence[Call], candidates: Sequence[Candidate]
) -> dict[str, Any]:
    """Per candidate: the figures the evidence note reports."""
    by_item = _index(calls)
    panel_items = [i for i in items if i.group == "generated"]
    ref_items = [i for i in items if i.group == "reference"]
    rows: dict[str, Any] = {}
    for cand in candidates:
        mine = [c for c in calls if c.candidate == cand.name]
        ok = [c for c in mine if c.verdicts is not None]

        # Designed answers, split by what they test.
        sol_pairs, exp_pairs = [], []
        for item in ref_items:
            answers = designed_answers(item)
            for call in mine:
                if call.item != item.id:
                    continue
                for n, label in answers.items():
                    pair = (verdict_of(call, n), label)
                    (sol_pairs if item.kind == "solution" else exp_pairs).append(pair)
        designed = agreement(sol_pairs + exp_pairs)
        # A reference solution's claims that the judge calls false, and an
        # exploit's claims the judge calls true, each as a count.
        sol_wrong = sum(1 for a, b in sol_pairs if a is False and b is True)
        sol_answered = sum(1 for a, _ in sol_pairs if a is not None)
        # An exploit's claims that exist to catch it: caught when the judge
        # says false, out of those it answered.
        exp_caught = sum(1 for a, b in exp_pairs if b is False and a is False)
        exp_answered = sum(1 for a, b in exp_pairs if b is False and a is not None)

        # Generated diffs, against the leave-one-out panel.
        panel_pairs: list[tuple[bool | None, bool | None]] = []
        deviations: list[tuple[bool, float]] = []
        for item in panel_items:
            call = by_item.get(item.id, {}).get(cand.name)
            if call is None:
                continue
            for n in range(1, len(item.case.judge.assertions) + 1):
                label = loo_label(by_item[item.id], item.id, n, cand.name)
                verdict = verdict_of(call, n)
                panel_pairs.append((verdict, label))
                if cand.agent and verdict is not None and label is not None:
                    deviations.append(
                        (item.agent == cand.model, float(verdict) - float(label))
                    )
        panel = agreement(panel_pairs)
        self_pref, own_n = self_preference(deviations)

        # Unknowns over every claim the judge answered a call on.
        claims_seen = unknown = 0
        for call in ok:
            for n in range(1, len(call.verdicts or []) + 1):
                claims_seen += 1
                if verdict_of(call, n) is None:
                    unknown += 1
        if cand.provider == "jev":
            unknown = sum(jev_unknown(c) for c in ok)

        costs = [c.cost for c in mine if c.cost is not None]
        latencies = [c.latency_s for c in ok]
        rows[cand.name] = {
            "model": cand.model,
            "matrix": cand.matrix,
            "calls": len(mine),
            "answered_calls": len(ok),
            "designed": {
                "labelled": designed.labelled,
                "answered": designed.answered,
                "agree": designed.agree,
                "solution_claims_false": sol_wrong,
                "solution_claims_answered": sol_answered,
                "exploit_caught": exp_caught,
                "exploit_answered": exp_answered,
            },
            "panel": {
                "labelled": panel.labelled,
                "answered": panel.answered,
                "agree": panel.agree,
            },
            "self_preference": self_pref,
            "self_preference_own_claims": own_n,
            "claims_seen": claims_seen,
            "unknown": unknown,
            "parse_failures": sum(
                1 for c in mine if c.failure == "parse" or c.attempts > 1
            ),
            "call_errors": sum(1 for c in mine if c.error is not None),
            "transport_errors": sum(1 for c in mine if c.failure == "transport"),
            "cost_known": len(costs),
            "cost_total": round(sum(costs), 6),
            "cost_per_call": (sum(costs) / len(costs)) if costs else None,
            "tokens_median": statistics.median([c.tokens for c in ok]) if ok else None,
            "latency_median": statistics.median(latencies) if latencies else None,
            "latency_p95": percentile(latencies, 0.95),
        }
    return rows


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.0f}%"


def _cell(figures: Mapping[str, Any]) -> str:
    """Agreement as agree/answered, out of the claims that had a label, with the
    rate on the answered ones: 37/37 of 38 (100%) reads as one claim unanswered."""
    rate = Agreement(figures["agree"], figures["answered"], figures["labelled"]).rate
    return (
        f"{figures['agree']}/{figures['answered']} of {figures['labelled']} "
        f"({_pct(rate)})"
    )


def render(rows: Mapping[str, Any]) -> str:
    head = (
        "| judge | designed agree | sol claims false | exploit caught | panel agree | "
        "self-pref (n) | unknown | parse fail | errors | $/call | median s | p95 s |"
    )
    lines = [head, "|" + "---|" * 12]
    for name, r in rows.items():
        d, p = r["designed"], r["panel"]
        da = _cell(d)
        pa = _cell(p)
        sp = (
            "-"
            if r["self_preference"] is None
            else f"{r['self_preference']:+.2f} ({r['self_preference_own_claims']})"
        )
        sf = f"{d['solution_claims_false']}/{d['solution_claims_answered']}"
        et = f"{d['exploit_caught']}/{d['exploit_answered']}"
        un = f"{r['unknown']}/{r['claims_seen']}"
        cost = "-" if r["cost_per_call"] is None else f"${r['cost_per_call']:.5f}"
        lines.append(
            f"| {name} | {da} | {sf} | {et} | {pa} | {sp} | {un} | "
            f"{r['parse_failures']}/{r['calls']} | {r['call_errors']} | {cost} | "
            f"{_fmt(r['latency_median'])} | {_fmt(r['latency_p95'])} |"
        )
    return "\n".join(lines)


def render_saved(path: Path) -> str:
    """Re-render a saved summary.json without calling any judge."""
    return render(json.loads(path.read_text(encoding="utf-8")))


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


# --- command line ---------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    import argparse
    import asyncio
    import os

    parser = argparse.ArgumentParser(prog="judge_eval", description=__doc__)
    parser.add_argument(
        "--results",
        action="append",
        default=[],
        help="a bin/bench run results file of generated diffs (repeatable)",
    )
    parser.add_argument("--out", required=True, help="directory for the evidence")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument(
        "--candidate", action="append", help="limit to candidates by name"
    )
    args = parser.parse_args(argv)

    api_key = os.environ.get("NARE_EVAL_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    root = os.environ.get("NARE_BASE_URL", "")
    if not api_key or not root:
        print("judge_eval: needs NARE_BASE_URL and a key in the environment")
        return 2
    repo = Path(__file__).resolve().parent.parent
    cases = {c.id: c for c in load_cases(repo / "benchmarks" / "cases")}
    candidates = [
        c
        for c in CANDIDATES
        if c.model and (not args.candidate or c.name in args.candidate)
    ]

    items: list[Item] = [
        *reference_items(cases, capture_reference(cases)),
    ]
    for results in args.results:
        items += generated_items(Path(results), cases, {})
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    def progress(message: str) -> None:
        print(message, flush=True)

    calls = asyncio.run(
        run_eval(
            items,
            candidates,
            root=root,
            api_key=api_key,
            repeats=args.repeats,
            progress=progress,
        )
    )
    write_calls(calls, out / "calls.jsonl")
    rows = summarise(items, calls, candidates)
    (out / "summary.json").write_text(
        json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    table = render(rows)
    (out / "summary.md").write_text(table + "\n", encoding="utf-8")
    print(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
