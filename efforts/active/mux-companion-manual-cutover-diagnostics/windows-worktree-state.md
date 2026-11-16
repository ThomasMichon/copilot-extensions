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

The existing source already provides:

- Worktrees' negotiated `worktree-data-v1` placement and optional complete
  project inventory, including retained descriptors after checkout retirement.
- Efforts' explicit fresh managed initialization, complete consistent export,
  verification and optional contributor projection. Native Windows allocation/
  retry and export/copy controls were qualified in #10777.
- Logger's policy-first default-off existing-tick composition, local immutable
  collection/readback and contributor retention. Its collection admission,
  private staging, private member creation and directory barriers currently
  refuse native Windows.
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

- [ ] W0. Review and merge this source amendment before behavior changes.
- [ ] W1. Implement native Windows private publication and staging inside the
  existing Logger owner, with focused refusal, durability and exact-cleanup tests.
- [ ] W2. Connect and qualify the actual selected Worktrees/Efforts/Logger flow
  on Windows and POSIX, retaining default-off and policy/compatibility behavior.
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
