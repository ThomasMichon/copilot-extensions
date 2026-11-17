# Configured Worktree Base Preparation

## Goal

**Kind:** completion. Prepare a new worktree from its explicitly refreshed
configured default branch without fetching unrelated branch refs, while
preserving the existing safe-anchor and honest offline-fallback behavior.

**Acceptance:** a fresh local-Git consumer proves exact configured-base
selection even when the ordinary remote refspec excludes that branch,
unrelated refs remain untouched, and existing preservation/failure controls
pass. The finite source correction clears required typing, tests, review and
CI and actually merges; canonical acceptance and its issue claim are settled.

**Finalization criterion:** every Plan/Validation item below is accepted or
explicitly transferred, the finite issue is resolved and its claim completed,
and the source worktree's independent obligations are preserved. A plan merge
is not implementation completion, and source acceptance is not rollout.

**Now:** review this proposal before implementation.
**Return:** implement only the accepted finite scope, then record its actual
source result and return to the contributor's independent remaining work.

- **Slug:** `worktree-base-preparation`
- **Repo:** copilot-extensions
- **Branch(es):** separate managed plan and implementation PRs against `dev`
- **Created:** 2026-11-17
- **Status:** Draft
- **Vision:** below-altitude correction consistent with
  [repository/worktree identity](../../../visions/plugins/agent-worktrees/README.md#repository-and-worktree-identity)
- **Umbrella issue:** #13013

## Guiding Intent

The configured creation base should determine this operation's fetch scope.
Reuse the existing explicit-branch fetch owner rather than introducing a
timeout knob, retry mechanism, service or second freshness policy.

## Participants

| Participant | Role in this effort | Reached via |
|---|---|---|
| `patient-knob` | Finite planning, source delivery and publication owner | Assigned managed source worktree and #13013 |

## Coordination

- **Topology:** one owner, one managed source worktree, sequential plan/code PRs.
- **Host (owns PRs):** `patient-knob`; original issue/claim publisher ThomasMichon.
- **Delegates:** none.
- **Handoff:** preserve this finite gate and claim through native continuation;
  no independent contributor's work transfers through this plan.
- The [discussion-only reservation](https://github.com/ThomasMichon/copilot-extensions/issues/13013#issuecomment-6937621536)
  remains active until its original publisher records completion or release.
  No native queue reservation is claimed.

## Context

`worktree_creation._prepare_worktree_source` delegates to
`git_ops.prepare_worktree_base`. The latter currently calls `fetch` without
the configured default branch, although `fetch(branch=...)` already supports
an explicit remote-tracking refspec that overrides an excluding ordinary
remote fetch configuration.

#4093 accepted reporting failed-fetch/last-known freshness while retaining
offline creation. Do not silently replace that minimum-warning alternative
with a new fail-closed policy. #4303 concerns an existing requested branch's
resume, not new default-base preparation.

The current contributor changes in #7178 concern failed-create branch cleanup
and a start-point docstring. #11828 changes encoded URL resolution and installer
recovery. Their inspected runtime hunks do not change this fetch call.
Preserve their implementation and shared tests; reconcile actual drift before
publication rather than pre-patching or replacing either PR.

## Request

Agent-derived finite request, captured publicly in #13013:

> Refresh the configured remote/default branch explicitly for new-worktree base preparation. Select that fetched head through the existing start-point owner without refreshing unrelated branch refs merely for this operation.
>
> Preserve the accepted warning/offline fallback from #4093, safe eligible-anchor advancement, local-work protection, authentication and the existing network deadline. No new CLI, schema, timeout policy or dependency is needed.

## Scope Approvals

Source-only planning, focused synthetic validation and the normal contribution
flow are in scope. This plan grants no installed rollout, service intervention,
live repository experiment, cleanup, credential change or other-owner takeover.
Existing-branch resume, finalize/GC fetch policy and cross-machine allocation
custody remain independent.

## Plan

### Phase 1 - Reviewed contract

- [ ] Land this finite proposal through the repository's actual review/CI gate.
- [ ] Reconcile the current default-base helper and independent contributor
  hunks without reopening their scopes or changing original acceptance.

### Phase 2 - Source delivery and finite acceptance

- [ ] Add a few fail-first, fully typed controls using disposable local Git.
- [ ] Pass `branch=default_branch` at the existing preparation fetch call;
  preserve all subsequent start-point and safe-anchor decisions.
- [ ] Satisfy whole-file strict changed-code checks without suppressions or
  pending-project admission. Limit necessary typing corrections to unchanged
  behavior; reconcile any material contributor overlap before proceeding.
- [ ] Pass affected tests, lint, install contract, changefile/version/size and
  exact-head required review/CI, then actually merge the source correction.
- [ ] Record the finite accepted result, settle #13013 and its original claim,
  and preserve every independent workspace and deployment obligation.

## Validation Plan

- [ ] **Exact base, fail first:** use a nonstandard configured default branch
  omitted by the ordinary fetch refspec; advance it remotely and prove the real
  helper refreshes/selects its exact head.
- [ ] **Ref scope:** advance an unrelated branch too and prove this operation
  does not refresh its remote-tracking ref.
- [ ] **Preservation:** retain eligible-anchor fast-forward and existing
  dirty/ahead/diverged/detached/non-default/no-remote controls.
- [ ] **Offline reporting:** the actual preparation caller reports the literal
  fetch failure and selected last-known base, preserving the existing fallback.
- [ ] **Fresh consumer:** local bare repositories and isolated Git configuration
  exercise actual subprocess/ref behavior without network credentials, operator
  state, installed runtimes or product checkouts.
- [ ] **Publication:** whole-file typing and all applicable exact-source gates
  pass; genuine final-head review and actual merge precede acceptance.
- [ ] **Evidence tiers:** document why the local-Git consumer is the relevant
  end-to-end/first-touch proof; use genuinely available supported lanes without
  treating source/CI evidence as installed or live-venue qualification.

## Proposal

Reuse `fetch(branch=...)` inside its existing default-base caller. Keep the
network bound and literal failure path unchanged. Introduce no public API or
cross-runtime contract. The architecture-pattern principles of right-sized
surfaces, cross-platform parity and truthful failure apply; this below-altitude
correction does not revise a vision or create a new service/lifecycle owner.

## Journal

### 2026-11-17 - Proposed finite correction

- Read the current creation helper/caller, #4093/#4303 discussions and actual
  independent contributor hunks. Public #13013 and its finite reservation are
  published and audited. No implementation or installed operation has begun.
