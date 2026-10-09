import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console, Group, RenderableType
from rich.text import Text

from nare.session import Message, Usage, dumps, loads, new_session
from nare.tools import Policy
from nare.tui.render import (
    Block,
    approval_text,
    attach_status,
    render_messages,
    render_outcome,
    render_user,
    settled,
    status_line,
    unified,
)


def plain(blocks: list[Block]) -> str:
    console = Console(width=100, file=io.StringIO(), record=True)
    for block in blocks:
        if block.title is not None:
            console.print(block.title, markup=False)
        content: RenderableType = block.content
        console.print(content, markup=False)
    return console.export_text()


def call(name: str, args: dict[str, Any], call_id: str = "c1") -> Message:
    return Message(
        "assistant", [{"type": "tool_use", "id": call_id, "name": name, "input": args}]
    )


def answer(text: str, *, call_id: str = "c1", error: bool = False) -> Message:
    return Message(
        "user",
        [
            {
                "type": "tool_result",
                "tool_use_id": call_id,
                "content": text,
                "is_error": error,
            }
        ],
    )


def test_settled_waits_for_unanswered_calls() -> None:
    messages = new_session("go").messages + [call("bash", {"command": "ls"})]
    # Mid-dispatch: the call is committed, its result is not.
    assert settled(messages) == 1
    messages.append(answer("exit 0"))
    assert settled(messages) == 3
    messages.append(Message("assistant", [{"type": "text", "text": "done"}]))
    assert settled(messages) == 4


def test_user_text_assistant_markdown_and_folded_thinking() -> None:
    messages = [
        Message("user", [{"type": "text", "text": "fix [redacted] bug"}]),
        Message(
            "assistant",
            [
                {"type": "thinking", "thinking": "look at foo"},
                {"type": "text", "text": "**Fixed** it"},
            ],
        ),
    ]
    blocks = render_messages(messages, 0, 2)
    assert [b.title for b in blocks] == [None, "thinking", None]
    text = plain(blocks)
    assert "> fix [redacted] bug" in text
    assert "look at foo" in text
    assert "Fixed it" in text


def test_a_tool_call_shows_its_folded_result() -> None:
    long = "\n".join(f"line {n}" for n in range(12))
    messages = [call("bash", {"command": "seq 12"}), answer(long)]
    text = plain(render_messages(messages, 0, 2))
    assert "bash seq 12" in text
    assert "line 4" in text
    assert "line 5" not in text
    assert "7 more lines" in text


def test_a_tool_result_only_user_message_draws_nothing_itself() -> None:
    messages = [call("bash", {"command": "true"}), answer("exit 0")]
    assert len(render_messages(messages, 1, 2)) == 0


def test_an_error_result_is_shown() -> None:
    messages = [call("write", {"path": "a", "content": "x"}), answer(
        "write was not approved", error=True
    )]  # fmt: skip
    assert "write was not approved" in plain(render_messages(messages, 0, 2))


def test_edit_in_the_transcript_is_a_diff_of_old_and_new() -> None:
    messages = [
        call("edit", {"path": "bar.py", "old": "x = 1", "new": "x = 2"}),
        answer("edited bar.py"),
    ]
    text = plain(render_messages(messages, 0, 2))
    assert "-x = 1" in text
    assert "+x = 2" in text


def test_unified_has_headers_and_no_glued_lines() -> None:
    diff = unified("a\nb", "a\nc", "f.py")
    assert diff.splitlines() == [
        "--- a/f.py", "+++ b/f.py", "@@ -1,2 +1,2 @@", " a", "-b", "+c",
    ]  # fmt: skip


def test_preview_of_edit_is_a_whole_file_diff(tmp_path: Path) -> None:
    (tmp_path / "bar.py").write_text("one\nx = 1\nthree\n")
    text, lexer = approval_text(
        "edit", {"path": "bar.py", "old": "x = 1", "new": "x = 2"},
        Policy(root=tmp_path),
    )  # fmt: skip
    assert lexer == "diff"
    assert "-x = 1" in text and "+x = 2" in text and " one" in text


