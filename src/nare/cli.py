"""`nare run` — the headless CLI.

A thin adapter over the library. A caller spawns this as it would any other
agent CLI, which keeps nare's own surface on the critical path so it cannot
rot.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import logging
import math
import os
import signal
import sys
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

from nare import __version__
from nare.accounting import Prices, finite_number, prices_from_env
from nare.contract import (
    CONTRACT_VERSION,
    EXIT_CODES,
    NEVER_STARTED,
    STOP_SIGNALS,
    describe,
)
from nare.corrections import SCHEMA_CORRECTIONS_DEFAULT
from nare.events import Event, redact
from nare.loop import (
    CUTOFFS_DEFAULT,
    MAX_TURNS_DEFAULT,
    RETRIES_DEFAULT,
    RETRY_SECONDS_DEFAULT,
    STRUCTURED_OUTPUT_MODES,
    RetryPolicy,
    budget_record,
    check_budget,
    check_structured_output,
    configure_budgets,
    final_text,
    run,
)
from nare.mcp import parse_servers
from nare.schema import UnsupportedSchema, check_supported
from nare.session import Session, loads, new_session, reopen, save, unanswered
from nare.tools import TOOLS, Policy, approve_all, stop_group
from nare.transport import EFFORTS, Transport, make_transport

log = logging.getLogger(__name__)

# Sent unless --system replaces it. Without it every run got the model's own
# defaults: the practice run installed Postgres from guessed URLs, claimed an
# end-to-end test it never ran, left a server running unreported and committed
# under an invented git identity, and the visual run wrote summary files
# nobody asked for.
DEFAULT_SYSTEM = """\
You are a coding agent working in the user's project. Follow these rules.

