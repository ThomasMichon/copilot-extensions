# Restricted dispatch -- host-driven Tier-P acceptance

This F1 programmatic lane composes the existing production supervisor,
resolving client, Bridge and container provider against a fresh central service
container and a separate fresh restricted worker. It is deliberately outside
`run.ps1 -Scenario`: an in-box scenario would need access to a Docker socket
to create its peer. No socket is mounted, no container is privileged, and
no live cloud login, installed service or shared host configuration is used.

Prepare candidate-source test runtimes with
`python tools/run-plugin-tests.py agent-dispatch agent-bridge agent-containers --prepare-only`.
Build `services/dispatch/Dockerfile` from that same candidate, passing the
environment's approved `PYTHON_INDEX_URL` build argument when required.
Ensure the worker image `node:22-bookworm-slim` is available. Then invoke:

```text
python tools/clean-room/restricted-dispatch/run.py --central-image <candidate-image> --bridge-python <absolute-prepared-bridge-python> --provider-python <absolute-prepared-containers-python> --results-root <existing-private-scratch-root>
```

The host Python must support the repository's test runner (Python 3.11+).
The results root must be outside the checkout; the runner creates one unique,
timestamped run directory. Keep that root private because failure diagnostics
can contain local paths. `cr-report.json` records PASS/FAIL and classified jams,
with each case's `runner.log` and `evidence/` directory. No result is inferred
from an exit code alone: both cases must retain their terminal authority proof
and successful cleanup trace. Skips, missing proof and partial runs fail.
Lane-specific jam categories distinguish `runner-exit`, `runner-io`,
`contained-timeout` and `proof-invalid`, retaining the concrete error alongside
its log reference and targeted hint. POSIX run directories are created
owner-only by `mkdtemp`; Windows callers must supply a private ACL-protected
results root rather than treating POSIX mode bits as ACL enforcement.
Manifest parsing and workload/budget drift fail as `manifest-invalid` before
any case launches; an empty case list cannot produce a passing report.
The manifest supplies execution limits only after validation against the
original two-case 300/360-second contract. Pytest-owned Docker finalizers report
cleanup errors separately, preserving an earlier acceptance failure as well.
The wrapper preassigns each case's exact container/network names and also
verifies their removal after the runner unwinds, including timeout/fatal-exit
paths. Cleanup failures remain separate jams rather than masking the primary
failure. Thirty seconds of the original 360-second case budget are reserved
for owner unwinding and verified cleanup; no item or aggregate budget grows.
On POSIX the canonical containment owner executes in the wrapper's process:
deadline interruption runs its process-group reaping `finally` instead of
killing that owner while its worker lives in another session. Native descendant
regressions exercise that actual interrupt/reaping boundary.

Each case has independent new containers, networks, Bridge state, signed
credentials and native conversation. The worker is UID 1000 with only its
root-owned, read-only task credential. It starts, reads and reports progress
for its own task, refuses five cross-task/fleet operations with HTTP 403,
rejects a tampered signature, observes trusted credential rotation, and then
either completes or reports failure through permitted abandonment. Its final
request must prove terminal credential revocation. An earlier ineligible task
is deferred without creating an agent. Native identity, single prompt,
scoped renewal and activity publication fences remain asserted.

The canonical contained test runner is reused once per case. The existing
300-second item and 360-second contained-case deadlines are unchanged; adding
a second independent case does not relax either. The lane stops on its first
failure and never resamples. It does not claim model behavior, full machine
bootstrap, Windows worker-container parity, central/local projection authority,
or automatic pool placement. It remains opt-in, not required PR CI.