def test_preview_of_write_diffs_an_existing_file(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("old\n")
    text, lexer = approval_text(
        "write", {"path": "a.txt", "content": "new\n"}, Policy(root=tmp_path)
    )
    assert lexer == "diff"
    assert "-old" in text and "+new" in text


@pytest.mark.parametrize(
    "before, after",
    [("one\r\ntwo\r\n", "one\ntwo\n"), ("one\ntwo\n", "one\ntwo")],
)
def test_preview_of_write_names_a_line_ending_change(
    tmp_path: Path, before: str, after: str
) -> None:
    # The line diff is empty, but the write still rewrites the file.
    (tmp_path / "a.txt").write_bytes(before.encode())
    text, _ = approval_text(
        "write", {"path": "a.txt", "content": after}, Policy(root=tmp_path)
    )
    assert "no change" not in text
    assert "line endings" in text


@pytest.mark.parametrize(
    "tool, args",
    [
        ("edit", {"path": "a.txt", "old": "two", "new": "TWO"}),
        ("write", {"path": "a.txt", "content": "one\nTWO\nthree\n"}),
    ],
)
def test_preview_names_a_crlf_rewrite_beside_the_change(
    tmp_path: Path, tool: str, args: dict[str, Any]
) -> None:
    # edit_file rewrites every CRLF as LF, and splitlines() treats the two
    # alike, so the diff alone showed a one-line change.
    (tmp_path / "a.txt").write_bytes(b"one\r\ntwo\r\nthree\r\n")
    text, _ = approval_text(tool, args, Policy(root=tmp_path))
    assert "line endings change to LF" in text
    assert "-two" in text and "+TWO" in text


def test_preview_of_write_names_a_new_file(tmp_path: Path) -> None:
    text, lexer = approval_text(
        "write", {"path": "n.txt", "content": "hello\n"}, Policy(root=tmp_path)
    )
    assert text.startswith("new file ")
    assert "hello" in text


def test_preview_of_bash_is_the_command() -> None:
    assert approval_text("bash", {"command": "ls -la"}, Policy()) == (
        "ls -la",
        "bash",
    )


def test_preview_of_another_tool_is_indented_json() -> None:
    text, lexer = approval_text("srv__q", {"q": 1}, Policy())
    assert (text, lexer) == ('{\n  "q": 1\n}', "json")


@pytest.mark.parametrize(
    "tool, args",
    [
        ("edit", {"path": "bar.py", "old": "missing", "new": "y"}),
        ("edit", {"path": "bar.py", "old": "x", "new": "y"}),  # appears twice
        ("edit", {"path": "bar.py", "old": "", "new": "y"}),
        ("edit", {"path": "bin.dat", "old": "a", "new": "b"}),  # not UTF-8
        ("edit", {"path": "../outside.py", "old": "a", "new": "b"}),
        ("write", {"path": "../outside.py", "content": "x"}),
        ("write", {"content": "no path"}),
        ("write", {"path": "bin.dat", "content": "x"}),  # not UTF-8
    ],
)
def test_preview_falls_back_to_raw_arguments(
    tmp_path: Path, tool: str, args: dict[str, Any]
) -> None:
    (tmp_path / "bar.py").write_text("x\nx\n")
    (tmp_path / "bin.dat").write_bytes(b"\xff\xfe\x00a")
    text, lexer = approval_text(tool, args, Policy(root=tmp_path))
    assert lexer == "json"
    assert text.startswith("{")


def test_status_line() -> None:
    usage = Usage(input=40_000, output=1_200, cost=0.31)
    line = status_line("claude-sonnet-5", 7, 50, usage, 2.0, "working")
    assert line.plain == (
        "claude-sonnet-5 · turn 7/50 · 41.2k tok · $0.31 / $2.00 · working"
    )
    unpriced = Usage(input=10, output=5, cost=None)
    line = status_line("m", 1, 50, unpriced, None, "done", note="could not write s")
    assert line.plain == (
        "m · turn 1/50 · 15 tok · cost unknown · done · could not write s"
    )


def test_outcome_shows_questions_or_the_error() -> None:
    s = new_session("go")
    assert render_outcome(s).plain == ""
    s.status = "blocked"
    s.questions = ["which file?", "which test?"]
    assert "1. which file?" in render_outcome(s).plain
    assert "2. which test?" in render_outcome(s).plain
    s.status = "error"
    s.error = "stopped after 50 turns"
    assert render_outcome(s).plain == "stopped after 50 turns"


def test_attach_status_carries_the_file_age() -> None:
    s = new_session("go")
    assert attach_status(s, None, 185).plain == (
        "turn 0 · 0 tok · $0.00 · working · last write 3m ago"
    )
    waiting = attach_status(None, "waiting for s.json", None)
    assert waiting.plain == "waiting for s.json"


def test_attach_status_shows_an_interrupted_file_as_interrupted() -> None:
    s = new_session("go")
    s.interrupted_at = (datetime.now(UTC) - timedelta(seconds=125)).isoformat()
    assert attach_status(s, None, 5).plain == (
        "turn 0 · 0 tok · $0.00 · interrupted 2m ago · last write 5s ago"
    )
    # Hand-edited or foreign: still not `working`, and no crash.
    s.interrupted_at = "yesterday"
    assert "· interrupted ·" in attach_status(s, None, 5).plain
    raw = json.loads(dumps(s))
    raw["interrupted_at"] = 1700000000
    assert "· interrupted ·" in attach_status(loads(json.dumps(raw)), None, 5).plain


def result_style(messages: list[Message], root: str | None = None) -> str:
    group = render_messages(messages, 0, 2, root)[0].content
    assert isinstance(group, Group)
    last = group.renderables[-1]
    assert isinstance(last, Text)
    return str(last.style)


def test_a_nonzero_exit_is_a_failure() -> None:
    bash = call("bash", {"command": "python x.py"})
    assert result_style([bash, answer("exit 127\npython: not found")]) == "red"
    assert result_style([bash, answer("exit -9")]) == "red"  # a signal
    assert result_style([bash, answer("exit 0\nok")]) == "dim"


def test_the_bash_header_drops_cd_root_and_folds_a_heredoc() -> None:
    def header(command: str) -> str:
        messages = [call("bash", {"command": command}), answer("exit 0")]
        return plain(render_messages(messages, 0, 2, "/r")).splitlines()[0]

    assert header("cd /r && python3 -m unittest") == "● bash python3 -m unittest"
    assert header("cd . && ls") == "● bash ls"
    assert header("cd /elsewhere && ls") == "● bash cd /elsewhere && ls"
    heredoc = "cat > a.py <<'EOF'\n" + "x = 1\n" * 11 + "EOF"
    assert header(heredoc) == "● bash cat > a.py <<'EOF' (+12 lines)"


def test_a_long_edit_diff_is_capped_at_twenty_lines() -> None:
    old = "\n".join(f"a{n}" for n in range(28))
    new = "\n".join(f"b{n}" for n in range(29))
    assert len(unified(old, new, "f.py").splitlines()) == 60
    edit = call("edit", {"path": "f.py", "old": old, "new": new})
    text = plain(render_messages([edit, answer("edited f.py")], 0, 2))
    assert "-a16" in text and "-a17" not in text
    assert "… 40 more lines" in text


def test_results_are_one_line_with_paths_relative_to_the_root() -> None:
    write = call("write", {"path": "/r/app.py", "content": "a\nb\nc\n"})
    text = plain(render_messages([write, answer("wrote 6 characters to /r/app.py")],
                                 0, 2, "/r"))  # fmt: skip
    assert "● write app.py" in text
    assert "wrote app.py (3 lines)" in text
    read = call("read", {"path": "/r/app.py"})
    text = plain(render_messages([read, answer("secret\ncontent")], 0, 2, "/r"))
    assert "read app.py (2 lines)" in text
    assert "secret" not in text
    edit = call("edit", {"path": "/r/app.py", "old": "a", "new": "b"})
    text = plain(render_messages([edit, answer("edited /r/app.py")], 0, 2, "/r"))
    assert "--- a/app.py" in text and "edited app.py" in text


def test_ask_is_a_count_and_its_success_is_hidden() -> None:
    ask = call("ask", {"questions": ["which file?", "which test?"]})
    text = plain(render_messages([ask, answer("questions sent")], 0, 2))
    assert text.strip() == "? asked 2 questions"
    text = plain(render_messages([ask, answer("no questions", error=True)], 0, 2))
    assert "no questions" in text


def test_single_newlines_are_kept_outside_code_fences() -> None:
    reply = "one\ntwo\n\n```\nx = 1\ny = 2\n```"
    lines = plain(render_messages([Message("assistant", [{"type": "text",
                                   "text": reply}])], 0, 1)).splitlines()  # fmt: skip
    assert [line.strip() for line in lines if line.strip()] == [
        "one", "two", "x = 1", "y = 2",
    ]  # fmt: skip


def test_a_blank_line_before_text_and_a_rule_before_a_prompt() -> None:
    messages = [call("bash", {"command": "ls"}), answer("exit 0"),
                Message("assistant", [{"type": "text", "text": "done"}])]  # fmt: skip
    lines = [
        line.rstrip() for line in plain(render_messages(messages, 0, 3)).splitlines()
    ]
    assert lines[lines.index("done") - 1] == ""
    rule, prompt = plain(render_user("next")).splitlines()
    assert set(rule) == {"─"} and prompt == "> next"
