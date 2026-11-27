# nare auth login|status|logout: store a key once

Issue #328 (tracking: #331)

## Scope

In: `nare auth login [--provider P] [--with-api-key]`, `nare auth status
[--provider P]`, `nare auth logout [--provider P]`, Codex's `nare login`,
`nare login status` and `nare logout`; the stored key used by every command
when the environment variable is unset; the one-line offer of `nare auth
login` when a person-facing run has no key.

Out: storing AWS credentials for bedrock, which has its own chain; status
reports whether that chain resolves.

## Assumptions

- The file is `$NARE_HOME/credentials.json`, `{"<provider>": {"api_key": "..."}}`,
  written atomically from a 0600 temporary file in a 0700 folder.
- The stored key reaches the provider client as `api_key`, never the
  environment, so bash and MCP servers cannot see it, `--bash-credentials` or
  not.
- `nare run`'s message without any key is unchanged; only the person-facing
  forms print the offer, before anything starts (exit 2).
- `auth status` exits 0 when the chosen provider (`--provider`, else
  `NARE_PROVIDER`, else anthropic) has a credential, 1 when not, and 2 for an
  unreadable credentials file.

## Tasks

- [x] 1. Login from stdin and from hidden input, 0600 file, nothing on stdout: `tests/test_auth.py`.
- [x] 2. Status sources and exit codes; logout.
- [x] 3. Precedence: env, then stored, in the transport; `nare run` without a key unchanged.
- [x] 4. The key never in argv, events, session files or bash.
