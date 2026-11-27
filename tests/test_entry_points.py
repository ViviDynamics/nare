"""The person-facing entry points (#324): `nare`, `nare "<task>"`, `nare -p`,
`nare exec`, and `nare run` at a terminal.

Every one is additive: `nare run --yes --contract 3 --jsonl` and every path
without a terminal behave as contract 3 says, which the contract tests and the
last tests here hold.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from fake_provider import FakeProvider, text_reply, tool_reply
from nare import person
from nare.cli import build_parser, main

STAMPS = re.compile(r'"(timestamp|session_id)": "[^"]+"')


def normal(out: str) -> list[str]:
    """Output lines with the parts that differ between two runs blanked."""
    return [STAMPS.sub(r'"\1": "X"', line) for line in out.splitlines()]


@pytest.fixture
def tui_calls(monkeypatch: pytest.MonkeyPatch) -> list[argparse.Namespace]:
    """Stand in for the TUI, recording what it was started with."""
    calls: list[argparse.Namespace] = []

    def fake_tui(args: argparse.Namespace, transport: Any = None) -> int:
        calls.append(args)
        return 0

    # A key, so the person-facing check for one (#328) lets the TUI start.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(person, "tui_available", lambda: True)
    monkeypatch.setattr(person, "interactive_terminal", lambda: True)
    monkeypatch.setattr("nare.tui.app.main", fake_tui)
    return calls


@pytest.fixture
def no_tui(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(person, "tui_available", lambda: False)


def at_terminal(monkeypatch: pytest.MonkeyPatch, typed: str) -> None:
    """A terminal whose person types `typed`."""
    monkeypatch.setattr(person, "interactive_terminal", lambda: True)
    monkeypatch.setattr("nare.cli.stdin_is_terminal", lambda: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO(typed))


def piped(monkeypatch: pytest.MonkeyPatch, data: bytes) -> None:
    """A real pipe on stdin, holding `data`."""
    read, write = os.pipe()
    os.write(write, data)
    os.close(write)
    monkeypatch.setattr(sys, "stdin", os.fdopen(read, "r", encoding="utf-8"))


def first_prompt(fake: FakeProvider) -> str:
    messages, _tools = fake.calls[0]
    return str(messages[0].content[0]["text"])


# Task 1: routing.


def test_bare_nare_opens_the_tui(tui_calls: list[argparse.Namespace]) -> None:
    assert main([]) == 0
    [args] = tui_calls
    assert args.prompt is None


def test_a_task_opens_the_tui_with_that_prompt(
    tui_calls: list[argparse.Namespace],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["fix the failing test", "--model", "m"]) == 0
    [args] = tui_calls
    assert args.prompt == "fix the failing test"
    assert args.model == "m"
    # The working directory is the root, as Claude Code and Codex confine it.
    assert Path(args.root).resolve() == tmp_path.resolve()


def test_the_tui_takes_the_namespace_a_task_builds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """nare "<task>" hands the TUI everything nare tui would."""
    from nare.tui import app

    built: list[object] = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(person, "tui_available", lambda: True)
    monkeypatch.setattr(person, "interactive_terminal", lambda: True)

    def prepare_only(args: argparse.Namespace, transport: Any = None) -> int:
        built.append(app.prepare(args, transport))
        return 0

    monkeypatch.setattr(app, "main", prepare_only)
    assert main(["list the files"], transport=FakeProvider([])) == 0
    [tui] = built
    assert isinstance(tui, app.TuiApp)


def test_double_dash_sends_a_subcommand_name_as_a_task(
    tui_calls: list[argparse.Namespace],
) -> None:
    assert main(["--", "run"]) == 0
    assert tui_calls[0].prompt == "run"


def test_a_subcommand_name_after_options_is_not_taken_as_a_task(
    tui_calls: list[argparse.Namespace], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--model", "m", "run"]) == 2
    assert tui_calls == []
    assert "nare -- run" in capsys.readouterr().err


def test_subcommands_keep_their_meaning(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["contract"]) == 0
    assert json.loads(capsys.readouterr().out)["contract"] == 3


def test_a_task_with_no_terminal_names_print_mode(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(person, "interactive_terminal", lambda: False)
    assert main(["fix it"], transport=FakeProvider([])) == 2
    captured = capsys.readouterr()
    assert "nare -p" in captured.err
    assert captured.out == ""


def test_bare_nare_without_the_tui_extra_says_what_to_do(
    no_tui: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(person, "interactive_terminal", lambda: True)
    assert main([]) == 0
    out = capsys.readouterr().out
    assert 'nare "<task>"' in out
    assert 'nare -p "<task>"' in out
    assert "ANTHROPIC_API_KEY" in out
    assert "tui extra" in out


def test_bare_nare_with_no_terminal_says_what_to_do(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(person, "tui_available", lambda: True)
    monkeypatch.setattr(person, "interactive_terminal", lambda: False)
    assert main([]) == 0
    assert 'nare -p "<task>"' in capsys.readouterr().out


# Task 2: nare -p and nare exec.


@pytest.mark.parametrize("argv", [["-p", "say hi"], ["--print", "say hi"]])
def test_print_mode_prints_only_the_answer(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(argv, transport=FakeProvider([text_reply("hi")])) == 0
    assert capsys.readouterr().out == "hi\n"


def test_exec_prints_only_the_answer(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["exec", "say hi"], transport=FakeProvider([text_reply("hi")])) == 0
    assert capsys.readouterr().out == "hi\n"


def test_print_mode_json_is_the_result_line_alone(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeProvider([text_reply("hi")])
    assert main(["-p", "--output-format", "json", "say hi"], transport=fake) == 0
    [line] = capsys.readouterr().out.splitlines()
    result = json.loads(line)
    assert result["type"] == "result"
    assert result["status"] == "done"
    assert result["text"] == "hi"
    assert result["contract"] == 3


def run_jsonl(argv: list[str], capsys: pytest.CaptureFixture[str]) -> list[str]:
    assert main(argv, transport=FakeProvider([text_reply("hi")])) == 0
    return normal(capsys.readouterr().out)


@pytest.mark.parametrize(
    "argv",
    [
        ["-p", "--output-format", "stream-json", "say hi"],
        ["-p", "--json", "say hi"],
        ["exec", "--json", "say hi"],
    ],
)
def test_stream_json_is_the_contract_stream(
    argv: list[str],
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    contract = run_jsonl(
        ["run", "--yes", "--contract", "3", "--jsonl", "--root", ".", "say hi"],
        capsys,
    )
    assert run_jsonl(argv, capsys) == contract
    assert json.loads(contract[-1])["type"] == "result"


def test_json_and_another_output_format_conflict(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["-p", "--json", "--output-format", "text", "x"], transport=None)
    assert code == 2
    err = capsys.readouterr().err
    assert "--json" in err and "--output-format" in err


def test_piped_stdin_is_extra_context(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    piped(monkeypatch, b"def f(): pass\n")
    fake = FakeProvider([text_reply("it defines f")])
    assert main(["-p", "explain"], transport=fake) == 0
    assert first_prompt(fake) == "explain\n\n<stdin>\ndef f(): pass\n</stdin>"
    assert capsys.readouterr().out == "it defines f\n"


def test_blank_stdin_adds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    piped(monkeypatch, b"\n")
    fake = FakeProvider([text_reply("x")])
    assert main(["-p", "explain"], transport=fake) == 0
    assert first_prompt(fake) == "explain"


@pytest.mark.parametrize("argv", [["-p", "-"], ["exec", "-"], ["-p"]])
def test_the_whole_prompt_can_come_from_stdin(
    argv: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    piped(monkeypatch, b"list the files")
    fake = FakeProvider([text_reply("x")])
    assert main(argv, transport=fake) == 0
    assert first_prompt(fake) == "list the files"


def test_print_mode_needs_a_task(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["-p"], transport=FakeProvider([])) == 2
    assert "nare -p" in capsys.readouterr().err


def write_then_answer(target: Path) -> FakeProvider:
    return FakeProvider(
        [
            tool_reply("write", {"path": str(target), "content": "hi"}),
            text_reply("done"),
        ]
    )


def test_print_mode_denies_a_change_and_keeps_going(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "out.txt"
    fake = write_then_answer(target)
    assert main(["-p", "write it"], transport=fake) == 0
    assert not target.exists()
    captured = capsys.readouterr()
    assert captured.out == "done\n"
    assert "write" in captured.err and "--yes" in captured.err
    # The model was told, and went on to answer.
    messages, _tools = fake.calls[1]
    [result] = messages[-1].content
    assert result["is_error"] is True
    assert "not approved" in str(result["content"])


def test_print_mode_runs_a_read_without_asking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("alpha\n")
    fake = FakeProvider(
        [tool_reply("read", {"path": "a.txt"}), text_reply("it says alpha")]
    )
    assert main(["-p", "read a.txt"], transport=fake) == 0
    messages, _tools = fake.calls[1]
    [result] = messages[-1].content
    assert not result.get("is_error")
    assert "alpha" in str(result["content"])


@pytest.mark.parametrize("flag", ["--yes", "--dangerously-skip-permissions", "--yolo"])
def test_a_permission_flag_approves_everything(
    flag: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "out.txt"
    assert main(["-p", flag, "write it"], transport=write_then_answer(target)) == 0
    assert target.read_text() == "hi"


def test_print_mode_confines_tools_to_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    target = tmp_path / "outside.txt"
    assert main(["-p", "--yes", "write it"], transport=write_then_answer(target)) == 0
    assert not target.exists()


def test_print_mode_reports_an_error_on_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeProvider([text_reply("cut", stop_reason="max_tokens")])
    code = main(["-p", "--max-cutoffs", "0", "x"], transport=fake)
    assert code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "nare:" in captured.err


# Task 3: nare run at a terminal.


def test_run_at_a_terminal_asks_before_a_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    at_terminal(monkeypatch, "y\n")
    target = tmp_path / "out.txt"
    assert main(["run", "write it"], transport=write_then_answer(target)) == 0
    assert target.read_text() == "hi"
    assert "allow?" in capsys.readouterr().err


def test_run_at_a_terminal_honours_a_no_with_its_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at_terminal(monkeypatch, "n wrong file\n")
    target = tmp_path / "out.txt"
    fake = write_then_answer(target)
    assert main(["run", "write it"], transport=fake) == 0
    assert not target.exists()
    messages, _tools = fake.calls[1]
    [result] = messages[-1].content
    assert "wrong file" in str(result["content"])


def test_run_without_a_terminal_still_refuses(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("nare.cli.stdin_is_terminal", lambda: False)
    assert main(["run", "go"], transport=FakeProvider([])) == 2
    captured = capsys.readouterr()
    assert captured.err == "nare run refuses to start unattended without --yes\n"
    assert captured.out == ""


def test_piped_run_refuses_exactly_as_before(tmp_path: Path) -> None:
    """`echo | nare run "x"`, in a fresh process with a pipe on stdin."""
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from nare.cli import main; sys.exit(main())",
            "run",
            "x",
        ],
        input="\n",
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=60,
    )
    assert proc.returncode == 2
    assert proc.stderr == "nare run refuses to start unattended without --yes\n"
    assert proc.stdout == ""


# Task 4: the line-mode session without the tui extra.


def test_line_mode_asks_answers_and_ends_at_eof(
    no_tui: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    at_terminal(monkeypatch, "y\n")
    target = tmp_path / "out.txt"
    assert main(["write it"], transport=write_then_answer(target)) == 0
    assert target.read_text() == "hi"
    captured = capsys.readouterr()
    assert captured.out == "done\n"
    assert "allow?" in captured.err


def test_line_mode_takes_a_follow_up(
    no_tui: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    at_terminal(monkeypatch, "\nand again\n")
    fake = FakeProvider([text_reply("one"), text_reply("two")])
    assert main(["say one"], transport=fake) == 0
    assert len(fake.calls) == 2
    messages, _tools = fake.calls[1]
    assert messages[-1].content[-1]["text"] == "and again"


def test_line_mode_runs_reads_without_asking(
    no_tui: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("alpha\n")
    # Nothing typed: a prompt would read EOF and deny.
    at_terminal(monkeypatch, "")
    fake = FakeProvider([tool_reply("read", {"path": "a.txt"}), text_reply("alpha")])
    assert main(["read a.txt"], transport=fake) == 0
    messages, _tools = fake.calls[1]
    assert "alpha" in str(messages[-1].content[0]["content"])


# Task 5: help.


def test_help_leads_with_the_person_facing_forms(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--help"]) == 0
    out = capsys.readouterr().out
    assert out.index('nare "<task>"') < out.index("For callers")
    assert out.index("nare -p") < out.index("For callers")
    callers = out[out.index("For callers") :]
    for name in ("contract", "redact", "mcp", "run"):
        assert re.search(rf"^  {name}\b", callers, re.MULTILINE), name
    [sub] = [
        a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction)
    ]
    for name in sub.choices:
        assert re.search(rf"^  {name}\b", out, re.MULTILINE), name
    assert re.search(r"^  exec\b", out, re.MULTILINE)


def test_print_help_lists_its_own_flags(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["-p", "--help"])
    out = capsys.readouterr().out
    for flag in ("--output-format", "--json", "--dangerously-skip-permissions"):
        assert flag in out


def test_read_line_reads_a_pipe_one_line_at_a_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from nare.terminal import read_line

    piped(monkeypatch, b"y\nn because\nlast")

    async def three() -> list[str | None]:
        return [await read_line("> ") for _ in range(4)]

    assert asyncio.run(three()) == ["y\n", "n because\n", "last", None]
