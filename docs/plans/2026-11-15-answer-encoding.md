# Explicit answer encoding

- Preserve existing json_schema semantics and third-party call signatures.
- Add declared OpenAI answer modes json_object and tool_call; unsupported modes
  fail before a request. Only constrained answer turns carry response_mode.
- JSON-object is syntax-only. Tool-call answers use one dedicated forced function,
  decoded to canonical answer text, never dispatched as actions. Mixed, missing,
  duplicate-key or malformed answers cannot bypass local validation.
- Structural corrections can constrain the next answer. Domain diagnostics/check
  corrections restore approved tools using the same persisted correction state,
  correction allowance and events. No additional loop, request or budget.
- Cover all modes with real transport/mock HTTP plain and streaming tests; test
  recovery through an evidence tool, truncated output, provider errors and old
  transports. Existing Anthropic schema mode remains unchanged; other modes refuse.
- Full build, independent review, preflight, guarded PR/CI/merge, official release.

## Verification

- [x] Actual OpenAI transport wire tests for all three modes, both plain and streaming.
- [x] Domain correction restores tools within the existing allowance.
- [x] Malformed/mixed functions and duplicate keys cannot dispatch or bypass validation.
- [x] Unsupported legacy transports refuse before inference; existing protocol typing stays compatible.
- [x] Independent review findings reproduced and fixed: streamed answer repetition and malformed-envelope usage loss.
- [x] Optional null streaming fragments retain their existing meaning.
- [x] Canonical build before the final optional-fragment edge case: 3,871 passed, 7 skipped, 11 deselected; lint and typing clean.
- [x] Final focused tests: 79 passed; strict typing of 185 modules passed.
- [ ] Exact-head preflight, guarded PR checks, merge and official release.
