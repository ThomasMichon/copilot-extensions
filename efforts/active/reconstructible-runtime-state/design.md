# Reconstruction and compatibility design

Back to [effort](README.md). **Proposal, not current behavior.** Source references
below are relative to the repository at the baseline in the effort header.

## Current seams and changes proposed

| Concrete source / symbol | Current evidence and protected value | Proposed responsibility |
|---|---|---|
| `plugins/agent-worktrees/src/agent_worktrees/record_read.py::read_exact_record` | Exact selected-project read, bounded JSON representation, typed `record-missing`; does not authorize launch | Keep exact reads cheap. Expose missing projection separately from absent resource; recovery is explicit, not a fallback global scan |
| `plugins/agent-worktrees/src/agent_worktrees/record_io.py::read_record_mapping`, `load_record_uncached` | One YAML read then typed parsing | Preserve original mapping/unknown fields for compatible updates; audit typed serialization before new fields become authoritative |
| `plugins/agent-worktrees/src/agent_worktrees/record_cache.py::cached_load`, `store` | Per-process `(mtime_ns,size)` memoization and independent copies; writes warm cache | Disposable accelerator, not durable authority. Rebuild/invalidate by published projection generation; do not make this cache the recovery source or remove write-side revision checks |
| `plugins/agent-worktrees/src/agent_worktrees/session_projection.py::validate_restored_hint` | Missing tracking record yields `restored-foreign`; absent expected relation yields `restored-collision`; revisions are checked | Distinguish missing recovery evidence from contradictory identity. Import candidates only after resource/Git/session corroboration; no historical live-head resurrection |
| `plugins/agent-worktrees/src/agent_worktrees/tracking_claim_write.py::apply_claim_add`, `apply_claim_release`, `apply_claim_settle` | Sidecar record lock, project mutation guard, persisted claim then history; reserved handoffs and protected producers cannot be casually released | Existing write owner integrates semantic durability under the same exclusion and revision boundary; retain reservation/settlement checks |
| `plugins/agent-worktrees/src/agent_worktrees/tracking_write.py::write_with_boot`, `dispatch`, `AmbiguousWriteOutcome` | Same-code fallback only before possible execution; sent-but-unknown outcome never auto-retried; verb capability floor | Preserve that rule. Add durable operation lookup at the owning transaction, not a generic retry after transport failure |
| `plugins/agent-worktrees/src/agent_worktrees/claim_history.py::record_event`, `history_path`, `SUPPORTED_KINDS` | Only PR claims; append after save, never raises on failure; lives in installation logs; no atomic save/history total order | Useful history, **not** acknowledgement-complete recovery truth. Do not silently promote it or rotating diagnostics into authority |
| `plugins/agent-worktrees/src/agent_worktrees/finalize_obligations.py::assert_obligations_settled`, `assert_producers_settled` | Missing record currently passes; otherwise local unsettled claims/handoffs block | Unknown recovery completeness must not become zero obligations. Keep unrelated reads; refuse/defer only destructive finalize until positive completeness/settlement |
| `plugins/agent-worktrees/src/agent_worktrees/obligations.py::normalize`, `should_abandon` | Unknown dispositions are active; reclaim needs positively gone **and** safe | Retain tri-state evidence and distinguish at-rest resource from released claim |
| `plugins/agent-worktrees/src/agent_worktrees/tracking_yaml_repair.py::targeted_doctor`, `apply_repair` | Exact record, read-only default, locked byte comparison, durable backup; deliberately narrow YAML repair | Reuse report/apply separation and compare-before-write. New recovery entrypoint must not widen this specialized repair into arbitrary migration |
| `plugins/agent-worktrees/src/agent_worktrees/providers/attribution.py::build_codename_marker`, `may_publish_codename` | Safe public handle; optional encrypted identity; unknown codename provenance omitted/refused by caller policy | Reuse disclosure rules and forge adapters; correlate stable opaque resource identity through a surviving private map, not raw public identity |
| `plugins/agent-bridge/src/agent_bridge/db_adoption.py::open_existing_writable` | Passive exact-file/WAL/schema check; no migration; caller owns routing authority | #1460 owns any hot-adoption relaxation. Cold recovery into a separate compatible projection is a different path |
| `plugins/agent-bridge/src/agent_bridge/db_schema.py::_SchemaMixin._init_schema` | Migrates lower schema versions, does not downgrade higher stamp; still executes bootstrap/column checks | A higher stamp is not proof a write is compatible. #1460/#1915 must supply operation-specific writer qualification before enabling writes |
| `plugins/agent-containers/src/agent_containers/lease.py::_read_leases`, `_write_leases` | Unknown core fields filtered; absent/corrupt file may return empty; known-field rewrite cannot retain arbitrary extensions | Component-owned lossless envelope and missing/invalid distinction. Preserve leases and uncertainty; no empty-store permission to borrow |
| `libs/installation-context/_installation_context_runtime_slot_ownership.py::_runtime_slot_paths`, `_validated_runtime_slot_ownership` | Exact canonical cell/slot, no reparse escape, snapshot/receipt identity and generation checks | Keep at provisioning/destructive effects. Installer owner supplies report/adopt/health-select rollback; no plan-side receipt forgery or numeric-version ownership election |

