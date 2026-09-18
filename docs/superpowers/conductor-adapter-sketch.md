# The Conductor adapter, sketched

Evidence that slice 1's contract holds — not shipped code, and not slice 1's
job to wire in. Written against `performer/backends/base.py`. The point is the
line count: the eight existing adapters average 668 lines.

```python
"""nare backend. The harness emits what the performer needs, so this adapter
transports bytes rather than reconstructing meaning."""

import asyncio
import json
from pathlib import Path

from performer.backends.base import BackendAdapter, BackendStatus
from performer.models import BackendEvent

_STATE = {"done": "completed", "blocked": "blocked", "error": "failed",
          "working": "running"}


class NareAdapter(BackendAdapter):
    def __init__(self, workdir: Path) -> None:
        self._workdir = workdir
        self._session = workdir / ".nare-session.json"
        self._proc: asyncio.subprocess.Process | None = None
        self._events: list[BackendEvent] = []
        self._result: dict | None = None
        self._pump: asyncio.Task | None = None

    async def start(self, stand, score, *, model=None, effort=None,
                    temperature=None, max_tokens=None) -> None:
        argv = ["nare", "run", "--yes", "--jsonl",
                "--session", str(self._session)]
        if self._session.exists():
            argv += ["--resume", str(self._session)]
        if score.persona:
            argv += ["--system", score.persona]
        for flag, value in (("--model", model), ("--effort", effort),
                            ("--max-tokens", max_tokens)):
            if value is not None:
                argv += [flag, str(value)]
        # temperature is deliberately NOT forwarded. anthropic 1.6.0 removed it
        # from messages.create, and nare refuses the flag rather than silently
        # dropping it -- so passing it through would exit 2 before the run
        # starts. Forward it once a provider that accepts it exists.
        argv.append(score.prompt)

        self._proc = await asyncio.create_subprocess_exec(
            *argv, cwd=self._workdir, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True,
        )
        self._pump = asyncio.create_task(self._read())

    async def _read(self) -> None:
        assert self._proc and self._proc.stdout
        async for raw in self._proc.stdout:
            payload = json.loads(raw)
            # The one branch in the whole adapter: the terminal line is state,
            # every other line is an event.
            if payload.get("type") == "result":
                self._result = payload
            else:
                self._events.append(BackendEvent(**payload))

    def drain_events(self) -> list[BackendEvent]:
        drained, self._events = self._events, []
        return drained

    def get_status(self) -> BackendStatus:
        r = self._result
        if r is None:
            return BackendStatus(state="running")
        usage = r["usage"]
        return BackendStatus(
            state=_STATE[r["status"]],
            questions=r["questions"],
            error_reason=r["error"],
            stop_reason=r["stop_reason"],
            tokens_processed=usage["input"] + usage["output"],
        )

    async def relay_feedback(self, feedback: str) -> None:
        # --resume is picked up by start(); the session file is already there.
        await self.stop()
        self._result = None
        await self.start(self._stand, self._score.with_prompt(feedback))

    async def stop(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            await self._proc.wait()
        if self._pump:
            self._pump.cancel()
```

## What slice 1 supplies, and what slice 2 still owes

Free from slice 1: `state`, `questions`, `stop_reason`, token counts,
`drain_events()` as a constructor call, `relay_feedback()` as resume, and
`stop()` as a process kill.

Still owed by slice 2: `output` (slice 1 says "the final assistant text";
decide then whether a `submit` tool earns its place), cost in dollars,
compaction, the `SUPPORTED_BACKENDS` entry, `"nare": "nare"` in
`capabilities.py:_BACKEND_BINARIES`, and which Dockerfile variants carry it.

Two contract details this sketch depends on: the `result` line is not a
`BackendEvent` and must be filtered, and a run that never starts emits no
result line at all — a failure to spawn surfaces through the exit code, not
through `status=error`.

One live integration consequence: `start()` accepts a `temperature`, and this
sketch drops it on the floor for the anthropic provider, because the vendor
removed the parameter and nare refuses the flag instead of ignoring it. Slice 2
has to decide what the performer does with a per-role temperature it cannot
honour — surface it to the operator, or hold it until a provider that accepts
it lands. Quietly discarding it is the one option this project's premise rules
out.
