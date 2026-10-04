# Prompts beyond argv limits

Issue #29

## Scope
In: UTF-8 --prompt-file PATH and positional - stdin routes, exactly one prompt source, production CLI subprocess evidence for 1 MiB prompts, contract documentation.
Out: compression, truncation or changing context-window protection.

## Assumptions
- File/stdin bytes decode as UTF-8 and preserve CRLF and trailing newlines, matching positional text.
- A supplied empty source is valid, matching an empty positional prompt; a run with no source still requires --resume.
- Resume accepts one optional source as follow-up text. Inputs fail with exit 2 before provider construction or session persistence.
- Large callers must configure a context window sufficient for their actual prompt; this change bypasses argv limits, not model context limits.
- Contract version 1 gains additive input routes.

## Tasks
- [x] 1. Failing actual CLI tests for file/stdin 1 MiB prompts, exact saved content parity, resume and invalid/conflicting input; implement source resolution.
- [x] 2. Document invocation, UTF-8/exact text, failures, context guard and resume semantics.
- [ ] 3. Preflight, independent review, CI, merge and installed release proof.
