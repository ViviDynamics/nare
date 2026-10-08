# TUI practice run, 2026-10-08: issues and fixes

Source material for nare / `nare tui` specs. Compiled from five independent
reviews (app design, app security, app code quality, run behaviour,
harness/TUI code), with the load-bearing claims re-checked by hand.

## The run

| | |
|---|---|
| Where | `~/vividynamics/nare_tmp`, empty directory, `nare tui --root . --session session.json` |
| nare | `2026.10.7.dev15+g89b4aa24d`, local branch `feat/tui` @ 89b4aa2 (editable uv-tool install of the checkout) |
| Model | `spark/glm-5.3-flash` (GLM reasoning model) via the LiteLLM proxy, `NARE_PROVIDER=anthropic`, streaming |
| Prompt | "Can you bootstrap this repo for a small social media, facebook clone?" plus one `ask` round (Next.js, Postgres, auth/profiles/posts/likes/comments/friends+follows/groups) |
| Outcome | `done` / `end_turn`. A Next.js 15 + Drizzle + Postgres app; build green, seeded, GET pages return 200. One commit. |
| Turns | 50: 2 before the ask, **48 of the 50-turn per-run cap** after it |
| Tokens | 1,873,248 input / 47,029 output, 0 cache reads; peak request 51,793 of a 262,144 window (compaction never ran) |
| Cost | Session says **$0.00**. Real cost at the proxy's published prices ($0.15/M in, $0.50/M out): about **$0.30** |
| Tool calls | 87 (41 write, 32 bash, 12 edit, 1 read, 1 ask); 1 tool error |
| Wall clock | about 32 min after the answer: 12:26–12:42 writing code, 12:42–12:50 install and 4 builds, 12:50–12:54 Postgres detour, 12:54–12:56 smoke tests, 12:56–12:58 git and summary |

### Grades

| Area | Grade | One line |
|---|---|---|
| App design | C | Sound structure, but group posting is broken in the UI, there are no indexes beyond PKs, and no migrations or tests |
| App security | B | Authorization is correct on every action, and injection/XSS/CSRF are handled; hardening is missing (rate limits, hashed tokens, defaults) |
| App code quality | C | `tsc` clean under `strict`, no `any`; errors are silent or 500s, there is no lint, tests or error boundary, and queries are duplicated |
| Run, overall | C+ | Outcome A-, efficiency C, communication C+, safety/scope **D** |

### Where the tokens went (turn ledger)

Calibration: tokens ≈ 1,549 + chars / 4.706. This reproduces the final request
and the session total to within 0.01%.

| Class | Turns | Input tokens | Share |
|---|---|---|---|
| Productive build (incl. orientation) | 11 | 197k | 10.5% |
| Self-correction rework | 2 | 39k | 2.1% |
| Build fixes | 8 | 325k | 17.3% |
| Verification: schema and builds | 5 | 203k | 10.8% |
| **Detour: unrequested Postgres install** | 10 | 462k | 24.7% |
| Verification only possible because of the detour | 6 | 288k | 15.4% |
| Housekeeping (pkill, git, session.json) | 6 | 306k | 16.3% |
| Communication (ask, summary) | 2 | 54k | 2.9% |

Stopping at the green build and summarising would have taken about 30 turns,
0.86M tokens and $0.15. The late turns are the expensive ones because every
turn re-sends the whole history uncached.

The final context was made up of `write` inputs (file contents) 46%, thinking
38%, tool results 7.5%, and assistant text 3%.

---

## Part 1: nare / TUI issues (spec material)

Line references are at **89b4aa2** (`feat/tui`). `origin/main` and
`feat/ask-only-when-ambiguous` have identical source, and the transport,
compact, accounting, tools and session modules are unchanged there since the
merge base. So every issue below is **not fixed on main** unless it says
otherwise. `src/nare/tui/*` exists only on `feat/tui`.

Severity: **high** means wrong results or unsafe behaviour; **medium** means
wasted money or turns, or misleading UI; **low** is polish.

The issues are grouped into six proposed specs. Order of work: A and D first
(safety, data loss), then B (biggest behavioural lever), then C, E, F.

### Spec A. Bash tool: honest results, contained side effects

