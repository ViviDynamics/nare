from __future__ import annotations

import pytest

from benchmarks.runner.config import Config
from benchmarks.runner.sandbox import IMAGE, SandboxError, preflight, repo_root

CONFIG = Config(
    model="claude-haiku",
    provider="anthropic",
    base_url=None,
    judge_model="claude-haiku",
    api_key="k",
)


def test_preflight_passes_when_docker_is_present() -> None:
    preflight(CONFIG, which=lambda name: "/usr/bin/docker")


def test_preflight_refuses_without_docker() -> None:
    with pytest.raises(SandboxError, match="docker"):
        preflight(CONFIG, which=lambda name: None)


def test_repo_root_holds_the_project_files() -> None:
    root = repo_root()
    assert (root / "pyproject.toml").is_file()
    assert (root / "benchmarks" / "Dockerfile").is_file()


def test_the_image_tag_is_local() -> None:
    assert IMAGE == "nare-bench:local"


@pytest.mark.docker
def test_the_image_builds_and_carries_nare() -> None:
    import subprocess

    from benchmarks.runner.sandbox import build_image

    build_image(repo_root())
    proc = subprocess.run(
        ["docker", "run", "--rm", IMAGE, "nare", "run", "--help"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0
    assert "--jsonl" in proc.stdout
