"""Read-only image results, wire formats and persistence stay deterministic."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from nare.tools import Policy, dispatch
from nare.transport import ToolCall


def picture(path: Path, format: str = "PNG", size: tuple[int, int] = (16, 16)) -> bytes:
    Image.new("RGB", size, "blue").save(path, format=format)
    return path.read_bytes()


@pytest.mark.asyncio
@pytest.mark.parametrize("format,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg")])
async def test_read_image_preserves_bytes_and_only_read_permission(
    tmp_path: Path, format: str, mime: str
) -> None:
    path = tmp_path / "image.bin"
    original = picture(path, format)
    result = await dispatch(
        ToolCall("c1", "read", {"path": "image.bin"}),
        Policy(frozenset({"read"}), tmp_path, image_input=True),
    )
    assert not result["is_error"], result
    assert isinstance(result["content"], str)
    source = result["image"]["source"]
    assert source["type"] == "base64" and source["media_type"] == mime
    assert base64.b64decode(source["data"], validate=True) == original
    assert result["image"]["width"] == 16 and result["image"]["height"] == 16
    assert path.read_bytes() == original


@pytest.mark.asyncio
async def test_unsupported_image_names_transport_and_model(tmp_path: Path) -> None:
    picture(tmp_path / "chart.png")
    result = await dispatch(
        ToolCall("c1", "read", {"path": "chart.png"}),
        Policy(
            frozenset({"read"}), tmp_path, image_model="TextTransport/model=text-only"
        ),
    )
    assert result["is_error"] and "image" not in result
    assert "TextTransport/model=text-only" in result["content"]
    assert "image input" in result["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("symlink", [False, True])
async def test_image_cannot_leave_root(tmp_path: Path, symlink: bool) -> None:
    picture(tmp_path / "outside.png")
    root = tmp_path / "root"
    root.mkdir()
    if symlink:
        (root / "link.png").symlink_to(tmp_path / "outside.png")
    result = await dispatch(
        ToolCall("c1", "read", {"path": "link.png" if symlink else "../outside.png"}),
        Policy(frozenset({"read"}), root, image_input=True),
    )
    assert result["is_error"] and "outside the root" in result["content"]
    assert "image" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["denied", "unapproved"])
async def test_image_respects_tool_permission_and_approval(
    tmp_path: Path, case: str
) -> None:
    picture(tmp_path / "chart.png")
    policy = Policy(
        frozenset() if case == "denied" else frozenset({"read"}),
        tmp_path,
        image_input=True,
    )
    result = await dispatch(
        ToolCall("c1", "read", {"path": "chart.png"}),
        policy,
        lambda name, args: case != "unapproved",
    )
    assert result["is_error"] and "image" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["bytes", "pixels", "edge", "corrupt", "gif"])
async def test_image_limits_and_validation_return_tool_errors(
    tmp_path: Path, case: str
) -> None:
    from nare.tools import MAX_IMAGE_BYTES

    path = tmp_path / "chart.png"
    if case == "bytes":
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * MAX_IMAGE_BYTES)
    elif case == "pixels":
        picture(path, size=(5000, 5000))
    elif case == "edge":
        picture(path, size=(8001, 1))
    elif case == "corrupt":
        path.write_bytes(b"\x89PNG\r\n\x1a\ninvalid")
    else:
        picture(path, format="GIF")
    result = await dispatch(
        ToolCall("c1", "read", {"path": str(path)}),
        Policy(frozenset({"read"}), tmp_path, image_input=True),
    )
    assert result["is_error"] and "image" not in result
    assert {
        "bytes": "bytes",
        "pixels": "pixels",
        "edge": "8000",
        "corrupt": "image",
        "gif": "PNG/JPEG",
    }[case] in result["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("rail", ["openai", "anthropic"])
@pytest.mark.parametrize("enabled", [False, True])
async def test_native_image_payload_and_disabled_history(
    tmp_path: Path, rail: str, enabled: bool
) -> None:
    from nare.session import Message
    from nare.transport.anthropic import messages_from as anthropic_messages
    from nare.transport.openai import messages_from as openai_messages

    picture(tmp_path / "chart.png")
    result = await dispatch(
        ToolCall("c1", "read", {"path": "chart.png"}),
        Policy(frozenset({"read"}), tmp_path, image_input=True),
    )
    messages = [
        Message(
            "user",
            [
                result,
                {
                    "type": "tool_result",
                    "tool_use_id": "c2",
                    "content": "text result",
                    "is_error": False,
                },
            ],
        )
    ]
    convert = openai_messages if rail == "openai" else anthropic_messages
    payload = convert(
        messages, image_input=enabled, image_model="rail/model=test-model"
    )
    if not enabled:
        assert "base64" not in str(payload)
        assert "rail/model=test-model" in str(payload)
    elif rail == "openai":
        assert [m["role"] for m in payload] == ["tool", "tool", "user"]
        url = payload[-1]["content"][-1]["image_url"]["url"]
        assert url == "data:image/png;base64," + result["image"]["source"]["data"]
    else:
        content = payload[0]["content"][0]["content"]
        assert content[-1] == {"type": "image", "source": result["image"]["source"]}
    assert "image" in messages[0].content[0], "serialization mutated saved history"


def test_saved_image_base64_is_opaque_but_text_is_redacted() -> None:
    from nare.session import Message, dumps, loads, new_session

    session = new_session("hi")
    session.messages.append(
        Message(
            "user",
            [
                {
                    "type": "tool_result",
                    "tool_use_id": "c1",
                    "is_error": False,
                    "content": "password: privatevalue",
                    "image": {
                        "type": "image",
                        "width": 16,
                        "height": 16,
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": "AAsecretAA==",
                        },
                    },
                }
            ],
        )
    )
    saved = loads(dumps(session))
    result = saved.messages[-1].content[0]
    assert result["content"] == "[redacted]"
    assert result["image"]["source"]["data"] == "AAsecretAA=="
    assert session.messages[-1].content[0]["content"] == "password: privatevalue"


def test_context_uses_image_pixels_and_compacts_old_images() -> None:
    from nare.compact import compact, estimate
    from nare.session import Message, new_session

    session = new_session("hi")
    image = {
        "type": "image",
        "width": 640,
        "height": 480,
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": "A" * 1024 * 1024,
        },
    }
    session.messages += [
        Message(
            "assistant", [{"type": "tool_use", "name": "read", "id": "c1", "input": {}}]
        ),
        Message(
            "user",
            [
                {
                    "type": "tool_result",
                    "tool_use_id": "c1",
                    "content": "image read",
                    "is_error": False,
                    "image": image,
                }
            ],
        ),
        Message("assistant", [{"type": "text", "text": "first"}]),
        Message("user", [{"type": "text", "text": "follow up"}]),
        Message("assistant", [{"type": "text", "text": "second"}]),
        Message("user", [{"type": "text", "text": "follow up again"}]),
    ]
    assert 1200 <= estimate(session) < 2000
    report = compact(session, 1600)
    assert report is not None and report.elided == 1
    assert "image" not in session.messages[2].content[0]
    assert "Rerun the tool" in session.messages[2].content[0]["content"]


@pytest.mark.asyncio
async def test_custom_text_transport_never_receives_saved_image_bytes() -> None:
    from dataclasses import asdict

    from nare.loop import run
    from nare.session import Message, Usage, new_session
    from nare.transport import Reply

    seen: list[dict[str, Any]] = []

    class TextTransport:
        model = "text-only"

        async def context_window(self) -> int:
            return 32000

        async def turn(
            self, messages: list[Message], tools: list[dict[str, Any]]
        ) -> Reply:
            seen.extend(asdict(message) for message in messages)
            return Reply(
                [{"type": "text", "text": "done"}],
                [],
                Usage(input=1, output=1),
                "end_turn",
            )

    session = new_session("hi")
    original = {
        "type": "image",
        "width": 16,
        "height": 16,
        "source": {"type": "base64", "media_type": "image/png", "data": "AAsecretAA=="},
    }
    session.messages.append(
        Message(
            "user",
            [
                {
                    "type": "tool_result",
                    "tool_use_id": "c1",
                    "content": "image read",
                    "image": original,
                }
            ],
        )
    )
    async for _ in run(session, transport=TextTransport()):
        pass
    assert session.status == "done"
    assert "base64" not in str(seen) and "image withheld" in str(seen)
    assert session.messages[1].content[0]["image"] == original


@pytest.mark.parametrize("long_text", [False, True])
def test_disabled_compaction_preserves_withheld_image(long_text: bool) -> None:
    from nare.compact import compact, estimate
    from nare.session import Message, new_session

    session = new_session("hi")
    image = {
        "type": "image",
        "width": 2048,
        "height": 2048,
        "source": {"type": "base64", "media_type": "image/png", "data": "AAsecretAA=="},
    }
    session.messages += [
        Message("assistant", [{"type": "tool_use", "name": "read", "id": "c1"}]),
        Message(
            "user",
            [
                {
                    "type": "tool_result",
                    "tool_use_id": "c1",
                    "is_error": False,
                    "content": "x" * 2000 if long_text else "image read",
                    "image": image,
                }
            ],
        ),
        Message("assistant", [{"type": "text", "text": "first"}]),
        Message("user", [{"type": "text", "text": "x" * 3200}]),
        Message("assistant", [{"type": "text", "text": "second"}]),
    ]
    before = estimate(session, image_input=False)
    report = compact(session, 1000, image_input=False)
    assert session.messages[2].content[0]["image"] == image
    assert (report is not None) == long_text
    assert estimate(session, image_input=False) <= before
