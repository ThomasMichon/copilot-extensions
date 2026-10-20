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

HTTP generation 43 retains every generation-42 fixture unchanged. Its
re-attestations identify each original vector by path and LF-normalized hash,
advance only `protocol_version` advertisements and record the exact committed
source, Git blob and source hash. `test_http_targets_contract.py` checks that
relationship for all 42 vectors and keeps the separate generation-40 to
generation-41 and generation-41 to generation-42 checks. The hand-authored
`owned-remote-generation-43.json` vector
adds managed-owner negotiation and retained relationship inspection expectations;
it does not claim that source capture proves live deployment or accepted
cross-worktree transfer.
Current re-attestations can identify different exact commits for different
changed source files. Their hashes must match each current registered source
and their complete capture identity must match registered provenance; an
unrelated file does not require recapture solely to share a commit number.
The ownership generation records the durable database dependencies alongside
the source capture for its current vectors. Queue consumption and turn persistence
are atomic, schema 26 requires ordinary preparation before passive adoption,
and production import deferral preserves the native five-second gate. The
expected wire bytes, prior-generation fixtures and capability floors do not
change. Removing the selected queue row during authority inspection advances
to the next durable row; changed rows, database failures and authority failures
are not mistaken for removal or retried.

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
