# nare — Working Handoff

**Last updated:** 2026-09-09
**Audience:** whoever picks up nare next, including future-you starting a fresh
session with no context.

This is internal process state, not product documentation. The slice-1 spec
says nare ships no roadmap document, and it should not: a public roadmap is a
promise you maintain instead of code. This file is different — it is the
working notes that let the next design session start cold. It lives under
`docs/superpowers/` with the specs, not in the product docs, and it is expected
to go stale and be rewritten rather than curated.

---

## 1. Status right now

| Thing | State |
|---|---|
| Slice 1 spec | `docs/superpowers/specs/2026-09-09-nare-walking-skeleton-design.md`, 271 lines, approved |
| Branch | `spec/nare-walking-skeleton`, pushed |
| `main` | Untouched at `46b0a3d` |
| Slice 1 issue | Body drafted, **not filed** — `gh` token expired |
| Code | None written. No `src/` yet. |

**Blocker:** the GitHub API token in `~/.config/gh/hosts.yml` is invalid.
SSH works (git push is fine); the REST API does not accept SSH keys, so issue
creation needs `gh auth login -h github.com`. Only `repo` scope is required —
the project board auto-adds from the repo, so no `project` scope and no
`--project` flag.

---

## 2. What nare is, in one page

A standalone agent harness: an agent loop, a tool set, a provider layer, and a
serializable session, exposed through a CLI now and a TUI, HTTP daemon, and
desktop app later.

It is a real harness, not a wrapper around other harnesses. Conductor's
performer will select it with `backend: nare` alongside the eight adapters that
drive external CLIs today.

**The case for building it:** those eight adapters total ~5,300 lines, and they
are large because they reverse-engineer structure out of CLIs that were never
built to emit it. A harness that emits `blocked` + `questions`, typed events,
honest `stop_reason`, and resumable sessions natively turns an 800-line adapter
into ~80.

### The six settled decisions

Full reasoning is in the spec; ADRs are still to be written.

1. **Own harness**, not a wrapper or meta-harness.
2. **Python 3.12+**, uv, hatchling. Matches Conductor's floor and toolchain.
   Known cost: no true single binary. Accepted as a packaging problem.
3. **Subprocess CLI integration.** The performer spawns `nare` exactly as it
   spawns `claude`. Keeps nare's CLI on the critical path so the product
   surface cannot rot. `import nare` stays free later.
4. **Approval seam with explicit `--yes`.** One dispatcher, one
   `approve(tool, args) -> bool`. One implementation in slice 1; the seam the
   TUI needs exists from the start.
5. **Serializable `Session` advanced by an explicit `step()`**, surfaced as an
   async generator. Resumable by construction, deterministic under test.
6. **Not LangGraph.** Conductor runs on it and should. nare should not carry a
   graph framework in its public surface.

---

## 3. The boundary — the most important thing in this document

Slice 2 will be under constant pressure to pull Conductor's concerns into nare.
Resist it. The line, concretely:

**nare knows about:** messages, tool calls, tokens, cost, the four statuses
(`working` / `blocked` / `done` / `error`), stop reasons, and questions.

**nare knows nothing about:** git, GitHub, pull requests, branches, cards,
roles, review cycles, or the lifecycle.

The evidence this line is real is in `agent/performer/src/performer/main.py`
around line 1990. The performer's own state vocabulary is far richer than the
backend's four values:

```
plan_committed        assessment_complete   approved
changes_requested     security_passed       security_failed
qa_passed             qa_failed             qa_env_blocked
docs_committed        env_bootstrap_complete
```

Every one of those is **derived by the performer** from a four-value backend
status plus git and GitHub actions it performs itself. `workspace.py` (~1,370
lines), `github.py` (~510), and `qa_capture.py` (~228) are Conductor domain and
must stay there.

If a slice-2 conversation starts proposing that nare open a pull request or
understand a review cycle, that is the boundary being crossed.

---

## 4. The five slices

| # | Sub-project | Deliverable | Status |
|---|---|---|---|
| 1 | Core engine and headless CLI | The loop, 5 tools, Anthropic provider, typed JSONL events, `nare run` | Specced |
| 2 | Conductor parity | `backend: nare` wired into the performer; compaction; cost accounting | Next |
| 3 | Interactive CLI and TUI | REPL, permission prompts, diff rendering, session browsing | Not started |
| 4 | Extension surface | MCP, hooks, custom tools, subagents | Not started |
| 5 | Desktop | HTTP daemon and UI | Not started |

