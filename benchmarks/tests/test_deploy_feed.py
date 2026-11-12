"""The deploy-feed-window case (#43): a paged feed that hands each page out once.

The case exists to rank compaction points. Its feed is sized so the default
trigger never compacts and every lower point drops pages, and a dropped page
cannot be fetched again. These tests pin the feed's behaviour, its size, and
each of the case's checks.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

from benchmarks.tests.test_cases import cases, check_with, fixture_checkout, run_here
from nare.compact import PREFIX, CompactionPoint, compact, context_chars
from nare.session import Message, new_session, save
from nare.tools import MAX_TOOL_OUTPUT

CASE = "deploy-feed-window"
PAGES = 8
SERVICES = 16
WINDOW = 64_000
LIVE = re.compile(r"deploy service=(\S+) build=([0-9a-f]{7}) status=live$", re.M)
DEPLOY = re.compile(r"deploy service=(\S+) build=([0-9a-f]{7}) status=(\S+)$", re.M)
CURSOR = re.compile(r"^next cursor: (\S+)$", re.M)


def feed_copy(path: Path) -> Path:
    shutil.copytree(cases()[CASE].fixture, path, dirs_exist_ok=True)
    return path


def next_page(work: Path, args: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    """One next_page call as nare makes it: the arguments as JSON on stdin."""
    return subprocess.run(
        [sys.executable, "feed.py"],
        cwd=work,
        input=json.dumps(args),
        capture_output=True,
        text=True,
    )


def read_feed(work: Path) -> list[str]:
    """Every page, in order, each fetched with the cursor the last one gave."""
    pages: list[str] = []
    args: dict[str, Any] = {}
    while len(pages) <= PAGES:
        done = next_page(work, args)
        assert done.returncode == 0, done.stderr
        pages.append(done.stdout)
        found = CURSOR.search(done.stdout)
        if found is None:
            return pages
        args = {"cursor": found.group(1)}
    raise AssertionError("the feed did not end")


@pytest.fixture(scope="module")
def pages(tmp_path_factory: pytest.TempPathFactory) -> list[str]:
    return read_feed(feed_copy(tmp_path_factory.mktemp("feed")))


def live_builds(pages: list[str]) -> dict[str, str]:
    return {s: b for page in pages for s, b in LIVE.findall(page)}


def other_builds(pages: list[str]) -> dict[str, set[str]]:
    """Each service's builds that appear with a status other than live."""
    found: dict[str, set[str]] = defaultdict(set)
    for page in pages:
        for service, build, status in DEPLOY.findall(page):
            if status != "live":
                found[service].add(build)
    return found


# The feed.


def test_the_feed_is_eight_pages_then_its_end(pages: list[str]) -> None:
    assert len(pages) == PAGES
    for k, page in enumerate(pages, start=1):
        assert page.startswith(f"deploy feed, page {k} of {PAGES}\n"), k
    assert all(CURSOR.search(page) for page in pages[:-1])
    assert "end of feed" in pages[-1]


def test_a_cursor_works_once(tmp_path: Path) -> None:
    work = feed_copy(tmp_path)
    first = next_page(work, {})
    found = CURSOR.search(first.stdout)
    assert found is not None
    assert next_page(work, {"cursor": found.group(1)}).returncode == 0
    again = next_page(work, {"cursor": found.group(1)})
    assert again.returncode != 0
    assert "handed out once" in again.stderr
    assert again.stdout == ""


@pytest.mark.parametrize("second", [{}, {"cursor": ""}])
def test_the_first_page_is_handed_out_once(
    tmp_path: Path, second: dict[str, Any]
) -> None:
    work = feed_copy(tmp_path)
    assert next_page(work, {}).returncode == 0
    again = next_page(work, second)
    assert again.returncode != 0
    assert "handed out once" in again.stderr


def test_an_unknown_cursor_is_refused_and_uses_up_nothing(tmp_path: Path) -> None:
    work = feed_copy(tmp_path)
    wrong = next_page(work, {"cursor": "00000000"})
    assert wrong.returncode != 0
    assert "unknown cursor" in wrong.stderr
    assert next_page(work, {}).returncode == 0


def test_a_fresh_checkout_hands_out_the_same_feed(
    pages: list[str], tmp_path: Path
) -> None:
    # The checks hold the answer, so the feed must never vary between reps.
    assert read_feed(feed_copy(tmp_path)) == pages


def test_two_services_go_live_on_each_page(pages: list[str]) -> None:
    assert [len(LIVE.findall(page)) for page in pages] == [2] * PAGES
    # status=live appears on those lines and nowhere else.
    assert [page.count("status=live") for page in pages] == [2] * PAGES


def test_sixteen_services_go_live_once_each(pages: list[str]) -> None:
    found = [pair for page in pages for pair in LIVE.findall(page)]
    assert len(found) == SERVICES
    assert len({service for service, _ in found}) == SERVICES


def test_every_service_has_builds_that_never_went_live(pages: list[str]) -> None:
    # The decoys: a model that confuses lines picks a build of the right
    # service that is the wrong answer, which the check can tell apart.
    live = live_builds(pages)
    others = other_builds(pages)
    for service, build in live.items():
        assert len(others[service] - {build}) >= 2, service


