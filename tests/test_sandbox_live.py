"""Escape tests: what a confined bash command can and cannot reach.

These run the real OS confinement (sandbox-exec on macOS, bubblewrap on Linux)
and skip where it is not available. Set NARE_REQUIRE_SANDBOX=1 to turn a skip
into a failure, which CI does on the Linux job, so a runner that cannot confine
cannot pass silently.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from nare import sandbox
from nare.tools import bash_outcome

pytestmark = pytest.mark.sandbox


@pytest.fixture(autouse=True)
def _needs_a_sandbox() -> Iterator[None]:
    found = sandbox.availability()
    if not found.available:
        if os.environ.get("NARE_REQUIRE_SANDBOX"):
            pytest.fail(f"NARE_REQUIRE_SANDBOX is set but no sandbox: {found.reason}")
        pytest.skip(found.reason)
    yield


@pytest.fixture
def layout(tmp_path: Path) -> dict[str, Path]:
    """A root to work in, a credential folder and a file beside the root."""
    root = tmp_path / "root"
    root.mkdir()
    secret_dir = tmp_path / "creds"
    secret_dir.mkdir()
    secret = secret_dir / "key"
    secret.write_text("TOPSECRET-VALUE\n", encoding="utf-8")
    return {"root": root, "secret_dir": secret_dir, "secret": secret}


async def run(
    command: str,
    root: Path,
    *,
    denied: tuple[Path, ...] = (),
    network: bool = False,
) -> tuple[int, str]:
    confine = sandbox.Confinement(
        root=root.resolve(), network=network, denied=tuple(p.resolve() for p in denied)
    )
    outcome = await bash_outcome(command, 30, cwd=str(root), confine=confine)
    head, _, body = outcome.text.partition("\n")
    code = int(head.split()[1]) if head.startswith("exit ") else -1
    return code, body


async def test_a_write_inside_the_root_works(layout: dict[str, Path]) -> None:
    code, _ = await run("echo ok > inside.txt", layout["root"])
    assert code == 0
    assert (layout["root"] / "inside.txt").read_text() == "ok\n"


async def test_a_write_to_the_private_temp_dir_works(layout: dict[str, Path]) -> None:
    code, _ = await run('echo ok > "$TMPDIR/t.txt"', layout["root"])
    assert code == 0


async def test_a_write_outside_the_root_fails(layout: dict[str, Path]) -> None:
    outside = layout["root"].parent / "escaped.txt"
    code, _ = await run("echo x > ../escaped.txt", layout["root"])
    assert code != 0
    assert not outside.exists()


async def test_an_absolute_write_outside_the_root_fails(
    layout: dict[str, Path],
) -> None:
    target = layout["root"].parent / "abs.txt"
    code, _ = await run(f"echo x > '{target}'", layout["root"])
    assert code != 0
    assert not target.exists()


async def test_a_denied_credential_cannot_be_read(layout: dict[str, Path]) -> None:
    code, body = await run(
        f"cat '{layout['secret']}'", layout["root"], denied=(layout["secret_dir"],)
    )
    assert code != 0
    assert "TOPSECRET" not in body


async def test_a_denied_credential_cannot_be_written(layout: dict[str, Path]) -> None:
    code, _ = await run(
        f"echo x >> '{layout['secret']}'",
        layout["root"],
        denied=(layout["secret_dir"],),
    )
    assert code != 0
    assert layout["secret"].read_text() == "TOPSECRET-VALUE\n"


async def test_a_symlink_made_inside_the_root_cannot_read_out(
    layout: dict[str, Path],
) -> None:
    code, body = await run(
        f"ln -s '{layout['secret']}' link && cat link",
        layout["root"],
        denied=(layout["secret_dir"],),
    )
    assert code != 0
    assert "TOPSECRET" not in body


async def test_a_symlink_made_inside_the_root_cannot_write_out(
    layout: dict[str, Path],
) -> None:
    target = layout["root"].parent / "via-link.txt"
    code, _ = await run(f"ln -s '{target}' out && echo x > out", layout["root"])
    assert code != 0
    assert not target.exists()


async def test_a_hard_link_from_a_credential_cannot_be_made(
    layout: dict[str, Path],
) -> None:
    code, body = await run(
        f"ln '{layout['secret']}' copy && cat copy",
        layout["root"],
        denied=(layout["secret_dir"],),
    )
    assert code != 0
    assert "TOPSECRET" not in body
    assert not (layout["root"] / "copy").exists()


async def test_python_c_cannot_write_outside_the_root(layout: dict[str, Path]) -> None:
    target = layout["root"].parent / "py.txt"
    code, _ = await run(
        f"{sys.executable} -c \"open('{target}', 'w').write('x')\"", layout["root"]
    )
    assert code != 0
    assert not target.exists()


async def test_python_c_cannot_read_a_credential(layout: dict[str, Path]) -> None:
    code, body = await run(
        f"{sys.executable} -c \"print(open('{layout['secret']}').read())\"",
        layout["root"],
        denied=(layout["secret_dir"],),
    )
    assert code != 0
    assert "TOPSECRET" not in body


async def test_a_subshell_cannot_write_outside_the_root(
    layout: dict[str, Path],
) -> None:
    target = layout["root"].parent / "sub.txt"
    code, _ = await run(f"(echo x > '{target}')", layout["root"])
    assert code != 0
    assert not target.exists()


async def test_env_cannot_write_outside_the_root(layout: dict[str, Path]) -> None:
    target = layout["root"].parent / "env.txt"
    code, _ = await run(f"env sh -c \"echo x > '{target}'\"", layout["root"])
    assert code != 0
    assert not target.exists()


async def test_the_network_is_off_by_default(layout: dict[str, Path]) -> None:
    with _listener() as port:
        code, body = await run(_connect_snippet(port), layout["root"])
    assert code != 0, body
    assert not _connected(body)


async def test_the_network_is_reachable_only_when_allowed(
    layout: dict[str, Path],
) -> None:
    with _listener() as port:
        code, body = await run(_connect_snippet(port), layout["root"], network=True)
    assert code == 0, body
    assert _connected(body)


@pytest.mark.skipif(sys.platform != "linux", reason="procfs is Linux")
async def test_linux_cannot_see_host_processes(layout: dict[str, Path]) -> None:
    host_pid = os.getpid()
    code, body = await run(
        f"cat /proc/{host_pid}/environ > /dev/null 2>&1; echo env=$?; "
        f"ls /proc | grep -x {host_pid} || echo hidden",
        layout["root"],
    )
    assert "hidden" in body
    assert "env=0" not in body


@pytest.mark.skipif(sys.platform != "linux", reason="procfs is Linux")
async def test_linux_proc_self_root_sees_the_same_masks(
    layout: dict[str, Path],
) -> None:
    # /proc/self/root is the sandbox's own root, so the credential mask holds
    # through it too. Without the mask the secret is readable, which is why the
    # folder is denied here.
    code, body = await run(
        f"readlink /proc/self/root; cat /proc/self/root{layout['secret']}",
        layout["root"],
        denied=(layout["secret_dir"],),
    )
    assert "TOPSECRET" not in body
    assert code != 0


@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt is macOS")
async def test_a_keychain_secret_cannot_be_read(layout: dict[str, Path]) -> None:
    service = f"nare-sandbox-test-{os.getpid()}"
    secret = "KEYCHAIN-SECRET-VALUE"
    add = subprocess.run(
        ["security", "add-generic-password", "-s", service, "-a", "nare", "-w", secret],
        capture_output=True,
        check=False,
    )
    if add.returncode != 0:
        pytest.skip("could not add a test keychain item")
    try:
        code, body = await run(
            f"security find-generic-password -s {service} -w", layout["root"]
        )
        assert secret not in body
        assert code != 0
    finally:
        subprocess.run(
            ["security", "delete-generic-password", "-s", service],
            capture_output=True,
            check=False,
        )


@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt is macOS")
def test_a_profile_with_awkward_paths_compiles(tmp_path: Path) -> None:
    awkward = tmp_path / 'we"ird\\dir'
    awkward.mkdir()
    profile = sandbox.seatbelt_profile(awkward.resolve(), tmp_path, (), network=False)
    result = subprocess.run(
        ["sandbox-exec", "-p", profile, "/usr/bin/true"],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


class _Listener:
    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port: int = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._accept, daemon=True)

    def _accept(self) -> None:
        try:
            while True:
                conn, _ = self.sock.accept()
                conn.close()
        except OSError:
            return

    def __enter__(self) -> int:
        self.thread.start()
        return self.port

    def __exit__(self, *exc: object) -> None:
        self.sock.close()


def _listener() -> _Listener:
    return _Listener()


def _connect_snippet(port: int) -> str:
    return (
        f'{sys.executable} -c "import socket; '
        f"socket.create_connection(('127.0.0.1', {port}), timeout=3); "
        "print('CONN' 'ECTED')\""
    )


def _connected(body: str) -> bool:
    # A traceback echoes the source line, so match the line the snippet prints.
    return any(line.strip() == "CONNECTED" for line in body.splitlines())


async def test_a_host_secret_in_the_environment_is_not_visible(
    layout: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_TOKEN", "ghp_probe_visible_to_host")
    code, body = await run('echo "gh=${GH_TOKEN:-none}"', layout["root"])
    assert code == 0
    assert "ghp_probe_visible_to_host" not in body
    assert "gh=none" in body


async def test_a_unix_socket_outside_the_root_cannot_be_connected(
    layout: dict[str, Path],
) -> None:
    # The socket is outside the root and in a folder the host shares. Reaching
    # it is how an agent socket or a container runtime socket would be used.
    import socket as socket_module

    # A short path: macOS limits a socket path to about 104 bytes.
    short_dir = Path(tempfile.mkdtemp(prefix="nsk", dir="/tmp"))
    sock_path = short_dir / "a.sock"
    server = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
    server.bind(str(sock_path))
    server.listen(1)
    try:
        snippet = (
            f'{sys.executable} -c "import socket as s; '
            f"c=s.socket(s.AF_UNIX, s.SOCK_STREAM); c.connect('{sock_path}'); "
            "print('CONN' 'ECTED')\""
        )
        code, body = await run(snippet, layout["root"])
        assert code != 0, body
        assert not _connected(body)
    finally:
        server.close()
        shutil.rmtree(short_dir, ignore_errors=True)


@pytest.mark.skipif(sys.platform != "darwin", reason="the system keychain is macOS")
async def test_the_system_keychain_cannot_be_read(layout: dict[str, Path]) -> None:
    code, body = await run(
        "head -c 4 /Library/Keychains/System.keychain | od -An -c",
        layout["root"],
        denied=sandbox.default_denied(),
    )
    assert code != 0, body
