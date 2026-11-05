from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import pytest

from benchmarks.runner.config import Config
from benchmarks.runner.nare_args import (
    CASE_ONLY,
    HOST_FILE_FLAGS,
    RESERVED,
    RUN_ONLY,
    ArgsError,
    Pair,
    RunArgs,
    check_args,
    check_invocation,
    nare_contract,
    pairs,
    stage_inputs,
    variant,
)
from benchmarks.runner.sandbox import _AGENT_SCRIPT

NO_VALUE = {"--yes", "--jsonl", "--stream", "--bash-credentials", "--image-input"}
EFFORTS = ["low", "medium", "high", "xhigh", "max"]
# The contract's shape, cut down to the flags the rules name and a few free ones.
FLAGS: dict[str, dict[str, Any]] = {
    flag: {"takes_value": flag not in NO_VALUE, "choices": None}
    for flag in (
        *RESERVED,
        *RUN_ONLY,
        *CASE_ONLY,
        "--tools",
        "--context-window",
        "--require-reads",
        "--image-input",
    )
}
FLAGS["--effort"] = {"takes_value": True, "choices": EFFORTS}
TOOLS = ["ask", "bash", "edit", "list", "read", "search", "write"]


def as_run(*tokens: str) -> list[Pair]:
    return check_args(tokens, FLAGS, TOOLS)


def as_case(*tokens: str) -> list[Pair]:
    return check_args(tokens, FLAGS, TOOLS, case_id="demo")


def test_the_rules_name_the_specs_flags() -> None:
    assert set(RESERVED) == {
        "--yes", "--jsonl", "--session", "--max-turns", "--model", "--provider",
        "--base-url", "--max-tokens", "--prompt-file", "--resume", "--replay",
        "--stream", "--contract", "--bash-credentials",
    }  # fmt: skip
    assert RUN_ONLY == {
        "--system", "--system-file", "--append-system", "--append-system-file"
    }  # fmt: skip
    assert CASE_ONLY == {
        "--schema", "--mcp-config", "--command-tools", "--require-read", "--root"
    }  # fmt: skip
    assert HOST_FILE_FLAGS == {"--system-file", "--append-system-file"}


def test_both_forms_of_a_flag_give_the_same_pair() -> None:
    assert as_run("--effort", "low") == as_run("--effort=low") == [("--effort", "low")]


def test_a_flag_that_takes_no_value_gives_none() -> None:
    assert as_case("--image-input", "--tools", "read") == [
        ("--image-input", None),
        ("--tools", "read"),
    ]


def test_a_value_that_starts_with_a_dash_needs_the_equals_form() -> None:
    with pytest.raises(ArgsError, match=re.escape("--system needs a value")):
        as_run("--system", "-be terse")
    assert as_run("--system=-be terse") == [("--system", "-be terse")]


@pytest.mark.parametrize("value", ["read,list,search", "none", " read , list "])
def test_tools_may_name_the_tools_nare_lists_or_none(value: str) -> None:
    assert as_case("--tools", value) == [("--tools", value)]


@pytest.mark.parametrize(
    "tokens",
    [
        ["--mcp-config", "s.json", "--tools", "read,db__query"],
        # nare reads the whole list before it resolves a tool.
        ["--tools", "db__query,read", "--mcp-config=s.json"],
        # An alias may end in _, as nare's MCP config allows.
        ["--mcp-config", "s.json", "--tools", "db___query"],
    ],
)
def test_tools_may_name_an_mcp_tool_beside_the_config_that_serves_it(
    tokens: list[str],
) -> None:
    """nare resolves `<alias>__<tool>` from the config when it connects."""
    found = as_case(*tokens)
    assert ("--tools", tokens[tokens.index("--tools") + 1]) in found


