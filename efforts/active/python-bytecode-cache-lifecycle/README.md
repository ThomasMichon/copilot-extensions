# Python Bytecode Cache Lifecycle

## Goal

Current operator request, verbatim:

> Help come up with a design/effort and then implement a plan to move the cache out of agent-worktrees and ensure we achieve incremental cleanup as we go, so we this stops growing boundlessly; also add cleanup for existing cache folders so as people install updates they get disk relief.

The preceding location preference remains part of that request:

> I think it would be good to keep this cache out of ~/.agent-* since it's more of a python system thing

- **Slug:** `python-bytecode-cache-lifecycle`
- **Repo:** copilot-extensions
- **Branch(es):** dependency-ordered PRs against `dev`
- **Created:** 2026-11-11
- **Status:** Active
- **Parent:** [Derived Disk Hygiene](../derived-disk-hygiene/README.md), bytecode follow-on
- **Coordination issue:** #10968
- **Vision:** reconcile the design with [authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement) before implementation

## Guiding Intent

Suite-created Python bytecode is regenerable cache, not durable tool state.
Give it a shared portable user-cache home, explicit ownership and a bounded
lifecycle. Normal execution and updates must not accumulate unbounded
per-invocation namespaces or leave the existing recognized backlog forever.
Preserve active users, rollback/runtime retention and unclassified content.

## Participants

| Participant | Role in this effort | Reached via |
|---|---|---|
| Cache lifecycle driver | Owns this design, source implementation, tests and PR stewardship | #10968; public source handle `jittery-beacon` |
| Derived Disk Hygiene coordinator | Retains the parent objective and unrelated tracks | #10968 |
| Existing installer and runtime owners | Retain construction, selection, service retirement and release authority | Existing install contract and scoped source PRs |

## Coordination

- **Topology:** one isolated source worktree; dependency-ordered, independently
  reviewed PRs against `dev`. No new persistent worker or collector service.
- **Host:** the cache lifecycle driver owns this effort's PRs through actual
  merge and source-workspace finalization.
- **Existing work:** #11122 delivered namespace reuse and explicit pruning.
  Extend that accepted foundation rather than replace its historical result.
  Parent tracks A/C/D, #9375's uninstall acceptance, #8932's maintenance
  proposal and #12013's installed-runtime qualification remain separately owned.
- **Handoff:** carry this Goal, original request, current source/review position,
  safety limits and all remaining validation. A merged phase is not completion.

## Context

The current selector reuses an `installed-<digest>` namespace when installation
evidence is valid and otherwise creates `unsealed-<id>` state beneath a runtime
root. Some waiting launchers clean their temporary namespaces; detached and
exec-style lifetimes can leave them behind. The existing explicit pruner is
conservative and does not provide automatic incremental collection of all
retired bytecode state.

Immutable runtime slots already have construction/retention custody and
protected-version GC. Installers also reconcile native daemon instances.
These are candidate integration owners, not permission to delete a cache
merely because a version is not current or a process is not visible.

## Request

The Goal preserves the current implementation request and location preference
verbatim. The requested outcomes are:

1. Move suite-produced Python bytecode outside per-tool `~/.agent-*` roots.
2. Bound ongoing accumulation with incremental lifecycle cleanup.
3. Give existing recognized cache folders a safe cleanup path during updates,
   so upgrading yields actual disk relief rather than permanent abandonment.

## Scope Approvals

The operator selected design and source implementation of those outcomes,
including repository review and validation. This is not authorization for
ad hoc real-cache deletion, terminating live runtimes, changing machine
configuration, installing a candidate on a host or fleet rollout.

Disposable fixtures may exercise the implemented update/retirement cleanup
contract. Real update adoption must still use the normal authorized installer.
Do not add an age-only automatic deletion policy to evade unknown ownership,
nor reinterpret an ignored file or a directory name as disposal authority.

## Plan

### Phase 1 - Reconcile and review the design

- [x] Trace current selectors, launchers, runtime references, retirement/GC,
  explicit pruning and legacy layouts from their actual source owners.
- [x] Reconcile the proposal with installer and lifecycle visions, patterns,
  contract evolution and the parent's existing accepted requirements.
