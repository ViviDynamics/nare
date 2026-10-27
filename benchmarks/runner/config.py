"""Everything the run supplies and a case deliberately does not.

Cases name a task; the environment names the model. Reusing nare's own
variable names means an operator who can already run `nare run` against their
endpoint can run the benchmark with no further configuration.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from nare.cli import stream_from_env
from nare.transport.anthropic import nonstreaming_ceiling


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
    # The agent's --max-tokens, or None for nare's own default. A reasoning
    # model can spend the default 8192 thinking and never reach a tool call.
    max_tokens: int | None = None
    # NARE_STREAM, read as `nare run` reads it. The agent gets the variable
    # itself; the judge streams on this. Through a proxy, a reasoning model's
    # non-streamed reply arrived with no content at all.
    stream: bool = False


def _max_tokens_from(
    env: Mapping[str, str], provider: str, model: str, stream: bool
) -> int | None:
    raw = env.get("NARE_BENCH_MAX_TOKENS")
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value <= 0:
        raise ConfigError(f"NARE_BENCH_MAX_TOKENS must be a positive integer: {raw!r}")
    _check_ceiling("NARE_BENCH_MAX_TOKENS", value, provider, model, stream)
    return value


def _check_ceiling(
    source: str, value: int, provider: str, model: str, stream: bool
) -> None:
    # nare refuses this at startup, so every rep would end with no result
    # line: a paid run that measures nothing.
    if provider == "anthropic" and not stream:
        ceiling = nonstreaming_ceiling(model)
        if value > ceiling:
            raise ConfigError(
                f"{source} {value} is above {ceiling}, the most "
                f"{model!r} takes without streaming; set NARE_STREAM=1"
            )


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
    told to go find an API key before it can print a comparison. They never
    run the agent either, so its output cap is not read.
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
    stream = stream_from_env(env)
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
        max_tokens=(
            _max_tokens_from(env, resolved_provider, resolved_model, stream)
            if need_key
            else None
        ),
        stream=stream,
    )


def route(base_url: str | None, provider: str) -> str | None:
    """One proxy, two routes: Chat Completions under /v1, Messages at its root.

    The openai transport posts to <base>/chat/completions, so its base ends in
    /v1. The Anthropic SDK adds /v1/messages itself, so its base does not.
    """
    if not base_url:
        return None
    root = base_url.rstrip("/").removesuffix("/v1")
    return f"{root}/v1" if provider == "openai" else root


def baseline_config(
    *,
    model: str,
    provider: str,
    judge_model: str,
    max_tokens: int | None,
    base_url: str | None,
    env: Mapping[str, str],
) -> Config:
    """One matrix model's config: the model, provider, judge and cap its
    baseline names, with the endpoint, key and streaming from the run.

    NARE_BENCH_MAX_TOKENS is not read, since the cap is the baseline's, but
    the cap is still held to the ceiling nare enforces without streaming. A
    baseline with no model or judge is refused, where resolve_config would
    take NARE_MODEL or NARE_BENCH_JUDGE_MODEL from the shell.
    """
    for name, value in (("model", model), ("judge_model", judge_model)):
        if not value:
            raise ConfigError(
                f"the baseline names no {name}, and the matrix never takes one "
                "from the shell"
            )
    if provider not in KEY_VARS:
        raise ConfigError(
            f"provider {provider!r} is not one nare supports: " + ", ".join(KEY_VARS)
        )
    config = resolve_config(
        model=model,
        provider=provider,
        base_url=route(base_url, provider),
        judge_model=judge_model,
        env={k: v for k, v in env.items() if k != "NARE_BENCH_MAX_TOKENS"},
    )
    if max_tokens is not None:
        _check_ceiling(
            "the baseline's max_tokens", max_tokens, provider, model, config.stream
        )
    return replace(config, max_tokens=max_tokens)
