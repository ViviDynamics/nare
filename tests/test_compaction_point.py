"""The compaction point is configurable: --compaction-trigger and --compaction-target.

Issue #43. The defaults are the documented 80% and 60% of the window, so a run that
passes neither flag behaves exactly as before.
"""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import Any

import pytest

from fake_provider import FakeProvider, text_reply
from nare.cli import build_parser, main
from nare.compact import (
    TARGET,
    TRIGGER,
    CompactionPoint,
    check_compaction_point,
    compact,
    context_chars,
    estimate,
    plan_summary,
)
from nare.session import Message, Session, dumps, new_session
from test_compact import transcript
from test_summary_compaction import drain


def parse(*argv: str) -> Namespace:
    return build_parser().parse_args(["run", "--yes", *argv, "go"])


def test_the_default_point_is_the_documented_eighty_and_sixty() -> None:
    assert CompactionPoint() == CompactionPoint(trigger=0.8, target=0.6)
    assert (CompactionPoint().trigger, CompactionPoint().target) == (TRIGGER, TARGET)


@pytest.mark.parametrize(
    "trigger, target",
    [
        (0.5, 0.5),  # target equal to trigger: nothing to elide toward
        (0.5, 0.6),  # target above trigger
        (0.5, 0.0),  # target at zero
        (0.5, -0.1),
        (0.0, 0.0),  # trigger at zero
        (1.1, 0.5),  # trigger above the window
        (-0.2, -0.3),
        (float("nan"), 0.5),
        (0.8, float("inf")),
        (True, 0.5),  # a bool is not a fraction
        ("0.8", 0.6),
    ],
)
def test_an_invalid_point_is_refused_with_a_clear_message(
    trigger: Any, target: Any
) -> None:
    with pytest.raises(ValueError, match="compaction"):
        check_compaction_point(trigger, target)


@pytest.mark.parametrize("trigger, target", [(1.0, 0.99), (0.3, 0.2), (0.8, 0.6)])
def test_a_point_inside_the_bounds_is_accepted(trigger: float, target: float) -> None:
    check_compaction_point(trigger, target)
    assert CompactionPoint(trigger, target) == CompactionPoint(trigger, target)


def test_a_point_names_the_flag_it_came_from_when_asked() -> None:
    with pytest.raises(
        ValueError, match="--compaction-target 0.9 must be above 0 and below"
    ):
        check_compaction_point(
            0.8,
            0.9,
            names=("--compaction-trigger", "--compaction-target"),
        )


def test_the_trigger_and_target_stop_at_the_point_not_at_eighty_and_sixty() -> None:
    s = new_session("go")
    s.messages = transcript()
    window = int(estimate(s) / 0.5)  # the estimate is half the window
    assert compact(s, window) is None  # the default trigger is 80%
    s = new_session("go")
    s.messages = transcript()
    report = compact(s, window, point=CompactionPoint(trigger=0.4, target=0.3))
    assert report is not None
    assert report.before >= window * 0.4
    assert report.after < report.before


def many_reads(turns: int = 8, chars: int = 4000) -> Session:
    """Eight large reads, so elision has old results to take well past the
    default target. The transcript() fixture has only two that may go."""
    s = new_session("go")
    s.messages = [Message("user", [{"type": "text", "text": "task"}])]
    for turn in range(1, turns + 1):
        s.messages += [
            Message(
                "assistant",
                [{"type": "tool_use", "id": f"c{turn}", "name": "read", "input": {}}],
            ),
            Message(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": f"c{turn}",
                        "content": "x" * chars,
                    }
                ],
            ),
        ]
    return s


def test_a_lower_target_elides_further_than_the_default() -> None:
    default = many_reads()
    window = int(estimate(default) / 0.85)  # over the default 80% trigger
    default_report = compact(default, window)
    low = many_reads()
    low_report = compact(low, window, point=CompactionPoint(trigger=0.8, target=0.3))
    assert default_report is not None and low_report is not None
    assert default_report.after <= window * 0.6
    assert low_report.after < default_report.after
    assert low_report.after <= window * 0.3