- [x] Publish [the concrete design](design.md) covering portable placement, namespace identity,
  actual ownership, bounded incremental progress, update integration, legacy
  compatibility, concurrency, refusal/defer outcomes and safety.
- [x] Land the design/effort PR through the repository's review and CI gate
  before implementation.

### Phase 2 - Implement the shared lifecycle and consumers

- [x] Implement shared cache placement and ownership without per-plugin
  divergent cache roots or a new resident cleanup service; provider foundation
  merged in #12145, with consumer adoption still separate.
- [ ] Integrate process-exit, runtime-retirement and bounded orphan cleanup
  with the existing lifecycle owners, preserving active and rollback users;
  indexed retirement collection merged in #12506, consumer no-write adoption
  and legacy reconciliation still remain.
- [ ] Update canonical launcher/installer sources and regenerate their declared
  consumers; keep independently updated old consumers compatible.
- [ ] Add bounded, safety-qualified cleanup of recognized existing cache trees
  to normal updates, reporting actual reclaimed and retained outcomes.
- [ ] Preserve the explicit inspection/prune surface and document new placement,
  legacy handling, incremental progress and refusal behavior.

### Phase 3 - Prove and deliver the complete flow

- [ ] Pass focused unit, lint, generation and install-contract gates.
- [ ] Prove fresh install, repeated invocation, process exit/retirement and
  repeated upgrades against isolated native fixtures and practical clean-room
  scenarios, including both Windows and POSIX behavior.
- [ ] Demonstrate bounded legacy disk relief and stable continuing cache growth
  without deleting protected, unknown or non-bytecode content.
- [ ] Land implementation through required review and exact-head CI; verify the
  release state before describing the change as released or deployed.
- [ ] Reconcile the parent bytecode track, remaining scopes and this effort's
  entire Plan/Validation before Done and normal source-workspace finalization.

## Validation Plan

- [ ] Valid installed reuse, development/unsealed behavior, interpreter/build
  separation and portable cache-root resolution.
- [ ] Native current-user ownership/permission boundaries reject pre-created
  foreign-owned or shared-writable caches before selecting executable bytecode.
- [ ] Active/retained/rollback/construction owners, interrupted callers,
  stale ownership, concurrent allocation and cleanup races.
- [ ] Strictly bounded discovery/work with persistent fair progress across
  repeated updates; no repeated starvation behind a retained namespace.
- [ ] Legacy idle bytecode is actually reclaimed under admitted proof;
  live/unknown, unexpected files, retained evidence, links/reparse points,
  unreadable state and replaced paths are preserved with truthful reasons.
- [ ] Incoming readiness and current selection remain authoritative; independent
  cleanup errors or incumbent retirement do not veto a successful replacement.
- [ ] Current required tests, generated-source audits, install-contract checks,
  documentation-impact review, AI attribution and sanitized publication.
- [ ] No real host cleanup or candidate rollout is claimed from fixture,
  source-merge or release evidence alone.

## Proposal

The [proposed design](design.md) selects the OS user-cache directory, completed
runtime namespace reuse, no-write unsealed execution, runtime-retirement
integration and evidence-qualified incremental legacy cleanup. Its budgets,
mixed-version compatibility and safety assertions are implementation acceptance
gates, not claims that the current code already provides them.

## Journal

### 2026-11-14 - Consumer source migration and native controls in progress

- Added the complete-vector facade while preserving the legacy always-prefix
  API and standalone loader. Minimal bundles without cache companions take
  `-B`; 15 focused facade/legacy controls pass without widening the module cap.
- Migrated canonical payload templates, Worktrees dispatchers/hooks, Manager
  launchers and Python producers toward policy-version-2 vectors. Older
  providers take `-B`, not a newly minted legacy namespace. Read-only and
  source-injecting bootstraps do not allocate a prefix.
- Native Manager CMD/PowerShell installed-cache and no-write controls pass.
  Corrected CRLF byte transport, stock PowerShell array decoding and CMD probe
  quoting through actual launchers. Bridge and Codespaces launch packets pass
  six and 16 cases respectively; broader consumer acceptance remains open.
