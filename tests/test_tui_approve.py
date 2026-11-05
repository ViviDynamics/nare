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


async def test_read_list_search_and_ask_never_prompt() -> None:
    keys = Keys()
    approver = Approver(keys)
    assert await approver("read", {"path": "a"}) is True
    assert await approver("list", {"path": "."}) is True
    assert await approver("search", {"pattern": "x"}) is True
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
        # git throws work away under many spellings.
        ("git checkout .", ["git discard"]),
        ("git checkout -- a.py", ["git discard"]),
        ("git checkout -f main", ["git discard"]),
        ("git checkout -qf main", ["git discard"]),
        ("git checkout --forc main", ["git discard"]),
        ("git checkout -p", ["git discard"]),
        ("git checkout --pathspec-from-file=paths", ["git discard"]),
        ("git checkout main a.py", ["git discard"]),
        ("git checkout '*.py'", ["git discard"]),
        ("git checkout :/", ["git discard"]),
        # -B resets a branch that exists, and its own commits are left behind.
        ("git checkout -B main", ["git discard"]),
        ("git checkout main", []),
        ("git checkout -", []),
        ("git checkout -b feat origin/main", []),
        ("git checkout --orphan=gh-pages", []),
        ("git checkout main 2>/dev/null", []),
        ("git restore a.py", ["git discard"]),
        ("git restore --staged --worktree a.py", ["git discard"]),
        ("git restore -SW a.py", ["git discard"]),
        # After `--` a word is a path, never an option.
        ("git restore -- --staged", ["git discard"]),
        ("git restore --staged a.py", []),
        ("git restore -S --source=HEAD~ a.py", []),
        ("git switch -f main", ["git discard"]),
        ("git switch --discard-changes main", ["git discard"]),
        ("git switch --disc main", ["git discard"]),
        ("git switch -C feat", ["git discard"]),
        ("git switch -c feat", []),
        ("git stash drop", ["git discard"]),
        ("git stash clear", ["git discard"]),
        ("git stash", []),
        ("git stash pop", []),
        ("git branch -D main", ["git discard"]),
        ("git branch -d --force main", ["git discard"]),
        ("git branch -f main HEAD~", ["git discard"]),
        ("git branch -M feat main", ["git discard"]),
        ("git branch -d feat", []),
        ("git branch new", []),
        # git named by its path is still git.
        ("bin/git checkout .", ["git discard"]),
        ("bin/git push", ["git commit"]),
        # git reads these as the paths they normalize or unescape to.
        ("git checkout zzz/..", ["git discard"]),
        ("git checkout zzz/../a.py", ["git discard"]),
        ("git checkout 'a\\.py'", ["git discard"]),
        ("git reset --merge", ["git discard"]),
        ("git reset --mer", ["git discard"]),
        ("git reset --keep HEAD~", []),
        # An option that takes a value can take `--` as it, and git reads on.
        ("git branch --sort -- -D main", ["git discard"]),
        ("git branch --format -- -f x main", ["git discard"]),
        ("git reset --pathspec-from-file -- --hard", ["git discard"]),
        ("git restore --staged --pathspec-from-file -- --worktree", ["git discard"]),
        # Here `--staged` is the file the paths are read from.
        ("git restore --pathspec-from-file --staged", ["git discard"]),
        ("git restore --staged --source HEAD~ a.py", []),
        # A short option that takes a value takes the rest of its word.
        ("git restore -sStable a.py", ["git discard"]),
        ("git checkout -bfeat", []),
        ("git switch -cfix-login", []),
        ("git branch -ufork/main", []),
        # git's own options and a redirection can come before the subcommand.
        ("git -C . checkout .", ["git discard"]),
        ("git --git-dir .git branch -D main", ["git discard"]),
        ("git --no-pager stash drop", ["git discard"]),
        ("git 2>/dev/null clean -fdx", ["git discard"]),
        ("git -c core.pager=cat push", ["git commit"]),
        # A redirection's target is not a name for checkout.
        ("git checkout main > /dev/null", []),
        ("git checkout main > /dev/null 2>&1", []),
        ("git checkout main &> /dev/null", []),
        ("git checkout -b fix origin/main > /dev/null", []),
        # bash ends a word at an unquoted `<` or `>`: git is handed `.`.
        ("git checkout .>/dev/null", ["git discard"]),
        ("git stash drop>/dev/null", ["git discard"]),
        ("git reset --hard>/dev/null", ["git discard"]),
        ("git switch --discard-changes>/dev/null main", ["git discard"]),
        ("python3 t.py>/etc/x", ["outside root"]),
        ("git checkout main>/dev/null", []),
        # A quoted `>` is a word, and the word after it is no redirection's.
        ("git reset '>' --hard", ["git discard"]),
        ("git branch '>' -D feat", ["git discard"]),
        # A redirection's target is not an option: bash writes a file `-b`.
        ("git checkout > -b main a.py", ["git discard"]),
        ("git restore > --staged a.py", ["git discard"]),
        ("git checkout --orphan side --no-orphan a.py", ["git discard"]),
        # A quoted `&>` is a word, not a redirection.
        ("git checkout '&>' a.py", ["git discard"]),
        # bash's `{name}>` redirects a descriptor it names.
        ("git {fd}>/dev/null checkout .", ["git discard"]),
        # bash splits words on spaces and tabs only.
        ("python3 t.py >/etc/x", ["outside root"]),
        ("pushd >/dev/null - && ls", ["outside root"]),
        ("pushd>/dev/null - && ls", ["outside root"]),
        # Finishing a merge, rebase, cherry-pick or am with --abort resets work.
        ("git merge feat", []),
        ("git merge --abort", ["git discard"]),
        ("git merge --abo", ["git discard"]),
        ("git rebase --abort", ["git discard"]),
        ("git cherry-pick --abort", ["git discard"]),
        ("git am --abort", ["git discard"]),
        # reflog delete and expire drop the entries a stash or branch needs.
        ("git reflog", []),
        ("git reflog show", []),
        ("git reflog delete 'refs/stash@{0}'", ["git discard"]),
        ("git reflog expire --all", ["git discard"]),
        # git rm refuses a modified file unless forced.
        ("git rm c.py", []),
        ("git rm -f b.py", ["git discard"]),
        # -r is recursive, so the rm badge shows too.
        ("git rm -rf dir", ["rm -r", "git discard"]),
        ("git rm --force b.py", ["git discard"]),
        ("git rm --forc b.py", ["git discard"]),
        ("git rm --cached a.py", []),
        ("git rm -- -f", []),
        # A redirection's target is not an option, as for checkout.
        ("git rm > -f c.py", []),
        # A nested brace list is read as bash reads it, or not at all.
        ("cat {x,{x,}", ["outside root"]),
        # A quoted brace is text, not a list.
        ("grep '{a,' a.py", []),
        # Other forms that throw work away.
        ("git rebase --skip", ["git discard"]),
        ("git revert --abort", ["git discard"]),
        ("git worktree remove --force wt", ["git discard"]),
        ("git worktree remove -f wt", ["git discard"]),
        ("git worktree remove wt", []),
        ("git worktree list", []),
        ("git mv -f a.py b.py", ["git discard"]),
        ("git mv a.py b.py", []),
        ("git checkout-index -f -a", ["git discard"]),
        ("git checkout-index -a", []),
        ("git read-tree --reset -u HEAD", ["git discard"]),
        ("git update-ref -d refs/stash", ["git discard"]),
        ("git update-ref refs/x HEAD", []),
        ("git gc --prune=now", ["git discard"]),
        ("git gc", []),
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