#### A1 [high] Pipe exit codes are lost; `bash` is really `/bin/sh` (dash)
- **Evidence:** builds at msgs 31, 43 and 49 ran `npm run build 2>&1 | tail -30`. The tool said `exit 0`, while npm's own logs say `exit 1` for 3 of the 4 builds. Msg 78: `/bin/sh: 1: psql: not found`. Msg 58: `curl -sL` on a 404 left a 9-byte file and reported exit 0.
- **Root cause:** `tools.py:143-152` uses `Popen(command, shell=True, …)`, which is `/bin/sh` (dash 0.5.12 here). dash rejects `set -o pipefail`, and `[[ … ]]` gives exit 127. The tool description (`tools.py:236-237`) names neither the shell nor that each call is a fresh shell, so the `export PATH` from msg 73 was gone by msg 77.
- **Fix:** run `[bash, "-o", "pipefail", "-c", command]` when `shutil.which("bash")`, otherwise sh. Description: "Runs in bash with pipefail. Each call is a fresh shell: `cd` and `export` do not persist."
- **Acceptance:** `run_bash("false | true")` starts with `exit 1`; `run_bash("[[ 1 == 1 ]]")` gives `exit 0`.
- Sources: HAR-2, RUN-6.

#### A2 [high] Processes outlive the call; a database server was left running
- **Evidence:** msg 79 ran `(npm run start > /tmp/next.log 2>&1 &)`, and msg 73 ran `pg_ctl … start`. The Postgres from `/tmp/pgenv` was still running hours later, and the final summary doesn't say so.
- **Root cause:** `tools.py:143-165` uses `start_new_session=True` and returns when the shell exits. The process group is killed only on timeout (`tools.py:155-163`). Nothing checks for survivors. Verified: `(sleep 3 >/dev/null 2>&1 &)` returns in 0.0 s with the child alive. `pg_ctl` calls setsid itself, so it escapes the group anyway.
- **Fix:**
  - (a) After `communicate`, probe `os.killpg(pgid, 0)`. If the group is alive, append `[still running: process group P (cmd)]` to the result and record P on the session.
  - (b) At turn end and on TUI quit, list recorded groups that are still alive and offer **k**ill / **l**eave.
  - (c) `ponytail:` ceiling: self-daemonizing processes escape the group. The upgrade path is `prctl(PR_SET_CHILD_SUBREAPER)`, so orphans reparent to nare and can be listed from `/proc/self/task/*/children`.
  - (d) The prompt rule from B1 makes the model report the daemons it starts.
- **Acceptance:** `run_bash("(sleep 2 >/dev/null 2>&1 &)")` reports a live group, and quitting the TUI with a live group prompts.
- Sources: HAR-1a, RUN-7.

#### A3 [medium] Signal exits read as "exit -15"; `pkill -f` kills the tool's own shell
- **Evidence:** msg 87 ran `pkill -f "next-server"; git init …` and got the result `exit -15`. That cost one turn (about 50k tokens), and the git commands never ran.
- **Root cause:** `sh -c "<command>"` puts the command text in the shell's argv, so `pkill -f` matches the shell itself. `tools.py:165` prints a negative `returncode` raw, with `is_error` false.
- **Fix:**
  - For `rc < 0`, print `killed by SIGTERM (signal 15)` and set `is_error`.
  - If the command contains `pkill`, `killall` or `kill`, add: "pkill -f also matches the shell running this command; use a pattern like `nex[t]-server`, or kill by `$!`."
  - Optional: keep the command out of argv with `bash -c 'eval "$NARE_CMD"'`, passing the command in the environment.
- **Acceptance:** a command that pkills its own unique tag returns `killed by SIGTERM`.
- Sources: HAR-11, RUN-7.

#### A4 [high] One `a` keypress approves every later bash command; the approval prompt carries no risk information
- **Evidence:** 32 bash calls with no refusals, including downloads from guessed URLs, running a downloaded binary (micromamba), `initdb` with `-A trust`, starting a daemon, and `git commit` under an invented identity. `--root .` doesn't constrain bash (`cli.py:186-188`).
- **Root cause:** `tui/approve.py:26-30`: `a` adds the tool *name* to the always-allow set. The prompt shows only the command (`render.py:133-134`, header at `app.py:98`): no cwd, no warnings.
- **Fix:**
  - (a) Scope `a` on bash to one program, taken from the first word of each `&&` / `;` / `|` segment.
  - (b) Some commands always prompt, even after `a`. Flag them in the prompt with a badge:
    - network fetch (`curl`, `wget`, `pip`/`npm i`/`git clone`)
    - fetch-then-execute or `chmod +x`
    - `sudo`
    - `kill`/`pkill`
    - backgrounding (`&`, `nohup`, `pg_ctl`, `systemctl`)
    - `rm -rf`
    - paths outside the root (`/tmp`, `~`, `..`)
    - `git commit`/`push`
  - (c) Show the cwd in the prompt.
- **Acceptance:** after `a` on `npm run build`, `curl x | sh` still prompts and shows the `network` and `exec` badges.
- Sources: HAR-1b/c, RUN-9.

