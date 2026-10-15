from __future__ import annotations

import pytest

from benchmarks.runner.config import ConfigError, resolve_config


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
        env={"ANTHROPIC_API_KEY": "k", "NARE_BENCH_MAX_TOKENS": "32000"},
    )
    assert config.max_tokens == 32000


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
