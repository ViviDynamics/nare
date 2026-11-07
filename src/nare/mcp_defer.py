"""MCP tool definitions deferred behind a search step (issue #242).

With deferral on, the request carries no MCP definitions. It carries three fixed
meta tools instead, and the model finds a tool, loads its definition and then
calls it through `mcp_call`:

- `mcp_search` lists deferred tools whose name or description has every word
  of the query. It returns names and short descriptions, and loads nothing.
- `mcp_load` returns the full definitions of up to 8 named tools and marks them
  loaded for the session. The definitions arrive as a tool result, at the end
  of the transcript, never in the request's prefix.
- `mcp_call` runs a loaded tool. Its arguments name the tool and carry its
  arguments, so the call still goes through the allowlist and the approval seam
  under the tool's real name.

Why a search step and not the definitions themselves. The tools array is what
signed thinking is bound to, and the prompt cache keys on its prefix. Appending
a loaded tool to the array mid-run would change both. So the array holds the
same entries from the first turn to the last, in the same order, and a resume
with the same configuration sends the same array again.

This module holds the pure parts: the meta tools' schemas, the search and load
texts, and the digest that says whether a loaded definition is still the one
the model saw. The dispatch that uses them lives in tools.py, beside dispatch.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nare.mcp import ExternalTool

SEARCH = "mcp_search"
LOAD = "mcp_load"
CALL = "mcp_call"
META_TOOLS = (SEARCH, LOAD, CALL)

# Bounds that keep the model's own requests small. A search that matches more
# than this says how many, and a load of more names than this is refused.
SEARCH_LIMIT = 20
LOAD_LIMIT = 8
DESCRIPTION_WIDTH = 120

SCHEMAS: list[dict[str, Any]] = [
    {
        "name": SEARCH,
        "description": (
            "Search the MCP tools that are not in your tool list yet. Each match "
            "is listed by name with a short description; it does not include the "
            "parameters. Every word of the query must appear in the name or the "
            "description. An empty query lists the tools, up to 20. Use "
            "mcp_load to get a tool's parameters before calling it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Words to match against tool names and descriptions."
                    ),
                }
            },
        },
    },
    {
        "name": LOAD,
        "description": (
            "Load the full definitions (description and input schema) of tools "
            "found with mcp_search, so that mcp_call can run them. Give up to 8 "
            "names. A loaded tool stays loaded for the rest of the session."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "The full tool names, as mcp_search listed them.",
                }
            },
            "required": ["names"],
        },
    },
    {
        "name": CALL,
        "description": (
            "Call an MCP tool that mcp_load has loaded. name is the tool's full "
            "name. arguments is an object that matches the tool's input schema."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "arguments": {"type": "object"},
            },
            "required": ["name"],
        },
    },
]


def schema_digest(schema: Mapping[str, Any]) -> str:
    """The identity of a definition: key order and whitespace are not a change."""
    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def deferred(
    external: Mapping[str, ExternalTool], allowed: Iterable[str]
) -> dict[str, ExternalTool]:
    """The MCP tools deferral holds back: connected, allowed, and from a server.

    Caller tools are never deferred. They are in-process, and a caller that
    registers one expects it in the request.
    """
    names = set(allowed)
    return {
        name: tool
        for name, tool in external.items()
        if tool.source == "mcp" and name in names
    }


def search_text(query: str, pool: Mapping[str, ExternalTool]) -> tuple[str, bool]:
    """The matches for a query, by name, with a short description each.

    Returns (text, found). A query matches a tool when each of its words occurs
    in the tool's name or description, case-insensitively.
    """
    words = query.lower().split()
    matches = []
    for name in sorted(pool):
        schema = pool[name].schema
        haystack = f"{name} {schema.get('description') or ''}".lower()
        if all(word in haystack for word in words):
            matches.append((name, schema.get("description") or ""))
    if not matches:
        return f"no MCP tool matches {query!r}", False
    lines = [
        f"{name}: {_short(description)}" for name, description in matches[:SEARCH_LIMIT]
    ]
    if len(matches) > SEARCH_LIMIT:
        lines.append(f"... {len(matches) - SEARCH_LIMIT} more match; narrow the query.")
    return "\n".join(lines), True


def _short(text: str) -> str:
    flat = " ".join(text.split())
    if len(flat) <= DESCRIPTION_WIDTH:
        return flat
    return flat[: DESCRIPTION_WIDTH - 3] + "..."


def load_text(
    names: list[str], pool: Mapping[str, ExternalTool]
) -> tuple[str, list[str]]:
    """The definitions of the named tools, as JSON, and the names loaded.

    Returns (text, loaded). Names the pool does not hold are listed under
    `not_found`, so a model that guessed a name learns that and nothing else.
    """
    loaded: list[str] = []
    not_found: list[str] = []
    tools = []
    for name in dict.fromkeys(names):
        if name in pool:
            schema = pool[name].schema
            loaded.append(name)
            tools.append(
                {
                    "name": name,
                    "description": schema.get("description") or "",
                    "input_schema": schema.get("input_schema"),
                }
            )
        else:
            not_found.append(name)
    body = {"tools": tools, "not_found": not_found}
    return json.dumps(body, ensure_ascii=False), loaded


def reconcile_loaded(
    loaded: Mapping[str, str], pool: Mapping[str, ExternalTool]
) -> tuple[dict[str, str], list[str]]:
    """Keep the loaded entries whose definition is still the one loaded.

    An entry is dropped when its tool is no longer deferred in this run, or when
    its definition has changed since the model loaded it. The model then has a
    definition in its transcript that is not the one a call would run against,
    so it must load the tool again. Returns (kept, dropped names).
    """
    kept: dict[str, str] = {}
    dropped: list[str] = []
    for name, digest in loaded.items():
        tool = pool.get(name)
        if tool is not None and digest == schema_digest(tool.schema):
            kept[name] = digest
        else:
            dropped.append(name)
    return kept, sorted(dropped)
