# Plugin Inventory and Activation Scope

- **Slug:** `plugin-inventory-activation-scope`
- **Repo:** copilot-extensions
- **Branch(es):** isolated issue worktree
- **Created:** 2026-08-31
- **Status:** Draft
- **Vision:** plugin-services `install-adopt-boundary`
- **Umbrella issue:** #1507

## Guiding Intent

Keep plugin availability and plugin activation as independent state. Updating or
restoring an available payload must preserve the user's selected activation
scope, while repository settings may activate that payload only where the
repository is trusted.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| implementation host | Owns design, implementation, validation, and PR | isolated issue worktree |
| agent-machines schema host | Owns the shared disposition and conflict contract | agent-machines declarative-control effort |
| independent reviewers | Review the plan and implementation | pull-request review |

## Coordination

- **Topology:** one coherent implementation PR after this proposal lands.
- **Host (owns PRs):** implementation host.
- **Delegates:** bounded read-only investigations may trace independent update,
  customization, and restore surfaces.
- **Dependency:** the `ensure-absent` settings contract is a bounded Phase 3
  slice of `agent-machines-declarative-control-plane`; this proposal reviews
  that shared schema change before implementation.
- **Handoff:** findings are integrated by the implementation host; no delegate
  publishes or edits overlapping files.

## Context

Issue #1507 identifies a state-boundary bug: a plugin may be installed and
available in user inventory without being user-globally active, while a trusted
repository activates it through its own settings. The unified update flow must
refresh that payload without promoting activation. Declarative machine restore
also needs a precise way to remove selected user-level activation keys without
uninstalling inventory or deleting unrelated settings.

This effort closes the existing plugin-services vision's
`install-adopt-boundary`: install and update may refresh machine-local payload
and runtime state but must not change the user's chosen behavior. It follows the
install-vs-adopt, cross-platform parity, and config-schema patterns.

## Request

Separate installed plugin inventory from activation scope across customization
guidance, unified updates, and declarative machine restore. Preserve user
activation tri-state during payload refresh, make installed inventory an update
authority, add safe inspection and deactivation guidance, and support
dry-run-first exact removal of selected user-level `enabledPlugins` keys with
conflict detection and bootstrap protection.

## Plan

### Phase 1 - Establish the state contract

- [ ] Document the authorities for installed inventory, user-global activation,
  repository activation, trust, and update effects.
- [ ] Add a small `customizing-copilot`-owned, cross-platform inspection and
  dry-run-first mutation helper that treats missing optional state as absence,
  rejects malformed state before mutation, and preserves unrelated JSON and
  inventory.

### Phase 2 - Preserve scope during unified update

- [ ] Make source-qualified installed inventory, rather than activation alone,
  an update authority while retaining repository and user activation as
  bootstrap inputs.
- [ ] Trace every payload/runtime refresh path and preserve the pre-update user
  activation state across any operation that bootstraps inventory.
- [ ] Prove installed-but-absent, installed-but-false, installed-but-inactive,
  and active repository/user cases refresh without changing absent, false, or
  true user activation while payload and runtime bootstrap still occur.

### Phase 3 - Add declarative activation removal

- [ ] Add the reviewed schema-versioned disposition for exact map-key absence
  in user `enabledPlugins`, distinct from capture `exclude` and out-of-band
  `prune`, as the bounded agent-machines declarative-control slice.
- [ ] Validate cross-package conflicts, exact source-qualified identities, and
  fixed plus declared bootstrap floors before planning or applying.
- [ ] Report exact removals, default to dry-run, back up before apply, preserve
  unrelated settings and inventory, and make repeated apply a no-op.
- [ ] Document a synthetic adopter package without embedding any operator or
  organization-specific plugin list.

### Phase 4 - Validate and publish

- [ ] Run focused plugin suites, changed-Python ruff checks, version and install
  contract gates, and a practical clean-room scenario or equivalent hermetic
  subprocess coverage.
- [ ] Bump every touched plugin version consistently, publish through the
  required PR flow, and verify the pushed ref and PR head.

## Validation Plan

- [ ] Helper tests cover inspection, valid absent files/keys, malformed-file and
  wrong-shape errors, dry-run/apply, preservation of unrelated JSON and
  inventory, and idempotency.
- [ ] Unified-update tests cover absent, false, and true user activation,
  installed-but-inactive inventory, successful and failed inventory bootstrap,
  and unchanged payload/runtime refresh guarantees on Windows and POSIX paths.
- [ ] Restore tests cover schema validation, exact planned removals,
  dry-run/apply/backup/idempotency, malformed live state, cross-package
  conflicts, and fixed plus declared bootstrap protection.
- [ ] Documentation and examples consistently distinguish inventory,
  user-global activation, repository activation, and trust.
- [ ] Version consistency, install contract, and relevant clean-room or
  hermetic behavior checks pass before publication.

## Proposal

Treat the four plugin-state axes as separate authorities. Preserve the exact
user activation entry around any inventory-creating CLI call. Add a
schema-versioned `ensure-absent` settings disposition whose narrow contract is
deleting named `enabledPlugins` map keys, with package-union conflict detection
and bootstrap-floor rejection. Keep mutation dry-run-first and preserve all
unmanaged state.

## Journal

### 2026-08-31 - Kickoff

- Confirmed a clean isolated worktree at current `origin/main`, claimed #1507,
  and found no conflicting open pull request.
- Reconciled the work as closing plugin-services
  `install-adopt-boundary`; no standing-intent revision is required.
- Traced independent customization, unified-update, and agent-machines restore
  surfaces to produce this proposal.
