"""`nare run` — the headless CLI.

A thin adapter over the library. Conductor's performer spawns this exactly as
it spawns claude or opencode, which keeps nare's own surface on the critical
path so it cannot rot.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import asdict
from pathlib import Path

from nare.events import Event
from nare.loop import MAX_TURNS_DEFAULT, run
from nare.session import Session, append_user_text, dumps, loads, new_session
from nare.tools import approve_all
from nare.transport import Transport, make_transport

log = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nare", description="A standalone agent harness."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="run a task headlessly")

    run_parser.add_argument("prompt", nargs="?", help="the task, as plain text")
    run_parser.add_argument(
        "--provider",
        choices=["anthropic"],
        default=os.environ.get("NARE_PROVIDER", "anthropic"),
        help="model provider (env: NARE_PROVIDER)",
    )
    run_parser.add_argument(
        "--model",
        default=os.environ.get("NARE_MODEL", "claude-sonnet-5"),
        help="model name (env: NARE_MODEL)",
    )
    run_parser.add_argument(
        "--base-url",
        default=os.environ.get("NARE_BASE_URL"),
        help="override the vendor endpoint (env: NARE_BASE_URL)",
    )
    run_parser.add_argument(
        "--temperature",
        type=float,
        help="sampling temperature; not accepted by the anthropic provider",
    )
    run_parser.add_argument(
        "--max-tokens", type=int, help="output token cap (default 8192)"
    )
    run_parser.add_argument(
        "--effort",
        choices=["low", "medium", "high"],
        help="reasoning effort",
    )
    run_parser.add_argument("--system", help="system prompt / persona text")
    run_parser.add_argument(
        "--jsonl", action="store_true", help="emit typed JSONL on stdout"
    )
    run_parser.add_argument(
        "--yes",
        action="store_true",
        help="approve every tool call (required for unattended runs)",
    )
    run_parser.add_argument("--resume", help="continue the session at this path")
    run_parser.add_argument("--session", help="write the session to this path")
    run_parser.add_argument(
        "--max-turns",
        type=int,
        default=MAX_TURNS_DEFAULT,
        help=f"stop after this many turns (default {MAX_TURNS_DEFAULT})",
    )
    return parser


def transport_from_args(args: argparse.Namespace) -> Transport:
    """Everything vendor-shaped is bound here and never reaches the loop."""
    return make_transport(
        args.provider,
        model=args.model,
        base_url=args.base_url,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        effort=args.effort,
        system=args.system,
    )


def _load_or_new(args: argparse.Namespace) -> Session:
    if not args.resume:
        return new_session(args.prompt)
    session = loads(Path(args.resume).read_text())
    if args.prompt:
        append_user_text(session, args.prompt)
    # Resuming is how conductor's relay_feedback works: reopen and keep going.
    session.status = "working"
    session.questions = []
    session.error = None
    session.stop_reason = None
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
                    "stop_reason": session.stop_reason,
                    "turns": session.turns,
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


async def _execute(
    session: Session, transport: Transport, args: argparse.Namespace
) -> int:
    try:
        async for event in run(
            session,
            transport=transport,
            approve=approve_all,
            max_turns=args.max_turns,
        ):
            _emit(event, args.jsonl)
    finally:
        if args.session:
            try:
                Path(args.session).write_text(dumps(session))
            except OSError as exc:
                log.error("could not write --session %s: %s", args.session, exc)
    _emit_result(session, args.jsonl)
    return 1 if session.status == "error" else 0


def main(argv: list[str] | None = None, *, transport: Transport | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    parser = build_parser()
    args = parser.parse_args(argv)

    # Everything below exits 2 and emits nothing on stdout: the result line
    # implies a session existed, so a run that never started does not emit one.
    if not args.yes:
        print("nare run refuses to start unattended without --yes", file=sys.stderr)
        return 2
    if not args.prompt and not args.resume:
        print("nare run needs a prompt, or --resume PATH", file=sys.stderr)
        return 2

    try:
        session = _load_or_new(args)
        if transport is None:
            transport = transport_from_args(args)
    except (ValueError, OSError) as exc:
        print(f"nare: {exc}", file=sys.stderr)
        return 2

    return asyncio.run(_execute(session, transport, args))
