# Native Windows worktree-state collection

Parent and continuing completion gate: [complete effort](README.md).
Existing ownership and acceptance: [worktree-state lifetime](worktree-state-lifetime.md).
Standing intent: [durable worktree data](../../../visions/plugins/agent-worktrees/worktree-data/README.md).
Bounded source coordination: #12914. Broader lifecycle context: #10968.

## Goal and source scope

Complete the native Windows local collection/readback path for owner-exported
agenda history, retaining checkout-independent placement and POSIX behavior.
This is a bounded follow-through on the existing D4/D5 design, not another
agenda writer, placement authority or archival campaign.

The admitted source request is:

> Approve source-only history/Logger slice

The implementation sequence and physical mechanisms below are agent
recommendations. Source plan review, implementation, synthetic-fixture tests
and normal PR delivery are in scope. Installation, collection enablement,
migration, retrospective collection, historical-data deletion, cache eviction,
user-output changes and other owners' live stores remain excluded.

## Reconciliation and owners

At the proposal cut, the existing source provided:

- Worktrees' negotiated `worktree-data-v1` placement and optional complete
  project inventory, including retained descriptors after checkout retirement.
- Efforts' explicit fresh managed initialization, complete consistent export,
  verification and optional contributor projection. Native Windows allocation/
  retry and export/copy controls were qualified in #10777.
- Logger's policy-first default-off existing-tick composition, local immutable
  collection/readback and contributor retention. Its collection admission,
  private staging, private member creation and directory barriers still
  refused native Windows.
- Conservative protection for checkout-local `.effort-agenda` and independent
  checkout retirement with managed data retained. Archive availability is not a
  checkout-disposal prerequisite.

Worktrees continues to own installation-aware project roots and descriptors;
Efforts owns store transactions, exports and writer identity; Logger owns
collection admission, staging, publication and readback. The existing Companion,
Relay, head, installer, archive and reclamation owners retain every independent
requirement, claim and hold. Existing selected roots and knowledge/machine
preferences are consumed, not replaced with a second configuration hierarchy.

This closes a platform gap in `domain-owned-lossless-export` and
`worktree-scoped-archival`; it does not extend the vision or complete its remote
archive, relocation, restoration or reclamation features. It follows the
[patterns](../../../docs/patterns/README.md)' a-la-carte composition,
cross-platform parity, installation identity and proportionate safeguards.

## Proposed implementation

1. Add the Windows edge inside Logger's existing publication/staging owner.
   Reuse its pinned no-redirect directory ancestry, the suite's existing native
   private-object creation where appropriate, exclusive member creation,
   file flushing and native owned-directory barriers. Create private objects
   with their restrictive DACL at creation, not after publication. Do not
   modify an existing destination's ACL or depend on permissive inheritance.
   Unsupported filesystems, access or barriers refuse visibly.
2. Connect that edge to the same collection namespace, exact revision identity,
   contributor metadata and receipt-last local readback flow. Preserve conflict/
   partial evidence and the existing domain lock shared with ordinary session
   publication. Reuse the current policy-first default-off tick; do not add a
   second scheduler, transport, destination registry or automatic enablement.
3. Qualify the complete synthetic owner flow on native Windows and Linux:
   allocate a new managed store, accept records, discover/export the exact store,
   collect/read it back, retire its disposable checkout and rediscover/read back
   the unchanged retained history. Preserve one live writer; do not relocate a
   legacy lane. An unavailable archive must not erase or hide independent data.
4. Update authoritative support/operating docs with the actual qualified tier
   and reconcile this parent. Source and disposable native-fixture acceptance
   do not establish installed adoption or remote archival acknowledgment.

No new CLI syntax, wire schema or configuration switch is proposed. Existing
export/verify/contributors negotiation remains against the actual selected
owner; old and rolled-back providers remain supported only for their advertised
contracts and otherwise refuse this optional path without provisioning.
Collection receipts remain v1/v2 with `remote_archive_acknowledged: false` and
`reclamation_authorized: false`; no schema is relabelled to imply stronger proof.

## Plan

- [x] W0. Review and merge this source amendment before behavior changes.
  #12916 merged as `8ef31e28bfdcc1cb9dae0f87d34f4ef460a6acc5` after
  current-head Copilot approval, no review comments and green required CI.
- [x] W1. Implement native Windows private publication and staging inside the
  existing Logger owner, with focused refusal, durability and exact-cleanup tests.
- [x] W2. Connect and qualify the actual selected Worktrees/Efforts/Logger flow
  on Windows and POSIX, retaining default-off and policy/compatibility behavior.
  Isolated source qualification only; installed adoption and remote archival
  acknowledgment remain independent.
- [ ] W3. Land the tested source, update actual platform-support documentation
  and reconcile the existing lifetime plan without closing unrelated gates.

## Validation Plan

- [ ] Native private file/directory creation prevents broader inherited access;
  existing foreign/unsafe objects, redirects and replacement fail without ACL
  repair. Faulted durability/publication never produces a successful receipt.
