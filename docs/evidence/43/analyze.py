"""Issue 43: summarize a compaction-point sweep on context-overflow-small-window.

usage: analyze.py RESULTS_DIR OUT_DIR RESULTS_JSONL [RESULTS_JSONL ...]

RESULTS_DIR is the benchmarks/results directory the artifacts paths are relative to.

Reads the bench results lines (only rows whose run args name --compaction-trigger
on this case), reads each kept rep's session.json for the findings the model wrote,
and writes reps.csv (one row per rep), cells.csv (one row per model and trigger),
and summary.md. Run it with -I: it reads files the benchmark wrote.
"""

from __future__ import annotations

import csv
import json
import math
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

CASE = "context-overflow-small-window"
EXPECTED = [f"SENTINEL_{n}=confirmed" for n in range(1, 9)]


def wilson(passes: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = passes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def hyper_pmf(a: int, r1: int, c1: int, n: int) -> float:
    # P(X = a) for 2x2 with row1 total r1, column1 total c1, grand total n.
    return math.comb(c1, a) * math.comb(n - c1, r1 - a) / math.comb(n, r1)


def fisher_two_sided(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact p for [[a, b], [c, d]]."""
    r1, c1, n = a + b, a + c, a + b + c + d
    lo, hi = max(0, c1 - (n - r1)), min(r1, c1)
    observed = hyper_pmf(a, r1, c1, n)
    total = 0.0
    for x in range(lo, hi + 1):
        p = hyper_pmf(x, r1, c1, n)
        if p <= observed * (1 + 1e-9):
            total += p
    return min(1.0, total)


def trigger_of(args: list[str]) -> tuple[float | None, float | None]:
    values = {}
    for flag, key in (("--compaction-trigger", "trigger"), ("--compaction-target", "target")):
        if flag in args:
            values[key] = float(args[args.index(flag) + 1])
    return values.get("trigger"), values.get("target")


def read_session(artifact: Path) -> dict:
    """The findings the model wrote, the reads it made and the elided results."""
    session = json.loads((artifact / "session.json").read_text(encoding="utf-8"))
    archived = [m for s in session.get("summaries") or [] for m in s.get("replaced") or []]
    messages = archived + session["messages"]
    calls, results = [], {}
    for message in messages:
        for block in message.get("content", []):
            if block.get("type") == "tool_use":
                calls.append(block)
            elif block.get("type") == "tool_result":
                results[block.get("tool_use_id")] = str(block.get("content", ""))
    reads = []
    findings = None
    for call in calls:
        command = str(call.get("input", {}).get("command", ""))
        match = re.fullmatch(r"python3?\s+(?:\S*/)?log\.py\s+(\d+)", command.strip())
        if call.get("name") == "bash" and match:
            reads.append(int(match.group(1)))
        if call.get("name") == "write" and "findings.json" in str(call.get("input", {}).get("path", "")):
            findings = call["input"].get("content")
    elided = sum(1 for text in results.values() if text.startswith("[elided by nare:"))
    parsed = None
    if isinstance(findings, str):
        try:
            parsed = json.loads(findings)
        except json.JSONDecodeError:
            parsed = None
    return {"reads": reads, "findings": parsed, "elided_results": elided}


def score(findings) -> tuple[int, int, int]:
    """(correct in position, fabricated, missing) against the eight sentinels."""
    if not isinstance(findings, list):
        return (0, 0, len(EXPECTED))
    correct = sum(1 for i, want in enumerate(EXPECTED) if i < len(findings) and findings[i] == want)
    fabricated = sum(1 for item in findings if item not in EXPECTED)
    return (correct, fabricated, len(EXPECTED) - correct)


def main() -> None:
    global RESULTS_ROOT
    RESULTS_ROOT = Path(sys.argv[1])
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for path in sys.argv[3:]:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("case") != CASE:
                continue
            trigger, target = trigger_of(rec.get("nare_args") or [])
            if trigger is None:
                continue
            artifact = RESULTS_ROOT / rec["artifacts"]
            try:
                info = read_session(artifact)
            except (OSError, json.JSONDecodeError, KeyError) as exc:
                info = {"reads": [], "findings": None, "elided_results": 0, "error": str(exc)}
            correct, fabricated, missing = score(info["findings"])
            usage = rec.get("usage", {})
            tokens = sum(usage.get(k, 0) or 0 for k in ("input", "output", "cache_read", "cache_write"))
            rows.append(
                {
                    "model": rec["model"],
                    "trigger": trigger,
                    "target": target,
                    "rep": rec["rep"],
                    "started": rec.get("started"),
                    "outcome": rec.get("outcome"),
                    "error_cause": rec.get("error_cause") or "",
                    "passed": 1 if rec.get("outcome") == "pass" else 0,
                    "turns": rec.get("turns"),
                    "compactions": rec.get("compactions"),
                    "elided_results": info["elided_results"],
                    "reads": len(info["reads"]),
                    "findings_written": 1 if info["findings"] is not None else 0,
                    "correct": correct,
                    "fabricated": fabricated,
                    "missing": missing,
                    "tokens": tokens,
                    "cost": rec.get("cost"),
                    "cost_estimated": rec.get("cost_estimated"),
                    "stop_reason": rec.get("stop_reason"),
                    "nare_commit": rec.get("nare_commit"),
                    "case_hash": rec.get("case_hash"),
                    "artifacts": rec.get("artifacts"),
                }
            )
    # Unique by model, trigger, rep, started (a rep appears once per run file).
    seen, unique = set(), []
    for r in rows:
        key = (r["model"], r["trigger"], r["started"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)
    rows = sorted(unique, key=lambda r: (r["model"], r["trigger"], r["started"] or ""))

    with (out / "reps.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    cells = defaultdict(list)
    for r in rows:
        cells[(r["model"], r["trigger"])].append(r)
    cell_rows = []
    default_cells = {}
    for (model, trigger), reps in sorted(cells.items()):
        scored = [r for r in reps if not r["error_cause"]]
        n = len(scored)
        passes = sum(r["passed"] for r in scored)
        lo, hi = wilson(passes, n)
        costs = [r["cost"] for r in scored if r["cost"] is not None]
        cell = {
            "model": model,
            "trigger": trigger,
            "target": reps[0]["target"],
            "n": n,
            "errors": len(reps) - n,
            "passes": passes,
            "rate": passes / n if n else float("nan"),
            "ci_low": lo,
            "ci_high": hi,
            "tokens_median": statistics.median(r["tokens"] for r in scored) if scored else None,
            "cost_median": statistics.median(costs) if costs else None,
            "cost_total": sum(costs) if costs else None,
            "compactions_median": statistics.median(r["compactions"] or 0 for r in scored) if scored else None,
            "elided_median": statistics.median(r["elided_results"] for r in scored) if scored else None,
            "reads_median": statistics.median(r["reads"] for r in scored) if scored else None,
            "correct_mean": statistics.mean(r["correct"] for r in scored) if scored else None,
            "fabricated_total": sum(r["fabricated"] for r in scored),
            "findings_missing": sum(1 for r in scored if not r["findings_written"]),
        }
        cell_rows.append(cell)
        if abs(trigger - 0.8) < 1e-9:
            default_cells[model] = (cell, scored)

    for cell in cell_rows:
        base = default_cells.get(cell["model"])
        if base is None or cell["n"] == 0:
            cell["p_vs_default"] = None
            continue
        ref, _ = base
        cell["p_vs_default"] = fisher_two_sided(
            cell["passes"], cell["n"] - cell["passes"], ref["passes"], ref["n"] - ref["passes"]
        )

    with (out / "cells.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(cell_rows[0].keys()))
        writer.writeheader()
        writer.writerows(cell_rows)

    lines = ["| model | trigger | target | n | pass | rate [95% Wilson] | Fisher p vs 0.8 | tokens med | cost/rep med | compactions med | elided med | reads med | fabricated | correct mean |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in cell_rows:
        p = "" if c["p_vs_default"] is None else f"{c['p_vs_default']:.3f}"
        cost = "" if c["cost_median"] is None else f"${c['cost_median']:.4f}"
        lines.append(
            f"| {c['model']} | {c['trigger']:g} | {c['target']:g} | {c['n']} | {c['passes']} | "
            f"{c['rate']:.2f} [{c['ci_low']:.2f}, {c['ci_high']:.2f}] | {p} | "
            f"{(c['tokens_median'] or 0):,.0f} | {cost} | {c['compactions_median']} | "
            f"{c['elided_median']} | {c['reads_median']} | {c['fabricated_total']} | "
            f"{(c['correct_mean'] or 0):.2f} |"
        )
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    total = sum((r["cost"] or 0) for r in rows)
    print(f"\nreps: {len(rows)}  total reported cost: ${total:.4f}")


if __name__ == "__main__":
    main()
