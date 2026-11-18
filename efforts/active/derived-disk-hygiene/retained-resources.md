# Retained resources and independent cleanup

Parent: [Derived Disk Hygiene](README.md). Coordination: #10968.

## Goal and scope

Complete the retained-resource lifecycle without making physical deletion a
condition of logical completion. A completed worktree can keep its checkout,
execution history and reusable dependencies. A separate cleanup operation must
discover those allocations and ask their existing owners to revalidate them.

This is a bounded continuation of the existing effort, not another campaign
or a replacement for the active-obligation ledger. It covers Worktrees and the
repository test runner. Other producers, lock migration and the optional
removal-executor consolidation retain their existing owners.

Source plan review precedes behavior changes. Source publication does not grant
deployment, data deletion or another owner's custody; adopters supply those
permissions independently.

## Existing boundaries

- Ordinary `finalize` can retain a held checkout but can also remove an idle
  eligible checkout. A caller cannot explicitly request logical-only completion.
- Active transitive obligations intentionally stop at settled child edges.
  That is correct for unfinished responsibility, but not a retained-storage
  inventory.
- Cleanup and garbage collection enumerate the selected project's records.
  A parent in one repository does not thereby inventory retained children in
  another repository.
- Repository test environments have source-qualified cache paths and a native
  environment-identity lease across preparation/use. Releasing that lease is
  not a source-retirement transaction. A prepared environment can still depend
  on editable paths after its processes exit.
- Owned run scratch and bounded failure evidence already have a different
  disposition contract. Do not recast reusable environments as disposable
  scratch or weaken the existing external-path reclaim boundary.

## Design and observable outcomes

### R1 - Explicit retained finalization

Add a supported non-removing selection to the existing finalization operation.
It must run the same source, PR, effort, producer, child and protected-pair
checks. Successful completion records finality and settles eligible parent
responsibility, while preserving the checkout and branch deliberately.
Report finality, checkout presence and history separately.

Keep the existing invocation's compatibility behavior. Declare actual support
independently of package versions; unsupported selections refuse before
effects. No caller may simulate retention by creating a fake live-process hold
or silently retry removing finalization.

### R2 - Cleanup-oriented retained relationship discovery

Expose a bounded read-only view over existing owner-qualified worktree records
and known child relationships, including settled edges and finalized-retained
children. Keep the active-obligation query unchanged.

Return root/parent/child identity, repository and environment, observed logical
state, checkout presence, and unresolved edges without implying eligibility.
Use bounded depth/count and cycle handling. Missing, foreign-environment,
remote or unreadable records remain explicit unresolved observations.
Do not scan arbitrary source roots, allocate state during reads, fetch every
repository, or create a parallel permanent allocation registry.

Discovery proposes scope; each selected resource's existing cleanup owner
still performs fresh source, PR, custody, process, history and filesystem
identity checks at removal. A blocked child does not prevent independent
eligible siblings from progressing.

### R3 - Test environment and source-lifetime disposition

Give the repository test environment an explicit previewed retirement path
that reuses its native environment lease and ordinary-directory validation.
Scope it to a selected source checkout, target and platform. Preserve unrelated
targets, global UV/interpreter caches, history, reports and other producers.

The implementation must establish whether prepared imports still need source,
and must not retire that source while a dependent environment remains usable.
Either materialize an independent environment through the existing preparation
owner or retain the dependency until its owner retires the environment.
Do not infer independence from process exit, a released lease or elapsed time.
Do not introduce a background sweeper, global age/size eviction policy or a
second cache index.

Preview/apply must preserve wrong-root, replaced identity, busy environment,
partial failure and unknown-outcome protections. Serialize preparation/use and
retirement through the same identity owner; do not remove a leased root and
allow another inode to bypass an active waiter.

### R4 - Legacy protection and real consumer proof

Classify ignored content by its real producer, not Git-ignore status. Unknown
files and checkout-local agendas remain protected. A supported rescue uses the
domain's complete consistent export, verifies identity/history and performs one
owner-led locator/writer cutover. It never creates an empty replacement store
and calls that migration.

Prove Root -> child -> grandchild behavior across repositories with disposable
fixtures. Show logical finalization with retained bytes, then a distinct cleanup
preview/apply. Retain live, unknown, unlanded and history-bearing candidates.
Report per-resource partial outcomes without releasing unresolved custody.

## Plan

- [x] R0 Review and merge this subplan before runtime implementation.
      #13280 merged after exact-head approval and required checks.
- [ ] R1 Deliver explicit retained finalization and runtime support declaration.
- [ ] R2 Deliver retained relationship discovery without changing obligations.
- [ ] R3 Deliver selected test-cache/source-lifetime disposition.
- [ ] R4 Complete native consumer and legacy-protection qualification.
- [ ] R5 Publish reality documentation, reconcile parent acceptance and preserve
      release/adopter obligations without claiming an unperformed rollout.

## Validation Plan