---

## 5. Slice 2 — what you need to spec it

### 5.1 The integration surface, exactly

Everything below is in `vividynamics/conductor`. These are the real symbols the
adapter must satisfy — read them before the brainstorm.

| What | Where |
|---|---|
| `BackendAdapter` protocol | `agent/performer/src/performer/backends/base.py` |
| `BackendStatus` value object | same file |
| `BackendEvent`, `BackendEventType` | `agent/performer/src/performer/models.py:40-60` |
| Backend registry (`SUPPORTED_BACKENDS`) | `agent/performer/src/performer/backends/__init__.py` |
| Closest reference implementation | `backends/claude_code.py` (821 lines) |
| Capability probe | `agent/performer/src/performer/capabilities.py` |
| Performer settings (`AGENT_BACKEND`) | `agent/performer/src/performer/config.py:19` |
| Daemon-side per-role config | `src/conductor/config.py:478-500` |
| Performer container images | `agent/performer/Dockerfile{,.base,.slim,.full,.extra}` |

The five methods to implement:

```python
async def start(stand, score, *, model=None, effort=None,
                temperature=None, max_tokens=None) -> None
def  get_status() -> BackendStatus
def  drain_events() -> list[BackendEvent]
async def relay_feedback(feedback: str) -> None
async def stop() -> None
```

`BackendStatus` fields: `state`, `questions`, `error_reason`, `stop_reason`,
`tokens_processed`, `progress`, `output`.

### 5.2 What slice 1 already gives you free

- `state`, `questions`, `stop_reason`, token counts — emitted natively, no
  parsing.
- `drain_events()` — nare's JSONL uses Conductor's six event types and four
  field names verbatim, so this is `BackendEvent(**json.loads(line))`.
- `relay_feedback()` — `--resume` plus one appended user message. This is why
  resume was pulled into slice 1.
- `stop()` — kill the subprocess group, same as every other adapter.

### 5.3 What slice 2 must actually build

1. **The adapter itself** — target ~80 lines in
   `performer/backends/nare.py`, plus a `SUPPORTED_BACKENDS` entry and
   `"nare": "nare"` in `capabilities.py:_BACKEND_BINARIES`.
2. **Compaction.** Deliberately deferred from slice 1, and it is a real design
   problem: token budget, what gets summarized, what is pinned, whether a
   summary turn is visible in the transcript. This is the single largest
   unknown in slice 2.
3. **Cost accounting** — per-model pricing, cache-read and cache-write tokens
   priced separately, surfaced in `cost` events and in `Usage`.
4. **`output`** — the structured field Conductor reads for the architect's
   plan and the reviewer's verdict. Slice 1 says "the final assistant text is
   the output." Decide in slice 2 whether that holds or whether a `submit` tool
   is warranted.
5. **Container packaging** — `pip install nare` into the performer images, and
   deciding which of the five Dockerfile variants carry it.

### 5.4 Two gaps in slice 1 you should fix before or during slice 2

Found while verifying this handoff. Neither is in the approved slice-1 spec,
and both are small. Raise them at the start of the slice-2 brainstorm, or amend
slice 1 — do not let them get discovered during implementation.

**Personas have no way in.** Conductor injects per-role instructions
(`personas:` in config, threaded through `main.py` to the backend prompt).
Slice 1's CLI is `nare run [--jsonl] [--yes] [--resume]` — there is no
`--system` or instructions flag. This is roughly three lines and it is
load-bearing for all nine roles.

**Tuning knobs have no way in.** `BackendAdapter.start()` takes `model`,
`effort`, `temperature`, and `max_tokens`, so the adapter must be able to pass
them. Slice 1's CLI accepts none. Also small, also load-bearing.

Recommendation: amend slice 1 to add `--system`, `--model`, `--temperature`,
`--max-tokens`, `--effort`. Roughly 15 lines total, and it keeps slice 2 to
genuinely new design work instead of retrofitting flags.

### 5.5 Open questions to bring to the slice-2 brainstorm

1. **Compaction strategy** — summarize-and-drop, sliding window, or
   tool-result eviction first? What is pinned? Is the summary a visible turn?
2. **Does `output` stay "final assistant text"**, or does a `submit(...)` tool
   earn its place for typed per-role output?
3. **Where does role prompting live** — does nare ship role presets, or does
   Conductor own all persona text and nare stay generic? (The boundary in §3
   argues strongly for the latter.)
