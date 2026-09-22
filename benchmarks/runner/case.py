"""Loading and validating cases. Pure: no Docker, no network, no clock.

A case is data. It describes a task and how to check it, and says nothing
about where it runs -- the model, the provider and the endpoint are the run's
business. `load_case` enforces that rather than trusting it.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

Tier = Literal["smoke", "full"]
CheckKind = Literal["bash", "result"]

# Fields that would pin a case to one operator's environment. Rejected by
# name so the failure explains itself instead of being silently ignored.
_ENVIRONMENT_FIELDS = ("model", "provider", "base_url")


class CaseError(Exception):
    """A case that cannot be run. Always exit 2; never a task failure."""


@dataclass(frozen=True)
class TierDefaults:
    reps: int
    timeout: int


TIER_DEFAULTS: dict[str, TierDefaults] = {
    "smoke": TierDefaults(reps=3, timeout=600),
    "full": TierDefaults(reps=5, timeout=900),
}


@dataclass(frozen=True)
class Check:
    kind: CheckKind
    cmd: str | None = None
    status: str | None = None
    max_turns: int | None = None


@dataclass(frozen=True)
class Judge:
    assertions: tuple[str, ...]
    min_met: int | None = None


@dataclass(frozen=True)
class Case:
    id: str
    tier: Tier
    prompt: str
    directory: Path
    max_turns: int
    reps: int
    timeout: int
    checks: tuple[Check, ...]
    judge: Judge | None

    @property
    def fixture(self) -> Path:
        return self.directory / "fixture"

    @property
    def bash_checks(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.kind == "bash")


def _require_str(raw: dict[str, Any], key: str, where: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise CaseError(f"{where}: {key} is required and must be a non-empty string")
    return value


def _load_check(raw: dict[str, Any], where: str) -> Check:
    kind = raw.get("kind")
    if kind == "bash":
        cmd = raw.get("cmd")
        if not isinstance(cmd, str) or not cmd:
            raise CaseError(f"{where}: a bash check needs a non-empty cmd")
        return Check(kind="bash", cmd=cmd)
    if kind == "result":
        status = raw.get("status")
        max_turns = raw.get("max_turns")
        if status is None and max_turns is None:
            raise CaseError(f"{where}: a result check needs status, max_turns, or both")
        if status is not None and not isinstance(status, str):
            raise CaseError(f"{where}: result check status must be a string")
        if max_turns is not None and not isinstance(max_turns, int):
            raise CaseError(f"{where}: result check max_turns must be an integer")
        return Check(kind="result", status=status, max_turns=max_turns)
    raise CaseError(f"{where}: unknown check kind {kind!r}; expected bash or result")


def _load_judge(raw: dict[str, Any], where: str) -> Judge:
    assertions = raw.get("assertions")
    if not isinstance(assertions, list) or not assertions:
        raise CaseError(f"{where}: judge assertions must be a non-empty list")
    if not all(isinstance(a, str) and a for a in assertions):
        raise CaseError(f"{where}: every judge assertion must be a non-empty string")
    min_met = raw.get("min_met")
    if min_met is not None:
        if not isinstance(min_met, int) or min_met < 1:
            raise CaseError(f"{where}: min_met must be a positive integer")
        if min_met > len(assertions):
            raise CaseError(
                f"{where}: min_met {min_met} exceeds {len(assertions)} assertions"
            )
    return Judge(assertions=tuple(assertions), min_met=min_met)


def _refuse_environment_fields(node: Any, where: str) -> None:
    """Refuse a model/provider/base_url key anywhere in the file.

    Nested counts: a `model` under `[judge]` is just as unportable as one at
    the top level, and is the easier of the two to write by accident.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _ENVIRONMENT_FIELDS:
                raise CaseError(
                    f"{where}: {key} belongs to the run, not the case. "
                    "Pass it with --model/--provider/--base-url or the NARE_* "
                    "variables so the case stays portable."
                )
            _refuse_environment_fields(value, where)
    elif isinstance(node, list):
        for item in node:
            _refuse_environment_fields(item, where)


def load_case(directory: Path) -> Case:
    """Read and validate one case directory."""
    path = directory / "case.toml"
    where = str(path)
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CaseError(f"{where}: no case.toml") from exc
    except tomllib.TOMLDecodeError as exc:
        raise CaseError(f"{where}: malformed TOML: {exc}") from exc

    _refuse_environment_fields(raw, where)

    # Absent and wrong are the same failure: the directory name is the
    # identity, and the field exists so a case file read alone says what it is.
    case_id = raw.get("id")
    if case_id != directory.name:
        raise CaseError(
            f"{where}: id {case_id!r} does not match its directory "
            f"{directory.name!r}; the directory name is the identity"
        )

    tier = _require_str(raw, "tier", where)
    if tier not in TIER_DEFAULTS:
        raise CaseError(
            f"{where}: tier {tier!r} is not one of {', '.join(TIER_DEFAULTS)}"
        )
    defaults = TIER_DEFAULTS[tier]

    prompt = _require_str(raw, "prompt", where)

    raw_checks = raw.get("check")
    if not isinstance(raw_checks, list) or not raw_checks:
        raise CaseError(f"{where}: a case needs at least one check")
    checks = tuple(_load_check(c, where) for c in raw_checks)

    raw_judge = raw.get("judge")
    judge = _load_judge(raw_judge, where) if raw_judge is not None else None

    if not (directory / "fixture").is_dir():
        raise CaseError(f"{where}: no fixture/ directory beside case.toml")

    return Case(
        id=case_id,
        tier=tier,  # type: ignore[arg-type]
        prompt=prompt,
        directory=directory,
        max_turns=int(raw.get("max_turns", 12)),
        # Resolved here so no later module repeats the fallback.
        reps=int(raw.get("reps", defaults.reps)),
        timeout=int(raw.get("timeout", defaults.timeout)),
        checks=checks,
        judge=judge,
    )


def load_cases(root: Path, tier: str | None = None) -> list[Case]:
    """Load every case under `root`, sorted by id, optionally filtered by tier."""
    if not root.is_dir():
        return []
    cases = [
        load_case(entry)
        for entry in sorted(root.iterdir())
        if entry.is_dir() and (entry / "case.toml").exists()
    ]
    # `full` is every case, not only the full-tagged ones: the tiers differ by
    # scale, so a smoke result predicts a full result. A case is tagged with
    # the smallest tier it belongs to.
    if tier is not None and tier != "full":
        cases = [c for c in cases if c.tier == tier]
    return cases
