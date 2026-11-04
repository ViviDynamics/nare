"""The machine contract: what a caller may rely on, and how it finds out.

A caller turns nare's event stream, session file and exit code into decisions.
Before this module, a change to any of those shapes reached the caller as a
wrong decision rather than as a loud failure. The version here is the thing a
caller checks once, and `nare run --contract N` is how it refuses to guess.

The policy is stated in docs/contract.md and enforced by a test: one contract
version at a time, additive changes keep the number, anything a caller could
mis-read bumps it.
"""

from __future__ import annotations

import argparse
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any, Literal, get_args

from nare.events import REDACTION_RULES, REDACTION_VERSION, EventType

if TYPE_CHECKING:
    from nare.session import Status

CONTRACT_VERSION = 2

# The release that produced a run. Read from the installed distribution when
# there is one, so a wheel's metadata and what nare reports can never disagree;
# the fallback is for a source checkout, which has no distribution to read.
try:  # pragma: no cover - exercised by whichever branch the environment takes
    NARE_VERSION = version("nare")
except PackageNotFoundError:  # pragma: no cover
    NARE_VERSION = "0.0.0+source"

# One code per terminal status, and these three are the whole set. done and
# blocked both exit 0 because both are runs that did what was asked: blocked
# carries questions, which is an outcome, not a failure.
EXIT_CODES: dict[Status, int] = {"done": 0, "blocked": 0, "error": 1}

# A run that never started: no session existed, so no result line is emitted
# and nothing reached stdout. A caller that sees this knows its invocation was
# wrong rather than its task.
NEVER_STARTED = 2

# The signals `nare run` stops on cleanly: it saves the session and writes a
# result with stop_reason interrupted, as --timeout does with timeout.
STOP_SIGNALS = ("SIGINT", "SIGTERM")

# What ended a run, as a caller branches on it. A stop reason alone can mean
# a provider's word (end_turn) or nare's (budget); the kind says what a caller
# does about it:
#   finished  the model ended the run; status says how
#   limit     a limit ended it, so the task is unjudged; a larger limit or a
#             resume may finish it
#   answer    the model ended without an acceptable answer
#   stopped   the caller stopped the run, which says nothing about the task
#   failure   something broke, so nothing was checked
# Null is the one value no completed response produced: the provider call, or
# nare itself, raised.
StopReasonKind = Literal["finished", "limit", "answer", "stopped", "failure"]
STOP_REASONS: tuple[tuple[str | None, StopReasonKind, tuple[Status, ...]], ...] = (
    # The provider's word for a turn it completed. The loop decides done or
    # blocked from whether the turn called tools, not from this value.
    ("end_turn", "finished", ("done", "blocked")),
    ("stop_sequence", "finished", ("done", "blocked")),
    ("tool_use", "finished", ("done", "blocked")),
    ("max_tokens", "limit", ("error",)),
    ("max_turns", "limit", ("error",)),
    ("context", "limit", ("error",)),
    ("budget", "limit", ("error",)),
    ("timeout", "limit", ("error",)),
    ("tool_calls", "limit", ("error",)),
    ("refusal", "answer", ("error",)),
    ("schema_violation", "answer", ("error",)),
    ("no_answer", "answer", ("error",)),
    ("interrupted", "stopped", ("error",)),
    (None, "failure", ("error",)),
    ("mcp", "failure", ("error",)),
    ("session_write", "failure", ("error",)),
)


def flags_of(parser: argparse.ArgumentParser) -> dict[str, dict[str, Any]]:
    """Every option a parser accepts, by its long name, in parser order.

    Read from the parser itself, so a new flag is listed the moment it exists.
    Defaults are left out on purpose: several come from the environment, and
    the contract says what this build accepts, not how this machine is set.
    """
    flags: dict[str, dict[str, Any]] = {}
    for action in parser._actions:
        if not action.option_strings or isinstance(action, argparse._HelpAction):
            continue
        flags[max(action.option_strings, key=len)] = {
            "takes_value": action.nargs != 0,
            "choices": None if action.choices is None else list(action.choices),
        }
    return flags


def _run_parser() -> argparse.ArgumentParser:
    from nare.cli import build_parser

    commands = next(
        a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction)
    )
    run: argparse.ArgumentParser = commands.choices["run"]
    return run


