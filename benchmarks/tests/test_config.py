from __future__ import annotations

import pytest

from benchmarks.runner.config import (
    Config,
    ConfigError,
    baseline_config,
    resolve_config,
    route,
)


def test_flags_win_over_environment() -> None:
    config = resolve_config(
        model="claude-haiku",
        provider=None,
        base_url=None,
        judge_model=None,
        env={"NARE_MODEL": "ignored", "ANTHROPIC_API_KEY": "k"},
    )
    assert config.model == "claude-haiku"
    assert config.provider == "anthropic"
    assert config.base_url is None


def test_environment_supplies_what_flags_omit() -> None:
    config = resolve_config(
        model=None,
        provider=None,
        base_url=None,
        judge_model=None,
        env={
            "NARE_MODEL": "gpt-5-nano",
            "NARE_PROVIDER": "anthropic",
            "NARE_BASE_URL": "https://proxy.example/v1",
            "ANTHROPIC_API_KEY": "k",
        },
    )
    assert config.model == "gpt-5-nano"
    assert config.base_url == "https://proxy.example/v1"


def test_judge_model_defaults_to_the_model_under_test() -> None:
    config = resolve_config(
        model="claude-haiku",
        provider=None,
        base_url=None,
        judge_model=None,
        env={"ANTHROPIC_API_KEY": "k"},
    )
    assert config.judge_model == "claude-haiku"


def test_judge_model_can_be_set_by_environment() -> None:
    config = resolve_config(
        model="claude-haiku",
        provider=None,
        base_url=None,
        judge_model=None,
        env={"ANTHROPIC_API_KEY": "k", "NARE_BENCH_JUDGE_MODEL": "gpt-5-nano"},
    )
    assert config.judge_model == "gpt-5-nano"


def test_the_agents_output_cap_can_be_set_by_environment() -> None:
    # A reasoning model spent nare's default 8192 output tokens thinking and
    # was cut off before its first tool call, which grades as the agent's fail.
    config = resolve_config(
        model="m",
        provider=None,
        base_url=None,
        judge_model=None,
        env={"ANTHROPIC_API_KEY": "k", "NARE_BENCH_MAX_TOKENS": "16000"},
    )
    assert config.max_tokens == 16000


def test_the_agents_output_cap_defaults_to_nares_own() -> None:
    config = resolve_config(
        model="m",
        provider=None,
        base_url=None,
        judge_model=None,
        env={"ANTHROPIC_API_KEY": "k"},
    )
    assert config.max_tokens is None


@pytest.mark.parametrize("value", ["0", "-5", "lots"])
def test_a_bad_output_cap_is_refused(value: str) -> None:
    with pytest.raises(ConfigError, match="NARE_BENCH_MAX_TOKENS"):
        resolve_config(
            model="m",
            provider=None,
            base_url=None,
            judge_model=None,
            env={"ANTHROPIC_API_KEY": "k", "NARE_BENCH_MAX_TOKENS": value},
        )


@pytest.mark.parametrize(
    ("value", "stream"),
    [("1", True), ("true", True), ("YES", True), ("0", False), ("no", False)],
)
def test_streaming_follows_nares_own_variable(value: str, stream: bool) -> None:
    # Read the way `nare run` reads it, so the agent and the judge agree.
    config = resolve_config(
        model="m",
        provider=None,
        base_url=None,
        judge_model=None,
        env={"ANTHROPIC_API_KEY": "k", "NARE_STREAM": value},
    )
    assert config.stream is stream


def test_a_cap_above_what_anthropic_takes_unstreamed_needs_streaming() -> None:
    # nare refuses it at startup without --stream, so every rep would end
    # with no result line and the whole paid run would measure nothing.
    env = {"ANTHROPIC_API_KEY": "k", "NARE_BENCH_MAX_TOKENS": "32000"}
    with pytest.raises(ConfigError, match="NARE_STREAM"):
        resolve_config(
            model="m", provider=None, base_url=None, judge_model=None, env=env
        )
    config = resolve_config(
        model="m",
        provider=None,
        base_url=None,
        judge_model=None,
        env={**env, "NARE_STREAM": "1"},
    )
    assert config.max_tokens == 32000


def test_the_unstreamed_limit_is_the_models_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import nare.transport.anthropic as anthropic

    monkeypatch.setattr(anthropic, "MODEL_NONSTREAMING_TOKENS", {"small": 8192})
    with pytest.raises(ConfigError, match="8192"):
        resolve_config(
            model="small",
            provider=None,
            base_url=None,
            judge_model=None,
            env={"ANTHROPIC_API_KEY": "k", "NARE_BENCH_MAX_TOKENS": "9000"},
        )


def test_openai_takes_any_cap_unstreamed() -> None:
    config = resolve_config(
        model="m",
        provider="openai",
        base_url=None,
        judge_model=None,
        env={"OPENAI_API_KEY": "k", "NARE_BENCH_MAX_TOKENS": "32000"},
    )
    assert config.max_tokens == 32000


def test_commands_that_never_run_the_agent_ignore_its_cap() -> None:
    # compare and bless read local files; a stale cap in the shell must not
    # stop them.
    config = resolve_config(
        model="m",
        provider=None,
        base_url=None,
        judge_model=None,
        env={"NARE_BENCH_MAX_TOKENS": "lots"},
        need_key=False,
    )
    assert config.max_tokens is None


def test_streaming_is_off_by_default() -> None:
    config = resolve_config(
        model="m",
        provider=None,
        base_url=None,
        judge_model=None,
        env={"ANTHROPIC_API_KEY": "k"},
    )
    assert config.stream is False


