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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nare import __version__, cli
from nare.contract import EXIT_CODES, NEVER_STARTED
from nare.loop import final_text
from nare.session import (
    SavedSession,
    Session,
    find_session,
    new_store_path,
    newest_in,
    reopen,
    resumable_in,
    started_now,
)
from nare.transport import Transport

# The first word that keeps meaning a subcommand. Anything else is a task.
SUBCOMMANDS = ("run", "tui", "contract", "redact", "mcp")
# The person-facing commands this module answers, in Codex's spelling.
SESSION_COMMANDS = ("sessions", "resume", "fork")
AUTH_COMMANDS = ("auth", "login", "logout")
COMMANDS = (*SUBCOMMANDS, "exec", *SESSION_COMMANDS, *AUTH_COMMANDS)
HELP_FLAGS = frozenset({"-h", "--help"})
PRINT_FLAGS = frozenset({"-p", "--print"})
OUTPUT_FORMATS = ("text", "json", "stream-json")
EXIT_WORDS = frozenset({"exit", "quit", "/exit", "/quit"})

KEYS = """\
nare needs a model key: run nare auth login to store one, or set
ANTHROPIC_API_KEY (OPENAI_API_KEY with --provider openai). nare run --help
lists every provider option."""

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
                      With no task (or -), stdin is the prompt; with a task,
                      stdin is not read.
                      --output-format text|json|stream-json, --json
  exec                nare exec "<task>": the same as nare -p
  -c, -r <id>         continue the newest session here, or the one named;
                      --fork-session writes the continuation as a new one
  sessions            nare sessions: this directory's saved sessions
  auth                nare auth login|status|logout: store a key once
                      (also nare login, nare logout)
  resume, fork        nare resume [--last|<id>], nare fork [--last|<id>]:
                      Codex's spelling of -c and -r; nare exec resume too
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
        return "exec-resume" if argv[1:2] == ["resume"] else "exec"
    if first in SESSION_COMMANDS:
        return first
    if first in AUTH_COMMANDS:
        return "auth"
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
    if mode == "sessions":
        return sessions_main(argv[1:])
    if mode == "auth":
        return auth_main(argv)
    try:
        if mode == "exec-resume":
            return print_main(
                codex_resume(argv[2:], "nare exec resume"), "nare exec", transport
            )
        if mode in ("resume", "fork"):
            return interactive_main(
                codex_resume(argv[1:], f"nare {mode}", fork=mode == "fork"), transport
            )
    except ValueError as exc:
        print(f"nare: {exc}", file=sys.stderr)
        return NEVER_STARTED
    if mode == "print":
        return print_main(argv, "nare", transport)
    return interactive_main(argv, transport)


def interactive_parser() -> argparse.ArgumentParser:
    parser = cli.NareParser(
        prog="nare",
        usage='nare ["<task>"] [options]',
        add_help=False,
        formatter_class=cli.AliasHelpFormatter,
    )
    parser.add_argument("-h", "--help", action="store_true")
    parser.add_argument("prompt", nargs="?")
    cli.add_run_flags(parser)
    parser.add_argument("--fork", action="store_true", help=cli.FORK_HELP)
    add_session_flags(parser)
    return parser


def add_session_flags(parser: argparse.ArgumentParser) -> None:
    """-c and --fork-session, for the person-facing forms only: nare run keeps
    its sessions where --session puts them."""
    for action in (
        parser.add_argument(
            "-c",
            "--continue",
            dest="continue_last",
            action="store_true",
            help="continue the newest session started in this directory",
        ),
        parser.add_argument(
            "--no-project-instructions",
            dest="no_instructions",
            action="store_true",
            help="leave out the project's AGENTS.md (or CLAUDE.md) files, which "
            "are added after the system prompt by default",
        ),
        parser.add_argument(
            "--fork-session",
            action="store_true",
            help="with -c or -r: write the continuation as a new session and "
            "leave the one it came from as it was",
        ),
    ):
        cli._mark(action, nare_exact=True)


