"""The Python API section of docs/contract.md runs as written.

Every ```python block under "## Calling nare from Python" is executed, in its
own namespace and inside a temporary directory, against the scripted transport
it names. The defaults the section states are read from the library and from
the command-line parser, so the section cannot drift from either.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

import nare
from nare.cli import build_parser
from nare.corrections import SCHEMA_CORRECTIONS_DEFAULT
from nare.loop import (
    CUTOFFS_DEFAULT,
    MAX_TURNS_DEFAULT,
    check_structured_output,
)
from nare.transport.replay import ReplayTransport

DOC = Path(__file__).resolve().parent.parent / "docs" / "contract.md"
HEADING = "## Calling nare from Python"
FENCE = re.compile(r"^```python\n(.*?)^```", re.MULTILINE | re.DOTALL)


def section() -> str:
    text = DOC.read_text(encoding="utf-8")
    start = text.index(HEADING)
    end = text.find("\n## ", start + len(HEADING))
    return text[start : len(text) if end == -1 else end]


def examples() -> list[str]:
    return FENCE.findall(section())


def test_the_section_has_examples() -> None:
    # A section that lost its fences would pass the parametrised test below
    # with no cases at all.
    assert len(examples()) >= 7


@pytest.mark.parametrize("index", range(len(examples())))
def test_example_runs_as_written(
    index: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # make_transport reads its key from the environment; no request is made.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-used-by-these-examples")
    monkeypatch.chdir(tmp_path)
    code = examples()[index]
    exec(compile(code, f"docs/contract.md, Python example {index + 1}", "exec"), {})


def test_library_defaults_are_as_documented() -> None:
    params = inspect.signature(nare.run).parameters
    assert params["max_turns"].default == MAX_TURNS_DEFAULT == 50
    assert params["max_corrections"].default == SCHEMA_CORRECTIONS_DEFAULT == 1
    # The one default the section says differs: no cut-off budget at all.
    assert params["max_cutoffs"].default is None
    assert params["require_reads"].default == 0


def test_command_line_defaults_are_as_documented() -> None:
    parser = build_parser()
    args = parser.parse_args(["run", "--yes", "x"])
    assert args.max_turns == MAX_TURNS_DEFAULT
    # The parser leaves --max-corrections unset; the run treats unset as the
    # documented default of one correction.
    assert args.max_corrections is None
    assert args.max_cutoffs == CUTOFFS_DEFAULT == 2
    # Unset reads as no requirement: the run passes `args.require_reads or 0`.
    assert args.require_reads is None


def test_replay_transport_refuses_structured_output() -> None:
    # The section says the recipe leaves this option out for this reason.
    transport = ReplayTransport({"replies": [{"text": "{}"}]})
    with pytest.raises(ValueError, match="does not support structured output"):
        check_structured_output(transport, {"type": "object"}, "json_schema")
