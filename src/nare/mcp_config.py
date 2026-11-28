"""MCP servers a person configures once: `nare mcp add|list|get|remove|trust`.

Two scopes, both in Claude Code's format, `{"mcpServers": {name: entry}}`:

- user: NARE_HOME/mcp.json, mode 0600, for every project;
- project: .mcp.json in the working directory, the file a project commits, so
  one that already carries a Claude Code .mcp.json works unchanged. Runs,
  the trust question, `nare mcp trust` and `nare mcp list` all read it there,
  never under --root (#353).

The person-facing forms (nare, nare "<task>", nare -p, nare exec) load both;
project servers start only once the person trusted that exact .mcp.json,
since a cloned repository could otherwise start any program; the question
shows each server's command line or URL. A project's ${VAR} may not name a
provider credential nare withholds from the programs it starts
(CREDENTIAL_ENV). `nare run` loads
only what --mcp-config names, as it always has. No MCP SDK import here: the
servers are only parsed, and connecting stays in nare.mcp.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nare.mcp import Server, parse_servers
from nare.session import nare_home, private_folder
from nare.transport import CREDENTIAL_ENV

PROJECT_FILE = ".mcp.json"
SCOPES = ("user", "project")
TRANSPORTS = ("stdio", "http", "streamable-http", "sse")
# ${VAR} and ${VAR:-default}, as Claude Code expands them in .mcp.json.
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def user_path() -> Path | None:
    home = nare_home()
    return None if home is None else home / "mcp.json"


def project_path(root: str) -> Path:
    return Path(root) / PROJECT_FILE


def project_file() -> Path:
    """The project's .mcp.json: the working directory's, for runs, the trust
    question, the skip line, trust and list alike (#353)."""
    return project_path(os.getcwd())


def _trust_path() -> Path | None:
    home = nare_home()
    return None if home is None else home / "trusted-projects.json"


def is_mcp_servers(raw: Any) -> bool:
    """Claude Code's format: one mcpServers object of entries. nare's own
    format is keyed by alias at the top, so a server named mcpServers there
    holds strings, never entries."""
    servers = raw.get("mcpServers") if isinstance(raw, dict) else None
    return isinstance(servers, dict) and all(
        isinstance(entry, dict) for entry in servers.values()
    )


def _withheld_message(where: str, name: str) -> str:
    return (
        f"{where}: ${{{name}}} is a provider credential nare keeps from the "
        "programs it starts, so a project's servers may not use it; to pass it "
        "to a server on purpose, add one under another name to your own config "
        "(nare mcp add)"
    )


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings(item)]
    return []


def variables(entry: Any) -> list[str]:
    """The ${VAR} names an entry reads, in order, each once."""
    names = [m.group(1) for text in _strings(entry) for m in _VAR.finditer(text)]
    return list(dict.fromkeys(names))


