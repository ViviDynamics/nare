from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from nare.cli import main
from nare.session import Message, Usage
from nare.transport import Reply, ToolCall


class MCPModel:
    def __init__(self, name: str = "local__echo") -> None:
        self.calls = 0
        self.name = name
        self.schemas: list[dict[str, Any]] = []
        self.messages: list[Message] = []

    async def context_window(self) -> int | None:
        return None

    async def turn(self, messages: list[Message], tools: list[dict[str, Any]]) -> Reply:
        self.calls += 1
        self.schemas = tools
        self.messages = list(messages)
        if self.calls == 1:
            return Reply(
                [
                    {
                        "type": "tool_use",
                        "id": "c",
                        "name": self.name,
                        "input": {"text": "hello"},
                    }
                ],
                [ToolCall("c", self.name, {"text": "hello"})],
                Usage(),
                "tool_use",
            )
        return Reply([{"type": "text", "text": "finished"}], [], Usage(), "end_turn")


def config(tmp_path: Path, command: str = sys.executable) -> tuple[Path, Path]:
    pid = tmp_path / "pid"
    path = tmp_path / "mcp.json"
    path.write_text(
        json.dumps(
            {
                "local": {
                    "command": command,
                    "args": [str(Path(__file__).with_name("mcp_server.py")), str(pid)],
                    "timeout": 1,
                }
            }
        )
    )
    return path, pid


def test_cli_real_stdio_tools_and_cleanup(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path, pid = config(tmp_path)
    model = MCPModel()
    code = main(
        ["run", "echo", "--yes", "--jsonl", "--mcp-config", str(path)], transport=model
    )
    assert code == 0
    assert "local__echo" in [s["name"] for s in model.schemas]
    assert model.messages[-1].content[0]["content"] == "hello"
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert any(
        e.get("type") == "tool_use" and e["text"] == "local__echo" for e in events
    )
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid.read_text()), 0)


def test_cli_excluded_external_tool(tmp_path: Path) -> None:
    path, _ = config(tmp_path)
    model = MCPModel()
    assert (
        main(
            ["run", "echo", "--yes", "--mcp-config", str(path), "--tools", "read"],
            transport=model,
        )
        == 0
    )
    assert [s["name"] for s in model.schemas] == ["read"]
    assert model.messages[-1].content[0]["is_error"]
    assert "not allowed" in model.messages[-1].content[0]["content"]


def test_startup_failure_named_without_model_call(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path, _ = config(tmp_path, "/no/such/mcp")
    model = MCPModel()
    assert (
        main(
            ["run", "echo", "--yes", "--jsonl", "--mcp-config", str(path)],
            transport=model,
        )
        == 1
    )
    assert model.calls == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["stop_reason"] == "mcp"
    assert result["status"] == "error"


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"bad alias": {"command": "x"}},
        {"local": {"command": "x", "url": "http://localhost"}},
        {"local": {"url": "file:///tmp/server"}},
        {"local": {"command": "x", "timeout": 0}},
    ],
)
def test_invalid_config_never_started(
    tmp_path: Path, payload: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload))
    model = MCPModel()
    assert (
        main(
            ["run", "echo", "--yes", "--jsonl", "--mcp-config", str(path)],
            transport=model,
        )
        == 2
    )
    assert model.calls == 0
    assert capsys.readouterr().out == ""


def test_hanging_initialize_is_bounded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import time

    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "hung": {
                    "command": sys.executable,
                    "args": ["-c", "import time; time.sleep(30)"],
                    "timeout": 0.1,
                }
            }
        )
    )
    model = MCPModel()
    before = time.monotonic()
    assert (
        main(
            ["run", "echo", "--yes", "--jsonl", "--mcp-config", str(path)],
            transport=model,
        )
        == 1
    )
    assert time.monotonic() - before < 5
    assert model.calls == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["stop_reason"] == "mcp"


@pytest.mark.asyncio
async def test_external_tool_requires_approval(tmp_path: Path) -> None:
    from nare.loop import run
    from nare.mcp import parse_servers
    from nare.session import new_session

    path, _ = config(tmp_path)
    model = MCPModel()
    session = new_session("echo")
    events = [
        e
        async for e in run(
            session,
            transport=model,
            mcp_servers=parse_servers(json.loads(path.read_text())),
            approve=lambda name, args: False,
        )
    ]
    assert session.status == "done"
    assert model.messages[-1].content[0]["is_error"]
    assert "not approved" in model.messages[-1].content[0]["content"]
    assert any(e.type == "tool_use" for e in events)


