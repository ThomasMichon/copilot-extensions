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
- [x] R1 Deliver explicit retained finalization and runtime support declaration.
      Source #13314 merged; installed adoption is separate.
- [x] R2 Deliver retained relationship discovery without changing obligations.
      Source #13314 merged with bounded native consumer evidence.
- [x] R3 Deliver selected test-cache/source-lifetime disposition.
      Source #13314 merged; existing legacy caches remain outside retirement.
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
Mixed-version review tightened the boundary: both preparers explicitly select
`--retirement-ready` to allocate and use the new cache namespace. The existing
`venv_root` helper retains its legacy path, including when an older caller
imports the current helper. A selected-source capability declaration is required.
Legacy caches are not migrated or retired by this tool; their owners retain
their process-drain and disposition obligations.
Success applies only to the selected environment; sibling caches and other
source consumers remain independent gates.

The contained Linux cache packet passes 63 cases with one Windows-only skip.
It exercises the actual CLI, a separate busy lease holder, interrupted owner
recovery, partial failure, preview staleness and actual source-dependent imports
after process exit. Fatal-error lint and whole-file strict typing pass for
both target platforms. Native Windows qualification of R3 is still pending.
The [source-lifetime reference](../../../docs/retained-test-environments.md)
documents the terminal selection and the separate source-cleanup decision.

The exact R3 candidate passed all 64 selected cases on disposable native
Windows in [run 44405932083](https://github.com/ThomasMichon/copilot-extensions/actions/runs/44405932083).
A subsequent control also binds the source directory identity: replacing the
checkout invalidates an earlier retirement preview.

### 2026-11-18 - Independent cleanup and ignored-data protection

The R4 consumer constructs a real Root -> child -> grandchild chain across
three repositories, plus an independent sibling. All nodes finalize with
retained checkouts. Settled relationships remain discoverable. A separate
preview changes no bytes, and normal cleanup removes eligible nodes while
retaining one grandchild's ignored evidence.

Physical disposal now retains unclassified ignored content until its producer
preserves or explicitly reclaims it. This intentionally tightens the old unsafe
removal path; logical finalization and ordinary empty/clean checkout behavior
remain separate. Inspection uses the existing caller-owned process primitive,
five-second execution and hard stdout/stderr limits, repository-isolated Git
configuration, and no new service or launch-time work. The native process-use
check remains real in the cross-repository consumer.

The current Linux lifecycle packet passes 90 cases. The existing late-agenda
selection passes 17 cases, including data created during removal/registration
inspection. The legacy hold is not a writer migration: existing complete-export
support alone does not provide relocation or a safe new writer. Full agenda
rescue/cutover and final exact-head native qualification remain open gates.
The source-replacement cache control also passes in the updated Linux packet:
64 passes and the same one Windows-only skip. Whole-file strict typing passes
all 18 touched Python files for both target platforms.

### 2026-11-18 - Review corrections

Review of #13314 found four real gaps. Fresh disposal now inspects tracked
gitlinks and conservatively retains nonempty submodule paths. Advisory
classification uses a separate cheap history hint, so classified list and both
status renderers start no ignored-data observation. CWD retained discovery uses
existing records only, without invoking adoption. Marker publication uses unique
temporary files with exception cleanup; fresh previews recover valid retiring
state with orphan temporary files, including the older fixed temporary name.

Focused controls cover actual submodule evidence, owner-led disposition and
deinitialization, both tracked/untracked CWD cases with byte-preserved ledgers,
real rendering work counts, and interruption inside final marker publication.
The lifecycle/status/classification selection passes 256 cases with four legacy
fixture failures. All four failed selectors also fail against the committed
pre-review `prune.py`; #13300 tracks that receiver/pair-fixture gap, not a CI
exception. The cache and actor-free selection passes 77 cases with one
Windows-only skip.

The original required unit job refused changed source hashes before test
execution. Its corrected local packet passed all 5,499 admitted cases with
coverage in 132.381 seconds under the unchanged 300-second budget. The review
corrections add two more actor-free controls and require a refreshed complete
packet, current-head CI and review before merge. Earlier native Windows
qualification includes 140 published-head CLI/recovery cases in
[run 44417101889](https://github.com/ThomasMichon/copilot-extensions/actions/runs/44417101889);
updated native qualification is still separate from those prior results.

### 2026-11-19 - Complete-history rescue candidate

#13314 merged the retained-resource core after current-head approval and passing
required checks. R1-R3 are source-delivered, not installed adoption. R4/RV5
remain open for the complete legacy-protection and rescue qualification.

The bounded format-2 rescue candidate reuses Worktrees' actual allocation owner,
verifies a complete export and binds its original store identity and history.
It reserves a fenced destination, commits the source writer fence and recovery
record, then activates the copied tables in one transaction. Matching retries
preserve later accepted work; provider presentation additions do not change
stable placement identity. Unsupported manual JSON and format-1 lanes remain
protected. No operator data, installed selection or checkout has been moved or
deleted by this source candidate.

The native Linux allocator/rescue/parser/independent-reader selection passed
21 cases within its original deadlines. The preceding agenda compatibility
packet passed 322 cases with two platform skips on Linux and 259 with 65 skips
on Windows. The source now also has bounded-recovery and additive-metadata
controls; those final controls require refreshed source checks before
publication. The broad packet does not substitute for the separate required
unit-only CI, current-head review or installed/adopter evidence.

Documentation impact: the Efforts entrypoint and command references now name
the explicit rescue procedure, unsupported legacy states, durable cutover,
unknown outcomes and retained evidence. Contract impact: the optional helper
advertises `agenda-managed-rescue-v1` independently, reuses the existing
`worktree-data-v1` provider contract and leaves fresh initialization and ordinary
format-2 commands compatible. Old readers refuse the fenced source through
their existing transaction-time format check; the new destination remains
ordinary format 2. Source adoption does not require peers to upgrade together.