- Vault's Windows launcher and strict daemon-admission controls pass the
  selected new policy cases. An independent Unicode-home fixture initially
  failed because RedirectionGuard refused its junction; a physical isolated
  Unicode home proves the same decoding contract and passes without weakening
  the guard or changing a host runtime.
- The broad Invocation packet reached its unchanged aggregate limit and
  exposed inherited no-self-provision fixture state. The corrected isolated
  first-use and mixed-version residual passes all 20 cases; the actual native
  cache-vector packet passes 14 cases. These focused results are not a pass of
  the timed-out complete file. Manager's serial packet passes 162 cases with
  14 platform skips; its corrected residual and engine-client packet pass one
  and 130 cases respectively.
- Windows global frontdoors and receiving-ledger controls pass 13 cases;
  outer native hooks pass 12 cases with six platform skips, and explicit
  selected-update forwarding passes 42 cases with 32 platform skips.
  Dispatch's native descriptors pass nine cases; Vault's selected launcher
  and same-process controls pass two six-case packets.
- A disposable Linux source export proves the POSIX helper's three cache
  policies, six generated frontdoor cases, three legacy global frontdoors,
  six native hook cases and 22 service-publication fault/best-effort cases.
  Exact native unit-writer and STOP-admission controls pass 83 cases on both
  Windows and Linux. The source change includes the dependent closed-shape
  admission for Vault's new helper and installed/legacy cache vectors.
- Source migration, regeneration, full consumer regression checks and review
  are still in progress. Legacy update relief, complete-flow evidence, release
  verification and the parent reconciliation remain unfinished; no real cache
  deletion, candidate install or host rollout is authorized or claimed.

### 2026-11-14 - Indexed collector merged; consumer migration next

- Merged #12506 after current-head Copilot approval, resolved review threads
  and every required/source-retention/bootstrap CI gate passed. The collector
  is on `dev`; this is not an installed rollout or completion of this effort.
- Final native collector/provider controls pass 68 Windows cases (nine skips)
  and 66 Linux cases (11 skips). Reference compatibility passes 53 Windows and
  35 Linux cases; four constructed-environment and five narrowed foundation
  admission cases pass. The broader foundation/Bridge selection reached its
  unchanged packet ceiling, not a passing full-portfolio result.
- The final exact-source unit packet passes all 4,973 admitted cases after
  preserving new upstream inventory. Consumed native fixtures were removed
  through their original scratch owner. No module-size baseline was widened.
- Removed the consumed collector export/draft directory after merge, reclaiming
  2,412,164,185 logical bytes of owned validation scratch. This is not legacy
  application-cache reclamation and does not satisfy that remaining gate.
- Continue with canonical launcher/provider-vector adoption and no-write
  fallback for older providers, then bounded evidence-qualified legacy update
  relief. The parent issue and all remaining Plan/Validation gates stay open;
  real host cleanup and candidate rollout remain outside this authorization.

### 2026-11-14 - Archive and build-context correction

- Native Windows archive/finalization/cache controls pass 94 cases with eight
  skips; Linux passes 87 with 15 Windows skips. All 4,954 unit assertions passed,
  but the proof gate correctly refused the initially static Gateway test because
  it exercised no production owner. The corrected test now consumes the actual
  Fleet Manager injected-resource contract before checking COPY/context inputs.
- A Docker client is present, but its existing engine is unreachable in this
  lane. No daemon was started or configuration changed; full image execution
  remains unavailable rather than being inferred from static input checks.
- The corrected complete unit proof passes all 4,954 cases (27 nonselected),
  with production contribution and one coverage capture verified. The consumed
  failed fixture and successful replacement fixture were removed through their
  original scratch owner.
- Publication and review remain before this collector slice can merge. Launcher
  migration, legacy update relief and whole-flow acceptance remain unfinished.
- First exact-head CI caught a transport distinction in the newly hash-bound
  Docker inputs: an automatic Windows checkout used CRLF while canonical Git
  blobs and Linux CI used LF. Declared LF for these two repository data inputs,
  normalized only their working copies and refreshed admission hashes. This
  preserves strict byte admission rather than weakening it with normalization.
