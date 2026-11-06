"""Docker. The only module that runs the agent, and the only one that can
damage something if it is wrong.

Containment covers the filesystem and the process tree, not the network: the
container must reach the model endpoint, so `--network none` is not available.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from benchmarks.runner.case import Case
from benchmarks.runner.config import Config
from benchmarks.runner.nare_args import INPUTS
from nare.session import new_session, save

IMAGE = "nare-bench:local"


class SandboxError(Exception):
    """The sandbox could not be prepared or run. Always exit 2."""


def repo_root() -> Path:
    """This file is benchmarks/runner/sandbox.py, so the root is two up."""
    return Path(__file__).resolve().parent.parent.parent


def preflight(
    config: Config, *, which: Callable[[str], str | None] = shutil.which
) -> None:
    """Fail before anything is spent. Config already proved the key and model."""
    if which("docker") is None:
        raise SandboxError(
            "docker is not on PATH; the benchmark runs every repetition in a container"
        )


def build_image(root: Path) -> None:
    proc = subprocess.run(
        [
            "docker",
            "build",
            "-t",
            IMAGE,
            "-f",
            str(root / "benchmarks" / "Dockerfile"),
            str(root),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SandboxError(f"docker build failed:\n{proc.stderr.strip()}")


# The agent's script. Values arrive as environment variables rather than
# interpolated text: a prompt is arbitrary user data, and splicing it into a
# shell command is how quoting bugs become arbitrary execution. The same goes
# for nare's own arguments: the run's, then the case's, arrive as positional
# parameters after the script and are expanded with "$@", never as text. nare
# is not exec'd: what it left running and what it installed die with this
# container, so the script reads them into /out/after.json after it exits.
_AGENT_SCRIPT = """\
set -e
git init -q -b main
git add -A
git -c user.email=bench@nare.invalid -c user.name=nare-bench commit -qm fixture
status=0
nare run --yes --jsonl --session /out/session.json \
     --max-turns "$BENCH_MAX_TURNS" --model "$BENCH_MODEL" \
     ${BENCH_MAX_TOKENS:+--max-tokens "$BENCH_MAX_TOKENS"} \
     "$@" -- "$BENCH_PROMPT" \
     || status=$?
