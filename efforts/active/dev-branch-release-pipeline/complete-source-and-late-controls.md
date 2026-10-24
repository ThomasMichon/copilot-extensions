# Complete source and late Main controls

Parent: [Dev/Main Release Pipeline](README.md), Phase 9d. Tracker: #8736.

**Status:** agent-recommended implementation plan; review before execution.
The parent Request retains the operator's exact direction and execution consent.

## Goal and non-goals

Open or update the final generated Main PR only after its complete source and
declared release artifacts exist, are qualified and have passed independent
raw-byte readback. Account for separately governed Main controls advancing
during preparation and before merge.

This extends the existing composer, frozen-candidate transport, artifact
publisher, qualification and Main gate. It introduces no second publisher,
release owner, package feed, updater or deployment. Dev remains the durable
release identity. Preserve the existing under-thirty-minute complete cycle,
unit/effect budgets and separately required construction/launch qualification;
general native, real-Git and integration suites do not become release gates.

## Identity and ownership

- **Source release:** the admitted Dev commit and existing release owner.
- **Build composition:** frozen versioned/vendored payload and its complete
  construction, lock, compatibility and qualification inputs.
- **Main control snapshot:** pinned Main revision, trusted ownership
  declaration and exact admitted workflow/governance inputs.
- **Landing generation:** the actual composite source tree, its base Main,
  artifact bindings and current admission. It is an operation receipt, not
  another source release or artifact authority.

Do not silently reinterpret the existing signed index's `content_tree` field,
discard a source comparison or rewrite an immutable source index to make a new
generation fit. Reuse must prove full input equivalence through reviewed,
explicit bindings, including optional profiles and toolchain/package policy.
The full composite source changes when its controls change, even if the
package artifacts remain equivalent.

The Composite-source contributor owns this bounded slice. #8367 retains its
optional profile/index producer and approval API; #4876 retains source
acquisition and consumer realization. Existing configured-feed construction
and Main-control owners retain their independently governed contracts. A
shared interface requires coordination, not a competing implementation.

## Plan

### 1. Review and isolate the extension

- [ ] Land the parent vision/plan amendment before production changes.
- [ ] Inventory existing source-archive, release-index, unit-packet and
      Main-control interfaces; coordinate the exact shared seams.
- [ ] Keep new helpers small and independently testable rather than expanding
      the near-cap producer or forking its canonical source representation.

### 2. Retain complete final source through the existing publisher

- [ ] Finalize source transformations before freezing build inputs.
- [ ] Produce a self-contained source archive that reconstructs the complete
      candidate tree, including tracked metadata, file modes, admitted
      controls and materialized first-party source. An incremental Git bundle
      or the Dev tag's automatic source archive is not this proof.
- [ ] Preserve applicable archive/path/type and size limits. Any required
      limit change needs its own justified review; never prune source to
      make an archive fit or substitute third-party dependency bytes.
- [ ] Keep the source/archive manifest external to the tree/archive whose
      digest it records, avoiding self-referential identities.
- [ ] Extend the existing publication inventory, signing/admission and
      immutable raw readback before final PR publication. Missing, extra,
      changed or wrong-generation content within this generation's inventory
      refuses readiness; unrelated immutable generations are not overwritten.

### 3. Reconcile advancing Main without changing release ownership

- [ ] Read the latest Main and classify changes against the trusted explicit
      Main-owned contract before final candidate push. Never infer ownership
      from a broad `.github/**` prefix or a Dev-supplied declaration.
- [ ] Preserve admitted control additions, deletions and renames, including
      the distinct rules for operator-owned settings and promoter-written
      state. Do not resurrect removed controls from an older Dev snapshot.
      Unsupported governance ownership requires its separately reviewed
      extension, not automatic admission.
- [ ] For control-only advancement, keep equivalent frozen payload inputs,
      compose the latest admitted controls on the new base, and create a new
      complete source/archive/landing generation. Keep all old published
      generations immutable; never retarget a tag or overwrite asset bytes.
- [ ] Revalidate current signing, qualification, test-selection and admission
      policy. Old authorization, candidate-specific receipts and PR checks
      do not become current merely because package inputs are unchanged.
- [ ] Reuse only independently verified equivalent payload artifacts and
      compatible unit evidence; rerun invalidated obligations. A changed
      controller/authority may require a new trusted execution rather than
      self-authorizing the running workflow.
- [ ] Refuse or replan changed payload, superseded source and unclassified
      Main changes through the existing lifecycle. No wholesale overlay of
      older Dev content over a newer Main payload.

### 4. Publish the actual proposal, then land that exact tree

- [ ] Require complete publication/readback and current admission before
      opening or updating the final Main PR, including replacement generations.
- [ ] Recheck candidate, base, ownership and policy before merge. Main
      advancement invalidates the landing generation, not necessarily every
      equivalent build artifact.
- [ ] Use the managed replacement lifecycle and required new checks;
      refreshing controls is not permission to force-push an immutable
      candidate ref, abandon another owner or reuse stale approval.
- [ ] Require server-enforced strict base freshness at the merge boundary.
      A client fetch and expected PR-head check alone do not close the
      base-advance race.
- [ ] Verify the actual merged full tree equals the complete published
      composite. The eventual Main commit is a landing receipt; no required
      release artifact is built or rewritten after merge.
- [ ] Bound reconciliation by the existing total cycle and phase deadlines.
      Repeated control churn becomes an explicit failed/blocked cycle with
      an owner, not an unbounded retry or a larger time allowance.

## Validation plan

- [ ] Eligible in-process units prove complete archive reconstruction,
      canonical producer/verifier agreement, deterministic inventory and
      file-mode/path handling; omissions, traversal, wrong source and
      wrong-generation bytes refuse readiness.
- [ ] Preserve existing signed-index/profile defaults and immutable reuse,
      including changed locks, toolchains, compatibility and approval policy.
- [ ] Exercise Main control edits, additions, deletion/rename, ownership
      manifest changes and promoter-state preservation. Payload and unknown
      path changes must not be misclassified as reusable controls.
- [ ] Prove package reuse does not reuse a stale full-source archive,
      admission receipt, test-selection decision or PR approval.
- [ ] Prove changed signing/controller authority fails closed or obtains a
      fresh trusted execution under the existing owner.
- [ ] Prove both pre-push and pre-merge base advances, managed replacement,
      server freshness refusal and post-merge full-tree comparison.
- [ ] Run directly relevant simple integration separately under its existing
      isolation and bounds; actual Git, subprocess and native races remain
      outside the unit-only promotion/required-PR portfolio.
- [ ] After reviewed implementation and separately governed activation,
      retain one real complete cycle: immutable Dev/build/control/generation
      identities; complete Release asset digests and raw readback before PR
      creation/update; required checks; actual Main merge/tree; tagging;
      complete elapsed time under thirty minutes including queueing.
- [ ] Exercise a bounded late-control witness through the existing owner.
      Record whether it safely refreshes or refuses, and why. An ordinary
      quiet success alone does not establish control-race correctness.

## Completion

This slice completes only when its Plan and Validation Plan are resolved,
required changes and controls are actually landed, the live artifact-before-PR
and exact-tree contract is verified, and owned worktrees/claims are settled
through managed lifecycle. It does not complete the entire release, artifact,
construction, runtime or Main-control campaigns.
