import argparse

import pytest

from nare.cli import build_parser, transport_from_args
from nare.transport.anthropic import AnthropicTransport


def parse(*argv: str) -> argparse.Namespace:
    return build_parser().parse_args(["run", *argv])


def test_defaults_match_the_spec() -> None:
    args = parse("do a thing")
    assert args.prompt == "do a thing"
    assert args.provider == "anthropic"
    assert args.model == "claude-sonnet-5"
    assert args.base_url is None
    assert args.temperature is None
    assert args.max_tokens is None  # resolved to 8192 inside the transport
    assert args.effort is None
    assert args.system is None
    assert args.max_turns == 50
    assert args.jsonl is False
    assert args.yes is False


def test_environment_supplies_the_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NARE_MODEL", "claude-opus-5")
    monkeypatch.setenv("NARE_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("NARE_PROVIDER", "anthropic")
    args = parse("go")
    assert args.model == "claude-opus-5"
    assert args.base_url == "http://localhost:11434"


def test_a_flag_beats_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NARE_MODEL", "claude-opus-5")
    assert parse("go", "--model", "claude-sonnet-5").model == "claude-sonnet-5"


def test_there_is_no_api_key_flag() -> None:
    with pytest.raises(SystemExit):
        parse("go", "--api-key", "sk-ant-nope")


def test_an_unknown_provider_flag_is_rejected_by_argparse() -> None:
    with pytest.raises(SystemExit):
        parse("go", "--provider", "openai")


def test_transport_from_args_binds_every_knob(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    args = parse("go", "--model", "m", "--max-tokens", "512", "--system", "persona")
    transport = transport_from_args(args)
    assert isinstance(transport, AnthropicTransport)
    assert (transport.model, transport.max_tokens) == ("m", 512)
    assert transport.system == "persona"


def test_temperature_is_refused_by_the_anthropic_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with pytest.raises(ValueError, match="temperature"):
        transport_from_args(parse("go", "--temperature", "0.2"))


def test_an_unknown_provider_from_the_environment_fails_at_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NARE_PROVIDER", "openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with pytest.raises(ValueError, match="anthropic"):
        transport_from_args(parse("go"))