1. Stay in scope. Offer extra setup, such as installing services or \
downloading tools, in your final message instead of doing it.
2. Do not install system software, download and run executables, or start \
long-lived services unless asked.
3. Claim only what tool output showed, and say what you did not test.
4. Stop what you start. List anything left running or changed outside the \
project root, with how to undo it.
5. Do not commit or push unless asked, and never set a git identity.
6. Tool calls in one turn run in order, so batch an edit with the command \
that checks it.
7. When the task is done, stop: no summary files, banners or extra \
documentation unless asked.\
"""


def stream_from_env(env: Mapping[str, str]) -> bool:
    """NARE_STREAM, as --stream's default reads it. The benchmark's judge
    reads it here too, so it streams exactly when the agent does.
    """
    return env.get("NARE_STREAM", "").strip().lower() in ("1", "true", "yes")


def cache_breakpoints_from_env(env: Mapping[str, str]) -> bool:
    """NARE_CACHE_BREAKPOINTS, as --cache-breakpoints' default reads it."""
    return env.get("NARE_CACHE_BREAKPOINTS", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nare", description="A standalone agent harness."
    )
    parser.add_argument(
        "--version",
        action="version",
        version=__version__,
        help="print the installed nare version and exit",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "contract",
        help="print the machine contract this nare speaks, as JSON, and exit",
    )
    sub.add_parser(
        "redact",
        help=(
            "apply the event redaction rules to text on stdin, writing the "
            "redacted text to stdout, and exit"
        ),
    )
    run_parser = sub.add_parser("run", help="run a task headlessly")

    run_parser.add_argument(
        "prompt", nargs="?", help="the task, as plain text; - reads UTF-8 stdin"
    )
    run_parser.add_argument(
        "--prompt-file", metavar="PATH", help="read the UTF-8 task file"
    )
    run_parser.add_argument(
        "--stream",
        action="store_true",
        default=stream_from_env(os.environ),
        help="stream text/reasoning while each model turn runs (env: NARE_STREAM)",
    )
    run_parser.add_argument(
        "--jsonl", action="store_true", help="emit typed JSONL on stdout"
    )
    run_parser.add_argument(
        "--yes",
        action="store_true",
        help="approve every tool call (required for unattended runs)",
    )
    run_parser.add_argument(
        "--contract",
        type=int,
        help=(
            "the contract version the caller speaks. nare refuses to start when "
            "it speaks a different one, rather than emitting a stream the caller "
            f"would mis-read (this nare: {CONTRACT_VERSION})"
        ),
    )
    run_parser.add_argument(
        "--schema",
        help=(
            "a JSON Schema file the final answer must satisfy. nare validates a "
            "documented subset (types, properties and maps, enum and const, string, "
            "array and number bounds, anyOf/oneOf/allOf, local $ref) and refuses a "
            "schema using anything else rather than validating it only in part"
        ),
    )
    run_parser.add_argument(
        "--structured-output",
        choices=STRUCTURED_OUTPUT_MODES,
        help=(
            "ask the backend to constrain answers to the --schema at decode time "
            "(both providers). A constrained turn offers no tools, so only "
            "the schema correction turn, or every turn of a run with no tools, "
            "is constrained. nare still validates every answer"
        ),
    )
    run_parser.add_argument(
        "--max-tool-calls",
        type=int,
        metavar="N",
        help=(
            "run at most N tool calls in this invocation, built-in and MCP. "
            "Past the cap a call is refused and the model told to answer; one "
            "that calls again ends the run with stop_reason tool_calls"
        ),
    )
    run_parser.add_argument(
        "--max-corrections",
        type=int,
        metavar="N",
        help=(
            "let the --schema answer be corrected up to N times in this run "
            "(default 1). A correction is free, and does not count, when the "
            "answer has fewer distinct errors than any earlier one; free corrections "
            "need N above 1. Each correction names the earlier fixes to keep"
        ),
    )
    run_parser.add_argument(
        "--max-cutoffs",
        type=int,
        default=CUTOFFS_DEFAULT,
        metavar="N",
        help=(
            "correct up to N replies cut off at the output cap or by repeating "
            "one span, in this invocation. A correction tells the model to "
            "reason briefly and answer completely, and the reply's tool calls "
            "do not run. Past N the run ends error with stop_reason max_tokens "
            "or repetition. 0 ends at the first cut "
            f"(default {CUTOFFS_DEFAULT})"
        ),
    )
    run_parser.add_argument(
        "--timeout",
        type=float,
        metavar="SECONDS",
        help=(
            "wall clock for this invocation. At the deadline nare stops the "
            "model or tool call in flight, kills the bash commands this run "
            "started, saves the session and writes a result with stop_reason "
            "timeout. SIGTERM and SIGINT do the same with stop_reason interrupted"
        ),
    )
    add_run_flags(run_parser)

    tui_parser = sub.add_parser(
        "tui", help="work with nare in a terminal UI, or watch a run with --attach"
    )
    tui_parser.add_argument(
        "prompt", nargs="?", help="the task to start with; without one, type it"
    )
    tui_parser.add_argument(
        "--attach",
        metavar="PATH",
        help="watch the session file at PATH, read-only; other flags are ignored",
    )
    add_run_flags(tui_parser)
    return parser