python3 -I -c "$BENCH_LEFTOVERS" /out/after.json || true
exit "$status"
"""

# benchmarks/runner/leftovers.py, which the image does not carry.
_LEFTOVERS = (Path(__file__).parent / "leftovers.py").read_text(encoding="utf-8")


@dataclass(frozen=True)
class RunArtifacts:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    duration_s: float


@contextmanager
def sandbox(case: Case) -> Iterator[tuple[Path, Path]]:
    """A throwaway copy of the fixture, plus a directory outside the graded tree.

    The session is written to the artifacts directory rather than the work
    directory so that it never shows up in the diff the judge reads, and still
    survives long enough to debug a failed repetition.
    """
    holder = Path(tempfile.mkdtemp(prefix=f"nare-bench-{case.id}-"))
    workdir = holder / "work"
    artifacts = holder / "out"
    try:
        shutil.copytree(case.fixture, workdir)
        artifacts.mkdir()
        yield workdir, artifacts
    finally:
        shutil.rmtree(holder, ignore_errors=True)


def _docker_run(
    workdir: Path,
    argv: list[str],
    timeout: int,
    *,
    artifacts: Path | None = None,
    read_only: bool = False,
    inputs: Path | None = None,
    env: dict[str, str] | None = None,
    passthrough: tuple[str, ...] = (),
    stdin: str | None = None,
) -> tuple[int, str, str, bool]:
    name = f"nare-bench-{uuid.uuid4().hex[:12]}"
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        # As the invoking user, so files in the bind mount are owned by them
        # and the host's git does not refuse the repository as dubious.
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "-v",
        f"{workdir}:/work",
        "-w",
        "/work",
    ]
    if artifacts is not None:
        mount = f"{artifacts}:/out:ro" if read_only else f"{artifacts}:/out"
        command += ["-v", mount]
    if inputs is not None:
        command += ["-v", f"{inputs}:{INPUTS}:ro"]
    for key, value in (env or {}).items():
        command += ["-e", f"{key}={value}"]
    for key in passthrough:
        if os.environ.get(key):
            # Passed by name so the value never reaches argv, which is
            # world-readable through ps and /proc.
            command += ["-e", key]
    if stdin is not None:
        # Kept open so the container can read it; without -i, docker sends none.
        command.insert(command.index("run") + 1, "-i")
    command += [IMAGE, *argv]

    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, input=stdin
        )
        return proc.returncode, proc.stdout, proc.stderr, False
    except subprocess.TimeoutExpired as exc:
        # subprocess's timeout kills the docker CLI, not the container it
        # started. Without this the container outlives the repetition.
        subprocess.run(["docker", "kill", name], capture_output=True)
        out = exc.stdout or b""
        err = exc.stderr or b""
        return (
            124,
            out.decode(errors="replace") if isinstance(out, bytes) else out,
            err.decode(errors="replace") if isinstance(err, bytes) else err,
            True,
        )


def run_agent(
    case: Case, workdir: Path, artifacts: Path, config: Config
) -> RunArtifacts:
    """Run one repetition of the agent against a prepared sandbox.

    The runner never passes `--effort` of its own accord: which levels a model
    takes, and what a level means behind a proxy, vary by model. An operator may
    pass it as a run argument. It is then part of the variant, and comparisons
    never cross models, so a level is only ever compared with the same level on
    the same model.
    """
    started = time.monotonic()
    code, out, err, timed_out = _docker_run(
        workdir,
        ["sh", "-c", _AGENT_SCRIPT, "nare-bench", *config.run_args.passed, *case.args],
        case.timeout,
        artifacts=artifacts,
        inputs=config.run_args.inputs,
        # Provider and endpoint come from the resolved config, not the host
        # environment: `--base-url` must steer the agent, not just the judge.
        env={
            "BENCH_PROMPT": case.prompt,
            "BENCH_MODEL": config.model,
            "BENCH_MAX_TURNS": str(case.max_turns),
            "BENCH_LEFTOVERS": _LEFTOVERS,
            "NARE_PROVIDER": config.provider,
            **({"NARE_BASE_URL": config.base_url} if config.base_url else {}),
            **(
                {"BENCH_MAX_TOKENS": str(config.max_tokens)}
                if config.max_tokens
                else {}
            ),
        },
        # The key the provider under test actually reads, not always the
        # Anthropic one: an openai run needs OPENAI_API_KEY in the container.
        # NARE_STREAM too: a proxy behind Cloudflare answers 524 to a turn
        # that takes over 120 s to return whole, and a streamed turn does not.
        # It changes how a turn arrives, not what the run measures.
        passthrough=(config.api_key_var, "NARE_STREAM"),
    )
    return RunArtifacts(
        stdout=out,
        stderr=err,
        exit_code=code,
        timed_out=timed_out,
        duration_s=time.monotonic() - started,
    )


def run_check(
    workdir: Path, cmd: str, timeout: int, *, artifacts: Path | None = None
) -> tuple[int, str]:
    """Grade one bash check in a second container over the same directory.

    The run's artifacts, when given, are mounted read-only at /out. A server
    the agent left running died with the agent's container; only
    /out/after.json remembers it.
    """
    code, out, err, _ = _docker_run(
        workdir, ["sh", "-c", cmd], timeout, artifacts=artifacts, read_only=True
    )
    return code, (out + err).strip()


def git_checkpoint(workdir: Path) -> None:
    """Used by `verify`, which needs a checkpoint without running the agent.

    A checkpoint that fails leaves no fixture commit, and a diff against no
    commit reads as no change, so the failure is raised, not ignored.
    """
    code, _, err, _ = _docker_run(
        workdir,
        [
            "sh",
            "-c",
            "git init -q -b main && git add -A && "
            "git -c user.email=bench@nare.invalid -c user.name=nare-bench "
            "commit -qm fixture",
        ],
        60,
    )
    if code != 0:
        raise SandboxError(f"could not checkpoint the fixture:\n{err.strip()}")


# The closing message, written by the container from stdin rather than by the
# host through the bind mount: Docker Desktop can serve a file's text from before
# a host write to the first container that reads it, so the first check could
# judge the message the run before it, or none. A container's own write is seen.
_WRITE_CLOSING = """\
import os, sys
path = sys.argv[1]
# The agent's container can leave anything at this path, a symlink included,
# and this write must land at the path, not wherever a link points. Remove
# what is there, then create the file exclusively.
if os.path.lexists(path):
    os.unlink(path)
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
with os.fdopen(fd, "w", encoding="utf-8") as out:
    out.write(sys.stdin.read())
