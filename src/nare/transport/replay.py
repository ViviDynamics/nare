"""The scripted transport: `--provider fake --replay FILE`.

Replays a list of replies a caller wrote down, so a caller's own tests can run
nare end to end with no key and no network, and reach the paths a real model
will not produce on demand: a blocked run, a truncated turn, a transport
error. Everything above the transport runs for real, so the events, the
session and the exit code are the ones a real run gives.

Imports no vendor code. The script is checked whole when it is loaded, so a
typo in a fixture is a startup failure naming the reply, not a run that goes
wrong three turns in.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import AsyncGenerator, Mapping
from pathlib import Path
from typing import Any, cast, get_args

from nare.session import Message, Usage
from nare.transport import Delta, Reply, StopReason, ToolCall

_REPLY_KEYS = frozenset(
    {"text", "thinking", "tool_calls", "stop_reason", "usage", "cost", "error"}
)
_CALL_KEYS = frozenset({"id", "name", "args"})
_USAGE_KEYS = ("input", "output", "cache_read", "cache_write")
_STOP_REASONS: tuple[str, ...] = get_args(StopReason)


def _unknown(where: str, raw: Mapping[str, Any], allowed: frozenset[str]) -> None:
    for key in raw:
        if key not in allowed:
            raise ValueError(
                f"{where}unknown key {key!r}; expected one of: "
                + ", ".join(sorted(allowed))
            )


def _string(where: str, raw: Mapping[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{where}{key} must be a string")
    return value


def _usage(where: str, raw: Any) -> Usage:
    if raw is None:
        return Usage()
    if not isinstance(raw, dict):
        raise ValueError(f"{where}usage must be a JSON object")
    _unknown(f"{where}usage: ", raw, frozenset(_USAGE_KEYS))
    counts: list[int] = []
    for key in _USAGE_KEYS:
        value = raw.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{where}usage.{key} must be a nonnegative integer")
        counts.append(value)
    return Usage(
        input=counts[0], output=counts[1], cache_read=counts[2], cache_write=counts[3]
    )


def _cost(where: str, raw: Any) -> float | None:
    if raw is None:
        return None
    if (
        isinstance(raw, bool)
        or not isinstance(raw, (int, float))
        or not math.isfinite(raw)
        or raw < 0
    ):
        raise ValueError(f"{where}cost must be a finite nonnegative number")
    return float(raw)


def _calls(where: str, raw: Any, number: int) -> list[ToolCall]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{where}tool_calls must be a list")
    calls: list[ToolCall] = []
    seen: set[str] = set()
    for index, call in enumerate(raw, start=1):
        at = f"{where}tool call {index}"
        if not isinstance(call, dict):
            raise ValueError(f"{at} must be a JSON object")
        _unknown(f"{at}: ", call, _CALL_KEYS)
        name = call.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"{at} needs a name")
        args = call.get("args", {})
        if not isinstance(args, dict):
            raise ValueError(f"{at}: args must be a JSON object")
        call_id = _string(f"{at}: ", call, "id") or f"call_{number}_{index}"
        if call_id in seen:
            raise ValueError(f"{at}: id {call_id!r} is already used in this reply")
        seen.add(call_id)
        calls.append(ToolCall(id=call_id, name=name, args=args))
    return calls


def _reply(raw: Any, number: int) -> Reply | str:
    """A Reply, or the text of the error this turn raises."""
    where = f"reply {number}: "
    if not isinstance(raw, dict):
        raise ValueError(f"reply {number} must be a JSON object")
    _unknown(where, raw, _REPLY_KEYS)
    if "error" in raw:
        error = _string(where, raw, "error")
        if len(raw) > 1:
            raise ValueError(f"{where}error stands alone: a failed turn has no reply")
        return error or ""
    text = _string(where, raw, "text")
    thinking = _string(where, raw, "thinking")
    calls = _calls(where, raw.get("tool_calls"), number)
    stop = raw.get("stop_reason", "tool_use" if calls else "end_turn")
    if stop not in _STOP_REASONS:
        raise ValueError(
            f"{where}stop_reason must be one of: {', '.join(_STOP_REASONS)}"
        )
    content: list[dict[str, Any]] = []
    if thinking is not None:
        content.append({"type": "thinking", "thinking": thinking})
    if text is not None:
        content.append({"type": "text", "text": text})
    content.extend(
        {"type": "tool_use", "id": c.id, "name": c.name, "input": c.args} for c in calls
    )
    return Reply(
        content=content,
        tool_calls=calls,
        usage=_usage(where, raw.get("usage")),
        stop_reason=cast(StopReason, stop),
        cost=_cost(where, raw.get("cost")),
    )


def load_script(script: Any) -> list[Reply | str]:
    """Check a whole script and return its turns: a Reply, or an error text.

    Raises ValueError naming the reply and the field that is wrong.
    """
    if not isinstance(script, dict):
        raise ValueError("a replay script must be a JSON object with a replies list")
    _unknown("", script, frozenset({"replies"}))
    if "replies" not in script:
        raise ValueError("a replay script needs a replies list")
    replies = script["replies"]
    if not isinstance(replies, list):
        raise ValueError("replies must be a list")
    if not replies:
        raise ValueError("a replay script needs at least one reply")
    return [_reply(raw, number) for number, raw in enumerate(replies, start=1)]


class ReplayTransport:
    """Replays a script, one reply per turn, and fails the turn after the last.

    `model` is recorded as given and changes nothing. `streaming` makes each
    reply's thinking and text arrive as deltas first, as a streaming backend
    sends them.
    """

    def __init__(
        self,
        script: Any,
        *,
        model: str = "replay",
        streaming: bool = False,
    ) -> None:
        self._turns = load_script(script)
        self._next = 0
        self.model = model
        self.streaming = streaming

    @classmethod
    def from_file(
        cls,
        path: str | os.PathLike[str],
        *,
        model: str = "replay",
        streaming: bool = False,
    ) -> ReplayTransport:
        """Read a JSON script. Raises OSError or ValueError naming the file."""
        try:
            loaded = json.loads(Path(path).read_text(encoding="utf-8"))
            return cls(loaded, model=model, streaming=streaming)
        except ValueError as exc:
            raise ValueError(f"--replay {path}: {exc}") from exc

    async def context_window(self) -> int | None:
        return None

    async def turn(self, messages: list[Message], tools: list[dict[str, Any]]) -> Reply:
        if self._next >= len(self._turns):
            raise RuntimeError(
                f"replay script has no reply for turn {self._next + 1}: "
                f"it holds {len(self._turns)}"
            )
        scripted = self._turns[self._next]
        self._next += 1
        if isinstance(scripted, str):
            raise RuntimeError(scripted)
        return scripted

    async def stream_turn(
        self, messages: list[Message], tools: list[dict[str, Any]]
    ) -> AsyncGenerator[Delta | Reply, None]:
        reply = await self.turn(messages, tools)
        for block in reply.content:
            if block["type"] == "thinking":
                yield Delta("thinking", block["thinking"])
            elif block["type"] == "text":
                yield Delta("progress", block["text"])
        yield reply
