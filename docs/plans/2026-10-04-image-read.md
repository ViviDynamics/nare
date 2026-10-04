# Images through the read-only tool

Issue #48

## Scope
In: PNG/JPEG read results under the existing read/root policy; bounded validated images, explicit capability declaration; both transport serializers, persistence/resume and context accounting; deterministic real CLI HTTP proof.
Out: screenshots, image prompt arguments, writes, model selection or capability guessing from model names.

## Assumptions
- --image-input declares that the selected model accepts images. Default is disabled; both built-in rails support the wire format, unknown/custom transports must explicitly implement image input.
- Unsupported reads name the transport/model in an ordinary is_error tool result. Historical images are withheld on a resume without capability enabled; stored originals remain available.
- Read returns its existing text result plus an optional image field. Text tool content and event/result fields keep their meanings; contract1 gains this optional field and flag.
- PNG/JPEG are validated with Pillow, limited to4MiB compressed bytes,20million pixels and8000pixels on either edge. No resizing/truncation changes the caller's image.
- Image data is opaque base64 in saved sessions, preserved byte-for-byte across redaction; text fields still use the existing rules. JSONL contains image metadata, not binary payload.
- Context estimate excludes base64 character length and uses a documented image patch heuristic (4tokens per32x32patch), calibrated to actual final provider usage after a turn. Cumulative budgets still use actual provider tokens.

## Tasks
- [x] 1. Failing read tests: PNG/JPEG blocks, unsupported named error, root/symlink, denied tool/approval, malformed/oversized images; implement bounded image reader.
- [x] 2. Failing serializer/persistence/context tests: real native payloads on both rails; binary preserved, resumable, historical images withheld if disabled, compaction elides older images.
- [x] 3. Actual CLI two-turn HTTP fixtures validate image pixels and answer; disabled models complete with named error. Document contract and discovery.
- [ ] 4. Preflight, independent review, CI, merge, usable release.

## Protocol references
- https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls
- https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create
- https://pillow.readthedocs.io/en/stable/reference/Image.html
