# Read evidence: record the digest and exact range of every read in the event stream

Issue #125

## Scope

In:

- Every successful `read`, `list` and `search` call yields one evidence record.
  It goes out as a `progress` event whose `detail` is
  `{"tool": <name>, "evidence": <record>}`, and the same record is appended to
  a new session field, `evidence`.
- `read` records the path relative to the root, the SHA-256 and size of the
  whole file, the line and byte range actually returned (after the output
  cap), and whether the output was truncated. The file is read once: the
  digest is of the same buffer that is decoded.
- `list` records the directory and the entries on the page (name, kind, size,
  binary), and whether more pages follow. It opens no file beyond the binary
  sniff, so it records no digest.
- `search` records the directory, glob and pattern, and per file on the page
  the path, and with a pattern, the file's SHA-256 and size and the line
  numbers returned. A glob-only search opens no file, so its files carry only
  their path.
- `nare contract` advertises the record under `evidence`. Docs:
  docs/contract.md, docs/architecture.md.

Out:

- Content-addressed storage and digests for `bash` output (out of scope in
  the issue).
- Evidence for `write`, `edit` and MCP tools: they are not reads of the tree.
- Reconciling two digests of one file: a file that changes between reads
  yields two records with two digests, as the issue asks.

## Assumptions

- Additive under the contract version policy: a new event detail key on an
  existing event type, a new session field with a default, and a new
  discovery key. The contract stays at version 2.
- The record is not put on the `tool_result` block, because the anthropic
  transport sends that block as it is. It is delivered by a callback from
  `dispatch`, as `left_running` is, and carries `tool_use_id` to tie it to the
  transcript.
- Records hold no content, so they pass through the usual event redaction
  unchanged; only a path that itself looks like a credential would change.
  The session stores the redacted record from the event, so both agree.
- Line numbers are the ones `read` counts: line `offset + 1` first, from 1,
  inclusive. Byte offsets are into the file, end exclusive. A truncated read
  ends at the last character returned, which may be mid-line. A read that
  returns nothing records `null` ranges.
- Search reads each searched file whole into memory once to hash it; a
  `ponytail:` comment marks that ceiling.
- The TUI does not show evidence events: the call block already names the
  read.

## Tasks

- [x] 1. `read` evidence: path, digest, size, line and byte ranges, with CRLF,
  offset and limit, a truncated read, a read past the end, and an image.
- [x] 2. `list` and `search` evidence: the page's entries, per-file digests
  and line numbers, glob-only paths, and `more`.
- [x] 3. Dispatch delivers the record with its tool and call id, and never
  for an error result.
- [ ] 4. Loop and CLI: the JSONL stream alone lists every (path, digest,
  range); the session stores the same records; a file changed between two
  reads yields two digests; an older session loads with no evidence.
- [ ] 5. Contract discovery, TUI quiet, and docs.
