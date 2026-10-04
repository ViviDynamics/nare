"""Read-only image results, wire formats and persistence stay deterministic."""

from __future__ import annotations

import base64
from pathlib import Path

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
