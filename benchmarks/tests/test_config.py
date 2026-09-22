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