def describe() -> dict[str, Any]:
    """The contract as data, for a caller to read before it spawns a run.

    The Status import is local: session.py takes CONTRACT_VERSION from here, so
    a module-level import back into session would be a cycle. One definition of
    the version is worth a function-level import.
    """
    from nare.loop import RETRIES_DEFAULT, RETRY_SECONDS_DEFAULT
    from nare.session import Status
    from nare.tools import (
        EVIDENCE_TOOLS,
        LIST_LIMIT,
        MATCH_WIDTH,
        MAX_IMAGE_BYTES,
        MAX_IMAGE_EDGE,
        MAX_IMAGE_PIXELS,
        MAX_PAGE,
        MAX_TOOL_OUTPUT,
        NAVIGATION_TOOLS,
        SEARCH_LIMIT,
        SKIPPED_DIRS,
        TOOLS,
    )
    from nare.transport import CREDENTIAL_ENV, EFFORTS

    return {
        "contract": CONTRACT_VERSION,
        "nare": NARE_VERSION,
        "exit_codes": dict(EXIT_CODES),
        "never_started": NEVER_STARTED,
        "statuses": list(get_args(Status)),
        "event_types": list(get_args(EventType)),
        # Additive: every value a result's stop_reason can take, the kind of
        # ending it is and the statuses it comes with.
        "stop_reasons": [
            {"stop_reason": value, "kind": kind, "statuses": list(statuses)}
            for value, kind, statuses in STOP_REASONS
        ],
        # Additive: the names `--tools` accepts, so a caller can check that a
        # tool it means to allow exists before it spawns a run.
        "tools": sorted(TOOLS),
        "navigation": {
            "tools": sorted(NAVIGATION_TOOLS),
            "max_output": MAX_TOOL_OUTPUT,
            "page_limit": {"list": LIST_LIMIT, "search": SEARCH_LIMIT, "max": MAX_PAGE},
            "cursor": "opaque",
            "match_width": MATCH_WIDTH,
            "line_numbers_from": 1,
            "follows_symlinks": False,
            "skipped_dirs": sorted(SKIPPED_DIRS),
        },
        # Additive: what each read, list and search returned, as a record
        # with no content, on a progress event and in the session file.
        "evidence": {
            "tools": sorted(EVIDENCE_TOOLS),
            "event": "progress",
            "event_detail": "evidence",
            "session_field": "evidence",
            "digest": "sha256",
            "line_numbers_from": 1,
            "byte_end": "exclusive",
        },
        # Additive: how each call ended. Every call's tool_use event and its
        # one result event carry the same top-level tool_use_id.
        "tool_results": {
            "event": "progress",
            "event_detail": "tool_result",
            "id_field": "tool_use_id",
            "id_on": ["tool_use", "tool_result", "evidence", "image"],
            "fields": ["tool_use_id", "is_error", "chars"],
            "content": {"built_in": False, "mcp": True},
        },
        # Additive: a run bounded by wall clock, or stopped by a signal, still
        # saves its session and writes a result line.
        # Additive: a cap on the tool calls one invocation runs.
        "tool_call_limit": {
            "flag": "--max-tool-calls",
            "per": "invocation",
            "stop_reason": "tool_calls",
            "budget_keys": ["tool_calls", "used_tool_calls"],
        },
        # Additive: how many times a failing --schema answer may be corrected.
        "schema_corrections": {
            "flag": "--max-corrections",
            "requires": "--schema",
            "per": "run",
            "default": 1,
            "free_when": (
                "fewer distinct errors than every earlier failing answer, "
                "with a budget above 1"
            ),
            "stop_reason": "schema_violation",
            "budget_keys": ["schema_corrections", "used_schema_corrections"],
            "result": "corrections",
        },
        "deadline": {
            "flag": "--timeout",
            "signals": list(STOP_SIGNALS),
            "stop_reasons": {"timeout": "timeout", "signal": "interrupted"},
            "exit": EXIT_CODES["error"],
        },
        "image_input": {
            "flag": "--image-input",
            "tool": "read",
            "media_types": ["image/png", "image/jpeg"],
            "max_bytes": MAX_IMAGE_BYTES,
            "max_pixels": MAX_IMAGE_PIXELS,
            "max_edge": MAX_IMAGE_EDGE,
        },
        # Additive: built-in calls are checked against the tool's schema
        # before approval, and a bad call is an is_error result.
        "tool_arguments": {
            "checked": "built_in",
            "against": "input_schema",
            "before": "approval",
            "unknown_parameters": "refused",
            "null_optional": "absent",
            "malformed_json": "is_error",
        },
        # Additive: how each tool's result is held to the output cap.
        "tool_output": {
            "max_chars": MAX_TOOL_OUTPUT,
            "bash": "head_and_tail_per_stream",
            "mcp": "head_and_tail",
            "read": "head_then_continue_notice",
        },
        # Additive: the provider credentials bash runs without, and how a
        # caller lets a task that needs one have it.
        "bash_env": {
            "withheld": sorted(CREDENTIAL_ENV),
            "opt_in_flag": "--bash-credentials",
            "policy_field": "bash_credentials",
        },
        "prompt_input": {
            "file_flag": "--prompt-file",
            "stdin_positional": "-",
            "encoding": "utf-8",
            "exclusive": True,
        },
        # Additive: each argv system option's file route, and that a resume
        # with no system option sends the prompt the session field recorded.
        "system_input": {
            "file_flags": {
                "--system": "--system-file",
                "--append-system": "--append-system-file",
            },
            "encoding": "utf-8",
            "exclusive": True,
            "session_field": "system",
            "resume": "recorded",
        },
        # Additive: a caller checks for it before asking for constrained
        # answers, and refuses to run unconstrained rather than guess.
        "structured_output": {
            "flag": "--structured-output",
            "modes": ["json_schema"],
            "requires": "--schema",
            "providers": ["anthropic", "openai"],
            "constrained_turns": ["schema_correction", "tool_free_run"],
            # Sent through the SDK's transform, which moves these into
            # descriptions; nare's validator still enforces every one.
            "anthropic_not_enforced_at_decode": [
                "minimum",
                "maximum",
                "exclusiveMinimum",
                "exclusiveMaximum",
                "minLength",
                "maxLength",
                "pattern",
                "maxItems",
                "minItems above 1",
                "uniqueItems",
                "const",
                "oneOf exclusivity",
                "additionalProperties other than false",
            ],
            "event": "progress",
            "event_detail": "structured_output",
        },
        # Additive: the levels --effort takes and what each rail sends.
        "effort": {
            "flag": "--effort",
            "levels": list(EFFORTS),
            "providers": {
                "anthropic": {
                    "request": {"output_config": {"effort": "LEVEL"}},
                    "thinking": {"type": "adaptive"},
                },
                "openai": {
                    "request": {"reasoning_effort": "LEVEL"},
                    "meaning": "server",
                },
            },
            "thinking_budget": {
                "flag": "--thinking-budget",
                "providers": ["anthropic"],
                "request": {"thinking": {"type": "enabled", "budget_tokens": "N"}},
            },
            "session_fields": ["effort", "thinking_budget"],
        },
        # Additive: in-run resends of a transient model request failure.
        "request_retries": {
            "flags": {
                "retries": "--request-retries",
                "seconds": "--request-retry-seconds",
            },
            "env": {
                "retries": "NARE_REQUEST_RETRIES",
                "seconds": "NARE_REQUEST_RETRY_SECONDS",
            },
            "default": {"retries": RETRIES_DEFAULT, "seconds": RETRY_SECONDS_DEFAULT},
            "per": "model_request",
            "providers": ["anthropic", "openai"],
            "event": "progress",
            "event_detail": "retry",
            "result_field": "retries",
            "session_field": "retries",
        },
        "request_timeout": {
            "flag": "--request-timeout",
            "env": "NARE_REQUEST_TIMEOUT",
            "default": 600.0,
            "providers": ["anthropic", "openai"],
        },
        "proxy_num_retries": {
            "flag": "--proxy-num-retries",
            "providers": ["openai"],
            "request_field": "num_retries",
            "default": None,
        },
        # Additive: opt-in prompt-cache markers on the openai rail, for a
        # proxy that forwards them. The anthropic rail always sends them.
        "cache_breakpoints": {
            "flag": "--cache-breakpoints",
            "env": "NARE_CACHE_BREAKPOINTS",
            "providers": ["openai"],
            "default": False,
            "marker": {"type": "ephemeral"},
        },
        # Additive: every `nare run` option, so a caller checks that a flag
        # exists instead of comparing release numbers.
        "flags": flags_of(_run_parser()),
        # The redaction is part of what a caller relies on: event `text` and
        # whatever a caller pipes through `nare redact` carry it, and a rule
        # change is a change to what those mean.
        "redaction": {
            "version": REDACTION_VERSION,
            "rules": list(REDACTION_RULES),
        },
    }
