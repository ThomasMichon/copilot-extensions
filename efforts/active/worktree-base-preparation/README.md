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

**Now:** complete the implementation PR's exact-source unit admission,
review and required CI after successful hosted Windows qualification.
**Return:** accept only the finite source scope, then record its actual
source result and return to the contributor's independent remaining work.

- **Slug:** `worktree-base-preparation`
- **Repo:** copilot-extensions
- **Branch(es):** separate managed plan and implementation PRs against `dev`
- **Created:** 2026-11-17
- **Status:** Active
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

- [x] Land this finite proposal through the repository's actual review/CI gate.
- [x] Reconcile the current default-base helper and independent contributor
  hunks without reopening their scopes or changing original acceptance.

### Phase 2 - Source delivery and finite acceptance

- [x] Add a few fail-first, fully typed controls using disposable local Git.
- [x] Pass `branch=default_branch` at the existing preparation fetch call;
  preserve all subsequent start-point and safe-anchor decisions.
- [x] Satisfy whole-file strict changed-code checks without suppressions or
  pending-project admission. Limit necessary typing corrections to unchanged
  behavior; reconcile any material contributor overlap before proceeding.
- [ ] Pass affected tests, lint, install contract, changefile/version/size and
  exact-head required review/CI, then actually merge the source correction.
- [ ] Record the finite accepted result, settle #13013 and its original claim,
  and preserve every independent workspace and deployment obligation.

## Validation Plan

- [x] **Exact base, fail first:** use a nonstandard configured default branch
  omitted by the ordinary fetch refspec; advance it remotely and prove the real
  helper refreshes/selects its exact head.
- [x] **Ref scope:** advance an unrelated branch too and prove this operation
  does not refresh its remote-tracking ref.
- [x] **Preservation:** retain eligible-anchor fast-forward and existing
  dirty/ahead/diverged/detached/non-default/no-remote controls.
- [x] **Offline reporting:** the actual preparation caller reports the literal
  fetch failure and selected last-known base, preserving the existing fallback.
- [x] **Fresh consumer:** local bare repositories and isolated Git configuration
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

### 2026-11-17 - Finite correction validated locally

- Implementation #13033 is open, not merged. The managed publication rebased
  the candidate onto current `dev`; all seven authored files survived
  byte-for-byte unchanged.
- Disposable hosted Windows qualification passed all selected cases at
  `8b5d6154493783062a4a04cfd19812a7495964a5` in
  [run 44027139733](https://github.com/ThomasMichon/copilot-extensions/actions/runs/44027139733).
  This includes the new preparation cases, safe-anchor controls, the actual
  binary/text Git consumer and binary-observation refusals. The source,
  canonical test and runtime-verification phases all passed.
- Required CI then correctly refused the changed `git_ops.py` source against
  its old actor-free packet digest. Rebound only that existing source entry:
  the original verifier passes all 810 source bindings, with exact node
  membership, coverage targets and budgets unchanged. No source or project
  admission was widened. Required CI must execute the corrected packet.
- The new local-Git controls failed first on unchanged production code:
  the excluded configured base remained stale, and an absent tracking base
  selected the local fallback. The actual caller's offline-warning control
  passed. The correction passes `branch=default_branch` to the existing owner.
- The focused contained packet passed 52 cases, including all three new
  cases plus selected wrapper, classification and anchor-preservation controls.
  Whole-file strict checking passed all three changed Python files for both
  Linux and Windows targets. Lint, install contract and version consistency
  passed; a patch changefile is present.
- Required typing exposed the existing wrapper's text/binary union and
  dynamically assembled options. Precise overloads, typed option dictionaries
  and equivalent explicit I/O branches retain those behaviors. Unchanged
  state/result models moved to `git_state_types.py` with explicit `git_ops`
  re-exports, keeping the original module below its unchanged size ceiling.
- Fresh disposable bare repositories exercise the actual native Git subprocess
  and ref contract, providing the focused end-to-end and first-touch evidence.
  No installer/provisioning behavior changed, so a fresh-OS install scenario is
  not applicable; no installed runtime or product-venue outcome is claimed.
- Initial local preparation on Windows ARM64 could not build the optional
  authentication dependency without a Rust toolchain. The existing x64
  interpreter completed the contained packet without changing host setup.
  A supplementary local binary-consumer packet then stopped before collection
  on a cached-build-file access error. The supported disposable hosted Windows
  lane supplied that source coverage instead; setup failure is not counted as
  a test pass and containment was not relaxed.
- Source publication and hosted qualification are complete. Review, required
  CI, actual merge
  and finite issue/claim settlement remain open.

### 2026-11-17 - Plan accepted

- #13017 actually merged at04:51:42 UTC as
  `956c703a73192bbbeb8b38053f240e727f8836e0`, after genuine current-head
  Copilot approval with no findings and all actual required checks passed.
- The index-only base conflict preserved both independent effort entries.
  The plan body and selected implementation files were unchanged through
  reconciliation. #13013 stays Open for implementation and finite acceptance.
- The retained source workspace synchronized onto the accepted plan. Begin
  the real local-Git regression controls; no installed operation is admitted.

### 2026-11-17 - Proposed finite correction

- Read the current creation helper/caller, #4093/#4303 discussions and actual
  independent contributor hunks. Public #13013 and its finite reservation are
  published and audited. No implementation or installed operation has begun.
