"""`nare run` — the headless CLI.

A thin adapter over the library. Conductor's performer spawns this exactly as
it spawns claude or opencode, which keeps nare's own surface on the critical
path so it cannot rot.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import logging
import os
import sys
from collections.abc import AsyncIterator, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

from nare import __version__
from nare.accounting import Prices, finite_number, prices_from_env
from nare.contract import CONTRACT_VERSION, EXIT_CODES, NEVER_STARTED, describe
from nare.events import Event, redact
from nare.loop import (
    MAX_TURNS_DEFAULT,
    STRUCTURED_OUTPUT_MODES,
    budget_record,
    check_budget,
    check_structured_output,
    configure_budgets,
    run,
)
from nare.mcp import parse_servers
from nare.schema import UnsupportedSchema, check_supported
from nare.session import Session, loads, new_session, reopen, save, unanswered
from nare.tools import TOOLS, Policy, approve_all
from nare.transport import Transport, make_transport

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
            "subset (type, properties, required, items, enum, "
            "additionalProperties) and refuses a schema using anything else "
            "rather than validating it only in part"
        ),
    )
    run_parser.add_argument(
        "--structured-output",
        choices=STRUCTURED_OUTPUT_MODES,
        help=(
            "ask the backend to constrain answers to the --schema at decode time "
            "(openai provider only). A constrained turn offers no tools, so only "
            "the schema correction turn, or every turn of a run with no tools, "
            "is constrained. nare still validates every answer"
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
        "--provider",
        choices=["anthropic", "openai"],
        default=os.environ.get("NARE_PROVIDER", "anthropic"),
        help="model provider: anthropic, or openai for anything speaking Chat "
        "Completions, including a proxy or a local server via --base-url "
        "(env: NARE_PROVIDER)",
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
        choices=["low", "medium", "high"],
        help="reasoning effort",
    )
    parser.add_argument(
        "--system",
        metavar="TEXT",
        help="replace nare's default system prompt with TEXT; --system '' sends none",
    )
    parser.add_argument(
        "--append-system",
        metavar="TEXT",
        help="add TEXT after the system prompt: the default, or --system's",
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


def transport_from_args(args: argparse.Namespace) -> Transport:
    """Everything vendor-shaped is bound here and never reaches the loop."""
    return make_transport(
        args.provider,
        model=args.model,
        base_url=args.base_url,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        effort=args.effort,
        system=system_from_args(args),
        streaming=args.stream,
        image_input=args.image_input,
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
    return Policy(tools=tools, root=root, pending=unresolved_names)


def validate_run_args(args: argparse.Namespace) -> Prices | None:
    """The startup checks nare run and nare tui share. Raises ValueError or
    OSError, which each caller turns into exit 2 before anything starts.
    """
    # A resume with nowhere to write back silently throws the run away and
    # replays the stale prefix next time. Resuming a file means updating it.
    if args.resume and not args.session:
        args.session = args.resume
    if args.budget_tokens is not None and args.budget_tokens <= 0:
        raise ValueError("--budget-tokens must be a positive integer")
    if args.context_window is not None and args.context_window <= 0:
        raise ValueError("--context-window must be a positive integer")
    if args.budget_usd is not None:
        finite_number(args.budget_usd, "--budget-usd")
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
    # Resuming is how conductor's relay_feedback works: reopen and keep going.
    reopen(session, args.prompt)
    return session


def _emit(event: Event, jsonl: bool) -> None:
    if jsonl:
        print(json.dumps(asdict(event)), flush=True)
    else:
        print(f"[{event.type}] {event.text}", flush=True)


def _emit_result(session: Session, jsonl: bool) -> None:
    if jsonl:
        print(
            json.dumps(
                {
                    "type": "result",
                    "session_id": session.id,
                    "status": session.status,
                    "questions": session.questions,
                    "usage": asdict(session.usage),
                    "budget": budget_record(session),
                    "stop_reason": session.stop_reason,
                    "turns": session.turns,
                    "contract": CONTRACT_VERSION,
                    "nare": __version__,
                    "output": session.output,
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


async def _execute(
    session: Session,
    transport: Transport | None,
    args: argparse.Namespace,
    policy: Policy,
    schema: dict[str, Any] | None,
    prices: Prices | None,
) -> int:
    saved_turns = -1
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
            )
            if transport is not None
            else _pending_events(session)
        )
        async for event in stream:
            _emit(event, args.jsonl)
            # Saved per turn, not once at the end. SIGTERM's default handler
            # exits without unwinding, so `finally` never runs and a run
            # persisted only at the end is not resumable after a kill --
            # which is how conductor ends a slow run. A streamed turn yields
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
    finally:
        if args.session:
            try:
                save(session, args.session)
            except OSError as exc:
                # Not just a non-zero exit: conductor derives everything from
                # the four-value status, so a `done` result line beside exit 1
                # still reads as finished work. Unpersisted work is not done.
                log.error("could not write --session %s: %s", args.session, exc)
                session.status = "error"
                session.error = f"could not write --session {args.session}: {exc}"
                _emit(Event("error", session.error), args.jsonl)
    _emit_result(session, args.jsonl)
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
        session = _load_or_new(args)
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