def test_subprocess_cli_remote_mcp(tmp_path: Path) -> None:
    import socket
    import subprocess
    import time

    from test_budget_cli import invoke, text

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server_env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")
    }
    proc = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("mcp_http_server.py")),
            str(port),
        ],
        env=server_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                if proc.poll() is not None or time.monotonic() >= deadline:
                    raise AssertionError("MCP fixture failed to start") from None
                time.sleep(0.02)
        path = tmp_path / "remote.json"
        path.write_text(
            json.dumps(
                {"remote": {"url": f"http://127.0.0.1:{port}/mcp", "timeout": 2}}
            )
        )
        replies = [
            {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "r",
                        "name": "remote__echo",
                        "input": {"text": "hello remote"},
                    }
                ],
                "tool_calls": [
                    {
                        "id": "r",
                        "name": "remote__echo",
                        "args": {"text": "hello remote"},
                    }
                ],
                "stop_reason": "tool_use",
            },
            text(),
        ]
        session_path = tmp_path / "session.json"
        result, lines, calls = invoke(
            tmp_path,
            replies,
            "go",
            "--mcp-config",
            str(path),
            "--tools",
            "remote__echo",
            "--session",
            str(session_path),
            env={"ANTHROPIC_API_KEY": "model-only-test-value"},
        )
        assert result.returncode == 0, result.stderr + result.stdout
        assert len(calls) == 2
        assert lines[-1]["status"] == "done"
        saved = json.loads(session_path.read_text())
        result_blocks = [
            b
            for m in saved["messages"]
            for b in m["content"]
            if b["type"] == "tool_result"
        ]
        assert result_blocks[0]["content"] == 'hello remote\n{"result": "hello remote"}'
        assert not result_blocks[0]["is_error"]
        assert saved["policy"]["tools"] == ["remote__echo"]
    finally:
        proc.terminate()
        proc.communicate(timeout=5)


@pytest.mark.asyncio
async def test_mcp_results_are_redacted_events(tmp_path: Path) -> None:
    from nare.loop import run
    from nare.mcp import parse_servers
    from nare.session import new_session

    class SecretModel(MCPModel):
        async def turn(
            self, messages: list[Message], tools: list[dict[str, Any]]
        ) -> Reply:
            reply = await super().turn(messages, tools)
            if reply.tool_calls:
                return Reply(
                    [
                        {
                            "type": "tool_use",
                            "id": "c",
                            "name": self.name,
                            "input": {"text": "password=private"},
                        }
                    ],
                    [ToolCall("c", self.name, {"text": "password=private"})],
                    Usage(),
                    "tool_use",
                )
            return reply

    path, _ = config(tmp_path)
    session = new_session("echo")
    events = [
        e
        async for e in run(
            session,
            transport=SecretModel(),
            mcp_servers=parse_servers(json.loads(path.read_text())),
        )
    ]
    results = [e for e in events if "tool_result" in e.detail]
    assert results
    assert "private" not in json.dumps([e.detail for e in results])
    assert "[redacted]" in results[0].detail["tool_result"]["content"]


@pytest.mark.asyncio
async def test_early_close_cleans_child(tmp_path: Path) -> None:
    from nare.loop import run
    from nare.mcp import parse_servers
    from nare.session import new_session

    path, pid = config(tmp_path)
    stream = run(
        new_session("echo"),
        transport=MCPModel(),
        mcp_servers=parse_servers(json.loads(path.read_text())),
    )
    await anext(stream)
    await stream.aclose()
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid.read_text()), 0)


def test_exhausted_resume_does_not_start_mcp(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from nare.session import dumps, new_session

    path, pid = config(tmp_path)
    session = new_session("echo")
    session.usage = Usage(input=10)
    session.budget = {"tokens": 10}
    saved = tmp_path / "session.json"
    saved.write_text(dumps(session))
    model = MCPModel()
    assert (
        main(
            [
                "run",
                "--resume",
                str(saved),
                "--yes",
                "--jsonl",
                "--mcp-config",
                str(path),
            ],
            transport=model,
        )
        == 1
    )
    assert model.calls == 0
    assert not pid.exists()
    assert (
        json.loads(capsys.readouterr().out.splitlines()[-1])["stop_reason"] == "budget"
    )


@pytest.mark.asyncio
async def test_partial_startup_failure_closes_opened_child(tmp_path: Path) -> None:
    from nare.loop import run
    from nare.mcp import Server, parse_servers
    from nare.session import new_session

    path, pid = config(tmp_path)
    servers = parse_servers(json.loads(path.read_text()))
    servers["bad"] = Server(command="/missing-server")
    session = new_session("echo")
    model = MCPModel()
    events = [e async for e in run(session, transport=model, mcp_servers=servers)]
    assert model.calls == 0
    assert session.stop_reason == "mcp"
    assert events[-1].type == "error"
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid.read_text()), 0)