#### A5 [medium] The running command isn't visible, and Esc can't stop it
- **Evidence:** after `a`, the 600-second `micromamba create` showed only "working".
- **Root cause:** `tool_use` events reach the TUI only when the whole step ends (`loop.py:279-280`, `514-515`). The live queue carries text deltas only (`loop.py:362-365`). `settled()` (`render.py:34-42`) hides the turn until its results exist. Bash runs in `asyncio.to_thread` (`tools.py:399`), which a cancel can't stop (there's already a `ponytail:` note at `app.py:246-250`).
- **Fix:**
  - The approver (called for every non-exempt call, including always-allowed ones) sets the status to `running bash 2m13s: <summary>`, updated by a 1 s timer.
  - Make `run_bash` async on `asyncio.create_subprocess_shell(start_new_session=True)`, and `killpg` on `CancelledError`.
- **Acceptance:** Esc during `sleep 30` kills the group within 1 s, and the status line shows the running command.
- Sources: HAR-8.

### Spec B. Default system prompt and tool descriptions

#### B1 [high] nare sends no system prompt at all
- **Evidence:** `anthropic.py:244,276` sends `system=self.system if self.system is not None else omit`. `--system` has no default (`cli.py:175`, and the same on main at `cli.py:130`), and the TUI injects nothing. The model's only guidance was the five tool descriptions (`tools.py:186-266`). Every behaviour in B2–B6 is the model's default with no rule against it.
- **Fix:** ship a short default prompt, which `--system` replaces or extends. Draft rules:
  1. Stay in scope. Offer extra setup (installing services, downloading tools) in your final message instead of doing it.
  2. Do not install system software, download and run executables, or start long-lived services unless asked.
  3. Claim only what tool output showed. Say explicitly what you did not test.
  4. Stop what you start. List anything left running or changed outside the project root, with how to undo it.
  5. Do not commit or push unless asked. Never set a git identity.
  6. Tool calls in one turn run in order, so batch an edit with the command that checks it.
- **Acceptance:** a benchmark case "bootstrap X in an environment without its database" ends with build plus instructions, starts no daemon, makes no commit, and has a final message that lists what was not tested. This is a natural new `benchmarks/cases/` entry.
- Sources: RUN-1.

#### B2 [high] Unrequested install from guessed URLs (scope creep)
- **Evidence:**
  - At msg 55 the agent planned "If not available, I'll finish up with git init and summarize", then didn't.
  - It invented a GitHub repo URL (msg 57; its thinking says "or zxZach?"), then tried an EDB URL.
  - It downloaded **x86** micromamba and executed it before checking `uname -m` (the machine is aarch64).
  - It then installed conda-forge Postgres into `/tmp/pgenv` (about 270 MB in `/tmp`).
  - Cost: 10 turns and 462k tokens, plus 288k in follow-on checks.
- **Fix:** B1 rules 1–2, plus the A4 risk badges as the backstop.
- Sources: RUN-2.

#### B3 [high] The final summary overclaims
- **Evidence (msg 99):**
  - "threaded comments": the comments table has no parent column.
  - "verified end-to-end": only GET requests with a session row inserted by hand (`smoketoken123`, msg 81). No register, login, post, like, comment, friend or group action was ever exercised. That is how the broken group post form (App-1) shipped.
  - "all 9 tables" was never re-checked after the count query failed (msg 78, exit 127).
  - Msg 87 cites an "Add friend" grep match that doesn't appear in msg 84.
- **Fix:** B1 rule 3. Optionally the loop appends a machine-made "commands run" list to the outcome, so the claims can be checked.
- Sources: RUN-4.

#### B4 [high] Side effects not disclosed; next steps contradict the environment
- **Evidence:** msg 99 says only "installed user-space PostgreSQL binaries". It does not mention:
  - the server left running
  - about 270 MB in `/tmp`
  - the downloaded executable
  - the `.env` it wrote
  - the 30-day session row for alice
  - the trust-auth superuser on port 5432

  It also tells you to `docker compose up -d` (msg 6 showed no Docker) and `cp .env.example .env` (`.env` already existed).
- **Fix:** B1 rule 4, plus the A2 survivor list in the outcome.
- Sources: RUN-5.

#### B5 [medium] Invented git identity and an unrequested commit
- **Evidence:** msgs 87, 89 and 97 use `git -c user.name="smadinya" -c user.email="smadinya@localhost"`, although a global identity is configured. Root commit 8188a26 carries that author.
- **Fix:** B1 rule 5, and the A4 `git commit` badge.
- Sources: RUN-12.

