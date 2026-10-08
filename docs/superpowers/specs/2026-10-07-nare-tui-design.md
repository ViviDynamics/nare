# nare - A Terminal UI

**Date:** 2026-10-07
**Status:** Draft for review
**Scope:** Slice 3 of the walking-skeleton roadmap ("Interactive CLI and TUI"),
narrowed to what is listed in section 2. Adds `nare tui` behind an optional
extra and four small, additive changes to the core. The machine contract does
not change.

## 1. Purpose

nare has one surface today, `nare run`, built for a caller that is a program.
This spec adds a surface for a person, used two ways:

1. **Driving nare interactively.** Type a task, watch it stream, approve or
   deny each tool call, see diffs, answer `ask` questions, and keep going in
   the same session.
2. **Watching a run something else started.** Point the TUI at the session
   file of a run that Conductor or the benchmark suite is executing, and see
   it advance turn by turn. A finished run is the same view, one that has
   stopped growing.

Success: a developer can do real work in `nare tui` without reaching for
`--yes`, and can see what a spawned run is doing, or why it blocked, without
reading JSON by hand.

## 2. Scope

In:

- Interactive runs with streaming output.
- Per-call approval prompts with y / n / "always for this tool".
- Diffs for `edit` and `write`, in the approval prompt and in the transcript.
- Interrupting a turn without losing the session.
- Follow-up messages after a run ends, and inline answers to `ask`.
- A status bar: model, turns, tokens, cost, budget, state.
- `--attach PATH`: a read-only, turn-level live view of a session file.

Out, each until it is asked for:

- A session picker.
- Typing ahead while a run is working.
- Approval rules that persist beyond one TUI process.
- Token-level live view of another process's run. It needs a new output
  channel from `nare run`; see section 9.
- `--schema` in the TUI. Schema-constrained answers are for programs.
- Themes, config files, key remapping.

## 3. Decisions

1. **In-process, not a subprocess.** The TUI imports the library and calls
   `run()` with its own approver. A subprocess cannot stop mid-turn to ask a
   person about a tool call without a new two-way protocol. ADR 0003 already
   holds that in-process import "remains available at no extra cost";
   Conductor keeps spawning `nare run`.
2. **Textual, as an optional extra.** The persistent status bar, modal
   prompts over a streaming transcript, cancelling a turn on a key, and a
   view that refreshes from a file are all Textual's default way of working,
   and Textual is asyncio-native, so `run()`'s async generator feeds it
   directly. It ships a headless test driver (`Pilot`). It is installed only
   by `nare[tui]`: `pip install nare`, the container image, and the bench
   images are unchanged. A stdlib REPL was considered and rejected: it has
   no persistent status bar, and its attach view would be a reprint loop.
3. **One renderer, drawn from messages.** Tool results are never events,
   only messages, so the transcript is rendered from `session.messages`. The
   interactive view and the attach view share that renderer.
4. **Attach polls the session file.** `nare run` already rewrites it
   atomically after every turn, so a turn-level view costs no new contract
   surface and works on any run given `--session`, including one inside a
   container with a mounted workdir.

## 4. Architecture

```
cli.py ---- nare run   (unchanged behaviour)
       `--- nare tui   imports nare.tui lazily
                       |
                       v