@pytest.mark.parametrize("case_id", [None, "demo"])
def test_an_mcp_tool_without_an_mcp_config_is_refused(case_id: str | None) -> None:
    where = "run arguments" if case_id is None else "case demo"
    with pytest.raises(
        ArgsError,
        match=re.escape(f"{where}: --tools names 'db__query', an MCP tool"),
    ):
        check_args(["--tools", "read,db__query"], FLAGS, TOOLS, case_id=case_id)


@pytest.mark.parametrize("name", ["teleport", "__query", "1db__query", "d.b__query"])
def test_beside_an_mcp_config_a_name_nare_cannot_resolve_is_still_refused(
    name: str,
) -> None:
    """A plain name nare does not list, or an alias nare's config cannot have."""
    with pytest.raises(
        ArgsError, match=re.escape(f"case demo: --tools names {name!r}, which nare")
    ):
        as_case("--mcp-config", "s.json", "--tools", f"read,{name}")


@pytest.mark.parametrize(
    "tokens",
    [
        ["--command-tools", "tools.json", "--tools", "read,deploy"],
        # nare reads the whole list before it resolves a tool.
        ["--tools", "deploy,read", "--command-tools=tools.json"],
        # Both files in one list: a plain name is a command tool, and an
        # alias__tool name is an MCP tool.
        [
            "--command-tools", "tools.json", "--mcp-config", "s.json",
            "--tools", "deploy,db__query",
        ],
    ],
)  # fmt: skip
def test_tools_may_name_a_command_tool_beside_the_file_that_defines_it(
    tokens: list[str],
) -> None:
    """The file is in the work tree, which this check never reads: nare
    resolves the name when it starts, and a name it lacks stops the run."""
    found = as_case(*tokens)
    assert ("--tools", tokens[tokens.index("--tools") + 1]) in found


@pytest.mark.parametrize("case_id", [None, "demo"])
def test_a_tool_nare_does_not_list_without_a_command_tools_file_is_refused(
    case_id: str | None,
) -> None:
    where = "run arguments" if case_id is None else "case demo"
    with pytest.raises(
        ArgsError,
        match=re.escape(f"{where}: --tools names 'deploy', which nare does not list"),
    ):
        check_args(["--tools", "read,deploy"], FLAGS, TOOLS, case_id=case_id)


def test_beside_a_command_tools_file_an_mcp_name_still_needs_an_mcp_config() -> None:
    """A command tool's name has no __, so `db__query` is never one."""
    with pytest.raises(
        ArgsError,
        match=re.escape("case demo: --tools names 'db__query', an MCP tool"),
    ):
        as_case("--command-tools", "tools.json", "--tools", "db__query")


@pytest.mark.parametrize("name", ["__query", "dep.loy", "x" * 65])
def test_beside_a_command_tools_file_a_name_nare_cannot_have_is_still_refused(
    name: str,
) -> None:
    """Not a name nare's command tools allow: 1 to 64 letters, digits, _ or -."""
    with pytest.raises(
        ArgsError, match=re.escape(f"case demo: --tools names {name!r}, which nare")
    ):
        as_case("--command-tools", "tools.json", "--tools", f"read,{name}")


@pytest.mark.parametrize(
    ("tokens", "message"),
    [
        (["--no-such-flag"], "unknown flag '--no-such-flag'"),
        # nare's parser expands abbreviations; the check never does.
        (["--mod", "x"], "unknown flag '--mod'"),
        (["--context", "9000"], "unknown flag '--context'"),
        (["--effort"], "--effort needs a value"),
        (["--effort", "--context-window", "9"], "--effort needs a value"),
        (
            ["--effort", "extreme"],
            "--effort 'extreme' is not one of low, medium, high, xhigh, max",
        ),
        (["--image-input=yes"], "--image-input takes no value"),
        (["--tools", "read,teleport"], "--tools names 'teleport'"),
        (["--tools", "none,read"], "--tools names 'none'"),
        (["fix the bug"], "stray 'fix the bug'"),
        (["-"], "stray '-'"),
        (["--"], "a bare '--'"),
    ],
)  # fmt: skip
def test_arguments_nare_would_refuse_are_refused(
    tokens: list[str], message: str
) -> None:
    with pytest.raises(ArgsError, match=re.escape(f"run arguments: {message}")):
        check_args(tokens, FLAGS, TOOLS)


