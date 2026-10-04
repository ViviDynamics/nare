"""Regenerate reviewable evidence using the real CLI and an offline transport.

Run from the repository root: uv run python tests/budget_evidence.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from test_budget_cli import invoke, read, text


def main() -> None:
    root = Path("docs/evidence/41")
    root.mkdir(parents=True, exist_ok=True)
    source = root / "source.txt"
    source.write_text("def reviewed_function():\n    return 'evidence'\n")
    schema = root / "findings.schema.json"
    schema.write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {
                    "findings": {"type": "array", "items": {"type": "string"}}
                },
                "required": ["findings"],
                "additionalProperties": False,
            },
            indent=2,
        )
        + "\n"
    )
    session = root / "budget" / "session.json"
    partial_session = root / "partial" / "session.json"
    replies = [
        read(source, "c1"),
        read(source, "c2"),
        text('{"findings":["confirmed"]}'),
    ]

    def capture(
        name: str, script: list[dict[str, Any]], *argv: str, measure_input: bool = False
    ) -> None:
        directory = root / name
        directory.mkdir(exist_ok=True)
        proc, lines, calls = invoke(
            directory, script, *argv, measure_input=measure_input
        )
        (directory / "stdout.jsonl").write_text(proc.stdout)
        (directory / "stderr.txt").write_text(proc.stderr)
        (directory / "invocation.json").write_text(
            json.dumps(
                {
                    "argv": ["nare", "run", "--yes", "--jsonl", *argv],
                    "offline_entrypoint": "uv run python tests/budget_probe.py "
                    + str(directory / "script.json"),
                    "exit": proc.returncode,
                    "model_calls": len(calls),
                },
                indent=2,
            )
            + "\n"
        )
        if "--resume" in argv:
            (directory / "session.json").write_text(
                Path(argv[argv.index("--resume") + 1]).read_text()
            )
        print(
            json.dumps(
                {
                    "case": name,
                    "exit": proc.returncode,
                    "model_calls": len(calls),
                    "result": lines[-1] if lines else None,
                }
            )
        )

    capture(
        "baseline",
        replies,
        "review",
        "--max-tokens",
        "5",
        "--session",
        str(root / "baseline" / "session.json"),
    )
    capture(
        "budget",
        replies,
        "review",
        "--max-tokens",
        "5",
        "--budget-tokens",
        "25",
        "--session",
        str(session),
    )
    # Resume writes back: save the original stop as an immutable evidence copy.
    (root / "budget" / "stopped.session.json").write_text(session.read_text())
    capture("resume-exhausted", [text()], "--resume", str(session))
    capture(
        "resume-raised",
        [text('{"findings":["confirmed"]}')],
        "--resume",
        str(session),
        "--budget-tokens",
        "60",
        "--schema",
        str(schema),
    )
    capture(
        "done-crossing",
        replies,
        "review",
        "--max-tokens",
        "5",
        "--budget-tokens",
        "31",
        "--schema",
        str(schema),
        "--session",
        str(root / "done-crossing" / "session.json"),
    )
    capture("invalid", [text()], "review", "--budget-tokens", "0")
    cached = read(source, "cache1")
    cached["usage"] = {"input": 3, "output": 2, "cache_read": 7, "cache_write": 4}
    capture(
        "cache",
        [cached, text()],
        "review",
        "--budget-tokens",
        "16",
        "--session",
        str(root / "cache" / "session.json"),
    )
    capture(
        "partial",
        [read(source, "partial1", '{"findings":["confirmed partial finding"]}')],
        "review",
        "--schema",
        str(schema),
        "--budget-tokens",
        "15",
        "--session",
        str(partial_session),
    )
    # Use the new benchmark's real generator through real bash tool calls.
    overflow: list[dict[str, Any]] = []
    for n in range(1, 9):
        args = {
            "command": f"python benchmarks/cases/context-overflow/fixture/log.py {n}"
        }
        overflow.append(
            {
                "content": [
                    {"type": "tool_use", "id": f"log{n}", "name": "bash", "input": args}
                ],
                "tool_calls": [{"id": f"log{n}", "name": "bash", "args": args}],
                "stop_reason": "tool_use",
            }
        )
    capture(
        "overflow",
        [*overflow, text("read all eight logs")],
        "read eight logs",
        "--context-window",
        "32000",
        measure_input=True,
    )
    # Keep budget/session.json aligned with its original stdout, rather than
    # with the later continuation (which has its own snapshot).
    session.write_text((root / "budget" / "stopped.session.json").read_text())


if __name__ == "__main__":
    main()