def test_checkout_of_a_file_or_directory_discards_its_changes(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("")
    (tmp_path / "src" / "lib").mkdir(parents=True)
    (tmp_path / "src" / "app.py").write_text("")
    (tmp_path / "src" / "lib" / "x.py").write_text("")
    policy = Policy(root=tmp_path)
    assert risks("git checkout a.py", policy) == ["git discard"]
    assert risks("git checkout src", policy) == ["git discard"]
    assert risks("git checkout src/./app.py", policy) == ["git discard"]
    assert risks("git checkout main", policy) == []
    # A new branch may share a name with a file.
    assert risks("git checkout -b src", policy) == []
    # A name is looked up wherever the line's cds may leave bash.
    assert risks("cd src && git checkout app.py", policy) == ["git discard"]
    assert risks("cd src && cd lib && git checkout x.py", policy) == ["git discard"]
    assert risks("cd src || git checkout a.py", policy) == ["git discard"]
    assert risks("cd src && git checkout main", policy) == []
    assert risks(f"cd {tmp_path} && git checkout main", policy) == []
    # A cd the scan cannot follow makes any name a path.
    assert risks("cd $DIR && git checkout main", policy) == ["git discard"]
    # A redirection is not where pushd goes.
    line = "pushd src>/dev/null && git checkout app.py"
    assert risks(line, policy) == ["git discard"]
    line = "pushd > /dev/null src && git checkout app.py"
    assert risks(line, policy) == ["git discard"]
    line = "pushd {fd}>/dev/null src && git checkout app.py"
    assert risks(line, policy) == ["git discard"]


def test_a_cd_through_a_link_is_read_both_ways(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "x.py").write_text("")
    (tmp_path / "other" / "deep").mkdir(parents=True)
    (tmp_path / "other" / "y.py").write_text("")
    (tmp_path / "sub" / "link").symlink_to(tmp_path / "other" / "deep")
    policy = Policy(root=tmp_path)
    # bash's cd takes `..` off the path as typed and lands in sub; cd -P
    # follows the link first and lands in other.
    assert risks("cd sub/link/.. && git checkout x.py", policy) == ["git discard"]
    assert risks("cd sub/link/.. && git checkout y.py", policy) == ["git discard"]
    assert risks("cd sub/link/.. && git checkout main", policy) == []


async def test_a_cd_into_the_root_does_not_make_checkout_ask(tmp_path: Path) -> None:
    keys = Keys("a")
    approver = Approver(keys, Policy(root=tmp_path))
    assert await approver("bash", bash("git checkout -b feat")) is True
    line = f"cd {tmp_path} && git checkout main"
    assert await approver("bash", bash(line)) is True
    assert keys.asked == ["bash"]


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
        # Quoted text is literal: a `$(` or a backslash inside single quotes, or
        # a backslash before an ordinary letter in double quotes.
        "grep 'a\\d' a.py",
        'grep "a\\d" a.py',
        "ls '$(rm x)'",
        'echo "\\$(rm x)"',
        # A quoted glob is find's, and one inside a path cannot start an option.
        "find . -name '*.py' | head",
        "ls src/*.py",
        'grep -n x "*"',
        # A copy of a descriptor, once its word is split off.
        "echo done>&2",
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
        # Unquoted, `$(` runs; an escaped `$` leaves a `(` bash refuses.
        'ls "$(rm x)"',
        "echo \\$(rm x)",
        "cat a\nrm x",
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
        # A subcommand that can discard work asks in the forms that do.
        (
            "git checkout -b feat",
            ["git checkout main", "git checkout -b fix main", "git checkout main>log"],
            [
                "git checkout .",
                "git checkout -- .",
                "git checkout -f main",
                "git checkout .>/dev/null",
                # A quoted `>` is a branch name here, and a.py a path.
                "git checkout '>' a.py",
                "git checkout 2'>' a.py",
                "git checkout main >'>' a.py",
                "git checkout > -b main a.py",
                "git checkout '&>' a.py",
                "git checkout '{fd}>x' a.py",
                # bash does not expand quoted braces: no `>` comes of them.
                "git checkout '{,>}' a.py",
                "git checkout --orphan side --no-orphan a.py",
            ],
        ),
        (
            "git restore --staged a.py",
            ["git restore --staged b.py"],
            ["git restore .", "git restore b.py", "git restore > --staged a.py"],
        ),
        (
            "pushd src > /dev/null",
            ["pushd lib>/dev/null"],
            ["pushd >/dev/null -", "pushd>/dev/null -"],
        ),
        (
            "git switch main",
            ["git switch -c feat"],
            ["git switch -f main", "git switch --discard-changes main"],
        ),
        (
            "git stash",
            ["git stash", "git stash >/dev/null"],
            ["git stash drop", "git stash clear", "git stash drop>/dev/null"],
        ),
        (
            "git branch new",
            ["git branch -d new"],
            ["git branch -D main", "git branch '>' -D feat"],
        ),
        # git named by a path is approved one subcommand at a time too.
        (
            "bin/git checkout -b feat",
            ["bin/git checkout main"],
            [
                "bin/git checkout .",
                "bin/git -C . checkout .",
                "bin/git -c x=y checkout -b fix",
                "bin/git status",
            ],
        ),
        # rm is approved whole, but a recursive rm always asks.
        (
            "rm a.txt",
            ["rm b.txt"],
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
                # An unquoted glob expands to whatever matches: not a plain word.
                "rm build/*.o",
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
        ("bin/git add .", {"bin/git add"}),
        ("bin/git -C sub add x", None),
        ("{ python3 t.py; }", None),
        ("python3 *.py", {"python3"}),
        ("./*.sh", None),
        ("ls#;rm x", None),
        ("make; # build it", None),
        # An alias (`co = checkout`) is not a git command the scan can name.
        ("git co .", None),
        # A command given as an argument runs a shell command the scan cannot see.
        ("git rebase -x 'git reset --hard'", None),
        ("git rebase --exec 'git clean -fdx'", None),
        # A line with a risk, such as a discard, is never scoped.
        ("git rebase --skip", None),
        ("git submodule foreach 'git reset --hard'", None),
        ("git ad x", None),
        ('"" python3 t.py', None),
        # `>|` is a redirect, not a pipe into a program named `a.py`.
        ("python3 t.py >| a.py", {"python3"}),
        # `\>` is an argument and `|` a pipe: bash runs rm after python3.
        ("python3 t.py \\>| rm x", {"python3", "rm"}),
    ],
)
def test_scope(command: str, programs: set[str] | None, tmp_path: Path) -> None:
    got = scope(command, Policy(root=tmp_path))
    assert got == (None if programs is None else frozenset(programs))


