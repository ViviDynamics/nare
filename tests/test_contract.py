"""The machine contract: one version, documented exit codes, a stated refusal."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fake_provider import FakeProvider, text_reply, tool_reply
from nare.cli import main
from nare.contract import CONTRACT_VERSION, EXIT_CODES, describe
from nare.session import loads


def test_every_terminal_status_has_an_exit_code() -> None:
    assert set(EXIT_CODES) == {"done", "blocked", "error"}


def test_the_description_carries_the_version_and_the_exit_codes() -> None:
    described = describe()

    assert described["contract"] == CONTRACT_VERSION
    assert described["exit_codes"] == dict(EXIT_CODES)
    assert "never_started" in described


def test_the_contract_subcommand_prints_it(capsys: Any) -> None:
    code = main(["contract"])

    printed = json.loads(capsys.readouterr().out)
    assert code == 0
    assert printed["contract"] == CONTRACT_VERSION
    assert printed["event_types"] == list(describe()["event_types"])


def test_a_run_refuses_a_contract_it_does_not_speak(capsys: Any) -> None:
    code = main(["run", "go", "--yes", "--contract", "99"])

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "99" in captured.err and str(CONTRACT_VERSION) in captured.err


def test_a_run_accepts_the_contract_it_speaks(capsys: Any) -> None:
    code = main(
        ["run", "go", "--yes", "--jsonl", "--contract", str(CONTRACT_VERSION)],
        transport=FakeProvider([text_reply("done")]),
    )

    capsys.readouterr()
    assert code == 0


def test_the_result_line_carries_the_contract_version(capsys: Any) -> None:
    main(
        ["run", "go", "--yes", "--jsonl"],
        transport=FakeProvider([text_reply("done")]),
    )

    last = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert last["contract"] == CONTRACT_VERSION


def test_a_completed_run_exits_zero(capsys: Any) -> None:
    code = main(["run", "go", "--yes"], transport=FakeProvider([text_reply("done")]))

    capsys.readouterr()
    assert code == EXIT_CODES["done"] == 0


def test_a_blocked_run_exits_zero(capsys: Any) -> None:
    code = main(
        ["run", "go", "--yes"],
        transport=FakeProvider([tool_reply("ask", {"questions": ["which?"]})]),
    )

    capsys.readouterr()
    assert code == EXIT_CODES["blocked"] == 0


def test_a_failed_run_exits_one(capsys: Any) -> None:
    code = main(
        ["run", "go", "--yes"],
        transport=FakeProvider([text_reply("cut off", stop_reason="max_tokens")]),
    )

    capsys.readouterr()
    assert code == EXIT_CODES["error"] == 1


def test_the_session_file_carries_the_contract(tmp_path: Path, capsys: Any) -> None:
    session_path = tmp_path / "s.json"

    main(
        ["run", "go", "--yes", "--session", str(session_path)],
        transport=FakeProvider([text_reply("done")]),
    )

    capsys.readouterr()
    assert json.loads(session_path.read_text(encoding="utf-8"))["contract"] == (
        CONTRACT_VERSION
    )


def test_a_session_from_another_contract_is_refused(tmp_path: Path) -> None:
    session_path = tmp_path / "s.json"
    main(
        ["run", "go", "--yes", "--session", str(session_path)],
        transport=FakeProvider([text_reply("done")]),
    )
    raw = json.loads(session_path.read_text(encoding="utf-8"))
    raw["contract"] = CONTRACT_VERSION + 1
    session_path.write_text(json.dumps(raw), encoding="utf-8")

    try:
        loads(session_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        assert "contract" in str(exc)
    else:  # pragma: no cover - the refusal is the point
        raise AssertionError("a session from another contract must be refused")


def test_the_documented_contract_matches_the_code() -> None:
    doc = Path(__file__).parent.parent / "docs" / "contract.md"
    text = doc.read_text(encoding="utf-8")

    for status, code in EXIT_CODES.items():
        assert status in text
        assert f"exit {code}" in text
    for event_type in describe()["event_types"]:
        assert event_type in text
    assert f"contract version {CONTRACT_VERSION}" in text