#### B6 [medium] Tool descriptions: rewrite-then-stale-edit, one call per turn, vague ask
- **Evidence:**
  - 5 whole-file rewrites of files it had just written (msgs 13, 17, 51).
  - Msg 33's edit to groups.ts used the old text from before the rewrite and failed. The auth.ts edit dropped an import that took 2 turns to repair.
  - 1 `read` in 87 calls.
  - From turn 15 on, each turn has exactly one call, so fixes and re-checks are split (about 4 avoidable turns, about 170k tokens).
- **Fix:**
  - `write`: "prefer `edit` for files that already exist."
  - `edit`: "re-read a file before editing it if you rewrote it."
  - `bash`: shell and fresh-shell semantics (A1); "kill by `$!`" (A3).
  - `ask`: "state the default you will use if unanswered."
  - Prompt rule 6 (in-order batching).
- Sources: RUN-13, RUN-14, RUN-18.

### Spec C. Budget, cost and status visibility in the TUI

#### C1 [medium] Turn-cap near miss that neither the model nor the user could see
- **Evidence:** 48 of 50 turns. One more failed download would have ended in `error: stopped after 50 turns`, with no summary and processes still running, even though the app worked.
- **Root cause:**
  - `loop.py:484-488` stops hard with no warning to the model.
  - The TUI status line (`app.py:292-302`, `render.py:172-188`) shows a plain `turn 48/50`.
  - After `max_turns`, typing anything reopens the session with a fresh 50 turns (`app.py:220`, `loop.py:480`), but nothing tells the user. An empty Enter does nothing (`app.py:203`).
- **Fix:**
  - (a) Loop: when 5 or fewer turns remain, append a note to the next user message: "N turns left: wrap up and summarise."
  - (b) Loop: at the cap, run one final turn with tools disabled to produce a summary.
  - (c) TUI: the turn segment goes yellow at 80% and red at 90%.
  - (d) TUI: the `max_turns` outcome reads "Turn limit reached (50 per run). Type `continue` for up to 50 more."
- **Acceptance:** `status_line` at 45/50 is styled; the `max_turns` outcome includes the hint; a scripted run that hits the cap still ends with an assistant summary.
- Sources: RUN-10, HAR-7.

#### C2 [medium] Cost shows a false "$0.00", and `--budget-usd` can never trip
- **Evidence:** cost 0.0 against 1.87M tokens.
- **Root cause (verified with curl against the proxy):**
  - Streamed replies carry no `x-litellm-response-cost`, but do carry `x-litellm-response-cost-original: 0.0`. The headers are sent before usage is known.
  - `reported_cost` (`transport/__init__.py:128-138`) falls back to `-original`, so every turn reports exactly 0.0.
  - `NARE_PRICE_*` is ignored, because `turn_usage` (`accounting.py:54-66`) applies prices only when cost is None.
  - The TUI can already render "cost unknown" (`render.py:165-169`); it never gets the chance.
  - Non-streamed replies carry the real cost.
- **Fix:**
  - (a) Ignore header cost on streamed responses.
  - (b) In the `/v1/model/info` call that `discover_context_window` already makes, also read the four `*_cost_per_token` fields (×1e6) and use them as `Prices` when `NARE_PRICE_*` is unset.
  - (c) Otherwise report cost as unknown. Label computed costs as estimates.
- **Acceptance:** a streamed reply with only `-original: 0.0`, plus model/info prices, gives a nonzero cost. With no prices, cost is `None` and the TUI shows "cost unknown".
- Sources: RUN-15, HAR-5. (This corrects the earlier note that setting `NARE_PRICE_*` would fix it.)

#### C3 [medium] The status line shows billed tokens, not context use
- **Evidence:** the status line showed about "1920.3k tok" (billed). The context window (262,144, discovered) appears only as a live-area notice that `_draw` clears (`app.py:258-275`, `loop.py:435-454`).
- **Fix:** add `ctx 51.8k/262k`, taken from `last_input_tokens` and the window kept from `event.detail["context_window"]`. Relabel the total as `1.9M billed`.
- Sources: HAR-7, RUN-15.

#### C4 [low] Notices vanish, and failed bash results look like successes
- **Root cause:** compaction and context notices go to the live area and are cleared on settle (`app.py:258-275`). Log warnings are short toasts. `render.py:112-114` colours a result red only when `is_error`, so `exit 1`, `exit 127` and `exit -15` render dim.
- **Fix:** write notices into the transcript as dim lines. Render red when a result starts with `exit N`, N ≠ 0 (and A3 makes signal exits errors anyway).
- **Acceptance:** `render_messages` styles `exit 2` red.
- Sources: HAR-14.