def test_a_case_refusal_names_the_case() -> None:
    with pytest.raises(ArgsError, match=re.escape("case demo: unknown flag '--nope'")):
        as_case("--nope")


@pytest.mark.parametrize(
    "tokens", [["--system", "a\udcffb"], ["--system=a\udcffb"], ["--a\udcffb"]]
)
@pytest.mark.parametrize("case_id", [None, "demo"])
def test_a_token_that_is_not_utf8_is_refused_before_any_other_rule(
    tokens: list[str], case_id: str | None
) -> None:
    # Bytes in argv that do not decode reach Python as lone surrogates, which a
    # results line and a baseline cannot hold as UTF-8 text.
    where = "run arguments" if case_id is None else "case demo"
    with pytest.raises(
        ArgsError, match=re.escape(f"{where}: {tokens[-1]!r} is not valid UTF-8")
    ):
        check_args(tokens, FLAGS, TOOLS, case_id=case_id)


@pytest.mark.parametrize("flag", sorted(RESERVED))
@pytest.mark.parametrize("case_id", [None, "demo"])
def test_a_reserved_flag_is_refused_in_either_list_and_either_form(
    flag: str, case_id: str | None
) -> None:
    forms = [[flag, "x"], [f"{flag}=x"]] if FLAGS[flag]["takes_value"] else [[flag]]
    reserved = re.escape(f"{flag} is reserved for {RESERVED[flag]}")
    for tokens in forms:
        with pytest.raises(ArgsError, match=reserved):
            check_args(tokens, FLAGS, TOOLS, case_id=case_id)


@pytest.mark.parametrize("flag", ["--model", "--provider", "--base-url"])
def test_a_flag_the_environment_owns_says_how_to_set_it(flag: str) -> None:
    """The likeliest mistake is `-- --model M`: the refusal says what to do."""
    with pytest.raises(ArgsError) as raised:
        as_run(flag, "x")
    message = str(raised.value)
    assert "bin/bench's own --model, --provider or --base-url" in message
    assert "before the --" in message
    assert "NARE_MODEL, NARE_PROVIDER or NARE_BASE_URL" in message
    assert ".py" not in message


@pytest.mark.parametrize("flag", sorted(RUN_ONLY))
def test_a_system_prompt_flag_is_a_run_argument_only(flag: str) -> None:
    assert as_run(flag, "x") == [(flag, "x")]
    with pytest.raises(
        ArgsError, match=re.escape(f"case demo: {flag} is a run argument")
    ):
        as_case(flag, "x")


@pytest.mark.parametrize(
    "tokens", [["--command-tools", "tools.json"], ["--command-tools=tools.json"]]
)
def test_the_command_tools_file_is_a_case_argument_only(tokens: list[str]) -> None:
    """Both forms: the file is a path in the fixture, as --mcp-config is."""
    assert as_case(*tokens) == [("--command-tools", "tools.json")]
    with pytest.raises(
        ArgsError,
        match=re.escape(
            "run arguments: --command-tools names a path in the work tree, "
            "so it belongs to a case's args"
        ),
    ):
        as_run(*tokens)


@pytest.mark.parametrize("flag", sorted(CASE_ONLY))
def test_a_work_tree_path_flag_is_a_case_argument_only(flag: str) -> None:
    assert as_case(flag, "x") == [(flag, "x")]
    with pytest.raises(
        ArgsError,
        match=re.escape(f"run arguments: {flag} names a path in the work tree"),
    ):
        as_run(flag, "x")


def test_a_flag_in_both_lists_is_refused_naming_the_flag_and_the_case() -> None:
    with pytest.raises(
        ArgsError,
        match=re.escape(
            "--context-window is in the run arguments and in case demo's args"
        ),
    ):
        check_invocation(
            ["--context-window", "16000"],
            {"demo": ["--context-window=8000"]},
            FLAGS,
            TOOLS,
        )


