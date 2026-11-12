"""A caller check the command line can run (#296), and the awaitable checks it is
built on."""

from __future__ import annotations

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
