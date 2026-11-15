# Answer diagnostics

A parseable answer can fail both structural validation and domain diagnostics.
Report both in the existing correction pass so fixing one does not hide the other
until the correction allowance is exhausted.

- Add optional `diagnose` to the library loop, without changing existing callers.
- Diagnose a detached parsed candidate before admission, even if schema errors
  exist. The callback must be pure and handle arbitrary parsed JSON shapes.
- Keep `check` after all validation; neither diagnostics nor schema correction
  can admit an answer. Reject callback exceptions.
- Retain the existing correction budget, free-progress policy, event stream and
  serialized state. No new correction loop or budget.
- Do not diagnose malformed JSON, duplicate keys, unfinished answers, tool turns
  or summaries. Preserve the previous behavior when no diagnostics are supplied.
- Verify plain/streaming transports, combined feedback, admission isolation,
  mutation isolation, exceptions, cancellation and resume with offline tests.
