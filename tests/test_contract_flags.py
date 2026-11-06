"""Every `nare run` flag, in `nare contract` and in docs/contract.md."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from nare.contract import describe


def _run_parser() -> argparse.ArgumentParser:
    from nare.cli import build_parser

    [sub] = [
        a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction)
    ]
    run: argparse.ArgumentParser = sub.choices["run"]
    return run


def test_contract_lists_every_run_flag() -> None:
    options = [
        a
        for a in _run_parser()._actions
        if a.option_strings and not isinstance(a, argparse._HelpAction)
    ]
    flags = describe()["flags"]
    assert list(flags) == [max(a.option_strings, key=len) for a in options]
    for action in options:
        entry = flags[max(action.option_strings, key=len)]
        assert entry == {
            "takes_value": action.nargs != 0,
            "choices": None if action.choices is None else list(action.choices),
        }


def test_contract_flags_cover_the_options_callers_ask_about() -> None:
    flags = describe()["flags"]
    for name in ("--stream", "--budget-tokens", "--budget-usd", "--mcp-config"):
        assert name in flags
    assert flags["--stream"] == {"takes_value": False, "choices": None}
    assert flags["--effort"] == {
        "takes_value": True,
        "choices": ["low", "medium", "high", "xhigh", "max"],
    }
    assert flags["--compaction"] == {
        "takes_value": True,
        "choices": ["elide", "summary"],
    }
    assert "--help" not in flags and "prompt" not in flags


def test_a_new_parser_option_is_listed_without_an_edit() -> None:
    from nare.contract import flags_of

    parser = _run_parser()
    parser.add_argument("--brand-new", choices=["a", "b"])
    assert flags_of(parser)["--brand-new"] == {
        "takes_value": True,
        "choices": ["a", "b"],
    }


def test_every_run_flag_is_documented_in_the_contract() -> None:
    """A flag added to `nare run` fails here until docs/contract.md lists it."""
    doc = Path(__file__).parent.parent / "docs" / "contract.md"
    section = doc.read_text(encoding="utf-8").split("## Run flags", 1)[1]
    section = section.split("\n## ", 1)[0]
    documented = re.findall(r"^\| `(--[a-z0-9-]+)` \|", section, re.MULTILINE)
    assert documented == list(describe()["flags"])
    assert "`flags`" in section
