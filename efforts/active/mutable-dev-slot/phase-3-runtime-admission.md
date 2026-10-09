# Phase 3 runtime admission and build coalescing

Parent: [mutable-dev-slot](README.md), Phase 3.
Coordination: #5472; prior numbered-slot overwrite incident #777.
This is an agent-recommended execution decomposition of the existing reviewed
immutability contract, prompted by the operator's request to build durable
fixes and guidance for the correct flow. Review this slice before changing
installer admission or publication behavior.

## Outcome and boundary

A published numbered runtime slot is immutable. Ordinary install/update either
reuses its exact completed build or refuses conflicting content; `--force`
cannot authorize rewriting it. Repair selects a distinct health-gated
generation. Explicitly claimed `versions/dev` remains the narrow development
exception already defined by the parent pattern, never an implicit update mode.

Concurrent first installers for the same installation/version cooperate:
one owns construction, others wait boundedly, revalidate completion and content,
and reuse the winner. Contention is neither a corrupt-runtime diagnosis nor
permission to delete a lease, kill the owner, or write without ownership.

Code and dependency files used by published numbered-runtime processes must be
pinned to immutable runtime inputs, not an unversioned marketplace payload or
checkout. An explicitly claimed dev slot is the existing narrow exception:
its owner may select the editable worktree input under the parent dev contract,
never as an implicit ordinary-update fallback.
Construction ownership is version/installation scoped. Control-plane guards over
selectors, routing, provenance, and claims coordinate state, not code-file
mutation; they never authorize modifying a published slot or require holding
payload/source trees open.

Repair preserves the existing whole-operation cutover guard: acquire it before
choosing or allocating replacement generation state, and retain it through
replacement construction, health gating, confirmed promotion, and drain/
retirement or the required rollback/commit-forward outcome. When repair needs
both guards, the cutover/repair guard precedes the target construction lease.
An ordinary first build releases construction ownership before joining cutover
coordination, never acquiring the guards in reverse order. Existing installation
governance lock order also remains binding; allocation/completion callbacks must
not invert it against these guards. Prove the combined ordering with concurrent
ordinary-build, repair, and cutover tests before adoption is complete.

Completed candidates also need retention protection until selection/cutover is
settled: releasing construction ownership must not expose an unselected ready
generation to GC. Register that protection atomically with the GC admission/
recheck protocol before handing off the candidate or dropping construction
protection. A completed-slot reuse caller must acquire equivalent protection
before trusting the selected candidate. Keep candidate and rollback-generation
references through their actual promotion/drain or rollback outcome, and release
them through their owning lifecycle. This protects identity without touching
immutable slot files or relying on a directory-mtime grace period. All legacy
and installation-cell cleanup paths must respect these references, with the
same consistent lock ordering as construction/cutover governance.

## Existing home and evidence

The parent already requires ordinary refusal, claimed dev iteration, and
distinct-generation health repair for each adopting plugin. This slice does
not create a competing effort or declare those adoptions complete prematurely.
The vendored-installer-engine effort remains the broader installer consolidation
home; this work extracts only the admission/coalescing contract it can reuse.

The canonical `libs/versioned-runtime` already owns strict completion markers,
content-hash comparison, slot resolution/order, activation, incomplete cleanup,
and dev claims. Its authoring-time fan-out and install-contract gate are existing
seams; extend those instead of implementing independent marker formats.

The directly traced agent-worktrees Windows path acquires a nonblocking
version-specific build lease before checking complete-slot reuse. A concurrent
first builder therefore causes an otherwise-valid unified update to fail.
Separately, completion with a different payload hash falls through to package
reinstallation while incomplete cleanup deliberately preserves complete slots:
the same numbered path can be rewritten. #5472 records equivalent hazards in
other adopters. The platform inventories identify the surfaces, not proof that
each adopter is already safe.

## Execution

### 3a — Shared admission, construction ownership, and publication contracts

- [ ] Add one canonical installer-facing admission seam that distinguishes
      exact completed reuse, completed-content conflict, genuinely unfinished
      construction, and health-repair-required states. Decisions are explicit;
      unavailable validation and malformed ownership/completion evidence must
      not silently select a rebuild of a published identity.
- [ ] Fingerprint the full runtime install input from an attributable frozen
      source/snapshot, including source-only and vendored dependency changes.
      Do not hash mutable input before building and then publish a marker for
      different input observed afterward.
- [ ] Return exact completed reuse without taking an exclusive construction
      lease or invoking venv/package writers. Refuse numbered content drift,
      including forced updates, with actionable published-version/dev guidance.
      Completion publication is create-once for a completed immutable identity,
      not a timestamp/PID rewrite on every healthy reuse.
- [ ] Serialize unfinished construction with a real OS-backed, installation/
      version-scoped lease. After acquiring it, revalidate the target before any
      write; after contention, wait within the caller's bounded budget and
      revalidate/reuse the winner. Different content must refuse after the
      winner publishes rather than becoming a second writer.
- [ ] Release construction ownership on success, failure, cancellation, and
      process death. Keep its lifetime scoped to construction, isolated health
      gating, and immutable publication, not unrelated binstub/service work.
      Preserve separate activation/cutover serialization and governance checks.
      A repair's outer cutover guard retains the whole-operation lifetime above;
      releasing the inner construction lease does not release repair authority.