- [ ] Interrupted, conflicting and exact-repeat collection retain identity and
  evidence; complete member, schema, revision and contributor corruption fail
  fresh readback. Existing POSIX protection and namespace coexistence still pass.
- [ ] Real owner processes preserve all accepted operations and contributor
  provenance under concurrent mutation, then freshly read back the retained
  revision after actual synthetic checkout retirement.
- [ ] Observe/default-off, dry-run, privacy refusal, wrong repository/target/
  installation, unsupported target/provider and provider rollback remain explicit
  and effect-free before their existing admission boundaries.
- [ ] Exercise the first-touch synthetic Windows path without prior staging or
  collection state, plus Linux regression integration. Keep heavy/native work
  in an existing explicitly selected lane; required PR CI remains scoped.
- [ ] Record focused units, real-process integration, fresh-environment/native
  and unavailable external/macOS tiers separately. No hardware power-loss,
  remote acknowledgment, installed collection or migration pass is inferred.

## Journal

### 2026-11-16 - Source proposal

Reconciled the existing D1-D5 contracts and native Windows #10777 evidence.
Placement/export and retained discovery already exist; Logger's native private
publication/staging refusal is the remaining implementation boundary selected
here. No runtime, settings, live data or cleanup effect has been performed.

The source amendment merged in #12916. W1-W3 and all runtime/native acceptance
remain open; the merge does not enable collection or grant data-effect authority.

### 2026-11-16 - Local source qualification

The native edge and composed owner flow are implemented. The focused Windows
packet passes 16 controls, including inherited broad access, unchanged unsafe
staging, junction refusal, long paths, failed barriers, retry and exact cleanup.
The existing selected-owner explicit/tick/missing-session-source process cases
and actual checkout-retirement/retained-history flow pass on native Windows.

The exact committed source fixture also passes 243 Linux controls with 13
platform skips across collection, namespace coexistence, provenance, preserved
history, tick, pipeline and ordinary publication. Earlier cross-filesystem
source runs exhausted the runner's group clock; native filesystem execution
resolved that venue cost without increasing containment or production limits.
Windows-only fixture additions do not claim a Linux platform pass.

These are synthetic first-touch filesystem/process results, not installation,
real collection, migration, remote archive acknowledgment, hardware power-loss
or macOS proof. W3 retains external selected-source qualification, review,
required CI, source merge and parent reconciliation.

### 2026-11-17 - Review correction

Review identified a real retry defect: a directory could exist after its parent
barrier failed, and a retry could skip that barrier. Native durable traversal now
uses the explicitly admitted owner root and reflushes every entry inside it,
including existing entries. The owner-root parent must already exist; missing
ancestors are not silently allocated. Six failed-parent/retry levels and an
absent-parent control cover this defect class. Partial export evidence still
requires owner reconciliation rather than an automatic overwrite.

The correction passes the native Windows 41-case edge/owner packet before the
subsequent native-boundary typing corrections. Strict whole-file checking now
passes all eight changed Python files for both declared Linux and Windows type
platforms. The affected Linux consumer packet passes 187 cases with one platform
skip, without increasing production or runner limits.

The independent native Windows lane qualified source
`e60c3142c61b6b11a64f3b4c3106e37568655cee` in run `43992304548`: all 41
cases passed on Windows Server 2025, AMD64, Python 3.12.10. A local ordinary
Windows run had an unresolved project-resolution failure; its subsequently
instrumented diagnostic pass is not counted as normal-path qualification.
The independent source result does not diagnose that local environment.

Required CI then refused the changed type-catalog source digest, before running
units. The catalog now selects the existing YAML stub lock; its exact admission
digest is refreshed without changing selected nodes, budgets or effect guards.
Required CI and current-head review remain W3 gates.

Review also identified that a private directory does not prove the privacy of
a restored Windows member with an explicit broad access rule. Typed reads now
validate each opened database/metadata/reused-reference file while its
no-redirect read handle is held. The database handle remains held through owner
verification. Existing permissions are never repaired. Six native regressions
restore byte-identical broad members inside an otherwise private collection;
readback/retry refuses and preserves their bytes and permissions. All 29
focused native publication/privacy controls pass. This correction still needs
complete owner-flow, strict, required-CI and fresh-head review qualification.

The next independent Windows run exposed an elevated-token compatibility error:
ordinary private export members can be owned by the token's native default owner
rather than its user SID. Read admission now accepts only those actual token
owners while retaining the same independent private-DACL check. A native
inherited-private export control covers that creation/read path; broad restored
member controls remain unchanged. The failed run is not qualification.

A subsequent review found a separate callback gap between database verification
and contributor reading. Contributor reads now hold their own protected database
handle, and complete readback retains database/manifest handles through its
receipt decision. Native restore-attempt controls cover source export, collected
copy and retained readback. The Windows correction packet passes 49 cases with
two unselected tick variants; fresh full native qualification, strict checks
and current-head CI/review remain required before delivery.