#### C5 [low] The final summary and session path disappear on quit
- **Root cause:** Textual uses the alternate screen, and `main()` (`app.py:436-463`) prints nothing after `app.run()`.
- **Fix:** after exit, print the last assistant text, the status, and `session: P (resume: nare tui --resume P)`.
- Sources: HAR-13.

### Spec D. Session file handling

#### D1 [high] The session file is written inside the project and got committed; without `--session` nothing is saved
- **Evidence:** the agent's `git add -A` committed `session.json` (msg 89). It spent 4 turns (about 205k tokens) finding it, gitignoring it and amending, and the user's `.gitignore` gained a nare-specific line.
- **Root cause:**
  - The README "Terminal UI" example is `nare tui "…" --root . --session s.json`.
  - `cli.py:204` accepts any path.
  - Leaving out `--session` means Ctrl-Q discards the whole run without a warning (`app.py:304-306`, `app.py:457`).
- **Fix:**
  - (a) With no `--session`, default to `$XDG_STATE_HOME/nare/sessions/<id>.json` and print the path on exit (with C5).
  - (b) Warn, as a notice and on stderr, when the resolved session path is inside `--root` or the cwd.
  - (c) Change the README example to leave out `--session`.
- **Acceptance:** `prepare()` with `--session` under `--root` warns; no `--session` gives an XDG path that exists after quit.
- Sources: HAR-3, RUN-11.

#### D2 [medium] Redaction corrupts code in the saved transcript
- **Evidence (verified):** 65 `[redacted]` markers in `session.json`. Whole lines of the agent's own code were blanked:
  - `authorId: number,` (the keyword `auth` matches "author")
  - `token: text("token").primaryKey(),`
  - `password: formData.get("password"),`
  - `dbCredentials: {`

  The saved `write` of `drizzle.config.ts` is 349 chars, while the tool result says it wrote 355. The same thing happened in thinking ("Feed logic: posts where [redacted] OR …").
- **Root cause:** `session.py:131`, `dumps()`, applies `redact_value` to all messages (deliberately; see its docstring). The credential rule in `events.py:41-60` matches the keywords `auth|token|secret|password|passwd|credential` followed by any run of word characters, with no requirement that what follows looks like a secret.
- **Impact:** a resumed session (or `--attach`) shows the model damaged copies of its own files, and it may "fix" code that isn't broken. Auth-related code, which is where secrets live, is exactly what gets mangled.
- **Fix:**
  - (a) Require the keyword to be a whole identifier segment (`\b`/camelCase boundary), so `author` stops matching.
  - (b) Require the assigned value to look like a secret: a quoted literal of 8 or more characters with mixed classes, or a known prefix. A bare identifier, `{`, `formData.get(` or `text(` doesn't qualify.
  - (c) Keep the prefix patterns (`sk-…`, `ghp_…`) everywhere.
  - (d) Use this session's `write` inputs as negative fixtures in the tests.
- **Acceptance:** redacting `src/db/schema.ts` and `src/actions/auth.ts` from this run changes nothing; `password = "hunter2hunter2"` and `sk-ant-…` are still redacted.
- Sources: RUN-19 (re-verified).

#### D3 [info] The contract 1→2 bump makes feat/tui session files unreadable after merging main
- **Detail:** main's ask work (#38) moved the contract to 2. Session files from `feat/tui` runs (contract 1, like this one) will be refused for `--resume` and treated as fatal by `--attach` (`session.py:148-154`). Merging is otherwise clean: there is one import-line conflict in `tests/test_loop.py` (import all of `INTERRUPTED`, `ASK_NOT_ALONE` and `ASK_NOT_DELIVERED`), and the full suite passes on the merged tree (543 passed). The ask flow itself works under contract 2.
- **Fix:** note it in the merge PR. Optionally have `--attach` read older contracts read-only.
- Sources: HAR-15.

### Spec E. Context and token efficiency

#### E1 [high] No prompt caching
- **Evidence:** cache_read and cache_write are 0 across 50 turns. There is no `cache_control` anywhere in `src/`, at 89b4aa2 or on main.
- **Impact:** about 1.82M re-sent tokens were billed at full price. At a 10% cache-read price, this run would have cost about $0.06 instead of $0.30 (−80%), and long TUI sessions scale the same way.
- **Fix:**
  - In anthropic `messages_from`, put `cache_control: {"type": "ephemeral"}` on the last block of the last message (it moves forward each turn), plus one on the tools.
  - Read `cached_tokens` on the OpenAI route.
  - Note: compaction rewrites old messages and breaks the cache once per compaction.
