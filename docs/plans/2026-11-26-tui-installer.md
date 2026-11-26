# Publish a TUI installer at a stable GitHub Releases latest URL

Issue #318

## Scope
In: an `install.sh` asset on every release, rendered from a template in the
repository and bound to that release's tag and wheel digest; release asset
validation and the notes' SHA-256 list extended to it; a smoke test of a clean
install and a repeat run, on every pull request (Linux and macOS) and in the
release job against the bytes it is about to publish; the README's install
section.
Out: switching the landing page to the stable URL, which happens outside this
repository after the first release that carries the installer; an uninstall
mode; Windows.

## Assumptions
- The template is `.github/scripts/install.sh.in`, next to `next_calver.py`.
  `.github/scripts/release_assets.py` renders it and validates `dist/`, so both
  are plain Python under pytest rather than sed and JavaScript in YAML.
- Two placeholders, `@NARE_VERSION@` and `@NARE_WHEEL_SHA256@`. The wheel is
  built before the installer is rendered, so its digest is known and baked in.
  Rendering refuses a version that is not CalVer, a digest that is not 64 hex
  characters, and any placeholder left over.
- The installer downloads the exact tagged wheel URL, never `latest`, and
  checks its SHA-256 before installing. `NARE_RELEASE_URL` replaces the
  download directory (https or file URLs only) so the smoke test can install
  local bytes; the baked-in digest still has to match, so the override cannot
  install anything else.
- uv only, no pipx fallback. uv provisions Python 3.12 itself when the system
  has none, which is the usual failure of a pipx install on macOS and older
  Linux. One tested path is better than an untested fallback. Without uv the
  installer stops and names uv's install page; it never pipes a second remote
  script.
- `uv tool install --force --python '>=3.12'` with the extra, from the
  verified local file. uv tool install takes the default interpreter rather
  than the wheel's requires-python (found by the first real smoke run, against
  a system Python 3.11), so the installer names the range. A repeat run
  reinstalls the same release cleanly.
- After installing, the installer checks `nare --version` against its own tag
  and imports `nare.tui.app` in the tool's environment, then prints how to
  start the TUI and, when needed, how to put uv's bin directory on PATH.
- Asset validation ships exactly the wheel, the sdist and `install.sh` for the
  release version. `uv build`'s `dist/.gitignore` is left behind; any other
  file in `dist/` fails the release, so a stray file cannot ship.

## Tasks
- [x] 1. Rendering: tests that the tag and digest are substituted, nothing is
  left over, bad versions and digests are refused.
- [x] 2. Asset validation: tests that exactly wheel, sdist and installer are
  accepted, a missing or stray file fails, `.gitignore` is ignored, and the
  digest lines cover all three.
- [x] 3. Installer behaviour offline, with a stub uv: verifies the digest and
  refuses a mismatch before installing, refuses a missing uv clearly, installs
  with the tui extra, checks the version, and a repeat run succeeds.
- [x] 4. Release workflow: render, validate, smoke, publish with three assets.
  CI job: the same smoke on Linux and macOS for every pull request.
- [ ] 5. README and contract: the stable command, prerequisites, `nare tui`,
  and the pinned form; the CLI's missing-extra hint points at a working command.