def settle_session(args: argparse.Namespace) -> None:
    """Where a person-facing run reads and writes its session: -c and -r find
    one in this directory's store, and a run given no --session saves to the
    store. Raises ValueError, which is exit 2."""
    here = os.getcwd()
    if args.continue_last and args.resume is not None:
        raise ValueError("-c continues the newest session; -r names one: give one")
    if args.continue_last:
        args.resume = str(newest_in(here))
    elif args.resume:
        args.resume = str(find_session(args.resume, here))
    if args.fork_session:
        if args.resume is None:
            raise ValueError("--fork-session needs -c or -r: it branches a session")
        args.fork = True
    args.session_id = None
    if args.session is None and (args.fork or args.resume is None):
        path, args.session_id = new_store_path(here)
        args.session = str(path)


def stamp_session(args: argparse.Namespace) -> Callable[[Session], None]:
    """Record where and when a person-facing session started, for nare
    sessions and the resume picker. A session that already has its record
    keeps it, except a fork, which starts now."""

    def stamp(session: Session) -> None:
        if args.session_id is not None:
            session.id = args.session_id
        if session.started_at is None or args.fork:
            session.started_at = started_now()
        if session.cwd is None:
            session.cwd = os.getcwd()
            session.root = None if args.root is None else str(Path(args.root).resolve())

    return stamp


def codex_resume(argv: list[str], prog: str, *, fork: bool = False) -> list[str]:
    """Codex's resume, fork and exec resume, as -c, -r and --fork-session:
    `--last` or a session id first, then the options and the prompt."""
    head, rest = argv[:1], argv[1:]
    if head == ["--last"]:
        picked = ["-c"]
    elif head and not head[0].startswith("-"):
        picked = ["-r", head[0]]
    elif prog == "nare exec resume":
        raise ValueError(f'{prog} needs --last or a session id: {prog} --last "<task>"')
    else:
        # No session named: the terminal UI's picker chooses one.
        picked, rest = [], argv
        rest = [*rest, "--resume="]
    return [*picked, *(["--fork-session"] if fork else []), *rest]


def auth_main(argv: list[str]) -> int:
    """nare auth login|status|logout, and Codex's nare login [status] and
    nare logout. Messages go to stderr, the status table to stdout."""
    from nare import credentials

    if argv[0] == "login":
        words = (
            ["status", *argv[2:]] if argv[1:2] == ["status"] else ["login", *argv[1:]]
        )
    elif argv[0] == "logout":
        words = ["logout", *argv[1:]]
    else:
        words = argv[1:]
    parser = cli.NareParser(
        prog="nare auth", description="Store a provider key once, and check it."
    )
    sub = parser.add_subparsers(dest="action", required=True)
    providers = list(credentials.KEY_ENV)
    default = os.environ.get("NARE_PROVIDER") or "anthropic"
    login = sub.add_parser(
        "login",
        help="store a key in NARE_HOME/credentials.json (mode 0600), typed with "
        "the input hidden",
    )
    login.add_argument("--provider", choices=[*providers, "bedrock"], default=default)
    login.add_argument(
        "--with-api-key",
        action="store_true",
        help="read the key from stdin instead, for scripts",
    )
    status = sub.add_parser(
        "status",
        help="name where each provider's key comes from, never the key; exit 0 "
        "when the provider has one, 1 when not",
    )
    status.add_argument("--provider", choices=[*providers, "bedrock", "fake"])
    logout = sub.add_parser("logout", help="remove a stored key")
    logout.add_argument("--provider", choices=providers, default=default)
    args = parser.parse_args(words)
    try:
        if args.action == "login":
            if args.provider == "bedrock":
                raise ValueError(
                    "bedrock signs with AWS credentials, not a stored key: use an "
                    "AWS profile, the environment or a role"
                )
            key = credentials.read_key(args.provider, from_stdin=args.with_api_key)
            path = credentials.store_key(args.provider, key)
            print(f"nare: stored the {args.provider} key in {path}", file=sys.stderr)
            return 0
        if args.action == "logout":
            removed = credentials.remove_key(args.provider)
            print(
                f"nare: removed the stored {args.provider} key"
                if removed
                else f"nare: no {args.provider} key was stored",
                file=sys.stderr,
            )
            return 0
        chosen = args.provider or default
        found = False
        for provider in [*providers, "bedrock"] if args.provider is None else [chosen]:
            has, where = credentials.source(provider)
            found = found or (has and provider == chosen)
            marker = "*" if provider == chosen else " "
            print(f"{marker} {provider:9} {where}")
        return 0 if found else 1
    except ValueError as exc:
        print(f"nare: {exc}", file=sys.stderr)
        return NEVER_STARTED