async def test_after_a_on_write_a_write_under_git_still_asks(tmp_path: Path) -> None:
    keys = Keys("a", "y", "y")
    approver = Approver(keys, Policy(root=tmp_path))
    assert await approver("write", {"path": "src/x.py", "content": ""}) is True
    assert await approver("write", {"path": "src/x.py", "content": ""}) is True
    assert keys.asked == ["write"]
    assert await approver("write", {"path": ".git/config", "content": ""}) is True
    hook = {"path": ".git/hooks/pre-commit", "old": "", "new": "x"}
    assert await approver("edit", hook) is True
    assert keys.asked == ["write", "write", "edit"]
    assert keys.keys == []
    assert approver.always == {"write"}


async def test_a_under_git_grants_nothing_more(tmp_path: Path) -> None:
    keys = Keys("a", "n", "y")
    approver = Approver(keys, Policy(root=tmp_path))
    assert await approver("write", {"path": ".git/config", "content": ""}) is True
    assert approver.always == set()
    assert await approver("write", {"path": ".git/config", "content": ""}) is False
    assert await approver("write", {"path": "src/x.py", "content": ""}) is True
    assert keys.asked == ["write", "write", "write"]


async def test_a_git_write_reached_through_a_link_or_dots_asks(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "link.txt").symlink_to(tmp_path / ".git" / "config")
    (tmp_path / "src").mkdir()
    keys = Keys("a", "n", "n")
    approver = Approver(keys, Policy(root=tmp_path))
    assert await approver("write", {"path": "src/x.py", "content": ""}) is True
    assert await approver("write", {"path": "link.txt", "content": ""}) is False
    assert await approver("write", {"path": "src/../.git/HEAD", "content": ""}) is False
    assert keys.asked == ["write", "write", "write"]