- A further review identified two remaining controls: a protected archive
  descriptor alone did not protect the filename reopened by POSIX import,
  and retirement's process census did not check the collector deadline between
  probes. Added protected resolved ancestry admission and threaded the
  cache-only deadline through recorded and native process probes, retaining
  work on expiry without changing ordinary runtime-GC policy. Native Windows
  collector/provider controls pass 59 cases with nine platform skips; runtime
  reference controls pass 53 cases. POSIX, exact-source CI and review remain.
- The next review found the canonical helper-retention bundle missing its new
  mandatory GC dependency, and protection metadata still outside the deadline
  when PID protection was disabled. Retained bundles now carry the helper and
  archive, and protection metadata/version discovery use the same checker.
  Extracted live-process aggregation into the existing lease owner to satisfy
  the unchanged shrink-only facade ceiling rather than widening its baseline.
  Native Windows controls pass 68 cases with nine skips, including GC from a
  retained helper bundle after removing the acquisition source; 53 reference
  compatibility and four constructed-environment cases pass separately.

### 2026-11-13 - Recovery and current-source reconciliation

- Resumed the same source owner after interrupted validation, preserving the
  original no-rollout and no-real-cache-deletion limits. Rebased the unpublished
  collector onto current `dev`, retaining newer installer changes and regenerating
  only conflicting derived archives and source-admission metadata.
- Rebased Windows collector/provider/interpreter controls pass 77 cases with
  seven platform/native-link skips. Native Linux passes 69 cases with 15
  Windows skips, including the shipped `-I -S -B` GC archive path.
- The complete Linux unit packet exposed a coupled resource-test assumption:
  the new shipped cache helper increases the resource set. Corrected the test
  to assert the exact source-owned set and duplicate-free outputs rather than
  the old fixed count. The first run reached 2,820 passing cases before that
  assertion; a corrected complete run is still required.
- Updated the complete SDK fixture declaration and constructor fixture inputs
  for the two new package modules and shipped runtime helper. The final admitted
  Linux packet passes all 4,914 tests (23 nonselected cases), with coverage
  recorded once. Its disposable source/tooling fixture was removed through the
  original scratch owner after the result was captured.
- Removed five consumed source-export archives, reclaiming approximately
  1.49 GB of disposable validation output while retaining the current review
  draft/export. This is not real application-cache reclamation.
- Collector publication, review and exact-head CI remain; consumer migration,
  legacy update relief and the whole effort's acceptance gate are unchanged.
- The first collector review identified five concrete controls to tighten:
  metadata lookup errors must not imply absence, finalization needs a viable
  minimum entry budget, normal POSIX group-writable mirrors remain behind the
  private traversal boundary, and deeply nested JSON must not reset fairness.
  Added native regression cases and synchronized the canonical fixes.
- First-head CI also identified missing transitive release entries and the
  retained source-preparation archive's incomplete cache-module declaration.
  Corrected both declaration groups. The native Windows retained-controller
  test then exposed Git's rejection of extended `--output` spelling; preserve
  the private native handle and pass the equivalent ordinary Git path.
  Validation and re-review of this correction batch remain in progress.
- Corrected review controls pass 84 Windows cases with eight platform skips
  and 77 Linux cases with 15 Windows skips. The exact corrected Linux source
  also passes the real retained-controller preparation case and all 4,914
  admitted units; the successful disposable fixture was removed.
- A copied-fixture diagnostic localized the Windows retained-controller
  timeout to progressing materialization, final Git status and content hashing,
  not a blocked archive. Its new complete path exceeds the native module's
  old 90-second aggregate on a slower host. Removed all diagnostic edits and
  gave only that case a finite 180-second ceiling; production deadlines and
  assertions remain unchanged. The ordinary native rerun is in flight.
- The ordinary Windows retained-controller case passes in 131.23 seconds.
  Published the first correction batch and resolved all five original threads.
  A second review requested a deadline check between saved continuation-frame
  opens; added that check and a slow-operation regression.
- Rebased the published source without discarding its ancestry. Preserved
  newer upstream installer/source declarations and the shared Windows Git-path
  normalizer, which independently supplies the same serialization correction.
  Refreshed only this collector's generated metadata and retained the entire
  Goal and remaining launcher/legacy acceptance gates.
