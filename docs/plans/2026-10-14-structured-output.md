# Request schema-constrained answers on the OpenAI rail

Issue #79

## Scope
In: `nare run --structured-output json_schema` for `--schema` runs on the OpenAI rail. A constrained turn sends `response_format` json_schema with the run's schema and no tools. Constrained turns: the single schema correction turn, and every turn of a run whose policy offers no tools. Startup refusals, a progress event per constrained turn, the `nare contract` advertisement and docs/contract.md.
Out: constraining tool-using turns (measured incompatible on vLLM: the model cannot call a tool), the Anthropic rail (no equivalent request field is used here), grammar or `structural_tag` modes, retrying anything other than a schema failure.

## Assumptions
- The measured vLLM-through-LiteLLM behaviour in #79 holds: `response_format` json_schema is forwarded and enforced, a constrained request with tools suppresses tool calls, and a tool-free constrained request after tool results answers from them.
- The constraint is a transport aid. The answer is still extracted and validated by nare's own validator; a constrained answer that fails is still a `schema_violation` after the one correction.
- A valid unconstrained answer is accepted as today. Constraining every no-tool answer in advance is impossible, because a turn is known to be final only after it is generated, and forcing an extra constrained turn on every run would re-send the whole context.
- `json_schema.name` is `answer` and `strict` is true, so a backend that cannot honour the schema refuses loudly instead of answering unconstrained.
- Whether the next turn is constrained is session state (`constrain_answer`), so a run killed during the correction resumes into the same constrained turn. It is an additive session field and the contract version is unchanged; so is the additive `structured_output` entry in `nare contract`.
- Off by default: without the flag no request, event or session field changes value, and transports are called with exactly `(messages, tools)`.
- A transport that does not declare `supports_structured_output` is refused before any turn by the library, and by the CLI at startup for `--provider anthropic`.

## Tasks
- [x] 1. OpenAI payload: `turn`/`stream_turn` take `response_schema`; it adds `response_format` and omits tools. Failing tests: constrained payload on both paths; unconstrained payload unchanged.
- [ ] 2. Loop correction turn: after a failed no-tool answer, the correction is sent tool-free with the schema and a progress event; a valid first answer sends no constraint; a second failure is still `schema_violation`; off is unchanged. Failing tests with a scripted structured transport, including tool turns before the answer and streaming.
- [ ] 3. Tool-free runs constrain every turn. Failing test.
- [ ] 4. Library refusal for a transport without support, before any turn. Failing test.
- [ ] 5. CLI flag and startup refusals (no `--schema`, `--provider anthropic`), wiring to the loop; real CLI HTTP proof of a tool turn, a failed answer and a constrained correction. Failing tests.
- [ ] 6. `nare contract` advertises `structured_output`; docs/contract.md documents it. Failing tests.
- [ ] 7. Preflight, review, CI, merge, release.
