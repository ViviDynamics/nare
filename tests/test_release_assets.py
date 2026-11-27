"""The release's installer and asset list (.github/scripts/release_assets.py).

Every release attaches `install.sh`, rendered from the template in the
repository and bound to that release's own tag and wheel digest, so the stable
`releases/latest/download/install.sh` URL can never mix one release's script
with another's wheel. These tests keep the rendering and the asset check
honest offline; the CI smoke job installs a real build.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / ".github" / "scripts"
MODULE = SCRIPTS / "release_assets.py"
TEMPLATE = SCRIPTS / "install.sh.in"
VERSION = "2026.11.9"
DIGEST = "ab" * 32


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("release_assets", MODULE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _render(version: str = VERSION, digest: str = DIGEST) -> str:
    rendered: str = _module().render_installer(
        TEMPLATE.read_text(encoding="utf-8"), version=version, wheel_sha256=digest
    )
    return rendered


def _dist(tmp_path: Path, *names: str) -> Path:
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in names:
        (dist / name).write_bytes(f"bytes of {name}\n".encode())
    return dist


WHEEL = f"nare-{VERSION}-py3-none-any.whl"
SDIST = f"nare-{VERSION}.tar.gz"


# Rendering


def test_the_template_has_both_placeholders() -> None:
    template = TEMPLATE.read_text(encoding="utf-8")
    assert "@NARE_VERSION@" in template
    assert "@NARE_WHEEL_SHA256@" in template


def test_rendering_binds_the_tag_and_the_digest() -> None:
    rendered = _render()
    assert f"version='{VERSION}'" in rendered
    assert f"wheel_sha256='{DIGEST}'" in rendered
    assert re.search(r"@[A-Z_]+@", rendered) is None


def test_the_rendered_script_downloads_the_exact_tag_never_latest() -> None:
    rendered = _render()
    assert "https://github.com/ViviDynamics/nare/releases/download/$version" in (
        rendered
    )
    assert "releases/latest" not in rendered


def test_the_rendered_script_is_posix_sh_and_strict() -> None:
    rendered = _render()
    assert rendered.startswith("#!/bin/sh\n")
    assert "set -eu" in rendered
    result = subprocess.run(
        ["/bin/sh", "-n"], input=rendered, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "version", ["2026.11", "v2026.11.9", "2026.09.1", "2026.11.9.dev1", "x'; rm"]
)
def test_rendering_refuses_a_version_that_is_not_calver(version: str) -> None:
    with pytest.raises(ValueError, match="CalVer"):
        _render(version=version)


@pytest.mark.parametrize("digest", ["", "ab" * 31, "AB" * 32, "zz" * 32])
def test_rendering_refuses_a_digest_that_is_not_sha256_hex(digest: str) -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        _render(digest=digest)


def test_rendering_refuses_a_template_with_a_leftover_placeholder() -> None:
    template = TEMPLATE.read_text(encoding="utf-8") + "# @NARE_OTHER@\n"
    with pytest.raises(ValueError, match="placeholder"):
        _module().render_installer(template, version=VERSION, wheel_sha256=DIGEST)


def test_render_into_dist_reads_the_built_wheels_digest(tmp_path: Path) -> None:
    dist = _dist(tmp_path, WHEEL, SDIST, ".gitignore")

    out = _module().render_into(dist, VERSION, template=TEMPLATE)

    assert out == dist / "install.sh"
    wheel_digest = hashlib.sha256((dist / WHEEL).read_bytes()).hexdigest()
    text = out.read_text(encoding="utf-8")
    assert f"wheel_sha256='{wheel_digest}'" in text
    assert out.stat().st_mode & 0o755 == 0o755


def test_render_into_dist_refuses_a_missing_wheel(tmp_path: Path) -> None:
    dist = _dist(tmp_path, SDIST)
    with pytest.raises(ValueError, match=re.escape(WHEEL)):
        _module().render_into(dist, VERSION, template=TEMPLATE)


# Asset validation


def test_the_assets_are_exactly_wheel_sdist_and_installer(tmp_path: Path) -> None:
    dist = _dist(tmp_path, WHEEL, SDIST, "install.sh", ".gitignore")

    files = _module().release_assets(dist, VERSION)

    assert [file.name for file in files] == [WHEEL, SDIST, "install.sh"]


@pytest.mark.parametrize("missing", [WHEEL, SDIST, "install.sh"])
def test_a_missing_asset_fails(tmp_path: Path, missing: str) -> None:
    names = [name for name in (WHEEL, SDIST, "install.sh") if name != missing]
    dist = _dist(tmp_path, *names)
    with pytest.raises(ValueError, match=re.escape(missing)):
        _module().release_assets(dist, VERSION)


@pytest.mark.parametrize(
    "stray",
    [
        "default.gitignore",
        "nare-2026.11.8-py3-none-any.whl",
        "nare-2026.11.8.tar.gz",
        "install.sh.in",
        "SHA256SUMS",
    ],
)
def test_a_stray_file_fails_rather_than_shipping(tmp_path: Path, stray: str) -> None:
    dist = _dist(tmp_path, WHEEL, SDIST, "install.sh", stray)
    with pytest.raises(ValueError, match=re.escape(stray)):
        _module().release_assets(dist, VERSION)


def test_the_digest_lines_cover_every_asset(tmp_path: Path) -> None:
    dist = _dist(tmp_path, WHEEL, SDIST, "install.sh")
    files = _module().release_assets(dist, VERSION)

    notes: str = _module().sha256_notes(files)

    lines = notes.splitlines()
    assert len(lines) == 3
    for line, file in zip(lines, files, strict=True):
        digest = hashlib.sha256(file.read_bytes()).hexdigest()
        assert line == f"    {digest}  {file.name}"


def test_the_command_line_renders_then_lists_the_assets(tmp_path: Path) -> None:
    dist = _dist(tmp_path, WHEEL, SDIST, ".gitignore")

    rendered = subprocess.run(
        [sys.executable, str(MODULE), "render", "--dist", str(dist), VERSION],
        capture_output=True,
        text=True,
    )
    assert rendered.returncode == 0, rendered.stderr
    listed = subprocess.run(
        [sys.executable, str(MODULE), "check", "--dist", str(dist), VERSION],
        capture_output=True,
        text=True,
    )
    assert listed.returncode == 0, listed.stderr

    report = json.loads(listed.stdout)
    assert [Path(file).name for file in report["files"]] == [
        WHEEL,
        SDIST,
        "install.sh",
    ]
    assert report["sha256_notes"].count("\n") == 3


def test_the_command_line_fails_on_a_stray_file(tmp_path: Path) -> None:
    dist = _dist(tmp_path, WHEEL, SDIST, "install.sh", "default.gitignore")

    listed = subprocess.run(
        [sys.executable, str(MODULE), "check", "--dist", str(dist), VERSION],
        capture_output=True,
        text=True,
    )

    assert listed.returncode != 0
    assert "default.gitignore" in listed.stderr
    assert listed.stdout == ""


# The workflows that run it


WORKFLOWS = ROOT / ".github" / "workflows"


def test_the_release_renders_smokes_and_checks_before_it_publishes() -> None:
    release = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    render = release.index("release_assets.py render")
    smoke = release.index("smoke_installer.sh")
    check = release.index("release_assets.py', 'check'")
    publish = release.index("'release', 'create'")
    # Releases are immutable: everything is attached in the creating call, so
    # the installer exists and has installed before that call is made.
    assert render < smoke < check < publish
    assert "files.length !== 2" not in release


def test_every_pull_request_smokes_the_installer_on_linux_and_macos() -> None:
    ci = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    job = ci[ci.index("\n  installer:") :]
    assert "smoke_installer.sh" in job
    assert "os: [ubuntu-latest, macos-latest]" in job
    assert "release_assets.py render" in job


def test_every_pull_request_installs_with_no_uv_and_on_a_clean_debian() -> None:
    ci = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    job = ci[ci.index("\n  installer:") : ci.index("\n  cases:")]
    assert 'smoke_installer.sh dist "$VERSION" --without-uv' in job
    debian = job[job.index("debian:stable-slim") :]
    assert "apt-get install -y -qq curl" in debian
    assert "--without-uv" in debian


def test_the_release_notes_give_the_pinned_and_headless_installs() -> None:
    release = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    notes = release[
        release.index("const install = ") : release.index("'release', 'create'")
    ]
    assert "releases/download/${version}/install.sh | sh" in notes
    # install ends in "| sh", so this renders as "| sh -s -- --no-tui".
    assert "install.sh | sh`;" in notes
    assert "${install} -s -- --no-tui" in notes


def test_no_workflow_runs_on_a_self_hosted_runner() -> None:
    hosted = re.compile(
        r"^(ubuntu|macos|windows)-[a-z0-9.-]+$|^\$\{\{ matrix\.os \}\}$"
    )
    for workflow in WORKFLOWS.glob("*.yml"):
        text = workflow.read_text(encoding="utf-8")
        for label in re.findall(r"^\s*runs-on:\s*(.+?)\s*$", text, re.MULTILINE):
            assert hosted.match(label), (workflow.name, label)
        for os_list in re.findall(r"^\s*os:\s*\[(.+)\]\s*$", text, re.MULTILINE):
            for label in os_list.split(","):
                assert hosted.match(label.strip()), (workflow.name, label)


def test_a_rerun_of_a_published_release_skips_the_build_side_steps() -> None:
    # A rerun exists to retry the image push. Rendering and installing again
    # for a release that already exists only adds network failures that would
    # block it.
    release = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    check = release.index("id: existing")
    gate = "if: steps.existing.outputs.published != 'true'"
    for step in (
        "name: Render the installer",
        "name: Install from the release assets",
        "name: Create GitHub release",
    ):
        start = release.index(step)
        assert check < start, step
        end = release.find("\n      - name:", start + 1)
        assert gate in release[start : end if end != -1 else None], step
