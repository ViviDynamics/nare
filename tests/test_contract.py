"""The machine contract: one version, documented exit codes, a stated refusal."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fake_provider import FakeProvider, text_reply, tool_reply
from nare.cli import main
from nare.contract import CONTRACT_VERSION, EXIT_CODES, describe
from nare.events import REDACTION_RULES, REDACTION_VERSION
from nare.session import Session, loads


def test_every_terminal_status_has_an_exit_code() -> None:
    assert set(EXIT_CODES) == {"done", "blocked", "error"}


def test_the_description_carries_the_version_and_the_exit_codes() -> None:
    described = describe()

    assert described["contract"] == CONTRACT_VERSION
    assert described["exit_codes"] == dict(EXIT_CODES)
    assert "never_started" in described


def test_the_contract_names_the_redaction_rule_set() -> None:
    described = describe()

    assert described["redaction"]["version"] == REDACTION_VERSION
    assert described["redaction"]["rules"] == list(REDACTION_RULES)


def test_the_redaction_identity_is_defined_once() -> None:
    """Same rule as CONTRACT_VERSION: two constants that must agree are a
    version skew waiting for the release where somebody bumps only one of
    them. contract.py may import the redaction identity but not declare it.
    """
    import nare.contract
    import nare.events

    assert (
        Path(nare.events.__file__)
        .read_text(encoding="utf-8")
        .count("REDACTION_VERSION")
        > 0
    )
    assert (
        Path(nare.contract.__file__)
        .read_text(encoding="utf-8")
        .count("REDACTION_VERSION =")
        == 0
    )


def test_the_contract_subcommand_prints_it(capsys: Any) -> None:
    code = main(["contract"])

    printed = json.loads(capsys.readouterr().out)
    assert code == 0
    assert printed["contract"] == CONTRACT_VERSION
    assert printed["event_types"] == list(describe()["event_types"])


def test_a_run_refuses_a_contract_it_does_not_speak(capsys: Any) -> None:
    code = main(["run", "go", "--yes", "--contract", "99"])

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "99" in captured.err and str(CONTRACT_VERSION) in captured.err


def test_a_run_accepts_the_contract_it_speaks(capsys: Any) -> None:
    code = main(
        ["run", "go", "--yes", "--jsonl", "--contract", str(CONTRACT_VERSION)],
        transport=FakeProvider([text_reply("done")]),
    )

    capsys.readouterr()
    assert code == 0


def test_the_result_line_carries_the_contract_version(capsys: Any) -> None:
    main(
        ["run", "go", "--yes", "--jsonl"],
        transport=FakeProvider([text_reply("done")]),
    )

    last = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert last["contract"] == CONTRACT_VERSION


def test_a_completed_run_exits_zero(capsys: Any) -> None:
    code = main(["run", "go", "--yes"], transport=FakeProvider([text_reply("done")]))

    capsys.readouterr()
    assert code == EXIT_CODES["done"] == 0


def test_a_blocked_run_exits_zero(capsys: Any) -> None:
    code = main(
        ["run", "go", "--yes"],
        transport=FakeProvider([tool_reply("ask", {"questions": ["which?"]})]),
    )

    capsys.readouterr()
    assert code == EXIT_CODES["blocked"] == 0


def test_a_failed_run_exits_one(capsys: Any) -> None:
    code = main(
        ["run", "go", "--yes", "--max-cutoffs", "0"],
        transport=FakeProvider([text_reply("cut off", stop_reason="max_tokens")]),
    )

    capsys.readouterr()
    assert code == EXIT_CODES["error"] == 1


def test_the_session_file_carries_the_contract(tmp_path: Path, capsys: Any) -> None:
    session_path = tmp_path / "s.json"

    main(
        ["run", "go", "--yes", "--session", str(session_path)],
        transport=FakeProvider([text_reply("done")]),
    )

    capsys.readouterr()
    assert json.loads(session_path.read_text(encoding="utf-8"))["contract"] == (
        CONTRACT_VERSION
    )


def test_a_session_from_another_contract_is_refused(tmp_path: Path) -> None:
    session_path = tmp_path / "s.json"
    main(
        ["run", "go", "--yes", "--session", str(session_path)],
        transport=FakeProvider([text_reply("done")]),
    )
    raw = json.loads(session_path.read_text(encoding="utf-8"))
    raw["contract"] = CONTRACT_VERSION + 1
    session_path.write_text(json.dumps(raw), encoding="utf-8")

    try:
        loads(session_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        assert "contract" in str(exc)
    else:  # pragma: no cover - the refusal is the point
        raise AssertionError("a session from another contract must be refused")


def test_the_documented_contract_matches_the_code() -> None:
    doc = Path(__file__).parent.parent / "docs" / "contract.md"
    text = doc.read_text(encoding="utf-8")

    for status, code in EXIT_CODES.items():
        assert status in text
        assert f"exit {code}" in text
    for event_type in describe()["event_types"]:
        assert event_type in text
    assert f"contract version {CONTRACT_VERSION}" in text


def test_the_documented_contract_names_the_redaction_rules() -> None:
    doc = Path(__file__).parent.parent / "docs" / "contract.md"
    text = doc.read_text(encoding="utf-8")

    assert "nare redact" in text
    for rule in describe()["redaction"]["rules"]:
        assert rule in text


def test_the_contract_version_is_defined_once() -> None:
    """Two constants that must agree are a version skew waiting for the release
    where somebody bumps only one of them. Identity proves nothing here, since
    CPython interns small integers, so this reads the source: session.py may
    import the version but must not declare one.
    """
    import nare.contract
    import nare.session

    assert Session(id="x").contract == nare.contract.CONTRACT_VERSION
    source = Path(nare.session.__file__).read_text(encoding="utf-8")
    declarations = [
        line
        for line in source.splitlines()
        if line.startswith("CONTRACT_VERSION") and "=" in line
    ]
    assert declarations == []


def test_contract_describes_prompt_input_routes() -> None:
    assert describe()["prompt_input"] == {
        "file_flag": "--prompt-file",
        "stdin_positional": "-",
        "encoding": "utf-8",
        "exclusive": True,
    }


def test_contract_describes_system_prompt_routes() -> None:
    assert describe()["system_input"] == {
        "file_flags": {
            "--system": "--system-file",
            "--append-system": "--append-system-file",
        },
        "encoding": "utf-8",
        "exclusive": True,
        "session_field": "system",
        "resume": "recorded",
    }


def test_contract_describes_read_image_input() -> None:
    assert describe()["image_input"] == {
        "flag": "--image-input",
        "tool": "read",
        "media_types": ["image/png", "image/jpeg"],
        "max_bytes": 4194304,
        "max_pixels": 20000000,
        "max_edge": 8000,
    }


def test_contract_describes_structured_output() -> None:
    assert describe()["structured_output"] == {
        "flag": "--structured-output",
        "modes": ["json_schema"],
        "requires": "--schema",
        "providers": ["anthropic", "bedrock", "openai"],
        "constrained_turns": ["schema_correction", "tool_free_run"],
        "anthropic_not_enforced_at_decode": describe()["structured_output"][
            "anthropic_not_enforced_at_decode"
        ],
        "event": "progress",
        "event_detail": "structured_output",
    }


def test_the_documented_contract_describes_structured_output() -> None:
    doc = Path(__file__).parent.parent / "docs" / "contract.md"
    text = doc.read_text(encoding="utf-8")

    assert "## Schema-constrained answers" in text
    assert "--structured-output json_schema" in text
    assert '"response_format"' in text
    assert '"output_config"' in text


def test_the_advertised_modes_are_the_ones_the_loop_accepts() -> None:
    from nare.loop import STRUCTURED_OUTPUT_MODES

    assert describe()["structured_output"]["modes"] == list(STRUCTURED_OUTPUT_MODES)


def test_contract_names_the_built_in_tools() -> None:
    from nare.tools import TOOLS

    assert describe()["tools"] == sorted(TOOLS)


def test_contract_describes_read_only_navigation() -> None:
    assert describe()["navigation"] == {
        "tools": ["list", "search"],
        "max_output": 30000,
        "page_limit": {"list": 200, "search": 100, "max": 1000},
        "cursor": "opaque",
        "match_width": 200,
        "line_numbers_from": 1,
        "follows_symlinks": False,
        "skipped_dirs": [".git"],
    }


def test_the_documented_contract_describes_navigation() -> None:
    doc = Path(__file__).parent.parent / "docs" / "contract.md"
    text = doc.read_text(encoding="utf-8")

    assert "## Read-only navigation" in text
    assert "--tools read,list,search" in text
    assert '[next page: cursor "' in text


def test_contract_describes_read_evidence() -> None:
    assert describe()["evidence"] == {
        "tools": ["list", "read", "search"],
        "event": "progress",
        "event_detail": "evidence",
        "session_field": "evidence",
        "digest": "sha256",
        "line_numbers_from": 1,
        "byte_end": "exclusive",
    }


def test_the_documented_contract_describes_read_evidence() -> None:
    doc = Path(__file__).parent.parent / "docs" / "contract.md"
    text = doc.read_text(encoding="utf-8")

    assert "## Read evidence" in text
    assert '"evidence"' in text and '"sha256"' in text


def test_contract_describes_tool_results() -> None:
    assert describe()["tool_results"] == {
        "event": "progress",
        "event_detail": "tool_result",
        "id_field": "tool_use_id",
        "id_on": ["tool_use", "tool_result", "evidence", "image"],
        "fields": ["tool_use_id", "is_error", "chars"],
        "content": {"built_in": False, "mcp": True, "caller": True},
    }


def test_the_documented_contract_describes_tool_results() -> None:
    doc = Path(__file__).parent.parent / "docs" / "contract.md"
    text = doc.read_text(encoding="utf-8")

    assert "## Tool results" in text
    assert '"tool_use_id"' in text and '"chars"' in text
    assert "`tool_results`" in text


def test_contract_describes_the_bash_environment() -> None:
    assert describe()["bash_env"] == {
        "withheld": [
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "AWS_ACCESS_KEY_ID",
            "AWS_BEARER_TOKEN_BEDROCK",
            "AWS_CONTAINER_AUTHORIZATION_TOKEN",
            "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SECURITY_TOKEN",
            "AWS_SESSION_TOKEN",
            "AWS_WEB_IDENTITY_TOKEN_FILE",
            "OPENAI_API_KEY",
        ],
        "opt_in_flag": "--bash-credentials",
        "policy_field": "bash_credentials",
    }


def test_the_documented_contract_states_the_bash_environment_rule() -> None:
    doc = Path(__file__).parent.parent / "docs" / "contract.md"
    text = doc.read_text(encoding="utf-8")

    assert "## The bash environment" in text
    assert "--bash-credentials" in text
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY"):
        assert name in text.split("## The bash environment")[1].split("\n## ")[0]


def test_contract_describes_the_tool_output_cap() -> None:
    assert describe()["tool_output"] == {
        "max_chars": 30000,
        "bash": "head_and_tail_per_stream",
        "mcp": "head_and_tail",
        "caller": "head_and_tail",
        "read": "head_then_continue_notice",
    }


def test_the_documented_contract_describes_the_tool_output_cap() -> None:
    doc = Path(__file__).parent.parent / "docs" / "contract.md"
    text = doc.read_text(encoding="utf-8")

    assert "## The tool output cap" in text
    assert "continue with offset=" in text
    assert "characters of stdout cut here" in text


def test_contract_describes_tool_argument_checks() -> None:
    assert describe()["tool_arguments"] == {
        "checked": "built_in",
        "against": "input_schema",
        "before": "approval",
        "unknown_parameters": "refused",
        "null_optional": "absent",
        "malformed_json": "is_error",
        "repeated_keys": "is_error",
    }


def test_the_documented_contract_describes_tool_argument_checks() -> None:
    doc = Path(__file__).parent.parent / "docs" / "contract.md"
    text = doc.read_text(encoding="utf-8")

    assert "## Tool arguments" in text
    assert "bash: timeout: expected integer, got string" in text
    assert "edit: missing required parameter new" in text


def test_the_documented_contract_describes_compaction_modes() -> None:
    from nare.compact import COMPACTION_MODES
    from nare.loop import SUMMARY_STATUSES

    text = (Path(__file__).parent.parent / "docs" / "contract.md").read_text(
        encoding="utf-8"
    )
    assert "Contract version 3 covers" in text
    assert "Contract version 2 covers" not in text
    section = text.split("### Compaction modes", 1)[1].split("\n## ", 1)[0]
    for mode in COMPACTION_MODES:
        assert f"| `{mode}`" in section
    for status in SUMMARY_STATUSES:
        assert f"| `{status}` |" in section
    for key in ("status", "error", "turns", "messages", "replaced_chars", "chars"):
        assert f"| `{key}` |" in section
    for key in ("usage", "text"):
        assert f"| `{key}` |" in section
    assert "[summary by nare:" in section
    assert "`summaries`" in section


def test_contract_describes_reported_cost_sources() -> None:
    assert describe()["reported_cost"] == {
        "proxy": "litellm",
        "headers": ["x-litellm-response-cost", "x-litellm-response-cost-original"],
        "header_rails": ["anthropic", "openai"],
        "streamed": {"openai": "usage.cost"},
        "estimated": False,
    }


def test_the_documented_contract_describes_streamed_reported_cost() -> None:
    doc = Path(__file__).parent.parent / "docs" / "contract.md"
    text = " ".join(doc.read_text(encoding="utf-8").split())

    assert "Streamed cost headers are ignored" not in text
    assert "`usage.cost` on the final usage chunk" in text
    assert "`reported_cost`" in text


COMPACTION_ENV = (
    "NARE_COMPACTION",
    "NARE_COMPACTION_TRIGGER",
    "NARE_COMPACTION_TARGET",
)


def test_contract_advertises_the_built_in_compaction_defaults() -> None:
    """#300: a caller that pins behavior across upgrades reads the values a run
    uses when it passes no flag, and notices when a release changes one."""
    entry = describe()["compaction"]

    assert entry["default"] == {"mode": "elide", "trigger": 0.8, "target": 0.6}
    assert entry["flags"] == {
        "mode": "--compaction",
        "trigger": "--compaction-trigger",
        "target": "--compaction-target",
    }
    assert entry["env"] == {
        "mode": "NARE_COMPACTION",
        "trigger": "NARE_COMPACTION_TRIGGER",
        "target": "NARE_COMPACTION_TARGET",
    }
    assert entry["modes"] == ["elide", "summary"]
    assert entry["valid"] == "0 < target < trigger <= 1"
    assert entry["unit"] == "share of the context window"
    assert entry["per"] == "invocation"
    assert entry["saved"] is False
    for name in entry["flags"].values():
        assert name in describe()["flags"]


def test_the_advertised_compaction_defaults_are_what_a_run_uses(
    monkeypatch: Any,
) -> None:
    import inspect

    import nare
    from nare.cli import build_parser

    for name in COMPACTION_ENV:
        monkeypatch.delenv(name, raising=False)
    default = describe()["compaction"]["default"]

    args = build_parser().parse_args(["run", "task"])
    assert (args.compaction, args.compaction_trigger, args.compaction_target) == (
        default["mode"],
        default["trigger"],
        default["target"],
    )
    params = inspect.signature(nare.run).parameters
    assert params["compaction"].default == default["mode"]
    assert params["compaction_trigger"].default == default["trigger"]
    assert params["compaction_target"].default == default["target"]


def test_the_compaction_defaults_do_not_follow_the_environment(
    monkeypatch: Any,
) -> None:
    """The contract says what this build does, not how this machine is set."""
    for name in COMPACTION_ENV:
        monkeypatch.delenv(name, raising=False)
    unset = describe()["compaction"]
    monkeypatch.setenv("NARE_COMPACTION", "summary")
    monkeypatch.setenv("NARE_COMPACTION_TRIGGER", "0.5")
    monkeypatch.setenv("NARE_COMPACTION_TARGET", "0.3")

    assert describe()["compaction"] == unset


def test_the_documented_contract_advertises_the_compaction_defaults() -> None:
    text = (Path(__file__).parent.parent / "docs" / "contract.md").read_text(
        encoding="utf-8"
    )
    flags = text.split("## Run flags", 1)[1].split("\n## ", 1)[0]
    assert "Defaults are not listed" not in flags
    assert "under `compaction`" in flags
    assert "not yet advertised" in flags
    section = text.split("### Compaction modes", 1)[1].split("\n## ", 1)[0]
    block = section.split('```json\n"compaction": ', 1)[1].split("\n```", 1)[0]
    assert json.loads(block) == describe()["compaction"]


def test_a_run_follows_the_advertised_compaction_environment(monkeypatch: Any) -> None:
    """Each variable the entry names is one `nare run` reads."""
    from nare.cli import build_parser

    entry = describe()["compaction"]
    values = {"mode": "summary", "trigger": 0.5, "target": 0.3}
    for key, name in entry["env"].items():
        monkeypatch.setenv(name, str(values[key]))
    args = build_parser().parse_args(["run", "task"])

    assert (args.compaction, args.compaction_trigger, args.compaction_target) == (
        values["mode"],
        values["trigger"],
        values["target"],
    )
