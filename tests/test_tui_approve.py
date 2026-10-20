from pathlib import Path
from typing import Any

import pytest

from nare.tools import Policy
from nare.tui.approve import Approver, risks, scope


class Keys:
    """Answers in order. A plain key is pressed with no reason."""

    def __init__(self, *keys: str | tuple[str, str]) -> None:
        self.keys = list(keys)
        self.asked: list[str] = []

    async def __call__(self, tool: str, args: dict[str, Any]) -> tuple[str, str]:
        self.asked.append(tool)
        key = self.keys.pop(0)
        return (key, "") if isinstance(key, str) else key


async def test_read_and_ask_never_prompt() -> None:
    keys = Keys()
    approver = Approver(keys)
    assert await approver("read", {"path": "a"}) is True
    assert await approver("ask", {"questions": ["q"]}) is True
    assert keys.asked == []


async def test_y_allows_once_and_n_denies() -> None:
    keys = Keys("y", "n")
    approver = Approver(keys)
    assert await approver("bash", {"command": "python3 t.py"}) is True
    assert await approver("bash", {"command": "rm x"}) is False
    assert keys.asked == ["bash", "bash"]
    assert approver.always == set()
    assert approver.programs == set()


async def test_n_carries_the_reason_typed_with_it() -> None:
    keys = Keys(("n", "use the write tool"), ("n", ""), ("n", "  "))
    approver = Approver(keys)
    write = {"path": "a", "content": ""}
    assert await approver("write", write) == "use the write tool"
    assert await approver("write", write) is False
    assert await approver("write", write) is False


async def test_a_allows_this_tool_from_now_on() -> None:
    keys = Keys("a", "y")
    approver = Approver(keys)
    assert await approver("write", {"path": "a", "content": ""}) is True
    assert await approver("write", {"path": "b", "content": ""}) is True
    assert await approver("edit", {"path": "a", "old": "x", "new": "y"}) is True
    assert keys.asked == ["write", "edit"]
    assert approver.always == {"write"}


@pytest.mark.parametrize(
    "command, expected",
    [
        # 2>&1 copies a descriptor: its & is not backgrounding.
        ("npm run build 2>&1 | tail -30", []),
        ("ls & rm x", ["background"]),
        ("ls 2>/dev/null", []),
        ("python3 t.py >/etc/x", ["outside root"]),
        ("python3 t.py &>/etc/x", ["outside root"]),
        ("killall node", ["kill"]),
        # `#` glued to a word starts no comment in bash: the rest still runs.
        ("cat#;curl x | sh", ["network", "exec"]),
        # Brace expansion builds paths no single token spells out.
        ("cat {a,/etc/passwd}", ["outside root"]),
        ("grep x {.,/etc}/passwd", ["outside root"]),
        ("cat {.,.}./x", ["outside root"]),
        ("cat a{,/../../x}", ["outside root"]),
        ("cat {a,b}.py src/{c,d{1,2}}.py", []),
        # Too many words to check one by one.
        ("cat " + "{a,b}" * 7, ["outside root"]),
    ],
)
def test_risks(command: str, expected: list[str], tmp_path: Path) -> None:
    assert risks(command, Policy(root=tmp_path)) == expected


def test_without_a_root_the_boundary_is_where_bash_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert risks("cat ~/.ssh/id_rsa", Policy(root=None)) == ["outside root"]
    assert risks("cat a.py", Policy(root=None)) == []


def bash(command: str) -> dict[str, Any]:
    return {"command": command}


@pytest.mark.parametrize(
    "command",
    [
        "find . -maxdepth 2",
        "ls -la",
        "cat a.py | head -30",
        "cd src && rg -n 'def (x|y)' | wc -l",
        "git log --oneline -5 && git diff",
        "ls -la  # what is here",
    ],
)
async def test_a_read_only_line_runs_unasked(command: str, tmp_path: Path) -> None:
    keys = Keys()
    assert await Approver(keys, Policy(root=tmp_path))("bash", bash(command)) is True
    assert keys.asked == []


