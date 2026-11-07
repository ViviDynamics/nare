"""Bash command rules: allow or deny a command by pattern.

Each bypass case names a marker file the command would create. A deny case
must be refused before the approver is asked, so the marker never appears and
the approver records no call. An allow case that smuggles a second command must
not be allowed by the rule: the approver is asked, refuses, and the marker
never appears.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nare.tools import Policy, dispatch
from nare.transport import ToolCall


def call(command: str) -> ToolCall:
    return ToolCall(id="call_1", name="bash", args={"command": command})


class Approver:
    """Answers every call the same way and remembers the calls it was asked."""

    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.asked: list[str] = []

    def __call__(self, tool: str, args: dict[str, Any]) -> bool:
        self.asked.append(str(args.get("command", "")))
        return self.answer


async def run_call(
    command: str,
    policy: Policy,
    approve: Approver,
    records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return await dispatch(
        call(command),
        policy,
        approve,
        rule_match=(records.append if records is not None else None),
    )


# A deny of `touch *` refuses every spelling of a `touch` below, whatever wraps
# it, however it is quoted, and whether it follows another command or not.
DENIED_TOUCHES = [
    "touch {m}",
    "echo ok; touch {m}",
    "echo ok && touch {m}",
    "echo ok || touch {m}",
    "echo ok | touch {m}",
    "echo ok & touch {m}",
    "{{ touch {m}; }}",
    "(touch {m})",
    "env touch {m}",
    "/usr/bin/env touch {m}",
    "/usr/bin/touch {m}",
    "'touch' {m}",
    "t''ouch {m}",
    "\\touch {m}",
    '"touch" {m}',
    "sh -c 'touch {m}'",
    'bash -c "touch {m}"',
    'eval "touch {m}"',
    "echo {m} | xargs touch",
    "find {d} -maxdepth 0 -exec touch {m} \\;",
    "time touch {m}",
    "nohup touch {m}",
    "timeout 5 touch {m}",
    "command touch {m}",
    "exec touch {m}",
    "X=1 touch {m}",
    "$(echo touch) {m}",
    "`echo touch` {m}",
    "x=touch; $x {m}",
    "t[o]uch {m}",
    "{{touch,{m}}}",
    "echo ok\ntouch {m}",
    "bash <<< 'touch {m}'",
    "$'touch' {m}",
    "env -S 'touch {m}'",
    "sudo touch {m}",
    "nice -n 5 touch {m}",
    "busybox touch {m}",
    # Found by adversarial review: expansion in a wrapper's arguments.
    "env {{touch,{m}}}",
    "xargs {{touch,{m}}}",
    "sh -c '{{touch,{m}}}'",
    "find {d} -maxdepth 0 -exec {{touch,{m}}} \\;",
    "env /bin/[t]ouch {m}",
    "env -S'touch {m}'",
    "bash -e -c 'touch {m}'",
    "TOUCH {m}",
    "Touch {m}",
    "echo {m} | sh",
    "sh {d}/s.sh",
]


@pytest.mark.parametrize("template", DENIED_TOUCHES)
async def test_a_deny_refuses_every_spelling_before_approval(
    template: str, tmp_path: Path
) -> None:
    marker = tmp_path / "ran"
    command = template.format(m=marker, d=tmp_path)
    approve = Approver(answer=True)
    records: list[dict[str, Any]] = []

    result = await run_call(
        command,
        Policy(root=tmp_path, bash_deny=frozenset({"touch *"})),
        approve,
        records,
    )

    assert result["is_error"] is True, command
    assert not marker.exists(), command
    assert approve.asked == [], command
    assert records == [
        {"tool_use_id": "call_1", "effect": "deny", "patterns": ["touch *"]}
    ], command


async def test_a_deny_holds_even_when_every_call_is_approved(tmp_path: Path) -> None:
    # --yes is approve_all: it approves each call, and the deny still refuses.
    marker = tmp_path / "ran"

    result = await dispatch(
        call(f"touch {marker}"),
        Policy(root=tmp_path, bash_deny=frozenset({"touch *"})),
    )

    assert result["is_error"] is True
    assert not marker.exists()


async def test_a_deny_refuses_an_unreadable_line_it_cannot_check(
    tmp_path: Path,
) -> None:
    # A line the lexer cannot read is refused rather than asked when a deny is
    # set: nothing in it can be shown to avoid the denied program.
    marker = tmp_path / "ran"
    approve = Approver(answer=True)

    result = await run_call(
        f"echo ok; touch {marker} <(true)",
        Policy(root=tmp_path, bash_deny=frozenset({"echo *"})),
        approve,
    )

    assert result["is_error"] is True
    assert not marker.exists()
    assert approve.asked == []


async def test_a_deny_beats_an_allow_for_the_same_command(tmp_path: Path) -> None:
    approve = Approver(answer=True)
    records: list[dict[str, Any]] = []

    result = await run_call(
        "echo bad",
        Policy(
            root=tmp_path,
            bash_allow=frozenset({"echo *"}),
            bash_deny=frozenset({"echo bad*"}),
        ),
        approve,
        records,
    )

    assert result["is_error"] is True
    assert approve.asked == []
    assert records[0]["effect"] == "deny"
    assert records[0]["patterns"] == ["echo bad*"]


async def test_an_allowed_plain_command_runs_without_asking(tmp_path: Path) -> None:
    approve = Approver(answer=False)
    records: list[dict[str, Any]] = []

    result = await run_call(
        "echo hello",
        Policy(root=tmp_path, bash_allow=frozenset({"echo *"})),
        approve,
        records,
    )

    assert result["is_error"] is False
    assert "hello" in result["content"]
    assert approve.asked == []
    assert records == [
        {"tool_use_id": "call_1", "effect": "allow", "patterns": ["echo *"]}
    ]


async def test_quotes_are_removed_before_an_allow_matches(tmp_path: Path) -> None:
    approve = Approver(answer=False)

    result = await run_call(
        "'echo' \"ok\"",
        Policy(root=tmp_path, bash_allow=frozenset({"echo *"})),
        approve,
    )

    assert result["is_error"] is False
    assert approve.asked == []


async def test_a_command_no_rule_matches_still_asks(tmp_path: Path) -> None:
    approve = Approver(answer=False)

    result = await run_call(
        "ls",
        Policy(root=tmp_path, bash_allow=frozenset({"echo *"})),
        approve,
    )

    assert result["is_error"] is True
    assert approve.asked == ["ls"]


ALLOW_BYPASSES = [
    "echo ok; touch {m}",
    "echo ok && touch {m}",
    "echo ok || touch {m}",
    "echo ok | touch {m}",
    "echo ok & touch {m}",
    "echo $(touch {m})",
    "echo `touch {m}`",
    "(echo ok; touch {m})",
    "echo ok > {m}",
    "echo ok >> {m}",
    "echo ok 2> {m}",
    "echo $HOME",
    "echo *",
    "echo ok\ntouch {m}",
]


@pytest.mark.parametrize("template", ALLOW_BYPASSES)
async def test_an_allow_never_covers_a_compound_or_an_expansion(
    template: str, tmp_path: Path
) -> None:
    # The allow pattern is `echo *`. Each line below contains an echo, but is
    # not a plain echo: the rule does not allow it, so the approver is asked and
    # refuses, and nothing runs.
    marker = tmp_path / "ran"
    command = template.format(m=marker)
    approve = Approver(answer=False)

    result = await run_call(
        command, Policy(root=tmp_path, bash_allow=frozenset({"echo *"})), approve
    )

    assert result["is_error"] is True, command
    assert approve.asked == [command], command
    assert not marker.exists(), command


async def test_an_unreadable_line_with_only_an_allow_is_asked(tmp_path: Path) -> None:
    approve = Approver(answer=False)

    result = await run_call(
        "echo ok\nls",
        Policy(root=tmp_path, bash_allow=frozenset({"echo *"})),
        approve,
    )

    assert result["is_error"] is True
    assert len(approve.asked) == 1


async def test_without_rules_the_approver_decides_as_before(tmp_path: Path) -> None:
    approve = Approver(answer=False)

    result = await run_call("echo ok", Policy(root=tmp_path), approve)

    assert result["is_error"] is True
    assert approve.asked == ["echo ok"]


def test_star_spans_spaces_and_question_mark_is_one_character() -> None:
    from nare.bash_rules import bash_rule_matches

    assert bash_rule_matches("echo *", "echo a b c")
    assert bash_rule_matches("echo ?", "echo a")
    assert not bash_rule_matches("echo ?", "echo ab")


def test_other_pattern_characters_match_themselves() -> None:
    from nare.bash_rules import bash_rule_matches

    assert bash_rule_matches("git.diff *", "git.diff x")
    assert not bash_rule_matches("git.diff *", "gitXdiff x")


def test_a_policy_rejects_a_bad_rule() -> None:
    with pytest.raises(ValueError, match="empty"):
        Policy(bash_deny=frozenset({""}))
    with pytest.raises(ValueError, match="newline"):
        Policy(bash_deny=frozenset({"rm\n*"}))
    with pytest.raises(ValueError, match="bash tool"):
        Policy(tools=frozenset({"read"}), bash_deny=frozenset({"rm *"}))


def test_the_policy_records_its_rules_only_when_set() -> None:
    assert "bash_deny" not in Policy().recorded()
    recorded = Policy(
        bash_allow=frozenset({"git diff *"}), bash_deny=frozenset({"rm *"})
    ).recorded()
    assert recorded["bash_allow"] == ["git diff *"]
    assert recorded["bash_deny"] == ["rm *"]


def test_the_cli_denies_under_yes_and_records_the_rule(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    from fake_provider import FakeProvider, text_reply, tool_reply
    from nare.cli import main

    marker = tmp_path / "ran"
    session_path = tmp_path / "s.json"
    provider = FakeProvider(
        [tool_reply("bash", {"command": f"touch {marker}"}), text_reply("done")]
    )

    main(
        [
            "run",
            "go",
            "--yes",
            "--jsonl",
            "--bash-deny",
            "touch *",
            "--session",
            str(session_path),
        ],
        transport=provider,
    )

    capsys.readouterr()
    assert not marker.exists()
    saved = json.loads(session_path.read_text(encoding="utf-8"))
    assert saved["policy"]["bash_deny"] == ["touch *"]
    assert saved["bash_rules"] == [
        {"tool_use_id": "call_1", "effect": "deny", "patterns": ["touch *"]}
    ]
    assert "command" not in json.dumps(saved["bash_rules"])


REFUSED_EXPANSION = ["ls *.py", "echo $HOME", "echo {a,b}", "cat f.txt; ls ~"]


@pytest.mark.parametrize("command", REFUSED_EXPANSION)
def test_a_deny_refuses_an_expansion_anywhere_in_the_line(command: str) -> None:
    # bash turns a glob, brace, variable or tilde into names this check cannot
    # see, so under a deny every word must be plain. The line is refused.
    from nare.bash_rules import decide

    assert decide(command, frozenset(), frozenset({"touch *"})) == (
        "deny",
        ["touch *"],
    )


def test_a_deny_does_not_refuse_a_plain_command() -> None:
    from nare.bash_rules import decide

    assert decide("cat f.txt", frozenset(), frozenset({"touch *"})) is None


# Found by the second adversarial review.
REVIEW_TWO = [
    ">{d}/x sh -c 'true; touch {m}'",
    "2>{d}/x sh -c 'true; touch {m}'",
    "X+=1 sh -c 'true; touch {m}'",
    "SH -c 'true; touch {m}'",
    "ENV sh -c 'true; touch {m}'",
    "csh -c 'touch {m}'",
    ". {d}/s.sh",
    "source {d}/s.sh",
    "bash --rcfile {d}/s.sh -c 'true'",
    "BASH_ENV={d}/s.sh bash -c 'true'",
    "arch -arm64 {{touch,{m}}}",
    "xcrun {{touch,{m}}}",
    "GIT_EXTERNAL_DIFF='touch {m}' git diff",
    # Found by the third review: env unescapes its -S string itself.
    "env -S 'touch\\_{m}'",
    'env -S"touch\\\\_{m}"',
    # An option that names a program, for a command that is not a runner.
    "tar --to-command='touch {m}' -xf a.tar",
    "sort --compress-program='touch {m}' x",
]


@pytest.mark.parametrize("template", REVIEW_TWO)
async def test_a_deny_refuses_the_second_review_bypasses(
    template: str, tmp_path: Path
) -> None:
    marker = tmp_path / "ran"
    (tmp_path / "s.sh").write_text(f"touch {marker}\n", encoding="utf-8")
    command = template.format(m=marker, d=tmp_path)
    approve = Approver(answer=True)

    result = await run_call(
        command, Policy(root=tmp_path, bash_deny=frozenset({"touch *"})), approve
    )

    assert result["is_error"] is True, command
    assert not marker.exists(), command
    assert approve.asked == [], command
