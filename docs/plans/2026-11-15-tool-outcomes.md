# Typed caller-tool outcomes

Issue #308

Preserve tool-result wire blocks and dispatch behavior while making safe outcome
classification available to event consumers. Nare owns schema rejection and
caller exception classification; applications retain their own recovery policy.

- [x] RED: schema rejection, coded/uncoded caller refusal, unexpected exception and
  success produce typed event metadata; provider blocks contain no metadata.
- [x] Add validated optional ToolError codes, an internal classified caller-call
  path, and dispatch outcome callbacks consumed by result events.
- [x] Verify the existing caller API still returns the same text/error pairs,
  errors remain bounded/redacted, and all full build checks pass.

No retry loop, error allowance, installation behavior, or application-specific
error codes belong in this change. Outcome metadata is a classification, not
permission to repeat an operation.
