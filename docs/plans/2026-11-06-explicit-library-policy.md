# Library: only an explicit policy enables bash and writes, and ask joins the read-only default

Issue #229

## Scope

In: `nare.run` with no `policy` offers `read`, `list`, `search` and `ask` only,
whether or not an `approve` callable is passed. An approver gates the calls a
policy allows; it no longer widens the default. Writing, editing and bash need an
explicit policy that names them. README and the "Calling nare from Python"
section of docs/contract.md describe the rule and the migration.

Out: the command line (it already passes its policy explicitly), the contract
version (library-only, as #220 decided), compaction code, and any change to
`step()`, which already requires a policy.

## Assumptions

- `READ_ONLY_TOOLS` gains `ask`. It has no side effects, and the issue names it.
- The approver default `approve_all` stays, so a policy-named tool that needs
  approval is still gated exactly as before.
- Caller tools and MCP servers are still offered when the call registers them,
  whichever default applies (#220's rule, unchanged).
- Contract version stays at 3: the change is to the Python API, which the contract
  does not version. The command line's stream, session file, flags and exit codes
  are unchanged.

## Tasks

- [x] 1. No policy, approver given: only read, list, search and ask are offered
  (`tests/test_policy_wiring.py::test_an_approver_without_a_policy_still_offers_only_read_only_tools`).
- [x] 2. No policy, approver says yes: a bash or write call is refused with no side
  effect (`test_an_approver_without_a_policy_refuses_bash_and_write`).
- [x] 3. Explicit policy unchanged (`test_an_explicit_policy_is_unchanged`).
- [x] 4. README and docs/contract.md state the rule and the migration.
