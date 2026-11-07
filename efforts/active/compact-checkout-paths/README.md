# Compact Checkout Paths

- **Slug:** `compact-checkout-paths`
- **Repo:** copilot-extensions
- **Branch(es):** independent proposal and implementation PRs targeting `dev`
- **Created:** 2026-10-23
- **Status:** Draft
- **Vision:** [Repository and worktree identity](../../../visions/plugins/agent-worktrees/README.md#repository-and-worktree-identity)
- **Umbrella issue:** #8172

## Guiding Intent

Allow a new ordinary managed worktree to use a compact physical checkout path
without shortening or replacing its canonical identity. This restores path
budget for tools with deep dependency trees while preserving worktree custody,
recovery and source-control completion.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| implementation owner | Proposal, implementation and focused validation | Source worktree `dusty-anvil`; issue #8172 |
| repository maintainers | Independent review and publication acceptance | Normal repository PR flow |

## Coordination

- **Topology:** a proposal PR precedes a separate implementation PR.
- **Host (owns PRs):** implementation owner.
- **Delegates:** none.
- **Handoff:** preserve the issue and effort obligations until completed or
  explicitly transferred. Source merge, release and installed rollout are
  distinct states; this effort does not implicitly authorize shared rollout.

## Context

The current allocation source constructs ordinary checkout paths as
`worktrees_dir / worktree_id`, and admission validates that equality
(`creation_allocation.py`). Git-directory inference also derives identity
from a directory basename (`worktree_identity.py`). Changing only the
allocation string would therefore be insufficient.

This implements the existing vision's distinction between stable identity
and machine-dependent paths. It retains the existing allocation, tracking,
execution-identity, claims, pairing and cleanup owners; no second registry
or alternative unmanaged workspace lifecycle is proposed.

## Request

Public-safe summary of the authorized scope: extend agent-worktrees to support
short physical paths for ordinary task worktrees through its source and normal
publication flow. Preserve canonical identity and responsibility. Shared
runtime rollout remains a separate authorized operation.

Private motivating details are omitted; they are not required to implement or
validate this general capability.

## Plan

### Phase 1 - Reviewed contract

- [ ] Land this generic proposal through the normal review and acceptance gate.
- [ ] Confirm the allocation, replay, identity inference and path-consumer
  contracts before implementation.

### Phase 2 - Compact ordinary-worktree layout

All mechanism choices in this phase are agent-recommended.

- [ ] Add an explicit opt-in compact layout for newly created ordinary
  worktrees; preserve the current default and existing recorded paths.
- [ ] Carry the chosen path through admission, durable allocation replay,
  registration and paired-worktree creation without changing canonical IDs,
  branches, execution identity or resource obligations.
- [ ] Make invocation and recovery resolve canonical identity from the owned
  path mapping, including real Git administrative directory names that differ
  from the canonical ID; refuse ambiguous mapping.
- [ ] Keep source-control publication, repair, claims and finalize on the
  recorded physical path rather than reconstructing the legacy layout.
- [ ] Document the supported option and compatibility behavior, and add the
  required plugin changefile.

### Phase 3 - Qualification and publication

- [ ] Run focused allocation/replay/identity/consumer regression tests.
- [ ] Exercise the real native CLI against an isolated synthetic repository:
  create, invoke from the compact checkout, resolve, repair/replay and finalize.
- [ ] Land the implementation through required CI, review and Maintainer
  acceptance; distinguish merged source from promoted release.
- [ ] Record rollout as separately authorized and verified, or transfer it
  explicitly to a named tracked objective without claiming it is deployed.

## Validation Plan

- [ ] Default creation and existing worktrees retain legacy IDs and paths.
- [ ] Compact paths provide a measured path-length reduction, with canonical
  IDs and branches unchanged and distinct allocations remaining distinct.
- [ ] Invalid layout, path escape, collision and ambiguous identity fail
  without adopting another worktree or leaving an unowned allocation.
- [ ] Retry/replay and creation-tail repair reuse the admitted physical path;
  changed configuration cannot redirect a previously admitted allocation.
- [ ] Actual Git/CWD inference, launch and lookup resolve the same canonical
  record instead of registering a duplicate under the compact basename.
- [ ] Paired worktrees retain reciprocal links and claims; publication and
  cleanup act only on the correct recorded paths and preserve unrelated work.
- [ ] Focused unit tests and a native synthetic end-to-end run exercise real
  consumers. Record unavailable platform/venue lanes honestly; do not count
  mocks as native or installed-rollout evidence.
- [ ] Review documentation impact and the repeated-work/performance impact:
  no new tree scan or process per lookup without a justified bounded contract.
- [ ] Required review, CI and Maintainer acceptance pass before publication
  is described as merged; promotion and installed rollout remain explicit.

## Proposal

Use an opt-in compact physical basename while retaining the canonical ID and
the existing authoritative path mapping. Exact option/schema and compact-name
construction will be selected after tracing allocation and replay contracts.
No live worktree move, machine-identity rewrite, system-workspace substitution,
installed-code patch or default layout change is proposed.

## Journal

### 2026-10-23 - Proposal

- Created the generic upstream effort for #8172 before runtime implementation.
- Source inspection identified allocation admission and Git-basename inference
  as contracts that must change together, not a directory-string-only fix.
- Plan review and publication acceptance are pending. No runtime source or
  installed runtime has been changed.

Prepared with AI assistance.
