"""What the rendered install.sh does, offline, with a stub uv.

The stub records what the installer asked of uv and lays down a fake `nare`
and a fake tool interpreter, so these tests cover the script's own decisions:
the digest check, finding or installing uv, the tui extra and the headless
option, the version and TUI checks, and a repeat run. A stub of Astral's uv
installer stands in for the real one. CI's installer smoke job does the same
against a real build, the real uv and the real uv installer.
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
  "--version") echo "uv 0.0.0-stub" ;;
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

# Astral's installer, as far as the installer relies on it: it puts uv in
# UV_INSTALL_DIR. It records how it was called and whether stdin was a
# terminal, which would let it prompt.
STUB_UV_INSTALLER = """#!/bin/sh
set -eu
PATH=/usr/bin:/bin
if [ -t 0 ]; then tty=yes; else tty=no; fi
printf 'uv-installer UV_INSTALL_DIR=%s tty=%s\\n' "${UV_INSTALL_DIR:-}" "$tty" \\
  >> "$STUB_LOG"
[ "$STUB_UV_INSTALLER_EXIT" = 0 ] || exit "$STUB_UV_INSTALLER_EXIT"
[ "$STUB_UV_INSTALLER_WRITES" = 1 ] || exit 0
mkdir -p "$UV_INSTALL_DIR"
cp "$STUB_UV_SOURCE" "$UV_INSTALL_DIR/uv"
chmod +x "$UV_INSTALL_DIR/uv"
"""

# What the installer may call besides uv. Everything else is a shell builtin.
TOOLS = ("curl", "mkdir", "mktemp", "mv", "rm", "sh", "sha256sum", "shasum")


@dataclass
class Release:
    home: Path
    assets: Path
    tools: Path
    log: Path
    installer: Path
    uv_installer: Path
    uv_source: Path


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
    astral = tmp_path / "astral"
    astral.mkdir()
    uv_installer = astral / "install.sh"
    uv_installer.write_text(STUB_UV_INSTALLER, encoding="utf-8")
    uv_source = astral / "uv"
    uv_source.write_text(STUB_UV, encoding="utf-8")
    return Release(
        home, assets, tools, tmp_path / "uv.log", installer, uv_installer, uv_source
    )


def _with_uv(release: Release, where: Path | None = None) -> Path:
    uv = (where or release.tools) / "uv"
    uv.parent.mkdir(parents=True, exist_ok=True)
    uv.write_text(STUB_UV, encoding="utf-8")
    uv.chmod(0o755)
    return uv


def _run(
    release: Release,
    *,
    stdin: bool = False,
    reports: str = VERSION,
    tui_exit: int = 0,
    uv_installer_exit: int = 0,
    uv_installer_writes: bool = True,
    release_url: str | None = None,
    args: tuple[str, ...] = (),
    extra_path: str = "",
    extra_env: dict[str, str] | None = None,
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
        # Never Astral's real installer: these tests are offline.
        "NARE_UV_INSTALLER_URL": f"file://{release.uv_installer}",
        "STUB_UV_SOURCE": str(release.uv_source),
        "STUB_UV_INSTALLER_EXIT": str(uv_installer_exit),
        "STUB_UV_INSTALLER_WRITES": "1" if uv_installer_writes else "0",
        **(extra_env or {}),
    }
    if stdin:
        # The documented form: curl ... | sh
        return subprocess.run(
            ["/bin/sh", "-s", "--", *args],
            input=release.installer.read_text(encoding="utf-8"),
            env=env,
            capture_output=True,
            text=True,
        )
    return subprocess.run(
        ["/bin/sh", str(release.installer), *args],
        env=env,
        capture_output=True,
        text=True,
    )


def _uv_calls(release: Release) -> list[str]:
    if not release.log.exists():
        return []
    return release.log.read_text(encoding="utf-8").splitlines()


def _installs(release: Release) -> list[str]:
    return [call for call in _uv_calls(release) if call.startswith("tool install")]


def _uv_installer_runs(release: Release) -> list[str]:
    return [call for call in _uv_calls(release) if call.startswith("uv-installer")]


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


def test_without_uv_it_installs_uv_into_local_bin_and_says_so(
    release: Release,
) -> None:
    result = _run(release, stdin=True)

    assert result.returncode == 0, result.stderr
    local_bin = release.home / ".local" / "bin"
    # Never a prompt: under curl | sh there is no one to answer it.
    assert _uv_installer_runs(release) == [
        f"uv-installer UV_INSTALL_DIR={local_bin} tty=no"
    ]
    assert (local_bin / "uv").is_file()
    assert "uv is not installed" in result.stdout
    assert "Astral's official installer" in result.stdout
    assert f"{local_bin / 'uv'}" in result.stdout
    assert len(_installs(release)) == 1
    assert f"{local_bin / 'uv'} tool update-shell" in result.stdout


def test_a_repeat_run_uses_the_uv_it_installed(release: Release) -> None:
    first = _run(release, stdin=True)
    second = _run(release, stdin=True)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert len(_uv_installer_runs(release)) == 1
    assert "uv is not installed" not in second.stdout
    assert len(_installs(release)) == 2


def test_an_existing_uv_is_used_and_no_other_is_installed(release: Release) -> None:
    _with_uv(release)

    result = _run(release, stdin=True)

    assert result.returncode == 0, result.stderr
    assert _uv_installer_runs(release) == []
    assert not (release.home / ".local" / "bin" / "uv").exists()
    assert "uv is not installed" not in result.stdout


def test_a_digest_mismatch_installs_no_uv_either(release: Release) -> None:
    (release.assets / WHEEL).write_bytes(b"different bytes\n")

    result = _run(release, stdin=True)

    assert result.returncode != 0
    assert "SHA-256" in result.stderr
    assert _uv_installer_runs(release) == []
    assert not (release.home / ".local" / "bin" / "uv").exists()


@pytest.mark.parametrize(
    ("exit_code", "writes"), [(1, False), (0, False)], ids=["fails", "no-uv"]
)
def test_a_uv_installer_that_leaves_no_uv_stops_with_how_to_get_it(
    release: Release, exit_code: int, writes: bool
) -> None:
    result = _run(release, uv_installer_exit=exit_code, uv_installer_writes=writes)

    assert result.returncode != 0
    assert "uv" in result.stderr
    assert "https://docs.astral.sh/uv/" in result.stderr
    assert _installs(release) == []


@pytest.mark.parametrize("url", ["http://example.invalid/uv.sh", "/plain/uv.sh"])
def test_a_uv_installer_url_must_be_https_or_file(release: Release, url: str) -> None:
    result = _run(release, extra_env={"NARE_UV_INSTALLER_URL": url})

    assert result.returncode != 0
    assert "NARE_UV_INSTALLER_URL" in result.stderr
    assert _uv_installer_runs(release) == []


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
    uv = _with_uv(release)

    off_path = _run(release)
    on_path = _run(release, extra_path=f"{release.home / 'bin'}{os.pathsep}")

    assert off_path.returncode == 0, off_path.stderr
    # The exact line for this shell, with $PATH left for the shell to expand.
    bin_dir = release.home / "bin"
    assert f'  export PATH="{bin_dir}:$PATH"' in off_path.stdout.splitlines()
    # The uv it used, by path: it may have been found off PATH too.
    assert f"{uv} tool update-shell" in off_path.stdout
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


def _installed_path(release: Release) -> str:
    [install] = _installs(release)
    return install.split(" @ file://", 1)[1]


def test_the_verified_wheel_is_kept_where_uv_can_read_it_again(
    release: Release,
) -> None:
    # uv records the wheel's path and re-reads it on upgrade or reinstall, so
    # a temporary directory would break uv tool upgrade later.
    _with_uv(release)

    result = _run(release)

    assert result.returncode == 0, result.stderr
    kept = release.home / ".local" / "share" / "nare" / "wheels" / WHEEL
    assert _installed_path(release) == str(kept)
    assert kept.read_bytes() == (release.assets / WHEEL).read_bytes()


def test_xdg_data_home_holds_the_kept_wheel(release: Release, tmp_path: Path) -> None:
    _with_uv(release)
    data = tmp_path / "data"

    result = _run(release, extra_env={"XDG_DATA_HOME": str(data)})

    assert result.returncode == 0, result.stderr
    assert _installed_path(release) == str(data / "nare" / "wheels" / WHEEL)


def test_a_new_install_drops_the_previous_releases_wheel(release: Release) -> None:
    _with_uv(release)
    wheels = release.home / ".local" / "share" / "nare" / "wheels"
    wheels.mkdir(parents=True)
    (wheels / "nare-2026.11.8-py3-none-any.whl").write_bytes(b"old\n")

    result = _run(release)

    assert result.returncode == 0, result.stderr
    assert sorted(path.name for path in wheels.iterdir()) == [WHEEL]


def test_a_failed_check_keeps_the_previous_releases_wheel(release: Release) -> None:
    _with_uv(release)
    wheels = release.home / ".local" / "share" / "nare" / "wheels"
    wheels.mkdir(parents=True)
    (wheels / "nare-2026.11.8-py3-none-any.whl").write_bytes(b"old\n")

    result = _run(release, tui_exit=1)

    assert result.returncode != 0
    assert (wheels / "nare-2026.11.8-py3-none-any.whl").exists()


@pytest.mark.parametrize("where", [".local/bin", ".cargo/bin", "xdg-bin"])
def test_it_finds_uv_where_uvs_own_installer_put_it(
    release: Release, where: str
) -> None:
    uv = _with_uv(release, release.home / where)
    assert shutil.which("uv", path=str(release.tools)) is None

    result = _run(release, extra_env={"XDG_BIN_HOME": str(release.home / "xdg-bin")})

    assert result.returncode == 0, result.stderr
    assert len(_installs(release)) == 1
    assert f"{uv} tool update-shell" in result.stdout


def test_uv_comes_from_astrals_installer_by_default(release: Release) -> None:
    # The tests replace it; a reader's run fetches Astral's own, over https.
    script = release.installer.read_text(encoding="utf-8")
    assert "${NARE_UV_INSTALLER_URL:-https://astral.sh/uv/install.sh}" in script


@pytest.mark.parametrize(
    ("args", "env"),
    [(("--no-tui",), {}), ((), {"NARE_INSTALL_NO_TUI": "1"})],
    ids=["flag", "env"],
)
def test_the_headless_option_installs_nare_without_the_tui_extra(
    release: Release, args: tuple[str, ...], env: dict[str, str]
) -> None:
    _with_uv(release)

    result = _run(release, stdin=True, args=args, extra_env=env)

    assert result.returncode == 0, result.stderr
    [install] = _installs(release)
    assert " nare @ file://" in install
    assert "[tui]" not in install
    # No TUI to check, and none to offer.
    assert not any(call.startswith("python ") for call in _uv_calls(release))
    assert f"nare {VERSION} is installed" in result.stdout
    assert "nare tui" not in result.stdout
    assert "nare run" in result.stdout


@pytest.mark.parametrize("value", ["", "0"])
def test_the_headless_variable_off_keeps_the_tui(release: Release, value: str) -> None:
    _with_uv(release)

    result = _run(release, extra_env={"NARE_INSTALL_NO_TUI": value})

    assert result.returncode == 0, result.stderr
    [install] = _installs(release)
    assert "nare[tui] @ file://" in install


@pytest.mark.parametrize("value", ["yes", "true", "2"])
def test_the_headless_variable_takes_only_0_or_1(release: Release, value: str) -> None:
    _with_uv(release)

    result = _run(release, extra_env={"NARE_INSTALL_NO_TUI": value})

    assert result.returncode != 0
    assert "NARE_INSTALL_NO_TUI" in result.stderr
    assert _installs(release) == []


def test_help_names_the_headless_option(release: Release) -> None:
    result = _run(release, args=("--help",))

    assert result.returncode == 0, result.stderr
    assert "--no-tui" in result.stdout
    assert "NARE_INSTALL_NO_TUI=1" in result.stdout


def test_help_works_whatever_the_headless_variable_holds(release: Release) -> None:
    result = _run(release, args=("--help",), extra_env={"NARE_INSTALL_NO_TUI": "yes"})

    assert result.returncode == 0, result.stderr
    assert "--no-tui" in result.stdout