def add_run_flags(parser: argparse.ArgumentParser) -> None:
    """The flags nare run and nare tui share, so the two cannot drift."""
    parser.add_argument(
        "--request-timeout",
        type=float,
        metavar="SECONDS",
        default=os.environ.get("NARE_REQUEST_TIMEOUT"),
        help="HTTP timeout for each model request, on both providers: the "
        "longest wait to connect or for the next bytes (default 600; "
        "env: NARE_REQUEST_TIMEOUT)",
    )
    parser.add_argument(
        "--request-retries",
        type=int,
        metavar="N",
        default=os.environ.get("NARE_REQUEST_RETRIES", RETRIES_DEFAULT),
        help="resend a model request up to N times after a transient failure "
        "(timeout, dropped connection, HTTP 429/502/503/504/529); 0 never "
        f"resends (default {RETRIES_DEFAULT}; env: NARE_REQUEST_RETRIES)",
    )
    parser.add_argument(
        "--request-retry-seconds",
        type=float,
        metavar="SECONDS",
        default=os.environ.get("NARE_REQUEST_RETRY_SECONDS", RETRY_SECONDS_DEFAULT),
        help="most seconds one model request waits between its retries "
        f"(default {RETRY_SECONDS_DEFAULT:g}; env: NARE_REQUEST_RETRY_SECONDS)",
    )
    parser.add_argument(
        "--proxy-num-retries",
        type=int,
        help="send num_retries to an OpenAI-compatible proxy (unset: omit); "
        "0 disables LiteLLM router retries, not fallbacks",
    )
    parser.add_argument(
        "--cache-breakpoints",
        action="store_true",
        default=cache_breakpoints_from_env(os.environ),
        help="mark prompt-cache breakpoints (cache_control) on the openai "
        "provider's requests, for a proxy that forwards them to a caching "
        "backend; off by default because some servers reject unknown fields. "
        "The anthropic provider always marks them (env: NARE_CACHE_BREAKPOINTS)",
    )
    parser.add_argument(
        "--provider",
        choices=["anthropic", "openai", "fake"],
        default=os.environ.get("NARE_PROVIDER", "anthropic"),
        help="model provider: anthropic, or openai for anything speaking Chat "
        "Completions, including a proxy or a local server via --base-url, or "
        "fake to replay a --replay script with no key or network "
        "(env: NARE_PROVIDER)",
    )
    parser.add_argument(
        "--replay",
        metavar="FILE",
        help="the JSON script of replies --provider fake replays, one per turn",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("NARE_MODEL", "claude-sonnet-5"),
        help="model name (env: NARE_MODEL)",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("NARE_BASE_URL"),
        help="override the vendor endpoint (env: NARE_BASE_URL)",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        help="sampling temperature; not accepted by the anthropic provider",
    )
    parser.add_argument(
        "--max-tokens", type=int, help="output token cap (default 8192)"
    )
    parser.add_argument(
        "--context-window",
        type=int,
        default=os.environ.get("NARE_CONTEXT_WINDOW"),
        help="context window for elision (env: NARE_CONTEXT_WINDOW; "
        "else backend or 32000)",
    )
    parser.add_argument(
        "--budget-tokens",
        type=int,
        default=os.environ.get("NARE_BUDGET_TOKENS"),
        help="session token budget, including input, output and cache "
        "(env: NARE_BUDGET_TOKENS)",
    )
    parser.add_argument(
        "--budget-usd",
        type=float,
        default=os.environ.get("NARE_BUDGET_USD"),
        help="cumulative session dollar budget (env: NARE_BUDGET_USD)",
    )
    parser.add_argument(
        "--effort",
        choices=EFFORTS,
        help="reasoning effort. anthropic: output_config.effort with adaptive "
        "thinking, and a model that does not take a level refuses it. openai: "
        "sent as reasoning_effort, and what a level means is the server's",
    )
    parser.add_argument(
        "--thinking-budget",
        type=int,
        metavar="TOKENS",
        help="anthropic only: a fixed thinking budget (thinking.budget_tokens), "
        "for the older models that still need one; current models refuse it, "
        "so use --effort there",
    )
    # Each file route is exclusive with its argv form, as --prompt-file is
    # with a positional prompt: a caller that sent both meant one of them.
    system = parser.add_mutually_exclusive_group()
    system.add_argument(
        "--system",
        metavar="TEXT",
        help="replace nare's default system prompt with TEXT; --system '' sends none",
    )
    system.add_argument(
        "--system-file",
        metavar="PATH",
        help="--system, read from a UTF-8 file, which keeps it out of argv",
    )
    append = parser.add_mutually_exclusive_group()
    append.add_argument(
        "--append-system",
        metavar="TEXT",
        help="add TEXT after the system prompt: the default, or --system's",
    )
    append.add_argument(
        "--append-system-file",
        metavar="PATH",
        help="--append-system, read from a UTF-8 file",
    )
    parser.add_argument(
        "--tools",
        help=(
            "comma-separated tools this run may call "
            f"(default all: {','.join(sorted(TOOLS))}; 'none' allows no tool)"
        ),
    )
    parser.add_argument(
        "--root",
        help=(
            "confine read, write and edit to this directory, and run bash in "
            "it. bash is given it as a working directory, not a jail: a shell "
            "can still walk upward, and confining it is the sandbox's job"
        ),
    )
    parser.add_argument(
        "--bash-credentials",
        action="store_true",
        help=(
            "let bash commands see the provider credentials nare reads "
            "(ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, OPENAI_API_KEY); "
            "withheld by default, and recorded in the session's policy"
        ),
    )
    parser.add_argument(
        "--resume",
        help="continue the session at this path, writing it back unless "
        "--session says otherwise",
    )
    parser.add_argument(
        "--mcp-config", help="JSON file of named MCP stdio or Streamable HTTP servers"
    )
    parser.add_argument(
        "--image-input",
        action="store_true",
        help="declare that the selected model accepts PNG/JPEG image input",
    )
    parser.add_argument("--session", help="write the session to this path")
    parser.add_argument(
        "--max-turns",
        type=int,
        default=MAX_TURNS_DEFAULT,
        help=f"stop after this many turns (default {MAX_TURNS_DEFAULT})",
    )


