# Process-log Reclamation

Parent: [Session Intelligence and Accounting](README.md), Phase 1; issue #5676.

**Status:** Policy amendment reviewed and merged in #7016; implementation active.
This document specifies future behavior, not an available command or setting.

## Selected policy and boundary

The operator selected `verified-all-targets-opt-in`:

> Opt-in: also require verified ZIP publication to every configured target;
> require an explicit retention age

This authorizes source-code work and synthetic fixtures only. Live deletion,
configuration changes and rollout require separate authorization. The existing
quiet threshold is not a retention age. Keep current defaults and additive ZIP
snapshot behavior unchanged.

This closes the existing session-intelligence vision's preservation and
storage-transition intent; it does not extend billing, identity or collection
scope. Reuse the current sync lock, archive reader, content-addressed ZIP
publication and pinned source/destination boundaries. Do not add a resident
service, parallel scheduler or replacement transport implementation.

## Configuration contract

- Reclamation is a separate machine-local, disabled-by-default opt-in. Repository
  configuration, namespace rescue and an ordinary sync cannot authorize it.
- Enabling requires a positive integer retention age with no implicit default,
  process-log collection and ZIP snapshots enabled, and the existing explicit
  local-producer attestation. Missing, boolean, fractional, negative and zero
  ages are configuration errors, not coercions.
- Require an explicit nonempty set of required target identities. Never infer
  that today's successful transfer is the complete set. Include the active sync
  target and every additional destination required by the operator's policy.
  Default option stubs for inactive transport types are not destinations.
- Reuse existing target construction/options. The current sync engine selects
  one active target; this amendment does not silently turn session-state sync
  into multi-target publication. The implementation must make the complete
  reclamation target set explicit and resolve its options once per pass.
- Missing, duplicate, unresolved, unsupported or source-aliasing destinations
  block reclamation. A target change invalidates previous verification; no
  saved receipt can authorize retirement under a different target set.
- An offline or unsupported required target retains the raw source. Initial
  implementation may support a bounded subset of transports, but must refuse
  enabled reclamation for any unsupported required transport, not omit it.
- An explicit namespace push and any repository-filtered pass remain outside
  this scope. They must neither publish additional mixed process-log evidence
  nor run reclamation.

## Per-revision sequence

The following mechanism details are agent-recommended safeguards implementing
the selected policy, not additional operator-authorized live actions.

1. Under the existing sync lock, resolve the policy, complete target set and
   local producer root. Enumerate only supported raw local producer logs.
   Require both the selected retention age and the existing quiet threshold,
   plus a provably exited PID. Reused PIDs and unclassifiable names retain
   their files. Future mtimes are not old evidence.
2. Pin the source boundary and record the exact regular-file identity, size,
   times, logical name and bounded-stream content hash. Create or reuse the
   existing content-addressed ZIP without replacing an existing archive.
   Fully verify its single member against that revision, including length,
   decoding/codec support and hash. Retain the local ZIP after reclamation.
3. Publish that exact ZIP revision to every required target using its supported
   transport. Read back and fully verify the stored member on each target.
   A successful transfer exit, matching size/mtime, object listing, cached
   status, zero transferred files or unsupported no-op is not verification.
   Recheck archive entry identity around readback through the pinned boundary.
4. Require a successful local verification and a positive verification result
   for every resolved target identity in this pass. Partial target success is
   not a quorum. Do not persist a success-shaped fallback or authorize later
   retirement solely from an earlier pass's receipts.
5. Revalidate the source revision, local replacement and producer status at
   the retirement boundary. Changing bytes, identity, root or policy cancels
   retirement. Retirement must address the exact validated source entry, not
   a pathname checked earlier and unlinked later.
6. Commit retirement only through a platform primitive whose exact-entry
   identity and writer-exclusion guarantees have been demonstrated. A pinned
   directory alone does not close the final stat/unlink race or stop a writer
   holding an already-open descriptor. If those guarantees cannot be
   established on a platform/filesystem, report unsupported and retain the raw
   source; do not ship an unsafe pathname fallback.