- [ ] Protect ready candidates and rollback generations through the owning GC
      retention protocol across construction release, cutover waits, concurrent
      cleanup, promotion, and drain. Matching-slot reuse needs the same atomic
      retention admission. Do not use immutable-slot mtime mutation as a pin.
- [ ] Surface timeout and actual storage/permission failures distinctly.
      Diagnostic owner evidence is attributable to the actual lease holder,
      not a completion helper's PID or a mere lock-file timestamp. A surviving
      marker file is not evidence that the OS lease remains held.
- [ ] Preserve unpublished candidate recovery without deleting or reconstructing
      an already-published/selected/rollback-pinned identity. A genuinely broken
      published slot enters the existing distinct-generation repair contract,
      never the ordinary incomplete-candidate cleanup path.
- [ ] Vendor the shared seam using the canonical sync tool, update the executable
      install-contract guard, and maintain standalone/offline plugin behavior.
      Installation-cell provenance and transaction authorization remain binding;
      legacy compatibility does not permit a new unqualified runtime root.

### 3b — First complete adopter, then phased portfolio rollout

- [ ] Use agent-worktrees as the first complete adoption, on PowerShell and
      POSIX entrypoints, with reuse/refusal/coalescing, claimed dev/release,
      and distinct-generation health repair. Do not check it off in the parent
      until every required adoption contract is implemented and verified.
- [ ] Propagate generation identity through discovery, selectors, manifests,
      running-version comparisons, recovery ordering, and process/lease evidence.
      Keep package version separate; a repair directory suffix alone must not
      cause repeated self-update or outrank a later genuine release.
- [ ] Adopt the remaining runtime plugins in independently mergeable waves.
      Preserve plugin-specific health, service, broker, and cutover hooks;
      coordinate with live installer PRs instead of superseding their changes.
      The canonical adopter inventory, not a hand-maintained plugin list,
      determines completion of the portfolio.
- [ ] For each wave, add plugin changefiles, run targeted contained suites and
      shared sync/contract gates, obtain review, merge, verify promotion, and
      deploy through the unified update flow. Preserve active service work
      throughout installation and generation cutover.

### 3c — Durable operator and contributor guidance

- [ ] Document ordinary update as reuse-or-create plus out-of-slot reconciliation,
      never stop-and-rewrite or unconditional reinstall into the selected venv.
- [ ] Distinguish `--force` payload/config reconciliation from permission to
      mutate a completed numbered slot. Remove contradictory stale/corrupt
      recovery advice as adopters land; identify not-yet-adopting plugins
      honestly rather than advertising a nonexistent repair/dev verb.
- [ ] Document bounded join/reuse for busy first builds, exact-version content
      drift refusal, explicit dev ownership/release, and distinct-generation
      health repair with rollback. Diagnose before retrying; never direct an
      operator to delete a held lease or kill an unattributed builder.
- [ ] Reconcile install-contract, mutable-dev-slot, lifecycle/contribution
      guidance, diagnosing-copilot-extensions, and touched plugin README/help.
      Installed-runtime fixes stay source-authored and versioned. Consumer
      projection refresh remains part of deployment, not an installed-file edit.

## Validation gates

- Real concurrent processes prove one first build for identical input, bounded
  waiter completion, reuse of the winner, and refusal of conflicting input.
  Exercise owner failure, process exit, cancellation, and permission/storage
  failure separately; do not rely solely on matching strings in installer files.
- Both actual installer entrypoints leave a completed numbered slot byte-for-byte
  unchanged for matching input and forced/content-conflicting updates. Source-only
  and vendored-input changes are detected. Legitimate out-of-slot reconciliation
  still runs on reuse.
- A failed direct health probe selects a distinct replacement without modifying
  the old slot or its rollback identity. Promotion is confirmed before retirement;
  recovery order and running-generation reports remain coherent after restart.
  Concurrent repair versus ordinary build/cutover proves the required outer
  guard, promotion-before-retirement, and absence of lock-order inversion,
  including installation-cell governance callbacks.
- Pause a real build/reuse caller after immutable completion but before cutover,
  run concurrent GC, and prove candidate/rollback retention and successful later
  activation. Prove retention admission cannot race an already-admitted deletion,
  and that terminal ownership release restores legitimate collection eligibility.
- Claimed dev/release preserves the original restore target, refuses a competing
  owner, and does not silently activate dev mode for ordinary install/update.
- Windows, Linux, and macOS behavior is accounted for separately. Record explicit,
  justified tracked gaps for unavailable live lanes; Linux is not a proxy for all
  POSIX lock/process behavior.
- Keep a focused, change-scoped fast CI contract and an explicit exhaustive lane.
  Extend clean-room first-build/provision/repair scenarios where practical.
  Tests use isolated roots and owned process groups, not live user tracking or
  service state. Stage new tests before the contained runner.

## Completion

This execution slice is complete only when its shared contract and every
explicitly adopted plugin's full guard/dev/repair flow are delivered, the
portfolio remainder is accounted for in the parent's tracked rollout, guidance
matches actual available commands, and merged releases are activated and
verified. A reviewed plan or an isolated guard is progress, not closure of
#5472 or the parent's Phase 3.
