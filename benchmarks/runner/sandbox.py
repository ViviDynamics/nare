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
# shell command is how quoting bugs become arbitrary execution.
_AGENT_SCRIPT = """\
set -e
git init -q -b main
git add -A
git -c user.email=bench@nare.invalid -c user.name=nare-bench commit -qm fixture
exec nare run --yes --jsonl --session /out/session.json \
     --max-turns "$BENCH_MAX_TURNS" --model "$BENCH_MODEL" -- "$BENCH_PROMPT"
"""


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
    env: dict[str, str] | None = None,
    passthrough: tuple[str, ...] = (),
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
        command += ["-v", f"{artifacts}:/out"]
    for key, value in (env or {}).items():
        command += ["-e", f"{key}={value}"]
    for key in passthrough:
        if os.environ.get(key):
            # Passed by name so the value never reaches argv, which is
            # world-readable through ps and /proc.
            command += ["-e", key]
    command += [IMAGE, *argv]

    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
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

    `--effort` is deliberately never passed: it renders as
    thinking.budget_tokens, which some models accept and others reject
    outright, and a flag whose availability varies by model has no place in a
    controlled measurement.
    """
    started = time.monotonic()
    code, out, err, timed_out = _docker_run(
        workdir,
        ["sh", "-c", _AGENT_SCRIPT],
        case.timeout,
        artifacts=artifacts,
        # Provider and endpoint come from the resolved config, not the host
        # environment: `--base-url` must steer the agent, not just the judge.
        env={
            "BENCH_PROMPT": case.prompt,
            "BENCH_MODEL": config.model,
            "BENCH_MAX_TURNS": str(case.max_turns),
            "NARE_PROVIDER": config.provider,
            **({"NARE_BASE_URL": config.base_url} if config.base_url else {}),
        },
        passthrough=("ANTHROPIC_API_KEY",),
    )
    return RunArtifacts(
        stdout=out,
        stderr=err,
        exit_code=code,
        timed_out=timed_out,
        duration_s=time.monotonic() - started,
    )


def run_check(workdir: Path, cmd: str, timeout: int) -> tuple[int, str]:
    """Grade one bash check in a second container over the same directory."""
    code, out, err, _ = _docker_run(workdir, ["sh", "-c", cmd], timeout)
    return code, (out + err).strip()


def git_checkpoint(workdir: Path) -> None:
    """Used by `verify`, which needs a checkpoint without running the agent."""
    _docker_run(
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
