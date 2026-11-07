"""Paired comparison of two nare commits, pooled across cases. Pure arithmetic.

Each case is a stratum with a 2x2 table: arm (head or base) by outcome (pass
or not). Pooling across strata is what gives the test its power: a harness
change moves every case a little, and no single case shows it. The statistics
are the standard stratified ones, and the only randomness is a seeded
bootstrap over cases and a seeded simulation for the power table.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

Z95 = 1.959963984540054
ONE_SIDED_05 = 1.6448536269514722


@dataclass(frozen=True)
class CaseAB:
    """One stratum: a case's pass counts per arm, and its token and cost medians.

    `n` counts the decided reps of an arm (passes, fails, and judge failures,
    as `summarize` counts them). Errors never enter.
    """

    case: str
    head_pass: int
    head_n: int
    base_pass: int
    base_n: int
    head_tokens: int | None = None
    base_tokens: int | None = None
    head_cost: float | None = None
    base_cost: float | None = None
    head_cost_reps: int = 0
    base_cost_reps: int = 0

    @property
    def usable(self) -> bool:
        return self.head_n > 0 and self.base_n > 0


@dataclass(frozen=True)
class Mantel:
    """Mantel-Haenszel common odds ratio of head over base, with its test."""

    odds_ratio: float | None
    low: float | None
    high: float | None
    chi_square: float | None
    p_value: float | None
    strata: int


def _tables(strata: Sequence[CaseAB]) -> list[tuple[float, float, float, float]]:
    """Per usable stratum: (a, b, c, d) = head pass, head fail, base pass, base fail."""
    rows = []
    for s in strata:
        if not s.usable:
            continue
        rows.append(
            (
                float(s.head_pass),
                float(s.head_n - s.head_pass),
                float(s.base_pass),
                float(s.base_n - s.base_pass),
            )
        )
    return rows


def mantel_haenszel(strata: Sequence[CaseAB]) -> Mantel:
    """The pooled odds ratio with a Robins-Breslow-Greenland interval, and the
    Cochran-Mantel-Haenszel test of no association, with continuity correction.

    The odds ratio is above 1 when head passes more often than base. Where a
    stratum or a sum is empty the estimate is undefined and reads as None.
    """
    tables = _tables(strata)
    if not tables:
        return Mantel(None, None, None, None, None, 0)
    num = den = 0.0
    p_r = q_s = p_s = q_r = r_sum = s_sum = 0.0
    chi_num = chi_var = 0.0
    for a, b, c, d in tables:
        n = a + b + c + d
        num += a * d / n
        den += b * c / n
        pr, qs = (a + d) / n, (b + c) / n
        ri, si = a * d / n, b * c / n
        p_r += pr * ri
        q_s += qs * si
        p_s += pr * si
        q_r += qs * ri
        r_sum += ri
        s_sum += si
        # Expected head passes under no association, and its variance.
        head_n, base_n = a + b, c + d
        passes, fails = a + c, b + d
        expected = head_n * passes / n
        chi_num += a - expected
        if n > 1:
            chi_var += head_n * base_n * passes * fails / (n * n * (n - 1))
    chi = None
    p_value = None
    if chi_var > 0:
        chi = (max(abs(chi_num) - 0.5, 0.0)) ** 2 / chi_var
        p_value = math.erfc(math.sqrt(chi / 2))
    if den == 0 or num == 0 or r_sum == 0 or s_sum == 0:
        return Mantel(None, None, None, chi, p_value, len(tables))
    odds = num / den
    var = (
        p_r / (2 * r_sum * r_sum)
        + (p_s + q_r) / (2 * r_sum * s_sum)
        + q_s / (2 * s_sum * s_sum)
    )
    spread = Z95 * math.sqrt(var)
    return Mantel(
        odds_ratio=odds,
        low=math.exp(math.log(odds) - spread),
        high=math.exp(math.log(odds) + spread),
        chi_square=chi,
        p_value=p_value,
        strata=len(tables),
    )


def _bootstrap_mean(
    values: Sequence[float], seed: int, resamples: int
) -> tuple[float, float, float]:
    """The mean, and its 95% percentile interval over resampled values."""
    rng = random.Random(seed)
    n = len(values)
    means = sorted(
        sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(resamples)
    )
    mean = sum(values) / n
    return mean, means[int(0.025 * resamples)], means[int(0.975 * resamples) - 1]


def pass_rate_effect(
    strata: Sequence[CaseAB], *, seed: int = 153, resamples: int = 4000
) -> tuple[float, float, float] | None:
    """The head-minus-base pass rate, averaged over cases with each case weighted
    equally, with a bootstrap interval over cases. None with no usable case."""
    diffs = [
        s.head_pass / s.head_n - s.base_pass / s.base_n for s in strata if s.usable
    ]
    if not diffs:
        return None
    return _bootstrap_mean(diffs, seed, resamples)


def paired_ratio(
    pairs: Sequence[tuple[float, float]], *, seed: int = 153, resamples: int = 4000
) -> tuple[float, float, float] | None:
    """The geometric mean of head over base across cases, with a bootstrap
    interval over cases. Each pair is (head, base), both positive."""
    logs = [math.log(h / b) for h, b in pairs if h > 0 and b > 0]
    if not logs:
        return None
    mean, low, high = _bootstrap_mean(logs, seed, resamples)
    return math.exp(mean), math.exp(low), math.exp(high)


def token_pairs(strata: Sequence[CaseAB]) -> list[tuple[float, float]]:
    return [
        (float(s.head_tokens), float(s.base_tokens))
        for s in strata
        if s.head_tokens and s.base_tokens
    ]


def dollars_per_rep_pairs(strata: Sequence[CaseAB]) -> list[tuple[float, float]]:
    """Per case, the head and base dollars per rep, where every rep reported a cost."""
    out = []
    for s in strata:
        if s.head_cost is None or s.base_cost is None:
            continue
        if not s.head_cost_reps or not s.base_cost_reps:
            continue
        out.append((s.head_cost / s.head_cost_reps, s.base_cost / s.base_cost_reps))
    return out


def power(
    reps_per_arm: int,
    base_rates: Sequence[float],
    drop: float,
    *,
    trials: int = 4000,
    seed: int = 153,
) -> float:
    """The chance the one-sided 5% test flags a head arm worse than base.

    Each stratum is a case at its base rate; the head arm runs `drop` lower
    (never below 0). Each arm is `reps_per_arm` reps per case. The test is the
    stratified statistic above, so the power table in the docs is reproducible.
    """
    rng = random.Random(seed)
    hits = 0
    for _ in range(trials):
        diff = var = 0.0
        for base in base_rates:
            head = max(0.0, base - drop)
            base_pass = sum(rng.random() < base for _ in range(reps_per_arm))
            head_pass = sum(rng.random() < head for _ in range(reps_per_arm))
            n = 2 * reps_per_arm
            passes = base_pass + head_pass
            expected = reps_per_arm * passes / n
            diff += head_pass - expected
            var += (
                reps_per_arm * reps_per_arm * passes * (n - passes) / (n * n * (n - 1))
            )
        if var > 0 and diff / math.sqrt(var) < -ONE_SIDED_05:
            hits += 1
    return hits / trials


def render(
    *,
    base_label: str,
    head_label: str,
    reps: int,
    how: str,
    strata: Sequence[CaseAB],
    skipped: Sequence[str],
    mantel: Mantel,
    effect: tuple[float, float, float] | None,
    tokens: tuple[float, float, float] | None,
    dollars: tuple[float, float, float] | None,
) -> str:
    """The comparison as text: the per-case counts, then the pooled figures."""
    lines = [
        f"A/B: head {head_label} against base {base_label}",
        f"cases: {len(strata)} ({how}); {reps} reps per arm per case",
    ]
    if skipped:
        lines.append(f"skipped, an arm was mostly errors: {', '.join(skipped)}")
    lines.append("")
    header = f"{'case':<26} {'base':>9} {'head':>9}"
    lines += [header, "-" * len(header)]
    for s in strata:
        lines.append(
            f"{s.case:<26} {f'{s.base_pass}/{s.base_n}':>9} "
            f"{f'{s.head_pass}/{s.head_n}':>9}"
        )
    lines.append("")
    if effect is not None:
        mean, low, high = effect
        lines.append(
            f"pass rate, head minus base (cases weighted equally): "
            f"{mean:+.1%} [{low:+.1%}, {high:+.1%}]"
        )
    if (
        mantel.odds_ratio is not None
        and mantel.low is not None
        and mantel.high is not None
    ):
        lines.append(
            f"Mantel-Haenszel odds ratio, head over base: {mantel.odds_ratio:.2f} "
            f"[{mantel.low:.2f}, {mantel.high:.2f}]"
        )
    if mantel.p_value is not None and mantel.chi_square is not None:
        lines.append(
            f"Cochran-Mantel-Haenszel test: chi2 {mantel.chi_square:.2f}, "
            f"two-sided p = {mantel.p_value:.3f}"
        )
        if mantel.p_value < 0.05 and effect is not None:
            direction = "head passes less" if effect[0] < 0 else "head passes more"
            lines.append(f"at 5%: {direction}")
        else:
            lines.append("at 5%: no detectable pass-rate difference")
    for label, ratio in (("tokens", tokens), ("dollars per rep", dollars)):
        if ratio is not None:
            value, low, high = ratio
            lines.append(
                f"{label}, head over base (geometric mean across cases): "
                f"{value:.2f}x [{low:.2f}, {high:.2f}]"
            )
    lines.append(
        "intervals resample cases; with few cases they are coarse, and the "
        "test is the authority on the pass rate"
    )
    return "\n".join(lines)
