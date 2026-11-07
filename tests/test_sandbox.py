"""The bash sandbox: profiles, refusal paths and wiring, without running a sandbox.

The live escape tests are in test_sandbox_live.py. These check what is built
and what is refused, on any platform.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from nare import sandbox
from nare.cli import main
from nare.sandbox import Availability, Confinement, SandboxUnavailable
from nare.tools import Policy, bash_outcome, dispatch
from nare.transport import ToolCall

AVAILABLE = Availability(mechanism="seatbelt", reason="")
UNAVAILABLE = Availability(mechanism=None, reason="no OS sandbox for test")


@pytest.fixture
def available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox, "availability", lambda: AVAILABLE)


@pytest.fixture
def unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox, "availability", lambda: UNAVAILABLE)


# Seatbelt profile


def test_seatbelt_denies_by_default_and_writes_only_root_and_scratch(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    scratch = tmp_path / "scratch"
    profile = sandbox.seatbelt_profile(root, scratch, (), network=False)
    assert profile.startswith("(version 1)")
    assert "(deny default)" in profile
    assert f'(allow file-write* (subpath "{root}"))' in profile
    assert f'(allow file-write* (subpath "{scratch}"))' in profile


def test_seatbelt_denies_each_credential_for_reads_and_writes(tmp_path: Path) -> None:
    secret = tmp_path / ".ssh"
    profile = sandbox.seatbelt_profile(
        tmp_path / "root", tmp_path / "s", (secret,), network=False
    )
    assert f'(deny file-read* (subpath "{secret}"))' in profile
    assert f'(deny file-write* (subpath "{secret}"))' in profile


def test_seatbelt_denies_the_keychain_services_the_policy_needs(tmp_path: Path) -> None:
    profile = sandbox.seatbelt_profile(tmp_path, tmp_path, (), network=False)
    assert '(deny mach-lookup (global-name "com.apple.SecurityServer")' in profile


def test_seatbelt_network_is_off_unless_allowed(tmp_path: Path) -> None:
    off = sandbox.seatbelt_profile(tmp_path, tmp_path, (), network=False)
    on = sandbox.seatbelt_profile(tmp_path, tmp_path, (), network=True)
    assert "(allow network" not in off
    assert "(allow network*)" in on


def test_seatbelt_escapes_quotes_and_backslashes(tmp_path: Path) -> None:
    root = tmp_path / 'a"b\\c'
    profile = sandbox.seatbelt_profile(root, tmp_path, (), network=False)
    assert 'a\\"b\\\\c' in profile


def test_seatbelt_refuses_a_control_character_in_a_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="control"):
        sandbox.seatbelt_profile(tmp_path / "a\nb", tmp_path, (), network=False)


# bubblewrap argv


def _bwrap(**kw: object) -> list[str]:
    root = kw.pop("root", Path("/work/root"))
    assert isinstance(root, Path)
    return sandbox.bwrap_argv(
        root,
        kw.pop("scratch", Path("/tmp/nare-bash-x")),  # type: ignore[arg-type]
        kw.pop("denied", ()),  # type: ignore[arg-type]
        network=bool(kw.pop("network", False)),
        command=["bash", "-o", "pipefail", "-c", "true"],
        bwrap="bwrap",
    )


def test_bwrap_reads_the_host_and_binds_only_root_and_scratch() -> None:
    argv = _bwrap()
    assert argv[0] == "bwrap"
    ro = argv.index("--ro-bind")
    assert argv[ro : ro + 3] == ["--ro-bind", "/", "/"]
    binds = [i for i, a in enumerate(argv) if a == "--bind"]
    assert [argv[i + 1 : i + 3] for i in binds] == [
        ["/tmp/nare-bash-x", "/tmp/nare-bash-x"],
        ["/work/root", "/work/root"],
    ]
    assert all(i > ro for i in binds)


def test_bwrap_isolates_pids_and_dies_with_nare() -> None:
    argv = _bwrap()
    assert "--unshare-pid" in argv
    assert "--die-with-parent" in argv
    assert "--proc" in argv


def test_bwrap_does_not_take_a_new_session() -> None:
    # The spawn already starts a session; a second setsid would move the shell
    # out of the group a timeout kills.
    assert "--new-session" not in _bwrap()


def test_bwrap_network_is_unshared_unless_allowed() -> None:
    assert "--unshare-net" in _bwrap(network=False)
    assert "--unshare-net" not in _bwrap(network=True)


def test_bwrap_masks_a_credential_folder_after_the_root_bind(tmp_path: Path) -> None:
    secret_dir = tmp_path / ".ssh"
    secret_dir.mkdir()
    argv = _bwrap(root=tmp_path, denied=(secret_dir,))
    mask = argv.index(str(secret_dir)) - 1
    assert argv[mask] == "--tmpfs"
    assert argv[mask + 2 : mask + 4] == ["--remount-ro", str(secret_dir)]
    assert mask > argv.index("--bind")


def test_bwrap_masks_a_credential_file_with_dev_null(tmp_path: Path) -> None:
    netrc = tmp_path / ".netrc"
    netrc.write_text("machine x password y\n", encoding="utf-8")
    argv = _bwrap(denied=(netrc,))
    i = argv.index(str(netrc)) - 2
    assert argv[i : i + 3] == ["--ro-bind", "/dev/null", str(netrc)]


def test_bwrap_runs_the_command_after_a_separator_and_chdirs_to_root() -> None:
    argv = _bwrap()
    assert argv[argv.index("--chdir") + 1] == "/work/root"
    sep = argv.index("--")
    assert argv[sep + 1 :] == ["bash", "-o", "pipefail", "-c", "true"]


def test_bwrap_leaves_the_host_tmp_read_only() -> None:
    # A writable /tmp would be a second place to write outside the root.
    assert "--tmpfs" not in _bwrap()


# Credential locations


def test_credential_paths_cover_the_usual_secrets(tmp_path: Path) -> None:
    paths = sandbox.credential_paths(tmp_path, tmp_path / "nare", "linux")
    for name in (".ssh", ".aws", ".gnupg", ".config/gh", ".netrc", ".npmrc"):
        assert tmp_path / name in paths
    assert tmp_path / "nare" in paths
    assert tmp_path / "Library" / "Keychains" not in paths


def test_credential_paths_add_the_keychain_on_macos(tmp_path: Path) -> None:
    paths = sandbox.credential_paths(tmp_path, tmp_path / "nare", "darwin")
    assert tmp_path / "Library" / "Keychains" in paths


def test_nare_home_is_listed_once_when_it_is_the_default(tmp_path: Path) -> None:
    paths = sandbox.credential_paths(tmp_path, tmp_path / ".nare", "linux")
    assert paths.count(tmp_path / ".nare") == 1


def test_fold_drops_a_path_that_sits_under_another() -> None:
    outer, inner, other = Path("/h/.nare"), Path("/h/.nare/home"), Path("/h/.ssh")
    assert sandbox.fold([outer, inner, other]) == (outer, other)


# Availability


def test_an_unsupported_platform_is_refused_clearly() -> None:
    found = sandbox._availability_for("win32", which=lambda _: None, run=_no_run)
    assert found.mechanism is None
    assert "macOS" in found.reason and "Linux" in found.reason


def test_linux_without_bwrap_names_the_missing_tool() -> None:
    found = sandbox._availability_for("linux", which=lambda _: None, run=_no_run)
    assert found.mechanism is None
    assert "bubblewrap" in found.reason


def test_a_failing_bwrap_probe_is_refused_with_its_reason() -> None:
    def failing(argv: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            argv, 1, stdout=b"", stderr=b"setting up uid map: Permission denied"
        )

    found = sandbox._availability_for(
        "linux", which=lambda _: "/usr/bin/bwrap", run=failing
    )
    assert found.mechanism is None
    assert "uid map" in found.reason


def test_a_working_bwrap_probe_is_available() -> None:
    def ok(argv: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

    found = sandbox._availability_for("linux", which=lambda _: "/usr/bin/bwrap", run=ok)
    assert found.available
    assert found.mechanism == "bubblewrap"


def _no_run(argv: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
    raise AssertionError(f"no probe should run, got {argv}")


# Policy validation


def test_the_sandbox_needs_a_root(available: None) -> None:
    with pytest.raises(ValueError, match="root"):
        Policy(bash_sandbox=True)


def test_the_sandbox_needs_the_bash_tool(available: None, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="bash"):
        Policy(bash_sandbox=True, root=tmp_path, tools=frozenset({"read"}))


def test_the_sandbox_refuses_a_root_of_slash(available: None) -> None:
    with pytest.raises(ValueError, match="/"):
        Policy(bash_sandbox=True, root=Path("/"))


def test_network_needs_the_sandbox(available: None, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="--bash-sandbox"):
        Policy(bash_network=True, root=tmp_path)


def test_a_requested_sandbox_that_cannot_start_is_refused_at_construction(
    unavailable: None, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="no OS sandbox for test"):
        Policy(bash_sandbox=True, root=tmp_path)


def test_a_sandbox_policy_that_is_available_constructs(
    available: None, tmp_path: Path
) -> None:
    policy = Policy(bash_sandbox=True, root=tmp_path)
    assert policy.bash_sandbox


def test_recorded_names_the_profile_in_force(available: None, tmp_path: Path) -> None:
    policy = Policy(bash_sandbox=True, root=tmp_path, bash_network=False)
    record = policy.recorded()["bash_sandbox"]
    assert record["mechanism"] == "seatbelt"
    assert record["profile"] == sandbox.PROFILE
    assert record["network"] is False
    assert record["writable"] == [str(tmp_path.resolve())]
    assert record["private_temp"] is True


def test_recorded_has_no_sandbox_key_when_it_is_off(tmp_path: Path) -> None:
    assert "bash_sandbox" not in Policy(root=tmp_path).recorded()


# Refusal at dispatch: nothing runs unconfined


async def test_dispatch_refuses_a_sandboxed_bash_call_when_the_sandbox_is_gone(
    available: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = Policy(bash_sandbox=True, root=tmp_path)
    monkeypatch.setattr(sandbox, "availability", lambda: UNAVAILABLE)
    ran: list[str] = []

    async def spy(*args: object, **kwargs: object) -> object:
        ran.append("ran")
        raise AssertionError("bash must not run")

    monkeypatch.setattr("nare.tools.bash_outcome", spy)
    result = await dispatch(
        ToolCall(id="c1", name="bash", args={"command": "echo hi"}),
        policy,
        approve=lambda *_: True,
    )
    assert result["is_error"] is True
    assert "not run" in result["content"]
    assert ran == []


async def test_bash_outcome_never_spawns_when_confinement_is_missing(
    unavailable: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_spawn(*args: object, **kwargs: object) -> object:
        raise AssertionError("a confined call must not spawn without a sandbox")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", no_spawn)
    confine = Confinement(root=tmp_path.resolve(), network=False, denied=())
    with pytest.raises(SandboxUnavailable):
        await bash_outcome("echo hi", 5, cwd=str(tmp_path), confine=confine)


def test_the_confined_argv_wraps_the_shell_for_the_platform(tmp_path: Path) -> None:
    confine = Confinement(root=tmp_path.resolve(), network=False, denied=())
    argv = confine.wrap(["bash", "-o", "pipefail", "-c", "true"], tmp_path, AVAILABLE)
    assert argv[:3] == ["sandbox-exec", "-p", argv[2]]
    assert argv[-5:] == ["bash", "-o", "pipefail", "-c", "true"]


def test_the_confined_argv_for_bubblewrap(tmp_path: Path) -> None:
    confine = Confinement(root=tmp_path.resolve(), network=False, denied=())
    argv = confine.wrap(
        ["bash", "-c", "true"],
        tmp_path,
        Availability(mechanism="bubblewrap", reason=""),
    )
    assert argv[0] == "bwrap"
    assert argv[-3:] == ["bash", "-c", "true"]


# CLI and contract


def test_the_cli_refuses_a_sandbox_that_cannot_start(
    unavailable: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "run",
            "--yes",
            "--provider",
            "fake",
            "--replay",
            "/dev/null",
            "--root",
            str(tmp_path),
            "--bash-sandbox",
            "x",
        ]
    )
    assert code == 2
    assert "no OS sandbox for test" in capsys.readouterr().err


def test_the_cli_passes_the_flags_to_the_policy(
    available: None, tmp_path: Path
) -> None:
    from nare.cli import build_parser, policy_from_args

    args = build_parser().parse_args(
        ["run", "--root", str(tmp_path), "--bash-sandbox", "--bash-network", "x"]
    )
    # validate_run_args sets these from their files; policy_from_args reads them.
    args.command_tools = []
    args.mcp_servers = {}
    policy = policy_from_args(args)
    assert policy.bash_sandbox is True
    assert policy.bash_network is True


def test_the_contract_reports_the_sandbox() -> None:
    from nare.contract import describe

    block = describe()["bash_sandbox"]
    assert block["flag"] == "--bash-sandbox"
    assert block["network_flag"] == "--bash-network"
    assert block["profile"] == sandbox.PROFILE
    assert isinstance(block["available"], bool)
    assert block["network"] == "off unless --bash-network"
    json.dumps(block)