- [ ] RV1 Unit controls cover new selection/default compatibility, open
      obligations, effort/PR/pair holds and truthful retained/removal results.
- [ ] RV2 Disposable multi-repository consumer proves live and settled child
      discovery, cycles, bounds, missing records and environment separation.
- [ ] RV3 Independent processes prove busy-cache exclusion, preview staleness,
      source dependence after process exit, serialization with preparation and
      interrupted/partial retirement through the actual owner.
- [ ] RV4 Native Windows and Linux fixtures prove the complete retained
      finalize -> discover -> preview -> independent cleanup flow. Keep existing
      process/deadline limits and account for platform skips explicitly.
- [ ] RV5 Unknown ignored evidence and legacy agendas remain byte-preserved on
      refusal; a synthetic rescue verifies accepted history before cutover.
- [ ] RV6 First-touch and legacy-provider tests refuse unsupported operations
      without effects. Required source checks, unit packet, type ratchet, CI
      and review pass for each published source head.

## Architecture and contract impact

This closes the existing
[checkout-retirement-is-not-data-reclamation](../../../visions/plugins/agent-worktrees/worktree-data/README.md#checkout-retirement-is-not-data-reclamation)
and
[preserve-legacy-data-before-disposal](../../../visions/plugins/agent-worktrees/worktree-data/README.md#preserve-legacy-data-before-disposal)
intent. It follows
[version-skew-safe contracts](../../../docs/patterns/service-contract-evolution.md)
and the existing single-owner, report-first cleanup pattern.

Provider support expands before ordinary callers use new syntax. Defaults and
active-obligation meanings remain compatible. No new daemon, runtime dependency,
periodic scan or global service is introduced. Inventory is an explicit bounded
read, not launch-time or session-start work. Native process and source checks
remain at their existing action boundaries; reuse observations within a single
bounded request rather than spawning per-field readers.

Required CI remains the admitted unit packet. Real filesystem/process and
native matrices belong in explicit qualification lanes, not a new unconditional
PR matrix. Documentation impact includes the Worktrees closeout/cleanup guides,
CLI reference, retained discovery contract and test-runner reference.

## Journal

### 2026-11-18 - Proposed continuation

The existing issue coordinates the bounded retained-resource follow-up. This
document states the source sequence and tests; no runtime behavior or live
cleanup is claimed by this plan.

### 2026-11-18 - Source plan accepted

#13280 merged after current-head Copilot approval, no open review findings and
passing required checks. A superseded direct source-attribution result was
reconciled through the documented PR-scoped workflow before managed merge.
Implementation starts with retained finalization and known-child discovery;
cache/source-lifetime disposition and full qualification remain open.

### 2026-11-18 - Retained finalization and discovery candidate

The R1/R2 candidate adds explicit checkout retention, a selected-runtime support
declaration, and bounded read-only traversal through settled child edges.
The existing active-obligation view and default finalization behavior remain
unchanged. Whole-file strict typing passes for both Linux and Windows targets.
The focused Linux packet passes all 80 cases. Fatal-error lint, documentation
consistency, version consistency and the install-contract check pass.

A broader 12-file compatibility packet reports 417 passes and 31 failures.
The unchanged source baseline has the identical 31 failing test selectors;
#13300 tracks that separate receiving-fixture qualification gap. Neither run
is a passing broader packet. Native Windows execution and the complete
multi-repository cleanup flow remain unqualified. R1/R2 delivery is not yet
merged, and the cache/source-lifetime and parent completion gates remain open.

Documentation impact: the CLI reference and closeout/reclamation guide now
name the explicit selection, runtime support check and retained discovery
boundary. No install or configuration default changes. Inventory is an explicit
bounded local read; it adds no launch-time work or background scan.

The exact R1/R2 candidate also passed all 80 focused cases on disposable native
Windows in [the hosted qualification run](https://github.com/ThomasMichon/copilot-extensions/actions/runs/44402600631).
This qualifies that packet, not the complete cross-repository cleanup flow.

### 2026-11-18 - Test-environment retirement candidate

The R3 candidate adds a selected source/target/native-platform preview and apply
owner. It reuses the runner's environment-identity lease and preserves the
leased directory. A terminal marker blocks preparation and `--reinstall`, so
an admitted waiter cannot recreate source-dependent imports after retirement.
Partial or interrupted work keeps that fence and requires a fresh preview.
Success applies only to the selected environment; sibling caches and other
source consumers remain independent gates.

The contained Linux cache packet passes 63 cases with one Windows-only skip.
It exercises the actual CLI, a separate busy lease holder, interrupted owner
recovery, partial failure, preview staleness and actual source-dependent imports
after process exit. Fatal-error lint and whole-file strict typing pass for
both target platforms. Native Windows qualification of R3 is still pending.
The [source-lifetime reference](../../../docs/retained-test-environments.md)
documents the terminal selection and the separate source-cleanup decision.
