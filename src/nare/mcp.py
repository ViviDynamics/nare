"""Explicit MCP sources, with bounded requests and session-owned connections."""

from __future__ import annotations

import asyncio
import json
import math
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx2
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]*\Z")


@dataclass(frozen=True)
class Server:
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = 30

    def __post_init__(self) -> None:
        if (self.command is None) == (self.url is None):
            raise ValueError("MCP server needs exactly one of command or url")
        if (
            not isinstance(self.timeout, (int, float))
            or isinstance(self.timeout, bool)
            or self.timeout <= 0
        ):
            raise ValueError("MCP timeout must be a positive finite number")
        try:
            finite = math.isfinite(self.timeout)
        except OverflowError:
            finite = False
        if not finite:
            raise ValueError("MCP timeout must be a positive finite number")
        if self.command is not None and (
            not isinstance(self.command, str) or not self.command
        ):
            raise ValueError("MCP command must be a nonempty string")
        if not isinstance(self.args, list) or not all(
            isinstance(a, str) for a in self.args
        ):
            raise ValueError("MCP args must be an array of strings")
        for value in (self.env, self.headers):
            if not isinstance(value, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in value.items()
            ):
                raise ValueError("MCP env and headers must be string maps")
        if self.url is not None:
            if not isinstance(self.url, str):
                raise ValueError("MCP url must be a string")
            parsed = urlsplit(self.url)
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError("MCP url must be HTTP(S) without embedded credentials")
            if self.args or self.env:
                raise ValueError("MCP remote server cannot specify args or env")
        elif self.headers:
            raise ValueError("MCP stdio server cannot specify headers")


def parse_servers(value: Any) -> dict[str, Server]:
    if not isinstance(value, dict):
        raise ValueError("MCP config must be an object keyed by server alias")
    servers: dict[str, Server] = {}
    for alias, config in value.items():
        if not isinstance(alias, str) or not _NAME.fullmatch(alias) or "__" in alias:
            raise ValueError(
                "MCP alias must start with a letter; use letters, digits, _ or -; no __"
            )
        if not isinstance(config, dict) or set(config) - {
            "command",
            "args",
            "env",
            "url",
            "headers",
            "timeout",
        }:
            raise ValueError(f"invalid MCP configuration for {alias}")
        servers[alias] = Server(**config)
    return servers


@dataclass(frozen=True)
class ExternalTool:
    schema: dict[str, Any]
    call: Callable[[dict[str, Any]], Awaitable[tuple[str, bool]]]


async def _bounded[Result](
    request: Coroutine[Any, Any, Result], timeout: float
) -> Result:
    """Own the write/read deadline, including SDK cancellation backpressure."""
    task = asyncio.create_task(request)
    try:
        done, _ = await asyncio.wait({task}, timeout=timeout)
        if not done:
            raise TimeoutError(f"MCP request exceeded {timeout}s")
        return task.result()
    finally:
        if not task.done():
            task.cancel()
            # The SDK tries a courtesy cancellation on the same transport.
            # Give it a short grace, then interrupt that write too: a wedged
            # server must not turn timeout handling into another stalled call.
            await asyncio.wait({task}, timeout=0.1)
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


def _tool(
    session: ClientSession, name: str, schema: dict[str, Any], timeout: float
) -> ExternalTool:
    async def call(args: dict[str, Any]) -> tuple[str, bool]:
        result = await _bounded(session.call_tool(name, args), timeout)
        if not isinstance(result, types.CallToolResult):
            raise ValueError("MCP tool did not return a completed tool result")
        parts = [
            c.text
            if isinstance(c, types.TextContent)
            else json.dumps(c.model_dump(mode="json", exclude_none=True))
            for c in result.content
        ]
        if result.structured_content is not None:
            parts.append(json.dumps(result.structured_content))
        return "\n".join(parts), result.is_error

    return ExternalTool(schema, call)


@asynccontextmanager
async def connect(
    servers: Mapping[str, Server],
) -> AsyncIterator[dict[str, ExternalTool]]:
    stack = AsyncExitStack()
    try:
        tools: dict[str, ExternalTool] = {}
        for alias, config in servers.items():
            if config.command is not None:
                streams = await stack.enter_async_context(
                    stdio_client(
                        StdioServerParameters(
                            command=config.command, args=config.args, env=config.env
                        )
                    )
                )
            else:
                assert config.url is not None
                client = await stack.enter_async_context(
                    httpx2.AsyncClient(
                        headers=config.headers,
                        timeout=httpx2.Timeout(config.timeout),
                        follow_redirects=False,
                    )
                )
                streams = await stack.enter_async_context(
                    streamable_http_client(config.url, http_client=client)
                )
            session = await stack.enter_async_context(
                ClientSession(*streams, read_timeout_seconds=config.timeout)
            )
            await _bounded(
                _discover(session, alias, config.timeout, tools), config.timeout
            )
        yield tools
    finally:
        # Close normally: passing GeneratorExit through SDK task groups wraps
        # an ordinary early close into an exception group.
        await stack.aclose()


async def _discover(
    session: ClientSession, alias: str, timeout: float, tools: dict[str, ExternalTool]
) -> None:
    await session.initialize()
    cursor: str | None = None
    seen: set[str] = set()
    while True:
        page = await session.list_tools(
            params=types.PaginatedRequestParams(cursor=cursor)
        )
        for item in page.tools:
            name = f"{alias}__{item.name}"
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or name in tools:
                raise ValueError(f"invalid or duplicate MCP tool name: {name}")
            tools[name] = _tool(
                session,
                item.name,
                {
                    "name": name,
                    "description": item.description or "",
                    "input_schema": item.input_schema,
                },
                timeout,
            )
        cursor = page.next_cursor
        if cursor is None:
            break
        if cursor in seen:
            raise ValueError(f"MCP tools/list repeats cursor for {alias}")
        seen.add(cursor)