def system_from_args(args: argparse.Namespace) -> str | None:
    """The system prompt a run sends: nare's default unless --system replaces
    it, then --append-system after it. None sends no system prompt at all.

    A blank part is dropped, not sent: `--system ''` is how a caller opts out
    of the default, and a blank append would only add a separator. A run
    with no tools gets no default: every rule in it is about tools, and a
    caller after one answer would get them back as prose in it.
    """
    if args.system is not None:
        base: str | None = args.system
    else:
        base = DEFAULT_SYSTEM if tools_from_args(args) else None
    parts = [part for part in (base, args.append_system) if part and part.strip()]
    return "\n\n".join(parts) or None


def read_system_files(args: argparse.Namespace) -> None:
    """Read --system-file and --append-system-file into their argv forms,
    as --prompt-file is read: UTF-8, every byte kept, nothing trimmed.
    """
    for flag, path_name, text_name in (
        ("--system-file", "system_file", "system"),
        ("--append-system-file", "append_system_file", "append_system"),
    ):
        path = getattr(args, path_name, None)
        if path is None:
            continue
        try:
            setattr(args, text_name, Path(path).read_bytes().decode("utf-8"))
        except (OSError, UnicodeError) as exc:
            raise ValueError(f"{flag} {path}: {exc}") from exc


def adopt_system(args: argparse.Namespace, session: Session) -> None:
    """Settle the system prompt this run sends, and record it on the session.

    A resume given no system option sends what the session recorded: falling
    back to the default would swap the model's instructions mid-session with
    no error, and change the prefix a caching backend matches on. A resume
    given one sends it, and says so when it differs from the record. A
    session saved before the record existed has none and keeps the flags.

    The file holds the redacted copy, so the comparison is made redacted: the
    same prompt resent is never reported as a change.
    """
    given = args.system is not None or args.append_system is not None
    if session.system is not None and not given:
        args.system, args.append_system = session.system, None
    sent = system_from_args(args) or ""
    if session.system is not None and given and redact(sent) != session.system:
        session.events.append(
            Event(
                "progress",
                "system prompt changed: this run sends a different system "
                "prompt from the one the session recorded",
                {"system_prompt": {"changed": True}},
            )
        )
    session.system = sent


def transport_from_args(args: argparse.Namespace) -> Transport:
    """Everything vendor-shaped is bound here and never reaches the loop."""
    return make_transport(
        args.provider,
        model=args.model,
        base_url=args.base_url,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        effort=args.effort,
        thinking_budget=args.thinking_budget,
        system=system_from_args(args),
        streaming=args.stream,
        image_input=args.image_input,
        proxy_num_retries=args.proxy_num_retries,
        cache_breakpoints=args.cache_breakpoints,
        replay=args.replay,
        request_timeout=args.request_timeout,
    )


