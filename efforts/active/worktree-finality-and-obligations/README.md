# Worktree Finality and Obligations

- **Slug:** `worktree-finality-and-obligations`
- **Repo:** copilot-extensions
- **Branch(es):** reviewed plan PR, followed by serial implementation PRs
- **Created:** 2026-08-28
- **Status:** Draft
- **Vision:** `visions/agent-fabric` - `legible-live-state`,
  `resource-claims`, `resource-accountability`,
  `disposition-is-asserted-pulse-is-derived`, and
  `claimed-resource-not-reclaimed`; `visions/picker` -
  `render-derive-not-own`
- **Umbrella issue:** [#1312](https://github.com/ThomasMichon/copilot-extensions/issues/1312)
- **Authorship:** AI-assisted; reviewed and directed by the repository owner.

## Guiding Intent

Make worktree finality an exact, inspectable proof rather than a loose synonym
for a tracking status or a Git milestone. A retained finalized worktree remains
resumable: new work or a newly held obligation reopens it automatically, while
pure settlement and release remain valid close-out actions. A worktree renders
`FINAL` and becomes prune-eligible only when all work is verified upstream, no
claim remains held, no follow-up obligation remains open, and no other
definitive cleanup blocker applies.

Represent follow-ups as itemized worktree-local obligations. They may point to
resource claims, dispatch tasks, issues, pull requests, files, efforts, or other
durable objectives, but they do not duplicate ownership from the subsystem that
owns the referenced object. Preserve the existing boolean as a derived
compatibility field.

Produce one versioned, faceted status descriptor from ground-layer truth and
make list JSON, the mux status segment, the Picker, cleanup policy, legends,
filters, and guidance consume it. A state label, glyph, color, count, or cleanup
decision must not be re-derived independently by each surface.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Host worktree | Owns the effort, implementation PRs, integration, and release | Current agent-worktrees session |
| Copilot reviewer | Reviews each public PR and reports non-blocking findings | GitHub pull-request review |

## Coordination

- **Topology:** one host worktree; serial PRs against `main`.
- **Host (owns PRs):** the current worktree.
- **Delegates:** read-only exploration and review agents only; no independent
  write branches.
- **Handoff:** every implementation slice lands through the host worktree and
  updates this effort at phase boundaries.

## Context

The existing architecture already contains most of the intended semantics, but
its implementation and presentation are split:

- `ResourceClaim` distinguishes active, at-rest, and released resources, and
  finalization already checks unresolved obligations.
- `add_resource_claim()` rejects finalized owners as frozen, even though
  lifecycle documentation says finalized worktrees are resumable.
- claim release/remove paths do not share one centralized mutation policy.
- `follow_up` is one boolean plus a summary, so cleanup cannot receive an exact
  list of what remains.
- tracking status, Git classification, prune assessment, mux labels, Picker
  labels, glyphs, colors, legends, and fallback mappings are separate sources
  of truth.
- a `finalized` tracking override can render `FINAL` while claims, follow-ups,
  or an open pull request still block cleanup.
- stale conduct guidance still says not to resume work after finalizing.

This effort closes existing vision intent. It does not revise the ownership
boundary: agent-worktrees owns worktree-local outbound resource claims and the
local obligation list; agent-dispatch owns inbound task claims. Cross-references
connect those records without creating a second owner.

Detailed contracts and proposed record shapes are in
[design.md](design.md).

## Request

_Verbatim operator request; original spelling and punctuation preserved._

> Our agent-worktrees leasing system is intended to block a worktree from moving
> into finalized, prune-able state, without first dealing with and releasing
> those claims. However, there are two loose ends:
>
> 1. Something in the leasing system or guidance told agents that a finalized
> worktree can't be "un-finalized"; instead, it says the claim system for a
> finalized worktree is "locked". This isn't correct. Taking action in a
> finalized worktree should be permitted; it just claims new resources and
> dirties up the branch as normal, and now the worktree no logner tracks as
> finalized and is no longer safe to prune. Fix technical blockers an agent
> guidance to reflect this allowance>
>
> 2. We need to differentiate "FINAL" from simply meaning "all Git changes have
> merged upstream" to also handle "this worktree is free of claims". A worktree
> should only be prune-able if free of claims, AND all work is merged upstream,
> AND the worktree has no flagged follow-ups.
>
> I think we should add more phases or sub-states to worktrees, and align
> indicators across the Picker and the MUX status, so they always match exact
> test and glyphs. I think we should itemize follow-ups explcitly, requiring
> agents to specify them in a list, so the follow-up flag is more like a
> follow-up "count". Then, the cleanup-worktrees skill can receive an explicit
> list to act on. We could also spin this so that "follow-ups" are actually
> claims on files issues and the like. That could unify the systems.

## Plan

### Phase 1 - Lock the contracts with failing fixtures
- [ ] Add focused fixtures for a retained finalized record that receives new
  work, a Git-settled record with held claims, and a Git-settled record with
  multiple follow-ups.
- [ ] Assert the same expected compact token, semantic style, blocker counts,
  and prune verdict across list JSON, mux rendering, and Picker derivation.
- [ ] Add compatibility fixtures for legacy boolean-only records and active
  effort bindings.
- [ ] Add stale-snapshot concurrency fixtures proving background stamp writes
  cannot erase, resurrect, or reorder concurrently-mutated follow-ups.
- [ ] Inventory every in-repo and known downstream consumer of literal `FINAL`,
  `status == finalized`, follow-up glyphs, and cleanup buckets before changing
  their meaning, including the agent-bridge worktree projection and cockpit
  consumers.

### Phase 2 - Make finalized records resumable
- [ ] Centralize claim add/update/remove/settle/release mutations so direct list
  replacement cannot bypass transition policy.
- [ ] Keep `finalizing` and `orphaned` hard-reject states. Atomically reopen
  `finalized -> active` only when a mutation changes the held obligation set:
  a new/reopened follow-up, a new claim, `released/abandoned ->
  active/at-rest`, or an accepted inbound obligation. Materializing an
  already-effective legacy synthetic follow-up is a no-op and does not reopen.
- [ ] Leave idempotent reads, pure settlement, release, removal, and
  metadata/heartbeat refreshes free to operate without reopening.
- [ ] Preserve the finalize freeze invariant: once the record becomes
  `finalizing`, claim/follow-up acquisition remains rejected until finalize
  commits or rolls back.
- [ ] Implement finalize rollback and stale-`finalizing` recovery so a failure
  after the freeze restores a mutable stable state instead of wedging the
  worktree permanently.
- [ ] Preserve historical finalization timestamps separately from current
  lifecycle state and re-arm disposition nudges when a worktree reopens.
- [ ] Reopen output and guidance must list prior resources that were released or
  re-homed by the earlier finalize cascade; reopening the worktree does not
  restore those resources.

### Phase 3 - Replace the boolean-only follow-up model
- [ ] Add a migration-free `FollowUpRecord` list with stable IDs, summary,
  state, timestamps, typed objective references, per-item revisions/tombstones,
  and a monotonic ledger revision protected by the record merge path.
- [ ] Add explicit list/add/resolve/dismiss and offer/accept/decline transfer CLI
  operations; transfer remains source-owned until acceptance commits.
- [ ] Keep `status --follow-up --summary` as a compatibility shorthand and
  continue emitting `follow_up` as the derived open-obligation boolean.
- [ ] Treat active effort bindings and legacy `follow_up=true` records as
  effective open obligations with explicit local clear/transfer paths, without
  pretending to infer completion from another repository.

### Phase 4 - Derive canonical finality once
- [ ] Add a versioned faceted descriptor that preserves Git state, tracking
  lifecycle, held-claim count, open-follow-up count, live blockers, and cleanup
  assessment as independent facts, plus evidence provenance, freshness, and
  completeness.
- [ ] Define `FINAL` as the conjunction of clean/upstream Git state, zero held
  claims, zero open follow-ups, and no definitive cleanup blocker.
- [ ] Render Git-settled but blocked worktrees as `MERGED`, with compact claim
  and follow-up counts, rather than `FINAL`.
- [ ] Define held claims as `active | at-rest`; `released | abandoned` remain
  non-held, with abandoned claims retained as visible audit history.
- [ ] Make finalize reject active claims, then release at-rest claims under the
  finalizing freeze before committing finalized status. Give legacy/GC close-out
  an explicit preview/apply reconciliation command rather than silently
  releasing current-version claims.
- [ ] Separate completed-worktree closure from other cleanup categories:
  `FINAL` is the strict completed-and-safe proof, while UNUSED, CONVO, GONE, and
  system-record reap retain their own opt-in/action dispositions.
- [ ] Make cleanup and GC consume the descriptor's graded action disposition and
  exact blockers instead of maintaining a parallel verdict.
- [ ] Recompute refreshed, complete evidence under the record/finalization lock
  immediately before any prune/delete action; cached or fetch-free descriptors
  are never destructive authorization.

### Phase 5 - Align every presentation and guidance surface
- [ ] Make list JSON publish the canonical descriptor and compatibility fields.
- [ ] Pass the descriptor through agent-bridge's allow-list projection and any
  cockpit consumer before treating descriptor absence as a mixed-version case.
- [ ] Make mux and Picker use the descriptor's exact compact text, marker counts,
  and semantic style token; surface adapters may translate that style token to
  their native palette without redefining state.
- [ ] Keep legends, filters, maintenance previews, and cleanup selections in
  parity with the same descriptor.
- [ ] Preserve mixed-version fleet safety: absent, unsupported, or newer
  descriptor versions render provisional/review and never `FINAL` or
  prune-eligible.
- [ ] Assemble and truncate compact text in one shared function so parity is
  measured before and after the same width rule, with deterministic priority:
  base label, blocker markers, then title/detail.
- [ ] Update lifecycle, conduct, worktree, and cleanup guidance: finalized is
  resumable until pruned; follow-ups are explicit items; cleanup receives and
  reports the exact blocking list.

### Phase 6 - Release and prove the lifecycle
- [ ] Run a fleet inventory/backfill preview for legacy boolean follow-ups,
  active effort bindings, at-rest claims, and finalized records whose current
  evidence is no longer final; provide explicit triage/apply output. Automatic
  cleanup never releases at-rest claims from current-version records.
- [ ] Run the agent-worktrees suite, payload/install/version guards, and
  headless Picker render assertions.
- [ ] Exercise a live finalized -> resumed -> held claim -> settled/released ->
  final cycle.
- [ ] Publish, review, merge, deploy, and confirm Picker/mux parity on the
  installed runtime.
- [ ] Mark the effort Done only when every Plan and Validation Plan item is
  complete or transferred to a named tracked objective.

## Validation Plan

- [ ] **Reopen:** adding a new claim or follow-up to a retained finalized
  worktree succeeds, changes lifecycle state away from finalized, and
  immediately removes prune eligibility.
- [ ] **Reopen history:** reopen output identifies released claims and re-homed
  child resources that were not restored by reopening.
- [ ] **Finalize rollback:** a failure after entering `finalizing` restores a
  mutable stable state, and stale finalizing records have an explicit recovery
  path.
- [ ] **Claim-free:** active and at-rest claims both prevent `FINAL`; only
  released and abandoned claims are excluded from the held count, and abandoned
  claims remain visible in audit detail.
- [ ] **Follow-up list:** multiple open obligations produce the exact count and
  list; resolving or transferring one changes the count atomically.
- [ ] **Legacy:** a boolean-only `follow_up=true` record remains blocked and
  gains a safe explicit representation on its next mutation.
- [ ] **Ownership:** resource-claim and dispatch-task references do not transfer,
  settle, release, or complete the referenced object implicitly.
- [ ] **Git:** open/unmerged pull requests, dirty files, local-only commits, and
  unverified squash equivalence prevent `FINAL`.
- [ ] **Parity:** the same fixture produces byte-identical compact status text
  and matching semantic style metadata in list JSON, mux, and Picker.
- [ ] **Evidence parity:** the same live worktree rendered through cached,
  fetch-free, and refreshed evidence modes has consistent labels; incomplete
  evidence can only lower confidence, never promote to `FINAL`.
- [ ] **Cleanup:** cleanup/GC enumerate exact held claims and open follow-ups and
  never offer a blocked completed worktree as safe; UNUSED, CONVO, and GONE keep
  their explicit existing action categories.
- [ ] **Blocker precedence:** an UNUSED, CONVO, GONE, or system record with a
  held claim or open follow-up is `blocked`, never `opt-in` or `record-reap`.
- [ ] **Destructive freshness:** cached/fetch-free evidence never authorizes
  deletion; the immediately-preceding refreshed recomputation must still be safe.
- [ ] **Guidance:** no shipped instruction says finalized work cannot be
  resumed; every close-out path instructs the agent to list and resolve,
  transfer, settle, or release obligations before finality.
- [ ] **Regression:** existing ACTIVE, DIRTY, WIP, UNUSED, CONVO, GONE, ORPHAN,
  and UNKNOWN behavior remains stable when no closure blockers exist.
- [ ] **Concurrency:** stale background record writers preserve every concurrent
  follow-up mutation through the ledger revision merge.
- [ ] **Mixed versions:** a remote without the descriptor, or with an
  unsupported descriptor version, is provisional and never prune-safe.
- [ ] **Bridge:** agent-bridge and its cockpit preserve the descriptor and do
  not drop rows into a permanent provisional state.
- [ ] **Explained blockers:** every `blocked` or `unsafe` disposition carries at
  least one blocker from the closed code set.

## Proposal

The approved design is the faceted model in [design.md](design.md):

- lifecycle, Git settlement, resource claims, follow-ups, and liveness remain
  separate facts;
- the ground layer emits one canonical closure/display descriptor;
- `FINAL` is a strict conjunction;
- follow-ups are itemized local obligations with optional references to claims
  and tasks, not replacement owners for them.

## Journal

### 2026-08-28 - Kickoff
- Confirmed the public `agent-fabric` vision already states the required
  resource-accountability and legible-live-state intent; this effort is
  vision-closing.
- Deduplicated against the completed claim-ledger, status-core, prune-triage,
  and garbage-collection work. This effort owns their missing integration:
  resumable finalization, explicit follow-up obligations, strict claim-free
  finality, and one presentation contract.
- Filed umbrella issue
  [#1312](https://github.com/ThomasMichon/copilot-extensions/issues/1312).
- Operator confirmed the slug `worktree-finality-and-obligations`, the faceted
  descriptor model, and the worktree-local obligation ledger with external
  references.
