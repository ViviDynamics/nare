# MCP: remote servers with OAuth

Issue #231

## Scope
In: remote Streamable HTTP MCP servers that require OAuth, with dynamic client registration or a pre-registered client; an explicit `nare mcp login ALIAS` that runs the authorization once; token refresh during `nare run`; token storage under NARE_HOME with 0600 files; fixed or redacted errors; docs/contract.md; an offline fake authorization server and MCP server in the suite.
Out: a browser launched by nare (the login prints the URL; a headless host forwards the loopback port); the device authorization grant (RFC 8628); client ID metadata documents (CIMD) as the registration route; token revocation and `nare mcp logout` (delete the file); identity-assertion grants (SEP-990); a shared or encrypted credential store (the file is protected by its mode and the NARE_HOME folder); changes to the redaction rule set or the contract version.

## Assumptions
- The MCP SDK already a dependency (`mcp>=2,<3`) ships a client OAuth provider that implements the current MCP authorization specification (protected resource metadata, authorization server metadata, PKCE S256, RFC 8707 resource, dynamic registration, refresh). It is used, not reimplemented. Its httpx client is `httpx2`, which nare already uses. No new third-party dependency.
- Login is the only interactive step, run by a person. A run is headless: it refreshes or fails, and never waits for a browser.
- The browser returns to a loopback listener on 127.0.0.1 (RFC 8252). A pre-registered client must accept that redirect; `callback_port` pins the port when it has to be exact.
- Credentials go only over https, or to a loopback host for testing. The client secret comes from an environment variable, never from the file or argv. Credentials go to the MCP server's origin unless `authorization_server` pins the authorization server; a server's metadata cannot choose another destination.
- Tokens are kept out of events by construction rather than by a new redaction rule. A rule change would bump the contract, and nothing in the event text needs one.
- The contract stays at version 3: the change is additive (a config object and a new subcommand), and no existing field, event, status or exit code changes meaning.
- The SDK's expiry does not allow for clock skew, so a stored token is treated as expired 30 seconds early, so a headless run refreshes before the server's 401 would ask for a browser.

## Tasks
- [x] 1. Configuration: failing tests reject an `auth` object on stdio, with an `Authorization` header, over plain http off loopback, with a bad alias, port or env name; `src/nare/mcp.py` parses the object into `OAuthSettings`.
- [x] 2. Token file: failing tests show the file is 0600 in a 0700 folder, bound to its server URL, refused when readable by others, and written atomically; `src/nare/mcp_auth.py` `TokenFile`.
- [x] 3. Flow: failing tests run login against the fake server (discovery, registration, PKCE, loopback redirect, token exchange) and then a `nare run` that refreshes an expired token and calls a tool; `build_provider` wraps the SDK provider.
- [x] 4. Headless run: failing test shows a run with no stored authorization stops before any model call, naming the login command; a refresh the server refuses names the login and quotes no reply.
- [x] 5. Secret handling: failing tests show the client secret is absent from the live login process command line, and tokens are absent from events, stderr, logs and every file outside the token file; a token-endpoint reply is replaced with fixed text; a forged state is rejected and stores nothing.
- [x] 6. CLI: `nare mcp login` prints the URL and result on stderr only; argument errors exit 2.
- [x] 7. Docs: docs/contract.md section on configuration, login, token storage and the contract decision.
- [x] 8. Review round 1 (adversarial, secret handling): every request the SDK's authorization flow sends now passes one origin and scheme rule (`_check_request`), so metadata cannot downgrade or redirect credentials. The authorization server must be the MCP server's origin unless `authorization_server` pins another. Refresh restores the stored authorization server metadata. Non-OAuth errors report their type only. Errors carry no chained context to a reply. The SDK's streamable HTTP logger is scrubbed too. A fresh login repairs a loose token file, and an oversized loopback request gets a quiet 400. Each fix has a flow-level regression test; removing the fix fails it (checked by mutation, except the startup-path context mutation, which that scenario does not reach).
- [x] 9. Review round 2 (fresh adversarial reviewer): an environment proxy could see the token exchange on a loopback http server; authorized connections now ignore the proxy variables. The POST and GET origin rules, the startup error mapping, and the state check on the loopback listener each have a flow-level test that fails without them. The `mcp.client.session` logger is scrubbed.
- [ ] 10. Preflight, quality guard, PR, rebase, CI, merge.

## Review focus
Secret handling end to end: argv, the session file, events, logs (the SDK logs with exception text, which the scrub filter removes), and file modes. The https rule for credentials and discovered endpoints. Refresh on a stored token at its boundary. The loopback listener: state is checked by the SDK, the listener accepts one redirect, and it binds 127.0.0.1 only.
