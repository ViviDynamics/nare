# Schema: accept generated JSON Schema ($ref, anyOf, const, bounds) without loosening the refusal rule

Issue #123

## Scope
In: local `$ref` resolution (`#`, `#/$defs/...`, `#/definitions/...` and any other local
JSON pointer), including recursive references; `anyOf`, `oneOf`, `allOf`; `const`;
`default` as an annotation; `minLength`, `maxLength`, `pattern`; `minItems`, `maxItems`,
`uniqueItems`; `minimum`, `maximum`, `exclusiveMinimum`, `exclusiveMaximum`. Every new
keyword has its value shape checked at startup, like the existing ones. Error messages
keep naming the JSON path of the failing value. `docs/contract.md` lists the supported
keywords.
Out: `format`, `if`/`then`/`else`, `patternProperties`, `dependentSchemas`, remote
references (all refused, as the issue says). A schema-valued `additionalProperties`
and a list-valued `type` stay refused; neither is in the issue's scope.

## Assumptions
- `$ref` siblings apply alongside the reference (2019-09 and later), which is never
  looser than ignoring them.
- A reference chain that returns to itself without descending into a property or an
  item can never terminate, so it is refused at startup rather than looping.
- Equality for `const`, `enum` and `uniqueItems` is JSON equality: `1` equals `1.0`, and
  `true` is not `1`.
- `pattern` uses Python `re.search` (unanchored, as JSON Schema specifies). A pattern
  Python cannot compile is refused at startup.
- `exclusiveMinimum`/`exclusiveMaximum` are numbers (draft 6 and later); the draft 4
  boolean form is refused as an unsupported shape.
- `--structured-output` already forwards the schema unchanged; a test pins that for a
  `$defs`/`$ref` schema. Provider refusals stay provider failures.
- No new runtime dependency. pydantic appears only in a test, skipped when absent.

## Tasks
- [x] 1. Bounds: string, array and number bounds fail with the value's path; bad shapes are refused.
- [x] 2. `const` and `default`: `const` fails with the path; `default` is never applied; JSON equality for `const`, `enum`.
- [ ] 3. Composition: `anyOf`, `oneOf`, `allOf` report every alternative's errors under the path.
- [ ] 4. References: local `$ref` resolves, recursion validates and terminates, remote and dangling refs and same-position cycles are refused.
- [ ] 5. Generated schemas: a pydantic v2 schema with nested models, optional fields, enums and bounds validates unedited; the CLI accepts it at startup; the structured-output request forwards it unchanged.
- [ ] 6. Docs: `docs/contract.md` keyword list, `--schema` help, module docstring.
