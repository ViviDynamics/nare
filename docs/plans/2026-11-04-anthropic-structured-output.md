# Anthropic rail: constrain schema answers at decode time with output_config.format

Issue #144

## Scope

In:

- `--structured-output json_schema` works on the anthropic rail. On the turns
  the openai rail constrains (the schema correction turn, and every turn of a
  run with no tools) the request carries `output_config.format: {"type":
  "json_schema", "schema": ...}` and no tools, unstreamed and streamed, beside
  any `output_config.effort`.
- The schema sent is the caller's schema passed through the SDK's own
  `anthropic.transform_schema`, because Anthropic answers a keyword it cannot
  enforce with a 400 rather than ignoring it. The transform moves unsupported
  constraints into descriptions, closes every object (`additionalProperties:
  false`) and maps `oneOf` to `anyOf`. nare's validator still checks every
  answer against the original schema.
- Refused at startup (exit 2), naming the place: a schema the transform cannot
  convert (a subschema with no `type`, `anyOf`, `oneOf`, `allOf` or `$ref`), a
  map (schema-valued or `true` `additionalProperties` on an object with no
  declared properties would decode as an empty object), `definitions` instead
  of `$defs` (the transform drops it and leaves a dangling `$ref`), `allOf`
  with a `$ref`, an `enum` holding objects or arrays, and a schema past
  Anthropic's documented limits (24 optional properties, 16 union-typed
  properties).
- `nare contract` lists both providers, and names the keywords the anthropic
  rail does not enforce at decode time.
- docs/contract.md documents the keyword limits per rail.

Out:

- Constraining tool-using turns on Anthropic, although its grammar does not
  apply to tool calls: the issue asks for the turns the openai rail constrains,
  and one rule across rails is what a caller reads.
- Strict tool use (`strict: true` on tools).

## Assumptions

- Verified against Anthropic's structured outputs page (read 2026-11-04) and the
  installed SDK 1.6.0 (`OutputConfigParam.format` is `JSONOutputFormatParam`,
  `{"type": "json_schema", "schema": {...}}`; `anthropic.transform_schema` is
  public). No beta header is needed.
- Additive under the version policy: a provider gains support behind an
  existing flag, plus a discovery key. The contract stays at version 2.

## Tasks

- [x] 1. The anthropic rail declares structured output and a constrained turn
  sends `output_config.format` with the transformed schema and no tools,
  unstreamed and streamed, merged with effort: request-shape tests, no key.
- [x] 2. The startup check refuses each unsupported shape with its place, and
  passes generated schemas the transform handles.
- [x] 3. The loop and CLI run the check: a tool-free `--schema
  --structured-output` run on the anthropic rail sends the format through the
  real CLI; a refused schema exits 2 with nothing on stdout.
- [x] 4. `nare contract` and docs.