def schema_from_args(args: argparse.Namespace) -> dict[str, Any] | None:
    """Read and vet the schema before the run starts: a caller that asked for
    data should not spend a model call to learn the schema was unreadable.
    """
    if args.schema is None:
        return None
    loaded = json.loads(Path(args.schema).read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"--schema {args.schema} is not a JSON object")
    check_supported(loaded)
    return loaded


def tools_from_args(args: argparse.Namespace) -> frozenset[str]:
    """The tools --tools names: all of them by default, none for 'none'."""
    if args.tools is None:
        return frozenset(TOOLS)
    named = [name.strip() for name in args.tools.split(",") if name.strip()]
    return frozenset() if named == ["none"] else frozenset(named)


def policy_from_args(args: argparse.Namespace) -> Policy:
    """Both failures here are startup failures: a caller that asked for a
    narrower session and did not get one must not be handed a wider one.
    """
    tools = tools_from_args(args)
    root = None
    if args.root is not None:
        root = Path(args.root)
        if not root.is_dir():
            raise ValueError(f"--root {args.root} is not a directory")
    unresolved_names = frozenset(
        name
        for name in tools
        if any(name.startswith(alias + "__") for alias in args.mcp_servers)
    )
    return Policy(
        tools=tools,
        root=root,
        pending=unresolved_names,
        bash_credentials=args.bash_credentials,
    )


def validate_run_args(args: argparse.Namespace) -> Prices | None:
    """The startup checks nare run and nare tui share. Raises ValueError or
    OSError, which each caller turns into exit 2 before anything starts.
    """
    if args.thinking_budget is not None:
        if args.thinking_budget <= 0:
            raise ValueError("--thinking-budget must be a positive integer")
        if args.provider != "anthropic":
            raise ValueError("--thinking-budget requires the anthropic provider")
    if args.proxy_num_retries is not None:
        if args.proxy_num_retries < 0:
            raise ValueError("--proxy-num-retries must be a nonnegative integer")
        if args.provider != "openai":
            raise ValueError("--proxy-num-retries requires the openai provider")
    if args.provider == "fake" and args.replay is None:
        raise ValueError("--provider fake requires --replay FILE")
    if args.replay is not None and args.provider != "fake":
        raise ValueError("--replay requires --provider fake")
    # A resume with nowhere to write back silently throws the run away and
    # replays the stale prefix next time. Resuming a file means updating it.
    if args.resume and not args.session:
        args.session = args.resume
    read_system_files(args)
    if args.budget_tokens is not None and args.budget_tokens <= 0:
        raise ValueError("--budget-tokens must be a positive integer")
    if args.context_window is not None and args.context_window <= 0:
        raise ValueError("--context-window must be a positive integer")
    if args.budget_usd is not None:
        finite_number(args.budget_usd, "--budget-usd")
    if args.request_timeout is not None and not (
        math.isfinite(args.request_timeout) and args.request_timeout > 0
    ):
        raise ValueError("--request-timeout must be a positive finite number")
    if args.request_retries < 0:
        raise ValueError("--request-retries must be a nonnegative integer")
    if not (
        math.isfinite(args.request_retry_seconds) and args.request_retry_seconds >= 0
    ):
        raise ValueError("--request-retry-seconds must be a nonnegative finite number")
    args.retry = RetryPolicy(
        retries=args.request_retries, seconds=args.request_retry_seconds
    )
    args.mcp_servers = (
        parse_servers(json.loads(Path(args.mcp_config).read_text(encoding="utf-8")))
        if args.mcp_config
        else {}
    )
    return prices_from_env()


def _load_or_new(args: argparse.Namespace) -> Session:
    if not args.resume:
        return new_session(args.prompt)
    session = loads(Path(args.resume).read_text(encoding="utf-8"))
    # Resuming is how a caller relays feedback: reopen and keep going.
    reopen(session, args.prompt)
    return session