def withheld_in(servers: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Each project server that names a withheld credential, with the first
    such ${VAR}: it is skipped, never started, while the rest still are."""
    found: dict[str, str] = {}
    for name, entry in servers.items():
        for variable in variables(entry):
            if variable.upper() in CREDENTIAL_ENV:
                found[name] = variable
                break
    return found


def leave_out_withheld(servers: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """`servers` without those that name a withheld credential, each named on
    stderr."""
    refused = withheld_in(servers)
    for name, variable in refused.items():
        where = f"skipped MCP server {_printable(name)} in {PROJECT_FILE}"
        print(f"nare: {_withheld_message(where, variable)}", file=sys.stderr)
    return {name: entry for name, entry in servers.items() if name not in refused}


def check_project(servers: dict[str, dict[str, Any]]) -> None:
    """Each project entry as nare will read it, before the person is asked to
    trust the file, so a yes is never recorded for one that could not load."""
    for name, entry in servers.items():
        if not isinstance(entry, dict):
            raise ValueError(f"MCP server {_printable(name)}: not an object")
        _entry(name, entry, expand=False, project=True)


def _expand(value: Any, where: str, withheld: frozenset[str]) -> Any:
    if isinstance(value, str):

        def one(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            if name.upper() in withheld:  # Windows reads env in any case
                raise ValueError(_withheld_message(where, name))
            found = os.environ.get(name)
            if found is not None:
                return found
            if default is not None:
                return default
            raise ValueError(f"{where}: ${{{name}}} is not set and has no default")

        return _VAR.sub(one, value)
    if isinstance(value, list):
        return [_expand(item, where, withheld) for item in value]
    if isinstance(value, dict):
        return {key: _expand(item, where, withheld) for key, item in value.items()}
    return value


def _entry(
    name: str, entry: dict[str, Any], *, expand: bool, project: bool
) -> dict[str, Any]:
    """One mcpServers entry in nare's terms: `type` checked and dropped, and
    ${VAR} expanded when the server is about to start."""
    entry = dict(entry)
    name = _printable(name)
    kind = entry.pop("type", None)
    if kind == "sse":
        raise ValueError(
            f"MCP server {name}: the sse transport is not supported; nare speaks "
            "Streamable HTTP (type http)"
        )
    if kind not in (None, "stdio", "http", "streamable-http"):
        raise ValueError(f"MCP server {name}: unknown type {kind!r}")
    if kind == "stdio" and "command" not in entry:
        raise ValueError(f"MCP server {name}: type stdio needs a command")
    if kind in ("http", "streamable-http") and "url" not in entry:
        raise ValueError(f"MCP server {name}: type {kind} needs a url")
    if not expand:
        return entry
    where = f"MCP server {name}" + (f" in {PROJECT_FILE}" if project else "")
    expanded: dict[str, Any] = _expand(
        entry, where, CREDENTIAL_ENV if project else frozenset()
    )
    return expanded


def to_nare(raw: Any, *, expand: bool = True, project: bool = False) -> Any:
    """A config in either format, as nare's own. nare's format passes through
    untouched, with no expansion, so it means what it always meant. A
    project's entries (`project`) may not expand a CREDENTIAL_ENV name."""
    if not is_mcp_servers(raw):
        return raw
    return {
        name: _entry(name, entry, expand=expand, project=project)
        for name, entry in raw["mcpServers"].items()
    }


def servers_from(raw: Any) -> dict[str, Server]:
    return parse_servers(to_nare(raw))


def _read(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    return _loads(_bytes(path), path)


def _bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ValueError(f"{path}: {exc}") from exc


def _loads(data: bytes, path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(data.decode("utf-8"))
    except ValueError as exc:
        raise ValueError(f"{path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must hold a JSON object")
    return loaded


def entries(path: Path | None) -> dict[str, dict[str, Any]]:
    """The servers a file names, in mcpServers form, whichever format it has."""
    return _servers_in(_read(path), path)


def _servers_in(raw: dict[str, Any], path: Path | None) -> dict[str, dict[str, Any]]:
    if not raw:
        return {}
    if is_mcp_servers(raw):
        servers: dict[str, dict[str, Any]] = raw["mcpServers"]
        return servers
    if all(isinstance(entry, dict) for entry in raw.values()):
        return raw
    raise ValueError(f"{path} is neither nare's MCP format nor mcpServers")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _project_key(root: str) -> str:
    return os.path.realpath(root)


def trusted(root: str, data: bytes | None = None) -> bool:
    """Whether the person trusted this project's .mcp.json, as it is now (or
    as `data`, the bytes already read): a file changed since, a new server in
    it say, is asked about again."""
    path = project_path(root)
    if data is None:
        if not path.is_file():
            return False
        data = path.read_bytes()
    record = _read(_trust_path()).get(_project_key(root))
    return isinstance(record, dict) and record.get("mcp_sha256") == _digest(data)


def trust(root: str, data: bytes | None = None) -> None:
    """Record the project's .mcp.json as trusted: `data`, the bytes the
    person was shown, or the file as it is now."""
    if data is None:
        data = project_path(root).read_bytes()
    records = _read(_trust_path())
    records[_project_key(root)] = {"mcp_sha256": _digest(data)}
    _write(_trust_path(), records, private=True)


def untrust(root: str) -> bool:
    records = _read(_trust_path())
    found = records.pop(_project_key(root), None) is not None
    if found:
        _write(_trust_path(), records, private=True)
    return found


def _write(path: Path | None, value: dict[str, Any], *, private: bool) -> None:
    if path is None:
        raise ValueError("no home directory to keep MCP servers in: set NARE_HOME")
    if private:
        private_folder(path.parent)
    if private:
        mode = 0o600
    elif path.exists():
        mode = path.stat().st_mode & 0o777
    else:
        mode = 0o644  # a project's .mcp.json is committed and read by others
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(value, handle, indent=2)
        handle.write("\n")
    try:
        os.chmod(handle.name, mode)
        os.replace(handle.name, path)
    except OSError:
        os.unlink(handle.name)
        raise


def defaults(
    args: argparse.Namespace,
    ask: Callable[[dict[str, dict[str, Any]]], bool] | None,
) -> tuple[dict[str, Any], list[str]]:
    """The servers a person-facing run loads besides --mcp-config: the user's,
    then the project's, which win on a shared name, in nare's format. An
    untrusted project's servers start only if `ask` says yes; without `ask`
    they are skipped and named (skipped_line). --strict-mcp-config loads none
    of them. The project's file is the working directory's, not --root's, so
    a run reads the file nare mcp trust and list read."""
    if getattr(args, "strict_mcp_config", False):
        return {}, []
    path = project_file()
    root = str(path.parent)
    given = getattr(args, "root", None)
    if given is not None and project_path(given).is_file():
        theirs = project_path(given)
        if os.path.realpath(theirs) != os.path.realpath(path):
            # Runs read --root's file before #353: say why this one does not.
            print(
                f"nare: {_printable(str(theirs))} was not read: {PROJECT_FILE} is "
                f"read from the working directory, {_printable(root)}; run nare "
                "in that folder to use it",
                file=sys.stderr,
            )
    user = entries(user_path())
    # Read once: what is shown, trusted and started are the same bytes.
    data = _bytes(path) if path.is_file() else None
    project = {} if data is None else _servers_in(_loads(data, path), path)
    skipped: list[str] = []
    if project and ask is not None:
        check_project(project)
    project = leave_out_withheld(project)
    if project and data is not None and not trusted(root, data):
        if ask is not None and ask(project):
            trust(root, data)
        else:
            skipped, project = sorted(project), {}
    # The project's entry wins on a shared name, so the user's is not expanded.
    user = {name: entry for name, entry in user.items() if name not in project}
    merged = {
        **to_nare({"mcpServers": user}),
        **to_nare({"mcpServers": project}, project=True),
    }
    return merged, skipped


def target(entry: dict[str, Any]) -> str:
    """A server as a person reads it: its URL, or its command line, unexpanded."""
    if "url" in entry:
        return str(entry["url"])
    args = entry.get("args", [])
    words = [str(arg) for arg in args] if isinstance(args, list) else [repr(args)]
    return shlex.join([str(entry.get("command", "")), *words])


def _printable(text: str) -> str:
    """`text` with each control character escaped, so a file cannot redraw or
    hide the question that asks about it."""
    return "".join(
        char if char.isprintable() else char.encode("unicode_escape").decode()
        for char in text
    )


def terminal_ask(servers: dict[str, dict[str, Any]]) -> bool:
    """The trust question, at a terminal, before anything takes the screen:
    each server with its command line, or URL, as the file has it, the names
    of the env and headers it sets, and each ${VAR} it reads."""
    lines = []
    for name in sorted(servers):
        entry = servers[name]
        notes = []
        env = entry.get("env")
        if isinstance(env, dict) and env:
            # Shown whole, unexpanded: PATH or NODE_OPTIONS can change what runs.
            notes.append("env " + ", ".join(f"{k}={v}" for k, v in env.items()))
        headers = entry.get("headers")
        if isinstance(headers, dict) and headers:
            notes.append("headers " + ", ".join(str(k) for k in headers))
        read = variables(entry)
        if read:
            notes.append("reads " + ", ".join(f"${{{v}}}" for v in read))
        line = f"  {name}: {target(entry)}"
        if notes:
            line += f" ({'; '.join(notes)})"
        lines.append(_printable(line))
    sys.stderr.write(
        f"nare: {_printable(str(project_file()))} starts MCP servers, which run "
        "programs on this machine or connect to these addresses:\n"
        + "\n".join(lines)
        + "\nTrust it and start them? [y/N] "
    )
    sys.stderr.flush()
    return sys.stdin.readline().strip().lower() in ("y", "yes")


def skipped_line(names: list[str]) -> str:
    """Why defaults skipped `names`, naming the file and the directory to
    trust it from: the working directory, which defaults read."""
    path = project_file()
    here = str(path.parent)
    return (
        f"nare: skipped the MCP servers in {_printable(str(path))} "
        f"({_printable(', '.join(names))}): it is not trusted. nare mcp trust in "
        f"{_printable(here)} trusts it, or run nare there once at a terminal"
    )


def _pairs(values: list[str] | None, flag: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for value in values or []:
        key, sep, item = value.partition("=")
        if not sep or not key:
            raise ValueError(f"{flag} takes KEY=VALUE, not {value!r}")
        pairs[key] = item
    return pairs


def _scope_path(scope: str) -> Path | None:
    return user_path() if scope == "user" else project_path(os.getcwd())


def add_main(argv: list[str]) -> int:
    """nare mcp add [options] <name> -- <command...>, or <name> --url <url>."""
    from nare.cli import NareParser

    before, after = (
        (argv[: argv.index("--")], argv[argv.index("--") + 1 :])
        if "--" in argv
        else (argv, None)
    )
    parser = NareParser(
        prog="nare mcp add",
        usage="nare mcp add [options] <name> -- <command> [args...]\n"
        "       nare mcp add [options] <name> --url <url>",
        description="Add an MCP server to the user's or this project's config.",
    )
    parser.add_argument("-s", "--scope", choices=SCOPES, default="user")
    parser.add_argument("-e", "--env", action="append", metavar="KEY=VALUE")
    parser.add_argument("-H", "--header", action="append", metavar="KEY=VALUE")
    parser.add_argument("-t", "--transport", choices=TRANSPORTS)
    parser.add_argument("--url")
    parser.add_argument("--timeout", type=float, metavar="SECONDS")
    parser.add_argument("name")
    parser.add_argument("command", nargs="*")
    args = parser.parse_args(before)
    command = [*args.command, *(after or [])]
    try:
        if args.transport == "sse":
            raise ValueError("the sse transport is not supported; use http")
        remote = args.url is not None or args.transport in ("http", "streamable-http")
        if remote and args.url is None and len(command) == 1:
            args.url, command = command[0], []  # Claude Code's positional URL
        if remote and command:
            raise ValueError("give a --url or a command, not both")
        if not remote and not command:
            raise ValueError("give the server's command after --, or --url")
        entry: dict[str, Any]
        if remote:
            entry = {"type": "http", "url": args.url}
            if args.header:
                entry["headers"] = _pairs(args.header, "--header")
            if args.env:
                raise ValueError("--env applies to a command, not a --url server")
        else:
            entry = {"command": command[0], "args": command[1:]}
            if args.env:
                entry["env"] = _pairs(args.env, "--env")
            if args.header:
                raise ValueError("--header applies to a --url server")
        if args.timeout is not None:
            entry["timeout"] = args.timeout
        # Checked as nare will read it, with ${VAR} left for the run to expand.
        parse_servers(to_nare({"mcpServers": {args.name: entry}}, expand=False))
        path = _scope_path(args.scope)
        raw = _read(path)
        if raw and not is_mcp_servers(raw):
            raise ValueError(f"{path} is not in the mcpServers format; edit it by hand")
        servers = dict(raw.get("mcpServers", {}))
        if args.name in servers:
            raise ValueError(
                f"{args.name} is already in {path}; nare mcp remove {args.name} first"
            )
        servers[args.name] = entry
        _write(path, {**raw, "mcpServers": servers}, private=args.scope == "user")
    except ValueError as exc:
        print(f"nare: {exc}", file=sys.stderr)
        return 2
    print(f"nare: added {args.name} to {path}", file=sys.stderr)
    return 0


def _rows() -> list[dict[str, Any]]:
    here = os.getcwd()
    rows: list[dict[str, Any]] = []
    project = project_path(here)
    for scope, path in (("user", user_path()), ("project", project)):
        if path is None or not path.is_file():
            continue
        for name, entry in entries(path).items():
            rows.append(
                {
                    "name": name,
                    "scope": scope,
                    "path": str(path),
                    "transport": "http" if "url" in entry else "stdio",
                    "target": entry.get("url")
                    or " ".join(
                        [str(entry.get("command", ""))] + list(entry.get("args", []))
                    ),
                    **({"trusted": trusted(here)} if scope == "project" else {}),
                }
            )
    return rows


def list_main(as_json: bool) -> int:
    try:
        rows = _rows()
    except ValueError as exc:
        print(f"nare: {exc}", file=sys.stderr)
        return 2
    if as_json:
        print(json.dumps(rows))
        return 0
    if not rows:
        print("no MCP servers configured: nare mcp add <name> -- <command>")
        return 0
    for row in rows:
        note = "" if row.get("trusted", True) else "  (not trusted: nare mcp trust)"
        print(f"{row['name']:16} {row['scope']:8} {row['target']}{note}")
    return 0


def _find(name: str, scope: str | None) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    for each in SCOPES if scope is None else (scope,):
        path = _scope_path(each)
        if path is not None and path.is_file() and name in entries(path):
            found.append((each, path))
    return found


def get_main(name: str) -> int:
    try:
        found = _find(name, None)
        if not found:
            raise ValueError(f"no MCP server {name} in the user or project config")
        scope, path = found[-1]  # the project's wins, as at run time
        entry = dict(entries(path)[name])
    except ValueError as exc:
        print(f"nare: {exc}", file=sys.stderr)
        return 2
    # Values in env and headers are often secrets: named, never printed.
    for key in ("env", "headers"):
        if isinstance(entry.get(key), dict):
            entry[key] = {k: "<set>" for k in entry[key]}
    print(
        json.dumps({"name": name, "scope": scope, "path": str(path), **entry}, indent=2)
    )
    return 0


def remove_main(name: str, scope: str | None) -> int:
    try:
        found = _find(name, scope)
        if not found:
            raise ValueError(f"no MCP server {name} to remove")
        if len(found) > 1:
            raise ValueError(f"{name} is in both scopes: give --scope user or project")
        [(_, path)] = found
        raw = _read(path)
        if not is_mcp_servers(raw):
            raise ValueError(f"{path} is not in the mcpServers format; edit it by hand")
        servers = dict(raw["mcpServers"])
        del servers[name]
        _write(path, {**raw, "mcpServers": servers}, private=path == user_path())
    except ValueError as exc:
        print(f"nare: {exc}", file=sys.stderr)
        return 2
    print(f"nare: removed {name} from {path}", file=sys.stderr)
    return 0


def trust_main(revoke: bool) -> int:
    here = os.getcwd()
    if revoke:
        untrust(here)
        print(f"nare: {here} is no longer trusted", file=sys.stderr)
        return 0
    if not project_path(here).is_file():
        print(f"nare: there is no {PROJECT_FILE} in {here} to trust", file=sys.stderr)
        return 2
    path = project_path(here)
    try:
        data = _bytes(path)
        servers = _servers_in(_loads(data, path), path)
        check_project(servers)
        leave_out_withheld(servers)
        trust(here, data)
    except ValueError as exc:
        print(f"nare: {exc}", file=sys.stderr)
        return 2
    print(f"nare: trusted {project_path(here)} as it is now", file=sys.stderr)
    return 0


def configured() -> dict[str, Server]:
    """The user's and this directory's trusted project servers, for nare mcp
    login and logout without --mcp-config."""
    namespace = argparse.Namespace(strict_mcp_config=False)
    merged, _skipped = defaults(namespace, None)
    return parse_servers(merged)