This table is the bounded trace, not a claim that every plugin schema or caller
has been audited. Telemetry implementation and Gateway services were not read.
Installer templates and generated copies must remain synchronized through the
existing tooling when their owner eventually changes them.

## Store taxonomy

Classification is by **facts and failure domain**, not filename or `.db`
extension. A mixed store is retained until every indispensable fact has a proven
external anchor. A manifest declares each scope, authority, required reader
capabilities, migration state and reconstruction adapter; no directory glob is
permission to delete it.

| Class | Examples | Recovery treatment |
|---|---|---|
| Existing user/system authority | Git common directory, refs/reflogs, linked-worktree administration, index, stash objects; tracked modifications and ignored/untracked content; original conversations and all checkpoints/session files | Preserve in place, read through owner contracts; never regenerate from a status cache |
| Configuration and security authority | Repository registration/source roots, knowledge bindings, account routing, consent, selected source/installation policy, vaults and encryption keys | Surviving configured roots; validate source/permissions. Never invent, rotate, export or silently switch accounts/credentials |
| Non-derivable semantic authority | Accepted ownership/transfer/release/conclusion decisions, tasks and results/steers, claim/lease lineage, reservations, obligations, accepted and uncertain external operations, terminal tombstones | Minimal durable per-resource snapshots plus journal outside runtime/cache roots; preserve original stores until migrated and proven |
| Resource-local evidence | Stable worktree/resource identity, repository correlation, creator/controller relationships, external references | Small gitignored folder in the actual worktree; private ACLs, no secrets or executable instructions. Corroborate against Git and current resource owner; evidence does not confer permission |
| Disposable derived state **after qualification** | Lookup/reverse-codename maps, parsed record cache, status/feed rows, rendered guidance, endpoint observations and a selected runtime's world-model projection | Rebuild separately; never infer no owner from absence; consume the owning readiness/status interfaces rather than reimplement them |
| Reconstructible executable artifacts | Immutable runtime slots, build scratch, generated launch wrappers and derived receipt indexes | Reinstall/reselect through the installer owner, retaining approved source/consent and current custody. A sole authoritative receipt is not disposable merely because it sits under a runtime root |
| Best-effort diagnostic data | Timed/rotated activity logs, trace samples and exported telemetry | Optional for diagnosis; never needed for logical replay or used to recreate user intent |

**Proposed placement for decision:** one owned, gitignored resource folder such
as `.agent-worktrees-recovery/` inside each actual worktree, plus a repository
recovery anchor under its Git common administrative directory for facts that
must outlive worktree removal. Non-Git resources need their owner's explicit
durable data root, not an install slot. Bootstrap locators live in surviving
configuration; they cannot be found only by querying the deleted index.
Exact names, worktree-marker exclusion handling and cross-owner adapters require
review before implementation. Existing user files at the selected path cause a
scoped conflict; use a reviewed alternative destination, never overwrite them.

No promise extends to loss of the actual Git objects, conversation bytes,
credential authority or the sole unrecorded semantic decision. Doctor reports
that precise missing anchor rather than silently reconstructing fiction.

## Identity and compatibility envelope

