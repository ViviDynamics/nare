# 3. Conductor integrates by subprocess, not by import

Status: Accepted (2026-09-09)

## Context

Conductor's performer could import nare directly, since both are Python. It
spawns every other backend as a subprocess.

## Decision

The performer spawns `nare` exactly as it spawns `claude` or `opencode`.

## Consequences

nare's own CLI stays on the critical path, so the standalone product surface
cannot rot while the integration keeps working. Crash isolation is preserved: a
backend that dies takes nothing with it. In-process `import nare` remains
available later at no extra cost, because the core is a library regardless.
