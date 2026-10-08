from typing import Any

from nare.tui.approve import Approver


class Keys:
    def __init__(self, *keys: str) -> None:
        self.keys = list(keys)
        self.asked: list[str] = []

    async def __call__(self, tool: str, args: dict[str, Any]) -> str:
        self.asked.append(tool)
        return self.keys.pop(0)


async def test_read_and_ask_never_prompt() -> None:
    keys = Keys()
    approver = Approver(keys)
    assert await approver("read", {"path": "a"}) is True
    assert await approver("ask", {"questions": ["q"]}) is True
    assert keys.asked == []


async def test_y_allows_once_and_n_denies() -> None:
    keys = Keys("y", "n")
    approver = Approver(keys)
    assert await approver("bash", {"command": "ls"}) is True
    assert await approver("bash", {"command": "rm x"}) is False
    assert keys.asked == ["bash", "bash"]
    assert approver.always == set()


async def test_a_allows_this_tool_from_now_on() -> None:
    keys = Keys("a", "y")
    approver = Approver(keys)
    assert await approver("write", {"path": "a", "content": ""}) is True
    assert await approver("write", {"path": "b", "content": ""}) is True
    assert await approver("edit", {"path": "a", "old": "x", "new": "y"}) is True
    assert keys.asked == ["write", "edit"]
    assert approver.always == {"write"}
