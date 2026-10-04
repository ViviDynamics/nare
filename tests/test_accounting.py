from __future__ import annotations

import json

import pytest

from nare.accounting import Prices, turn_usage
from nare.session import Usage, dumps, loads, new_session
from nare.transport import Reply, reported_cost


def reply(cost: float | None = None) -> Reply:
    return Reply([], [], Usage(10, 5, 7, 3), "end_turn", cost=cost)


def test_reported_cost_wins_over_configured_prices() -> None:
    assert turn_usage(reply(0.25), Prices(1, 2)).cost == 0.25


def test_price_all_categories_and_cache_fallback() -> None:
    assert turn_usage(reply(), Prices(1, 2)).cost == 0.000030
    assert turn_usage(reply(), Prices(1, 2, 0.5, 2)).cost == 0.0000295


def test_unknown_cost_poison_total() -> None:
    assert (turn_usage(reply(), None) + turn_usage(reply(1), None)).cost is None
    assert (turn_usage(reply(1), None) + turn_usage(reply(), None)).cost is None
    assert (Usage(cost=0.25) + Usage(cost=0.5)).cost == 0.75


@pytest.mark.parametrize(
    "headers,want",
    [
        ({}, None),
        ({"x-litellm-response-cost": "0.2"}, 0.2),
        ({"x-litellm-response-cost-original": "0"}, 0.0),
        (
            {
                "x-litellm-response-cost": "0.2",
                "x-litellm-response-cost-original": "0.3",
            },
            0.2,
        ),
        ({"x-litellm-response-cost": "nan"}, None),
        ({"x-litellm-response-cost": "inf"}, None),
        ({"x-litellm-response-cost": "-1"}, None),
        ({"x-litellm-response-cost": "bad"}, None),
    ],
)
def test_cost_header_validation(headers: dict[str, str], want: float | None) -> None:
    assert reported_cost(headers) == want


def test_legacy_nonempty_usage_has_unknown_cost() -> None:
    raw = json.loads(dumps(new_session("go")))
    raw["usage"] = {"input": 10, "output": 5}
    assert loads(json.dumps(raw)).usage.cost is None
    raw["usage"] = {"input": 0, "output": 0}
    assert loads(json.dumps(raw)).usage.cost == 0
