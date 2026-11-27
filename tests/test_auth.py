"""nare auth login|status|logout (#328): a key stored once, in
NARE_HOME/credentials.json, mode 0600, read after the environment.

The key never reaches argv, stdout, events, session files or bash.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from fake_provider import FakeProvider, text_reply, tool_reply
from nare import credentials, person
from nare.cli import main, parse_args, transport_from_args
from nare.transport.anthropic import AnthropicTransport
from nare.transport.openai import OpenAITransport

KEY = "sk-ant-api03-" + "K" * 40
OPENAI_KEY = "sk-proj-" + "Q" * 40


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "NARE_PROVIDER"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def home() -> Path:
    path = credentials.credentials_path()
    assert path is not None
    return path


def piped(monkeypatch: pytest.MonkeyPatch, data: str) -> None:
    read, write = os.pipe()
    os.write(write, data.encode())
    os.close(write)
    monkeypatch.setattr(sys, "stdin", os.fdopen(read, "r", encoding="utf-8"))


def stored(home: Path) -> dict[str, object]:
    loaded: dict[str, object] = json.loads(home.read_text())
    return loaded


@pytest.mark.parametrize("argv", [["auth", "login"], ["login"]])
def test_login_with_api_key_reads_stdin(
    argv: list[str],
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    piped(monkeypatch, KEY + "\n")
    assert main([*argv, "--with-api-key"]) == 0
    assert stored(home) == {"anthropic": {"api_key": KEY}}
    assert stat.S_IMODE(home.stat().st_mode) == 0o600
    assert stat.S_IMODE(home.parent.stat().st_mode) == 0o700
    captured = capsys.readouterr()
    assert KEY not in captured.out + captured.err
    assert captured.out == ""


def test_login_reads_hidden_input_at_a_terminal(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []

    def typed(prompt: str) -> str:
        asked.append(prompt)
        return OPENAI_KEY

    monkeypatch.setattr(credentials, "terminal", lambda: True)
    monkeypatch.setattr(credentials, "hidden_input", typed)
    assert main(["auth", "login", "--provider", "openai"]) == 0
    assert stored(home) == {"openai": {"api_key": OPENAI_KEY}}
    assert "openai" in asked[0].lower()


def test_login_without_a_terminal_never_prompts(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(credentials, "terminal", lambda: False)
    monkeypatch.setattr(
        credentials, "hidden_input", lambda prompt: pytest.fail("prompted")
    )
    assert main(["auth", "login"]) == 2
    assert "--with-api-key" in capsys.readouterr().err
    assert not home.exists()


def test_a_key_in_argv_is_refused(home: Path) -> None:
    with pytest.raises(SystemExit) as stopped:
        main(["auth", "login", KEY])
    assert stopped.value.code == 2
    assert not home.exists()


def test_an_empty_key_is_refused(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    piped(monkeypatch, "\n")
    assert main(["auth", "login", "--with-api-key"]) == 2
    assert not home.exists()


def test_bedrock_takes_no_stored_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    piped(monkeypatch, KEY)
    code = main(["auth", "login", "--provider", "bedrock", "--with-api-key"])
    assert code == 2
    assert "AWS" in capsys.readouterr().err


def test_logging_in_twice_keeps_the_other_provider(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    piped(monkeypatch, KEY)
    assert main(["auth", "login", "--with-api-key"]) == 0
    piped(monkeypatch, OPENAI_KEY)
    assert main(["auth", "login", "--provider", "openai", "--with-api-key"]) == 0
    assert stored(home) == {
        "anthropic": {"api_key": KEY},
        "openai": {"api_key": OPENAI_KEY},
    }


def test_status_with_nothing_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["auth", "status"]) == 1
    out = capsys.readouterr().out
    assert "anthropic" in out and "not set" in out


def test_status_names_the_stored_file_without_the_key(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    credentials.store_key("anthropic", KEY)
    assert main(["auth", "status"]) == 0
    out = capsys.readouterr().out
    assert str(home) in out
    assert KEY not in out


def test_status_prefers_the_environment(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    credentials.store_key("anthropic", KEY)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env")
    assert main(["auth", "status"]) == 0
    line = next(
        line for line in capsys.readouterr().out.splitlines() if "anthropic" in line
    )
    assert "ANTHROPIC_API_KEY" in line
    assert "from-env" not in line


def test_status_for_another_provider(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    credentials.store_key("anthropic", KEY)
    assert main(["auth", "status", "--provider", "openai"]) == 1
    monkeypatch.setenv("NARE_PROVIDER", "openai")
    assert main(["auth", "status"]) == 1
    monkeypatch.setenv("OPENAI_API_KEY", OPENAI_KEY)
    assert main(["auth", "status"]) == 0


def test_logout_removes_the_stored_key(home: Path) -> None:
    credentials.store_key("anthropic", KEY)
    credentials.store_key("openai", OPENAI_KEY)
    assert main(["auth", "logout"]) == 0
    assert stored(home) == {"openai": {"api_key": OPENAI_KEY}}
    assert main(["logout", "--provider", "openai"]) == 0
    assert stored(home) == {}
    assert main(["auth", "logout"]) == 0  # nothing left: still fine


def test_a_stored_key_reaches_the_transport() -> None:
    credentials.store_key("anthropic", KEY)
    transport = transport_from_args(parse_args(["run", "--yes", "x"]))
    assert isinstance(transport, AnthropicTransport)
    assert transport._client.api_key == KEY


def test_the_environment_beats_the_stored_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials.store_key("anthropic", KEY)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-" + "E" * 30)
    transport = transport_from_args(parse_args(["run", "--yes", "x"]))
    assert isinstance(transport, AnthropicTransport)
    assert transport._client.api_key == "sk-ant-env-" + "E" * 30


def test_a_stored_openai_key_reaches_the_transport() -> None:
    credentials.store_key("openai", OPENAI_KEY)
    args = parse_args(["run", "--yes", "--provider", "openai", "--model", "m", "x"])
    transport = transport_from_args(args)
    assert isinstance(transport, OpenAITransport)
    assert transport._key == OPENAI_KEY


def test_run_without_a_key_says_what_it_always_said(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["run", "--yes", "x"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("nare: no Anthropic API key: set ANTHROPIC_API_KEY")


def test_a_person_without_a_key_is_offered_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["-p", "x"]) == 2
    [line] = capsys.readouterr().err.splitlines()
    assert "nare auth login" in line and "ANTHROPIC_API_KEY" in line


def test_a_person_without_a_key_in_the_tui_is_offered_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(person, "tui_available", lambda: True)
    monkeypatch.setattr(person, "interactive_terminal", lambda: True)
    monkeypatch.setattr(
        "nare.tui.app.main", lambda args, transport=None: pytest.fail("started")
    )
    assert main(["do it"]) == 2
    assert "nare auth login" in capsys.readouterr().err


def test_the_key_stays_out_of_bash_events_and_the_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Even with --bash-credentials: a stored key is not in any environment."""
    monkeypatch.chdir(tmp_path)
    credentials.store_key("anthropic", KEY)
    session = tmp_path / "s.json"
    fake = FakeProvider([tool_reply("bash", {"command": "env"}), text_reply("ok")])
    argv = ["run", "--yes", "--jsonl", "--bash-credentials", "--session", str(session)]
    assert main([*argv, "x"], transport=fake) == 0
    captured = capsys.readouterr()
    assert KEY not in captured.out + captured.err
    assert KEY not in session.read_text()
    messages, _tools = fake.calls[1]
    assert KEY not in json.dumps([m.content for m in messages])


def test_a_malformed_file_is_named(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home.parent.mkdir(parents=True, exist_ok=True)
    home.write_text("{not json")
    assert main(["auth", "status"]) == 2
    assert str(home) in capsys.readouterr().err


def test_help_mentions_login(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--help"]) == 0
    assert "nare auth login" in capsys.readouterr().out
