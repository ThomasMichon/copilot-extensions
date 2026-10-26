# Ordinary Call-Site Activation - Phase 3d Proposal

Parent: [Machine Alias Compatibility and Locality](README.md).
Prerequisites: [structured identity contract](physical-machine-environment-boundary.md)
and [manifest-independent context](manifest-independent-local-identity.md).
Tracking: [#5674](https://github.com/ThomasMichon/copilot-extensions/issues/5674).

## Scope and authority

The operator selected tuple-first new records, while explicitly gating ordinary
activation on replay, one-term equivalence and installed-reader acceptance.
Those installed opt-in gates are recorded in the current journal. This
**agent-recommended realization** is the separately reviewed ordinary selection
proposal, not a change to the operator's intent or authorization for rollout.

Review this proposal before implementing it. Complete #9110's qualified release
and installed guidance observation before enabling ordinary selection in source.
Actual installed activation remains a separate explicit authorization gate.
Do not infer it from a plan/source merge, an earlier corrective update, or a
successful opt-in fixture.

## Selection boundaries

Select the existing internal `tuple-v1` mode explicitly at ordinary entry points.
Keep omitted-mode behavior `legacy` in `_create_worktree_core`,
`launch_request.execute`, `_adopt_linked_worktree` and
`load_or_create_anchor_record`. Do not introduce a flag, environment override,
persistent setting, registry, package-version policy or silent fallback.

| Existing boundary | Prospective selection |
|---|---|
| `worktree_ops_cli.cmd_create` | Explicit tuple selection for ordinary session and system allocation, including keyed/Dispatch provenance |
| `resolve_cli` JSON New path | Explicit tuple selection for ordinary session/bridge creation; existing lookup and Resume retain their records |
| `resolve_launch_cli` interactive New path | Explicit tuple selection; prospective preflight/dry-run names use the same physical/environment derivation as actual creation |
| `handoff_cli` embody New path | Explicit tuple selection for a genuinely new workspace, not an existing Resume or anchor |
| `launch_request.cmd_request` | Select the mode when invoking existing daemon-owned admission; old/new retained receipts still determine replay representation |
| `worktree_identity._infer_worktree_id_from_cwd` and `session_binding_cli` registration | Select tuple mode only when adopting a previously untracked, proven linked worktree |
| `worktree_ops_cli._ensure_anchor_ledger` | Select tuple mode only for a genuinely absent anchor ledger in its actual receiving project |
| Paired knowledge creation and internal creation recursion | Carry the already-selected/admitted tuple; do not independently select a different representation or rename a retained allocation |

The existing shared local-context resolver supplies physical machine, observed
hostname, actual environment and one-term owner. Optional maps customize that
context, never enroll the current machine. Preserve exact configured keys,
binding provenance, profile conflict checks and foreign/project/namespace fences.

### Receiving anchor configuration

The existing anchor helper can receive a qualified owner from a project other
than the ambient one. Activation must not feed that project's path and name into
a factory that reloads unrelated ambient configuration. Carry its real receiving
configuration through the existing tracking factory, adding an optional internal
configuration argument if needed while preserving existing callers.

Use the existing project configuration/registration owners and locked if-absent
writer. A receiving conflict must remain an explicit diagnostic, not a swallowed
tuple-adoption failure followed by a success-shaped claim. Prove legitimate
cross-project anchor use as well as wrong-project/platform/namespace refusal;
do not broaden receiving authority to make the new mode succeed.

## Preservation boundaries

Existing records, IDs, paths, branches, owner/pair/controller/resource references,
sessions, head, lifecycle, flags, title, receipts and creation nonces retain their
bytes and meaning. An ordinary read/save or successful Resume cannot upgrade a
legacy record. The if-absent winner alone selects representation for a new anchor
or external record; a retained winner is returned unchanged.

Completed creation-key or launch-admission replay must return its original
supported allocation across the selection change. Pending/interrupted intents
remain inspect-only except for the existing explicit, provenance-checked tail
repair. No second allocation, replacement receipt, suffix reformatting, new
reservation store or implicit migration is permitted.

For a fresh name, use the selected physical machine plus the environment once.
Preserve a literal physical name ending in platform-like text; one environment
component can legitimately follow such a name. Historical repeated qualifiers
are not rename targets. Compact execution references remain distinct from
structured physical metadata.

## Ordered implementation and acceptance

- [x] Land this proposal through independent review and required source checks.
  #9114 merged after exact-head approval and passing required provider checks.
- [x] Verify #9110's natural qualification, exact promoted source and actual
  installed guidance in native/guest contexts; preserve refusal diagnostics.
  Natural `40378983487`, Main #9116 and eight actual selected-runtime guidance
  observations close this prerequisite. The current journal records the
  qualification, actual installation and preserved legacy-default boundaries.
- [x] Add failing-first ordinary-entry cases proving fresh tuple records while
  the four omitted-mode low-level APIs remain legacy.
- [x] Implement explicit selection at the inventoried boundaries and consistent
  prospective New preflight/dry-run naming, without changing existing Resume.
- [x] Complete receiving-anchor configuration propagation and visible
  refusal handling before activating that adoption boundary.
- [x] Exercise actual new-record adoption and paired flows; retain original
  legacy/tuple replay and competing if-absent winners unchanged.
- [x] Pass affected bounded tests, unchanged admitted unit-only packet, source
  guards, documentation/install-contract and independent current-head review.
  Add the ordinary Worktrees changefile; never hand-edit versions.
  #9125 merged after final-head approval, all four addressed threads resolved
  and passing required checks. The current journal records the source gates.
- [ ] Merge the source implementation, then observe natural qualification and
  exact release publication. No duplicate controller or integration promotion
  requirement is introduced.
- [ ] Obtain explicit ordinary-activation rollout authorization, use the normal
  updater and verify selected installed native/guest entry points. Preserve
  historical tracking and unrelated work; dispose only specifically authorized
  new validation resources through their ordinary lifecycle.
- [ ] Reconcile the parent Plan/Validation Plan, retaining separately owned
  guest-installation/native-repair work or explicit named transfers before closure.

## Validation Plan

- [x] Cross Windows, WSL and Linux ordinary source entries with absent, empty,
  unrelated, matching, unscoped and sibling-only maps. Validate real entry
  arguments and resulting tracking/JSON, not just a helper returning a tuple.
- [x] Prove preview and actual New use the same identity dimensions. Cover a
  configured guest shorthand, physical-plus-environment selection, custom key,
  alias, literal physical suffix and historical repeated-qualifier records.
- [x] Retain explicit ambiguity, malformed map, conflicting environment, foreign
  guest/native, receiving-project and namespace refusal before new effects.
- [x] Exercise anchor adoption from the actual receiving configuration and
  external linked-Git adoption outside the managed worktree root. Existing
  records remain byte-identical, including a competing if-absent winner.
- [x] Replay old and new completed creation/admission receipts across ordinary
  selection; preserve ID/path/branch/nonce/relationships and prevent a second
  allocation. Pending/interrupted and changed-intent cases still refuse.
- [x] Verify existing legacy and tuple Resume/lookup, paired knowledge, owner,
  controller and resource boundaries, with no physical-host-only authority.
- [x] Keep real Git, filesystem, SQLite, cross-process and installed/native
  effects in existing bounded development/acceptance lanes, not new promotion
  prerequisites or an expanded required unit node inventory.
- [ ] After separately authorized rollout, observe actual ordinary creation and
  non-preview Resume planning in every registered native/guest project. Report
  source-only hosting and missing paths separately; execute no returned agent
  command merely to make allocation acceptance appear complete.
