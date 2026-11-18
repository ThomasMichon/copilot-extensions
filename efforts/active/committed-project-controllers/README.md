# Committed Project Controllers

## Goal

> Implement and publish bare-anchor support using an isolated, pinned committed source snapshot; preserve controller custody and fail-closed behavior; do not install it

- **Slug:** `committed-project-controllers`
- **Repo:** copilot-extensions
- **Branch(es):** reviewed proposal, then separate implementation against `dev`
- **Created:** 2026-11-18
- **Status:** Draft
- **Vision:** [fresh default upstream installation](../../../visions/installer/README.md#fresh-default-upstream-installation)
- **Umbrella issue:** #10377, bounded controller source-selection slice only

## Guiding Intent

An opted-in project update must not derive its executable source from an absent
working tree when the registered repository is bare. Preserve the existing
update authority and project policy; resolve a single committed source through
the repository-policy owner and execute an isolated realization of that source.
Do not silently choose another worktree, update implementation, branch or source.

This is a finite **completion** assignment. Acceptance is reviewed source that
supports a real bare-repository controller consumer, retains normal checkout
behavior and refusal/custody contracts, passes required checks and reaches its
normal publication gate. Finalize only after the source effort is Done, all
Plan/Validation items are resolved or explicitly transferred, and its owned
PRs and disposable fixtures are settled. Installation is explicitly excluded.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Controller source driver | Proposal, implementation, qualification and source PR stewardship | Managed source `ticking-gizmo`; #10377 |
| Existing installer and update owners | Existing request, source-selection, materialization and custody contracts | Their source modules and existing efforts; no transfer |

## Coordination

- **Topology:** One source owner; proposal clears review before implementation.
- **Host (owns PRs):** Controller source driver.
- **Delegates:** None.
- **Overlap:** Do not replace another contributor's open PR or adopt the wider
  #10377 campaign. Native Manager/Worktrees qualification and unrelated
  maintenance work remain with their current owners.
- **Handoff:** This Goal, current phase, exact source/check evidence, remaining
  gates and live obligations. A proposal or feature PR is not installation.
- **Now / Return:** Review this execution-contract proposal, implement the
  accepted bounded change, then return the source result to its caller without
  running an installed update or changing the caller's acceptance/holds.

## Context

`agent_worktrees.project_update_controller._controller_path` currently resolves
the script and working directory from `repo.anchor`. Its documented contract
assumes a checkout. Separately, `inrepo_config_source` can read committed
repository policy independently of an anchor's working tree. A bare repository
can therefore select a valid controller declaration but have no executable file
at the anchor-relative path.

The existing controller seam is #10791; runtime-backend intent in #11862 and
the current participant-custody contract remain authoritative. Extend that
seam, not the updater, scheduler or installation authority. Normal checkout
behavior must remain compatible; this scope does not generally replace every
working-tree script with a committed copy.

## Request

The Goal preserves the complete public-safe selected instruction verbatim.
The operator selected pinned, isolated committed-source execution rather than
an arbitrary live-worktree fallback. Source implementation and publication are
approved; installation is not.

## Scope Approvals

- Source changes and normal reviewed publication for this bounded capability.
- Focused unit and disposable, caller-contained Git/controller consumer tests.
  Synthetic controllers must have no installer or service effects.
- No installed-runtime patch, host update, deployment, service/session restart,
  bare-anchor mutation, credential/access change or production data operation.
- No silent standalone fallback, authority substitution, new resident process,
  second updater, scheduler or maintenance owner.
- No change to another contributor's acceptance, resource custody or holds.

## Plan

The decomposition below is **agent-recommended**, not additional operator
wording or deployment authority.

### Phase 1 - Review the source-selection contract

- [ ] Reconcile the bare-anchor behavior with the installer vision and existing
  source-policy, snapshot/materialization and update-driver owners.
- [ ] Land this proposal through normal review and required CI before changing
  execution behavior.

### Phase 2 - Implement pinned bare-anchor controller execution

- [ ] Resolve the authoritative committed source once for the selected bare
  project, retaining the policy/controller association and exact commit while
  preparing execution. Preserve ordinary checkout behavior.
- [ ] Reuse the applicable repository-owned source/materialization and finite
  process-lifetime mechanisms for an isolated tracked-source snapshot. Keep
  all required local helper inputs from that pinned source; never mix a
  policy revision with an arbitrary live worktree or moving branch.
- [ ] Run only the admitted repo-relative Python controller under existing
  update-driver participant custody, argument/environment forwarding and
  normal return semantics. Keep the snapshot alive until the owned execution
  settles, then settle its owned temporary resources.
- [ ] Preserve fail-closed behavior for missing/unprovable source, changed
  policy/controller selection, unsafe paths/links, unsupported snapshots,
  failed preparation, controller failure and unconfirmed cleanup. Do not
  fall back to an anchor file, another worktree or another updater.

### Phase 3 - Qualify, publish and return

- [ ] Pass focused source/unit and actual disposable consumer checks, required
  source gates and review. Update affected docs and add the required changefile.
- [ ] Merge the implementation through the normal source flow, distinguish
  actual release availability from installation, and record omitted lanes.
- [ ] Resolve all Plan/Validation items, publish the closing record and archive
  the effort; release focus and settle the source worktree through its owner.

## Validation Plan

- [ ] Failure-first: a real bare repository with committed controller policy
  and script succeeds without creating a checkout at the anchor.
- [ ] The actual isolated controller reads a relative helper from the pinned
  commit and reports its source/cwd and forwarded flags/custody; moving the
  source ref or changing a separate working tree cannot change that execution.
- [ ] Missing refs/scripts, unsafe paths/link escapes, unavailable or changed
  selection and failed materialization refuse without running any controller,
  standalone updater or unrelated actor.
- [ ] Controller exit failure, bounded timeout/cancellation and preparation/
  execution cleanup retain truthful outcomes and exact owned custody.
- [ ] Normal checkout, explicit standalone/partial modes, platform selection
  and optional backend-intent behavior preserve their existing contracts.
- [ ] Focused tests, touched-Python Ruff, install/contract/type and required CI
  pass. Record portable/native, fresh-root and live-venue applicability and
  omissions; live installation is not an allowed validation lane.

## Proposal

Pending review. No execution behavior has been changed or installed.

## Journal

### 2026-11-18 - Source-only assignment and contract boundary

- Current source and released Main still resolve controller executables beneath
  the anchor, while committed policy selection is independent of working-tree
  contents. The declared behavior does not yet support a bare anchor.
- The operator explicitly selected isolated pinned-source support and normal
  source publication, with no installation. A scoped coordination notice on
  #10377 identifies this source owner without taking over the broader effort.
- Propose the contract extension before implementation; retain existing
  controller custody and fail-closed behavior rather than adding a live
  worktree or standalone fallback.
