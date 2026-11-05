# Feature gap: nare against OpenCode v2 and Claude Code

Issue #113. Status: Proposed (2026-11-05). The decision is Jason's; nothing
in this document is filed, closed or merged on its strength.

Research date: 2026-11-05. nare baseline: origin/main 91e75a8, `nare contract`
version 3, build 2026.11.44. Source tags like [C3] and [O4] point at the
source list at the end.

## Read this first: the source caveats

- **OpenCode v2 has thin official docs.** The v2 pages ([O1], [O2]) are a
  landing page and a v1-to-v2 migration note. They document no headless run,
  permission, subagent, MCP or provider behaviour. Every OpenCode feature
  claim below comes from the current `opencode.ai/docs` pages, which are dated
  2026-11-05 and do not name a version, so they describe v1 and v2 together.
  Treat the OpenCode column as "what the current docs say", not as a verified
  v2 contract.
- **OpenCode v2 release status is unclear.** Two third-party snippets call it
  a beta ([T1], [T2]). The official pages state no status. Verify before
  relying on it.
- **Not found in the pages read:** OpenCode prompt caching, OpenCode per-run
  cost in output, OpenCode and Claude Code redaction rules, and any public
  benchmark harness for either product. These cells say `unverified`.

## Summary of the recommendation

- **Target:** Bedrock; context compaction (window discovery, then summary);
  a cost-aware benchmark (#151-159); bash command-pattern rules; remote MCP
  OAuth; session fork. Measure before changing behaviour.
- **Cut:** in-loop subagents, hooks and plugins, skills and plugin packaging,
  a network server or SDK, and TUI feature parity. Each has a reason below and
  a condition for revisiting it.
- **Undecided (Jason):** an OS sandbox for `bash`; a bounded sub-run as a
  tool; Vertex and Foundry providers; MCP tool deferral; and whether comparisons
  with other products belong in an ADR (see the last undecided item).

## 1. Feature matrix

`has` = documented and present. `partial` = present with a narrower scope.
`no` = absent. `unverified` = not found in the pages read.

| Area | nare | OpenCode | Claude Code |
| --- | --- | --- | --- |
| Headless run, typed event stream | has: JSONL events (`progress`, `tool_use`, `thinking`, `cost`, `error`, `output`), one `result` line [README] | partial: `run --format json` emits raw JSON events; no result line documented [O3] | has: `-p` with `--output-format json` or `stream-json`; `system/init`, `api_retry`, `result` messages [C2] |
| Structured output | has: `--schema` validated against the answer, schema-constrained decode on both rails [contract] | no: `run` has no schema flag [O3] | partial: `--json-schema` returns `structured_output`; no correction turn, and no field on a failed run [C2][C6] |
| Schema correction loop | has: `--max-corrections`, free corrections when errors shrink, kept valid output, `check` callable [contract] | no | no: not documented |
| Stop reasons | has: 18 stop reasons in 5 kinds, versioned, `--contract N` refusal [nare contract] | unverified | partial: exit 0 or non-zero; no stop taxonomy on the headless page [C2] |
| Budgets | has: tokens (`--budget-tokens`), USD (`--budget-usd`), tool calls, cut-off replies, deadline, turns [contract] | partial: `steps` per agent [O5]; no run-level budget flag [O3] | partial: `--max-turns`, `--max-budget-usd`, estimated spend; subagent spend counts [C6] |
| Resume, continue, fork | partial: `--resume` a redacted session file; no fork [README] | has: `--continue`, `--session`, `--fork` [O3] | has: `--resume`, `--continue`, `--fork-session` [C6] |
| Cost reporting | has: `usage.cost` from provider headers or price env, `estimated` flag, `null` when unknown [README] | partial: `opencode stats` reports cost as a separate command [O3] | has: `total_cost_usd` in JSON, `/usage`, `modelPricing` managed setting [C2][C3] |
| Read evidence and provenance | has: SHA-256 and exact line ranges per read, in the event stream [contract] | no | unverified |
| Required reads before answering | has: `--require-reads` [contract] | unverified | unverified |
| Built-in tools | has: read, list, search, write, edit, bash, ask [contract] | has: built-in tools [O4] | has: Bash, Read, Edit, Write, Grep, Glob, WebFetch, WebSearch [C4][C6] |
| Caller-registered tools | has: `nare.Tool`, `--command-tools` [ADR 0009] | has: plugin tools, which override a built-in of the same name [O8] | partial: via MCP and hooks; in-process SDK tools not checked here |
| MCP | partial: stdio and remote URL servers, allowlist, `server__tool` names; no OAuth [contract] | has: local and remote; per-tool disable, globs [O6] | has: MCP; tool definitions deferred by default [C3] |
| MCP OAuth for remote servers | no | has: OAuth with dynamic client registration [O6] | unverified |
| Permissions model | partial: `--yes`, `--tools`, `--root`, approval seam, recorded in session [README][ADR 0004] | has: allow, ask, deny per tool; `--auto` [O4] | has: six modes, allow and deny rules, `--permission-prompts none` [C4][C6] |
| Bash command patterns | no: `--yes` approves all of bash; PR #206 asks before destructive git | has: per-command patterns such as `git *` [O4] | has: `Bash(git diff *)` rules [C2] |
| Sandbox | no by design: run in a container [README] | no documented sandbox [O4] | partial: OS sandbox, off by default, Seatbelt or bubblewrap, network proxy [C8] |
| Hooks | no | has: plugin events, `tool.execute.before` can rewrite args or throw [O8] | has: 33 events; `PreToolUse` blocks or rewrites; `Stop` can continue [C7] |
| Subagents | no | has: primary and subagent modes, `@` invocation, child sessions [O5] | has: markdown definitions, tool scope, `maxTurns`, background, depth 3, cap of 20, resume [C5] |
| Skills | no runtime skills (`.agents/skills` is dev tooling) | has: Agent Skills, `.opencode/skills` [O2] | has: skills, preloadable into subagents [C5] |
| Plugins | no | has: JS and TS plugins; v1 plugins do not run in v2 [O2][O8] | has: plugin directories and marketplaces [C2] |
| Providers | partial: Anthropic and OpenAI-compatible rails (LiteLLM, vLLM, llama.cpp, Ollama); no Bedrock (#74) [README] | has: 75+ through the AI SDK, Bedrock, custom OpenAI-compatible endpoints [O7] | has: Anthropic API, Bedrock, Google Cloud, Microsoft Foundry, LLM gateways [C1][C3] |
| Prompt caching | has: Anthropic breakpoints automatic; OpenAI opt-in; cache tokens in usage [README] | unverified | has: automatic; cache statistics in `/usage` [C3] |
| Compaction | partial: elides old tool output at 80% and 60%; stops with `context` otherwise; no summary (#42) [README] | has: `compaction` config, `session.compacted` event, custom compaction hook [O2][O8] | has: auto-compact, `/compact` with instructions, `PreCompact` and `PostCompact` hooks [C3][C7] |
| Context window discovery | partial: `/v1/model/info` on base-URL routes, else 32000 (#44) [README] | has: `limit` config [O7] | unverified |
| Reasoning effort | has: `--effort` on both rails, thinking budget [README] | partial: `--variant`, provider-specific [O3] | has: `--effort`, `/effort` [C6] |
| Terminal UI | partial: `nare tui`, read-only `--attach`, resume [README] | has: full TUI, the primary surface [O3] | has: full TUI [C1] |
| Embedding over a network | no: subprocess and library only, by design [ADR 0003] | has: `serve`, `attach`, HTTP API with basic auth [O3] | has: Agent SDK in Python and TypeScript [C2] |
| Images as input | has: `--image-input` with caps [README] | partial: `--file` attachments [O3] | has: screenshots [C1] |
| Redaction | has: named ruleset, `nare redact`, provider keys withheld from `bash` [README] | unverified | unverified |
| Benchmark harness | has: `benchmarks/`, with an open issue set (#151-159) [repo] | no public harness found | unverified |

## 2. nare's distinctive strengths

Where the pages read show no equivalent, a caller cannot get these from the
other two without writing the layer itself.

1. **The schema is a loop, not a filter.** One validated answer, one correction
   budget that is free when errors shrink, kept earlier valid output, a `check`
   callable, and a reads-required gate. Claude Code returns no structured
   output on a failed run, and no correction turn is documented [C2].
2. **A closed, versioned stop taxonomy.** Eighteen stop reasons in five kinds
   (`finished`, `limit`, `answer`, `stopped`, `failure`), exit codes that match,
   and `nare contract` as a self-description a caller pins with `--contract N`.
3. **One unit for budgets.** `usage.total` includes cache, `--budget-usd` is
   labelled `estimated`, and tool calls, cut-off replies and deadlines are all
   counted in the result. A SIGTERM saves the session and writes a result.
4. **Evidence a caller can check.** Each read carries a SHA-256 and exact line
   range, so an answer can be tested against what the model was shown.
5. **Keyless CI.** `--provider fake --replay` runs the real tools, session and
   exit codes with no key and no network.
6. **Caller tools on the same approval seam.** In-process or command-line
   tools pass the same argument check, allowlist and approval as built-ins
   (ADR 0009), and the contract does not name any caller (ADR 0008).

## 3. Gaps

### Target: worth closing for nare's purpose

| Gap | Competitors | Existing issue | Why it matters |
| --- | --- | --- | --- |
| Bedrock provider | both | #74, PR #76 open | Parity on the enterprise rail both others cover |
| Context window discovery | both | #44 | Compaction cannot be sized without it |
| LLM summary compaction | both | #42 | Long tasks stop with `context` today |
| Configurable compaction point | both | #43 | Needs a benchmark to pick a value |
| Cost per solved task, paired A/B, judge quality | unverified | #151-159, #114 | Every other decision is judged by this |
| Bash command-pattern rules | both | new (P1) | `--yes` is all or nothing for `bash` |
| Remote MCP OAuth | both | new (P2) | Remote servers that need login cannot be used |
| Session fork | both | new (P3) | A caller branches a run without copying files by hand |
| Local home for sessions and settings | none | #72 | Operational convenience only |

### Cut: explicitly out of scope

- **In-loop subagents (C1).** A caller composing several nare runs gets the
  same parallelism, and each run keeps its own budget and stop reason. An
  in-loop subagent would need a new budget and accounting surface, and policy
  across runs belongs to the caller (ADR 0008). *Revisit if* a measured task
  needs an isolated context inside one run (see U2).
- **Hooks and plugin lifecycle (C2).** The approval seam (ADR 0004) and caller
  tools (ADR 0009) already give pre-call control inside the caller's process.
  Hooks would make nare a platform with a second extension contract.
- **Skills and plugin packaging (C3).** A caller supplies its persona with
  `--append-system-file`. nare's own skills stay development tooling.
- **Network server or SDK (C4).** ADR 0003 chose subprocess integration. A
  server adds auth, a lifecycle and a second transport that the contract would
  have to cover.
- **TUI feature parity (C5).** The TUI exists so a person can watch a run. The
  caller is headless. Bug fixes continue (#106); feature work does not.

### Undecided: for Jason

- **U1. OS sandbox for `bash`.** Claude Code ships one, off by default. nare's
  README leaves confinement to the container. This is the largest security gap
  in the table. Decide whether to ship optional confinement or keep the
  container as the contract.
- **U2. A bounded sub-run as a tool.** A fresh context that returns a summary
  and counts against the parent budget. It is the only subagent form with a
  clear case, and it crosses the ADR 0008 policy line.
- **U3. Vertex and Foundry providers.** Claude Code has both; only Bedrock is
  planned.
- **U4. MCP tool deferral.** Claude Code defers MCP tool definitions behind a
  search step by default [C3]. Worth it only if callers hit context limits with
  many MCP tools.
- **U5. OpenCode v2 status.** Confirm the release state and whether v2 changes
  any `opencode` row above before the matrix is used for a decision.
- **U6. Where comparisons live.** ADR 0008 bars other products' names from
  living documentation, and the PR template repeats that rule. This document
  sits in `docs/plans/`, which the caller-agnostic test exempts as history. If
  comparisons should be recorded as ADRs, the rule itself needs an amendment.

## 4. Recommended order for the target gaps

1. **Measure first:** #155 (cost on every result line) and #153 (paired A/B).
   Every later change is then judged in dollars per solved task.
2. **Bedrock:** #74, with PR #76 already open. Low risk, and it closes a
   parity gap.
3. **Compaction, in dependency order:** #44 (window discovery, since sizing
   needs the real window), then #42 (summary), then #43 (compaction point, tuned
   by the benchmark). Run #152 and #151 before #42 so judging is trustworthy.
4. **Benchmark hygiene, interleaved with step 3:** #152, #151, #154, #157,
   #158, #159.
5. **Re-run the comparison:** #114, after steps 1 to 4.
6. **Permissions and MCP:** P1 (bash command patterns), then P2 (remote MCP
   OAuth).
7. **Small items:** P3 (session fork), then #72 (local home).

## 5. Proposed issues (not filed)

- **P1. Bash command-pattern allow rules.** Policy entries such as
  `bash: git diff *`. A deny wins over an allow. Matches are recorded in the
  session. Acceptance: a run with `--yes` still denies a pattern the policy
  denies.
- **P2. Remote MCP servers with OAuth.** Pre-registered client, or dynamic
  registration where the server supports it. Tokens stay out of argv and out
  of the session file.
- **P3. Fork a saved session.** `--resume PATH --fork` writes a new session
  file and leaves the original alone, with the same redaction and
  schema-change rules as a resume.

## Sources

Access date for every URL: 2026-11-05.

**Claude Code (primary, code.claude.com)**

- [C1] https://code.claude.com/docs/en/overview
- [C2] https://code.claude.com/docs/en/headless
- [C3] https://code.claude.com/docs/en/costs
- [C4] https://code.claude.com/docs/en/permissions
- [C5] https://code.claude.com/docs/en/sub-agents
- [C6] https://code.claude.com/docs/en/cli-reference
- [C7] https://code.claude.com/docs/en/hooks
- [C8] https://code.claude.com/docs/en/sandboxing

**OpenCode (primary, opencode.ai)**

- [O1] https://opencode.ai/v2/docs/ (landing page only)
- [O2] https://opencode.ai/v2/docs/migrate-v1
- [O3] https://opencode.ai/docs/cli/
- [O4] https://opencode.ai/docs/permissions/
- [O5] https://opencode.ai/docs/agents/
- [O6] https://opencode.ai/docs/mcp-servers/
- [O7] https://opencode.ai/docs/providers/
- [O8] https://opencode.ai/docs/plugins/

**Third party (search snippets only, not read in full)**

- [T1] https://capitalandcompute.net/md/opencode-2-0-what-changed.md
- [T2] https://byteiota.com/opencode-2-beta-unified-ai-coding-agent/

**nare (repository and CLI, origin/main 91e75a8)**

- README.md; docs/contract.md; docs/architecture.md; CONTRIBUTING.md
- docs/adr/0003, 0004 (as cited by 0009), 0008, 0009
- `nare contract` output, version 3, build 2026.11.44
- Merged PRs #98-#212 (`gh pr list --state merged`)
- Issues #113, #42-44, #72, #74, #106, #114, #150-159 (open state checked
  2026-11-05)
- Claude Code compaction and prompt-cache statements come from the costs page
  [C3]. The `PreCompact` and `PostCompact` hook names come from [C7].