def test_a_build_belongs_to_one_service(pages: list[str]) -> None:
    owners: dict[str, set[str]] = defaultdict(set)
    for page in pages:
        for service, build, _ in DEPLOY.findall(page):
            owners[build].add(service)
    assert all(len(services) == 1 for services in owners.values())


def test_the_feed_sits_between_the_low_points_and_the_default(
    pages: list[str],
) -> None:
    # Timestamps, ids and numbers make this log text dense: 2.26 characters a
    # token by both the cl100k and the o200k tokenizers. The whole feed is then
    # 0.5 to 0.7 of the window: under the default trigger (0.8), with room for
    # the prompt and the model's notes, so the default never compacts, and over
    # 0.5, so every lower point drops pages.
    case = cases()[CASE]
    assert case.args[case.args.index("--context-window") + 1] == str(WINDOW)
    tokens = sum(len(page) for page in pages) / 2.26
    assert 0.5 * WINDOW < tokens < 0.7 * WINDOW
    # No page is cut by nare's cap on a tool result.
    assert max(len(page) for page in pages) < MAX_TOOL_OUTPUT


def elided_pages(pages: list[str], trigger: float) -> list[int]:
    """The pages nare's own compact() elides in a run at `trigger`, the target
    at the default ratio (0.75 of the trigger), as the #43 sweep sets it.

    The run is scripted: one next_page call a turn, each with a one-line note of
    the page before's live builds. After each call the provider's count is the
    request at 2.26 characters a token, plus 1,500 for the system prompt and the
    tool definitions, and compact() runs before the next call as loop.step()
    runs it.
    """
    s = new_session(cases()[CASE].prompt)
    point = CompactionPoint(trigger, 0.75 * trigger)
    note = ""
    for k, page in enumerate([*pages, None], start=1):
        compact(s, WINDOW, point=point)
        s.last_input_tokens = int(context_chars(s.messages) / 2.26) + 1500
        s.last_input_messages = len(s.messages)
        s.last_input_chars = context_chars(s.messages)
        if page is None:
            break
        content: list[dict[str, Any]] = [{"type": "text", "text": note}] if note else []
        call = {"type": "tool_use", "id": f"c{k}", "name": "next_page", "input": {}}
        s.messages.append(Message("assistant", [*content, call]))
        s.messages.append(
            Message(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": f"c{k}",
                        "content": page,
                        "is_error": False,
                    }
                ],
            )
        )
        note = f"Page {k}: live " + ", ".join(" ".join(p) for p in LIVE.findall(page))
    return [
        int(block["tool_use_id"][1:])
        for message in s.messages
        for block in message.content
        if block.get("type") == "tool_result"
        and str(block.get("content", "")).startswith(PREFIX)
    ]


def test_the_lower_the_trigger_the_more_of_the_feed_is_elided(
    pages: list[str],
) -> None:
    # What makes the case able to rank the points: the default keeps the whole
    # feed, and each lower point loses more of it, oldest first.
    elided = {t: elided_pages(pages, t) for t in (0.8, 0.5, 0.3, 0.2)}
    assert elided[0.8] == []
    assert 0 < len(elided[0.5]) <= len(elided[0.3]) <= len(elided[0.2])
    assert len(elided[0.2]) >= PAGES - 2
    for points in elided.values():
        assert points == list(range(1, len(points) + 1))


# The case.


def test_the_case_is_in_the_sweep_tier_with_only_the_feed_to_run() -> None:
    case = cases()[CASE]
    assert case.tier == "sweep"
    assert case.args == (
        "--context-window",
        str(WINDOW),
        "--command-tools",
        "tools.json",
        "--tools",
        "read,write,next_page",
    )
    tools = json.loads((case.fixture / "tools.json").read_text())
    assert set(tools) == {"next_page"}
    assert tools["next_page"]["command"] == ["python3", "feed.py"]


# The answer check.


def answer_check() -> str:
    return check_with(CASE, "live.json")


def test_the_answer_check_passes_the_live_builds(
    pages: list[str], tmp_path: Path
) -> None:
    (tmp_path / "live.json").write_text(json.dumps(live_builds(pages)))
    assert run_here(answer_check(), tmp_path) == 0


def test_the_answer_check_accepts_upper_case_and_spaces(
    pages: list[str], tmp_path: Path
) -> None:
    answer = {f" {s} ": f"{b.upper()} " for s, b in live_builds(pages).items()}
    (tmp_path / "live.json").write_text(json.dumps(answer))
    assert run_here(answer_check(), tmp_path) == 0


def test_the_answer_check_fails_a_decoy_build(pages: list[str], tmp_path: Path) -> None:
    answer = live_builds(pages)
    service = sorted(answer)[0]
    answer[service] = sorted(other_builds(pages)[service] - {answer[service]})[0]
    (tmp_path / "live.json").write_text(json.dumps(answer))
    assert run_here(answer_check(), tmp_path) != 0


