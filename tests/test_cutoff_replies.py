"""A reply the model cut off, at its output cap or by repeating itself, is
corrected within a budget of cut-offs, and ends the run once that is spent.
"""

from __future__ import annotations

from nare.contract import describe
from nare.repetition import REPEAT_SPAN


def test_the_contract_lists_repetition_as_a_limit_that_ends_in_error() -> None:
    rows = {row["stop_reason"]: row for row in describe()["stop_reasons"]}

    assert rows["repetition"] == {
        "stop_reason": "repetition",
        "kind": "limit",
        "statuses": ["error"],
    }
    assert rows["max_tokens"]["kind"] == "limit"


def test_the_contract_describes_the_cut_off_budget() -> None:
    cut_offs = describe()["cut_off_replies"]

    assert cut_offs == {
        "flag": "--max-cutoffs",
        "default": 2,
        "per": "invocation",
        "stop_reasons": ["max_tokens", "repetition"],
        "event": "progress",
        "event_detail": "cutoff",
        "result_field": "cutoffs",
        "session_field": "cutoffs",
        "budget_keys": ["cutoffs", "used_cutoffs"],
    }


def test_the_repetition_span_is_at_least_eight_copies_of_the_longest_period() -> None:
    # Documented in docs/contract.md: the rule needs at least eight copies of
    # the longest period it reads.
    from nare.repetition import MAX_PERIOD

    assert REPEAT_SPAN >= 8 * MAX_PERIOD
