"""`nare run` — the headless CLI.

A thin adapter over the library. Conductor's performer spawns this exactly as
it spawns claude or opencode, which keeps nare's own surface on the critical
path so it cannot rot.
"""

from __future__ import annotations

import argparse
import os

from nare.loop import MAX_TURNS_DEFAULT
from nare.transport import Transport, make_transport


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
        "--yes", action="store_true", help="approve every tool call; required"
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