def test_the_answer_check_fails_a_missing_service(
    pages: list[str], tmp_path: Path
) -> None:
    answer = live_builds(pages)
    del answer[sorted(answer)[-1]]
    (tmp_path / "live.json").write_text(json.dumps(answer))
    assert run_here(answer_check(), tmp_path) != 0


@pytest.mark.parametrize("body", [None, "[]", "not json"])
def test_the_answer_check_fails_no_answer(tmp_path: Path, body: str | None) -> None:
    if body is not None:
        (tmp_path / "live.json").write_text(body)
    assert run_here(answer_check(), tmp_path) != 0


# The transcript check: every page was fetched through the tool.


def transcript_check() -> str:
    return check_with(CASE, "next_page")


def page_call(
    index: int, args: dict[str, Any], result: str, *, is_error: bool = False
) -> list[dict[str, Any]]:
    call = f"call-{index}"
    return [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": call, "name": "next_page", "input": args}
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": call,
                    "content": result,
                    "is_error": is_error,
                }
            ],
        },
    ]


def write_answer(index: int) -> list[dict[str, Any]]:
    call = f"call-{index}"
    return [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": call,
                    "name": "write",
                    "input": {"path": "live.json", "content": "{}"},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": call,
                    "content": "wrote live.json",
                    "is_error": False,
                }
            ],
        },
    ]


def page_calls(pages: list[str]) -> list[list[dict[str, Any]]]:
    """The eight calls a run makes, each with the cursor the page before gave."""
    calls = []
    args: dict[str, Any] = {}
    for k, page in enumerate(pages):
        calls.append(page_call(k, args, page))
        found = CURSOR.search(page)
        args = {"cursor": found.group(1)} if found else {}
    return calls


def elided(turn: int) -> str:
    return (
        f"[elided by nare: 14000 chars of output from turn {turn}. "
        "Rerun the tool if you need it.]"
    )


def transcript_passes(tmp_path: Path, session: dict[str, Any]) -> bool:
    (tmp_path / "session.json").write_text(json.dumps(session))
    return run_here(transcript_check(), tmp_path, tmp_path) == 0


def messages(*turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [m for turn in turns for m in turn]


def test_the_transcript_check_passes_eight_pages_fetched(
    pages: list[str], tmp_path: Path
) -> None:
    session = {"messages": messages(*page_calls(pages), write_answer(9))}
    assert transcript_passes(tmp_path, session)


def test_the_transcript_check_passes_pages_compaction_elided(
    pages: list[str], tmp_path: Path
) -> None:
    calls = page_calls(pages)
    for turn, call in enumerate(calls[:6], start=1):
        call[1]["content"][0]["content"] = elided(turn)
    session = {"messages": messages(*calls, write_answer(9))}
    assert transcript_passes(tmp_path, session)


def test_the_transcript_check_passes_pages_a_summary_replaced(
    pages: list[str], tmp_path: Path
) -> None:
    calls = page_calls(pages)
    session = {
        "summaries": [
            {
                "replaced_turns": 5,
                "summary": "Read pages 1 to 5.",
                "replaced": messages(*calls[:5]),
            }
        ],
        "messages": messages(*calls[5:], write_answer(9)),
    }
    assert transcript_passes(tmp_path, session)


def test_the_transcript_check_fails_a_page_never_fetched(
    pages: list[str], tmp_path: Path
) -> None:
    calls = page_calls(pages)
    del calls[4]
    session = {"messages": messages(*calls, write_answer(9))}
    assert not transcript_passes(tmp_path, session)


@pytest.mark.parametrize("text", ["exit status 1: cursor already used", "page"])
def test_the_transcript_check_fails_a_page_whose_only_fetch_failed(
    pages: list[str], tmp_path: Path, text: str
) -> None:
    # A failed call proves nothing, even one whose text holds the page.
    calls = page_calls(pages)
    calls[4][1]["content"][0].update(
        content=pages[4] if text == "page" else text, is_error=True
    )
    session = {"messages": messages(*calls, write_answer(9))}
    assert not transcript_passes(tmp_path, session)


def test_the_transcript_check_fails_a_page_result_that_is_not_the_page(
    pages: list[str], tmp_path: Path
) -> None:
    # A result that is neither page 5 nor nare's stub for a long result.
    calls = page_calls(pages)
    calls[4][1]["content"][0]["content"] = "deploy feed, page 4 of 8\n"
    session = {"messages": messages(*calls, write_answer(9))}
    assert not transcript_passes(tmp_path, session)


def test_the_transcript_check_fails_an_idle_run(tmp_path: Path) -> None:
    # verify's pristine run: the session nare starts with fetches nothing.
    save(new_session(cases()[CASE].prompt), tmp_path / "session.json")
    assert run_here(transcript_check(), tmp_path, tmp_path) != 0


# The feed is left as it is.


@pytest.mark.parametrize("path", ["feed.py", "tools.json"])
def test_the_case_fails_a_rewritten_feed(tmp_path: Path, path: str) -> None:
    cmd = check_with(CASE, "-- feed.py")
    fixture_checkout(CASE, tmp_path)
    assert run_here(cmd, tmp_path) == 0
    (tmp_path / path).write_text("{}\n")
    assert run_here(cmd, tmp_path) != 0
