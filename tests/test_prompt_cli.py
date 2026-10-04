"""Prompt input routes exercise the production CLI with an offline transport."""

import json
from pathlib import Path

import pytest

from test_budget_cli import invoke, text


@pytest.mark.parametrize("source", ["file", "stdin"])
def test_one_mib_prompt_bypasses_argv(tmp_path: Path, source: str) -> None:
    prompt = "x" * (1024 * 1024)
    saved = tmp_path / "session.json"
    path = tmp_path / "prompt.txt"
    path.write_bytes(prompt.encode())
    args = ["--prompt-file", str(path)] if source == "file" else ["-"]
    proc, rows, calls = invoke(
        tmp_path,
        [text()],
        *args,
        "--session",
        str(saved),
        "--context-window",
        "1000000",
        stdin=prompt if source == "stdin" else None,
    )
    assert proc.returncode == 0, proc.stderr
    assert rows[-1]["status"] == "done"
    assert len(calls) == 1
    assert json.loads(saved.read_text())["messages"][0]["content"][0]["text"] == prompt
    (tmp_path / "stdout.jsonl").write_text(proc.stdout)


@pytest.mark.parametrize("source", ["file", "stdin"])
def test_prompt_content_matches_positional(tmp_path: Path, source: str) -> None:
    prompt = "héllo\r\nnext\n\n"
    positional = tmp_path / "positional.json"
    proc, _, _ = invoke(tmp_path, [text()], prompt, "--session", str(positional))
    assert proc.returncode == 0
    path = tmp_path / "prompt.txt"
    path.write_bytes(prompt.encode())
    saved = tmp_path / "session.json"
    args = ["--prompt-file", str(path)] if source == "file" else ["-"]
    proc, _, _ = invoke(
        tmp_path,
        [text()],
        *args,
        "--session",
        str(saved),
        stdin=prompt if source == "stdin" else None,
    )
    assert proc.returncode == 0, proc.stderr
    assert (
        json.loads(saved.read_text())["messages"]
        == json.loads(positional.read_text())["messages"]
    )


@pytest.mark.parametrize("positional", ["task", "-"])
def test_two_prompt_sources_refuse_before_start(
    tmp_path: Path, positional: str
) -> None:
    saved = tmp_path / "session.json"
    proc, rows, calls = invoke(
        tmp_path,
        [text()],
        positional,
        "--prompt-file",
        str(tmp_path / "missing"),
        "--session",
        str(saved),
    )
    assert proc.returncode == 2
    assert "--prompt-file" in proc.stderr
    assert ("stdin" if positional == "-" else "positional") in proc.stderr
    assert not rows and not calls and not saved.exists()


@pytest.mark.parametrize("kind", ["missing", "directory", "invalid-utf8"])
def test_bad_prompt_file_refuses_before_start(tmp_path: Path, kind: str) -> None:
    path = tmp_path / "prompt.txt"
    if kind == "directory":
        path.mkdir()
    elif kind == "invalid-utf8":
        path.write_bytes(b"\xff")
    proc, rows, calls = invoke(tmp_path, [text()], "--prompt-file", str(path))
    assert proc.returncode == 2
    assert "--prompt-file" in proc.stderr
    assert not rows and not calls


def test_file_prompt_is_resume_followup(tmp_path: Path) -> None:
    saved = tmp_path / "session.json"
    proc, _, _ = invoke(tmp_path, [text()], "first", "--session", str(saved))
    assert proc.returncode == 0
    path = tmp_path / "prompt.txt"
    path.write_text("follow up\n")
    proc, rows, calls = invoke(
        tmp_path, [text()], "--resume", str(saved), "--prompt-file", str(path)
    )
    assert proc.returncode == 0, proc.stderr
    assert rows[-1]["status"] == "done" and len(calls) == 1
    assert (
        json.loads(saved.read_text())["messages"][-2]["content"][0]["text"]
        == "follow up\n"
    )


@pytest.mark.parametrize("source", ["file", "stdin"])
def test_empty_explicit_prompt_is_a_source(tmp_path: Path, source: str) -> None:
    path = tmp_path / "prompt.txt"
    path.write_bytes(b"")
    args = ["--prompt-file", str(path)] if source == "file" else ["-"]
    proc, rows, calls = invoke(tmp_path, [text()], *args, stdin="")
    assert proc.returncode == 0, proc.stderr
    assert rows[-1]["status"] == "done" and len(calls) == 1


def test_invalid_utf8_stdin_refuses_before_start(tmp_path: Path) -> None:
    import subprocess
    import sys

    from test_budget_cli import PROBE

    script = tmp_path / "script.json"
    script.write_text(json.dumps({"replies": [text()]}))
    proc = subprocess.run(
        [sys.executable, str(PROBE), str(script), "run", "--yes", "--jsonl", "-"],
        input=b"\xff",
        capture_output=True,
    )
    assert proc.returncode == 2
    assert b"stdin (-)" in proc.stderr
    assert not proc.stdout and not script.with_suffix(".calls.json").exists()