def test_the_summary_gap_is_the_trigger_minus_the_target() -> None:
    # The gate asks for a summary only when the replaced turns free the gap
    # between trigger and target (spec 2026-11-05, and #275, which is not fixed
    # here). A point with a smaller gap admits turns the default refuses.
    s = new_session("go")
    s.messages = transcript()
    for i in (2, 4, 6, 8):
        s.messages[i].content[0]["content"] = "ok"
    for i in (10, 12):
        s.messages[i].content[0]["content"] = "x" * 14000
    replaced = context_chars(s.messages[1:9]) / 4
    window = int(replaced / 0.15)  # the replaced turns are 15% of the window
    assert estimate(s) >= 0.8 * window
    assert plan_summary(s, window) is None  # default gap is 20%
    assert plan_summary(s, window, point=CompactionPoint(0.8, 0.7)) is not None


def test_the_cli_defaults_to_the_documented_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NARE_COMPACTION_TRIGGER", raising=False)
    monkeypatch.delenv("NARE_COMPACTION_TARGET", raising=False)
    args = parse()
    assert (args.compaction_trigger, args.compaction_target) == (0.8, 0.6)


def test_the_environment_sets_the_point_and_the_flag_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NARE_COMPACTION_TRIGGER", "0.5")
    monkeypatch.setenv("NARE_COMPACTION_TARGET", "0.4")
    args = parse()
    assert (args.compaction_trigger, args.compaction_target) == (0.5, 0.4)
    args = parse("--compaction-trigger", "0.3", "--compaction-target", "0.2")
    assert (args.compaction_trigger, args.compaction_target) == (0.3, 0.2)


def test_an_empty_environment_value_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NARE_COMPACTION_TRIGGER", "")
    monkeypatch.setenv("NARE_COMPACTION_TARGET", "")
    args = parse()
    assert (args.compaction_trigger, args.compaction_target) == (0.8, 0.6)


def test_a_non_numeric_flag_exits_2() -> None:
    with pytest.raises(SystemExit) as exited:
        parse("--compaction-trigger", "eighty")
    assert exited.value.code == 2


def test_an_invalid_point_on_the_command_line_never_starts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeProvider([text_reply("done")])
    argv = ["run", "--yes", "--jsonl", "--compaction-trigger", "0.5", "go"]
    assert main(argv, transport=fake) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert (
        "--compaction-target 0.6 must be above 0 and below --compaction-trigger 0.5"
        in err
    )
    assert fake.calls == []


def test_an_invalid_point_from_the_environment_never_starts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("NARE_COMPACTION_TRIGGER", "1.5")
    fake = FakeProvider([text_reply("done")])
    assert main(["run", "--yes", "--jsonl", "go"], transport=fake) == 2
    assert "--compaction-trigger" in capsys.readouterr().err
    assert fake.calls == []


def test_the_point_flags_reach_the_compaction(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    s = new_session("go")
    s.messages = transcript()
    window = int(estimate(s) / 0.5)
    path = tmp_path / "s.json"
    path.write_text(dumps(s), encoding="utf-8")

    def compactions(*flags: str) -> list[dict[str, Any]]:
        fake = FakeProvider([text_reply("done")])
        argv = ["run", "--yes", "--jsonl", "--context-window", str(window)]
        argv += ["--resume", str(path), *flags]
        capsys.readouterr()
        path.write_text(dumps(s), encoding="utf-8")
        assert main(argv, transport=fake) == 0
        lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
        return [
            x["detail"]["compaction"]
            for x in lines
            if "compaction" in x.get("detail", {})
        ]

    assert compactions() == []  # the estimate is 50%, under the default 80%
    assert compactions("--compaction-trigger", "0.4", "--compaction-target", "0.3")


async def test_the_library_run_takes_the_point() -> None:
    s = many_reads()
    window = int(estimate(s) / 0.5)  # under the default 80% trigger
    fake = FakeProvider([text_reply("done")])
    events = await drain(
        s, fake, context_window=window, compaction_trigger=0.4, compaction_target=0.3
    )
    assert any("compaction" in e.detail for e in events)


async def test_the_library_run_refuses_an_invalid_point_before_any_call() -> None:
    fake = FakeProvider([])
    with pytest.raises(ValueError, match="compaction target 0.6 must be above 0"):
        await drain(many_reads(), fake, compaction_trigger=0.5)
    assert fake.calls == []
