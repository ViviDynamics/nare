# Keep nare caller-agnostic: state it, and guard it

Issue #13

## Scope

In:

- ADR 0008: nare names no caller and defines its contracts on its own terms;
  what belongs to nare and what belongs to a caller's adapter.
- The event contract restated on nare's terms, in `src/nare/events.py` and
  `docs/contract.md`. The wire shape and the event type names do not change.
- A test that fails when a caller's product name appears in `src/`, in a flag
  name or in a tool name, and also in the living docs (`README.md`, `docs/`
  outside its history), `tests/` and `benchmarks/`.
- Every existing hit in that scope rewritten into nare's vocabulary, so the
  test passes on `main`.
- A review checklist line in the pull request template: a feature request is
  accepted in nare's vocabulary or rewritten into it.

Out:

- Renaming event types or changing the wire shape (out of scope in the issue).
- History and issue provenance (out of scope in the issue): ADRs 0001 to
  0007, `docs/plans/`, `docs/superpowers/`, `docs/evidence/`, git history, and
  provenance lines in issues. ADR 0008 restates what the earlier ADRs decided
  in nare's terms instead of editing them.
- Org policy and provenance outside nare's product surface: `CONTRIBUTING.md`,
  `SECURITY.md`, `.github/` and `bin/`. They name the organization's project
  family or where a convention was adopted from, not a caller nare serves.

## Assumptions

- The names are the organization's other products, from its repository list:
  coordinare, conductor, qare, progettare, scrutare, steward, dettmore,
  reusify, elm. `noul` is left out because the benchmark judge's wire format
  uses it as an answer type.
- A name matches case-insensitively as a whole word, with a plural or
  possessive, and inside an identifier when it is delimited by `_` or a
  digit (`conductors_backend_event` matches).

## Tasks

- [x] 1. The guard: a test over src/, flags, tool names, living docs, tests
  and benchmarks, plus a test that a name planted in a scanned file fails it.
- [x] 2. Rewrite every hit on main into nare's vocabulary.
- [x] 3. Event contract restated on nare's terms.
- [x] 4. ADR 0008, and the review checklist line.
