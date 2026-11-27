"""The person-facing entry points, in the shape Claude Code and Codex share.

    nare                  the terminal UI, or a getting-started without it
    nare "<task>"         the terminal UI with that first prompt, or a
                          line-mode session without the tui extra
    nare -p "<task>"      one-shot: the answer on stdout (also --print)
    nare exec "<task>"    the same, in Codex's spelling

None of this is the machine contract. `nare run` is, and nothing here changes
what it does: nare -p and nare exec reuse its path and its flags, and
`--output-format stream-json` prints exactly what `nare run --jsonl` prints.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import stat
import sys
from collections.abc import Awaitable, Callable
from typing import Any

from nare import __version__, cli
from nare.contract import EXIT_CODES, NEVER_STARTED
from nare.loop import final_text
from nare.session import Session, reopen
from nare.transport import Transport

# The first word that keeps meaning a subcommand. Anything else is a task.
SUBCOMMANDS = ("run", "tui", "contract", "redact", "mcp")
HELP_FLAGS = frozenset({"-h", "--help"})
PRINT_FLAGS = frozenset({"-p", "--print"})
OUTPUT_FORMATS = ("text", "json", "stream-json")
EXIT_WORDS = frozenset({"exit", "quit", "/exit", "/quit"})

KEYS = """\
nare needs a model key: set ANTHROPIC_API_KEY, or OPENAI_API_KEY with
--provider openai. nare run --help lists every provider option."""

GETTING_STARTED = f"""\
nare {{version}}: a coding agent for your terminal.

  nare "<task>"       work on a task in this directory; nare asks before
                      each change and each command
  nare -p "<task>"    run one task and print the answer (also: nare exec)
  nare run --yes ...  the machine contract, for scripts and other programs

{KEYS}
"""

HELP = f"""\
usage: nare ["<task>"] [options]
       nare -p "<task>" [options]
       nare exec "<task>" [options]
       nare <command> ...

A coding agent for your terminal, and a harness other programs drive.

Start here:
  nare                open the terminal UI
  nare "<task>"       open it with a first task; without the tui extra, a
                      line-mode session that asks before each change
  nare -p "<task>"    one-shot: the answer on stdout, progress on stderr.
                      Piped stdin is extra context; - reads the prompt from it.
                      --output-format text|json|stream-json, --json
  exec                nare exec "<task>": the same as nare -p
  tui                 nare tui [options]: the terminal UI, or --attach to
                      watch a run

For callers (scripts, CI, other agents):
  run                 nare run --yes --jsonl "<task>": the machine contract
                      (docs/contract.md)
  contract            print the contract this nare speaks, as JSON
  redact              apply the event redaction rules to stdin
  mcp                 authorize a remote MCP server that needs OAuth

Options:
  -h, --help          show this help; nare <command> --help for a command's
  --version           print the installed nare version

