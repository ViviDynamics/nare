"""A stdio MCP server for the benchmark: a lookup table, and a long report."""

from mcp.server.mcpserver import MCPServer

VALUES = {f"item-{i:02d}": (i * 13 + 5) % 97 for i in range(40)}
ANSWER = 4217
REPORT_ROWS = 1200

mcp = MCPServer("fanout")


@mcp.tool()
def lookup(key: str) -> str:
    """Return the value stored under a key."""
    if key not in VALUES:
        raise ValueError(f"no such key: {key}")
    return str(VALUES[key])


@mcp.tool()
def report(name: str) -> str:
    """Return a long report. Only one of its lines matters."""
    rows = [f"row {i}: " + "x" * 40 for i in range(REPORT_ROWS)]
    rows.insert(40, f"ANSWER={ANSWER}")
    return "\n".join(rows)


if __name__ == "__main__":
    mcp.run()
