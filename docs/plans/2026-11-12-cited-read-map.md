# Path map for --require-cited-reads

Issue #297

## Scope
In: a map from a cited value to the root-relative path (or paths) that satisfy
it, as `cited_read_map=` on `nare.run` and `--cited-read-map FILE` on `nare run`;
the correction naming the path to read; the contract and docs.
Out: checking the cited revision, digest or line range (a caller check, see
#296); changing how an unmapped cited value is compared.

## Assumptions
- A key is a cited string, matched exactly as the answer holds it. A value is a
  path or a non-empty list of paths, each relative to `--root` (or absolute
  inside it), normalized as evidence records paths. Any one listed path that
  was read satisfies the citation.
- A cited value with no entry is compared as it is today.
- A malformed map (not an object, an empty key, a value that is not a path or
  a non-empty list of paths) or a mapped path outside the root is refused
  before the run: ValueError in Python, exit 2 on the command line.
- A map needs cited pointers: `cited_read_map` without `require_cited_reads`
  is refused, as is `--cited-read-map` without `--require-cited-reads`.
- The correction names the mapped path: "read capture/x, which the answer cites
  as src/x.py", or "read one of a, b, which the answer cites as src/x.py".
- Additive: the contract's `cited_reads` entry gains a `map` key; version 3.

## Tasks
- [x] 1. `reads.build` takes and validates a map; `cited_unmet` uses it: unit
      tests for satisfied, unsatisfied (one and several targets), unmapped, and
      each refusal.
- [x] 2. `run(cited_read_map=...)`: a held answer is corrected with the mapped
      path and accepted once it is read; the map without pointers is refused.
- [x] 3. `--cited-read-map FILE`: end to end, and exit 2 for a malformed file,
      a path outside the root, and the flag without `--require-cited-reads`.
- [ ] 4. Contract `cited_reads.map` and docs.