async def test_git_is_only_the_root_git_directory(tmp_path: Path) -> None:
    keys = Keys("a", "y")
    approver = Approver(keys, Policy(root=tmp_path))
    assert await approver("write", {"path": "a", "content": ""}) is True
    # A directory named .github or .gitignore is not .git/.
    assert await approver("write", {"path": ".gitignore", "content": ""}) is True
    assert await approver("write", {"path": ".github/x", "content": ""}) is True
    assert keys.asked == ["write"]


async def test_after_a_a_git_write_in_another_case_still_asks(tmp_path: Path) -> None:
    # macOS and Windows volumes fold case, so .GIT/hooks is the same directory.
    (tmp_path / ".git").mkdir()
    keys = Keys("a", "n")
    approver = Approver(keys, Policy(root=tmp_path))
    assert await approver("write", {"path": "src/x.py", "content": ""}) is True
    assert (
        await approver("write", {"path": ".GIT/hooks/pre-commit", "content": ""})
        is False
    )
    assert keys.asked == ["write", "write"]


async def test_with_no_root_a_write_to_the_enclosing_git_asks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # git finds its repository from a subdirectory, so the write asks too.
    (tmp_path / ".git").mkdir()
    (tmp_path / "sub").mkdir()
    monkeypatch.chdir(tmp_path / "sub")
    keys = Keys("a", "n")
    approver = Approver(keys, Policy())
    assert await approver("write", {"path": "x.py", "content": ""}) is True
    assert (
        await approver("write", {"path": "../.git/hooks/pre-commit", "content": ""})
        is False
    )
    assert keys.asked == ["write", "write"]


