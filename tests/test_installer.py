"""What the rendered install.sh does, offline, with a stub uv.

The stub records what the installer asked of uv and lays down a fake `nare`
and a fake tool interpreter, so these tests cover the script's own decisions:
the digest check, the uv requirement, the tui extra, the version and TUI
checks, and a repeat run. CI's installer smoke job does the same against a
real build and the real uv.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / ".github" / "scripts"
VERSION = "2026.11.9"
WHEEL = f"nare-{VERSION}-py3-none-any.whl"

STUB_UV = """#!/bin/sh
set -eu
# The stub's own tools, not the installer's restricted PATH.
PATH=/usr/bin:/bin
printf '%s\\n' "$*" >> "$STUB_LOG"
case "$*" in
  "tool dir --bin") printf '%s\\n' "$UV_TOOL_BIN_DIR" ;;
  "tool dir") printf '%s\\n' "$UV_TOOL_DIR" ;;
  "tool install "*)
    mkdir -p "$UV_TOOL_BIN_DIR" "$UV_TOOL_DIR/nare/bin"
    cat > "$UV_TOOL_BIN_DIR/nare" <<'NARE'
#!/bin/sh
echo "$STUB_REPORTS"
NARE
    cat > "$UV_TOOL_DIR/nare/bin/python" <<'PYTHON'
#!/bin/sh
echo "python $*" >> "$STUB_LOG"
exit "$STUB_TUI_EXIT"
PYTHON
    chmod +x "$UV_TOOL_BIN_DIR/nare" "$UV_TOOL_DIR/nare/bin/python"
    ;;
  *) echo "stub uv: unexpected $*" >&2; exit 64 ;;
