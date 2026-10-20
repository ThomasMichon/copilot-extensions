# agent-bridge contract evidence

This directory freezes observed bridge contracts so compatibility changes are
reviewed against independent evidence rather than producer-generated snapshots.
The registry is repository-side evidence; no runtime component reads it.

## Updating a contract

1. Change the owning source and its tests.
2. Update the affected immutable fixture or add a new generation fixture.
3. Record exact source commit, plugin version, capture method, support window,
   and removal gate in `registry.json`.
4. Refresh every changed fixture and source SHA-256 in the registry. Text
   fingerprints use UTF-8 with line endings normalized to LF so Windows and
   POSIX checkouts produce the same evidence.
5. Run:

   ```text
   python tools/check-agent-bridge-contracts.py
   python tools/run-plugin-tests.py agent-bridge --guards
   ```

HTTP generation 44 adds the Bridge-owned startup plugin-readiness requirement,
after generation 41's direct remote ACP targets and generation 42's attention
boundaries, followed by generation 43's managed remote ACP ownership.
All historical generations and their capability floors remain
unchanged.
Its frozen vectors include typed readiness results, readiness-bound task/mode
inputs and omission-compatible ordinary input. Every readiness golden is replayed
through the public HTTP router, durable readiness-control acknowledgment and
SQLite inbox. Public input uses `kind: prompt`; only the admitted inbox message
carries the generated `startup:prompt:<control-id>` kind. The frozen control
digest, admission receipt, inbox packet, retry identity and changed-selection
refusal are checked at that boundary, not only through model parsing. The native
observer and inbox tests consume the same generated packet; a pending selection
must produce no SDK send or acknowledgment. Historical generation-40 packets remain unchanged,
and earlier capability floors do not move merely because the health
advertisement advances. Each semantic source has current-generation provenance,
including the joined-session observer and delivery producer.

## Re-attesting the current preference-authority vectors

After committing the authority source, run from the repository root:

```text
python tools/capture_bridge_current_authority.py --source-commit <full-source-commit> --write
python tools/capture_bridge_current_authority.py --source-commit <full-source-commit>
python tools/check-agent-bridge-contracts.py
```

The capture verifies that every authority-closure source still matches the
selected commit. It runs the real terminal settings reader and receipt emitter
with deterministic synthetic settings, namespace, PID and channel inputs; no
real child is executed. It verifies the resulting receipt, every existing
negative vector, terminal consent/refusal, and the real Host's
verify-before-consent and refusal/reaping order. It refuses changes to receipt
semantics, authority closure, binding environment or wire generation rather than
silently broadening this capture.

Only the three current `wrapper-authority-v1`, `wrapper-exec` and
`wrapper-consent` fixtures and their registry hashes/provenance are written.
Historical fixtures remain unchanged. Review these four outputs; do not replace
the captured commit with a guessed future publication commit. A squash may make
the genuine capture commit unavailable: the checker treats historical commit
lookup as opportunistic, verifies a resolvable blob against its SHA-256, and
always validates tracked source/fixture hashes. A wrong resolvable blob/hash
still fails. Re-running without `--write` verifies reproducibility; unexpected
dirty source or output bytes are refused. Before any output is written, all
four approved files and their parent chains must remain inside the resolved
repository root, without symlinks, reparse points or file aliases. Any invalid
output refuses the whole write pass.

Externally owned contracts remain references until their owner promotes them.
Do not copy or reinterpret another protocol's normative corpus here.

Historical producer tests use local Git evidence when available. A minimal
disconnected snapshot may omit an older release's commit; the registered frozen
producer source then supplies the same bytes, checked against both its SHA-256
and Git blob identity before execution. Other Git errors still fail. This does
not fetch history or replace the registry's independent provenance checks.
The historical test's read-only Git command trusts only its explicitly selected
checkout for that invocation, so a root-staged test snapshot does not depend on
the test user's redirected HOME carrying a global `safe.directory` setting.