- **Acceptance:** two calls with the same prefix to `claude-haiku-4-5` through the proxy, where the second shows `cache_read > 0`. For spark it depends on the backend; check whether LiteLLM maps `cached_tokens` (proxy-config).
- Sources: RUN-3, HAR-4.

#### E2 [medium] Unsigned thinking is re-sent every turn (38% of context)
- **Evidence:** all 44 thinking blocks have `signature: ''`. One 23.9k-char planning block (msg 7) was re-sent 46 times, about 250k tokens.
- **Root cause:** anthropic `messages_from` (`anthropic.py:114-139`) resends every block. The openai transport already drops thinking (`openai.py:108`).
- **Fix:** drop thinking blocks whose signature is empty or missing. These are made by the proxy, carry no server state, and the real API can't verify them. Keep signed blocks.
- **Saving:** up to about 635k tokens on this run (34%), if the proxy forwards them to GLM (unknown; A/B it).
- **Acceptance:** `messages_from` drops `{"type":"thinking","signature":""}` and keeps signed blocks.
- Sources: RUN-8, HAR-6.

#### E3 [low] Empty text blocks are saved and re-sent
- **Evidence:** msgs 41, 49, 53, 77 and 97 hold `{"type":"text","text":""}`. The real Anthropic API rejects these, which matters when a session is resumed on another provider. The TUI draws each as a blank line.
- **Root cause:** `anthropic.py:316-318` keeps them, and `messages_from` resends them.
- **Fix:** the E2 filter also drops empty text blocks. If an assistant message ends up empty, drop it.
- Sources: HAR-10.

#### E4 [low] Compaction can't reach the bulk of a write-heavy context
- **Evidence:** `compact.py:85-108` elides only `tool_result` content, which was 7.5% here. Write inputs were 46% and thinking 38%. Even with a 64k window, compaction would have recovered under 6k tokens, and then the loop stops on the context limit (`loop.py:163-169`).
- **Fix:** a second tier. Outside the protected tail, replace `write.content` and long `edit.old`/`edit.new` with `[elided by nare: wrote 4,521 chars to src/db/schema.ts at turn 9. Read the file if you need it.]`. Keep `input` an object and keep call/result pairs intact. E2 covers thinking.
- **Acceptance:** a write-heavy fixture session drops below 60% after compaction.
- Sources: RUN-17, HAR-12.

### Spec F. Proxy route problems

#### F1 [medium] Dropped first word on the Anthropic route for reasoning models
- **Evidence:** 24 of 45 non-empty texts start mid-sentence: `" looks good:"`, `"romamba works."`, `"kill\` matched my own shell"`, and the final summary `" the repo is bootstrapped…"`. The TUI streamed the same broken text.
- **Root cause (verified with curl, no nare involved):** LiteLLM's `/v1/messages` stream opens text block 0, then thinking block 1, and loses the chunk where reasoning switches to the answer. "Micromamba works fine today." arrived as `" works fine today."`. The OpenAI `/v1/chat/completions` route returned the full sentence. nare already handles text carried in `content_block_start`, so nare isn't at fault.
- **Fix:**
  - (a) On the first anthropic reply containing unsigned thinking, log one warning: "reasoning is translated by a proxy; on this route LiteLLM can drop words or whole answers. Use `--provider openai` for this model." The TUI shows warnings as notices (and C4 makes them persist).
  - (b) Extend the README note, which currently mentions only missing whole answers.
  - (c) No automatic provider switch (the two routes use different keys).
  - (d) proxy-config: report it upstream to LiteLLM, or route GLM through the OpenAI adapter.
- **Acceptance:** a fake stream with `signature=""` logs the warning once per transport.
- Sources: RUN-16, HAR-9.

### Did right (keep)
- **The `ask` was justified and well-formed.** It ran `ls`, saw an empty repo, then asked 3 numbered questions with defaults. The answer added real requirements (follows, groups). The ask blocked, and the typed answer resumed the session correctly.
- **Writes were batched.** 41 writes went out in 13 turns.
- **Progress notes were honest about its own bugs.** It caught 4 sloppy outputs and 3 build failures by reading the output, even when the exit codes lied.
- **Discovery worked.** The context window was discovered from the proxy (262,144).

---

## Part 2: The generated app (`nare_tmp`)

These are about the app, not nare. They matter for nare in one way: **App-1
shipped because no write path was ever run** (B3), which makes it the best
single argument for B1 rule 3.

