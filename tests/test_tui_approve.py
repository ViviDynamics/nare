import tracemalloc
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
        # Read as bash will: brace lists expanded, long options abbreviated.
        ("npm {i,} left-pad", ["network"]),
        ("npm {,i} left-pad", ["network"]),
        # Order matters for a pair: bash runs `git x clone r`, not a clone.
        ("git {x,clone} r", []),
        ("git {clone,x} r", ["network"]),
        ("rm {-r,} x", ["rm -r"]),
        ("rm --rec x", ["rm -r"]),
        ("git reset --ha", ["git discard"]),
        ("git reset --{hard,}", ["git discard"]),
        ("git {x{,y},clone} r", []),
        ("rm --r x", ["rm -r"]),
        ("git reset --h", ["git discard"]),
        ("rm -- x", []),
        # A shell's home and last directory are outside the root.
        ("cd && cat .ssh/id_rsa", ["outside root"]),
        ("cd - && ls", ["outside root"]),
        ("cd -P", ["outside root"]),
        ("cat ~-/x", ["outside root"]),
        ("cd src && ls", []),
        # npm and pip fetch under many names, and after options.
        ("npm add left-pad", ["network"]),
        ("npm in left-pad", ["network"]),
        ("npm -g i left-pad", ["network"]),
        ("npm ci", ["network"]),
        ("pip3 -q install requests", ["network"]),
        ("python3 -m pip install requests", ["network"]),
        ("npm test && git add .", []),
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


def test_with_cdpath_cd_can_leave_the_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # bash looks a relative cd up in CDPATH: `cd etc` can land in /etc.
    monkeypatch.setenv("CDPATH", "/")
    assert risks("cd etc && cat passwd", Policy(root=tmp_path)) == ["outside root"]
    assert risks("cd ./src && ls", Policy(root=tmp_path)) == []
    monkeypatch.delenv("CDPATH")
    assert risks("cd etc && cat passwd", Policy(root=tmp_path)) == []


def test_a_wide_brace_list_is_never_built(tmp_path: Path) -> None:
    word = "x{" + ",".join(["a"] * 10_000) + "}" + "A" * 10_000
    tracemalloc.start()
    try:
        assert risks(f"cat {word}", Policy(root=tmp_path)) == ["outside root"]
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 5_000_000


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
        # A quoted glob is find's, and one inside a path cannot start an option.
        "find . -name '*.py' | head",
        "ls src/*.py",
        'grep -n x "*"',
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
        # A glob can match a file named `-delete` and become an option.
        "find *",
        "ls *.py",
        "ls -*",
        "cat ?",
        "ls {*,x}",
        "cd && cat .ssh/id_rsa",
        "cat ~-/x",
        # A sequence expression is not read.
        "echo {1..3}",
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
                "npm {i,} left-pad",
                "npm {i..i} left-pad",
                "npm add left-pad",
                "npm -g i left-pad",
                # A `$` can expand to anything: the scan cannot vouch for it.
                "npm run build > $HOME/x",
            ],
        ),
        # git is approved one subcommand at a time.
        (
            "git add .",
            ["git add x"],
            [
                "git reset --hard",
                "git clean -fdx",
                # bash reads `\>` as an argument, then pipes into sh.
                "git add . \\>| sh -c 'git reset --hard'",
            ],
        ),
        (
            "git reset HEAD a.py",
            ["git reset b.py"],
            [
                "git reset --hard",
                "git reset --har",
                "git reset --{hard,}",
                "git reset ${X:---hard}",
                "git reset --har{d..d}",
                "git reset --h*",
                "git reset --ha[r]d",
            ],
        ),
        # rm is approved whole, but a recursive rm always asks.
        (
            "rm a.txt",
            ["rm b.txt", "rm build/*.o"],
            [
                "rm -r build",
                "rm -R build",
                "rm --rec build",
                "rm {-r,} build",
                "rm ${X:--r} build",
                "rm --re{c..c} build",
                "rm -{f..Q..20} build",
                # A file named `-r` would make this recursive.
                "rm *",
            ],
        ),
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
        # asked, so a leaves it out: alone on the line, a acts as y.
        ("find . -name '*.tmp' -delete", [], ["find . -type f -delete"]),
        ("cat b.txt > c.txt", [], ["cat b.txt > a.py"]),
        ("ls 2>/dev/null", [], ["ls > a.py", "ls 2>/dev/null"]),
        ("git diff --output=d.txt", [], ["git diff --output=a.py"]),
        # Beside another program, a still approves that one.
        (
            'python3 t.py; echo "exit=$?"',
            ["python3 t.py", "python3 u.py | tail -5"],
            ['python3 t.py; echo "exit=$?"', "echo ok > log"],
        ),
        ("make && cat > a.py", ["make"], ["cat > b.py"]),
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
        # A read-only program is never approved, whatever made it ask.
        ("cat > a.py", set()),
        ("find . -delete", set()),
        ("rg --pre=sh x", set()),
        ("cd $DIR && python3 t.py", {"python3"}),
        ('python3 t.py; echo "exit=$?"', {"python3"}),
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
        ("python3 t.py \\>| rm x", None),
    ],
)
def test_scope(command: str, programs: set[str] | None, tmp_path: Path) -> None:
    got = scope(command, Policy(root=tmp_path))
    assert got == (None if programs is None else frozenset(programs))