def test_repeats_within_one_list_are_left_alone() -> None:
    tokens = ["--require-read", "a.py", "--require-read", "b.py"]
    assert as_case(*tokens) == [("--require-read", "a.py"), ("--require-read", "b.py")]
    check_invocation([], {"demo": tokens}, FLAGS, TOOLS)


def test_the_invocation_checks_the_run_arguments_and_every_case() -> None:
    with pytest.raises(ArgsError, match=re.escape("case b: unknown flag '--nope'")):
        check_invocation(
            ["--effort", "low"],
            {"a": ["--tools", "read"], "b": ["--nope"]},
            FLAGS,
            TOOLS,
        )


def test_every_flag_the_rules_name_is_one_nare_has() -> None:
    """The real contract: a renamed nare flag fails here, not in a paid run."""
    flags, tools = nare_contract()
    assert {*RESERVED, *RUN_ONLY, *CASE_ONLY, *HOST_FILE_FLAGS, "--tools"} <= set(flags)
    check_invocation(
        ["--effort", "low", "--context-window", "16000", "--system-file", "p.md"],
        {"read-only-question": ["--tools", "read,list,search", "--require-reads", "1"]},
        flags,
        tools,
    )


def test_every_flag_the_agent_script_passes_is_reserved() -> None:
    """Read from the script itself, so a flag it gains cannot be passed twice."""
    script = set(re.findall(r"--[a-z][a-z-]*", _AGENT_SCRIPT))
    assert script, "the pattern found no flag in the agent script"
    assert script <= set(RESERVED)


def test_the_variant_ignores_order_and_the_equals_form() -> None:
    assert variant(["--effort", "low", "--context-window", "16000"], {}) == variant(
        ["--context-window=16000", "--effort=low"], {}
    )
    assert variant(["--effort", "low"], {}) == (("--effort", "low"),)


def test_the_variant_keeps_the_order_of_repeats() -> None:
    assert variant(["--b", "2", "--a", "x", "--b", "1"], {}) == (
        ("--a", "x"),
        ("--b", "2"),
        ("--b", "1"),
    )


def test_a_run_file_is_keyed_by_its_digest_not_its_path() -> None:
    one = variant(["--system-file", "a/terse.md"], {"a/terse.md": "sha256:aa"})
    assert one == variant(["--system-file=b/terse.md"], {"b/terse.md": "sha256:aa"})
    assert one != variant(["--system-file", "a/terse.md"], {"a/terse.md": "sha256:bb"})


def test_no_arguments_is_the_default_variant() -> None:
    assert variant([], {}) == ()


@pytest.mark.parametrize(
    ("tokens", "case_id"),
    [
        (["--image-input", "--effort", "low"], "demo"),
        (["--system=-x", "--context-window", "9"], None),
        (["--require-read", "a.py", "--require-read=b.py", "--image-input"], "demo"),
    ],
)
def test_the_key_parse_agrees_with_the_check(
    tokens: list[str], case_id: str | None
) -> None:
    assert pairs(tokens) == check_args(tokens, FLAGS, TOOLS, case_id=case_id)


def digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_arguments_without_a_file_are_not_staged(tmp_path: Path) -> None:
    dest = tmp_path / "inputs"
    staged = stage_inputs(["--effort", "low"], base=tmp_path, root=tmp_path, dest=dest)
    assert staged == RunArgs(recorded=("--effort", "low"), passed=("--effort", "low"))
    assert not dest.exists()