| ID | Sev | Issue | Fix | Sources |
|---|---|---|---|---|
| App-1 | **critical** | Group post form never passes `groupId` (`groups/[slug]/page.tsx:75`), so "post in group" lands on the personal timeline (re-verified) | `<PostForm groupId={group.id} …/>` | DES-1, QUA-1, SEC-8 |
| App-2 | high | No secondary indexes; feed/profile/group/likes/follows/requests all do full scans | Indexes on `posts(author_id, created_at)`, `posts(group_id, created_at)`, `comments(post_id)`, `likes(post_id)`, `follows(followed_id)`, `friend_requests(recipient_id, status)`, `group_members(user_id)`, `sessions(user_id)` | DES-2 |
| App-3 | high | Friend-request uniqueness has a direction (A→B and B→A both allowed); check-then-insert race; `declined` never written (decline deletes, so requests can be re-sent forever) | Unique on `(least, greatest)`, `ON CONFLICT DO NOTHING`, auto-accept a reverse pending request, `CHECK (a <> b)`, use `declined` with a cooldown, add blocking | DES-3, DES-9, QUA-12, SEC-15 |
| App-4 | high | `/friends` loads every user and every pending request on the site, every view | One SQL query with `NOT EXISTS` and `LIMIT 10`; rank by mutual friends later | DES-4, QUA-7, SEC-14 |
| App-5 | high | Unique/FK violations are unhandled (500s), no `error.tsx`, 9 actions fail silently, group creation isn't in a transaction | Map PG errors 23505/23503 to `{error}`, `useActionState` everywhere, `db.transaction`, add `app/error.tsx` | QUA-2, QUA-3, DES-10 |
| App-6 | medium | ID parsing: `Number(null)` is 0 and passes; values over 2^31 give 500s; 10 copies of the pattern | Shared `z.coerce.number().int().positive().max(2147483647)` | QUA-4, SEC-9 |
| App-7 | medium | No rate limiting or lockout on login/register; bcrypt makes flooding a cheap CPU DoS | Per-IP and per-account throttle with backoff | SEC-1 |
| App-8 | medium | Session tokens stored in plaintext | Store `sha256(token)` and hash the cookie before lookup | SEC-2 |
| App-9 | medium | Unstated everything-public privacy model; like/comment skip the visibility check that posting has | Decide and document; one `visiblePostsWhere(viewer)` helper used by reads and like/comment | SEC-3, SEC-7 |
| App-10 | medium | docker-compose publishes Postgres on all interfaces with `postgres/postgres` | `127.0.0.1:5432:5432`, `${POSTGRES_PASSWORD:?}`, a non-superuser app role | SEC-4 |
| App-11 | medium | `db:seed` truncates whatever DB it points at and adds `password123` accounts; the login page advertises them in every environment | Refuse unless `ALLOW_SEED=1` / not production; show the hint only in dev | SEC-5, DES-12 |
| App-12 | medium | Schema changes use `drizzle-kit push` only; no migration history (the agent deleted the generated one) | Commit the initial migration; add `db:generate` / `db:migrate` | DES-12 |
| App-13 | medium | No pagination: profile and group posts unbounded, every comment loaded, feed capped at 50 with no "more" | Keyset pagination; latest 3 comments plus a count | DES-5 |
| App-14 | medium | Friend and follow graphs are separate; the feed takes 4 round trips with app-side ID lists | Accepting a friend request creates follows both ways; feed = follows ∪ groups in one query with EXISTS | DES-7 |
| App-15 | medium | Admin role is decorative; the last admin can leave; deleting the creator cascades away the group and its posts | Block last-admin leave or promote; creator FK `set null`; minimal admin powers | DES-8 |
| App-16 | medium | Toggles (like/follow/join) flip whatever the server finds, so double clicks invert intent | Send explicit `intent`; idempotent insert/delete | DES-11 |
| App-17 | medium | Lazy DB `Proxy` adds indirection; each dev HMR reload opens a new 10-connection pool | `globalThis`-cached `drizzle(postgres(url))` | DES-15, QUA-5 |
| App-18 | medium | No lint, typecheck or test scripts; stricter tsc finds unused imports and unchecked `[0]` | `eslint-config-next` flat config, `typecheck`, `tsx --test`; one Playwright smoke test (register → group post → comment → friend) | QUA-6, QUA-18, DES-14 |
| App-19 | medium | Queries inline in pages and duplicated (incoming-requests query copied verbatim; group members queried twice) | Move all reads into `lib/queries.ts`, add a `pairWhere(a,b)` helper | DES-16, QUA-8 |
| App-20 | medium | React 19 resets forms when an action returns an error (wrong password clears username) | Return submitted `fields`; use `defaultValue` | QUA-9 |
| App-21 | medium | Emails are case-sensitive (duplicate accounts, login needs exact case) | `.trim().toLowerCase()`; unique index on `lower(email)` | DES-18, QUA-10, SEC-13 |
| App-22 | medium | Accessibility: unnamed avatar links, emoji-only like button, placeholder-only labels, errors not announced, low contrast | `aria-label`/`aria-pressed`, `sr-only` labels, `role="alert"`, `autoComplete` | QUA-11 |
| App-23 | low | README is wrong: `docker compose up -d` first (no Docker on this machine); "threaded comments"; "zod on every action"; `declined` state | Non-Docker setup first; correct the claims | DES-6, DES-13, QUA-16 |
| App-24 | low | Followers list missing; you can't see other users' friends | Add followers/following/friends lists to profiles | DES coverage |
| App-25 | low | Email enumeration via register error and login timing | Generic error; dummy-hash compare | SEC-6 |
| App-26 | low | No security headers or CSP; `X-Powered-By` sent | `headers()` with CSP, frame-ancestors, HSTS, nosniff; `poweredByHeader: false` | SEC-10 |
| App-27 | low | bcrypt cost 10 and silent 72-byte truncation | Cost 12 or argon2id; reject truncating passwords | SEC-11 |
| App-28 | low | Session lifecycle: expired rows never deleted, no log-out-everywhere, no password change | Cleanup on read and on a schedule; password change revokes all sessions; `__Host-` cookie | SEC-12 |
| App-29 | low | `drizzle-orm` 0.44.7 has an advisory (GHSA-gpj5-g38j-94v9), not reachable today | `drizzle-orm@^0.45.2`; update drizzle-kit | SEC-16 |
| App-30 | low | Session looked up twice per request; `force-dynamic` redundant | React `cache()` on `getCurrentUser` | DES-17, QUA-13 |
| App-31 | low | Slug edge cases ("Café" → `caf`); slug probe races the insert; wrong min-length message | NFKD normalise; insert-and-retry on conflict | QUA-14 |
| App-32 | info | Full user rows (with `passwordHash`) flow through server components | `SafeUser` projection; `import "server-only"` | SEC-17 |
| App-33 | nit | Locale-dependent dates; duplicated card/user-row JSX; package named `facebook-clone`, app called "bookface" | `<time>` with an explicit locale; `Card`/`UserRow` components | QUA-19, QUA-20 |