4. **Does slice 2 add a second provider?** Conductor has configs for ollama,
   openai-compatible, and litellm. If yes, this is where `provider.py` becomes
   `providers/` and the message format question from slice 1 gets reopened.
5. **Which Dockerfile variants** carry nare, and does the capability probe need
   to distinguish "nare installed" from "nare with a working API key"?
6. **Migration posture** — is `backend: nare` opt-in per role indefinitely, or
   is there a point where it becomes the default and adapters get deleted?

---

## 6. How to continue — the flow

Yes, start each new slice with `/superpowers:brainstorming`. The cycle per
spec is:

```
/superpowers:brainstorming   -> questions, approaches, sectioned design
                             -> docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md
                             -> committed on a branch
/superpowers:writing-plans   -> implementation plan from that spec
/superpowers:executing-plans -> execution with review checkpoints
   (or /superpowers:subagent-driven-development for independent tasks)
```

Brainstorming is per **spec**, not per slice. If a slice splits into three
specs, that is three brainstorms.

A cold session has none of this context, so open the next one by pointing at
the artifacts explicitly. Something like:

> Read `docs/superpowers/HANDOFF.md` and
> `docs/superpowers/specs/2026-09-09-nare-walking-skeleton-design.md`, then
> `/superpowers:brainstorming` for slice 2: Conductor parity. The integration
> surface is in `vividynamics/conductor` at
> `agent/performer/src/performer/backends/base.py`.

Note that slice 1 is specced but **not implemented**. Slice 2's spec can be
written against slice 1's contract without slice 1 existing, but it should not
be *implemented* first — the adapter needs something to talk to.

---

## 7. When to split a slice into multiple specs

Default to one spec per slice. Split when **any** of these is true:

1. **The parts ship independently.** If A being late does not block B, they are
   two specs.
2. **The questions do not cross over.** If answering every clarifying question
   for A changes nothing about B's design, they are two designs wearing one
   name. This is the strongest signal.
3. **The acceptance criteria share no rationale.** Criteria that could be
   satisfied by unrelated work belong to unrelated specs.
4. **It exceeds one plan's worth of work.** Rough proxy: more than ~10
   implementation tasks, or more than ~1,500 lines of new code.
5. **It introduces a second trust boundary or external dependency surface.**
   Network listeners, plugin loading, and credential handling each deserve
   their own design conversation.

Keep them together when the parts share a data model or a protocol you would
otherwise have to invent twice.

### Applying that to slices 3 to 5

- **Slice 2 — one spec.** Compaction, cost, and the adapter all move the same
  contract. Split compaction out only if it generates a question set of its
  own, which is plausible.
- **Slice 3 — likely two.** The permission-prompt design does not depend on the
  TUI framework (test 2). Split into interactive loop plus approval UX, and
  TUI rendering plus session browsing.
- **Slice 4 — three or four.** MCP, hooks, custom tools, and subagents are
  independent extension points that fail every test above. Do not write one
  "extensibility" spec.
- **Slice 5 — two.** HTTP daemon plus auth is a trust boundary (test 5); the
  UI is separate.

---

## 8. Issues and the board

Default: **one tracking issue per spec**, not per slice. The issue carries the
problem, the scope, the acceptance criteria, and a link to the spec file. Break
into sub-issues only when someone needs to pick up pieces independently.

**Open question that changes all of this:** is the nare project board polled by
Conductor? If nare is going to be built by the Coordinare pipeline, then issues
are dispatch units for an autonomous performer, not human tracking artifacts —
which means smaller cards, acceptance criteria crisp enough to be machine-
checkable, and each card independently mergeable. That is a materially
different sizing discipline. Decide it before filing more than a handful of
issues, because retrofitting it across a populated board is tedious.

---

## 9. Reference — Conductor files worth reading before slice 2

```
agent/performer/src/performer/
  backends/base.py          the protocol, ~57 lines, read first
  backends/claude_code.py   closest reference adapter, 821 lines
  backends/__init__.py      the registry
  models.py:40-60           BackendEvent + redaction validator
  capabilities.py           what the performer advertises it can run
  config.py:19              AGENT_BACKEND
  main.py:~1990             the performer state vocabulary (see §3)
src/conductor/config.py:478 per-role backend/model/effort config
```

Adapter sizes, for calibrating the ~80-line target:

```
pi 405   openclaw 535   junie 536   opencode 628
opencode_compat 736   codex 741   claude_code 821   hermes 940
                                          total 5,342
```
