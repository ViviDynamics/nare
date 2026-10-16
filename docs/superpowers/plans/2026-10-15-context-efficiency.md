# Context Efficiency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Long sessions stop paying full price to re-send their history (issue ViviDynamics/nare#67, E1 to E4). The anthropic rail marks the newest block and the last tool for prompt caching (E1). Unsigned thinking and empty text stay in the session but are no longer sent back (E2, E3). Compaction gains a second tier that elides old `write` contents and long `edit` texts when eliding tool results was not enough (E4).

**Architecture:** In `src/nare/transport/anthropic.py`, `messages_from()` filters each saved turn through a new `_sent_back()` and drops an assistant turn left empty, merging the user turns on either side. A new `with_cache_breakpoints()` marks the last block of the last message and a copy of the last tool, and a new `AnthropicTransport._wire()` builds both for `turn()` and `stream_turn()`. In `src/nare/compact.py`, a new `_elide_inputs()` runs after the existing tool-result tier, oldest first and outside the protected tail, and `CompactionReport` gains an `inputs` count that the loop's compaction event reports. The OpenAI rail already reads `cached_tokens` and needs no change (Decision 1).

**Tech Stack:** Python (CI: 3.12 via `astral-sh/setup-uv`; this machine's uv venv: 3.14.2), the `anthropic` SDK on `httpx2`, pytest with `asyncio_mode = "auto"`, ruff, mypy strict. CI is `bin/build`.

**Spec:** the issue body (`export GH_TOKEN=$(gh auth token); gh issue view 67 --repo ViviDynamics/nare`) and its design doc, which is on an unmerged branch: `git show origin/spec/tui-review:docs/superpowers/specs/2026-10-08-context-efficiency-design.md`. The evidence behind E1 to E4 is `git show origin/spec/tui-review:docs/evidence/2026-10-08-tui-practice-run.md` (section "Spec E"). Read the issue before Task 1. Decisions 1, 2 and 9 below narrow what it asks for, from measurements taken while planning.

## Measured while planning (2026-10-15)

Every task below was trialled on a scratch worktree of `origin/main` at 4af56db. Each new test failed there as its "verify it fails" step says, and passed after its code. The full `bin/build` passed with all three tasks: 805 passed, 8 deselected. These live probes went through the org's LiteLLM proxy. The haiku calls cost under $0.01 in total.

| Probe | Result |
| --- | --- |
| `claude-haiku-5-5`, anthropic rail, same 11.8k-token prefix twice, through `AnthropicTransport` with Task 2 | turn 1: `cache_write` 11,773, $0.00148; turn 2: `cache_read` 11,773, $0.000126 |
| The same on `origin/main` (no markers) | 11,777 input tokens and $0.00119 on each turn, 0 cache reads |
| `spark/glm-5.3-flash`, anthropic rail, markers on | HTTP 200; usage has no cache fields; 8,470 input tokens on both turns |
| `spark/glm-5.3-flash`, openai rail, through `OpenAITransport` | no `prompt_tokens_details`; 8,469 input tokens on both turns |
| spark, anthropic rail, a history holding one 23.9k-character unsigned thinking block | `origin/main`: 8,470 input tokens, against 170 without the block. With Task 1: 170 both ways |
| `claude-haiku-5-5`, the same unsigned thinking block | HTTP 200, and the block is ignored: 35 input tokens with or without it |
| haiku and spark, an empty text block in an assistant turn | HTTP 200 on both |
| spark, thinking enabled, a tool loop whose last assistant turn has no thinking block | HTTP 200 |

What follows from these: caching pays off on haiku, which the proxy routes to Anthropic. Spark reports no cache tokens on either rail, so E1 saves nothing there until its backend caches and reports it, a proxy configuration job outside this repo. E2 is spark's big win: the proxy forwards unsigned thinking to GLM and bills every token of it. A Claude backend ignores unsigned thinking and accepts empty text, so E2 and E3 cut cost and noise; they do not fix rejected requests.

## Global Constraints

- Work on branch `feat/67-context-efficiency`. It already exists on origin, linked to #67 (the issue's Development section), branched from `origin/main` at 4af56db (PR #82 merged), and its first commit is this plan. A draft PR that closes #67 tracks it. Task 1 checks it out.
- Scope: `messages_from()`, two new helpers and the request building in `src/nare/transport/anthropic.py`; `src/nare/compact.py`; the compaction event text in `src/nare/loop.py`; two paragraphs of `README.md` and one sentence of `docs/contract.md`. Tests go in `tests/test_transport.py`, `tests/test_streaming.py` and `tests/test_compact.py`. Do not touch `src/nare/transport/openai.py` (Decision 1).
- **Every test run and build runs under a hard timeout**: `timeout -k 10 120` for a pytest run of a few files, `timeout -k 10 600` for `bin/build`, `timeout -k 10 300` for a live check. When you run a command through a tool, also set the tool's own timeout above the command's (for example 620000 ms for `bin/build`).
- `bin/build` (ruff format --check, ruff check, mypy strict, pytest -q) passes before every commit. Run `uv run ruff format src tests` first; the build only checks formatting. Baseline on `origin/main` at 4af56db, measured on this machine: 796 passed, 8 deselected.
- No new dependencies. No new `skip` or `skipif` markers. Never disable, skip, delete or loosen a test. Two existing tests get a new exact expectation (Decision 4); that is the only edit to an existing test.
- ruff: line length 88, rules E, F, I, UP, B. mypy strict covers `tests/` too.
- No em dashes in code comments, commit messages, or any prose.
- Code comments follow the repo's habit: say why, and cite the failure that was seen.
- Stage only the files a task names. Never `git add -A` or `git add .`: the tree holds an untracked `.bench-report/`, which does not belong in these commits.
- Commit after each of Tasks 1 to 3. Do not push, and do not open another PR: the branch's draft PR already exists.
- Live checks (Task 4) load the proxy credentials from the repo's gitignored `.env` in the same command (`set -a; . ./.env; set +a`) and never print them. Use `spark/glm-5.3-flash` (free) for everything except the haiku cache check, which needs a model that caches. Model aliases change: list `/v1/models` first, and never commit an alias anywhere in the repo.

## Decisions

These are settled so every task agrees. A reviewer who disagrees should reject the task that owns the decision.

1. **No change to the OpenAI rail.** The issue asks to "read `cached_tokens` on the OpenAI route". `openai.py` already does, in `usage_from()`, for both the plain and the streamed reply. `tests/test_openai_transport.py::test_cached_prompt_tokens_are_excluded_from_input` and the openai case of `tests/test_streaming.py::test_stream_normalizes_final_reply_and_usage` pin it. Its `messages_from()` already drops all thinking. Owner: Task 4, which records it.
2. **The live cache check uses the haiku alias the proxy lists today, `claude-haiku-5-5`.** The issue names `claude-haiku-4-5`, which `/v1/models` no longer lists (checked 2026-10-15). The check's prefix is about 11.8k tokens, above the 4,096-token minimum that Haiku 4.5 needs before it caches anything. For spark the answer to "does LiteLLM map `cached_tokens`" is no: spark reports no cache fields on either rail, so there is nothing to map. Owner: Task 4.
3. **Cache markers go on in request building, not in `messages_from()`.** `messages_from()` stays the serializer that `tests/test_images.py` and the new Task 1 tests pin, and `with_cache_breakpoints()` runs on its output in `_wire()`, the one place both `turn()` and `stream_turn()` build their request. Owner: Task 2.
4. **Two existing tests change their expected wire body**: `test_tool_schemas_pass_through_anthropic_shaped` and `test_messages_serialize_as_role_plus_blocks` in `tests/test_transport.py`. They pin the exact request body, and the body now carries `cache_control` on purpose. Each keeps its exact-equality assertion, with the marker added to the expected value. That is a new expectation, not a looser one. Owner: Task 2.
5. **The second marker goes on the last tool, as the issue says, not on the system prompt.** Marking the system prompt would turn `system` from a string into a block list on the wire and change `test_system_reaches_the_wire`. nare's tools are small, so this marker mostly matters after compaction rewrites old messages. Owner: Task 2.
6. **No guard for a thinking block as the last block.** The API takes no marker on a thinking block, but the loop calls the model only after a user turn, so the last block is always text or a `tool_result`. `reopen()` answers a trailing turn of unanswered calls before anything follows it. Owner: Task 2.
7. **No flag to turn caching off.** Both backends the proxy offers accept `cache_control`: spark ignores it, and haiku caches. Add a flag only if some backend refuses the field. Owner: Task 2.
8. **`tools` is copied before marking, never marked in place.** `Policy.schemas()` returns the module-level dicts in `TOOL_SCHEMAS`, so marking in place would put a marker on every later request's tools and leak it into other tests. `messages_from()` returns fresh copies (`asdict`), so marking its output in place is safe. Owner: Task 2.
9. **E2 and E3 change only what is sent, not what is saved**, as the issue's fix says. The session keeps unsigned thinking and empty text, so the TUI still shows the thinking, and still draws an empty text block as a blank line. That blank line is a TUI rendering fix, outside this issue's scope. Owner: Task 1.
10. **Dropping an empty assistant turn merges the user turns on either side**, rather than sending two user turns in a row. The Anthropic API would combine them itself, but a backend behind the proxy may not, and merging is what `append_user_text()` already does. The merge joins any adjacent same-role turns, which a well-formed transcript never has. Owner: Task 1.
11. **The second compaction tier runs only when the first leaves the estimate above 60%**, oldest first, outside the same protected tail (from the second-to-last assistant turn on). A field is elided only when its stub is shorter than it, the same "long" rule tool results use. Owner: Task 3.
12. **A failed call keeps its input.** Its stub would say "wrote 4000 chars" about a write that never happened. Failed means its `tool_result` has `is_error`, which also covers a write that was not approved. Owner: Task 3.
13. **Stub numbers have no thousands separator** ("wrote 4521 chars to src/db/schema.ts at turn 9"), to match the existing tool-result stub ("14302 chars of output from turn 6"). The issue's example writes "4,521". Owner: Task 3.
14. **`CompactionReport` gains `inputs: int = 0`**, the number of calls whose input was elided. The event text adds " and N tool inputs" only when N is not 0, so today's text, and the TUI test that quotes it, stay as they are. The event's `detail.compaction` gains an `inputs` key; additive fields keep the contract version (`docs/contract.md`, "What changes the version"). Owner: Task 3.

## Review Focus

1. **Signed and redacted thinking must still go back unchanged.** A Claude model with thinking on needs its signed blocks in a tool loop. Covered by `test_unsigned_thinking_and_empty_text_are_not_sent_back` (Task 1).
2. **Markers never reach the session or the shared tool schemas** (Decision 8). Covered by the last two assertions of `test_the_last_tool_and_the_newest_block_are_cache_breakpoints` (Task 2).
3. **The streamed request carries the same markers as the plain one.** Covered by `test_anthropic_stream_requests_carry_the_cache_breakpoints` (Task 2).
4. **The first compaction tier behaves exactly as before.** The existing tests in `tests/test_compact.py` and the compaction tests in `tests/test_images.py` run unchanged in every build, and `test_tool_results_are_elided_before_tool_inputs` (Task 3) guards the tier order.
5. **Compaction edits the session in place**, so a resumed session or `nare tui --attach` shows a stub where an old write's content was, as it already does for tool results. The attach view keeps the text it already drew (`src/nare/tui/attach.py`).
6. **Known and accepted: the estimate still counts unsigned thinking.** `compact.estimate()` measures the saved session, which keeps the thinking that Task 1 stops sending. Between measurements it can overestimate by about one turn's thinking, so compaction may run a little early, never late. Each turn's measured `last_input_tokens` resets it. Not changed here (scope).
7. **Known and accepted: the cost event's text.** It reads `"{input} in / {output} out"`, and with caching `input` is only the uncached tail (4 tokens in the haiku check) while the history moves to `cache_read`. The event's detail and the budgets count all four categories, so budgets are unchanged. Making the one-line text show cached tokens is a TUI and cost-display job (the practice run's spec C), not this issue.

---

## File map

| File | Responsibility | Tasks |
| --- | --- | --- |
| `src/nare/transport/anthropic.py` | `_sent_back()`, `messages_from()` filter and merge; `with_cache_breakpoints()`; `AnthropicTransport._wire()` used by `turn()` and `stream_turn()` | 1, 2 |
| `src/nare/compact.py` | `_INPUT_STUBS`, `CompactionReport.inputs`, `_elide_inputs()` | 3 |
| `src/nare/loop.py` | the compaction event's text in `step()` | 3 |
| `README.md` | the providers paragraph (caching, unsigned thinking) and the compaction paragraph | 2, 3 |
| `docs/contract.md` | the compaction sentence | 3 |
| `tests/test_transport.py` | Task 1's two tests, Task 2's test and two updated expectations | 1, 2 |
| `tests/test_streaming.py` | Task 2's streaming test | 2 |
| `tests/test_compact.py` | `write_transcript()` and Task 3's five tests | 3 |

---

### Task 1: Stop sending unsigned thinking and empty text (E2, E3)

**Files:**
- Modify: `src/nare/transport/anthropic.py:114-120` (the head of `messages_from()`, down to `output = [asdict(message) for message in messages]`)
- Test: `tests/test_transport.py` (insert directly before the `# --- Normalization tables, section 4 of the transport spec` comment, after `test_turn_normalizes_the_response`; add `messages_from` to the `nare.transport.anthropic` import)

**Interfaces:**
- Consumes: `Message` from `nare.session` (unchanged).
- Produces: `_sent_back(block: dict[str, Any]) -> bool` (module-private). `messages_from()` keeps its signature and return type. Task 2 calls it from `_wire()`.

- [ ] **Step 1: Check out the branch**

```bash
cd /home/smadinya/vividynamics/nare
git fetch origin
git switch feat/67-context-efficiency
git pull --ff-only
git log --oneline -2
```

Expected: `docs: add the implementation plan for context efficiency (#67)` on top of `4af56db fix: a cancel while bash starts kills the whole process group (#82)`. The untracked `.bench-report/` comes along untracked; leave it alone. If origin/main has moved since, do not rebase here: the line numbers in this plan are for 4af56db.

- [ ] **Step 2: Write the failing tests**

In `tests/test_transport.py`, add `messages_from` to the import from `nare.transport.anthropic`, between `AnthropicTransport` and `stop_reason_from`:

```python
from nare.transport.anthropic import (
    _STOP_REASONS,
    DEFAULT_MAX_TOKENS,
    EFFORT_BUDGETS,
    NONSTREAMING_MAX_TOKENS,
    AnthropicTransport,
    messages_from,
    stop_reason_from,
    usage_from,
)
```

Then insert directly before the `# --- Normalization tables, section 4 of the transport spec` comment:

```python
def test_unsigned_thinking_and_empty_text_are_not_sent_back() -> None:
    # On the practice run all 44 thinking blocks had signature '': a proxy
    # made them, and re-sending them was 38% of every request. Empty text
    # blocks were re-sent too. Signed and redacted thinking go back as is.
    messages = [
        Message("user", [{"type": "text", "text": "task"}]),
        Message(
            "assistant",
            [
                {"type": "thinking", "thinking": "plan", "signature": ""},
                {"type": "thinking", "thinking": "no signature at all"},
                {"type": "thinking", "thinking": "real", "signature": "sig"},
                {"type": "redacted_thinking", "data": "opaque"},
                {"type": "text", "text": ""},
                {"type": "tool_use", "id": "c1", "name": "read", "input": {}},
            ],
        ),
        Message(
            "user",
            [
                {
                    "type": "tool_result",
                    "tool_use_id": "c1",
                    "content": "a",
                    "is_error": False,
                }
            ],
        ),
    ]
    assert messages_from(messages)[1]["content"] == [
        {"type": "thinking", "thinking": "real", "signature": "sig"},
        {"type": "redacted_thinking", "data": "opaque"},
        {"type": "tool_use", "id": "c1", "name": "read", "input": {}},
    ]
    assert len(messages[1].content) == 6, "serialization edited the session"


def test_an_assistant_turn_left_empty_is_dropped_and_the_user_turns_join() -> None:
    # A reply of unsigned thinking and empty text leaves nothing to send.
    # Dropping it puts two user turns side by side, and roles must alternate.
    messages = [
        Message("user", [{"type": "text", "text": "task"}]),
        Message(
            "assistant",
            [
                {"type": "thinking", "thinking": "plan", "signature": ""},
                {"type": "text", "text": ""},
            ],
        ),
        Message("user", [{"type": "text", "text": "go on"}]),
    ]
    assert messages_from(messages) == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "task"},
                {"type": "text", "text": "go on"},
            ],
        }
    ]
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
cd /home/smadinya/vividynamics/nare
timeout -k 10 120 uv run pytest -q tests/test_transport.py -k "unsigned_thinking or left_empty" 2>&1 | tail -15
```

Expected: 2 failed, both with `AssertionError`. The first shows the unsigned thinking and empty text still in the list; the second shows three messages where one is expected. If either passes, stop and report.

- [ ] **Step 4: Filter what goes back**

In `src/nare/transport/anthropic.py`, replace exactly this, the head of `messages_from()`:

```python
def messages_from(
    messages: list[Message],
    *,
    image_input: bool = False,
    image_model: str = "AnthropicTransport/model unspecified",
) -> list[dict[str, Any]]:
    output = [asdict(message) for message in messages]
```

with:

```python
def _sent_back(block: dict[str, Any]) -> bool:
    """Whether a saved block goes back to the model.

    Thinking with no signature was made by a proxy translating a reasoning
    model. It carries no server state, and a Claude backend ignores it, but
    the proxy hands it back to the model it came from and bills it: on the
    practice run all 44 thinking blocks were unsigned, 38% of the context,
    re-sent every turn. Empty text is dead weight too. Signed and redacted
    thinking go back as they came.
    """
    kind = block.get("type")
    if kind == "thinking":
        return bool(block.get("signature"))
    if kind == "text":
        return bool(block.get("text"))
    return True


def messages_from(
    messages: list[Message],
    *,
    image_input: bool = False,
    image_model: str = "AnthropicTransport/model unspecified",
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for turn in messages:
        raw = asdict(turn)
        raw["content"] = [block for block in raw["content"] if _sent_back(block)]
        if not raw["content"] and turn.role == "assistant":
            continue
        if output and output[-1]["role"] == raw["role"]:
            # A dropped assistant turn leaves two user turns side by side, and
            # roles must alternate.
            output[-1]["content"] += raw["content"]
        else:
            output.append(raw)
```

Leave the rest of `messages_from()` (the `for message in output:` image loop and `return output`) unchanged. The new loop variable is `turn`, not `message`: mypy strict rejects reusing `message` for both a `Message` and the image loop's dict.

- [ ] **Step 5: Run the new tests and the transport, image and streaming tests**

```bash
cd /home/smadinya/vividynamics/nare
timeout -k 10 120 uv run pytest -q tests/test_transport.py tests/test_images.py tests/test_streaming.py 2>&1 | tail -3
```

Expected: all pass, including both new tests.

- [ ] **Step 6: Format and run the build**

```bash
cd /home/smadinya/vividynamics/nare
uv run ruff format src tests
timeout -k 10 600 bin/build 2>&1 | tail -5
```

Expected: ruff and mypy clean, `798 passed, 8 deselected` (two more than the baseline, if origin/main has not moved).

- [ ] **Step 7: Commit**

```bash
cd /home/smadinya/vividynamics/nare
git add src/nare/transport/anthropic.py tests/test_transport.py
git commit -m "fix: stop re-sending unsigned thinking and empty text to the model"
```

---

### Task 2: Mark the newest block and the last tool for prompt caching (E1)

**Files:**
- Modify: `src/nare/transport/anthropic.py` (a new `with_cache_breakpoints()` directly before `class AnthropicTransport:`; a new `_wire()` method after `context_window()`; the `messages=` and `tools=` arguments in `turn()` and `stream_turn()`)
- Modify: `README.md:42-45` (a new paragraph after the "Which rail matters for reasoning models" paragraph)
- Test: `tests/test_transport.py` (two updated expectations; one new test inserted directly before the `# --- Normalization tables` comment, after Task 1's tests)
- Test: `tests/test_streaming.py` (one new test inserted directly before the `@pytest.mark.asyncio` line above `test_openai_delta_arrives_before_final_chunk`)

**Interfaces:**
- Consumes: `messages_from()` as Task 1 left it.
- Produces: `with_cache_breakpoints(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]` and `AnthropicTransport._wire(self, messages: list[Message], tools: list[dict[str, Any]]) -> tuple[list[MessageParam], list[ToolParam]]`. `turn()` and `stream_turn()` keep their signatures.

- [ ] **Step 1: Update the two wire-body expectations (Decision 4)**

In `tests/test_transport.py`, in `test_tool_schemas_pass_through_anthropic_shaped`, replace:

```python
    assert (await outbound())["tools"] == [
        {"name": "read", "description": "read a file", "input_schema": {}}
    ]
```

with:

```python
    assert (await outbound())["tools"] == [
        {
            "name": "read",
            "description": "read a file",
            "input_schema": {},
            "cache_control": {"type": "ephemeral"},
        }
    ]
```

In `test_messages_serialize_as_role_plus_blocks`, replace:

```python
    assert (await outbound())["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "read a.py"}]}
    ]
```

with:

```python
    assert (await outbound())["messages"] == [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "read a.py",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        }
    ]
```

- [ ] **Step 2: Write the failing tests**

In `tests/test_transport.py`, insert directly before the `# --- Normalization tables, section 4 of the transport spec` comment (after Task 1's two tests). `httpx2`, `json`, `build` and `recording_client` are already in the module.

```python
async def test_the_last_tool_and_the_newest_block_are_cache_breakpoints() -> None:
    # The practice run re-sent 1.82M tokens with 0 cache reads. A marker on
    # the newest block caches the history before it; one on the last tool
    # keeps the tools cached when compaction rewrites old messages.
    captured: list[httpx2.Request] = []
    transport = build(http_client=recording_client(captured))
    tools = [
        {"name": "read", "description": "read a file", "input_schema": {}},
        {"name": "write", "description": "write a file", "input_schema": {}},
    ]
    unmarked = [dict(tool) for tool in tools]
    messages = [
        Message("user", [{"type": "text", "text": "read a.py"}]),
        Message(
            "assistant",
            [{"type": "tool_use", "id": "c1", "name": "read", "input": {}}],
        ),
        Message(
            "user",
            [
                {
                    "type": "tool_result",
                    "tool_use_id": "c1",
                    "content": "print(1)",
                    "is_error": False,
                }
            ],
        ),
    ]
    await transport.turn(messages, tools)
    body = json.loads(captured[0].content)
    ephemeral = {"type": "ephemeral"}
    marked = [
        block
        for message in body["messages"]
        for block in message["content"]
        if "cache_control" in block
    ]
    assert marked == [body["messages"][-1]["content"][-1]]
    assert marked[0]["cache_control"] == ephemeral
    assert [tool.get("cache_control") for tool in body["tools"]] == [None, ephemeral]
    # Markers are for the wire: never saved, never left on the caller's tools.
    assert all("cache_control" not in b for m in messages for b in m.content)
    assert tools == unmarked
```

In `tests/test_streaming.py`, insert directly before the `@pytest.mark.asyncio` line above `test_openai_delta_arrives_before_final_chunk`. `Bytes`, `anthropic_chunks`, `AnthropicTransport` and `Message` are already in the module.

```python
@pytest.mark.asyncio
async def test_anthropic_stream_requests_carry_the_cache_breakpoints() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=Bytes(anthropic_chunks()),
        )

    transport = AnthropicTransport(
        model="m",
        api_key="test",
        streaming=True,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    await transport.turn(
        [Message("user", [{"type": "text", "text": "hi"}])],
        [{"name": "read", "description": "read a file", "input_schema": {}}],
    )
    ephemeral = {"type": "ephemeral"}
    assert seen[0]["messages"][-1]["content"][-1]["cache_control"] == ephemeral
    assert seen[0]["tools"][-1]["cache_control"] == ephemeral
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
cd /home/smadinya/vividynamics/nare
timeout -k 10 120 uv run pytest -q tests/test_transport.py tests/test_streaming.py -k "cache_breakpoints or pass_through_anthropic or role_plus_blocks" 2>&1 | tail -8
```

Expected: 4 failed. They are the two updated tests, the new transport test (`marked` is empty), and the streaming test (`KeyError: 'cache_control'`). If any passes, stop and report.

- [ ] **Step 4: Add the helper and build both requests through it**

In `src/nare/transport/anthropic.py`, insert directly before `class AnthropicTransport:` (after `messages_from()`):

```python
def with_cache_breakpoints(
    messages: list[dict[str, Any]], tools: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Mark the newest block and the last tool as prompt-cache breakpoints.

    The practice run re-sent 1.82M tokens with 0 cache reads. The newest
    block's marker moves forward each turn, and the API finds the previous
    turn's entry behind it, so the history is read back at the cache price.
    The tool marker keeps the tools cached when compaction rewrites old
    messages. `messages` must be messages_from's fresh copy, which this marks
    in place. `tools` is the caller's own list, so it is copied, not marked.
    """
    if messages and messages[-1]["content"]:
        messages[-1]["content"][-1]["cache_control"] = {"type": "ephemeral"}
    if tools:
        tools = [*tools[:-1], {**tools[-1], "cache_control": {"type": "ephemeral"}}]
    return messages, tools
```

Add this method to `AnthropicTransport`, directly after `context_window()`:

```python
    def _wire(
        self, messages: list[Message], tools: list[dict[str, Any]]
    ) -> tuple[list[MessageParam], list[ToolParam]]:
        wire_messages, wire_tools = with_cache_breakpoints(
            messages_from(
                messages,
                image_input=self.image_input,
                image_model=f"AnthropicTransport/model={self.model}",
            ),
            tools,
        )
        return (
            cast("list[MessageParam]", wire_messages),
            cast("list[ToolParam]", wire_tools),
        )
```

In `turn()`, replace:

```python
        raw = await self._client.messages.with_raw_response.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=cast(
                "list[MessageParam]",
                messages_from(
                    messages,
                    image_input=self.image_input,
                    image_model=f"AnthropicTransport/model={self.model}",
                ),
            ),
            tools=cast("list[ToolParam]", tools),
```

with:

```python
        wire_messages, wire_tools = self._wire(messages, tools)
        raw = await self._client.messages.with_raw_response.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=wire_messages,
            tools=wire_tools,
```

In `stream_turn()`, replace:

```python
        async with self._client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=cast(
                "list[MessageParam]",
                messages_from(
                    messages,
                    image_input=self.image_input,
                    image_model=f"AnthropicTransport/model={self.model}",
                ),
            ),
            tools=cast("list[ToolParam]", tools),
```

with:

```python
        wire_messages, wire_tools = self._wire(messages, tools)
        async with self._client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=wire_messages,
            tools=wire_tools,
```

The `system=` and `thinking=` arguments after them stay as they are.

- [ ] **Step 5: Say so in the README**

In `README.md`, directly after the paragraph that ends "On the openai rail the reasoning arrives as a `thinking` event and the answer stays the answer.", insert a blank line and:

```markdown
On the anthropic rail nare marks the newest message and the last tool for
prompt caching, so a backend that caches reads each turn's history back at its
cache price. Thinking that arrives without a signature, which is what a proxy
makes when it translates a reasoning model, is kept in the session but not sent
back to the model, and neither is empty text.
```

- [ ] **Step 6: Run the transport, streaming and image tests**

```bash
cd /home/smadinya/vividynamics/nare
timeout -k 10 120 uv run pytest -q tests/test_transport.py tests/test_streaming.py tests/test_images.py 2>&1 | tail -3
```

Expected: all pass.

- [ ] **Step 7: Format and run the build**

```bash
cd /home/smadinya/vividynamics/nare
uv run ruff format src tests
timeout -k 10 600 bin/build 2>&1 | tail -5
```

Expected: all clean, `800 passed, 8 deselected`.

- [ ] **Step 8: Commit**

```bash
cd /home/smadinya/vividynamics/nare
git add src/nare/transport/anthropic.py tests/test_transport.py tests/test_streaming.py README.md
git commit -m "feat: mark the newest block and the last tool for prompt caching"
```

---

### Task 3: Compact old write and edit inputs when results are not enough (E4)

**Files:**
- Modify: `src/nare/compact.py` (`_INPUT_STUBS` after `PREFIX` on line 10; `inputs` on `CompactionReport`, lines 54-59; the end of `compact()`, lines 108-115; a new `_elide_inputs()` at the end of the module)
- Modify: `src/nare/loop.py:221-230` (the compaction event in `step()`)
- Modify: `README.md` (the compaction paragraph, "Before each model call, old tool output is elided ...")
- Modify: `docs/contract.md:178-180` (the compaction sentence)
- Test: `tests/test_compact.py` (import `PREFIX`; append `write_transcript()` and five tests at the end)

**Interfaces:**
- Consumes: `Session`, `estimate()` and `PREFIX`, unchanged. `run()` and `FakeProvider` in the event test.
- Produces: `CompactionReport.inputs: int = 0` (a new last field, so positional construction elsewhere still works) and `_elide_inputs(s: Session, window: int, protected_from: int, *, image_input: bool) -> int`. `compact()` keeps its signature, and returns a report when either tier elided something.

- [ ] **Step 1: Write the failing tests**

In `tests/test_compact.py`, change `from nare.compact import compact, estimate` to:

```python
from nare.compact import PREFIX, compact, estimate
```

Append at the end of the file:

```python
def write_transcript(turns: int = 8, chars: int = 4000) -> list[Message]:
    """A write-heavy session: big write inputs, short results."""
    messages = [Message("user", [{"type": "text", "text": "task"}])]
    for turn in range(1, turns + 1):
        path = f"src/f{turn}.ts"
        messages += [
            Message(
                "assistant",
                [
                    {"type": "text", "text": f"writing {path}"},
                    {
                        "type": "tool_use",
                        "id": f"w{turn}",
                        "name": "write",
                        "input": {"path": path, "content": "x" * chars},
                    },
                ],
            ),
            Message(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": f"w{turn}",
                        "content": f"wrote {chars} characters to {path}",
                        "is_error": False,
                    }
                ],
            ),
        ]
    return messages


def test_a_write_heavy_session_compacts_below_sixty_percent() -> None:
    # On the practice run write inputs were 46% of the context and tool
    # results 7.5%, so eliding results alone could not get below 60%.
    s = new_session("go")
    s.messages = write_transcript()
    before = copy.deepcopy(s.messages)
    window = int(estimate(s) / 0.9)
    report = compact(s, window)
    assert report is not None
    assert report.after <= window * 0.6
    assert (report.elided, report.inputs) == (0, 3)
    assert s.messages[1].content[1] == {
        "type": "tool_use",
        "id": "w1",
        "name": "write",
        "input": {
            "path": "src/f1.ts",
            "content": "[elided by nare: wrote 4000 chars to src/f1.ts at turn 1. "
            "Read the file if you need it.]",
        },
    }
    for i in (3, 5):
        assert s.messages[i].content[1]["input"]["content"].startswith(PREFIX)
    # Results, assistant text and the newer writes are untouched.
    for i, message in enumerate(s.messages):
        if message.role == "user" or i > 5:
            assert message == before[i]
        else:
            assert message.content[0] == before[i].content[0]


def test_long_edit_texts_are_elided_and_short_ones_kept() -> None:
    s = new_session("go")
    s.messages = write_transcript()
    s.messages[1].content[1]["name"] = "edit"
    s.messages[1].content[1]["input"] = {"path": "a.py", "old": "z" * 3000, "new": "y"}
    s.messages[3].content[1]["name"] = "edit"
    s.messages[3].content[1]["input"] = {"path": "b.py", "old": "z", "new": "y" * 3000}
    report = compact(s, int(estimate(s) / 0.9))
    assert report is not None
    assert s.messages[1].content[1]["input"] == {
        "path": "a.py",
        "old": "[elided by nare: replaced 3000 chars in a.py at turn 1. "
        "Read the file if you need it.]",
        "new": "y",
    }
    assert s.messages[3].content[1]["input"] == {
        "path": "b.py",
        "old": "z",
        "new": "[elided by nare: wrote 3000 chars into b.py at turn 2. "
        "Read the file if you need it.]",
    }


def test_a_failed_write_keeps_its_content() -> None:
    # Its stub would claim a write that never happened.
    s = new_session("go")
    s.messages = write_transcript()
    s.messages[2].content[0]["content"] = "write was not approved"
    s.messages[2].content[0]["is_error"] = True
    report = compact(s, int(estimate(s) / 0.9))
    assert report is not None
    assert s.messages[1].content[1]["input"]["content"] == "x" * 4000
    assert s.messages[3].content[1]["input"]["content"].startswith(PREFIX)


def test_tool_results_are_elided_before_tool_inputs() -> None:
    # The first tier still runs first: an input goes only when eliding old
    # output was not enough.
    s = new_session("go")
    s.messages = write_transcript()
    for i in range(2, 13, 2):
        s.messages[i].content[0]["content"] = "r" * 4000
    report = compact(s, int(estimate(s) / 0.9))
    assert report is not None
    assert report.elided > 0
    assert report.inputs == 0
    assert all(
        block["input"]["content"] == "x" * 4000
        for message in s.messages
        for block in message.content
        if block["type"] == "tool_use"
    )


async def test_the_compaction_event_counts_elided_inputs() -> None:
    s = new_session("go")
    s.messages = write_transcript()
    window = int(estimate(s) / 0.9)
    fake = FakeProvider([text_reply("done")])
    events = [event async for event in run(s, transport=fake, context_window=window)]
    notices = [e for e in events if "compaction" in e.detail]
    assert len(notices) == 1
    assert notices[0].detail["compaction"]["inputs"] == 3
    assert notices[0].text.startswith(
        "compacted: elided 0 tool results and 3 tool inputs, ~"
    )
```

Why `window = int(estimate(s) / 0.9)`: it puts the session at 90% of its window, above the 80% trigger, whatever the fixture's exact size. On this fixture eliding three writes takes it below 60%, and the two newest turns are protected, so turns 1 to 3 are the ones elided.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /home/smadinya/vividynamics/nare
timeout -k 10 120 uv run pytest -q tests/test_compact.py 2>&1 | tail -10
```

Expected: 5 failed, 6 passed. Three fail at `assert report is not None`, because the first tier skips results shorter than their stubs and returns nothing. `test_tool_results_are_elided_before_tool_inputs` fails only on `AttributeError: ... 'inputs'`: the first tier already elides results there, and the test's job is to guard the tier order. The event test fails at `assert len(notices) == 1`. If any of the five passes, stop and report.

- [ ] **Step 3: Add the second tier**

In `src/nare/compact.py`, directly after `PREFIX = "[elided by nare:"`, insert:

```python

# The second tier: the write and edit fields compaction may elide, and what
# each stub says the call did.
_INPUT_STUBS: dict[str, dict[str, str]] = {
    "write": {"content": "wrote {chars} chars to {path}"},
    "edit": {
        "old": "replaced {chars} chars in {path}",
        "new": "wrote {chars} chars into {path}",
    },
}
```

In `CompactionReport`, add a last field after `window: int`:

```python
    inputs: int = 0
```

At the end of `compact()`, replace:

```python
            elided += 1
    # ponytail: model-written summaries may follow elision if measured runs
    # keep reaching the context stop. No extra model call is made here.
    return (
        CompactionReport(elided, before, estimate(s, image_input=image_input), window)
        if elided
        else None
    )
```

with:

```python
            elided += 1
    inputs = _elide_inputs(s, window, protected_from, image_input=image_input)
    # ponytail: model-written summaries may follow elision if measured runs
    # keep reaching the context stop. No extra model call is made here.
    return (
        CompactionReport(
            elided, before, estimate(s, image_input=image_input), window, inputs
        )
        if elided or inputs
        else None
    )


def _elide_inputs(
    s: Session, window: int, protected_from: int, *, image_input: bool
) -> int:
    """Elide old write contents and edit texts, oldest first; count the calls.

    On the practice run write inputs were 46% of the context and tool results
    7.5%, so eliding results alone could not reach 60%. A failed call keeps
    its input: its stub would claim a write that never happened. `input`
    stays an object, so every call still pairs with its result.
    """
    failed = {
        b.get("tool_use_id")
        for m in s.messages
        if m.role == "user"
        for b in m.content
        if b.get("type") == "tool_result" and b.get("is_error")
    }
    inputs = 0
    turn = 0
    for i, message in enumerate(s.messages):
        if message.role != "assistant":
            continue
        turn += 1
        if i >= protected_from:
            break
        for block in message.content:
            if estimate(s, image_input=image_input) <= window * 0.6:
                return inputs
            stubs = _INPUT_STUBS.get(block.get("name", ""))
            args = block.get("input")
            if (
                block.get("type") != "tool_use"
                or stubs is None
                or not isinstance(args, dict)
                or block.get("id") in failed
            ):
                continue
            path = str(args.get("path", ""))
            touched = False
            for name, did in stubs.items():
                value = args.get(name)
                if not isinstance(value, str) or value.startswith(PREFIX):
                    continue
                stub = (
                    f"{PREFIX} {did.format(chars=len(value), path=path)} "
                    f"at turn {turn}. Read the file if you need it.]"
                )
                if len(stub) < len(value):
                    args[name] = stub
                    touched = True
            if touched:
                inputs += 1
    return inputs
```

`block.get("name", "")`, not `block.get("name")`: mypy strict rejects `Any | None` as a `str` key.

- [ ] **Step 4: Report the inputs in the compaction event**

In `src/nare/loop.py`, in `step()`, replace:

```python
                f"compacted: elided {report.elided} tool results, "
                f"~{report.before:.0f} -> ~{report.after:.0f} tokens",
```

with:

```python
                f"compacted: elided {report.elided} tool results"
                + (f" and {report.inputs} tool inputs" if report.inputs else "")
                + f", ~{report.before:.0f} -> ~{report.after:.0f} tokens",
```

- [ ] **Step 5: Update the README and the contract**

In `README.md`, replace the first six lines of the compaction paragraph:

```markdown
Before each model call, old tool output is elided if estimated context reaches
80% of the window, stopping at 60% where possible. The task, assistant messages,
last two turns, error results and ask results are protected. Every tool call
keeps its result. If estimated context still reaches the window, nare stops
with `stop_reason=context` without calling the model. This character-based
estimate can undercount; a provider can still reject context as `max_tokens`.
```

with:

```markdown
Before each model call, old tool output is elided if estimated context reaches
80% of the window, stopping at 60% where possible. If that is not enough, the
contents of old `write` calls and long `edit` texts are elided next. The task,
assistant text and thinking, the last two turns, error results, ask results and
the inputs of failed calls are protected. Every tool call keeps its result. If
estimated context still reaches the window, nare stops with
`stop_reason=context` without calling the model. This character-based
estimate can undercount; a provider can still reject context as `max_tokens`.
```

The paragraph's last two lines, about window discovery, stay as they are.

In `docs/contract.md`, replace:

```markdown
the caller retains stdout. Context compaction replaces eligible old tool-result
bodies with stubs, preserving pairs and assistant text; prior streamed events
are unaffected.
```

with:

```markdown
the caller retains stdout. Context compaction replaces eligible old tool-result
bodies with stubs, then, if that is not enough, the contents of old `write`
calls and long `edit` texts, preserving pairs and assistant text; prior streamed
events are unaffected.
```

- [ ] **Step 6: Run the compaction, image, TUI and contract tests**

```bash
cd /home/smadinya/vividynamics/nare
timeout -k 10 120 uv run pytest -q tests/test_compact.py tests/test_images.py tests/test_tui_app.py tests/test_contract.py 2>&1 | tail -3
```

Expected: all pass. `tests/test_tui_app.py` quotes today's event text, and `tests/test_contract.py` reads `docs/contract.md`.

- [ ] **Step 7: Format and run the build**

```bash
cd /home/smadinya/vividynamics/nare
uv run ruff format src tests
timeout -k 10 600 bin/build 2>&1 | tail -5
```

Expected: all clean, `805 passed, 8 deselected`.

- [ ] **Step 8: Commit**

```bash
cd /home/smadinya/vividynamics/nare
git add src/nare/compact.py src/nare/loop.py tests/test_compact.py README.md docs/contract.md
git commit -m "feat: compaction elides old write and edit inputs when results are not enough"
```

---

### Task 4: Verify live through the proxy, and with the full build

This task changes no files and makes no commit. Report its numbers to your caller; the PR body needs them.

**Files:**
- Create (scratch only, never in the repo): `<scratch>/live_check.py`, where `<scratch>` is your session's scratchpad directory (or `mktemp -d` if you have none).

**Interfaces:**
- Consumes: `AnthropicTransport` from Tasks 1 and 2, and `OpenAITransport` (unchanged), through the checkout's editable install.
- Produces: the usage lines for the PR body.

- [ ] **Step 1: Write the live-check script**

Save as `<scratch>/live_check.py`:

```python
"""Live check for #67 through nare's own transports.

cache: two turns with the same long prefix on the anthropic rail; the second
should read it from the cache. openai-cache: the same on the openai rail.
thinking: the same history with and without a 23k-character unsigned thinking
block; with the fix both cost the same input tokens.
"""

import asyncio
import os
import sys
import time

import nare.transport.anthropic as anthropic
from nare.session import Message
from nare.transport.anthropic import AnthropicTransport
from nare.transport.openai import OpenAITransport

PREFIX = "\n".join(
    f"Line {i}: the {['red', 'blue', 'green', 'amber'][i % 4]} fox number {i} "
    f"jumps over fence {i * 7 % 101} near river {i * 13 % 59}."
    for i in range(400)
)
TOOLS = [
    {
        "name": "read",
        "description": "Read a file.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    }
]


def history(thinking: bool) -> list[Message]:
    reply: list[dict[str, str]] = [{"type": "text", "text": "Noted."}]
    if thinking:
        reply.insert(0, {"type": "thinking", "thinking": PREFIX, "signature": ""})
    return [
        Message("user", [{"type": "text", "text": "Remember the word kiwi."}]),
        Message("assistant", reply),
        Message("user", [{"type": "text", "text": "Reply with the word OK."}]),
    ]


async def main(mode: str, model: str) -> None:
    print(anthropic.__file__)
    transport: AnthropicTransport | OpenAITransport = AnthropicTransport(
        model=model, base_url=os.environ["NARE_BASE_URL"], max_tokens=16
    )
    if mode == "openai-cache":
        transport = OpenAITransport(
            model=model,
            base_url=os.environ["NARE_BASE_URL"].rstrip("/") + "/v1",
            api_key=os.environ["ANTHROPIC_API_KEY"],
            max_tokens=16,
        )
    if mode in ("cache", "openai-cache"):
        text = f"run {time.time_ns()}\n{PREFIX}\nReply with the word OK."
        messages = [Message("user", [{"type": "text", "text": text}])]
        for attempt in (1, 2):
            reply = await transport.turn(messages, TOOLS)
            print(attempt, reply.usage, f"cost={reply.cost}")
    else:
        for thinking in (False, True):
            reply = await transport.turn(history(thinking), TOOLS)
            print(f"unsigned thinking in history={thinking}", reply.usage)


asyncio.run(main(sys.argv[1], sys.argv[2]))
```

The `run {time.time_ns()}` line makes each run's prefix new, so turn 1 can never read an entry an earlier run left in the cache.

- [ ] **Step 2: Find the haiku alias, then run the cache check on it (acceptance criterion 1)**

```bash
cd /home/smadinya/vividynamics/nare
set -a; . ./.env; set +a
curl -s -H "x-api-key: $ANTHROPIC_API_KEY" "${NARE_BASE_URL%/}/v1/models" \
  | python3 -c 'import json,sys; print([m["id"] for m in json.load(sys.stdin)["data"] if "haiku" in m["id"]])'
timeout -k 10 300 uv run python <scratch>/live_check.py cache <haiku alias> 2>&1 | tail -3
```

Use the alias the first command prints (`claude-haiku-5-5` on 2026-10-15). If it prints none, stop and report: no model on the proxy can show caching.

Expected: the first line is `/home/smadinya/vividynamics/nare/src/nare/transport/anthropic.py`, this checkout and not some other install. Line `1` shows `cache_write` near 11,800 and `cache_read=0`. Line `2` shows `cache_read` near 11,800 and `cache_write=0`, at roughly a tenth of line 1's cost (planning trial: $0.00148, then $0.000126). If line 2 shows `cache_read=0`, run it once more: the entry can expire if the two turns straddle a proxy restart. If it is 0 again, stop and report.

- [ ] **Step 3: Run the spark checks (acceptance criterion 1, spark half; the E2 A/B)**

```bash
cd /home/smadinya/vividynamics/nare
set -a; . ./.env; set +a
timeout -k 10 300 uv run python <scratch>/live_check.py cache spark/glm-5.3-flash 2>&1 | tail -2
timeout -k 10 300 uv run python <scratch>/live_check.py openai-cache spark/glm-5.3-flash 2>&1 | tail -2
timeout -k 10 300 uv run python <scratch>/live_check.py thinking spark/glm-5.3-flash 2>&1 | tail -2
```

Expected, as in planning: both cache runs show `cache_read=0` on both turns, with the same input count each time (about 8,470). Spark reports no cache tokens on either rail, so LiteLLM has nothing to map (Decision 2). Report it as found; it is not a failure of this change. The thinking run shows the same `input` on both lines (170 in planning; `origin/main` gave 8,470 for the second line). If the second line's `input` is thousands higher than the first, Task 1 is not in effect: stop and report.

- [ ] **Step 4: Confirm the OpenAI rail already reads cached tokens (Decision 1)**

```bash
cd /home/smadinya/vividynamics/nare
git diff origin/main --stat -- src/nare/transport/openai.py
timeout -k 10 120 uv run pytest -q tests/test_openai_transport.py tests/test_streaming.py -k "cached_prompt_tokens or normalizes_final_reply" 2>&1 | tail -3
```

Expected: no diff for `openai.py`, and 3 passed (one OpenAI test, and the streaming test once per provider).

- [ ] **Step 5: Final build on the branch**

```bash
cd /home/smadinya/vividynamics/nare
timeout -k 10 600 bin/build 2>&1 | tail -5
git log --oneline origin/main..HEAD
git status --short
```

Expected: `bin/build` clean with `805 passed, 8 deselected`, exactly four commits on the branch (this plan's, then Tasks 1 to 3), and `git status` showing only the untracked `.bench-report/`.

---

## Acceptance criteria map

| Criterion from #67 | Where |
| --- | --- |
| Two calls with the same prefix to a haiku model through the proxy: the second shows `cache_read > 0` | Task 4, Step 2 (`claude-haiku-5-5`, Decision 2) |
| For spark, check whether LiteLLM maps `cached_tokens` | Task 4, Step 3: spark reports none on either rail; Step 4: nare already reads it where it is sent |
| `messages_from` drops `{"type":"thinking","signature":""}` and empty text blocks, and keeps signed thinking | Task 1, `test_unsigned_thinking_and_empty_text_are_not_sent_back` and `test_an_assistant_turn_left_empty_is_dropped_and_the_user_turns_join` |
| A write-heavy fixture session drops below 60% of its window after compaction | Task 3, `test_a_write_heavy_session_compacts_below_sixty_percent` |
| `bin/build` passes | Tasks 1 to 3, build steps, and Task 4, Step 5 |
