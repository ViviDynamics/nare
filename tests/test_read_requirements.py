"""Read requirements: a final answer waits for the reads it must rest on, and
the result says what was read, with digests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from fake_provider import (
    FakeProvider,
    StreamingProvider,
    StructuredProvider,
    text_reply,
    tool_reply,
)
from nare.cli import main
from nare.loop import run
from nare.session import Session, new_session
from nare.tools import Policy


async def drain(session: Session, provider: Any, **kwargs: Any) -> list[Any]:
    return [event async for event in run(session, transport=provider, **kwargs)]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "a.txt").write_text("alpha\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("bravo\n", encoding="utf-8")
    return tmp_path


def reads_only(root: Path) -> Policy:
    return Policy(tools=frozenset({"read"}), root=root)


def sent_text(provider: FakeProvider, call: int) -> str:
    messages = provider.calls[call][0]
    return json.dumps([block for message in messages for block in message.content])


async def test_an_answer_after_one_read_is_held_until_two_distinct_reads(
    root: Path,
) -> None:
    provider = FakeProvider(
        [
            tool_reply("read", {"path": "a.txt"}),
            text_reply("answer one"),
            tool_reply("read", {"path": "b.txt"}),
            text_reply("answer two"),
        ]
    )
    session = new_session("go")

    await drain(session, provider, policy=reads_only(root), require_reads=2)

    assert session.status == "done"
    assert session.output is None
    assert [e["charged"] for e in session.schema_correction_log] == [True]
    assert session.schema_correction_log[0]["kind"] == "reads"
    assert "read at least 2 distinct files" in sent_text(provider, 2)


async def test_a_failed_read_and_a_read_outside_the_root_do_not_count(
    root: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("outside") / "secret.txt"
    outside.write_text("nope\n", encoding="utf-8")
    provider = FakeProvider(
        [
            tool_reply("read", {"path": "missing.txt"}),
            tool_reply("read", {"path": str(outside)}),
            text_reply("answer one"),
            tool_reply("read", {"path": "a.txt"}),
            tool_reply("read", {"path": "b.txt"}),
            text_reply("answer two"),
        ]
    )
    session = new_session("go")

    await drain(session, provider, policy=reads_only(root), require_reads=2)

    assert session.status == "done"
    assert [e["path"] for e in session.evidence if e["tool"] == "read"] == [
        "a.txt",
        "b.txt",
    ]
    assert len(session.schema_correction_log) == 1


async def test_a_named_path_must_be_read_before_the_answer(root: Path) -> None:
    provider = FakeProvider(
        [
            tool_reply("read", {"path": "a.txt"}),
            text_reply("answer one"),
            tool_reply("read", {"path": "b.txt"}),
            text_reply("answer two"),
        ]
    )
    session = new_session("go")

    await drain(session, provider, policy=reads_only(root), require_read=["b.txt"])

    assert session.status == "done"
    assert "read b.txt" in sent_text(provider, 2)


async def test_a_read_correction_is_never_free_and_keeps_tools_offered(
    root: Path,
) -> None:
    provider = StructuredProvider(
        [
            text_reply("{}"),
            tool_reply("read", {"path": "a.txt"}),
            text_reply("{}"),
        ]
    )
    session = new_session("go")
    schema = {"type": "object"}

    await drain(
        session,
        provider,
        policy=reads_only(root),
        schema=schema,
        structured_output="json_schema",
        require_reads=1,
        max_corrections=3,
    )

    assert session.status == "done"
    assert session.schema_retried is False
    assert provider.constraints == [None, None, None]
    assert [bool(tools) for _, tools in provider.calls] == [True, True, True]


async def test_a_spent_budget_ends_the_run_with_its_own_stop_reason(
    root: Path,
) -> None:
    provider = FakeProvider([text_reply("answer one"), text_reply("answer two")])
    session = new_session("go")

    await drain(session, provider, policy=reads_only(root), require_reads=1)

    assert session.status == "error"
    assert session.stop_reason == "reads_required"
    assert "a.txt" not in (session.error or "")
    assert "read at least 1 distinct files" in (session.error or "")


async def test_without_the_option_an_unread_answer_is_accepted(root: Path) -> None:
    provider = FakeProvider([text_reply("answer")])
    session = new_session("go")

    await drain(session, provider, policy=reads_only(root))

    assert session.status == "done"
    assert session.schema_correction_log == []


def test_the_cli_lists_each_distinct_read_with_its_digest(
    root: Path, capsys: Any
) -> None:
    code = main(
        [
            "run",
            "go",
            "--yes",
            "--jsonl",
            "--root",
            str(root),
            "--tools",
            "read",
            "--require-reads",
            "2",
        ],
        transport=FakeProvider(
            [
                tool_reply("read", {"path": "a.txt"}),
                tool_reply("read", {"path": "a.txt"}),
                tool_reply("read", {"path": "b.txt"}),
                text_reply("done reading"),
            ]
        ),
    )

    last = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0
    assert last["status"] == "done"
    assert last["reads"] == [
        {
            "path": "a.txt",
            "sha256": hashlib.sha256(b"alpha\n").hexdigest(),
            "size": 6,
        },
        {
            "path": "b.txt",
            "sha256": hashlib.sha256(b"bravo\n").hexdigest(),
            "size": 6,
        },
    ]
    assert last["budget"]["schema_corrections"] == 1
    assert last["budget"]["used_schema_corrections"] == 0


def test_a_run_without_the_option_reports_no_reads_key(root: Path, capsys: Any) -> None:
    code = main(
        ["run", "go", "--yes", "--jsonl", "--root", str(root), "--tools", "read"],
        transport=FakeProvider(
            [tool_reply("read", {"path": "a.txt"}), text_reply("ok")]
        ),
    )

    last = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0
    assert "reads" not in last


@pytest.mark.parametrize(
    "extra",
    [
        ["--require-reads", "0"],
        ["--require-reads", "-1"],
        ["--require-read", ""],
        ["--require-reads", "1", "--tools", "bash"],
    ],
)
def test_invalid_requirements_never_start(
    root: Path, capsys: Any, extra: list[str]
) -> None:
    code = main(
        ["run", "go", "--yes", "--jsonl", "--root", str(root), *extra],
        transport=FakeProvider([text_reply("x")]),
    )

    assert code == 2
    assert capsys.readouterr().out == ""


def test_a_requirement_through_the_library_refuses_a_tool_free_policy(
    root: Path,
) -> None:
    import asyncio

    session = new_session("go")
    provider = FakeProvider([text_reply("x")])

    async def go() -> None:
        await drain(
            session,
            provider,
            policy=Policy(tools=frozenset(), root=root),
            require_reads=1,
        )

    with pytest.raises(ValueError, match="read"):
        asyncio.run(go())


async def test_a_read_hold_after_a_schema_correction_offers_tools_again(
    root: Path,
) -> None:
    # A schema correction constrains the next turn. After a valid answer that
    # the read hold sends back, the turn that follows must offer tools again:
    # a withheld read tool would end the run with reads_required for nothing.
    provider = StructuredProvider(
        [
            text_reply("not json"),
            text_reply("{}"),
            tool_reply("read", {"path": "a.txt"}),
            text_reply("{}"),
        ]
    )
    session = new_session("go")

    await drain(
        session,
        provider,
        policy=reads_only(root),
        schema={"type": "object"},
        structured_output="json_schema",
        require_reads=1,
        max_corrections=2,
    )

    assert session.status == "done"
    assert provider.constraints[1] is not None
    assert provider.constraints[2] is None
    assert [bool(tools) for _, tools in provider.calls[2:]] == [True, True]


async def test_an_answer_before_any_read_is_held(root: Path) -> None:
    provider = FakeProvider(
        [text_reply("guess"), tool_reply("read", {"path": "a.txt"}), text_reply("ok")]
    )
    session = new_session("go")

    await drain(session, provider, policy=reads_only(root), require_reads=1)

    assert session.status == "done"
    assert "read at least 1 distinct files (read 0 so far)" in sent_text(provider, 1)


async def test_a_streamed_run_is_held_until_it_has_read(root: Path) -> None:
    provider = StreamingProvider(
        [
            text_reply("too early"),
            tool_reply("read", {"path": "b.txt"}),
            text_reply("now"),
        ]
    )
    session = new_session("go")

    await drain(session, provider, policy=reads_only(root), require_read=["b.txt"])

    assert session.status == "done"
    assert [e["charged"] for e in session.schema_correction_log] == [True]


async def test_a_link_inside_the_root_counts_as_its_target(root: Path) -> None:
    (root / "real.txt").write_text("real\n", encoding="utf-8")
    (root / "link.txt").symlink_to(root / "real.txt")
    provider = FakeProvider(
        [
            tool_reply("read", {"path": "link.txt"}),
            text_reply("answer"),
        ]
    )
    session = new_session("go")

    await drain(session, provider, policy=reads_only(root), require_read=["link.txt"])

    assert session.status == "done"
    assert session.schema_correction_log == []


def test_a_link_out_of_the_root_is_refused_at_startup(
    root: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    from nare.reads import build

    outside = tmp_path_factory.mktemp("elsewhere") / "x.txt"
    outside.write_text("x\n", encoding="utf-8")
    (root / "escape.txt").symlink_to(outside)

    with pytest.raises(ValueError, match="outside the root"):
        build(0, ["escape.txt"], root=root)


async def test_without_a_root_an_absolute_path_outside_cwd_counts(
    tmp_path: Path,
) -> None:
    target = tmp_path / "far.txt"
    target.write_text("far\n", encoding="utf-8")
    provider = FakeProvider(
        [tool_reply("read", {"path": str(target)}), text_reply("answer")]
    )
    session = new_session("go")

    await drain(
        session,
        provider,
        policy=Policy(tools=frozenset({"read"})),
        require_read=[str(target)],
    )

    assert session.status == "done"


def test_max_corrections_is_allowed_with_a_read_requirement_and_no_schema(
    root: Path, capsys: Any
) -> None:
    code = main(
        [
            "run",
            "go",
            "--yes",
            "--jsonl",
            "--root",
            str(root),
            "--tools",
            "read",
            "--require-reads",
            "1",
            "--max-corrections",
            "2",
        ],
        transport=FakeProvider(
            [tool_reply("read", {"path": "a.txt"}), text_reply("ok")]
        ),
    )

    last = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0
    assert last["budget"]["schema_corrections"] == 2


CITES_SCHEMA = {
    "type": "object",
    "properties": {
        "checks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        }
    },
    "required": ["checks"],
}


def cites(*paths: str) -> str:
    return json.dumps({"checks": [{"path": path} for path in paths]})


async def test_a_cited_path_that_was_never_read_is_held_then_accepted(
    root: Path,
) -> None:
    provider = FakeProvider(
        [
            text_reply(cites("b.txt")),
            tool_reply("read", {"path": "b.txt"}),
            text_reply(cites("b.txt")),
        ]
    )
    session = new_session("go")

    await drain(
        session,
        provider,
        policy=reads_only(root),
        schema=CITES_SCHEMA,
        require_cited_reads=["/checks/*/path"],
    )

    assert session.status == "done", session.error
    assert session.output == {"checks": [{"path": "b.txt"}]}
    assert [e["kind"] for e in session.schema_correction_log] == ["reads"]
    assert "read b.txt, which the answer cites" in sent_text(provider, 1)


async def test_a_cited_path_read_before_the_answer_is_not_held(root: Path) -> None:
    provider = FakeProvider(
        [tool_reply("read", {"path": "a.txt"}), text_reply(cites("a.txt"))]
    )
    session = new_session("go")

    await drain(
        session,
        provider,
        policy=reads_only(root),
        schema=CITES_SCHEMA,
        require_cited_reads=["/checks/*/path"],
    )

    assert session.status == "done", session.error
    assert session.schema_correction_log == []


async def test_a_partial_read_of_a_cited_file_counts(root: Path) -> None:
    provider = FakeProvider(
        [
            tool_reply("read", {"path": "b.txt", "offset": 1, "limit": 1}),
            text_reply(cites("b.txt")),
        ]
    )
    session = new_session("go")

    await drain(
        session,
        provider,
        policy=reads_only(root),
        schema=CITES_SCHEMA,
        require_cited_reads=["/checks/*/path"],
    )

    assert session.status == "done", session.error
    assert session.schema_correction_log == []


async def test_the_wildcard_names_only_the_unread_paths(root: Path) -> None:
    provider = FakeProvider(
        [
            tool_reply("read", {"path": "a.txt"}),
            text_reply(cites("a.txt", "b.txt")),
            tool_reply("read", {"path": "b.txt"}),
            text_reply(cites("a.txt", "b.txt")),
        ]
    )
    session = new_session("go")

    await drain(
        session,
        provider,
        policy=reads_only(root),
        schema=CITES_SCHEMA,
        require_cited_reads=["/checks/*/path"],
    )

    assert session.status == "done", session.error
    sent = sent_text(provider, 2)
    assert "read b.txt, which the answer cites" in sent
    assert "read a.txt, which the answer cites" not in sent


async def test_a_cited_path_outside_the_root_is_never_satisfied(
    root: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("outside") / "secret.txt"
    outside.write_text("nope\n", encoding="utf-8")
    provider = FakeProvider(
        [
            text_reply(cites(str(outside))),
            text_reply(cites(str(outside))),
        ]
    )
    session = new_session("go")

    await drain(
        session,
        provider,
        policy=reads_only(root),
        schema=CITES_SCHEMA,
        require_cited_reads=["/checks/*/path"],
    )

    assert session.status == "error"
    assert session.stop_reason == "reads_required"
    assert "remove" in sent_text(provider, 1)


async def test_a_spent_budget_ends_a_cited_hold_with_reads_required(
    root: Path,
) -> None:
    provider = FakeProvider([text_reply(cites("b.txt")), text_reply(cites("b.txt"))])
    session = new_session("go")

    await drain(
        session,
        provider,
        policy=reads_only(root),
        schema=CITES_SCHEMA,
        require_cited_reads=["/checks/*/path"],
    )

    assert session.status == "error"
    assert session.stop_reason == "reads_required"
    assert "read b.txt, which the answer cites" in (session.error or "")


async def test_a_cited_hold_shares_the_correction_budget(root: Path) -> None:
    provider = FakeProvider(
        [
            text_reply(cites("b.txt")),
            tool_reply("read", {"path": "b.txt"}),
            text_reply(cites("b.txt")),
        ]
    )
    session = new_session("go")

    await drain(
        session,
        provider,
        policy=reads_only(root),
        schema=CITES_SCHEMA,
        require_cited_reads=["/checks/*/path"],
        max_corrections=2,
    )

    assert session.status == "done", session.error
    assert session.schema_corrections == 1
    assert session.schema_retried is False


async def test_a_check_with_two_positional_parameters_receives_the_reads(
    root: Path,
) -> None:
    seen: list[Any] = []

    def check(answer: Any, reads: list[dict[str, Any]]) -> list[str]:
        seen.append([record["path"] for record in reads])
        return []

    provider = FakeProvider(
        [tool_reply("read", {"path": "a.txt"}), text_reply(cites("a.txt"))]
    )
    session = new_session("go")

    await drain(
        session,
        provider,
        policy=reads_only(root),
        schema=CITES_SCHEMA,
        check=check,
    )

    assert session.status == "done", session.error
    assert seen == [["a.txt"]]


async def test_a_one_argument_check_is_called_with_the_answer_alone(root: Path) -> None:
    seen: list[Any] = []

    def check(answer: Any, *extra: Any) -> list[str]:
        seen.append(extra)
        return []

    provider = FakeProvider([text_reply(cites("a.txt"))])
    session = new_session("go")

    await drain(
        session, provider, policy=reads_only(root), schema=CITES_SCHEMA, check=check
    )

    assert session.status == "done", session.error
    assert seen == [()]


@pytest.mark.parametrize(
    "pointer",
    ["", "checks", "/checks/", "/a/~2", "/a//b"],
)
def test_a_malformed_pointer_never_starts(root: Path, pointer: str) -> None:
    from nare.reads import build

    with pytest.raises(ValueError, match="pointer"):
        build(0, [], root=root, cited=[pointer])


async def test_cited_reads_need_a_schema(root: Path) -> None:
    session = new_session("go")
    provider = FakeProvider([text_reply("x")])

    with pytest.raises(ValueError, match="needs a schema"):
        await drain(
            session,
            provider,
            policy=reads_only(root),
            require_cited_reads=["/checks/*/path"],
        )


def test_the_cli_refuses_cited_reads_without_a_schema(root: Path, capsys: Any) -> None:
    code = main(
        [
            "run",
            "go",
            "--yes",
            "--jsonl",
            "--root",
            str(root),
            "--tools",
            "read",
            "--require-cited-reads",
            "/checks/*/path",
        ],
        transport=FakeProvider([text_reply("x")]),
    )

    assert code == 2
    assert capsys.readouterr().out == ""


def test_the_cli_reports_the_reads_for_cited_reads(
    root: Path, tmp_path: Path, capsys: Any
) -> None:
    schema_file = tmp_path / "schema.json"
    schema_file.write_text(json.dumps(CITES_SCHEMA), encoding="utf-8")
    code = main(
        [
            "run",
            "go",
            "--yes",
            "--jsonl",
            "--root",
            str(root),
            "--tools",
            "read",
            "--schema",
            str(schema_file),
            "--require-cited-reads",
            "/checks/*/path",
        ],
        transport=FakeProvider(
            [
                text_reply(cites("b.txt")),
                tool_reply("read", {"path": "b.txt"}),
                text_reply(cites("b.txt")),
            ]
        ),
    )

    last = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0
    assert last["status"] == "done"
    assert [record["path"] for record in last["reads"]] == ["b.txt"]
    assert last["corrections"][0]["kind"] == "reads"
