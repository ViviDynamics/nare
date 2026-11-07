"""OS confinement for the bash tool: optional, off by default.

With `Policy(bash_sandbox=True)` (the `--bash-sandbox` flag), each bash command
runs under the platform's own sandbox: `sandbox-exec` (Seatbelt) on macOS and
`bwrap` (bubblewrap) on Linux. Writes are allowed only inside the root and a
private scratch directory made for the call. Reads are allowed except for
credential locations. The network is off unless `bash_network` allows it.

Nothing here falls back. When confinement was asked for and cannot run, the
call is refused and no shell starts, so a command never runs unconfined because
the sandbox was missing or failed. The limits are in docs/contract.md.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

# Recorded in the session's policy, so a reader can tell which rules were in
# force. Changing what the profile allows or denies means a new name.
PROFILE = "confined-1"
SUPPORTED = "macOS (sandbox-exec) and Linux (bubblewrap)"

# Credential locations, relative to the home directory. A folder is masked as a
# whole, and a file is masked by its own path. NARE_HOME is added by the caller.
_CREDENTIAL_DIRS = (
    ".ssh",
    ".aws",
    ".gnupg",
    ".kube",
    ".docker",
    ".azure",
    ".password-store",
    ".config/gh",
    ".config/gcloud",
)
_CREDENTIAL_FILES = (
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".s3cfg",
    ".vault-token",
    ".git-credentials",
    ".config/git/credentials",
)
_MACOS_CREDENTIALS = ("Library/Keychains",)

# The mach services that answer keychain and security-policy requests. Denied by
# name, since a path rule cannot stop a secret that a service hands over.
KEYCHAIN_SERVICES = (
    "com.apple.SecurityServer",
    "com.apple.secd",
    "com.apple.security.agent",
    "com.apple.security.syspolicy",
)


class SandboxUnavailable(RuntimeError):
    """Confinement was asked for, and this host cannot give it."""


@dataclass(frozen=True)
class Availability:
    """Whether this host can confine bash, and how.

    `mechanism` is None when it cannot, and `reason` says why in one line.
    """

    mechanism: str | None
    reason: str
    binary: str = ""

    @property
    def available(self) -> bool:
        return self.mechanism is not None


Runner = Callable[..., "subprocess.CompletedProcess[bytes]"]

# A bwrap probe with the same isolation the real call uses, so a host where
# bwrap starts but cannot isolate is refused here, not at the first call.
_BWRAP_PROBE = (
    "--ro-bind",
    "/",
    "/",
    "--dev",
    "/dev",
    "--proc",
    "/proc",
    "--unshare-pid",
    "--die-with-parent",
    "--",
    "/bin/true",
)


def _availability_for(
    system: str, *, which: Callable[[str], str | None], run: Runner
) -> Availability:
    """The availability of confinement on `system`, probed with `run`.

    Taken as parameters so a test can ask about another platform, or a broken
    tool, without changing the machine it runs on.
    """
    if system == "darwin":
        binary = which("sandbox-exec")
        if binary is None:
            return Availability(None, "sandbox-exec is not on this Mac")
        probe = [binary, "-p", "(version 1)(allow default)", "/usr/bin/true"]
        mechanism = "seatbelt"
    elif system == "linux":
        binary = which("bwrap")
        if binary is None:
            return Availability(
                None,
                "bubblewrap (bwrap) is not installed; install the bubblewrap package",
            )
        probe = [binary, *_BWRAP_PROBE]
        mechanism = "bubblewrap"
    else:
        return Availability(None, f"no OS sandbox for {system}; supported: {SUPPORTED}")
    try:
        done = run(probe, capture_output=True, check=False, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        return Availability(None, f"{mechanism} could not be run: {exc}")
    if done.returncode != 0:
        lines = done.stderr.decode(errors="replace").strip().splitlines()
        detail = lines[-1] if lines else f"exit {done.returncode}"
        return Availability(None, f"{mechanism} would not start here: {detail}")
    return Availability(mechanism, "", binary=binary)


@lru_cache(maxsize=1)
def availability() -> Availability:
    """Whether this host can confine bash. Probed once per process."""
    return _availability_for(sys.platform, which=shutil.which, run=subprocess.run)


def fold(paths: Iterable[Path]) -> tuple[Path, ...]:
    """The paths in order, without duplicates and without any that sit under
    another one, since the outer mask already covers the inner.
    """
    kept: list[Path] = []
    for path in dict.fromkeys(paths):
        if any(path == other or path.is_relative_to(other) for other in kept):
            continue
        kept = [other for other in kept if not other.is_relative_to(path)]
        kept.append(path)
    return tuple(kept)


def credential_paths(
    home: Path, nare_home: Path | None, system: str
) -> tuple[Path, ...]:
    """The locations a confined command may not read or write.

    `nare_home` is the folder nare keeps its MCP tokens and saved sessions in.
    It is denied whole, which covers the token files.
    """
    names = [*_CREDENTIAL_DIRS, *_CREDENTIAL_FILES]
    if system == "darwin":
        names += list(_MACOS_CREDENTIALS)
    paths = [home / name for name in names]
    if nare_home is not None:
        paths.append(nare_home)
    return fold(paths)


def default_denied() -> tuple[Path, ...]:
    """The credential locations on this machine, resolved for the sandbox."""
    from nare.session import nare_home

    try:
        home = Path.home()
    except RuntimeError:
        home = Path("/nonexistent-home")  # no home: only NARE_HOME below
    found = nare_home()
    paths = credential_paths(home, found, sys.platform)
    return tuple(p.resolve() for p in paths)


def _quote(path: Path) -> str:
    """A path as a Seatbelt string literal."""
    text = str(path)
    if any(ord(ch) < 32 or ch == "\x7f" for ch in text):
        raise ValueError(f"a sandbox path may not hold a control character: {text!r}")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def seatbelt_profile(
    root: Path,
    scratch: Path,
    denied: Sequence[Path],
    *,
    network: bool,
) -> str:
    """The Seatbelt profile for one bash call.

    Deny by default, then open what a shell needs. Seatbelt uses the last rule
    that matches, so the credential denials come after the broad read allow and
    the write allows. Paths are used as given: the caller resolves them.
    """
    keychain = " ".join(f'(global-name "{name}")' for name in KEYCHAIN_SERVICES)
    lines = [
        "(version 1)",
        "(deny default)",
        "(allow process-fork)",
        "(allow process-exec)",
        "(allow signal (target same-sandbox))",
        "(allow sysctl-read)",
        "(allow mach-lookup)",
        f"(deny mach-lookup {keychain})",
        "(allow ipc-posix-shm)",
        "(allow file-read*)",
        "(allow file-ioctl)",
        '(allow file-write-data (literal "/dev/null") (literal "/dev/tty")'
        ' (literal "/dev/stdout") (literal "/dev/stderr")'
        ' (literal "/dev/fd/1") (literal "/dev/fd/2"))',
        f"(allow file-write* (subpath {_quote(root)}))",
        f"(allow file-write* (subpath {_quote(scratch)}))",
    ]
    for path in denied:
        lines.append(f"(deny file-read* (subpath {_quote(path)}))")
        lines.append(f"(deny file-write* (subpath {_quote(path)}))")
    if network:
        lines.append("(allow network*)")
    return "\n".join(lines) + "\n"


def bwrap_argv(
    root: Path,
    scratch: Path,
    denied: Sequence[Path],
    *,
    network: bool,
    command: Sequence[str],
    bwrap: str = "bwrap",
) -> list[str]:
    """The bubblewrap command line for one bash call.

    The host is mounted read-only, then the root and the scratch directory are
    bound writable on top, and each credential is masked after that: a folder
    by an empty read-only tmpfs, a file by /dev/null. /tmp is not given a
    tmpfs, so the host's /tmp stays read-only, as on macOS: the root and the
    scratch directory are the only writable places. The PID namespace hides every host
    process, so /proc names nothing outside. `--new-session` is not passed, since
    the spawn already starts a session and a second setsid would leave the
    process group a timeout kills.
    """
    argv = [
        bwrap,
        "--die-with-parent",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
    ]
    if not network:
        argv.append("--unshare-net")
    argv += [
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--bind",
        str(scratch),
        str(scratch),
        "--bind",
        str(root),
        str(root),
    ]
    for path in denied:
        if path.is_dir():
            # Empty and read-only: a command sees no files, and writes fail.
            argv += ["--tmpfs", str(path), "--remount-ro", str(path)]
        elif path.exists():
            argv += ["--ro-bind", "/dev/null", str(path)]
    argv += ["--chdir", str(root), "--", *command]
    return argv


@dataclass(frozen=True)
class Confinement:
    """What one bash call may reach: the root, the network, and the denied paths.

    `wrap` builds the argv for the platform, or raises SandboxUnavailable. It
    never returns the command unwrapped.
    """

    root: Path
    network: bool = False
    denied: tuple[Path, ...] = ()

    def wrap(
        self, command: Sequence[str], scratch: Path, found: Availability
    ) -> list[str]:
        if found.mechanism == "seatbelt":
            binary = found.binary or "sandbox-exec"
            profile = seatbelt_profile(
                self.root, scratch, self.denied, network=self.network
            )
            return [binary, "-p", profile, *command]
        if found.mechanism == "bubblewrap":
            return bwrap_argv(
                self.root,
                scratch,
                self.denied,
                network=self.network,
                command=command,
                bwrap=found.binary or "bwrap",
            )
        raise SandboxUnavailable(found.reason or "no OS sandbox")


def record(confine: Confinement, mechanism: str | None) -> dict[str, Any]:
    """The profile in force, as the session's policy records it."""
    return {
        "mechanism": mechanism,
        "profile": PROFILE,
        "network": confine.network,
        "writable": [str(confine.root)],
        "private_temp": True,
        "denied": [str(path) for path in confine.denied],
    }