{KEYS}
A command's flags: nare run --help, nare -p --help. A task that is a
command's name goes after --: nare -- run.
"""


def route(argv: list[str]) -> str | None:
    """Which form an argv is. None is a subcommand or a top-level flag, which
    the argument parser handles as it always has."""
    if not argv:
        return "interactive"
    first = argv[0]
    if first in SUBCOMMANDS or first == "--version":
        return None
    if first in HELP_FLAGS:
        return "help"
    if first == "exec":
        return "exec"
    if any(word in PRINT_FLAGS for word in _before_dashes(argv)):
        return "print"
    return "interactive"


def _before_dashes(argv: list[str]) -> list[str]:
    return argv[: argv.index("--")] if "--" in argv else argv


def tui_available() -> bool:
    return importlib.util.find_spec("textual") is not None


def interactive_terminal() -> bool:
    """A person on both ends: typing on stdin and reading stdout."""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError, OSError):
        return False


def stdin_piped() -> bool:
    """Whether stdin is a pipe or a file to read as context. A terminal,
    /dev/null, or no stdin at all is not read."""
    try:
        mode = os.fstat(sys.stdin.fileno()).st_mode
    except (AttributeError, ValueError, OSError):
        return False
    return stat.S_ISFIFO(mode) or stat.S_ISREG(mode)


def main(mode: str, argv: list[str], *, transport: Transport | None = None) -> int:
    if mode == "help":
        print(HELP, end="")
        return 0
    if mode == "exec":
        return print_main(argv[1:], "nare exec", transport)
    if mode == "print":
        return print_main(argv, "nare", transport)
    return interactive_main(argv, transport)


def interactive_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nare", usage='nare ["<task>"] [options]', add_help=False
    )
    parser.add_argument("-h", "--help", action="store_true")
    parser.add_argument("prompt", nargs="?")
    cli.add_run_flags(parser)
    parser.add_argument("--fork", action="store_true", help=cli.FORK_HELP)
    return parser


def interactive_main(argv: list[str], transport: Transport | None) -> int:
    args = interactive_parser().parse_args(argv)
    if args.help:
        print(HELP, end="")
        return 0
    if args.prompt in SUBCOMMANDS and "--" not in argv:
        print(
            f"nare: {args.prompt!r} is a command: put its options after it "
            f"(nare {args.prompt} ...), or send it as a task with "
            f"nare -- {args.prompt}",
            file=sys.stderr,
        )
        return NEVER_STARTED
    at_terminal = interactive_terminal()
    if args.prompt is None and not (at_terminal and tui_available()):
        print(GETTING_STARTED.format(version=__version__), end="")
        if at_terminal:
            print(f"\n{cli._tui_extra_hint(__version__)}")
        return 0
    if not at_terminal:
        print(
            'nare: nare "<task>" is interactive, and there is no terminal to ask '
            'before a change. For a one-shot run use nare -p "<task>"; for the '
            "machine contract, nare run --yes",
            file=sys.stderr,
        )
        return NEVER_STARTED
    args.attach = None
    if args.root is None:
        args.root = os.getcwd()
    if tui_available():
        from nare.tui import app

        return app.main(args, transport=transport)
    return line_session(args, transport)


def print_parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        usage=f'{"nare -p" if prog == "nare" else prog} "<task>" [options]',
        description=(
            "Run one task and print the answer on stdout, with progress on "
            "stderr. Takes every nare run flag. Without a permission flag, a "
            "tool call that would change something or run a command is denied "
            "and reported, and the run goes on."
        ),
    )
    cli.add_run_command_flags(parser)
    if prog == "nare":
        parser.add_argument(
            "-p",
            "--print",
            action="store_true",
            help="run the task once and print the answer (nare exec is the same)",
        )
    parser.add_argument(
        "--output-format",
        choices=OUTPUT_FORMATS,
        help=(
            "text: the answer alone (default); json: the result line alone; "
            "stream-json: the event stream and the result line, exactly as "
            "nare run --jsonl prints them"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="--output-format stream-json (Codex's spelling; --jsonl is the same)",
    )
    parser.add_argument(
        "--dangerously-skip-permissions",
        "--yolo",
        dest="skip_permissions",
        action="store_true",
        help="approve every tool call, as --yes does",
    )
    return parser


def print_main(argv: list[str], prog: str, transport: Transport | None) -> int:
    args = print_parser(prog).parse_args(argv)
    try:
        output = output_format(args)
        read_prompt(args, prog)
    except ValueError as exc:
        print(f"nare: {exc}", file=sys.stderr)
        return NEVER_STARTED
    args.jsonl = output == "stream-json"
    if args.root is None:
        args.root = os.getcwd()
    args.yes = args.yes or args.skip_permissions
    if output != "stream-json":
        from nare.terminal import progress

        args.emit = progress
        args.emit_result = _json_result if output == "json" else _text_result
    approver = None
    if not args.yes:
        from nare.terminal import deny_approver

        approver = deny_approver
    return cli.run_command(args, transport, approver=approver)


def output_format(args: argparse.Namespace) -> str:
    """The one output format asked for. --json and --jsonl both mean
    stream-json, and a different --output-format beside them is a conflict."""
    streamed = [
        flag for flag, on in (("--json", args.json), ("--jsonl", args.jsonl)) if on
    ]
    if streamed and args.output_format not in (None, "stream-json"):
        raise ValueError(
            f"{streamed[0]} conflicts with --output-format {args.output_format}"
        )
    if streamed:
        return "stream-json"
    return str(args.output_format or "text")


def read_prompt(args: argparse.Namespace, prog: str) -> None:
    """Piped stdin is extra context after the task, in a <stdin> block. With
    no task, stdin is the whole prompt, as positional - makes it."""
    piped = stdin_piped()
    if args.prompt is None and args.prompt_file is None and args.resume is None:
        if not piped:
            command = "nare -p" if prog == "nare" else prog
            raise ValueError(
                f'{command} needs a task: {command} "<task>", or pipe one on stdin'
            )
        args.prompt = "-"
        return
    if args.prompt in (None, "-") or args.prompt_file is not None or not piped:
        return
    try:
        extra = sys.stdin.buffer.read().decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"stdin: {exc}") from exc
    if extra.strip():
        if not extra.endswith("\n"):
            extra += "\n"
        args.prompt = f"{args.prompt}\n\n<stdin>\n{extra}</stdin>"


def _text_result(session: Session, **totals: Any) -> None:
    """The answer alone on stdout; anything else a person needs on stderr."""
    if session.status == "done":
        print(final_text(session) or "", flush=True)
    elif session.status == "blocked":
        for question in session.questions:
            print(question, flush=True)
    else:
        print(f"nare: {session.error or session.status}", file=sys.stderr, flush=True)


def _json_result(session: Session, **totals: Any) -> None:
    print(json.dumps(cli.result_record(session, **totals)), flush=True)


def line_session(args: argparse.Namespace, transport: Transport | None) -> int:
    """nare "<task>" without the tui extra: the task, then follow-ups, at a
    plain terminal. Ends at end of input (Ctrl-D) or on exit."""
    from nare.terminal import progress, read_line, terminal_approver

    # The run-only options at their defaults, under the flags given.
    full = print_parser("nare").parse_args([])
    for key, value in vars(args).items():
        setattr(full, key, value)
    args = full
    try:
        session, transport, policy, schema, prices, check, cited = cli.prepare_run(
            args, transport
        )
    except (ValueError, OSError) as exc:
        print(f"nare: {exc}", file=sys.stderr)
        return NEVER_STARTED
    args.emit = progress
    args.emit_result = _text_result
    args.approve = terminal_approver(policy)
    # A person reads the reply, so a question in it is answered, not nudged.
    args.question_nudge = False

    async def drive() -> int:
        code = EXIT_CODES["error"]
        while True:
            code = await cli._execute(
                session, transport, args, policy, schema, prices, check, cited
            )
            text = await _follow_up(read_line)
            if text is None:
                return code
            reopen(session, text)

    try:
        return asyncio.run(drive())
    except KeyboardInterrupt:
        print("", file=sys.stderr)
        return 130


async def _follow_up(read: Callable[[str], Awaitable[str | None]]) -> str | None:
    """The next thing the person types, or None to end. A blank line asks
    again."""
    while True:
        line = await read("\n> ")
        if line is None:
            return None
        text = line.strip()
        if text in EXIT_WORDS:
            return None
        if text:
            return text