Keep four independent fields: stable **resource ID**, accepted **writer
incarnation**, **record revision**, and **schema/operation capabilities**.
Software package version is diagnostic and selection data, not an election.

- A deliberately selected older runtime with supported operation semantics is
  current. Newer additive optional fields survive read-modify-write unchanged,
  including nested unknown fields. Unknown observations may be displayed as
  such; an unknown required semantic feature refuses only the affected mutation.
- A newer process explicitly replaced by an accepted ownership transition is
  revoked even if its schema is newest. Check incarnation, exact resource and
  expected revision at the write/stop/delete effect, under existing custody.
- Loss of a projection must not reset authority to a reusable generation zero.
  Persist replacement/tombstone evidence outside that failure domain; if
  surviving authority is incomplete or another writer cannot be fenced, defer.
  New projection epochs do not themselves revoke an otherwise current writer.
- Supported old code that cannot preserve required semantics cannot write that
  record format. Retain a compatible representation or independent sidecar,
  expand readers first, or refuse the specific operation with a doctor route.
  Never claim an unmodified old binary gained capabilities by changing metadata.
- #1460 remains the one Bridge foundation for negotiation, pinned session
  semantics, durable schemas and operating-generation transitions. #1915
  evidence is a dependency, not satisfied by this plan.

Proposed typed result envelope: `resource_id`, `operation`, `reason`,
`disposition`, `evidence_revision`, `recovery_completeness`, `next_action`.
Reasons include `projection_missing`, `anchor_missing`, `identity_ambiguous`,
`live_owner_conflict`, `writer_revoked`, `revision_conflict`,
`required_capability_unsupported`, `malformed_durable_record`,
`effect_outcome_uncertain` and `source_unavailable`.

`disposition` distinguishes **refusal** (effect not admitted), **deferred**
(live/uncertain effect must wait), **warning** (usable but incomplete/freshness
degraded), and **optional_omitted** (for example unsafe optional attribution).
A transport timeout after submission is not a refusal or proof of no effect.
Do not map every diagnostic to a global exit failure or silently convert a
required refusal into a warning. Preserve current caller exit-code contracts
until versioned adaptation and tests explicitly change them.

## Transition and recovery protocol

_(Agent-recommended transaction mechanics; not a command to execute.)_

1. **Preview:** enumerate only the selected scope from configured roots, Git,
   original session records and configured provider APIs. Report durable versus
   derived paths, missing anchors, identities, unknown files, live owners and
   projected writes. Default to no mutations, no service starts, no broad
   filesystem scan, no credential payload in output.
2. **Prepare durable intent:** allocate a stable operation ID before any external
   mutation; record exact target, expected revision, accepted incarnation and
   capability envelope in the owner-managed durable journal. Commit/flush that
   record before the effect. The journal is not an ordinary observation log.
3. **Admit and execute once:** under current resource custody, revalidate target,
   authority and revision. Use provider-native idempotency/correlation or atomic
   create+marker when available. Record result/reference and accepted semantic
   transition durably before acknowledging it as recoverable.
4. **Handle the acknowledgement gap:** after effect but before durable result,
   retain `uncertain`; query by operation identity through the owning provider.
   If correlation is unavailable or ambiguous, require explicit reconciliation,
   never repeat create/delete. After a durable result but lost response, retry
   returns that result without a second effect. If save/history were separate,
   the owner's transactional outbox or equivalent recovery record must bridge
   them; best-effort post-save history alone is insufficient.
5. **Reconstruct:** ingest complete supported records, dedupe operation IDs,
   reduce per-resource revisions/lineage, and retain truncated-tail/conflict
   evidence. Transfer/release/terminal tombstones outrank stale markers under
   that resource's recorded ordering; clocks and global append order do not.
   Refresh live observations. Historical PIDs, leases and sessions are not
   resurrected as current authority.
6. **Build separately:** render a compatible inactive projection generation.
   Validate semantic equality to the surviving authorities and durable facts,
   including pending/uncertain outcomes. Never use a hidden old central DB copy
   as the sole source for this proof. Keep originals and unknown files intact.
7. **Quiesce/fence or defer:** before publication use the existing owner's
   transition/custody primitive, freeze affected mutations and drain accepted
   work. A remote/live owner not safely controllable within authorized scope
   makes publication deferred; do not contact existing agents or acquire their
   sessions to make the test pass.