def no_key(args: argparse.Namespace, transport: Transport | None) -> bool:
    """A person-facing run with no key says how to get one, in one line, and
    stops before anything starts. A run handed its transport needs none."""
    from nare.credentials import missing_key_line

    line = None if transport is not None else missing_key_line(args.provider)
    if line is not None:
        print(f"nare: {line}", file=sys.stderr)
    return line is not None


def sessions_main(argv: list[str]) -> int:
    """nare sessions: this directory's saved sessions, newest first."""
    parser = cli.NareParser(
        prog="nare sessions",
        description="List the sessions started in this directory, newest first.",
    )
    parser.add_argument("--json", action="store_true", help="one JSON array")
    args = parser.parse_args(argv)
    here = os.getcwd()
    try:
        rows = resumable_in(here)
    except ValueError as exc:  # a relative NARE_HOME
        print(f"nare: {exc}", file=sys.stderr)
        return NEVER_STARTED
    if args.json:
        print(json.dumps([_row(row) for row in rows]))
        return 0
    if not rows:
        print(f"no saved session started in {here}")
        return 0
    for row in rows:
        record = _row(row)
        started = str(record["started"] or "")[:16].replace("T", " ")
        prompt = record["prompt"]
        if len(prompt) > 60:
            prompt = prompt[:59] + "…"
        print(f"{record['id']}  {started:16}  {record['status']:11}  {prompt}")
    print("\ncontinue the newest: nare -c; one of these: nare -r <id>")
    return 0


def _row(row: SavedSession) -> dict[str, Any]:
    return {
        "id": row.path.stem,
        "session_id": row.id,
        "path": str(row.path),
        # The recorded start, or for a session saved before nare recorded
        # one, when its file last changed.
        "started": row.started_at
        or datetime.fromtimestamp(row.changed, UTC).isoformat(timespec="seconds"),
        "changed": datetime.fromtimestamp(row.changed, UTC).isoformat(
            timespec="seconds"
        ),
        "prompt": row.prompt,
        "status": row.status,
        "turns": row.turns,
    }


def interactive_main(argv: list[str], transport: Transport | None) -> int:
    parser = interactive_parser()
    args = parser.parse_args(argv)
    try:
        cli.resolve_aliases(parser, argv, args)
    except ValueError as exc:
        print(f"nare: {exc}", file=sys.stderr)
        return NEVER_STARTED
    if args.help:
        print(HELP, end="")
        return 0
    if args.prompt in COMMANDS and "--" not in argv:
        print(
            f"nare: {args.prompt!r} is a command: put its options after it "
            f"(nare {args.prompt} ...), or send it as a task with "
            f"nare -- {args.prompt}",
            file=sys.stderr,
        )
        return NEVER_STARTED
    at_terminal = interactive_terminal()
    resuming = args.continue_last or args.resume is not None
    if args.prompt is None and not resuming and not (at_terminal and tui_available()):
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
    if no_key(args, transport):
        return NEVER_STARTED
    if args.resume == "" and not tui_available():
        print(
            "nare: picking a session needs the terminal UI; give an id with -r "
            "(nare sessions lists them), or -c for the newest",
            file=sys.stderr,
        )
        return NEVER_STARTED
    try:
        settle_session(args)
        from nare import mcp_config

        # At a terminal, before anything takes the screen: ask about an
        # untrusted project's servers once.
        args.mcp_defaults, _skipped = mcp_config.defaults(args, mcp_config.terminal_ask)
    except ValueError as exc:
        print(f"nare: {exc}", file=sys.stderr)
        return NEVER_STARTED
    args.load_instructions = not args.no_instructions
    args.instructions_event = cli.load_instructions(args)
    if tui_available():
        from nare.tui import app

        return app.main(args, transport=transport)
    return line_session(args, transport)


