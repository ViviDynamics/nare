import argparse
import json
import re
from pathlib import Path

import pytest

from fake_provider import Exploding, FakeProvider, text_reply, tool_reply
from nare.cli import build_parser, main, transport_from_args
from nare.transport.anthropic import AnthropicTransport


def parse(*argv: str) -> argparse.Namespace:
    return build_parser().parse_args(["run", *argv])


def test_defaults_match_the_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    # The defaults are read from the environment, so this test has to state
    # what it means by "default". Without this, anyone with NARE_MODEL
    # exported -- which a .env for a LiteLLM proxy does -- fails here, and
    # the failure reads like a code defect rather than their own shell.
    for var in ("NARE_PROVIDER", "NARE_MODEL", "NARE_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
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


TIMESTAMP = re.compile(r'"timestamp": "[^"]+"')


def lines(captured: str) -> list[dict[str, object]]:
    return [
        json.loads(TIMESTAMP.sub('"timestamp": "T"', line))
        for line in captured.strip().splitlines()
    ]


def test_run_refuses_to_start_without_yes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", "go"], transport=FakeProvider([])) == 2
    captured = capsys.readouterr()
    assert "--yes" in captured.err
    # A run that never started emits no result line, because the result line
    # implies a session existed.
    assert captured.out == ""


def test_run_needs_a_prompt_or_a_resume(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", "--yes"], transport=FakeProvider([])) == 2
    assert "prompt" in capsys.readouterr().err


def test_golden_jsonl(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "out.txt"
    fake = FakeProvider(
        [
            tool_reply("write", {"path": str(target), "content": "hi"}),
            text_reply("finished"),
        ]
    )
    code = main(["run", "--yes", "--jsonl", "write a file"], transport=fake)
    assert code == 0
    assert target.read_text() == "hi"

    emitted = lines(capsys.readouterr().out)
    usage = {"input": 10, "output": 5, "cache_read": 0, "cache_write": 0}
    assert emitted[:5] == [
        {"timestamp": "T", "type": "cost", "text": "10 in / 5 out", "detail": usage},
        {
            "timestamp": "T",
            "type": "tool_use",
            "text": "write",
            "detail": {"path": str(target), "content": "hi"},
        },
        {"timestamp": "T", "type": "progress", "text": "finished", "detail": {}},
        {"timestamp": "T", "type": "cost", "text": "10 in / 5 out", "detail": usage},
        {"timestamp": "T", "type": "output", "text": "finished", "detail": {}},
    ]
    result = emitted[5]
    assert result["type"] == "result"
    assert result["status"] == "done"
    assert result["stop_reason"] == "end_turn"
    assert result["questions"] == []
    assert result["turns"] == 2
    assert result["usage"] == {
        "input": 20,
        "output": 10,
        "cache_read": 0,
        "cache_write": 0,
    }
    assert len(emitted) == 6


def test_every_line_is_json_and_the_result_is_last(
    capsys: pytest.CaptureFixture[str],
) -> None:
    main(["run", "--yes", "--jsonl", "go"], transport=FakeProvider([text_reply("ok")]))
    emitted = lines(capsys.readouterr().out)
    assert [e["type"] for e in emitted].count("result") == 1
    assert emitted[-1]["type"] == "result"


def test_blocked_exits_zero_with_questions(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeProvider([tool_reply("ask", {"questions": ["which file?"]})])
    assert main(["run", "--yes", "--jsonl", "go"], transport=fake) == 0
    result = lines(capsys.readouterr().out)[-1]
    assert result["status"] == "blocked"
    assert result["questions"] == ["which file?"]


def test_an_errored_run_exits_one(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["run", "--yes", "--jsonl", "go"], transport=Exploding())
    assert code == 1
    assert lines(capsys.readouterr().out)[-1]["status"] == "error"


def test_max_turns_is_honoured(capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeProvider([tool_reply("bash", {"command": "true"}) for _ in range(5)])
    code = main(["run", "--yes", "--jsonl", "--max-turns", "2", "loop"], transport=fake)
    assert code == 1
    result = lines(capsys.readouterr().out)[-1]
    assert result["stop_reason"] == "max_turns"
    assert result["turns"] == 2


def test_without_jsonl_the_output_is_plain_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    main(["run", "--yes", "go"], transport=FakeProvider([text_reply("ok")]))
    out = capsys.readouterr().out
    assert "[progress] ok" in out
    assert "{" not in out


def test_session_file_is_written_and_resumable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "s.json"
    fake = FakeProvider([tool_reply("ask", {"questions": ["which file?"]})])
    main(["run", "--yes", "--jsonl", "--session", str(path), "go"], transport=fake)
    assert json.loads(path.read_text())["status"] == "blocked"

    resumed = FakeProvider([text_reply("finished")])
    code = main(
        ["run", "--yes", "--jsonl", "--resume", str(path), "use bar.py"],
        transport=resumed,
    )
    assert code == 0
    result = lines(capsys.readouterr().out)[-1]
    assert result["status"] == "done"
    assert result["turns"] == 2
    # The feedback merged into the trailing user turn rather than following it.
    sent = resumed.calls[0][0]
    assert sent[-1].role == "user"
    assert sent[-1].content[-1] == {"type": "text", "text": "use bar.py"}


def test_a_session_file_is_written_even_when_the_run_fails(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    main(["run", "--yes", "--session", str(path), "go"], transport=Exploding())
    assert json.loads(path.read_text())["status"] == "error"


def test_a_construction_failure_exits_two_with_no_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # No transport= here on purpose: this exercises main()'s own
    # construction path, which every other test bypasses.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    code = main(["run", "--yes", "--jsonl", "--temperature", "0.2", "go"])
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "temperature" in captured.err


def test_a_missing_api_key_exits_two_with_no_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    code = main(["run", "--yes", "--jsonl", "go"])
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "ANTHROPIC_API_KEY" in captured.err


def test_an_unreadable_resume_path_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        ["run", "--yes", "--resume", str(tmp_path / "missing.json")],
        transport=FakeProvider([]),
    )
    assert code == 2
    assert capsys.readouterr().out == ""


def test_a_malformed_resume_file_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "s.json"
    main(
        ["run", "--yes", "--jsonl", "--session", str(path), "go"],
        transport=FakeProvider([text_reply("ok")]),
    )
    capsys.readouterr()
    raw = json.loads(path.read_text())
    raw["unexpected_field"] = 1
    path.write_text(json.dumps(raw))
    code = main(
        ["run", "--yes", "--jsonl", "--resume", str(path)],
        transport=FakeProvider([]),
    )
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "malformed session file" in captured.err


def test_a_failed_session_write_still_emits_the_result_line(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Losing the result line would break the consumer's parser. But the run
    # is not `done` either: conductor derives everything from the four-value
    # status, so a `done` line beside a non-zero exit still reads as finished
    # work, and its next --resume would fail far from the cause.
    unwritable = tmp_path / "missing" / "deep" / "s.json"
    code = main(
        ["run", "--yes", "--jsonl", "--session", str(unwritable), "go"],
        transport=FakeProvider([text_reply("ok")]),
    )
    captured = capsys.readouterr()
    emitted = lines(captured.out)
    assert code == 1
    assert emitted[-1]["type"] == "result"
    assert emitted[-1]["status"] == "error"
    assert "--session" in str(emitted[-1]["error"])
    # logging output is observed via caplog, not capsys: pytest's own logging
    # plugin pre-populates the root logger's handlers, which makes
    # logging.basicConfig() a no-op and means log records never reach the
    # real stderr stream that capsys inspects.
    assert "--session" in caplog.text


def test_resume_clears_the_previous_stop_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "s.json"
    main(
        ["run", "--yes", "--jsonl", "--session", str(path), "go"],
        transport=FakeProvider([tool_reply("ask", {"questions": ["which?"]})]),
    )
    capsys.readouterr()
    code = main(
        ["run", "--yes", "--jsonl", "--resume", str(path), "keep going"],
        transport=Exploding(),
    )
    result = lines(capsys.readouterr().out)[-1]
    assert code == 1
    assert result["status"] == "error"
    # Not the pre-resume "tool_use": a stale stop_reason on a new error lies.
    assert result["stop_reason"] is None


def test_resume_writes_the_session_back_without_an_explicit_session_flag(
    tmp_path: Path,
) -> None:
    # Without this the whole resumed run is discarded and the next --resume
    # replays the stale prefix.
    path = tmp_path / "s.json"
    fake = FakeProvider([tool_reply("ask", {"questions": ["which file?"]})])
    main(["run", "--yes", "--jsonl", "--session", str(path), "go"], transport=fake)
    assert json.loads(path.read_text())["turns"] == 1

    main(
        ["run", "--yes", "--jsonl", "--resume", str(path), "use bar.py"],
        transport=FakeProvider([text_reply("finished")]),
    )
    saved = json.loads(path.read_text())
    assert saved["status"] == "done"
    assert saved["turns"] == 2


def test_a_failed_session_write_leaves_the_previous_file_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The adapter SIGTERMs mid-run and resumes the same path, so a torn write
    # would strand the run with no backup. The rename is what prevents it.
    path = tmp_path / "s.json"
    main(
        ["run", "--yes", "--session", str(path), "go"],
        transport=FakeProvider([tool_reply("ask", {"questions": ["which file?"]})]),
    )
    good = path.read_text()

    def boom(src: object, dst: object) -> None:
        raise OSError("no space left on device")

    monkeypatch.setattr("nare.cli.os.replace", boom)
    code = main(
        ["run", "--yes", "--resume", str(path), "keep going"],
        transport=FakeProvider([text_reply("finished")]),
    )
    assert path.read_text() == good
    assert code == 1
    # And no half-written temp file left beside it in the workdir.
    assert [f.name for f in tmp_path.iterdir()] == ["s.json"]


def test_the_session_is_written_after_every_turn_not_just_at_the_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Conductor SIGTERMs a slow run, and SIGTERM's default handler exits
    # without unwinding, so `finally` never runs. A run persisted only at the
    # end is not resumable after a kill -- which is the whole claim.
    path = tmp_path / "s.json"
    seen: list[int] = []

    def watch(tool: str, args: dict[str, object]) -> bool:
        # Runs mid-run, inside a turn: whatever is on disk here is what a kill
        # at this instant would leave behind.
        seen.append(json.loads(path.read_text())["turns"] if path.exists() else 0)
        return True

    monkeypatch.setattr("nare.cli.approve_all", watch)
    main(
        ["run", "--yes", "--jsonl", "--session", str(path), "go"],
        transport=FakeProvider(
            [
                tool_reply("bash", {"command": "true"}, call_id="c1"),
                tool_reply("bash", {"command": "true"}, call_id="c2"),
                text_reply("finished"),
            ]
        ),
    )

    # Every completed turn is durable before the next one starts work: turn 2
    # runs its tool with turn 1 already on disk. The turn in flight is the only
    # thing a kill can cost, instead of the whole run.
    assert seen == [0, 1]
    assert json.loads(path.read_text())["turns"] == 3


def test_the_session_file_is_readable_only_by_its_owner(tmp_path: Path) -> None:
    # It holds the full transcript, including whatever `read` and `bash`
    # returned, and it lands in the workdir, which is a git checkout.
    path = tmp_path / "s.json"
    main(
        ["run", "--yes", "--session", str(path), "go"],
        transport=FakeProvider([text_reply("ok")]),
    )
    assert path.stat().st_mode & 0o777 == 0o600
