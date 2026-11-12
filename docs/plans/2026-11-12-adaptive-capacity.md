# Adaptive in-flight concurrency under provider capacity

Issue #289

## Scope
In: an AIMD limiter on in-flight model requests per provider route, driven by
the capacity refusals #288 already classifies. Two stores behind one policy: an
in-process store, and a file-locked store that many nare processes share. A
declared-limit seam on transports (no provider-specific discovery). Events for
each adjustment and for time spent waiting for a slot. Modes off, fixed (#288
queueing only) and adaptive. Additive flags and a contract advertisement.
Out: latency growth as a signal (softer, optional in the issue; deferred).
Discovery of a proxy's `max_parallel_requests` (no provider-agnostic route;
the openai transport declares none and the gap is reported). A Scrutare-side
companion issue. Windows support for the shared store (fcntl only). Any change
to #288's defaults (1800 s, 50 refusals). Any contract version bump.

## Assumptions
- Route = provider, base URL and model. The key is a hash; the API key is not in it.
- Cut: one multiplicative decrease (limit * 0.5, floor at the configured floor)
  per cooldown window, so one burst of refusals cuts once. Refusals always
  refresh the quiet clock, even inside the cooldown.
- Increase: one additive step (+1, to the ceiling) per quiet period with no
  refusal and no earlier increase.
- Slots: a shared slot is a file locked with `flock`. The kernel drops the lock
  when the holder exits, so a crashed holder frees its slot without any stale
  record. Limit state is a JSON file, read and written under a separate `flock`
  and replaced atomically. A corrupt state file is reinitialized.
- A reduced limit does not preempt in-flight requests; they drain.
- The limiter gates each model request attempt, not the run. A queued refusal
  releases its slot before it waits, so waiting does not hold capacity.
- A refusal is a capacity signal whether or not the queue admits it, so the
  limit still drops when the queue is exhausted.
- In-process limits apply across the runs that share one `CapacityQueue` (the
  Python API). The CLI builds one process, so a single CLI run is one request
  at a time; its shared store is what spans processes.
- The shared directory must be on a local filesystem. Network filesystems do
  not guarantee `flock`; this is documented, not detected.
- Flags: `--capacity-adaptive` (needs `--capacity-queue`), `--capacity-ceiling`
  (default 8), `--capacity-floor` (default 1), `--capacity-quiet-seconds`
  (default 60), `--capacity-shared-dir`. Each has an env var. Defaults for these
  new flags are proposed here, not carried over from #288.

## Tasks
- [ ] 1. AIMD policy: a refusal cuts once per cooldown and clamps to the floor;
  a quiet period raises by one to the ceiling; refusals reset the quiet clock;
  a backwards clock counts as elapsed. Test: `tests/test_capacity_limiter.py`
  `test_aimd_*`.
- [ ] 2. In-process store: acquire blocks at the limit, release wakes a waiter,
  a quiet period raises the limit. Test: `test_local_*`.
- [ ] 3. File store: slots are exclusive across two handles, a SIGKILLed holder
  frees its slot, corrupt state reinitializes, state is shared across handles.
  Test: `test_shared_*`.
- [ ] 4. Declared-limit seam: a transport's `declared_concurrency()` sets the
  start limit; a ceiling above it emits a warning event. Test:
  `test_declared_*`.
- [ ] 5. Loop: a model request holds a slot only while it is in flight; a
  qualifying refusal cuts the limit with reason `refusal` and emits an
  adjustment event; a refusal after a stream item does not cut; a transport
  without markers does not cut; slot wait is counted on the session. Test:
  `tests/test_capacity_adaptive.py`.
- [ ] 6. CLI: flags, env, dependency errors, `CapacityQueue(limiter=...)`
  wiring, and the contract advertisement and docs table rows. Test:
  `tests/test_capacity_adaptive.py` CLI section, `tests/test_contract.py`.
- [ ] 7. Docs: `docs/contract.md` capacity section, README mention if #288 has one.