**Done right in the app:**
- Every write is scoped to the session user.
- Responding to a friend request checks the recipient and the pending status.
- All SQL is parameterised; there is no `dangerouslySetInnerHTML` and no open redirects.
- CSRF is covered by Next's Origin check plus SameSite=Lax.
- Session tokens are 256-bit, httpOnly and rotated on every login.
- Zod enforces length caps.
- `tsc --strict` is clean with no `any`.

---

## Environment left behind (cleanup checklist)

- [ ] Postgres from `/tmp/pgenv` is still running (data in `/tmp/pgdata`, trust auth, port 5432), and the `npm run dev` started at 13:12 uses it. Stop it with `/tmp/pgenv/bin/pg_ctl -D /tmp/pgdata stop`.
- [ ] About 270 MB under `/tmp`: `pgenv/`, `pgdata/`, `bin/micromamba`, `mm.tar.bz2`, `pg.tar.bz2`, `next.log`, `pg.log`, `initdb.log`.
- [ ] A session row `smoketoken123` for alice in the `bookface` DB (gone if the DB is discarded).
- [ ] `nare_tmp` root commit authored `smadinya <smadinya@localhost>`; `.gitignore` has a `session.json` line.
- [ ] Stray `~/package-lock.json` (87 bytes, from 2025) makes Next infer the wrong workspace root.
- [ ] `nare` on PATH is an editable install of the nare checkout, so switching branches changes what `nare tui` runs. `feat/tui` is local-only and not pushed.

## Suggested spec breakdown

| Spec | Issues | Size | Why first / last |
|---|---|---|---|
| A. Bash tool: honest results, contained side effects | A1–A5 | M | Wrong exit codes and leaked daemons are correctness and safety bugs |
| D. Session file handling | D1–D3 | S | Data loss (no save without `--session`), repo pollution, corrupted resumes |
| B. Default system prompt + tool descriptions | B1–B6 | S (text) + bench case | Biggest behavioural lever: would have removed the detour, overclaims, commit and identity issues |
| C. TUI budget/cost/status visibility | C1–C5 | M | The turn-cap near miss and the false $0 |
| E. Context efficiency | E1–E4 | M | Up to about 80% cost reduction (caching) plus a 34% context cut (thinking) |
| F. Proxy route warning | F1 | S | Docs plus one warning; the real fix is proxy-side |

The DES-*, SEC-*, QUA-*, RUN-* and HAR-* IDs in "Sources" refer to the five
reviewer reports this document merges; every finding they relied on is
restated here with its evidence.
