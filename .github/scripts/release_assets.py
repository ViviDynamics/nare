"""Render a release's install.sh, and list exactly what the release ships.

    release_assets.py render --dist dist VERSION
    release_assets.py check --dist dist VERSION

`render` writes `dist/install.sh` from `install.sh.in`, bound to VERSION and
to the SHA-256 of the wheel already built in `dist/`. Binding at build time is
what lets `releases/latest/download/install.sh` be a stable URL: whichever
release `latest` points at, its script installs that release's wheel and no
other.

`check` prints JSON naming the files to attach, in order, and the SHA-256
lines for the release notes. It fails on a missing asset and on any file in
`dist/` other than `uv build`'s own `.gitignore`, so nothing stray ships.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path

TEMPLATE = Path(__file__).with_name("install.sh.in")
INSTALLER = "install.sh"
# What `uv build` leaves in dist/ besides the artifacts. 2026.9.7 shipped it
# as default.gitignore, so it is named here rather than matched loosely.
IGNORED = frozenset({".gitignore"})

# The release tag, CalVer with SemVer's no-leading-zeros rule (next_calver.py).
_CALVER = re.compile(r"^[0-9]{4}\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PLACEHOLDER = re.compile(r"@[A-Z][A-Z0-9_]*@")


def wheel_name(version: str) -> str:
    return f"nare-{version}-py3-none-any.whl"


def sdist_name(version: str) -> str:
    return f"nare-{version}.tar.gz"


def render_installer(template: str, *, version: str, wheel_sha256: str) -> str:
    """The template with its tag and wheel digest filled in."""
    if not _CALVER.match(version):
        raise ValueError(f"{version!r} is not a CalVer release tag")
    if not _SHA256.match(wheel_sha256):
        raise ValueError(f"{wheel_sha256!r} is not a lowercase hex SHA-256")
    rendered = template.replace("@NARE_VERSION@", version).replace(
        "@NARE_WHEEL_SHA256@", wheel_sha256
    )
    leftover = sorted(set(_PLACEHOLDER.findall(rendered)))
    if leftover:
        raise ValueError(f"unfilled placeholder in the installer: {leftover}")
    return rendered


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render_into(dist: Path, version: str, *, template: Path = TEMPLATE) -> Path:
    """Write dist/install.sh for VERSION, bound to the wheel built in dist/."""
    wheel = dist / wheel_name(version)
    if not wheel.is_file():
        raise ValueError(f"{wheel.name} is not in {dist}; build it first")
    out = dist / INSTALLER
    out.write_text(
        render_installer(
            template.read_text(encoding="utf-8"),
            version=version,
            wheel_sha256=_sha256(wheel),
        ),
        encoding="utf-8",
    )
    out.chmod(0o755)
    return out


def release_assets(dist: Path, version: str) -> list[Path]:
    """The wheel, the sdist and the installer, or ValueError."""
    expected = [wheel_name(version), sdist_name(version), INSTALLER]
    present = {path.name for path in dist.iterdir()}
    missing = [name for name in expected if name not in present]
    stray = sorted(present - set(expected) - IGNORED)
    problems = []
    if missing:
        problems.append(f"missing {', '.join(missing)}")
    if stray:
        problems.append(f"unexpected {', '.join(stray)}")
    if problems:
        raise ValueError(f"{dist} is not a {version} release: {'; '.join(problems)}")
    return [dist / name for name in expected]


def sha256_notes(files: Sequence[Path]) -> str:
    """One indented `digest  name` line per file, for the release notes."""
    return "".join(f"    {_sha256(file)}  {file.name}\n" for file in files)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["render", "check"])
    parser.add_argument("version")
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    args = parser.parse_args(argv)
    try:
        if args.command == "render":
            print(render_into(args.dist, args.version))
            return 0
        files = release_assets(args.dist, args.version)
    except ValueError as error:
        print(f"release_assets: {error}", file=sys.stderr)
        return 1
    report = {
        "files": [str(file) for file in files],
        "sha256_notes": sha256_notes(files),
    }
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
