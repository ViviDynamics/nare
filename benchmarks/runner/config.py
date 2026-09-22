"""Everything the run supplies and a case deliberately does not.

Cases name a task; the environment names the model. Reusing nare's own
variable names means an operator who can already run `nare run` against their
endpoint can run the benchmark with no further configuration.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


class ConfigError(Exception):
    """Configuration that cannot produce a run. Always exit 2."""


@dataclass(frozen=True)
class Config:
    model: str
    provider: str
    base_url: str | None
    judge_model: str
    api_key: str


def resolve_config(
    *,
    model: str | None,
    provider: str | None,
    base_url: str | None,
    judge_model: str | None,
    env: Mapping[str, str],
) -> Config:
    """Flags win, environment fills the gaps, and two values are mandatory."""
    resolved_model = model or env.get("NARE_MODEL")
    if not resolved_model:
        raise ConfigError("no model: pass --model or set NARE_MODEL")
    api_key = env.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ConfigError("ANTHROPIC_API_KEY is not set")
    return Config(
        model=resolved_model,
        provider=provider or env.get("NARE_PROVIDER") or "anthropic",
        base_url=base_url or env.get("NARE_BASE_URL") or None,
        # The judge defaults to the model under test because that is the one
        # model the operator is known to have access to.
        judge_model=(
            judge_model or env.get("NARE_BENCH_JUDGE_MODEL") or resolved_model
        ),
        api_key=api_key,
    )