- A third review extended the same deadline requirement to initial ancestor
  admission and catalog setup. Threaded an optional checker through those
  internal owners, including native metadata checks and SQLite admission,
  without changing existing allocation callers or mandatory unwind/commit.
  Added slow-ancestor, final-root, catalog-setup and metadata-error controls;
  the complete native Windows cache packet passes 90 cases with eight skips.
- The fourth review found a missing Gateway container build input, missing
  pre-import archive ownership/write protection, and finalization starting a
  second mutation after expiry. Added the runtime/cache inputs to the recipe,
  context allowlist and existing witness declaration, plus a cheap canonical
  build-input contract check. Its two data files are explicitly hash-bound and
  remain nonexecutable in unit admission; this does not qualify a container run.
- Archive admission now checks the opened identity and POSIX root/current-user
  ownership/write bits or the existing Windows file-handle DACL owner before
  import. macOS conservatively refuses nonempty/unverifiable extended ACLs.
  A native writable-archive canary proves refusal before code import.
- Carried the budget through anchored metadata/read/removal operations and added
  slow finalization controls before owner cleanup and namespace removal. The
  complete native Windows cache packet passes 94 cases with eight skips.
  Shipped-script fixtures use exact copied bytes with fixture-owned permissions,
  rather than silently trusting the source checkout's ambient DACL.

### 2026-11-12 - Indexed collector native fixture proof

- Implemented the new-layout collector behind normal version-slot GC, using
  indexed keyset rotation and identity-bound depth-first continuation rather
  than a directory census. Construction, retention, publication, selectors
  and durable runtime references remain the existing owner's decision.
- Native Windows fixtures pass for bounded large-tree convergence, exact
  successful logical-byte accounting, fairness past retained namespaces,
  interrupted directory/owner removal, replaced paths, unknown content,
  hardlinks, real GC-to-cache integration and absent-slot construction.
  The combined collector/provider packet passes 38 cases with seven explicit
  platform/native-link skips. POSIX and canonical packaging validation remain.
- Corrected a native Windows sharing conflict exposed by actual deletion:
  metadata is read through the existing guarded handle, not a second CRT
  open which cannot share its pending delete access. This remains source and
  disposable-fixture work; no installed runtime or real cache was changed.
- Native Linux collector/provider/interpreter controls pass 68 cases with
  15 Windows skips. An actual Windows `-I -S -B` shipped-script invocation
  loads its own lifecycle archive, removes the retired fixture bytecode and
  converges through repeated GC while preserving stdout JSON.
- Removed unrelated optional-mirror churn from broad regeneration. Corrected
  standalone resource admission for the new helper and kept the finite
  constructor guardian's narrower retained-source closure unchanged. The
  source/helper admission gate passes; the complete unit rerun remains.
- Consumer migration and legacy discovery/relief are still unfinished.
  The indexed collector does not pretend to inventory older unindexed roots.

### 2026-11-12 - Provider merged; collection is next

- #12145 merged as `2ecd801f0e054e5357442c5c2cbf3762c9771617`
  after exact-head Copilot approval, required CI success and resolution of
  all nine concrete review findings. The provider remains additive: current
  launchers are not migrated and no automatic cache reclamation is claimed.
- Pulled the source workspace forward without replaying the already-landed
  squash. Retained the canonical effort and source owner; this phase does not
  complete the operator's Goal.
- Removed the consumed provider-validation scratch through its original owner,
  including approximately 1.45 GB of source archives. This is disposable test
  evidence cleanup, not deletion of a real application cache.
- Next: implement bounded indexed collection under the existing runtime
  cleanup/construction/retention owner, then adopt consumers and add qualified
  legacy update relief. Retained, live, rollback and unknown state remain
  protected; no new cleanup service or process-stop policy is authorized.

### 2026-11-11 - Additive provider foundation

- Implemented the version-2 private platform-cache policy provider, retaining
  the legacy helper and current consumers unchanged. Native ownership/DACL
  checks protect executable bytecode; unsealed or unsupported state gets
  the no-write result without per-invocation allocation.
- Added indexed installation-local ownership and progress storage before
  publishing an allocation's owner record. This prepares bounded collector
  discovery; it does not implement retirement or legacy deletion yet.
