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

- [ ] Implement shared cache placement and ownership without per-plugin
  divergent cache roots or a new resident cleanup service.
- [ ] Integrate process-exit, runtime-retirement and bounded orphan cleanup
  with the existing lifecycle owners, preserving active and rollback users.
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
  5 platform skips). No real cache, runtime or service was changed.

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
