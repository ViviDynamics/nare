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


# Each provider reads its own key, under the name nare's transport for it
# expects. The benchmark must require, and pass on, the one this run will use.
KEY_VARS = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}


@dataclass(frozen=True)
class Config:
    model: str
    provider: str
    base_url: str | None
    judge_model: str
    api_key: str | None
    api_key_var: str


def resolve_config(
    *,
    model: str | None,
    provider: str | None,
    base_url: str | None,
    judge_model: str | None,
    env: Mapping[str, str],
    need_key: bool = True,
) -> Config:
    """Flags win, environment fills the gaps, and two values are mandatory.

    `need_key` is False for the subcommands that only read local files.
    Demanding a credential they never spend is how a working checkout gets
    told to go find an API key before it can print a comparison.
    """
    resolved_model = model or env.get("NARE_MODEL")
    if not resolved_model:
        raise ConfigError("no model: pass --model or set NARE_MODEL")
    resolved_provider = provider or env.get("NARE_PROVIDER") or "anthropic"
    key_var = KEY_VARS.get(resolved_provider)
    if key_var is None:
        raise ConfigError(
            f"unknown provider {resolved_provider!r}; nare supports: "
            + ", ".join(KEY_VARS)
        )
    api_key = env.get(key_var)
    if need_key and not api_key:
        raise ConfigError(f"{key_var} is not set")
    return Config(
        model=resolved_model,
        provider=resolved_provider,
        base_url=base_url or env.get("NARE_BASE_URL") or None,
        # The judge defaults to the model under test because that is the one
        # model the operator is known to have access to.
        judge_model=(
            judge_model or env.get("NARE_BENCH_JUDGE_MODEL") or resolved_model
        ),
        api_key=api_key,
        api_key_var=key_var,
    )