def print_parser(prog: str) -> argparse.ArgumentParser:
    parser = cli.NareParser(
        prog=prog,
        formatter_class=cli.AliasHelpFormatter,
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
    add_session_flags(parser)
    return parser


def print_main(argv: list[str], prog: str, transport: Transport | None) -> int:
    parser = print_parser(prog)
    args = parser.parse_args(argv)
    try:
        cli.resolve_aliases(parser, argv, args)
        output = output_format(args)
        if no_key(args, transport):
            return NEVER_STARTED
        if args.root is None:
            args.root = os.getcwd()
        settle_session(args)
        read_prompt(args, prog)
        from nare import mcp_config

        args.mcp_defaults, skipped = mcp_config.defaults(args, None)
        if skipped:
            print(mcp_config.skipped_line(skipped), file=sys.stderr)
    except ValueError as exc:
        print(f"nare: {exc}", file=sys.stderr)
        return NEVER_STARTED
    args.jsonl = output == "stream-json"
    args.load_instructions = args.load_instructions or not args.no_instructions
    if output != "stream-json":
        from nare.terminal import progress

        args.emit = progress
        args.emit_result = _json_result if output == "json" else _text_result
    approver = None
    if not args.yes:
        from nare.terminal import deny_approver

        approver = deny_approver
    return cli.run_command(
        args, transport, approver=approver, on_session=stamp_session(args)
    )


def output_format(args: argparse.Namespace) -> str:
    """The one output format asked for. --json and --jsonl both mean
    stream-json, and a different --output-format beside them is a conflict."""
    streamed = [
        flag
        for flag, on in (("--json", args.alias_json), ("--jsonl", args.jsonl))
        if on
    ]
    if streamed and args.output_format not in (None, "stream-json"):
        raise ValueError(
            f"{streamed[0]} conflicts with --output-format {args.output_format}"
        )
    if streamed:
        return "stream-json"
    return str(args.output_format or "text")


def read_prompt(args: argparse.Namespace, prog: str) -> None:
    """Settle the prompt a one-shot run sends. Stdin is read only when the
    prompt is - or missing, as Codex exec does: a task on the command line
    never waits on stdin, so a caller that leaves its pipe open does not hang
    the run (#351). With no task, piped stdin is the whole prompt, and on a
    resume the follow-up (#349). A resume with no follow-up exits 2 before
    any model call, since running a finished session again only repeats it.
    """
    if args.prompt_file is not None or args.prompt not in (None, "-"):
        return
    command = "nare -p" if prog == "nare" else prog
    resuming = args.resume is not None
    follow_up = (
        f'a resume needs a follow-up: {command} -c "<follow-up>", or pipe one on stdin'
    )
    if args.prompt is None and not stdin_piped():
        if resuming:
            raise ValueError(follow_up)
        raise ValueError(
            f'{command} needs a task: {command} "<task>", or pipe one on stdin'
        )
    if not resuming:
        args.prompt = "-"
        return
    try:
        text = sys.stdin.buffer.read().decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"stdin: {exc}") from exc
    if not text.strip():
        raise ValueError(follow_up)
    args.prompt = text
    # Read here, so the run does not read stdin again for a follow-up that
    # is itself a lone -.
    args.prompt_from_stdin = True


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

    if args.prompt is None:
        # A resume with nothing typed yet: ask before the model runs again.
        first = asyncio.run(_follow_up(read_line))
        if first is None:
            return 0
        args.prompt = first
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
    stamp_session(args)(session)
    # Not when the resume sent the record instead (adopt_system).
    event = getattr(args, "instructions_event", None)
    if event is not None and getattr(args, "instructions_text", None) is not None:
        session.events.append(event)
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
