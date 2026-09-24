from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.runner.case import Case, Check
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


def make_case(tmp_path: Path, prompt: str = "Create done.txt containing ok.") -> Case:
    directory = tmp_path / "demo"
    (directory / "fixture").mkdir(parents=True)
    (directory / "fixture" / "bar.py").write_text("def foo():\n    return 1\n")
    return Case(
        id="demo",
        tier="smoke",
        prompt=prompt,
        directory=directory,
        max_turns=4,
        reps=1,
        timeout=120,
        checks=(Check(kind="bash", cmd="test -f done.txt"),),
        judge=None,
    )


def test_sandbox_copies_the_fixture_and_cleans_up(tmp_path: Path) -> None:
    from benchmarks.runner.sandbox import sandbox

    case = make_case(tmp_path)
    with sandbox(case) as (workdir, artifacts):
        assert (workdir / "bar.py").read_text().startswith("def foo")
        assert workdir != case.fixture, "the fixture itself is never graded"
        assert artifacts.is_dir()
        kept = workdir
    assert not kept.exists()


@pytest.mark.docker
def test_run_check_reports_the_exit_code(tmp_path: Path) -> None:
    from benchmarks.runner.sandbox import run_check, sandbox

    with sandbox(make_case(tmp_path)) as (workdir, _):
        assert run_check(workdir, "test -f bar.py", 60)[0] == 0
        code, output = run_check(workdir, "test -f absent.txt", 60)
        assert code != 0


@pytest.mark.docker
def test_run_check_writes_as_the_invoking_user(tmp_path: Path) -> None:
    """Root-owned files in the bind mount would break git diff on the host."""
    import os

    from benchmarks.runner.sandbox import run_check, sandbox

    with sandbox(make_case(tmp_path)) as (workdir, _):
        assert run_check(workdir, "touch made.txt", 60)[0] == 0
        assert (workdir / "made.txt").stat().st_uid == os.getuid()


@pytest.mark.docker
def test_git_diff_shows_the_agents_change_only(tmp_path: Path) -> None:
    from benchmarks.runner.sandbox import git_checkpoint, git_diff, sandbox

    with sandbox(make_case(tmp_path)) as (workdir, _):
        git_checkpoint(workdir)
        assert git_diff(workdir) == ""
        (workdir / "bar.py").write_text("def foo():\n    return 2\n")
        diff = git_diff(workdir)
        assert "return 2" in diff
        assert "bar.py" in diff


@pytest.mark.docker
def test_git_diff_shows_new_files_and_survives_an_agent_commit(
    tmp_path: Path,
) -> None:
    import subprocess

    from benchmarks.runner.sandbox import git_checkpoint, git_diff, sandbox

    with sandbox(make_case(tmp_path)) as (workdir, _):
        git_checkpoint(workdir)
        (workdir / "notes.txt").write_text("slow.sh hung\n")
        assert "notes.txt" in git_diff(workdir)
        subprocess.run(
            ["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "x"],
            cwd=workdir,
            check=True,
        )
        assert "slow.sh hung" in git_diff(workdir)


def test_run_agent_sends_the_resolved_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from benchmarks.runner import sandbox as mod

    seen: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> tuple[int, str, str, bool]:
        seen.update(kwargs)
        return 0, "", "", False

    monkeypatch.setattr(mod, "_docker_run", fake_run)
    config = replace(CONFIG, provider="openai", base_url="http://proxy")
    mod.run_agent(make_case(tmp_path), tmp_path, tmp_path, config)
    env = seen["env"]
    assert isinstance(env, dict)
    assert env["NARE_PROVIDER"] == "openai"
    assert env["NARE_BASE_URL"] == "http://proxy"


@pytest.mark.docker
def test_a_hanging_command_is_killed_at_the_timeout(tmp_path: Path) -> None:
    from benchmarks.runner.sandbox import run_check, sandbox

    with sandbox(make_case(tmp_path)) as (workdir, _):
        code, _ = run_check(workdir, "sleep 30", 3)
        assert code != 0


@pytest.mark.live
@pytest.mark.docker
def test_run_agent_end_to_end(tmp_path: Path) -> None:
    """The one test that spends money. Deselected twice over by default."""
    import os

    from benchmarks.runner.config import resolve_config
    from benchmarks.runner.sandbox import run_agent, sandbox

    config = resolve_config(
        model=None, provider=None, base_url=None, judge_model=None, env=os.environ
    )
    case = make_case(tmp_path)
    with sandbox(case) as (workdir, artifacts):
        result = run_agent(case, workdir, artifacts, config)
    assert not result.timed_out
    assert '"type": "result"' in result.stdout or '"type":"result"' in result.stdout
