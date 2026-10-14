# Collector role totals and provenance

An agent-recommended measurement-only increment of
[Process Slot Ownership](README.md), following
[native sample accounting](reader-sample-accounting.md).
It supplies bounded collector-cost evidence for role calibration, not numeric
budgets, process membership, owner-liveness proof or admission enforcement.

## Contract

- Formalize the existing measurement key as plugin, `namespace-list` role and
  bounded namespace. Give each provider-local accounting scope an anonymous
  epoch so replacement/recreation/reset cannot present old totals as current.
  An epoch is measurement provenance, not a logical session owner or a slot
  release verdict.
- Reduce completed native sample accounting inside the existing reader
  observation lifecycle. Export an immutable current snapshot through the
  existing content-free telemetry seam; add no global observer, rival process
  registry, resident helper or default collection in other consumers.
- Count each actual completed attempt once, preserving success/failure
  distinctions and the identity already used for cached-sample deduplication.
  Pending work and cancelled waits do not contribute final cost.
- Keep collector wall and worker-thread CPU totals separate from command wall
  duration and reader-owned tree CPU. Sum only trustworthy completed costs;
  expose their known-contribution counts and unknown-contribution counts.
  With no known contribution, the total is unknown, not invented zero.
  Failed samples may still have trustworthy collector cost.
- Retain bounded current/last evidence only, not an ever-growing identity or
  event history. Counter overflow or lost continuity must remain explicit,
  never wrap, silently reset or masquerade as a complete total.
- Native callbacks and observation publication must see coherent snapshots.
  Provider replacement, command completion, cancellation and loop shutdown
  must preserve existing native-holder ownership and late accounting.
  Old-epoch telemetry must not be attributed to a replacement provider.
- Preserve enumeration, fallback, completion cadence, bounded shutdown,
  privacy and exception semantics. Add no CPU threshold, fan-out policy,
  circuit behavior, termination, deployment or system-setting change.

## Validation

- [x] Stable measurement keys and distinct epochs separate replacement,
      recreation and any explicit continuity reset.
- [x] Cached reuse/duplicate completion does not increment counts or cost;
      successive actual attempts do, including failed attempts.
- [x] Deterministic mixed known/unknown/zero costs produce truthful lower
      bounds and counts without mixing in command or process-tree CPU.
- [x] Pending/cancelled waits retain native holders and contribute only after
      actual completion, including late completion under an old epoch.
- [x] Concurrent snapshot/publication and repeated sampling retain coherent,
      bounded evidence; overflow/continuity loss is visibly incomplete.
- [x] Native Windows/Linux consumer fixtures export actual collector totals
      while optional-import, identity, cadence, fallback and privacy regressions
      remain green.

## Implementation evidence

The reviewed proposal landed in #6544. The producer-local ledger retains one
immutable locked snapshot. Each sampler's current/last completion cursor remains
pinned to its existing callback, not stored in a global history. Native
completion records collector cost before exposing the completion timestamp;
publication serializes an immutable snapshot outside the lock.
No continuity-reset API is introduced. Provider recreation gets a fresh epoch;
overflow/gaps remain visibly incomplete within the existing epoch.

The focused Windows selection passed 52 tests, including mixed known/unknown
costs, known zero, failed attempts, cached deduplication, overflow, sequence
rotation, concurrent publication, bounded cursor reclamation, late completion
after loop shutdown, provider replacement and retained native futures.
The native consumer fixture verifies the exported aggregate against actual
worker-thread CPU counter deltas. All nine bounded Windows consumer sub-suites
passed (4,297 passed, 97 skipped), with cleanup verified. Canonical Linux code
CI installed psutil 7.2.2 and passed all nine sub-suites (4,345 passed,
49 skipped), including the same native aggregate counter witness.
Merged #6547 as `8c04c5c64` after approval with zero open findings on
`033adc3f25` and actual CI success. All six focused validation items are
complete; this does not close the parent effort's policy, wider-reader,
global ownership, release or authorized adoption gates.

Expected-cost/fan-out policy, global reader coverage, logical-owner membership,
budget enforcement, no-progress reclamation and authorized adoption remain
separate parent gates.