7. Report inspected, eligible, retained, retired and failed files separately,
   with bounded filename-only diagnostics and reclaimed raw-byte totals.
   Verification, durability and cleanup failures remain visible. A post-commit
   acknowledgement failure must not remove ZIP evidence or claim rollback.

Readback proves the configured destination's stored bytes at verification time;
it does not prove a cloud client's remote upload or perpetual remote retention.
A destination requiring a stronger acknowledgement must supply that capability
or remain unsupported. OneDrive-backed local readback must not be described as
cloud acknowledgement.

## Crash, retry and dry-run behavior

- A dry run makes no ZIP, target, metadata, quarantine or source changes. It
  reports eligibility and missing verification/capability without claiming that
  retirement succeeded or would certainly succeed.
- The scheduled path may reclaim only after publication and all-target readback,
  never in the existing pre-transfer snapshot step. Manual snapshot-only
  `compact-logs` behavior stays additive unless an explicitly separate
  reclamation mode is selected.
- Crashes before retirement leave the raw file and any additive ZIP copies.
  Retries reverify evidence; content changes generate distinct ZIP revisions.
- If exact-entry retirement needs staging, staging must preserve unexpected
  identities and interruptions, avoid overwriting a resumed source, and expose
  recovery state. Never sweep guessed temporary paths or silently delete
  unrecognized artifacts on restart.
- Retiring raw evidence does not authorize deleting local/target ZIP revisions,
  remote raw copies, gzip evidence, session-state or rescued/foreign sources.

## Implementation plan

- [ ] Prove the exact-entry retirement and writer-exclusion boundary first on
  Linux and native Windows, with isolated synthetic adversarial tests. Record
  unsupported platforms/filesystems explicitly; no enabled fallback.
  Native Windows primitive and corrected sharing probes landed in #7023;
  POSIX explicitly refuses retirement and has no demonstrated deletion boundary.
- [ ] Add validated machine-local settings and an explicit required-target set
  without changing existing collection, snapshots or retention defaults.
  Strict policy parsing and explicit target resolution landed in #7026;
  enabled runtime settings remain refused until orchestration exists.
- [x] Add typed per-revision readback results through the target abstraction;
  implement bounded supported transports and reject the rest explicitly.
  #7026 supplies full filesystem ZIP readback for local/OneDrive targets,
  unsupported results for SSH/SSH-tunnel/ingest and no cached authority.
  Complete final-head CI and actual native Windows execution passed.
- [ ] Wire post-publication all-target verification and policy-gated retirement
  under the existing lock, keeping dry-run and manual additive behavior intact.
  The exact-ZIP-only publisher landed in #7038 with current-head Linux/native
  Windows coverage; it remains internal and does not yet orchestrate retirement.
- [ ] Document actual supported behavior and land a plugin changefile with the
  implementation; release/install and live retirement remain separate gates.

## Validation plan

- [ ] Defaults, malformed settings, missing age, empty/incomplete target set,
  filtered scope and disabled automation cannot retire a source.
- [ ] Two distinct required targets: both verified permits only that exact raw
  revision; one absent, offline, deferred, corrupt or unsupported retains it.
- [ ] Identical size/mtime with different bytes, invalid ZIP metadata/codecs,
  truncated members and target ancestor/entry swaps never satisfy verification.
- [ ] Active-idle/reused PID, append, same-size rewrite, truncation, rotation,
  resume and source replacement at every retirement seam preserve evidence.
- [ ] A pre-existing writable descriptor and concurrent new writable open cannot
  evade the retirement primitive's demonstrated exclusion boundary.
- [ ] Crash/retry and any staging recovery preserve unexpected bytes, existing
  good archives and replacement raw names; no broad cleanup is performed.
- [ ] Dry run is side-effect free; repeated successful passes are idempotent;
  byte counters and per-file failures reflect actual outcomes.
- [ ] Linux and native Windows execution demonstrate equivalent safety
  contracts, with unsupported primitives stated honestly rather than mocked
  into a native acceptance claim.
- [ ] Required review, complete same-head CI and bounded plugin validation pass;
  the parent effort stays Active until its own remaining gates are resolved.
