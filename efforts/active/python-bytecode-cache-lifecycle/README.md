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

The baseline selector reused an `installed-<digest>` namespace when installation
evidence was valid and otherwise created `unsealed-<id>` state beneath a runtime
root. Some waiting launchers cleaned their temporary namespaces; detached and
exec-style lifetimes could leave them behind. The existing explicit pruner is
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
  merged in #12145 and consumer adoption in #12569.
- [ ] Integrate process-exit, runtime-retirement and bounded orphan cleanup
  with the existing lifecycle owners, preserving active and rollback users;
  indexed retirement collection merged in #12506 and no-write adoption in
  #12569; legacy reconciliation still remains.
- [x] Update canonical launcher/installer sources and regenerate their declared
  consumers; keep independently updated old consumers compatible; merged in #12569.
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

- [2026-11-14 - consumer adoption and collector](journal/2026/11.14 consumer adoption and collector.md).
- [2026-11-13 - recovery and qualification](journal/2026/11.13 recovery and qualification.md).
- [2026-11-12 - provider and native proof](journal/2026/11.12 provider and native proof.md).
- [2026-11-11 - design and foundation](journal/2026/11.11 design and foundation.md).
