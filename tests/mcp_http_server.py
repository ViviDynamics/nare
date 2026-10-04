"""Loopback MCP fixture for subprocess integration tests."""

from __future__ import annotations

import os
import sys

from mcp.server import MCPServer

server = MCPServer("fixture")


@server.tool()
def echo(text: str) -> str:
    """Echo text; reject accidental model credential inheritance."""
    if "ANTHROPIC_API_KEY" in os.environ or "OPENAI_API_KEY" in os.environ:
        raise ValueError("model credentials reached tool server")
    return text


if __name__ == "__main__":
    server.run(transport="streamable-http", host="127.0.0.1", port=int(sys.argv[1]))