def test_missing_model_is_refused() -> None:
    with pytest.raises(ConfigError, match="model"):
        resolve_config(
            model=None,
            provider=None,
            base_url=None,
            judge_model=None,
            env={"ANTHROPIC_API_KEY": "k"},
        )


def test_missing_api_key_is_refused() -> None:
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        resolve_config(
            model="claude-haiku",
            provider=None,
            base_url=None,
            judge_model=None,
            env={},
        )


def test_the_required_key_follows_the_provider() -> None:
    config = resolve_config(
        model="gpt-5-nano",
        provider="openai",
        base_url=None,
        judge_model=None,
        env={"OPENAI_API_KEY": "k"},
    )
    assert config.api_key_var == "OPENAI_API_KEY"
    assert config.api_key == "k"


def test_an_openai_run_is_not_satisfied_by_the_anthropic_key() -> None:
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        resolve_config(
            model="gpt-5-nano",
            provider="openai",
            base_url=None,
            judge_model=None,
            env={"ANTHROPIC_API_KEY": "k"},
        )


def test_an_unknown_provider_is_refused() -> None:
    with pytest.raises(ConfigError, match="unknown provider"):
        resolve_config(
            model="m",
            provider="gemini",
            base_url=None,
            judge_model=None,
            env={"ANTHROPIC_API_KEY": "k"},
        )


def test_a_key_is_optional_for_the_subcommands_that_never_spend_one() -> None:
    config = resolve_config(
        model="claude-haiku",
        provider=None,
        base_url=None,
        judge_model=None,
        env={},
        need_key=False,
    )
    assert config.api_key is None


@pytest.mark.parametrize(
    ("base_url", "provider", "routed"),
    [
        ("https://proxy.example", "openai", "https://proxy.example/v1"),
        ("https://proxy.example/v1/", "openai", "https://proxy.example/v1"),
        ("https://proxy.example/v1", "anthropic", "https://proxy.example"),
        ("https://proxy.example/", "anthropic", "https://proxy.example"),
        (None, "openai", None),
    ],
)
def test_one_base_url_gives_each_provider_its_route(
    base_url: str | None, provider: str, routed: str | None
) -> None:
    # The openai transport posts to <base>/chat/completions; the Anthropic
    # SDK adds /v1/messages itself.
    assert route(base_url, provider) == routed


MATRIX_ENV = {
    "ANTHROPIC_API_KEY": "k",
    "OPENAI_API_KEY": "k",
    # What a shell set up for a single run holds. The baseline wins.
    "NARE_MODEL": "ada/qwen3-14b",
    "NARE_PROVIDER": "anthropic",
    "NARE_BENCH_JUDGE_MODEL": "ada/qwen3-14b",
    "NARE_BENCH_MAX_TOKENS": "16000",
}


def test_a_matrix_model_runs_as_its_baseline_names() -> None:
    config = baseline_config(
        model="gpt-5-nano",
        provider="openai",
        judge_model="claude-sonnet-5-5",
        max_tokens=None,
        base_url="https://proxy.example",
        env=MATRIX_ENV,
    )
    assert (config.model, config.provider, config.judge_model) == (
        "gpt-5-nano",
        "openai",
        "claude-sonnet-5-5",
    )
    assert config.base_url == "https://proxy.example/v1"
    assert config.api_key_var == "OPENAI_API_KEY"
    # The baseline's cap, which is nare's default, not the shell's 16000.
    assert config.max_tokens is None


def test_a_matrix_model_keeps_its_baselines_cap() -> None:
    config = baseline_config(
        model="spark/glm-5.3-flash",
        provider="openai",
        judge_model="j",
        max_tokens=32000,
        base_url=None,
        env=MATRIX_ENV,
    )
    assert config.max_tokens == 32000
    assert config.base_url is None


def test_a_matrix_model_needs_its_providers_key() -> None:
    env = {k: v for k, v in MATRIX_ENV.items() if k != "OPENAI_API_KEY"}
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        baseline_config(
            model="gpt-5-nano",
            provider="openai",
            judge_model="j",
            max_tokens=None,
            base_url=None,
            env=env,
        )


def test_a_matrix_cap_above_the_unstreamed_ceiling_needs_streaming() -> None:
    def config(env: dict[str, str]) -> Config:
        return baseline_config(
            model="claude-haiku-5-5",
            provider="anthropic",
            judge_model="j",
            max_tokens=32000,
            base_url=None,
            env=env,
        )

    with pytest.raises(ConfigError, match="NARE_STREAM"):
        config(MATRIX_ENV)
    assert config({**MATRIX_ENV, "NARE_STREAM": "1"}).max_tokens == 32000


def test_a_baseline_without_a_provider_is_refused() -> None:
    with pytest.raises(ConfigError, match="provider"):
        baseline_config(
            model="m",
            provider="",
            judge_model="j",
            max_tokens=None,
            base_url=None,
            env=MATRIX_ENV,
        )


@pytest.mark.parametrize(
    ("model", "judge_model", "missing"),
    [("", "j", "no model"), ("m", "", "no judge_model")],
)
def test_a_baseline_without_a_model_or_judge_is_refused(
    model: str, judge_model: str, missing: str
) -> None:
    # resolve_config would fill the gap from NARE_MODEL or
    # NARE_BENCH_JUDGE_MODEL, and the matrix never runs what the shell names.
    with pytest.raises(ConfigError, match=missing):
        baseline_config(
            model=model,
            provider="openai",
            judge_model=judge_model,
            max_tokens=None,
            base_url=None,
            env=MATRIX_ENV,
        )
