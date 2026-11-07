"""Deterministic MCP stdio fixture; no model or external service."""

import json
import os
import sys
import time
from pathlib import Path
from typing import Any


def main() -> None:
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(str(os.getpid()))
    if "--slow-start" in sys.argv:
        time.sleep(0.5)
    for line in sys.stdin:
        request = json.loads(line)
        if "id" not in request:
            continue
        method = request["method"]
        if method == "initialize":
            result = {
                "protocolVersion": request["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fixture", "version": "1"},
            }
        elif method == "tools/list" and "--endless-pages" in sys.argv:
            cursor = int(request.get("params", {}).get("cursor") or 0)
            result = {"tools": [], "nextCursor": str(cursor + 1)}
        elif method == "tools/list" and "--many" in sys.argv:
            result = {"tools": many_tools(many_count(sys.argv))}
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo text",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                            "required": ["text"],
                        },
                    }
                ]
            }
        elif method == "tools/call":
            params = request["params"]
            if params["name"] == "echo":
                text = params["arguments"]["text"]
            else:
                text = f"{params['name']} ran"
            result = {
                "content": [{"type": "text", "text": text}],
                "isError": False,
            }
        else:
            result = {}
        print(
            json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}),
            flush=True,
        )
        if method == "tools/list" and "--stall" in sys.argv:
            time.sleep(30)


FILLER_FIELDS = 6


def many_count(argv: list[str]) -> int:
    """The N after --many, or 40 when none is given."""
    index = argv.index("--many")
    if index + 1 < len(argv) and argv[index + 1].isdigit():
        return int(argv[index + 1])
    return 40


def many_tools(count: int) -> list[dict[str, Any]]:
    """`echo`, plus count - 1 filler tools with verbose descriptions and schemas.

    The fillers stand in for a server with many tools: each carries a long
    description and several typed properties, so its definition is large. The
    real tool keeps the name `echo`, so a search for "echo" finds it.
    """
    tools = [
        {
            "name": "echo",
            "description": "Echo the given text back unchanged",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        }
    ]
    for n in range(1, count):
        properties = {
            f"field_{k}": {
                "type": "string",
                "description": (
                    f"Field {k} of record family {n}. Accepts the identifier of "
                    "the upstream resource, normalised to lower case before lookup."
                ),
            }
            for k in range(FILLER_FIELDS)
        }
        tools.append(
            {
                "name": f"filler_{n:03d}",
                "description": (
                    f"Filler operation {n} for the record family {n}. It looks up "
                    "upstream records by identifier, applies the family's paging "
                    "rules, and returns the matching rows as text. Use it only "
                    "when the caller names the family explicitly."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": properties,
                    "required": ["field_0"],
                },
            }
        )
    return tools


if __name__ == "__main__":
    main()