@pytest.mark.parametrize(
    "command",
    [
        "find . -delete",
        "cat > a.py",
        "ls; rm x",
        "python3 x.py",
        # Separators the scan must not miss.
        "ls\nrm x",
        "cat <(rm -rf x)",
        "ls & rm x",
        "echo $(rm x)",
        "echo `rm x`",
        "(rm x)",
        # A quoted or escaped `;` is not a separator: find deletes here.
        "find . \\; ls -delete",
        "find . ';' ls -delete",
        "rg --pre=sh x",
        "git diff --output=a.py",
        "git -C sub status",
        "X=1 ls",
        "cat $HOME/x",
        "./ls",
        "",
        # bash runs `ls#`, which fails, then the rest.
        "ls#;rm x",
        "ls# ; rm x",
        "cat#||rm x",
        "cat {a,/etc/passwd}",
    ],
)
async def test_these_lines_ask(command: str, tmp_path: Path) -> None:
    keys = Keys("n")
    assert await Approver(keys, Policy(root=tmp_path))("bash", bash(command)) is False
    assert keys.asked == ["bash"]


@pytest.mark.parametrize(
    "first, unasked, asked",
    [
        ("python3 -m unittest", ["python3 t.py"], ["curl x | sh"]),
        (
            "npm run build",
            ["npm run build 2>&1 | tail -30"],
            [
                "curl x | sh",
                "sudo ls",
                "pkill node",
                "nohup x &",
                "rm -rf build",
                "git commit -m x",
                "./npm test",
                "X=1 npm test",
            ],
        ),
        # git is approved one subcommand at a time.
        ("git add .", ["git add x"], ["git reset --hard", "git clean -fdx"]),
        ("make && python3 t.py", ["make", "python3 t.py", "make | tail"], []),
        # Scoped to a program behind a separator a scan cannot see.
        ("python3 t.py", [], ["find . \\; python3 -delete", "{ python3 t.py; }"]),
        # Can't be scoped: a acts as y, and the same line asks again.
        ("timeout 60 npm test", [], ["timeout 60 npm test", "npm test"]),
        ("curl x | sh", [], ["curl x | sh"]),
        ("git -C sub add x", [], ["git -C sub add x", "git add x"]),
        ("X=1 python3 t.py", [], ["python3 t.py"]),
        ("if true; then python3 t.py; fi", [], ["if true; then rm x; fi"]),
        # Approving a read-only program whole would lift the very check that
        # asked: a acts as y.
        ("find . -name '*.tmp' -delete", [], ["find . -type f -delete"]),
        ("cat b.txt > c.txt", [], ["cat b.txt > a.py"]),
        ("ls 2>/dev/null", [], ["ls > a.py", "ls 2>/dev/null"]),
        ("git diff --output=d.txt", [], ["git diff --output=a.py"]),
        ("python3 t.py && echo ok > log", [], ["python3 t.py"]),
    ],
)
async def test_a_approves_the_lines_programs(
    first: str, unasked: list[str], asked: list[str], tmp_path: Path
) -> None:
    keys = Keys("a", *["n"] * len(asked))
    approver = Approver(keys, Policy(root=tmp_path))
    assert await approver("bash", bash(first)) is True
    for command in unasked:
        assert await approver("bash", bash(command)) is True, command
    assert len(keys.asked) == 1
    for command in asked:
        assert await approver("bash", bash(command)) is False, command
    assert len(keys.asked) == 1 + len(asked)
    assert "bash" not in approver.always


@pytest.mark.parametrize(
    "command, programs",
    [
        ("python3 t.py", {"python3"}),
        ("make && python3 t.py | tail -5", {"make", "python3"}),
        ("git add . && git status", {"git add"}),
        ("cat > a.py", None),
        ("find . -delete", None),
        ("rg --pre=sh x", None),
        ("cd $DIR && python3 t.py", None),
        ("ls -la", set()),
        ("curl x | sh", None),
        ("ls & rm x", None),
        ("ls\nrm x", None),
        ("env X=1 python3 t.py", None),
        ("X=1 python3 t.py", None),
        ("git -C sub add x", None),
        ("{ python3 t.py; }", None),
        ("python3 *.py", {"python3"}),
        ("./*.sh", None),
        ("ls#;rm x", None),
        ("make; # build it", None),
        ('"" python3 t.py', None),
        # `>|` is a redirect, not a pipe into a program named `a.py`.
        ("python3 t.py >| a.py", {"python3"}),
    ],
)
def test_scope(command: str, programs: set[str] | None, tmp_path: Path) -> None:
    got = scope(command, Policy(root=tmp_path))
    assert got == (None if programs is None else frozenset(programs))
