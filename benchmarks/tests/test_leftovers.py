from __future__ import annotations

import json
from pathlib import Path

from benchmarks.runner.leftovers import database_files, harness, processes, snapshot


def fake_process(
    proc: Path,
    pid: int,
    args: list[str],
    *,
    ppid: int = 1,
    pgid: int = 0,
    state: str = "S",
) -> None:
    entry = proc / str(pid)
    entry.mkdir(parents=True)
    # comm can hold spaces and ")", which is why the real parser splits after
    # the last one.
    comm = f"({args[0][:15]} x)"
    entry.joinpath("stat").write_text(
        f"{pid} {comm} {state} {ppid} {pgid or pid} {pgid or pid} 0 -1 4194560\n"
    )
    entry.joinpath("cmdline").write_bytes(b"".join(a.encode() + b"\0" for a in args))


def test_processes_lists_what_is_still_running(tmp_path: Path) -> None:
    fake_process(tmp_path, 1, ["sh", "-c", "the agent script"])
    fake_process(tmp_path, 40, ["postgres", "-D", "/tmp/pg"], pgid=40)
    fake_process(tmp_path, 41, ["postgres: checkpointer"], ppid=40, pgid=40)
    assert processes(tmp_path, skip={1}) == [
        {"pid": 40, "pgid": 40, "args": "postgres -D /tmp/pg"},
        {"pid": 41, "pgid": 40, "args": "postgres: checkpointer"},
    ]


def test_processes_leaves_out_zombies(tmp_path: Path) -> None:
    # The script's shell is PID 1 and reaps nothing, so an orphan that
    # finished stays in /proc as a zombie. It is not running.
    fake_process(tmp_path, 7, ["python", "app.py"], state="Z")
    assert processes(tmp_path, skip=set()) == []


def test_processes_ignores_entries_that_are_not_processes(tmp_path: Path) -> None:
    (tmp_path / "self").mkdir()
    (tmp_path / "meminfo").write_text("")
    # A process that exits between the listing and the read.
    (tmp_path / "99").mkdir()
    assert processes(tmp_path, skip=set()) == []


def test_harness_is_the_snapshot_and_everything_above_it(tmp_path: Path) -> None:
    fake_process(tmp_path, 1, ["sh"], ppid=0)
    fake_process(tmp_path, 5, ["python3"], ppid=1)
    fake_process(tmp_path, 6, ["sleep", "300"], ppid=1)
    assert harness(tmp_path, 5) == {1, 5}


def test_database_files_finds_a_server_install_or_data_directory(
    tmp_path: Path,
) -> None:
    made = [
        "tmp/pgserver/pginstall/bin/initdb",
        "tmp/conda/envs/pg/bin/pg_ctl",
        "usr/lib/postgresql/16/bin/postgres",
        "work/data/PG_VERSION",
    ]
    for name in [*made, "usr/lib/python3/site-packages/psycopg/libpq.so.5"]:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("")
    assert database_files(tmp_path, skip=()) == sorted(
        str(tmp_path / name) for name in made
    )


def test_database_files_skips_what_it_is_told_to(tmp_path: Path) -> None:
    (tmp_path / "proc" / "1").mkdir(parents=True)
    (tmp_path / "proc" / "1" / "postgres").write_text("")
    assert database_files(tmp_path, skip=(tmp_path / "proc",)) == []


def test_snapshot_writes_both_lists(tmp_path: Path) -> None:
    proc = tmp_path / "proc"
    fake_process(proc, 1, ["sh"], ppid=0)
    fake_process(proc, 2, ["python3", "-c", "..."], ppid=1)
    fake_process(proc, 3, ["flask", "run"], ppid=1)
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "PG_VERSION").write_text("16\n")
    out = tmp_path / "after.json"
    snapshot(out, proc=proc, root=tmp_path, pid=2)
    assert json.loads(out.read_text()) == {
        "processes": [{"pid": 3, "pgid": 3, "args": "flask run"}],
        "database_files": [str(tmp_path / "work" / "PG_VERSION")],
    }
