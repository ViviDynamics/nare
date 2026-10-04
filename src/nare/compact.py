"""Deterministic context elision, preserving task and tool-call pairing."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from nare.session import Message, Session

PREFIX = "[elided by nare:"


def context_chars(messages: list[Message], *, image_input: bool = True) -> int:
    raw = [asdict(m) for m in messages]
    image_tokens = 0
    for message in raw:
        for block in message["content"]:
            image = block.get("image") if block.get("type") == "tool_result" else None
            if isinstance(image, dict):
                if not image_input:
                    block.pop("image")
                    continue
                image.get("source", {}).pop("data", None)
                width, height = (
                    int(image.get("width", 8000)),
                    int(image.get("height", 8000)),
                )
                image_tokens += 4 * ((width + 31) // 32) * ((height + 31) // 32)
    return len(json.dumps(raw)) + 4 * image_tokens


def _chars(s: Session, end: int | None = None, *, image_input: bool = True) -> int:
    return context_chars(s.messages[:end], image_input=image_input)


def estimate(s: Session, *, image_input: bool = True) -> float:
    if (
        s.last_input_tokens == 0
        or s.last_input_image_input is not None
        and s.last_input_image_input != image_input
    ):
        return _chars(s, image_input=image_input) / 4
    # Include new messages and changes to a measured prefix (elision, or user
    # feedback merged into a trailing user message). Saved boundaries make
    # the estimate stable across resumes. This is a heuristic, not a tokenizer.
    prefix_delta = (
        _chars(s, s.last_input_messages, image_input=image_input) - s.last_input_chars
    )
    appended = s.messages[s.last_input_messages :]
    appended_chars = context_chars(appended, image_input=image_input) if appended else 0
    return max(0, s.last_input_tokens + (prefix_delta + appended_chars) / 4)


@dataclass(frozen=True)
class CompactionReport:
    elided: int
    before: float
    after: float
    window: int


def compact(
    s: Session, window: int, *, image_input: bool = True
) -> CompactionReport | None:
    before = estimate(s, image_input=image_input)
    if before < window * 0.8:
        return None
    assistant_indices = [i for i, m in enumerate(s.messages) if m.role == "assistant"]
    protected_from = assistant_indices[-2] if len(assistant_indices) >= 2 else 0
    calls = {
        b.get("id"): b.get("name")
        for m in s.messages
        if m.role == "assistant"
        for b in m.content
        if b.get("type") == "tool_use"
    }
    elided = 0
    turn = 0
    for i, message in enumerate(s.messages):
        if message.role == "assistant":
            turn += 1
            continue
        if i == 0 or i >= protected_from:
            continue
        for block in message.content:
            if estimate(s, image_input=image_input) <= window * 0.6:
                break
            if (
                block.get("type") != "tool_result"
                or block.get("is_error")
                or calls.get(block.get("tool_use_id")) == "ask"
            ):
                continue
            content = block.get("content", "")
            if not isinstance(content, str) or content.startswith(PREFIX):
                continue
            stub = (
                f"{PREFIX} {len(content)} chars of output from turn {turn}. "
                "Rerun the tool if you need it.]"
            )
            if len(stub) >= len(content) and "image" not in block:
                continue
            block["content"] = stub
            block.pop("image", None)
            elided += 1
    # ponytail: model-written summaries may follow elision if measured runs
    # keep reaching the context stop. No extra model call is made here.
    return (
        CompactionReport(elided, before, estimate(s, image_input=image_input), window)
        if elided
        else None
    )