8. **Health gate and select:** verify candidate reader/writer contracts and
   current revisions, then atomically select the derived generation through
   the owning layer. A failed health check leaves the old view selected.
   Installer/runtime selection remains the installer's separate authority.
9. **Recover interrupted publication:** persist the selected transition state
   outside the discarded projections. Re-enter the same operation ID, inspect
   selection and committed outcomes, and finish or retain a safe previous view.
   Rollback changes the selected compatible projection/runtime, not the fact
   journal or accepted external effects; no effect compensation by blind replay.
10. **Retain then compact:** preserve original records through a verified
    migration/rollback window. Compact only to checked per-resource snapshots
    plus sufficient tombstones/outcome IDs. Cleanup is a separate explicit,
    exact-target owner operation; the rebuild does not delete unknown files.

**Force-rebuild alternative:** only a separately reviewed and expressly
confirmed rebuild-and-publish mode could supersede obsolete *derived*
provenance. It cannot override provider permissions, unknown durable semantics,
live custody, unresolved effects or user edits. No implementation/default flag,
reset invocation or shared installation action is included in this proposal.

## Staged PR ownership and dependencies

| Slice | Proposed files / symbols | Prerequisites and stop condition |
|---|---|---|
| G1 — Facts and envelope | New Worktrees `recovery_facts.py`, `recovery_markers.py`, `recovery_types.py`; fixtures beside `test_record_read.py` / `test_session_projection.py` | Admission + anchor choice; reader-only parsing/preservation first. Stop with reviewed format and isolated durability tests, no live marker backfill |
| G2 — Acknowledgement completeness | Existing `tracking_claim_write.apply_claim_*`, owner-approved record persistence seam; new `recovery_transactions.py`; later `tracking_claims` and `claim_handoffs` owner adapters | G1 + #3761 transaction-owner agreement. Stage claim mutation before create/transfer/finalize integrations. Stop until each adapter proves its accepted and uncertain outcomes survive loss |
| G3 — Report-first recovery | New `recovery_inventory.py` / `recovery_doctor.py`; adapt `record_read` and `session_projection.validate_restored_hint`; use `tracking_yaml_repair` report pattern, not its specialized repair body | G1; schema readers precede writers. Coordinate Git adoption interface with #7974 without editing `worktree_identity`, `context_cli` or `manager_launch_cli` in this lane |
| G4 — Projection reconstruction | New `recovery_projection.py`; `record_cache` invalidation; `finalize_obligations` completeness adapter | G2/G3 + status-owner interface #7936/#7937/#7946. Do not rewrite feed/receipt producers. Loss tests only once their manifest declares the selected stores disposable |
| G5 — Component compatibility | #1460 owns `db_adoption`/`db_schema`; container owner owns `lease._read_leases`/`_write_leases`; installer owner owns runtime-slot adoption | #1915 and each component's fresh claims. Bridge hot adoption stays strict until qualified. No competing schemas, installer implementations, or existing-owner takeover |
| G6 — Scoped doctor publication | Owner-specific doctor entrypoints and tests; installer adapter consumes existing custody, health/select/rollback | G4/G5 + separately accepted force-mode decision if any. Stop at isolated native qualification; live deployment and shared rollout remain separately authorized |

G3 can develop against G1 read fixtures while G2 is reviewed. G4 cannot precede
G2's durable acknowledgement contract. Component owners may implement independent
G5 reader work, but no new-format/default writer is enabled until all affected
supported readers and rollback paths pass. Interfaces to other campaign lanes
go through the campaign admission owner, not existing agent conversations.

## Decisions before implementation

1. Accept or revise the resource-local folder and surviving repository/non-Git
   durable-anchor placement. Include backup/repository relocation semantics.
2. Name the supported rollback reader/writer floor and capability evolution
   policy through #1460/#1915; package recency alone is never that policy.
3. Select first semantic adapter (recommended: local claim add/release/settle)
   and agree transaction ownership with #3761 before touching its code.
4. Decide whether force rebuild is needed at all. Default recommendation:
   deliver preview, ordinary adoption and non-destructive projection rebuilding
   first; leave force mode absent until those results justify it.
