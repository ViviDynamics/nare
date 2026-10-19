"""Deterministic context elision, preserving task and tool-call pairing."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from nare.session import Message, Session, sent_back, without_sent_thinking

PREFIX = "[elided by nare:"

# The second tier: the write and edit fields compaction may elide, and what
# each stub says the call did.
_INPUT_STUBS: dict[str, dict[str, str]] = {
    "write": {"content": "wrote {chars} chars to {path}"},
    "edit": {
        "old": "replaced {chars} chars in {path}",
        "new": "wrote {chars} chars into {path}",
    },
}


def context_chars(messages: list[Message], *, image_input: bool = True) -> int:
    raw = [asdict(m) for m in messages]
    image_tokens = 0
    for message in raw:
        # Measure what is sent. Counting unsent thinking, which compaction
        # cannot elide, put a ~590-token request at 8235 and stopped the run
        # before its first call. An older nare saved last_input_chars with the
        # thinking in, so its prefix delta comes out negative by that much.
        message["content"] = [b for b in message["content"] if sent_back(b)]
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
    inputs: int = 0
    thinking: int = 0


def _drop_thinking(s: Session, start: int) -> int:
    """Remove the thinking that goes back to the model from message `start` on.

    Elision changes message `start`, so every signed or redacted thinking
    block from there on is stale (see `without_sent_thinking`). Putting a
    removed block back is invalid, so the blocks leave the session, not just
    the request. Each edit calls this, so the estimate sees the space it frees.
    """
    dropped = 0
    for message in s.messages[start:]:
        kept = without_sent_thinking(message.content)
        if len(kept) < len(message.content):
            dropped += len(message.content) - len(kept)
            message.content = kept
    return dropped


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
    thinking = 0
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
            if not isinstance(content, str) or (
                content.startswith(PREFIX) and not (image_input and "image" in block)
            ):
                continue
            stub = (
                f"{PREFIX} {len(content)} chars of output from turn {turn}. "
                "Rerun the tool if you need it.]"
            )
            if len(stub) >= len(content) and not (image_input and "image" in block):
                continue
            block["content"] = stub
            if image_input:
                block.pop("image", None)
            elided += 1
            thinking += _drop_thinking(s, i)
    inputs, dropped = _elide_inputs(s, window, protected_from, image_input=image_input)
    thinking += dropped
    # ponytail: model-written summaries may follow elision if measured runs
    # keep reaching the context stop. No extra model call is made here.
    return (
        CompactionReport(
            elided,
            before,
            estimate(s, image_input=image_input),
            window,
            inputs,
            thinking,
        )
        if elided or inputs
        else None
    )


def _elide_inputs(
    s: Session, window: int, protected_from: int, *, image_input: bool
) -> tuple[int, int]:
    """Elide old write contents and edit texts, oldest first.

    Returns the number of calls elided and of thinking blocks dropped.

    On the practice run write inputs were 46% of the context and tool results
    7.5%, so eliding results alone could not reach 60%. A failed call keeps
    its input: its stub would claim a write that never happened. `input`
    stays an object, so every call still pairs with its result.
    """
    failed = {
        b.get("tool_use_id")
        for m in s.messages
        if m.role == "user"
        for b in m.content
        if b.get("type") == "tool_result" and b.get("is_error")
    }
    inputs = 0
    thinking = 0
    turn = 0
    for i, message in enumerate(s.messages):
        if message.role != "assistant":
            continue
        turn += 1
        if i >= protected_from:
            break
        for block in message.content:
            stubs = _INPUT_STUBS.get(block.get("name", ""))
            args = block.get("input")
            if (
                block.get("type") != "tool_use"
                or stubs is None
                or not isinstance(args, dict)
                or block.get("id") in failed
            ):
                continue
            path = str(args.get("path", ""))
            pending: dict[str, str] = {}
            for name, did in stubs.items():
                value = args.get(name)
                if not isinstance(value, str) or value.startswith(PREFIX):
                    continue
                stub = (
                    f"{PREFIX} {did.format(chars=len(value), path=path)} "
                    f"at turn {turn}. Read the file if you need it.]"
                )
                if len(stub) < len(value):
                    pending[name] = stub
            if not pending:
                continue
            # estimate() serializes the whole session; run per block it cost
            # ~3.9 s per model call on a 280-turn session even with nothing
            # left to elide, so it runs only when this call can be elided.
            if estimate(s, image_input=image_input) <= window * 0.6:
                return inputs, thinking
            args.update(pending)
            inputs += 1
            thinking += _drop_thinking(s, i)
    return inputs, thinking