async def test_a_path_the_check_cannot_resolve_asks(tmp_path: Path) -> None:
    keys = Keys("n")
    approver = Approver(keys, Policy(root=tmp_path))
    assert await approver("write", {"path": "a\x00b", "content": ""}) is False
    assert keys.asked == ["write"]


async def test_a_quoted_operator_or_dollar_is_text_not_an_expansion(
    tmp_path: Path,
) -> None:
    keys = Keys("a", "n", "n")
    approver = Approver(keys, Policy(root=tmp_path))
    # bash passes a quoted `\<`, `>` or `$` to grep and sed as text.
    assert await approver("bash", bash("grep '\\<foo\\>' a.py")) is True
    assert keys.asked == []
    assert await approver("bash", bash("sed -n '1,$p' a.py")) is True
    assert keys.asked == ["bash"]
    assert await approver("bash", bash("sed -n '1,$p' b.py")) is True
    assert keys.asked == ["bash"]
    # Unquoted, the `$` expands, so the same program asks again.
    assert await approver("bash", bash('sed -n "1,$HOME" a.py')) is False
    assert keys.asked == ["bash", "bash"]


@pytest.mark.parametrize(
    "command",
    [
        "python3 t.py $HOME",
        'python3 t.py "$(id)"',
        'python3 t.py "$HOME"',
        "python3 t.py x{1,2}.py",
        "python3 t.py a*b",
        "python3 t.py ~/x",
        'python3 t.py "`id`"',
        "python3 t.py <<EOF",
        "python3 t.py <(id)",
    ],
)
async def test_an_approved_program_asks_about_any_unplain_word(
    command: str, tmp_path: Path
) -> None:
    keys = Keys("a", "n")
    approver = Approver(keys, Policy(root=tmp_path))
    assert await approver("bash", bash("python3 t.py")) is True
    assert await approver("bash", bash(command)) is False, command