"""


def write_out(workdir: Path, artifacts: Path, name: str, text: str) -> None:
    """Write one file under /out from a container, where the checks read it."""
    code, _, err, _ = _docker_run(
        workdir,
        ["python3", "-I", "-c", _WRITE_CLOSING, f"/out/{name}"],
        60,
        artifacts=artifacts,
        stdin=text,
    )
    if code != 0:
        raise SandboxError(f"could not write /out/{name}:\n{err.strip()}")


def write_closing(workdir: Path, artifacts: Path, text: str) -> None:
    """Put the closing message at /out/closing.txt, where the checks read it."""
    write_out(workdir, artifacts, "closing.txt", text)


# A reference attempt, applied the way an agent's work arrives: in one container,
# which then leaves its leftovers in /out/after.json, as nare's container does.
# The reference's files are mounted read-only at /in: patch.diff, then solve.sh.
_APPLY_REFERENCE = """\
cd /work || exit 2
status=0
if [ -f /in/patch.diff ]; then git apply /in/patch.diff || status=$?; fi
if [ "$status" -eq 0 ] && [ -f /in/solve.sh ]; then sh /in/solve.sh || status=$?; fi
python3 -I -c "$BENCH_LEFTOVERS" /out/after.json || true
exit "$status"
"""


def apply_reference(workdir: Path, artifacts: Path, directory: Path) -> int:
    """Apply a reference solution or exploit to the sandbox; its exit status."""
    code, _, _, _ = _docker_run(
        workdir,
        ["sh", "-c", _APPLY_REFERENCE],
        600,
        artifacts=artifacts,
        inputs=directory,
        env={"BENCH_LEFTOVERS": _LEFTOVERS},
    )
    return code


def idle_artifacts(case: Case, workdir: Path, artifacts: Path) -> None:
    """What /out holds after a run in which the agent did nothing: the
    session nare starts with, an empty closing message, and the snapshot of a
    container left idle. Used by `verify`, which never runs the agent.
    """
    save(new_session(case.prompt), artifacts / "session.json")
    # An idle run closes with nothing: the closing message is empty.
    write_closing(workdir, artifacts, "")
    _docker_run(
        workdir,
        ["python3", "-I", "-c", _LEFTOVERS, "/out/after.json"],
        60,
        artifacts=artifacts,
    )


def git_diff(workdir: Path) -> str:
    """Read the agent's change on the host: the bind mount put .git here.

    Staged and diffed against the fixture commit, not `git diff HEAD`: that
    leaves out every file the agent created, and shows nothing at all once the
    agent commits its own work.
    """

    def git(*args: str) -> str:
        proc = subprocess.run(
            ["git", *args], cwd=workdir, capture_output=True, text=True
        )
        return proc.stdout

    git("add", "-A")
    fixture = git("rev-list", "--max-parents=0", "HEAD").strip()
    return git("diff", "--cached", fixture) if fixture else ""
