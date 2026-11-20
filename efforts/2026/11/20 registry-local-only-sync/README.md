# Registry Local-Only Sync

## Goal

> Treat registered local-only repositories as skipped during registry synchronization instead of failing the entire operation.

Resolve only the ordinary local-only result retained in #4173 A5 /
4228-P1/P2. Truthful per-entry aggregation is part of the outcome; the
existing loop does not necessarily abort on one error.

- **Slug:** `registry-local-only-sync`
- **Repo:** copilot-extensions
- **Branch(es):** independent reviewed plan, implementation and acceptance PRs
- **Created:** 2026-11-19
- **Status:** Done
- **Vision:** [Repository identity and ownership](../../../../visions/plugins/agent-worktrees/README.md#repository-and-worktree-identity)
- **Umbrella issue:** #4173; original source #4228

## Guiding Intent

Keep an intentionally local repository useful in the registered inventory
without requiring a remote or disguising a real synchronization failure.
Use the existing registry synchronizer, result states and CLI consumer.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| `patient-knob` | Finite design, source, validation and PR delivery | Original reservation in #4173 |
| Existing project-context owners | All other model, selection, Configuration, Vault and topology work | Their retained #4173/#4205/#7689 contracts |

## Coordination

- **Topology:** one finite registry-sync source lane, sequential reviewed PRs.
- **Host (owns PRs):** `patient-knob`; no delegates.
- **Handoff:** same-owner continuation retains the original reservation and
  unfinished acceptance; no other owner's work or native claim transfers.
- The [original reservation](https://github.com/ThomasMichon/copilot-extensions/issues/4173#issuecomment-7025401600)
  covers only A5 / 4228-P1/P2 and its paired validation.
- Keep #4173 open. #4228's administrative retirement was not product
  completion. Neither whole-model migration nor nine-slug selection depends
  on finishing this finite lane, or is completed by it.

## Context

The proposal baseline dispatched bare anchors separately and skipped dirty
or detached checkouts. Its ordinary path could switch to the declared
branch and call the account-aware `git_ops.fetch("origin")` without first
classifying a repository with no remotes. The resulting Git failure was
reported as `error`. `sync_all()` already collected each entry's outcome,
and `repos_cli` already rendered `skipped` distinctly.

The current Repositories Configuration consumer separately composes
registration, adoption, related routes, binding and writable-pair facts.
This fix changes none of those authorities or stores. A joined view is
not proof that the authoritative-model decision has been made.

## Request

The Goal quotes #4228's complete original public request. Its accepted
receiving contract is #4173's 4228-P1/P2, A5 and corresponding V4 facet:
preserve real authentication, transport, timeout and repository failures,
existing skips and truthful aggregate results, without adding a remote.

## Scope Approvals

Source design, fixture-owned tests and normal review/CI/publication only.
No live registered-repository sync, configuration/adoption change, remote
provider operation, runtime rollout, service change or cleanup is included.
The parent, per-fact/consumer owners, bounded Vault claimant, topology
candidate and all existing resource/native obligations remain unchanged.

## Plan

- [x] **P1:** Land this finite design through normal planning review before
  source implementation.
- [x] **P2:** After the existing bare/dirty/detached guards, inspect the
  checkout's configured remote names through the existing isolated local
  Git helper. Only a successful empty result means
  `skipped: local-only repository (no remotes configured)`.
  Make that decision before branch checkout, fetch or merge. Registry
  metadata alone is not evidence that the actual checkout has no remotes.
- [x] **P3:** A nonzero/failed/timed-out remote observation remains an
  error. Any configured remote, including a non-`origin` remote, follows
  the existing origin-based synchronization behavior; do not guess a
  replacement, swallow a fetch error or change authentication.
- [x] **P4:** Preserve result shape, selectors, aggregate collection,
  bare/dirty/detached/diverged handling and declared-branch repair for
  remote-backed checkouts. Update the actual registry command reference
  and add the required plugin changefile.
- [x] **P5:** Publish qualified source evidence, satisfy current-head
  review and required CI, actually merge, then record finite canonical
  acceptance and complete only the original discussion reservation.

## Validation Plan

- [x] **V1:** Fail-first ordinary no-remote controls exercise both matching
  and differing declared branches. Prove an intentional skip without
  branch/config/index/content change or a fetch/merge attempt.
- [x] **V2:** Cover failed and timed-out remote observation, configured
  non-origin remotes and genuine fetch failures; preserve literal error
  outcomes and the existing account-aware fetch path.
- [x] **V3:** Exercise actual `sync_all()` and the CLI with fixture-owned
  local-only, valid local-remote and failing local-remote entries. Preserve
  per-entry/aggregate results, no-remote state and unrelated repositories;
  run existing bare/dirty/detached/diverged and branch-repair controls.
- [x] **V4:** Run focused contained tests, touched-file lint and declared
  whole-file typing, relevant documentation/install-contract checks and
  actual required CI. Real Git fixtures are optional integration evidence,
  not new unit-portfolio admission. This is not an installer or first-host
  change; no live external target is needed or authorized.
- [x] **V5:** Reconcile accepted source against this entire finite Plan and
  Validation Plan, verify #4173 remains open, and retain every unrelated
  parent/owner/native/release/installed gate.

## Proposal

This is a below-altitude behavior correction within the existing repository
identity owner, not a new registry or architectural model. It follows
the patterns' single owner, cross-platform parity and truthful-error
principles. The CLI grammar and `(state, detail)` contract are unchanged;
an existing `skipped` outcome replaces only the confirmed no-remote error.
One bounded local Git read is added on the ordinary eligible path, before
the existing effectful work; no network, tree walk, daemon or startup work
is added. Existing remote timeouts and credential selection are unchanged.

## Journal

### 2026-11-20 - Finite source accepted

Implementation #13554 actually merged at `2026-11-20T00:52:22Z` as
`f2e033fb06d9fda599f46e97352f0abbe9ab5398`. Genuine current-head
approval5985820821 on `00876e94b6b12daaa6fbb3ec6a0d6656c2a45ffa`
reports no findings; the complete inline collection is empty. Refreshed
required CI44648180757, lockdown/source-attribution44648204241 and the
separate identifier check pass. Native pull-forward succeeds.

Four old-source controls fail first. Twenty-six focused original/new sync
controls pass; both final complete test files pass149 cases locally, with
no failures/skips. Disposable Windows run44647160873 qualifies the exact
published source with149 passes, no failures/skips and all three phases
successful in65.375 seconds. This exercises the real fixture-owned
registry/CLI and preserves existing bare/dirty/detached/divergence,
branch-repair and account-aware-fetch behavior.

The old unreachable-branch fixture is explicitly remote-backed. Its
whole-file strict ratchet requires signature annotations, typed callbacks
and non-None assertions; the coupled account-projection helper is annotated
without changing behavior. All existing test string constants remain;
assertions increase236 to246. F/E9, final committed module size,13-plugin
install contract and25-plugin documentation consistency pass.

Exact local checker preparation remains unqualified because of selected-feed
publication metadata and a canonical-index TLS failure; no dependency lock,
age policy, admission or ignore is changed. The retained build-lock checker
is diagnostic only. Actual CI44647036606 independently prepares the original
pinned Worktrees lock and passes all four complete physical files on both
Python3.10 analysis targets; refreshed CI passes too. Local ARM64 dependency
preparation fails without Rust; selecting existing x64 Python only for the
test invocation resolves that prerequisite without a global toolchain change.

The complete current #4173 body/all four prior comments are re-read.
Only 4228-P1/P2/V1/V2 and A5 are accepted; exact whole-body comparison
preserves every other byte apart from line endings. Global V4 stays
unchecked for its independent shadow-state facet. Original-author
[acceptance and reservation completion](https://github.com/ThomasMichon/copilot-extensions/issues/4173#issuecomment-7026927678)
settles only claim `18725b968a304ddab8752f736f25e039`. Full five-comment
readback preserves the four earlier comments and verifies the exact
terminal body and author. The parent is confirmed Open.

All five Plan and five Validation items are resolved for this finite source
effort. The authoritative model, selector/Configuration family, Vault
claimant, topology source/native owners and every unrelated resource duty
remain with their existing owners. No live registered-repository sync,
productive runtime installation, service operation or cleanup occurs.
Release and installed adoption are unobserved, not inferred from merge.

### 2026-11-19 - Reviewed plan accepted

Planning #13543 actually merged as `88ad885c362a8bb2b934a9e0a4028465d78f2abe`
after current-head approval and required CI. The added Active effort index
entry makes this bounded lane discoverable; its review thread is resolved.
The source worktree is pulled forward onto that accepted plan. Only P1 is
complete; implementation, validation and finite canonical acceptance remain.

### 2026-11-19 - Finite proposal

Read the complete current #4173 body and all three existing comments, the
original #4228 body/all three comments, and the retained #4205/#5299/#7689
source records and discussions. The full Configuration workbench's
applicable owner map and actual Configuration/router/sync consumers
remain separate. No competing A5 implementation or active A5 claim was
found in the bounded current PR/discussion check. The original publisher
verified the finite reservation and full post-publication race readback.
