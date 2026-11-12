"""A caller check the command line can run (#296), and the awaitable checks it is
built on."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fake_provider import FakeProvider, text_reply, tool_reply
from nare.events import Event
from nare.loop import run
from nare.session import Session, new_session
from nare.tools import Policy

PERSON: dict[str, Any] = {
    "type": "object",
    "properties": {"name": {"type": "string"}},
    "required": ["name"],
    "additionalProperties": False,
}


async def drain(session: Session, provider: Any, **kwargs: Any) -> list[Event]:
    return [event async for event in run(session, transport=provider, **kwargs)]


@pytest.fixture
def notes(tmp_path: Path) -> Policy:
    (tmp_path / "NOTES.txt").write_text("ada\n", encoding="utf-8")
    return Policy(tools=frozenset({"read"}), root=tmp_path)


async def test_an_async_check_error_reaches_the_model_as_a_correction(
    notes: Policy,
) -> None:
    async def check(answer: Any) -> list[Any]:
        if answer["name"] == "bob":
            return [{"code": "name_unknown", "location": ["name"], "message": "no bob"}]
        return []

    provider = FakeProvider(
        [text_reply('{"name": "bob"}'), text_reply('{"name": "ada"}')]
    )
    session = new_session("who?")

    events = await drain(session, provider, schema=PERSON, policy=notes, check=check)

    assert session.status == "done"
    assert session.output == {"name": "ada"}
    [progress] = [e for e in events if "schema correction" in e.text]
    assert progress.detail["errors"][0]["code"] == "name_unknown"


async def test_an_async_check_that_raises_ends_the_run(notes: Policy) -> None:
    async def check(answer: Any) -> list[Any]:
        raise RuntimeError("lookup failed")

    session = new_session("who?")

    await drain(
        session,
        FakeProvider([text_reply('{"name": "ada"}')]),
        schema=PERSON,
        policy=notes,
        check=check,
    )

    assert session.status == "error"
    assert session.stop_reason is None
    assert session.error == "check raised RuntimeError: lookup failed"


async def test_an_async_check_with_two_parameters_is_given_the_reads(
    notes: Policy,
) -> None:
    seen: list[Any] = []

    async def check(answer: Any, reads: list[dict[str, Any]]) -> list[Any]:
        seen.append([r["path"] for r in reads])
        return []

    provider = FakeProvider(
        [tool_reply("read", {"path": "NOTES.txt"}), text_reply('{"name": "ada"}')]
    )

    await drain(new_session("who?"), provider, schema=PERSON, policy=notes, check=check)

    assert seen == [["NOTES.txt"]]


# The command check itself. Each test writes a small Python script and runs it
# with this interpreter, as `--check-command` would run a caller's argv.


def script(tmp_path: Path, body: str) -> list[str]:
    import sys

    path = tmp_path / "check.py"
    path.write_text("import json, os, sys, time\n" + body, encoding="utf-8")
    return [sys.executable, str(path)]


async def test_a_command_check_gets_the_answer_and_reads_and_returns_errors(
    tmp_path: Path,
) -> None:
    from nare.caller_tools import command_check

    argv = script(
        tmp_path,
        "got = json.load(sys.stdin)\n"
        "json.dump([{'code': 'seen', 'location': ['name'], 'message': "
        "json.dumps(got, sort_keys=True), 'guidance': 'g'}, 'plain'], sys.stdout)\n",
    )
    check = command_check(argv, timeout=10, root=None)

    found = await check({"name": "ada"}, [{"path": "NOTES.txt"}])

    assert found == [
        {
            "code": "seen",
            "location": ["name"],
            "message": '{"answer": {"name": "ada"}, "reads": [{"path": "NOTES.txt"}]}',
            "guidance": "g",
        },
        "plain",
    ]


async def test_an_empty_list_passes(tmp_path: Path) -> None:
    from nare.caller_tools import command_check

    check = command_check(script(tmp_path, "print('[]')\n"), timeout=10, root=None)

    assert await check({"name": "ada"}, []) == []


async def test_a_command_check_runs_in_the_root_without_provider_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nare.caller_tools import command_check

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-key")
    monkeypatch.setenv("NARE_CHECK_PROBE", "kept")
    root = tmp_path / "root"
    root.mkdir()
    argv = script(
        tmp_path,
        "print(json.dumps([os.getcwd(), str('OPENAI_API_KEY' in os.environ), "
        "os.environ.get('NARE_CHECK_PROBE')]))\n",
    )

    found = await command_check(argv, timeout=10, root=root)({}, [])

    assert found == [str(root.resolve()), "False", "kept"]


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (
            "sys.stderr.write('manifest missing'); sys.exit(3)\n",
            "exit status 3: manifest missing",
        ),
        ("print('not json')\n", "stdout is not JSON"),
        ("print('{}')\n", "stdout is not a JSON list of errors"),
        ("print('[3]')\n", "error 0 is not a string or an object"),
        ('print(\'[{"message": "m"}]\')\n', "error 0 has no string code"),
        ('print(\'["ok", {"code": "c"}]\')\n', "error 1 has no string message"),
        (
            'print(\'[{"code": "c", "message": "m", "location": "ab"}]\')\n',
            "error 0 location is not a list of keys and indexes",
        ),
        (
            'print(\'[{"code": "c", "message": "m", "location": [{}]}]\')\n',
            "error 0 location is not a list of keys and indexes",
        ),
        (
            'print(\'[{"code": "c", "message": "m", "guidance": 5}]\')\n',
            "error 0 guidance is not a string",
        ),
    ],
)
async def test_a_command_check_that_fails_raises(
    tmp_path: Path, body: str, reason: str
) -> None:
    from nare.caller_tools import CheckCommandFailed, command_check

    check = command_check(script(tmp_path, body), timeout=10, root=None)

    with pytest.raises(CheckCommandFailed, match=reason):
        await check({}, [])


async def test_a_command_check_past_its_timeout_is_killed_with_its_children(
    tmp_path: Path,
) -> None:
    import subprocess as sp
    import time

    from nare.caller_tools import CheckCommandFailed, command_check

    pid_file = tmp_path / "child.pid"
    argv = script(
        tmp_path,
        "import subprocess\n"
        "child = subprocess.Popen(\n"
        "    [sys.executable, '-c', 'import time; time.sleep(60)']\n"
        ")\n"
        f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(60)\n",
    )
    started = time.monotonic()

    with pytest.raises(CheckCommandFailed, match="timed out after 0.5 seconds"):
        await command_check(argv, timeout=0.5, root=None)({}, [])

    assert time.monotonic() - started < 10
    child = int(pid_file.read_text())
    assert sp.run(["kill", "-0", str(child)], capture_output=True).returncode != 0


async def test_a_cancelled_command_check_is_killed(tmp_path: Path) -> None:
    import asyncio
    import subprocess as sp

    from nare.caller_tools import command_check

    pid_file = tmp_path / "check.pid"
    argv = script(
        tmp_path,
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\ntime.sleep(60)\n",
    )
    task = asyncio.ensure_future(command_check(argv, timeout=60, root=None)({}, []))
    for _ in range(200):
        if pid_file.exists() and pid_file.read_text():
            break
        await asyncio.sleep(0.02)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    pid = int(pid_file.read_text())
    assert sp.run(["kill", "-0", str(pid)], capture_output=True).returncode != 0


@pytest.mark.parametrize(
    "text",
    ['"check.py"', "[]", '["python3", 3]', '["", "x"]', "python3 check.py", "{}"],
)
def test_a_check_command_must_be_a_json_array_of_strings(text: str) -> None:
    from nare.caller_tools import parse_check_command

    with pytest.raises(ValueError, match="--check-command"):
        parse_check_command(text)


def test_a_check_command_parses_to_its_argv() -> None:
    from nare.caller_tools import parse_check_command

    assert parse_check_command('["python3", "check.py", "--strict"]') == [
        "python3",
        "check.py",
        "--strict",
    ]


# The command line: --check-command and --check-timeout on `nare run`.

REFUSE_BOB = (
    "got = json.load(sys.stdin)\n"
    "bad = got['answer']['name'] == 'bob'\n"
    "print(json.dumps([{'code': 'name_unknown', 'location': ['name'], "
    "'message': 'no such person: bob', 'guidance': 'name someone in NOTES.txt'}] "
    "if bad else []))\n"
)


def cli_run(tmp_path: Path, extra: list[str], replies: list[Any]) -> tuple[int, Any]:
    from nare.cli import main

    schema = tmp_path / "person.json"
    schema.write_text(json.dumps(PERSON), encoding="utf-8")
    code = main(
        ["run", "who?", "--yes", "--jsonl", "--schema", str(schema), *extra],
        transport=FakeProvider(replies),
    )
    return code, schema


def result_line(out: str) -> dict[str, Any]:
    last: dict[str, Any] = json.loads(out.strip().splitlines()[-1])
    return last


def test_a_check_command_corrects_an_answer_in_the_session(
    tmp_path: Path, capsys: Any
) -> None:
    argv = json.dumps(script(tmp_path, REFUSE_BOB))

    code, _ = cli_run(
        tmp_path,
        ["--check-command", argv],
        [text_reply('{"name": "bob"}'), text_reply('{"name": "ada"}')],
    )

    out = capsys.readouterr().out
    assert code == 0
    result = result_line(out)
    assert result["status"] == "done"
    assert result["output"] == {"name": "ada"}
    events = [json.loads(line) for line in out.strip().splitlines()]
    [correction] = [e for e in events if "schema correction" in e.get("text", "")]
    assert correction["detail"]["errors"][0]["code"] == "name_unknown"


def test_a_failing_check_command_ends_the_run_as_a_raising_check(
    tmp_path: Path, capsys: Any
) -> None:
    argv = json.dumps(
        script(tmp_path, "sys.stderr.write('no manifest'); sys.exit(3)\n")
    )

    code, _ = cli_run(
        tmp_path, ["--check-command", argv], [text_reply('{"name": "ada"}')]
    )

    result = result_line(capsys.readouterr().out)
    assert code == 1
    assert result["status"] == "error"
    assert result["stop_reason"] is None
    assert result["error"] == (
        "check raised CheckCommandFailed: exit status 3: no manifest"
    )


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--check-command", "python3 check.py"], "--check-command takes a JSON array"),
        (["--check-timeout", "5"], "--check-timeout requires --check-command"),
        (["--check-command", '["true"]', "--check-timeout", "0"], "--check-timeout"),
        (["--check-command", '["true"]', "--check-timeout", "nan"], "--check-timeout"),
    ],
)
def test_bad_check_flags_never_start(
    tmp_path: Path, capsys: Any, args: list[str], message: str
) -> None:
    code, _ = cli_run(tmp_path, args, [text_reply('{"name": "ada"}')])

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert message in captured.err


def test_a_check_command_without_a_schema_never_starts(capsys: Any) -> None:
    from nare.cli import main

    code = main(
        ["run", "go", "--yes", "--check-command", '["true"]'],
        transport=FakeProvider([text_reply("done")]),
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "--check-command requires --schema" in captured.err


def test_the_contract_advertises_the_check_command() -> None:
    from nare.contract import describe

    entry = describe()["check_command"]

    assert entry == {
        "flag": "--check-command",
        "argv": "JSON array of strings, run without a shell",
        "timeout_flag": "--check-timeout",
        "default_timeout": 30.0,
        "requires": "--schema",
        "runs_on": "a final answer that passes the schema",
        "cwd": "--root, else the current directory",
        "credentials": "withheld",
        "sandbox": "not confined by --bash-sandbox",
        "stdin": {"answer": "the parsed answer", "reads": "the run's read records"},
        "stdout": (
            "JSON list of errors: {code, message, location?, guidance?} or strings"
        ),
        "passes": "an empty list",
        "budget": "--max-corrections, shared with schema corrections",
        "failure": {
            "when": ["non-zero exit", "timeout", "stdout not a list of errors"],
            "status": "error",
            "stop_reason": None,
            "error_prefix": "check raised CheckCommandFailed: ",
        },
    }
    for flag in (entry["flag"], entry["timeout_flag"]):
        assert flag in describe()["flags"]


def test_the_documented_contract_describes_the_check_command() -> None:
    text = (Path(__file__).parent.parent / "docs" / "contract.md").read_text(
        encoding="utf-8"
    )
    flat = " ".join(text.split())

    assert "The command line takes no check" not in flat
    assert "`--check-command`" in flat
    assert "check raised CheckCommandFailed:" in flat
    assert '{"answer": ..., "reads": [...]}' in flat
    assert "advertises this under `check_command`" in flat


def test_the_run_deadline_stops_a_slow_check_command(
    tmp_path: Path, capsys: Any
) -> None:
    import time

    argv = json.dumps(script(tmp_path, "time.sleep(60)\nprint('[]')\n"))
    started = time.monotonic()

    code, _ = cli_run(
        tmp_path,
        ["--check-command", argv, "--check-timeout", "120", "--timeout", "1"],
        [text_reply('{"name": "ada"}')],
    )

    result = result_line(capsys.readouterr().out)
    assert time.monotonic() - started < 15
    assert result["stop_reason"] == "timeout"
    assert code == 1