- Focused provider/interpreter controls passed: native Windows 48 passed /
  1 POSIX skip; native Linux 44 passed / 5 Windows skips. The Linux run used
  an exact committed source export and the contained runner; the hardened
  container prerequisites were unavailable. Its deferred disposable-fixture
  cleanup was reconciled afterward, with all exact fixture paths absent.
- Canonical source validation, install contract and focused Python lint
  passed. The full mirror audit reported unrelated existing toolkit drift;
  the documented source-only gate validates inputs without asserting mirror
  equality. No deployed files, real caches or service lifetimes changed.
- The library remains pending static-type admission under #9807; this
  foundation does not claim type-portfolio graduation or certification.
- Expanded permission controls cover inherited Windows grants, private POSIX
  traversal, unresolved homes, sticky-parent first touch and Darwin's native
  ACL iterator ABI. The corrected focused packet passes on Windows
  (54 passed / 4 platform skips) and Linux (51 passed / 7 platform skips).
  Native macOS remains unavailable. Windows descendant ACL admission now uses
  an explicitly bounded metadata-only check, because bypass-traverse access
  defeats parent-only protection. Native Windows directory/file grants and
  entry/time exhaustion regressions pass (56 passed / 5 platform skips).
  Large Windows caches conservatively select no-write execution; the design
  and library contract state this reuse limitation rather than hiding a walk.
- Full Linux unit-packet execution exposed an older-Python test injection
  mismatch: that interpreter's TemporaryDirectory stores its removal callable
  separately from `shutil.rmtree`. The focused fault control now targets the
  actual TemporaryDirectory removal seam. The complete admitted Linux unit
  packet now passes (4,792 tests), including that regression. Rebased native
  Linux provider/interpreter controls pass (51 passed / 10 platform skips);
  successful disposable fixtures were removed through their scratch owner.
- Extended the same native protection boundary to the SQLite catalog, existing
  journal/WAL/shared-memory sidecars and owner record. Empty schema-zero
  catalogs recover serialized interrupted initialization without resetting
  unknown schemas; immutable owner records publish only after complete writes.
  Native Windows interruption and metadata-DACL controls pass (63 passed /
  5 platform skips). The corrected native Linux packet passes (53 passed /
  15 platform skips), and the complete admitted unit packet passes again
  (4,792 tests). Successful disposable fixtures were removed. No real cache,
  runtime or service was changed.

### 2026-11-11 - Design accepted; implementation starting

- #12131 merged as `42175ce8eb81cea7f48871b155ddbb9c119988b9`
  after exact-head Copilot approval, required CI success and resolution of
  the cache ownership finding. #10968 remains open.
- Pulled the source workspace forward onto the accepted plan. Phase 2 starts
  with shared placement and producer compatibility, followed by bounded
  retirement/update collection and native fixture proof. No runtime rollout
  or real-cache deletion is authorized or claimed.

### 2026-11-11 - Intake and isolation

- Located the existing #10968 campaign and accepted #11122 foundation.
  Current other claims are bounded guidance and Efforts publisher fixes,
  not competing bytecode-lifecycle implementation.
- Created an isolated externally controlled source worktree without launching
  an authoring session. Compatible effort adoption was positively verified.
- Published and read back the bounded bytecode follow-on ownership claim in
  [the existing coordination thread](https://github.com/ThomasMichon/copilot-extensions/issues/10968#issuecomment-6808191324),
  preserving other drivers' scopes.
- Captured the current request and preceding location preference verbatim
  before substantive design or implementation. No production cleanup,
  migration, service change or runtime rollout has occurred.
- Traced the shared selector, canonical shim templates, explicit pruner and
  version-slot GC. Reconciled the design with authoritative replacement:
  outgoing retirement/cache cleanup must not veto incoming readiness and
  complete selection. Retired-source subtree cleanup is distinct from a
  whole-namespace liveness claim; unknown legacy content remains protected.
- Cache trust requires a native current-user access boundary, not just
  matching metadata: shared writable roots or foreign-owned pre-created
  namespaces must never supply executable bytecode. The design includes
  native ownership/permission and ancestor-substitution controls with a
  no-write fallback, plus corresponding regression obligations.
