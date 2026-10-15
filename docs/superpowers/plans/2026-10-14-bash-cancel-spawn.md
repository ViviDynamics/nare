# Bash Cancel While the Shell Starts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A cancel that lands while asyncio is still starting a `bash` call kills the call's whole process group and raises `CancelledError`, instead of leaking background children or hanging (issue ViviDynamics/nare#78).

**Architecture:** `bash_outcome()` in `src/nare/tools.py` starts `asyncio.create_subprocess_exec(...)` as a task and awaits it through `asyncio.shield`, so a cancel never reaches asyncio's own spawn cleanup (which kills the shell alone, then waits on pipes a background child holds open). On `CancelledError` it waits the spawn out in a shielded loop, kills the group, reaps the shell, and re-raises the cancel, even if the spawn itself failed. Three deterministic tests in `tests/test_tools.py` hold the spawn open on an `asyncio.Event`, so the cancel lands mid-spawn every time, with or without load.

**Tech Stack:** Python (CI: 3.12 via `astral-sh/setup-uv`; this machine's uv venv: 3.14.2), asyncio subprocesses, pytest with `asyncio_mode = "auto"` (pytest-asyncio), ruff, mypy strict. CI is `bin/build`.

**Spec:** the issue body is the spec: `export GH_TOKEN=$(gh auth token); gh issue view 78 --repo ViviDynamics/nare`. Read it before Task 1. Decision 1 below changes how its third test makes the spawn fail.

## Global Constraints

- Work on branch `fix/78-cancel-spawn`, created from `origin/main` (at 0323427, PR #77 merged) in Task 1.
- Scope: `bash_outcome()` in `src/nare/tools.py`, plus three new tests and two test helpers in `tests/test_tools.py`. Touch nothing else. Do not edit any existing test.
- **Every test, trial or build runs under a hard timeout**, so nothing can hang: `timeout -k 10 120` for a pytest run of a few tests, `timeout -k 10 600` for `bin/build`, `timeout -k 10 540` for a load-check batch. When you run a command through a tool, also set the tool's own timeout above the command's (for example 600000 ms for `bin/build`). pytest-timeout is not installed, and adding it is a new dependency: use coreutils `timeout`.
- `bin/build` (ruff format --check, ruff check, mypy strict, pytest -q) passes before every commit. Run `uv run ruff format src tests` first; the build only checks formatting. CI baseline on main at 0323427: 767 passed, 8 deselected.
- No new dependencies. No new `skip` or `skipif` markers. Never disable, skip, delete or loosen a test.
- ruff: line length 88, rules E, F, I, UP, B. mypy strict covers `tests/` too, so test helpers need full annotations.
- No em dashes in code comments, commit messages, or any prose.
- Code comments follow the repo's habit: say why, cite the failure that was seen, and mark known limits with `ponytail:` plus the upgrade path.
- Stage only the files a task names (`git add src/nare/tools.py tests/test_tools.py`). Never `git add -A` or `git add .`: the tree holds an untracked `.bench-report/` and this plan file, and neither belongs in these commits.
- Commit after each of Tasks 1 to 3. Do not push, do not open a PR.
- Do not switch the project's `.venv` to Python 3.12 (`uv run --python 3.12` would rebuild it). Task 4 checks 3.12 in a separate scratch environment.
- A failing run of a new test (the "verify it fails" steps) can leave a `sleep 30` behind for up to 30 s. It exits on its own; do not pkill by pattern.

## Decisions

These are settled so every task agrees. A reviewer who disagrees should reject the task that owns the decision.

1. **Test 3 patches `asyncio.create_subprocess_exec`, not the loop's `connect_read_pipe` as the issue text says.** Why: CI runs Python 3.12, and no 3.12 release (3.12.3 through 3.12.13 checked) has asyncio's fix that finishes a spawn whose pipe connect failed. The fix (`_pipes_connected` in `asyncio/base_subprocess.py`) arrived in 3.14.1 and a late 3.13 (absent in 3.13.9 and 3.14.0, present in 3.13.14 and 3.14.1). On 3.12, when `connect_read_pipe` raises, asyncio's spawn awaits `transp._wait()` forever. The shielded loop from Task 2 then swallows `wait_for`'s cancel too, so the test as the issue words it hung forever on CI's Python. Patching `create_subprocess_exec` (looked up as `asyncio.create_subprocess_exec` at call time in `tools.py`) makes the spawn fail the same way on every Python. A trial on 3.12.3 and 3.14.2 gave the same results on both: `OSError` with the Task 1 and Task 2 code, and a pass with the Task 3 code. The user approved this change. The test name, its `true` command, its two Events and its assertion stay as the issue says. Owner: Task 3.
2. **A `ponytail:` comment records the 3.12 limit**: on Python 3.12, a spawn whose pipe connect fails never finishes, so a cancel then waits forever. The upgrade path is moving CI to Python 3.14.1 or later. The user approved this. Owner: Task 3.
3. **The spawn task is made with `asyncio.create_task`**, the repo's idiom (`loop.py`, `mcp.py`), not `asyncio.ensure_future` as in the trial patch. Owner: Task 1.
4. **The cancel path keeps its own `except asyncio.CancelledError` block** and does not fold into the existing `except BaseException`. Folding would also send a spawn's own failure (for example a cwd that does not exist, with no cancel) into a kill path that has no process. It would also blur the staged diffs that the acceptance criteria review. The kill and reap is three lines, repeated once. Owner: Task 1.
5. **Staged on purpose.** Each acceptance criterion says its test fails without its part. So Task 1 lands the shield with a bare `await spawn`, which Task 2 replaces with the loop, and Task 2's loop lets the spawn's error propagate until Task 3 re-raises the cancel. Do not write ahead to the final code in Task 1. Owners: Tasks 1 to 3.
6. **`spawn.cancelled()` is checked before `spawn.exception()`.** Loop shutdown (`asyncio.run` cancels every task) can cancel the spawn task too, and then `spawn.exception()` would raise. The check keeps the caller's own `CancelledError`. Owner: Task 3.
7. **Expected, not a bug:** test 3 passes on main, because main never waits for the spawn (the cancel lands inside it). It fails with the Task 1 and Task 2 code, which wait for the spawn and let its OSError through. That is why it comes last. Also, on 3.14 asyncio logs `OSError exception in shielded future` while test 3 runs, because the first shield was cancelled before the spawn failed. That log is expected and does not fail the test.
8. **The load check uses 3 busy loops per core, at least 30**, above the acceptance criterion's 20 on 10 cores. A trial with 20 got only 12 mid-spawn cancels in 300 runs; with 30 it got 170 in 700. Owner: Task 4.
9. **Two test helpers, `_hold_pipe_connects` and `_wait_for_pidfile`**, are shared by tests 1 and 2. The existing `test_a_cancel_kills_the_whole_process_group` stays exactly as it is. Owner: Task 1.

## Review Focus

1. **A bash call that is never cancelled** must behave exactly as before: output, exit status, timeout, still-running probe. Covered by the existing bash tests in `tests/test_tools.py`, which run in every task's `bin/build`.
2. **A cancel after the spawn finished** (mid-command, or during the still-running probe) must still kill the group. Covered by the existing `test_a_cancel_kills_the_whole_process_group` and `test_a_cancel_while_probing_for_a_group_left_running_kills_it`, run in each task's Step 4.
3. **A spawn that fails with no cancel** (a cwd that does not exist) must still raise its own error, so `dispatch` reports it. `await asyncio.shield(spawn)` re-raises the spawn's error, and the new block catches only `CancelledError`. No test pins this and the issue fixes the scope at three tests, so Task 4 Step 4 checks it by hand.
4. **A timeout** must still raise `subprocess.TimeoutExpired` and kill the group. Covered by the existing `test_a_timeout_kills_the_whole_process_group`, run in each task's Step 4.
5. **CI's Python 3.12**, where asyncio's subprocess code differs from this machine's 3.14.2. Task 4 Step 5 runs the new tests under `/usr/bin/python3.12` in a scratch environment.

---

## File map

| File | Responsibility | Tasks |
| --- | --- | --- |
| `src/nare/tools.py` | `bash_outcome()`: shielded spawn, kill and reap on a cancel | 1, 2, 3 |
| `tests/test_tools.py` | `_hold_pipe_connects`, `_wait_for_pidfile`, three new tests after `test_a_cancel_kills_the_whole_process_group` | 1, 2, 3 |

---

### Task 1: Shield the spawn so a cancel kills the group

**Files:**
- Modify: `src/nare/tools.py:294-301` (the `proc = await asyncio.create_subprocess_exec(...)` statement in `bash_outcome()`)
- Test: `tests/test_tools.py` (insert after `test_a_cancel_kills_the_whole_process_group`, which ends near line 292, before `test_commands_do_not_inherit_nares_stdin`)

**Interfaces:**
- Consumes: `run_bash(command: str, timeout: int = 120, *, cwd: str | None = None) -> str` and the test module's existing `_gone(pid: int) -> bool`. Both are unchanged.
- Produces (test helpers that Task 2 reuses):
  - `_hold_pipe_connects(monkeypatch: pytest.MonkeyPatch) -> asyncio.Event`
  - `async def _wait_for_pidfile(pidfile: Path) -> None`
- `bash_outcome()`'s signature and return type do not change.

- [ ] **Step 1: Create the branch**

```bash
cd /home/smadinya/vividynamics/nare
git fetch origin
git switch -c fix/78-cancel-spawn origin/main
git log --oneline -1
```

Expected: `0323427 fix: bash reports what really happened and what it left running (#77)` (or a later origin/main commit). The untracked `.bench-report/` and this plan come along untracked; leave them alone.

- [ ] **Step 2: Write the failing test and its helpers**

Insert into `tests/test_tools.py` directly after `test_a_cancel_kills_the_whole_process_group`. `asyncio`, `Any`, `Path` and `pytest` are already imported.

```python
def _hold_pipe_connects(monkeypatch: pytest.MonkeyPatch) -> asyncio.Event:
    """Hold asyncio's pipe connects for the next spawn until the Event is set.

    Popen has started the shell by then, so a cancel lands mid-spawn every
    time, with or without load.
    """
    loop = asyncio.get_running_loop()
    real = loop.connect_read_pipe
    released = asyncio.Event()

    async def held(*args: Any, **kwargs: Any) -> Any:
        await released.wait()
        return await real(*args, **kwargs)

    monkeypatch.setattr(loop, "connect_read_pipe", held)
    return released


async def _wait_for_pidfile(pidfile: Path) -> None:
    for _ in range(500):
        if pidfile.exists() and pidfile.read_text().strip():
            return
        await asyncio.sleep(0.01)


async def test_a_cancel_while_asyncio_connects_the_pipes_kills_the_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A cancel while asyncio connected the pipes met asyncio's own cleanup,
    # which kills the shell alone, then waits on the pipes the background
    # sleep holds open. Under load 1 cancel in 4 hung or left the sleep.
    released = _hold_pipe_connects(monkeypatch)
    pidfile = tmp_path / "pid"
    task = asyncio.create_task(run_bash(f"sleep 30 & echo $! > {pidfile}; wait"))
    await _wait_for_pidfile(pidfile)
    task.cancel()
    released.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert _gone(int(pidfile.read_text()))
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
cd /home/smadinya/vividynamics/nare
timeout -k 10 120 uv run pytest -q tests/test_tools.py -k test_a_cancel_while_asyncio_connects_the_pipes_kills_the_group 2>&1 | tail -15
```

Expected: 1 failed, after about 5 s, with `TimeoutError` from `asyncio.wait_for` (3 of 3 in trials). Rarely it fails instead at `assert _gone(...)` after about 5 more seconds. Either one is the bug. If it passes, stop and report: the test is not holding the spawn.

- [ ] **Step 4: Shield the spawn (minimal: a bare `await spawn` after a cancel)**

In `src/nare/tools.py`, `bash_outcome()`, replace exactly this statement:

```python
    proc = await asyncio.create_subprocess_exec(
        *_shell(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        cwd=cwd,
    )
```

with:

```python
    spawn = asyncio.create_task(
        asyncio.create_subprocess_exec(
            *_shell(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=cwd,
        )
    )
    try:
        # Shielded: Popen has started the shell before asyncio connects its
        # pipes. asyncio's own cleanup for a cancel there kills the shell
        # alone, then waits on pipes a background child holds open, so under
        # load 1 cancel in 4 hung the cancel or left `sleep 30 &` running.
        proc = await asyncio.shield(spawn)
    except asyncio.CancelledError:
        proc = await spawn
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        await proc.wait()
        raise
```

Leave the existing `try: out, err = await asyncio.wait_for(proc.communicate(), timeout)` block and everything after it unchanged. `contextlib`, `os` and `signal` are already imported.

- [ ] **Step 5: Run the new test and the existing cancel and timeout tests**

```bash
cd /home/smadinya/vividynamics/nare
timeout -k 10 120 uv run pytest -q tests/test_tools.py -k "cancel or timeout" 2>&1 | tail -5
```

Expected: all pass, including `test_a_cancel_while_asyncio_connects_the_pipes_kills_the_group`, `test_a_cancel_kills_the_whole_process_group`, `test_a_timeout_kills_the_whole_process_group` and `test_a_cancel_while_probing_for_a_group_left_running_kills_it`.

- [ ] **Step 6: Format and run the build**

```bash
cd /home/smadinya/vividynamics/nare
uv run ruff format src tests
timeout -k 10 600 bin/build 2>&1 | tail -5
```

Expected: ruff and mypy clean, pytest `N passed` with no failures (one more than before this task).

- [ ] **Step 7: Commit**

```bash
cd /home/smadinya/vividynamics/nare
git add src/nare/tools.py tests/test_tools.py
git commit -m "fix: kill the group when a cancel lands while bash starts"
```

---

### Task 2: A second cancel must not reach the spawn

**Files:**
- Modify: `src/nare/tools.py` (the `except asyncio.CancelledError:` block Task 1 added in `bash_outcome()`)
- Test: `tests/test_tools.py` (insert after `test_a_cancel_while_asyncio_connects_the_pipes_kills_the_group`)

**Interfaces:**
- Consumes (from Task 1, in `tests/test_tools.py`): `_hold_pipe_connects(monkeypatch: pytest.MonkeyPatch) -> asyncio.Event`, `async def _wait_for_pidfile(pidfile: Path) -> None`, and the existing `_gone(pid: int) -> bool`. In `tools.py`: the local `spawn` task (`asyncio.Task[asyncio.subprocess.Process]`) that Task 1 created.
- Produces: nothing new for later tasks. Task 3 edits the same `except` block.

- [ ] **Step 1: Write the failing test**

Insert into `tests/test_tools.py` directly after `test_a_cancel_while_asyncio_connects_the_pipes_kills_the_group`:

```python
async def test_a_second_cancel_while_the_pipes_connect_still_kills_the_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # After a cancel the call waits for its spawn, to kill what it started. A
    # second cancel that reached the spawn there handed it to asyncio's own
    # cleanup after all: the sleep ran on, or held up the cancel.
    released = _hold_pipe_connects(monkeypatch)
    pidfile = tmp_path / "pid"
    task = asyncio.create_task(run_bash(f"sleep 30 & echo $! > {pidfile}; wait"))
    await _wait_for_pidfile(pidfile)
    task.cancel()
    # One yield delivers the first cancel, so the call is waiting on its
    # spawn when the second one comes.
    await asyncio.sleep(0)
    task.cancel()
    released.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert _gone(int(pidfile.read_text()))
```

Why one `asyncio.sleep(0)` is enough: `task.cancel()` cancels the shield's outer future, which queues the call's wakeup before the test's own. So the call runs first, enters the `except` block and awaits the spawn before the test cancels again. Two cancels with no yield between them collapse into one.

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /home/smadinya/vividynamics/nare
timeout -k 10 120 uv run pytest -q tests/test_tools.py -k test_a_second_cancel_while_the_pipes_connect_still_kills_the_group 2>&1 | tail -15
```

Expected: 1 failed, with `TimeoutError` from `asyncio.wait_for` or at `assert _gone(...)`. In trials, Task 1's bare `await spawn` failed it 3 of 3 times (2 TimeoutError, 1 orphan). If it passes, stop and report.

- [ ] **Step 3: Loop over the shielded await instead of a bare `await spawn`**

In `bash_outcome()`, replace the `except` block from Task 1:

```python
    except asyncio.CancelledError:
        proc = await spawn
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        await proc.wait()
        raise
```

with:

```python
    except asyncio.CancelledError:
        # Wait the spawn out, then kill what it started. A loop, not a bare
        # `await spawn`: a second cancel would reach the spawn and hand it to
        # asyncio's own cleanup after all.
        while not spawn.done():
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(spawn)
        proc = spawn.result()
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        await proc.wait()
        raise
```

The bare `raise` re-raises the first `CancelledError`: once the inner `suppress` exits, Python restores the exception being handled.

- [ ] **Step 4: Run the new tests and the existing cancel and timeout tests**

```bash
cd /home/smadinya/vividynamics/nare
timeout -k 10 120 uv run pytest -q tests/test_tools.py -k "cancel or timeout" 2>&1 | tail -5
```

Expected: all pass, including both new tests.

- [ ] **Step 5: Format and run the build**

```bash
cd /home/smadinya/vividynamics/nare
uv run ruff format src tests
timeout -k 10 600 bin/build 2>&1 | tail -5
```

Expected: all clean, with one more test passed than after Task 1.

- [ ] **Step 6: Commit**

```bash
cd /home/smadinya/vividynamics/nare
git add src/nare/tools.py tests/test_tools.py
git commit -m "fix: a second cancel while bash starts still kills the group"
```

---

### Task 3: A spawn that fails after a cancel still raises the cancel

**Files:**
- Modify: `src/nare/tools.py` (the `except asyncio.CancelledError:` block in `bash_outcome()`, as Task 2 left it)
- Test: `tests/test_tools.py` (insert after `test_a_second_cancel_while_the_pipes_connect_still_kills_the_group`)

**Interfaces:**
- Consumes: `run_bash` (unchanged). In `tools.py`: the `spawn` task and the shielded loop from Task 2. `bash_outcome()` must keep calling the spawn as `asyncio.create_subprocess_exec(...)` (a module attribute lookup at call time), because this test patches it there.
- Produces: the final `bash_outcome()`. Task 4 only verifies it.

- [ ] **Step 1: Write the failing test**

Insert into `tests/test_tools.py` directly after `test_a_second_cancel_while_the_pipes_connect_still_kills_the_group`:

```python
async def test_a_spawn_that_fails_after_a_cancel_still_raises_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A cancelled call waits for its spawn so it can kill the group. A spawn
    # that then failed raised its own OSError, so the caller, who asked to
    # cancel, never heard that the call was cancelled.
    # The spawn fails here, not in connect_read_pipe as #78 first said: on
    # Python 3.12, which CI runs, asyncio's spawn never finishes once a pipe
    # connect fails, and the test hung.
    entered = asyncio.Event()
    released = asyncio.Event()

    async def failing(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        await released.wait()
        raise OSError("spawn failed")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", failing)
    task = asyncio.create_task(run_bash("true"))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    released.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /home/smadinya/vividynamics/nare
timeout -k 10 120 uv run pytest -q tests/test_tools.py -k test_a_spawn_that_fails_after_a_cancel_still_raises_cancelled 2>&1 | tail -15
```

Expected: 1 failed, at once, with `OSError: spawn failed` (3 of 3 in trials with the Task 2 code). An `OSError exception in shielded future` line in the captured log is expected (Decision 7). If it passes, stop and report.

- [ ] **Step 3: Re-raise the cancel when the spawn fails, and record the 3.12 limit**

In `bash_outcome()`, replace the `except` block from Task 2:

```python
    except asyncio.CancelledError:
        # Wait the spawn out, then kill what it started. A loop, not a bare
        # `await spawn`: a second cancel would reach the spawn and hand it to
        # asyncio's own cleanup after all.
        while not spawn.done():
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(spawn)
        proc = spawn.result()
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        await proc.wait()
        raise
```

with:

```python
    except asyncio.CancelledError:
        # Wait the spawn out, then kill what it started. A loop, not a bare
        # `await spawn`: a second cancel would reach the spawn and hand it to
        # asyncio's own cleanup after all.
        # ponytail: on Python 3.12, which CI runs, asyncio's spawn never
        # finishes once a pipe connect fails, so this loop, and the cancel,
        # wait forever. The upgrade is Python 3.14.1 or later, whose asyncio
        # finishes that spawn.
        while not spawn.done():
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.shield(spawn)
        # A failed spawn leaves no process here: asyncio's own cleanup has
        # killed the shell. The caller asked to cancel, so it hears that,
        # not how a spawn it no longer wants went wrong.
        if not spawn.cancelled() and spawn.exception() is None:
            proc = spawn.result()
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            await proc.wait()
        raise
```

For reference, the whole spawn section of `bash_outcome()` now reads, from `spawn = asyncio.create_task(` down to the untouched `try: out, err = ...`:

```python
    spawn = asyncio.create_task(
        asyncio.create_subprocess_exec(
            *_shell(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=cwd,
        )
    )
    try:
        # Shielded: Popen has started the shell before asyncio connects its
        # pipes. asyncio's own cleanup for a cancel there kills the shell
        # alone, then waits on pipes a background child holds open, so under
        # load 1 cancel in 4 hung the cancel or left `sleep 30 &` running.
        proc = await asyncio.shield(spawn)
    except asyncio.CancelledError:
        # Wait the spawn out, then kill what it started. A loop, not a bare
        # `await spawn`: a second cancel would reach the spawn and hand it to
        # asyncio's own cleanup after all.
        # ponytail: on Python 3.12, which CI runs, asyncio's spawn never
        # finishes once a pipe connect fails, so this loop, and the cancel,
        # wait forever. The upgrade is Python 3.14.1 or later, whose asyncio
        # finishes that spawn.
        while not spawn.done():
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.shield(spawn)
        # A failed spawn leaves no process here: asyncio's own cleanup has
        # killed the shell. The caller asked to cancel, so it hears that,
        # not how a spawn it no longer wants went wrong.
        if not spawn.cancelled() and spawn.exception() is None:
            proc = spawn.result()
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            await proc.wait()
        raise
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
```

Everything from that second `try:` on is unchanged from main.

- [ ] **Step 4: Run all three new tests and the existing cancel and timeout tests**

```bash
cd /home/smadinya/vividynamics/nare
timeout -k 10 120 uv run pytest -q tests/test_tools.py -k "cancel or timeout or spawn" 2>&1 | tail -5
```

Expected: all pass, including all three new tests.

- [ ] **Step 5: Format and run the build**

```bash
cd /home/smadinya/vividynamics/nare
uv run ruff format src tests
timeout -k 10 600 bin/build 2>&1 | tail -5
```

Expected: all clean. pytest shows three more passed than on main (770 in CI terms, if nothing else changed on origin/main).

- [ ] **Step 6: Commit**

```bash
cd /home/smadinya/vividynamics/nare
git add src/nare/tools.py tests/test_tools.py
git commit -m "fix: a cancelled bash call whose spawn fails still raises the cancel"
```

---

### Task 4: Verify under load, on Python 3.12, and with the full build

This task changes no files and makes no commit. Report its numbers to your caller; the PR body needs them.

**Files:**
- Create (scratch only, never in the repo): `<scratch>/load_check.py`, where `<scratch>` is your session's scratchpad directory (or `mktemp -d` if you have none).

**Interfaces:**
- Consumes: the final `bash_outcome()` from Task 3, through `nare.tools.run_bash`.
- Produces: the load-check counts, and the results of the 3.12 run and the bad-cwd check, for the PR.

- [ ] **Step 1: Write the load-check script**

It is the body of `test_a_cancel_kills_the_whole_process_group`, run N times. It also records whether each cancel landed while asyncio was still starting the shell ("mid-spawn"). Save as `<scratch>/load_check.py`:

```python
"""Load check for #78: the body of test_a_cancel_kills_the_whole_process_group.

Run under CPU load. mid-spawn=True means the cancel landed while asyncio was
still starting the shell, the window #78 fixes.
"""

import asyncio
import os
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

import nare.tools as tools
from nare.tools import run_bash

state = {"spawning": False}
real_exec = asyncio.create_subprocess_exec


async def spawn(*args, **kwargs):
    state["spawning"] = True
    try:
        return await real_exec(*args, **kwargs)
    finally:
        state["spawning"] = False


asyncio.create_subprocess_exec = spawn


def gone(pid):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return True
        time.sleep(0.02)
    return False


async def once(tmp):
    pidfile = tmp / "pid"
    task = asyncio.create_task(run_bash(f"sleep 30 & echo $! > {pidfile}; wait"))
    for _ in range(500):
        if pidfile.exists() and pidfile.read_text().strip():
            break
        await asyncio.sleep(0.01)
    mid = state["spawning"]
    task.cancel()
    try:
        await asyncio.wait_for(task, 5)
    except asyncio.CancelledError:
        pass
    except TimeoutError:
        os.kill(int(pidfile.read_text()), 9)
        return "FAIL hung", mid
    else:
        return "FAIL no CancelledError", mid
    pid = int(pidfile.read_text())
    if gone(pid):
        return "pass", mid
    os.kill(pid, 9)
    return "FAIL orphan", mid


async def main(n):
    results = Counter()
    for _ in range(n):
        with tempfile.TemporaryDirectory() as d:
            outcome, mid = await once(Path(d))
        results[f"{outcome} | mid-spawn={mid}"] += 1
    print(tools.__file__)
    for key, count in sorted(results.items()):
        print(f"{count:4d}  {key}")


asyncio.run(main(int(sys.argv[1])))
```

- [ ] **Step 2: Run 1,000 runs under load, as two batches of 500**

Each batch starts its own busy loops (3 per core, at least 30; Decision 8), runs 500 checks under a hard timeout, then kills the busy loops. The busy loops also carry their own `timeout`, so they die even if the kill is missed. Run this command once with `B=1`, then again with `B=2`. Give the tool a 600000 ms timeout.

```bash
cd /home/smadinya/vividynamics/nare
S=<scratch>; B=1
n=$(( $(nproc) * 3 )); [ "$n" -lt 30 ] && n=30
hogs=()
for i in $(seq "$n"); do timeout -k 5 600 sh -c 'while :; do :; done' & hogs+=($!); done
timeout -k 10 540 uv run python "$S/load_check.py" 500 > "$S/load_check_$B.out" 2>&1
echo "exit $?"
kill "${hogs[@]}" 2>/dev/null; wait 2>/dev/null
tail -20 "$S/load_check_$B.out"
```

Expected for each batch: `exit 0`. The first line is `/home/smadinya/vividynamics/nare/src/nare/tools.py` (this checkout, not some other install). Every count line says `pass`, split across `mid-spawn=False` and `mid-spawn=True`, and the counts sum to 500.

Acceptance: 0 `FAIL` lines across both batches (1,000 runs). Add up the `mid-spawn=True` counts and report them (a trial of the shield had 182 in 1,000). If both batches show 0 mid-spawn runs, the load never reached the window: rerun both batches with `n=$(( $(nproc) * 5 ))` and report that. If `exit` is 124 or 137, the batch ran out of time (the script prints only at the end). Do not raise the timeout: run four batches of 250 instead (`B=1` to `4`, with `250` in place of `500`), and report that you did. If a batch of 250 also runs out of time, stop and report it as a possible hang.

- [ ] **Step 3: Confirm no busy loop or sleep is left**

```bash
pgrep -c -f 'while :[;] do' || true
pgrep -c -x sleep || true
```

The `[;]` keeps `pgrep -f` from matching the shell that runs this command. Expected: `0` for the first. The second may count unrelated sleeps; if it is not 0, check with `ps -o pid,etimes,args -C sleep` that none is a `sleep 30` older than 30 s.

- [ ] **Step 4: A spawn that fails with no cancel still raises its own error (Review Focus 3)**

```bash
cd /home/smadinya/vividynamics/nare
timeout -k 10 60 uv run python -c "
import asyncio
from nare.tools import run_bash
try:
    asyncio.run(run_bash('true', cwd='/nonexistent-78'))
except FileNotFoundError as exc:
    print('ok', type(exc).__name__)
"
```

Expected: `ok FileNotFoundError`.

- [ ] **Step 5: Run the new tests on Python 3.12, as CI does (Review Focus 5)**

This uses a scratch environment, so the project's `.venv` stays on 3.14. `--frozen` keeps `uv.lock` unchanged.

```bash
cd /home/smadinya/vividynamics/nare
UV_PROJECT_ENVIRONMENT=<scratch>/venv312 timeout -k 10 600 uv run --frozen --python /usr/bin/python3.12 pytest -q tests/test_tools.py -k "cancel or timeout or spawn" 2>&1 | tail -5
git status --short
```

Expected: all pass, and `git status` shows only the untracked `.bench-report/` and this plan. If uv cannot build the environment (for example no network for a wheel), report that and go on. CI will run 3.12 anyway.

- [ ] **Step 6: Final build on the branch**

```bash
cd /home/smadinya/vividynamics/nare
timeout -k 10 600 bin/build 2>&1 | tail -5
git log --oneline origin/main..HEAD
```

Expected: `bin/build` clean, and exactly three commits on the branch (Tasks 1 to 3).

---

## Acceptance criteria map

| Criterion from #78 | Where |
| --- | --- |
| `test_a_cancel_while_asyncio_connects_the_pipes_kills_the_group` fails without the shield, passes with it | Task 1, Steps 3 and 5 |
| `test_a_second_cancel_while_the_pipes_connect_still_kills_the_group` fails with a bare `await spawn`, passes with the loop | Task 2, Steps 2 and 4 |
| `test_a_spawn_that_fails_after_a_cancel_still_raises_cancelled` fails when the spawn's error propagates (OSError), passes when the cancel is re-raised | Task 3, Steps 2 and 4 (injection per Decision 1) |
| 0 failures in 1,000 runs of `test_a_cancel_kills_the_whole_process_group`'s body under 20+ busy loops on 10 cores | Task 4, Step 2 |
| `bin/build` passes | Tasks 1 to 3, build steps, and Task 4, Step 6 |