def _emit(event: Event, jsonl: bool) -> None:
    if jsonl:
        print(json.dumps(event.wire()), flush=True)
    else:
        print(f"[{event.type}] {event.text}", flush=True)


def _emit_result(
    session: Session,
    jsonl: bool,
    *,
    max_tool_calls: int | None = None,
    tool_calls_from: int | None = None,
    max_corrections: int | None = None,
    max_cutoffs: int | None = None,
    cutoffs_from: int | None = None,
) -> None:
    if jsonl:
        print(
            json.dumps(
                {
                    "type": "result",
                    "session_id": session.id,
                    "status": session.status,
                    "questions": session.questions,
                    "usage": session.usage.wire(),
                    "budget": budget_record(
                        session,
                        max_tool_calls=max_tool_calls,
                        tool_calls_from=tool_calls_from,
                        max_corrections=max_corrections,
                        max_cutoffs=max_cutoffs,
                        cutoffs_from=cutoffs_from,
                    ),
                    # Every correction the run made, free or charged, with the
                    # distinct errors it answered. Present only when there was one.
                    **(
                        {"corrections": session.schema_correction_log}
                        if session.schema_correction_log
                        else {}
                    ),
                    "stop_reason": session.stop_reason,
                    "turns": session.turns,
                    "retries": session.retries,
                    "cutoffs": session.cutoffs,
                    "contract": CONTRACT_VERSION,
                    "nare": __version__,
                    "output": session.output,
                    "text": final_text(session),
                    "error": session.error,
                }
            ),
            flush=True,
        )
    else:
        print(
            f"{session.status} after {session.turns} turns "
            f"({session.usage.input} in / {session.usage.output} out)",
            flush=True,
        )


async def _pending_events(session: Session) -> AsyncIterator[Event]:
    """A budget refusal has events but needs no provider construction."""
    while session.events:
        yield session.events.pop(0)


class _Stop:
    """Turns --timeout, SIGTERM and SIGINT into a cancel of the run, and
    remembers which one it was.

    A cancel, because the loop already answers every tool call a cancel
    interrupts and bash already kills the command it is running when
    cancelled, so what is saved afterwards is a transcript a resume accepts.
    SIGTERM's default handler exits without unwinding, which is why a killed
    run used to write no result line.

    The first stop removes the handlers, so a second signal acts as it would
    without nare's: a stop that hangs can still be forced.
    """

    def __init__(self, timeout: float | None) -> None:
        self.timeout = timeout
        self.reason: str | None = None
        self.signal: str | None = None
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.current_task()
        self._signals: list[signal.Signals] = []
        self._timer: asyncio.TimerHandle | None = None

    def install(self) -> None:
        for signum in (signal.Signals[name] for name in STOP_SIGNALS):
            try:
                self._loop.add_signal_handler(signum, self._stop, "interrupted", signum)
            except (NotImplementedError, RuntimeError, ValueError):
                # Not the main thread, or no signal support: the deadline
                # still works, and the signal keeps its default.
                continue
            self._signals.append(signum)
        if self.timeout is not None:
            self._timer = self._loop.call_later(
                self.timeout, self._stop, "timeout", None
            )

    def release(self) -> None:
        for signum in self._signals:
            self._loop.remove_signal_handler(signum)
        self._signals = []
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _stop(self, reason: str, signum: signal.Signals | None) -> None:
        if self.reason is not None:
            return
        self.reason = reason
        self.signal = signum.name if signum is not None else None
        self.release()
        if self._task is not None:
            self._task.cancel()