async def test_quoted_plain_words_of_an_approved_program_run_unasked(
    tmp_path: Path,
) -> None:
    keys = Keys("a")
    approver = Approver(keys, Policy(root=tmp_path))
    assert await approver("bash", bash("python3 t.py")) is True
    assert await approver("bash", bash("python3 'my test.py' \"x y\"")) is True
    assert await approver("bash", bash("python3 t.py 2>&1 | tail -3")) is True
    assert keys.asked == ["bash"]


@pytest.mark.parametrize(
    "command",
    [
        # bash expands the braces first, so the write flag is there to run.
        "find . {-depth,-delete}",
        "git diff {--output=OUT,}",
        "find . -fp{rint,x} f",
        # A NUL in a ~ word is refused, not raised.
        'cat "~\x00"',
    ],
)
async def test_a_write_flag_hidden_by_braces_or_a_bad_tilde_asks(
    command: str, tmp_path: Path
) -> None:
    keys = Keys("n")
    approver = Approver(keys, Policy(root=tmp_path))
    assert await approver("bash", bash(command)) is False, command
    assert keys.asked == ["bash"]


async def test_a_on_git_merge_still_asks_for_merge_abort(tmp_path: Path) -> None:
    keys = Keys("a", "n")
    approver = Approver(keys, Policy(root=tmp_path))
    assert await approver("bash", bash("git merge feat")) is True
    assert await approver("bash", bash("git merge --abort")) is False
    assert keys.asked == ["bash", "bash"]


async def test_a_on_git_reflog_still_asks_for_delete(tmp_path: Path) -> None:
    keys = Keys("a", "n")
    approver = Approver(keys, Policy(root=tmp_path))
    assert await approver("bash", bash("git reflog show")) is True
    assert await approver("bash", bash("git reflog delete 'refs/stash@{0}'")) is False
    # The first call asked too: reflog is not read-only.
    assert keys.asked == ["bash", "bash"]
    keys = Keys("a", "n", "n")
    approver = Approver(keys, Policy(root=tmp_path))
    assert await approver("bash", bash("git reflog show")) is True


async def test_a_on_git_rm_asks_for_force_and_not_for_plain(tmp_path: Path) -> None:
    keys = Keys("a", "n", "n")
    approver = Approver(keys, Policy(root=tmp_path))
    assert await approver("bash", bash("git rm a.py")) is True
    assert await approver("bash", bash("git rm -f b.py")) is False
    assert await approver("bash", bash("git rm --force b.py")) is False
    assert await approver("bash", bash("git rm c.py")) is True
    assert keys.asked == ["bash", "bash", "bash"]


async def test_an_alias_is_never_approved_with_a(tmp_path: Path) -> None:
    # `co` may be an alias for checkout, which discards work: `a` acts as y.
    keys = Keys("a", "n")
    approver = Approver(keys, Policy(root=tmp_path))
    assert await approver("bash", bash("git co .")) is True
    assert await approver("bash", bash("git co .")) is False
    assert keys.asked == ["bash", "bash"]


async def test_a_on_another_git_command_still_asks_for_these(tmp_path: Path) -> None:
    keys = Keys("a", "n", "n", "n", "n")
    approver = Approver(keys, Policy(root=tmp_path))
    assert await approver("bash", bash("git rebase x")) is True
    assert await approver("bash", bash("git rebase -x 'git reset --hard'")) is False
    assert await approver("bash", bash("git rebase --skip")) is False
    assert await approver("bash", bash("git rebase --exec 'git clean -fdx'")) is False
    assert keys.asked == ["bash", "bash", "bash", "bash"]
