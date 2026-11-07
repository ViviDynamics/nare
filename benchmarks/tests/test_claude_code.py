"""The Claude Code arm's pure parts: its stream, its mapping onto nare's result
line, its price arithmetic and the case selection. No container, no model."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.runner.case import load_cases
from benchmarks.runner.claude_code import (
    COMPARABLE_CASES,
    EXCLUDED_CASES,
    ModelPrice,
    agent_script,
    dollars,
    parse_stream,
    price_from_model_info,
    to_result_line,
)

BENCH = Path(__file__).resolve().parent.parent
HAIKU_INFO = {
    "input_cost_per_token": 1e-07,
    "output_cost_per_token": 5e-07,
    "cache_read_input_token_cost": 1e-08,
    "cache_creation_input_token_cost": 1.25e-07,
}


def _lines(*objs: dict) -> str:
    return "\n".join(json.dumps(o) for o in objs) + "\n"


def _result(**overrides: object) -> dict:
    base = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "api_error_status": None,
        "result": "All done.",
        "num_turns": 3,
        "total_cost_usd": 0.01,
        "usage": {
            "input_tokens": 5,
            "output_tokens": 40,
            "cache_read_input_tokens": 900,
            "cache_creation_input_tokens": 1200,
        },
    }
    base.update(overrides)
    return base


def _tool_use(block_id: str, name: str) -> dict:
    return {
        "type": "assistant",
        "message": {
            "id": "m" + block_id,
            "content": [
                {"type": "tool_use", "id": block_id, "name": name, "input": {}}
            ],
        },
    }


def test_stream_counts_tools_and_reads_result_fields():
    text = _lines(
        {"type": "system", "subtype": "init", "tools": ["Bash", "Read"]},
        _tool_use("t1", "Bash"),
        _tool_use("t2", "Bash"),
        _tool_use("t3", "Read"),
        _result(),
    )
    run = parse_stream(text)
    assert run.tool_calls == {"Bash": 2, "Read": 1}
    assert run.closing == "All done."
    assert run.turns == 3
    assert run.reported_cost == pytest.approx(0.01)


def test_a_tool_block_repeated_in_a_streamed_message_is_counted_once():
    text = _lines(_tool_use("t1", "Bash"), _tool_use("t1", "Bash"), _result())
    assert parse_stream(text).tool_calls == {"Bash": 1}


def test_no_result_object_is_no_line():
    text = _lines({"type": "system", "subtype": "init"})
    assert to_result_line(parse_stream(text), HAIKU_PRICE) is None


def test_non_json_lines_are_ignored():
    text = "warning: something\n" + _lines(_result())
    assert to_result_line(parse_stream(text), HAIKU_PRICE).status == "done"


def test_success_maps_to_done_with_end_turn():
    line = to_result_line(parse_stream(_lines(_result())), HAIKU_PRICE)
    assert line.status == "done"
    assert line.stop_reason == "end_turn"
    assert line.error is None


def test_turn_limit_maps_to_nare_limit_which_is_an_error_status():
    result = _result(subtype="error_max_turns", num_turns=12)
    line = to_result_line(parse_stream(_lines(result)), HAIKU_PRICE)
    assert line.status == "error"
    assert line.stop_reason == "max_turns"
    assert line.turns == 12


def test_api_error_is_an_infrastructure_error_with_no_stop_reason():
    result = _result(
        is_error=True, subtype="success", api_error_status=529, result="overloaded"
    )
    line = to_result_line(parse_stream(_lines(result)), HAIKU_PRICE)
    assert line.status == "error"
    assert line.stop_reason is None
    assert line.error == "overloaded"


def test_usage_maps_onto_the_four_nare_fields():
    line = to_result_line(parse_stream(_lines(_result())), HAIKU_PRICE)
    assert line.usage == {
        "input": 5,
        "output": 40,
        "cache_read": 900,
        "cache_write": 1200,
    }


def test_price_comes_from_the_gateway_model_info_and_costs_by_token_class():
    price = price_from_model_info(HAIKU_INFO)
    assert price == ModelPrice(
        input=1e-07, output=5e-07, cache_read=1e-08, cache_write=1.25e-07
    )
    usage = {"input": 2, "output": 4, "cache_read": 0, "cache_write": 17252}
    assert dollars(usage, price) == pytest.approx(
        2 * 1e-07 + 4 * 5e-07 + 17252 * 1.25e-07
    )


def test_a_model_without_a_cache_price_is_refused_not_costed_as_zero():
    info = dict(HAIKU_INFO)
    del info["cache_read_input_token_cost"]
    with pytest.raises(ValueError, match="cache_read"):
        price_from_model_info(info)


def test_the_cost_of_a_mapped_line_is_estimated_from_prices():
    line = to_result_line(parse_stream(_lines(_result())), HAIKU_PRICE)
    assert line.cost_estimated is True
    assert line.cost == pytest.approx(dollars(line.usage, HAIKU_PRICE))


def test_agent_script_passes_the_prompt_by_variable_not_text():
    script = agent_script()
    assert '-p "$BENCH_PROMPT"' in script
    assert "--permission-mode bypassPermissions" in script
    assert "--disallowedTools=" in script
    assert "--output-format stream-json" in script


def test_every_case_is_compared_or_excluded_with_a_reason():
    cases = {c.id for c in load_cases(BENCH / "cases")}
    assert set(COMPARABLE_CASES) | set(EXCLUDED_CASES) == cases
    assert not set(COMPARABLE_CASES) & set(EXCLUDED_CASES)
    assert all(reason.strip() for reason in EXCLUDED_CASES.values())


def test_comparable_cases_use_only_what_both_arms_have():
    # The exclusions are the decision; a case moved into the set has to be
    # checked against them. None of these reads nare's session file or ask tool.
    for case in load_cases(BENCH / "cases"):
        if case.id not in COMPARABLE_CASES:
            continue
        assert not case.args, f"{case.id} passes nare-only flags"
        assert not any("/out/session.json" in (c.cmd or "") for c in case.bash_checks)
        assert all(c.status != "blocked" for c in case.checks if c.kind == "result")


HAIKU_PRICE = price_from_model_info(HAIKU_INFO)


def test_a_truncated_final_turn_is_an_error_even_on_success_subtype():
    result = _result(stop_reason="max_tokens")
    line = to_result_line(parse_stream(_lines(result)), HAIKU_PRICE)
    assert line.status == "error"
    assert line.stop_reason == "max_tokens"
