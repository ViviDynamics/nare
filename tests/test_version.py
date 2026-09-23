"""The version a caller pins, records, and can read back off a run."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import nare
from fake_provider import FakeProvider, text_reply
from nare.cli import main


def test_the_cli_prints_the_released_version(capsys: Any) -> None:
    try:
        main(["--version"])
    except SystemExit as exit_:
        assert exit_.code == 0

    assert capsys.readouterr().out.strip() == nare.__version__


def test_the_result_line_carries_the_nare_version(capsys: Any) -> None:
    main(
        ["run", "go", "--yes", "--jsonl"], transport=FakeProvider([text_reply("done")])
    )

    last = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert last["nare"] == nare.__version__


def test_the_session_records_the_version_that_produced_it(
    tmp_path: Path, capsys: Any
) -> None:
    session_path = tmp_path / "s.json"

    main(
        ["run", "go", "--yes", "--session", str(session_path)],
        transport=FakeProvider([text_reply("done")]),
    )

    capsys.readouterr()
    assert json.loads(session_path.read_text(encoding="utf-8"))["nare"] == (
        nare.__version__
    )


def test_the_contract_description_names_the_version(capsys: Any) -> None:
    main(["contract"])

    assert json.loads(capsys.readouterr().out)["nare"] == nare.__version__


def test_the_version_is_derived_from_the_tag_not_written_by_hand() -> None:
    """A hand-written version drifts from the tag the release was cut at, and
    then a caller records one number while having installed another. The build
    takes it from the tag instead, so the two cannot disagree.
    """
    pyproject = (Path(__file__).parent.parent / "pyproject.toml").read_text(
        encoding="utf-8"
    )

    assert 'dynamic = ["version"]' in pyproject
    assert '[tool.hatch.version]\nsource = "vcs"' in pyproject
    assert 'version = "' not in pyproject.split("[project]")[1].split("[")[0]


def test_what_the_cli_prints_is_what_is_installed() -> None:
    from importlib.metadata import version

    assert nare.__version__ == version("nare")