esac
"""

# What the installer may call besides uv. Everything else is a shell builtin.
TOOLS = ("curl", "mktemp", "rm", "sha256sum", "shasum")


@dataclass
class Release:
    home: Path
    assets: Path
    tools: Path
    log: Path
    installer: Path


def _render(digest: str) -> str:
    spec = importlib.util.spec_from_file_location(
        "release_assets", SCRIPTS / "release_assets.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    template = (SCRIPTS / "install.sh.in").read_text(encoding="utf-8")
    rendered: str = module.render_installer(
        template, version=VERSION, wheel_sha256=digest
    )
    return rendered


@pytest.fixture
def release(tmp_path: Path) -> Release:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / WHEEL).write_bytes(b"not really a wheel\n")
    digest = hashlib.sha256((assets / WHEEL).read_bytes()).hexdigest()
    installer = assets / "install.sh"
    installer.write_text(_render(digest), encoding="utf-8")

    # A PATH with the system tools the installer needs and nothing else, so
    # a uv on this machine cannot leak in.
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in TOOLS:
        found = shutil.which(name)
        if found:
            (tools / name).symlink_to(found)
    home = tmp_path / "home"
    home.mkdir()
    return Release(home, assets, tools, tmp_path / "uv.log", installer)


def _with_uv(release: Release) -> None:
    uv = release.tools / "uv"
    uv.write_text(STUB_UV, encoding="utf-8")
    uv.chmod(0o755)


def _run(
    release: Release,
    *,
    stdin: bool = False,
    reports: str = VERSION,
    tui_exit: int = 0,
    release_url: str | None = None,
    extra_path: str = "",
) -> subprocess.CompletedProcess[str]:
    env = {
        "HOME": str(release.home),
        "PATH": f"{extra_path}{release.tools}",
        "NARE_RELEASE_URL": release_url or f"file://{release.assets}",
        "UV_TOOL_DIR": str(release.home / "tools"),
        "UV_TOOL_BIN_DIR": str(release.home / "bin"),
        "STUB_LOG": str(release.log),
        "STUB_REPORTS": reports,
        "STUB_TUI_EXIT": str(tui_exit),
    }
    if stdin:
        # The documented form: curl ... | sh
        return subprocess.run(
            ["/bin/sh"],
            input=release.installer.read_text(encoding="utf-8"),
            env=env,
            capture_output=True,
            text=True,
        )
    return subprocess.run(
        ["/bin/sh", str(release.installer)], env=env, capture_output=True, text=True
    )


def _uv_calls(release: Release) -> list[str]:
    if not release.log.exists():
        return []
    return release.log.read_text(encoding="utf-8").splitlines()


def _installs(release: Release) -> list[str]:
    return [call for call in _uv_calls(release) if call.startswith("tool install")]


def test_it_installs_the_tagged_wheel_with_the_tui_extra(release: Release) -> None:
    _with_uv(release)

    result = _run(release, stdin=True)

    assert result.returncode == 0, result.stderr
    [install] = _installs(release)
    assert "--force" in install.split()
    # uv tool install takes the default interpreter, not the wheel's
    # requires-python, so the installer has to ask for one that fits.
    assert "--python >=3.12" in install
    assert "nare[tui] @ file://" in install
    assert install.endswith(f"/{WHEEL}")
    assert f"nare {VERSION}" in result.stdout
    assert "nare tui" in result.stdout


def test_it_checks_the_tui_imports_in_the_tool_environment(
    release: Release,
) -> None:
    _with_uv(release)

    result = _run(release)

    assert result.returncode == 0, result.stderr
    assert any(
        call.startswith("python ") and "nare.tui.app" in call
        for call in _uv_calls(release)
    )


def test_a_repeat_run_reinstalls_cleanly(release: Release) -> None:
    _with_uv(release)

    first = _run(release)
    second = _run(release)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert len(_installs(release)) == 2
    assert f"nare {VERSION}" in second.stdout


def test_a_wheel_with_another_digest_is_never_installed(release: Release) -> None:
    _with_uv(release)
    (release.assets / WHEEL).write_bytes(b"different bytes\n")

    result = _run(release)

    assert result.returncode != 0
    assert "SHA-256" in result.stderr
    assert _installs(release) == []


def test_without_uv_it_stops_and_says_how_to_get_it(release: Release) -> None:
    result = _run(release)

    assert result.returncode != 0
    assert "uv" in result.stderr
    assert "https://docs.astral.sh/uv/" in result.stderr


def test_a_mismatched_installed_version_fails(release: Release) -> None:
    _with_uv(release)

    result = _run(release, reports="2026.11.8")

    assert result.returncode != 0
    assert "2026.11.8" in result.stderr
    assert VERSION in result.stderr


def test_a_tui_that_does_not_import_fails(release: Release) -> None:
    _with_uv(release)

    result = _run(release, tui_exit=1)

    assert result.returncode != 0
    assert "TUI" in result.stderr


@pytest.mark.parametrize(
    "url", ["http://example.invalid/nare", "ftp://example.invalid", "/plain/path"]
)
def test_a_release_url_must_be_https_or_file(release: Release, url: str) -> None:
    _with_uv(release)

    result = _run(release, release_url=url)

    assert result.returncode != 0
    assert "NARE_RELEASE_URL" in result.stderr
    assert _installs(release) == []


def test_it_says_how_to_reach_nare_when_the_bin_dir_is_off_path(
    release: Release,
) -> None:
    _with_uv(release)

    off_path = _run(release)
    on_path = _run(release, extra_path=f"{release.home / 'bin'}{os.pathsep}")

    assert off_path.returncode == 0, off_path.stderr
    assert "PATH" in off_path.stdout
    assert on_path.returncode == 0, on_path.stderr
    assert "PATH" not in on_path.stdout


def test_it_refuses_arguments_it_does_not_know(release: Release) -> None:
    _with_uv(release)

    result = subprocess.run(
        ["/bin/sh", str(release.installer), "--version", "2026.1.0"],
        env={"HOME": str(release.home), "PATH": str(release.tools)},
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "bound to" in result.stderr
    assert _installs(release) == []