def _stopped(session: Session, stop: _Stop, groups_from: int, jsonl: bool) -> None:
    """Record a run that a deadline or a signal ended."""
    # A command that left something running is this run's to stop. The one
    # running when the stop landed was killed by its own cancel.
    killed = [
        group["pgid"]
        for group in session.process_groups[groups_from:]
        if stop_group(group.get("pgid"))
    ]
    session.status = "error"
    if stop.reason == "timeout":
        assert stop.timeout is not None
        session.stop_reason = "timeout"
        session.error = f"run stopped: --timeout {stop.timeout:g} s reached"
        detail: dict[str, Any] = {"reason": "timeout", "timeout": stop.timeout}
    else:
        session.stop_reason = "interrupted"
        session.error = f"run stopped by {stop.signal}"
        detail = {"reason": "interrupted", "signal": stop.signal}
    detail["process_groups"] = killed
    _emit(Event("error", session.error, {"stop": detail}), jsonl)


async def _execute(
    session: Session,
    transport: Transport | None,
    args: argparse.Namespace,
    policy: Policy,
    schema: dict[str, Any] | None,
    prices: Prices | None,
) -> int:
    saved_turns = -1
    groups_from = len(session.process_groups)
    tool_calls_from = session.tool_calls
    corrections_limit = (
        SCHEMA_CORRECTIONS_DEFAULT
        if args.max_corrections is None
        else args.max_corrections
    )
    # Reported only for a schema run, as the budget it spends.
    corrections_budget = None if schema is None else corrections_limit
    cutoffs_from = session.cutoffs
    stop = _Stop(getattr(args, "timeout", None))
    stop.install()
    try:
        stream = (
            run(
                session,
                transport=transport,
                approve=approve_all,
                policy=policy,
                schema=schema,
                max_turns=args.max_turns,
                budget_tokens=args.budget_tokens,
                context_window=args.context_window,
                budget_usd=args.budget_usd,
                prices=prices,
                mcp_servers=args.mcp_servers,
                mcp_allow_all=args.tools is None,
                structured_output=args.structured_output,
                retry=args.retry,
                max_tool_calls=args.max_tool_calls,
                max_corrections=corrections_limit,
                max_cutoffs=args.max_cutoffs,
            )
            if transport is not None
            else _pending_events(session)
        )
        async for event in stream:
            _emit(event, args.jsonl)
            # Saved per turn, not once at the end. SIGTERM and SIGINT save on
            # the way out, but SIGKILL cannot be caught, and a run persisted
            # only at the end is not resumable after one. A streamed turn yields
            # events after step() commits its calls and before they have
            # results; a file saved then is one vendors reject on resume, so
            # the save waits for the results.
            if (
                args.session
                and session.turns != saved_turns
                and not unanswered(session.messages)
            ):
                # Advanced before the attempt, not after: a turn yields several
                # events, and a failure that left this behind would retry --
                # and log -- once per event rather than once per turn.
                saved_turns = session.turns
                try:
                    save(session, args.session)
                except OSError as exc:
                    # Transient here: only the final write decides the run.
                    log.warning("could not write --session %s: %s", args.session, exc)
    except asyncio.CancelledError:
        if stop.reason is None:
            raise
        task = asyncio.current_task()
        assert task is not None
        task.uncancel()
        _stopped(session, stop, groups_from, args.jsonl)
    finally:
        stop.release()
        if args.session:
            try:
                save(session, args.session)
            except OSError as exc:
                # Not just a non-zero exit: a caller may decide from the
                # four-value status alone, so a `done` result line beside exit
                # 1 still reads as finished work. Unpersisted work is not done.
                log.error("could not write --session %s: %s", args.session, exc)
                session.status = "error"
                session.stop_reason = "session_write"
                session.error = f"could not write --session {args.session}: {exc}"
                _emit(Event("error", session.error), args.jsonl)
    _emit_result(
        session,
        args.jsonl,
        max_tool_calls=args.max_tool_calls,
        tool_calls_from=tool_calls_from,
        max_corrections=corrections_budget,
        max_cutoffs=args.max_cutoffs,
        cutoffs_from=cutoffs_from,
    )
    return EXIT_CODES.get(session.status, EXIT_CODES["error"])


