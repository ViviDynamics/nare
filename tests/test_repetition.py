"""A reply that degenerates into one short span repeated is recognised, while
it streams and once it has finished, and a valid reply is not.
"""

from __future__ import annotations

import json

import pytest

from nare.repetition import REPEAT_SPAN, RepetitionWatch, degenerate, looping


@pytest.mark.parametrize("loop", ["flux? no, just finish: ", " | 1", "\n", "ab"])
def test_an_exactly_repeating_tail_is_degenerate(loop: str) -> None:
    text = '{"assessment":{"findings":[' + loop * (REPEAT_SPAN // len(loop) + 2)
    assert degenerate(text)


def test_a_loop_that_only_counts_upward_is_degenerate() -> None:
    text = '{"ids":[' + "".join(f'"end-{index}" \t,  ' for index in range(2000))
    assert degenerate(text)


def test_a_repeat_followed_by_a_closing_line_is_not_degenerate() -> None:
    # The rule reads the tail. A reply that stopped looping and finished is
    # not a cut-off, so the closing text clears it.
    text = "ab" * 3000 + " and that is the answer."
    assert not degenerate(text)


def test_short_text_is_never_degenerate() -> None:
    assert not degenerate("x" * REPEAT_SPAN)


def test_eight_identical_short_array_items_are_not_degenerate() -> None:
    text = json.dumps({"items": ["a"] * 8, "note": "eight identical items"})
    assert len(text) < REPEAT_SPAN
    assert not degenerate(text)


def test_a_valid_reply_with_repeated_records_is_not_degenerate() -> None:
    words = ["bundle", "rspec", "rubocop", "helm", "compose", "psql", "node"]
    checks = [
        {
            "id": f"check-{index:03d}-{words[index % 7]}-{words[index * 3 % 7]}",
            "argv": ["bash", "-c", f"{words[index % 7]} --version"],
        }
        for index in range(200)
    ]
    text = json.dumps({"manifest": {"checks": checks}})
    assert len(text) > 6 * REPEAT_SPAN
    assert not any(degenerate(text[:end]) for end in range(REPEAT_SPAN, len(text), 97))


def test_a_long_list_of_varied_items_is_not_degenerate() -> None:
    words = [format(index * 2654435761 % 2**32, "x") for index in range(1, 500)]
    text = " ".join(words[(index * index) % 499] for index in range(3000))
    assert len(text) > 2 * REPEAT_SPAN
    assert not degenerate(text)


def test_a_long_list_of_same_shape_records_reads_as_a_counting_loop() -> None:
    # The accepted trade-off of folding digits: a few thousand characters of
    # records that differ only in their numbers look like a loop that counts.
    # Short lists, and lists whose items differ in words, are unaffected.
    records = "".join(f'{{"id": {index}, "ok": true}}, ' for index in range(400))
    assert degenerate(records)
    assert not degenerate(
        "".join(f'{{"id": {index}, "ok": true}}, ' for index in range(8))
    )


def test_a_watch_trips_on_a_streamed_loop_before_it_finishes() -> None:
    watch = RepetitionWatch()
    fed = 0
    tripped_at: int | None = None
    prefix = '{"assessment":{"findings":['
    for fragment in [prefix] + [" | 1"] * 5000:
        fed += len(fragment)
        if watch.feed(fragment):
            tripped_at = fed
            break
    assert tripped_at is not None
    assert tripped_at < 4 * REPEAT_SPAN


def test_a_watch_stays_quiet_on_varied_prose() -> None:
    watch = RepetitionWatch()
    words = [format(index * 2654435761 % 2**32, "x") for index in range(1, 500)]
    prose = " ".join(words[(index * index) % 499] for index in range(3000))
    assert not any(watch.feed(prose[i : i + 7]) for i in range(0, len(prose), 7))


def test_looping_checks_text_and_reasoning_blocks() -> None:
    looped = "no, just finish. " * 400
    assert looping([{"type": "thinking", "thinking": looped}])
    assert looping([{"type": "text", "text": looped}])
    assert not looping([{"type": "text", "text": "a short answer"}])
    assert not looping([{"type": "tool_use", "id": "x", "name": "bash", "input": {}}])