tui/app.py       Textual App: transcript, input, status bar, key bindings
tui/approve.py   the async approver: y / n / a, the "always" set, exemptions
tui/render.py    pure: Message and Event -> Rich renderables; diffs via difflib
tui/attach.py    pure: poll a session file, report what changed
```

`render.py`, `approve.py`'s decision logic, and `attach.py` do not import
Textual and are tested as plain units. Only `app.py` needs `Pilot`.

Without the extra installed, `nare tui` prints
`nare tui needs the tui extra: uv tool install 'nare[tui]'` to stderr and
exits 2.

### 4.1 Changes to the core

All four are additive. None changes the event stream, the `result` line, the
session file's shape, or an exit code, so the contract version stands.

1. **`Approve` may be async.** `tools.Approve` becomes
   `Callable[[str, dict[str, Any]], bool | Awaitable[bool]]`. `dispatch()`
   awaits the result when it is awaitable. `approve_all` and every existing
   caller are unchanged. ADR 0004 gets a note recording that the seam it
   reserved for this slice now takes an async callable.
2. **`reopen(session, text)` and `save(session, path)` move into
   `session.py`.** `reopen` is the reset `cli._load_or_new` performs on
   resume today (append user text; reset `status` to `working` and clear
   `questions`, `schema_retried`, `error`, `stop_reason`). `output` is kept:
   an exhausted resume still reports its partial findings. A transcript that
   ends in calls without results, which an older `nare run --stream` could
   save, has them answered first, since a vendor rejects it. `save` is
   `cli._save`, the atomic owner-only write. The CLI calls both; the TUI
   uses both. One copy of each.
3. **Run flags are shared.** The arguments `nare run` and `nare tui` have in
   common (provider, model, base-url, temperature, max-tokens, effort,
   system, tools, root, max-turns, budgets, context window, prices, MCP,
   session, resume) are added by one helper both subparsers call, and turned
   into a transport and policy by the existing `transport_from_args` and
   `policy_from_args`. `nare tui` does not take `--yes`, `--jsonl`,
   `--contract`, `--schema`, `--prompt-file`, or `--stream`; it always
   streams.
4. **An interrupted dispatch says so.** `step()`'s `finally` answers every
   unanswered tool call with `tool dispatch failed`. When the cause is
   cancellation, that text becomes `interrupted by the user`, so the model is
   told what happened. The transcript stays valid either way.

## 5. The interactive flow

`nare tui [flags] [prompt]`. With a prompt, the run starts at once; without
one, the input box waits. `--resume PATH` opens a saved session for a
follow-up; `--session PATH` sets where it is written.

### 5.1 Screen

Three regions: the scrolling transcript, the input box, and a one-line
status bar beneath it:

```
claude-sonnet-5 · turn 7/50 · 41.2k tok · $0.31 / $2.00 · working
```

Model, turns used this invocation against `--max-turns`, total tokens, cost
(`cost unknown` when nare cannot price the backend; the `/ $limit` part only
when a USD budget is set), and the state: `idle`, `working`,
`awaiting approval`, `blocked`, `done`, `error`, or `interrupted`. Every
value comes from `Session` and `cost` events. There is no new accounting.

### 5.2 Transcript

Drawn from `session.messages`: user text; assistant text as Markdown;
thinking dimmed and folded; each tool call as a one-line header with its
result folded to its first few lines; errors in red. `write` shows the head
of its content and `edit` a diff of `old` against `new`; the approval
prompt (5.3) shows the full-file diff instead.

While a turn is in flight, a live block below the transcript shows
`progress` and `thinking` events as they stream. `tool_use` events arrive
only after `step()` returns, so the live block shows streamed text and
thinking only. When the turn's messages can be drawn, the live block is
replaced by them. A tool call is drawn with its result, so a turn with calls
waits for its dispatch.

### 5.3 Approvals

The approver is constructed with the session's `Policy` and called by
`dispatch()` for every tool except `read` and `ask`, which change nothing
and never prompt. It opens a modal and awaits a key:

- `bash`: the command.
- `edit`: a unified diff of the file before and after the replacement.
- `write`: a unified diff against the existing file, or `new file` and its
  contents.
- MCP and any other tool: the arguments as indented JSON.

The path for `edit` and `write` is resolved through `policy.resolve()`. When
that refuses, the modal shows the raw arguments; `dispatch()` refuses the
call itself either way.

Keys: **y** allows; **n** or **Esc** denies; **a** allows and adds the tool
to the "always" set. That set lives for the TUI process and is never written
to the session. A denial reaches the model as `<tool> was not approved`, the
existing `dispatch()` result, and the run continues.

### 5.4 Interrupt

While a run is working, **Esc** (with no modal open) or **Ctrl-C** cancels
the task driving `run()`. **Ctrl-Q** quits.

- Cancelled while the model is streaming: nothing was committed, and the
  session ends on the user's message.
- Cancelled during dispatch: `step()`'s `finally` answers every unanswered
  call with `interrupted by the user` (section 4.1, change 4). A pending
  approval modal closes.
- In both cases the state becomes `interrupted` and the input is live.

A `bash` command still running when the run is cancelled is killed with its
process group, so an interrupt stops it and Ctrl-Q never waits on it.

### 5.5 Follow-ups and `ask` answers

These are one path. When the state is `done`, `error`, `interrupted`, or
`blocked`, submitting the input calls `reopen(session, text)` and `run()`
again. A `blocked` session shows its questions as a highlighted block above
the input, whose placeholder reads `answer the questions above`. While a run
is working the input is disabled.

### 5.6 Persistence

With `--session`, the session is saved after every turn once its calls have
results, as `nare run` does, and once more on quit. A file is never saved
mid-dispatch, while an approval waits: a kill then would leave calls without
results, which a vendor rejects on resume. Quitting while a run is working
cancels it first, then saves.

## 6. Attach

`nare tui --attach PATH`. Read-only: no input box, no approvals, no provider
flags, no API key.

- **Polling.** Once a second, stat the file (mtime and size). When either
  changes, read it with `loads()`. `nare run` writes by atomic rename, so
  every read sees a whole file.
- **Updating.** Same session id and a longer message list: append the new
  messages. Anything else: redraw the whole transcript. Compaction edits old
  messages in place; the view keeps the text it already drew.
- **Staleness.** `working` in a file can mean the process died. The status
  bar shows the state with the file's age, for example
  `working · last write 3m ago`.
- **Missing file:** shows `waiting for PATH` and keeps polling; a run writes
  nothing before its first turn.
- **Malformed file:** keeps the last good view, shows the error, retries.
- **Contract or session-version mismatch:** permanent, so it is shown once
  and polling stops.
- Polling continues after `done` or `blocked`, because Conductor resumes the
  same path for its next round. **Ctrl-Q** quits.

## 7. Errors

- Startup failures (bad flags, `--root` not a directory, an unreadable
  `--resume`, a missing extra) print to stderr and exit 2 before Textual
  takes the screen. The rules match `nare run`'s. `--attach` never fails at
  startup on the file's contents: a missing or malformed file is a state the
  view shows (section 6).
- Model and tool failures arrive through `run()` as `error` events and a
  status, as today. The TUI shows them and stays usable for a follow-up.
- A failed per-turn save is a warning in the status bar, not fatal: a person
  is watching. A `finally` around the app makes one last save, so a crash in
  the TUI's own code still keeps the transcript.
- `nare tui` exits 0 on quit. Its exit code is not part of the contract; the
  TUI is not a machine surface.

## 8. Packaging and testing

**Packaging.** `[project.optional-dependencies] tui = ["textual>=8.2,<9"]`.
Textual is also
added to the dev group, so `bin/build` and CI run the TUI tests. No change to
`Dockerfile` or the benchmark images.

**Testing.** No terminal and no network, using `FakeProvider` as the rest of
the suite does.

- Core: `dispatch()` with an async approver that allows and one that denies;
  cancelling `step()` mid-dispatch leaves `interrupted by the user` results
  and a transcript a transport would accept; the existing CLI resume tests
  cover `reopen()` and `save()` after the move.
- `render.py`: each block type, and `edit` and `write` diffs including a new
  file.
- Approver logic: `read` and `ask` exempt; the "always" set; a path
  `policy.resolve()` refuses.
- `attach.py`, on temporary files: grown, replaced, missing, malformed, and
  contract-mismatched.
- `Pilot`: prompt to `done`; approval with y, n, and a; Esc interrupt then a
  follow-up; `blocked`, answer, `done`; the status bar after a turn; attach
  redrawing when the file is rewritten.

## 9. Later

- **Token-level attach.** A `--events FILE` flag on `nare run` that tees the
  JSONL stream to a file the viewer tails. Additive to the contract, but
  Conductor and the benchmark runner would have to pass it.
- **Session picker.** List recent session files in a directory.