def test_a_system_file_is_copied_once_hashed_and_passed_under_in(
    tmp_path: Path,
) -> None:
    prompt = write(tmp_path / "benchmarks" / "prompts" / "terse.md", "Be terse.\n")
    dest = tmp_path / "results" / "stamp" / "inputs"
    staged = stage_inputs(
        ["--effort", "low", "--system-file", str(prompt)],
        base=tmp_path,
        root=tmp_path,
        dest=dest,
    )
    recorded = "benchmarks/prompts/terse.md"
    assert staged.recorded == ("--effort", "low", "--system-file", recorded)
    assert staged.files == {recorded: digest("Be terse.\n")}
    assert staged.passed == ("--effort", "low", "--system-file", "/in/0-terse.md")
    assert staged.inputs == dest
    # The copy is what the run measures: a later edit does not reach it.
    prompt.write_text("Edited mid-run.\n")
    assert (dest / "0-terse.md").read_text() == "Be terse.\n"


def test_the_equals_form_is_staged_in_place(tmp_path: Path) -> None:
    write(tmp_path / "p.md", "x")
    staged = stage_inputs(
        ["--append-system-file=p.md"],
        base=tmp_path,
        root=tmp_path,
        dest=tmp_path / "in",
    )
    assert staged.recorded == ("--append-system-file=p.md",)
    assert staged.passed == ("--append-system-file=/in/0-p.md",)


def test_two_files_with_one_name_do_not_overwrite_each_other(tmp_path: Path) -> None:
    write(tmp_path / "a" / "p.md", "one")
    write(tmp_path / "b" / "p.md", "two")
    dest = tmp_path / "in"
    staged = stage_inputs(
        ["--system-file", "a/p.md", "--append-system-file", "b/p.md"],
        base=tmp_path,
        root=tmp_path,
        dest=dest,
    )
    assert staged.passed == (
        "--system-file", "/in/0-p.md", "--append-system-file", "/in/1-p.md"
    )  # fmt: skip
    assert [(dest / n).read_text() for n in ("0-p.md", "1-p.md")] == ["one", "two"]


def test_a_relative_path_is_read_from_base_and_recorded_from_the_root(
    tmp_path: Path,
) -> None:
    write(tmp_path / "benchmarks" / "prompts" / "terse.md", "x")
    staged = stage_inputs(
        ["--system-file", "prompts/terse.md"],
        base=tmp_path / "benchmarks",
        root=tmp_path,
        dest=tmp_path / "in",
    )
    assert staged.recorded == ("--system-file", "benchmarks/prompts/terse.md")
    assert list(staged.files) == ["benchmarks/prompts/terse.md"]


def test_a_file_outside_the_checkout_is_recorded_by_its_absolute_path(
    tmp_path: Path,
) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    outside = write(tmp_path / "mine.md", "x").resolve().as_posix()
    staged = stage_inputs(
        ["--system-file", outside], base=root, root=root, dest=root / "in"
    )
    assert staged.recorded == ("--system-file", outside)
    assert list(staged.files) == [outside]


def test_a_run_file_that_cannot_be_read_is_refused(tmp_path: Path) -> None:
    with pytest.raises(
        ArgsError,
        match=re.escape("run arguments: --system-file absent.md: cannot read it"),
    ):
        stage_inputs(
            ["--system-file", "absent.md"],
            base=tmp_path,
            root=tmp_path,
            dest=tmp_path / "in",
        )


def test_a_run_file_that_is_not_utf8_is_refused_before_it_is_staged(
    tmp_path: Path,
) -> None:
    """nare reads a prompt file as UTF-8 at startup, after the image is built."""
    (tmp_path / "latin1.md").write_bytes("Sé breve.\n".encode("latin-1"))
    dest = tmp_path / "in"
    with pytest.raises(
        ArgsError,
        match=re.escape("run arguments: --system-file latin1.md: it is not UTF-8"),
    ):
        stage_inputs(
            ["--system-file", "latin1.md"], base=tmp_path, root=tmp_path, dest=dest
        )
    assert not dest.exists()


def test_a_config_passes_no_run_arguments_by_default() -> None:
    config = Config("m", "anthropic", None, "m", "k", "ANTHROPIC_API_KEY")
    assert config.run_args == RunArgs()