def _redact_stdin() -> int:
    """`nare redact`: the event redaction over a caller's own text.

    Raw bytes in and out, with exactly one decode and one encode: text mode
    would translate newlines and the locale could mangle what was not asked
    for, and the point of this command is that what comes out is byte for byte
    what an event's text field would carry. The whole result is decided before
    anything is written, so an unreadable stream costs the caller nothing.
    """
    data = sys.stdin.buffer.read()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        print(f"nare redact: stdin is not readable UTF-8 text: {exc}", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(redact(text).encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


def main(argv: list[str] | None = None, *, transport: Transport | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "contract":
        print(json.dumps(describe()), flush=True)
        return 0

    if args.command == "redact":
        return _redact_stdin()

    if args.command == "tui":
        if importlib.util.find_spec("textual") is None:
            print(
                "nare tui needs the tui extra: uv tool install 'nare[tui]'",
                file=sys.stderr,
            )
            return NEVER_STARTED
        from nare.tui.app import main as tui_main

        return tui_main(args, transport=transport)

    # Everything below exits 2 and emits nothing on stdout: the result line
    # implies a session existed, so a run that never started does not emit one.
    if args.contract is not None and args.contract != CONTRACT_VERSION:
        print(
            f"nare speaks contract {CONTRACT_VERSION}, and the caller asked for "
            f"{args.contract}. Refusing rather than emitting a stream it would "
            "mis-read.",
            file=sys.stderr,
        )
        return NEVER_STARTED
    if not args.yes:
        print("nare run refuses to start unattended without --yes", file=sys.stderr)
        return NEVER_STARTED
    if args.prompt is not None and args.prompt_file is not None:
        source = "stdin (-)" if args.prompt == "-" else "positional prompt"
        print(f"nare: {source} conflicts with --prompt-file", file=sys.stderr)
        return NEVER_STARTED
    if args.prompt is None and args.prompt_file is None and not args.resume:
        print("nare run needs a prompt, or --resume PATH", file=sys.stderr)
        return NEVER_STARTED

    try:
        if args.timeout is not None:
            finite_number(args.timeout, "--timeout")
        if args.max_tool_calls is not None and args.max_tool_calls <= 0:
            raise ValueError("--max-tool-calls must be a positive integer")
        if args.max_corrections is not None and args.max_corrections <= 0:
            raise ValueError("--max-corrections must be a positive integer")
        if args.max_cutoffs < 0:
            raise ValueError("--max-cutoffs must be a nonnegative integer")
        prices = validate_run_args(args)
        if args.prompt_file is not None:
            try:
                args.prompt = Path(args.prompt_file).read_bytes().decode("utf-8")
            except (OSError, UnicodeError) as exc:
                raise ValueError(f"--prompt-file {args.prompt_file}: {exc}") from exc
        elif args.prompt == "-":
            try:
                args.prompt = sys.stdin.buffer.read().decode("utf-8")
            except (OSError, UnicodeError) as exc:
                raise ValueError(f"stdin (-): {exc}") from exc
        policy = policy_from_args(args)
        schema = schema_from_args(args)
        if args.structured_output is not None and schema is None:
            raise ValueError("--structured-output requires --schema")
        if args.max_corrections is not None and schema is None:
            raise ValueError("--max-corrections requires --schema")
        session = _load_or_new(args)
        adopt_system(args, session)
        configure_budgets(session, args.budget_tokens, args.budget_usd)
        if check_budget(session):
            # Already-spent sessions report their artifacts even when provider
            # construction would fail (for example, credentials were removed).
            transport = None
        elif transport is None:
            transport = transport_from_args(args)
        if transport is not None:
            check_structured_output(transport, schema, args.structured_output)
    except (ValueError, OSError, UnsupportedSchema) as exc:
        print(f"nare: {exc}", file=sys.stderr)
        return NEVER_STARTED

    try:
        return asyncio.run(_execute(session, transport, args, policy, schema, prices))
    except KeyboardInterrupt:
        print("nare: interrupted", file=sys.stderr)
        return 130
