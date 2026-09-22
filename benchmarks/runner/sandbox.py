"""Docker. The only module that runs the agent, and the only one that can
damage something if it is wrong.

Containment covers the filesystem and the process tree, not the network: the
container must reach the model endpoint, so `--network none` is not available.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

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
