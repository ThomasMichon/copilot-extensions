# Worktree Finality and Obligations

- **Slug:** `worktree-finality-and-obligations`
- **Repo:** copilot-extensions
- **Branch(es):** reviewed plan PR, followed by serial implementation PRs
- **Created:** 2026-08-28
- **Status:** Active
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

An agent that files an issue closely related to its current worktree's task
should proactively open a follow-up (or claim) referencing that issue rather
than letting it drift unattached — the filed bug remains this worktree's
obligation until it resolves, is explicitly dismissed as unrelated, or is
transferred, instead of silently disappearing from the worktree's picture the
moment finalize's local Git checks are otherwise clean.

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

**2026-09-13 follow-up** (verbatim):

> Next, let's check for an local efforts, visions, or tracking items related to
> a request I had a bit back to itemize "follow-up" flag items and track them
> as claims. Right now, I think a worktree has a follow-up solo flag, but I'd
> rather force a worktree agent to name the follow-up items, or just open
> claims on things like bugs and whatnot. The intent here is that if an agent
> decided to pause mid-effort, it should keep its claim on the effort, bug, etc.
> that it was doing, and the *claim* should be what blocks finalization even
> though all local changes are settled. The user must be the one to request
> finalization and release the claims. This prevents auto-cleanup of worktrees
> caught mid-stream in an effort or task series.
>
> And for the agent, we should encourage the agent to claim bugs it files
> proactively, when they are related to the task at hand.

**2026-09-14 follow-up** (verbatim):

> I had a thought: what if worktrees can claim Copilot sessions? The
> sessionStart hook, if a worktree is on CWD, should open a claim from the
> worktree to that session. Then a session should end in three ways:
> 1. The user requests finalization, which implies the user is done with the
> worktree and also the current session. Gets a little complicated when the
> user starts another turn, but that can be handled with a user-prompt-submit
> hook.
> 2. The user requests a handoff and the handoff is performed. Again, implies
> the user is done with the session. Can be re-claimed using a hook for user
> prompt submit, again.
> 3. The user manually uses /clear . Presumably, the sessionEnd hook kicks in
> for the old session, whereas /new leaves the old session dangling.
> When the user requests to finalize a worktree and there are open, claimed
> sessions besides the current one, the agent can mention this (and the
> session ids) to the user and ask if the user wants to ignore them, or sweep
> for follow-ups.
> By doing this, we can ensure that every session which gets started in a
> worktree gets tracked, gets handled in the correct order in normal course of
> business, and gets dealt with in the case of mishap.

**2026-09-15 follow-up** (verbatim, MERGED/FINAL sub-state model):

> My expected difference between MERGED and FINAL would be
> - MERGED is verified proof that the current worktree commit is part of
> origin/[main|master]'s history
> - FINAL is MERGED, plus proof that all claims downstream of the worktree are
> released
>
> MERGED should be re-computed on each git operation; ideally a post-op hook,
> and a periodic background check, would ensure the `fetch` happens and then
> compare each worktree branch's relation to [main|master], to ensure that
> status. For FINAL, we then need to audit claims, re-validating on each claim
> update whether our assertion holds.
>
> Every status in the enum represents a *computation* of individual
> sub-states:
> 1. Has turns since last checkpoint?
> 2. Worktree branch at or upstream of origin/[main|master]
> 3. Has uncommitted local changes?
> 4. Has open downstream claims?
> 5. Has pending unclaimed handoff?
>
> I'd rather you use an asterisk "*" if the state is an unverified guess,
> rather than have whole separate states for the "unverified" cases.

Follow-up direction on sequencing (verbatim):

> The agent-worktrees daemon is like the correct place to schedule the
> background git work, but that needs to be maybe once a minute. We can also
> fire it on-demand in response to certain transitions, and we should ensure
> that operations like pr-merge, git-sync, etc trigger on-demand recomputes
> globally.

Mined into `visions/plugins/agent-worktrees/README.md`'s *Derived status*
concept (PR #2734): the reduction's inputs are named as a fixed, independently
owned set of facts, freshness of upstream-containment is actively pursued by
the resident accelerator (periodic sweep + operation-triggered recompute,
repo-scoped since sibling worktrees share remote-tracking refs), and an
unconfirmed fact is marked, never given a separate whole state. See Phase 9
below for the carved implementation plan.

## Plan

### Phase 1 - Lock the contracts with failing fixtures
- [ ] Add focused fixtures for a retained finalized record that receives new
  work, a Git-settled record with held claims, and a Git-settled record with
  multiple follow-ups.
- [x] Prune-verdict slice only: `cleanup_disposition` and
  `classify_managed_worktree` now treat a held resource claim
  (`active`/`at-rest`) as a blocker even when `status == finalized` or Git is
  COMPLETED/merged (new `held-claims` bucket/reason), closing the safety gap
  PR #2592 opened (a finalized owner can now accept a new claim, but nothing
  downstream of that fix previously re-checked for one before cleanup/GC).
  The cross-surface **compact token/style/blocker-count parity** across list
  JSON, mux, and Picker (the rest of this bullet) is not done -- no unified
  descriptor exists yet; that's the remaining Phase 1/4 work.
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
- [x] Centralize claim add/update/remove/settle/release mutations so direct list
  replacement cannot bypass transition policy. Reopening now lives in one
  place: `tracking.add_resource_claim` computes whether a mutation increases
  held obligations (`_claim_reopens_owner`) and calls
  `reopen_finalized_owner`, which every claim-adding caller (`claims add`,
  future claim-handoff acceptance) already routes through. Claim-add is still
  the only ledger-mutating claim path -- `settle_resource_claim` and release
  paths were not changed since they never increase held obligations (see the
  reopen table below).
- [x] Keep `finalizing` and `orphaned` hard-reject states (unchanged). Atomically
  reopen `finalized -> active` only when a mutation changes the held obligation
  set: a new claim, or `released/abandoned -> active/at-rest` re-added via
  `add_resource_claim`. **Follow-up and accepted-inbound-obligation reopen
  triggers are NOT done** -- `FollowUpRecord` doesn't exist yet (Phase 3), and
  claim-handoff acceptance itself isn't implemented yet (`claim_handoffs.py` is
  still "Phase 1": offer/decline/cancel only, no `accept`). Materializing an
  already-effective legacy synthetic follow-up doesn't apply yet for the same
  reason.
- [x] Leave idempotent reads, pure settlement, release, removal, and
  metadata/heartbeat refreshes free to operate without reopening --
  `_claim_reopens_owner` returns `False` for a same-state replay and for
  settling `active -> at-rest` (already held, not an increase); neither of
  those code paths calls `reopen_finalized_owner`.
- [x] Preserve the finalize freeze invariant: once the record becomes
  `finalizing`, claim/follow-up acquisition remains rejected until finalize
  commits or rolls back (unchanged -- `add_resource_claim` already hard-rejects
  `finalizing`).
- [x] Implement finalize rollback and stale-`finalizing` recovery so a failure
  after the freeze restores a mutable stable state instead of wedging the
  worktree permanently. Found and fixed a **real wedge bug**: neither the
  post-freeze `lock.acquire()` `TimeoutError` path nor the outer cleanup
  `except Exception` path reverted `record.status` off `finalizing` on
  failure, so either failure permanently froze creator ownership (no path
  back, since `add_resource_claim` hard-rejects `finalizing`). Added
  `finalize._rollback_finalizing_freeze` (captures the pre-freeze status,
  reverts it on either failure path, no-ops if a concurrent process already
  resolved the record) with unit tests. **Not done:** a dedicated operator-
  facing `stale-finalizing recovery` CLI verb using lock/owner-liveness+age
  evidence for a wedge that predates this fix or crashes before either catch
  block runs (e.g. process killed mid-freeze) -- the automatic rollback only
  covers failures raised *within* `validate_and_finalize` itself.
- [x] Preserve historical finalization timestamps separately from current
  lifecycle state and re-arm disposition nudges when a worktree reopens. Added
  `WorktreeRecord.last_finalized_at` (copied from `completed_at` before it's
  cleared by the reopen) and clear `status_note_at` on reopen so a stale
  "nothing left to do" note doesn't linger.
- [ ] Reopen output and guidance must list prior resources that were released or
  re-homed by the earlier finalize cascade; reopening the worktree does not
  restore those resources. `claims add`'s CLI output now reports `reopened:
  true` and prints a one-line notice, but it does not yet enumerate the prior
  finalize's released/re-homed resources -- that needs `finalize`'s
  `release_all_resources` cascade to leave a durable trail on the record for
  a later reopen to read back.

### Phase 3 - Replace the boolean-only follow-up model
- [x] Add a migration-free `FollowUpRecord` list with stable IDs, summary,
  state, timestamps, typed objective references, per-item revisions/tombstones,
  and a monotonic ledger revision protected by the record merge path. Landed as
  `tracking.FollowUpRecord`/`FollowUpRef` + `WorktreeRecord.follow_ups`
  (YAML-round-tripped, emitted only when non-empty). Revision bumps on every
  mutation; deletion is a tombstone (state flips to
  resolved/dismissed/transferred, never a list removal) so history and
  revision continuity survive -- but the **cross-writer merge-by-highest-
  revision path itself is not implemented yet** (see the unchecked
  concurrency item below and Validation Plan's **Concurrency** row).
- [x] Add explicit list/add/resolve/dismiss ... CLI operations (`follow-ups
  [id]`, `follow-ups add <summary> [--ref kind:value]...`, `follow-ups
  resolve <id> [--result-ref <ref>]`, `follow-ups dismiss <id> --reason
  <text>`). **`offer`/`accept`/`decline` transfer is NOT implemented** --
  `claim_handoffs.py` itself only has offer/decline/cancel (no `accept`) so
  there's no existing acceptance machinery to route a follow-up transfer
  through yet.
- [x] Keep `status --follow-up --summary` as a compatibility shorthand and
  continue emitting `follow_up` as the derived open-obligation boolean. The
  CLI flag itself is unchanged; `tracking.effective_open_follow_up_count`
  is the new derivation point (itemized open/pending-transfer items, falling
  back to the legacy boolean only when the ledger is empty -- no double
  counting). `set_disposition`'s `follow_up=True` path now also reopens a
  `finalized` owner (closing a related gap: `effort-focus bind`'s automatic
  `follow_up=True` previously didn't reopen a finalized record at all).
- [x] Treat active effort bindings ... as effective open obligations. Turns
  out this was **already correct** pre-existing behavior: `effort-focus
  bind` already calls `set_disposition(follow_up=True, ...)`, which
  `effective_open_follow_up_count` picks up via the legacy-boolean fallback
  -- no new code needed beyond the reopen fix above. Legacy
  `follow_up=true` records with no itemized entries are handled the same way
  (one synthetic open item).
- [x] Add a `kind: issue` follow-up ref and guidance. `FollowUpRefKind`
  includes `issue` (plus `dispatch-task`/`pull-request`/`file`/`effort`/
  `resource-claim`/`other`); the `worktree` skill's obligation-gate section
  now explicitly tells an agent to `follow-ups add "<summary>" --ref
  issue:<repo>#<n>` for a bug it files related to the current task. The
  `file-issue` skill itself was **not** touched (cross-repo, aperture-labs-
  owned) -- that pointer is the follow-up work.

### Phase 4 - Derive canonical finality once
- [x] Add a versioned faceted descriptor that preserves Git state, tracking
  lifecycle, held-claim count, open-follow-up count, live blockers, and cleanup
  assessment as independent facts, plus evidence provenance, freshness, and
  completeness. Landed as `prune.ClosureDescriptor` /
  `assemble_closure_descriptor` (version 1): a pure function over
  already-computed facts (git `WorktreeStateInfo`, `CleanupDisposition`, held-
  claim count, open-follow-up count), never a second mutable store.
- [x] Define `FINAL` as the conjunction of clean/upstream Git state, zero held
  claims, zero open follow-ups, and no definitive cleanup blocker -- plus
  `evidence_mode == "refreshed" and evidence_complete` (a cached/fetch-free
  descriptor never reports current `FINAL`, even when otherwise qualified) and
  "not live" (`ACTIVE` has display precedence, matching design.md).
- [x] Render Git-settled but blocked worktrees as `MERGED`, with compact claim
  (`C<N>`) and follow-up (`F<N>`) markers, rather than `FINAL`. Only the git
  `completed` state gets the MERGED/FINAL treatment; every other base state
  (`ACTIVE`/`DIRTY`/`WIP`/`UNUSED`/`CONVO`/`ORPHAN`/`UNKNOWN`) keeps its own
  label regardless of blockers, per design.md's presentation rules.
- [x] Define held claims as `active | at-rest`; `released | abandoned` remain
  non-held. (Already true since Phase 1/2's `ResourceClaim.is_live`; the
  descriptor just reads that count -- abandoned-claim audit visibility is
  unchanged, pre-existing behavior.)
- [ ] Make finalize reject active claims, then release at-rest claims under the
  finalizing freeze before committing finalized status. Give legacy/GC close-out
  an explicit preview/apply reconciliation command rather than silently
  releasing current-version claims. **Not done this phase** -- `finalize.py`'s
  existing obligation gate (pre-dates this effort) already rejects active
  claims and releases at-rest ones under the freeze; the explicit
  preview/apply reconciliation command for legacy/GC close-out is unbuilt.
- [x] Separate completed-worktree closure from other cleanup categories:
  `FINAL` is the strict completed-and-safe proof, while UNUSED, CONVO, GONE, and
  system-record reap retain their own opt-in/action dispositions (unchanged --
  `_BUCKET_TO_ACTION_DISPOSITION` maps `unused`/`conversation` to `opt-in`
  distinctly from `clean` -> `safe`; GONE/record-reap are explicitly **not**
  handled by this descriptor yet, matching `cleanup_disposition`'s own
  docstring that GONE is the caller's concern).
- [ ] Make cleanup and GC consume the descriptor's graded action disposition and
  exact blockers instead of maintaining a parallel verdict. **Not done** --
  `cleanup`/`gc` still consume `CleanupDisposition` directly; only `list --json
  --classify` publishes the descriptor so far (additive, alongside the legacy
  fields). Switching cleanup/GC's own decision logic over is Phase 5 work
  (avoids two behavior changes landing in one PR).
- [x] Recompute refreshed, complete evidence under the record/finalization lock
  immediately before any prune/delete action; cached or fetch-free descriptors
  are never destructive authorization. Enforced structurally: any
  `evidence_mode != "refreshed"` (or incomplete) descriptor downgrades a would-
  be `safe` action disposition to `blocked` inside `assemble_closure_descriptor`
  itself -- a caller cannot accidentally treat stale evidence as authorization.

### Phase 5 - Align every presentation and guidance surface
- [x] Make list JSON publish the canonical descriptor and compatibility
  fields. Done in Phase 4 (additive `closure` field alongside the legacy
  `cleanup_bucket`/`state` fields, unchanged).
- [ ] Pass the descriptor through agent-bridge's allow-list projection and any
  cockpit consumer before treating descriptor absence as a mixed-version case.
  **Split status:** the agent-bridge half is done -- the worktree-discovery
  crawl now runs `list --json --mux-details --classify` (with a
  classify-specific timeout budget and an unsupported-flag fallback so an
  older/slower remote never loses discovery entirely), and
  `_WorktreeEntry`/`_parse_worktree_list` thread a raw, OPAQUE `closure` field
  (absent unless present and a dict) through to `to_dict()`. Deliberately does
  NOT interpret it (label/final-ness/action) inside agent-bridge itself -- a
  cross-machine crawl can reach an older/newer agent-worktrees runtime, so
  only a consumer that knows the current `DESCRIPTOR_VERSION` (via
  `prune.interpret_descriptor_payload`) may treat it as authoritative. **Not
  done (item stays unchecked until this lands too):** the actual cockpit
  consumer calling `interpret_descriptor_payload` on this field.
- [ ] Make mux and Picker use the descriptor's exact compact text, marker counts,
  and semantic style token; surface adapters may translate that style token to
  their native palette without redefining state. **Split status:** the
  PSMux/TMux status segment (`_render_status_segment`) is now done -- it
  calls `prune.cleanup_disposition` + `prune.assemble_closure_descriptor` on
  a record's held claims/open follow-ups and renders the descriptor's own
  `style`-keyed color (`_DESCRIPTOR_STYLE_BG`) and `compact` text
  (`C<N>`/`F<N>` markers included), instead of the old raw-state-only
  `_SEGMENT_STYLE` lookup; a fetch-free (cached) poll now correctly renders
  `MERGED` rather than `FINAL` for a COMPLETED worktree, matching design.md's
  destructive-freshness rule. **Not done:** the Textual Picker
  (`picker_tui/derive.py`/`engine.py`) still derives its own label
  independently -- its `state` column is a fixed 6-char width with an exact
  `C_STATE` dict lookup keyed on the bare label, so folding in compact
  markers needs real column-layout work (a wider column or a new one) plus
  golden/layout test updates, not just a data-source swap. That's its own
  focused slice.
- [ ] Keep legends, filters, maintenance previews, and cleanup selections in
  parity with the same descriptor. **Not done**, same reason as above.
- [x] Preserve mixed-version fleet safety: absent, unsupported, or newer
  descriptor versions render provisional/review and never `FINAL` or
  prune-eligible. Landed as `prune.interpret_descriptor_payload`: an exact
  `version == DESCRIPTOR_VERSION` match is trusted; anything else (missing,
  malformed, older, or newer) reports `supported: False`,
  `final: False`, `action_disposition: "blocked"` regardless of what the
  payload's own fields claim. Not yet CALLED by a real remote/cockpit
  consumer (there isn't one yet -- see the two unchecked items above); the
  safety net itself is built and tested ahead of that wiring.
- [ ] Assemble and truncate compact text in one shared function so parity is
  measured before and after the same width rule, with deterministic priority:
  base label, blocker markers, then title/detail. **Partially done**:
  `assemble_closure_descriptor` already assembles `label` + `C<N>`/`F<N>`
  markers in one place (base label, then blocker markers, matching the
  priority order), but does NOT yet fold in title/detail or truncate to a
  width budget -- that needs the mux/Picker wiring above to know what width
  budget applies.
- [x] Update lifecycle, conduct, worktree, and cleanup guidance: finalized is
  resumable until pruned; follow-ups are explicit items; cleanup receives and
  reports the exact blocking list.
  - Fixed the actual stale instruction the effort's own Context section named:
    `scripts/conduct/worktree-conduct.md` (the deployed postToolUse nudge
    fragment -- the "It's been N tool calls..." hint every agent sees) said
    "do not resume work after finalizing." Replaced with the correct
    "`finalized` is not terminal... resuming work afterward is normal and
    safe, and reopens the worktree automatically."
  - Fixed a real reporting gap while doing this: `cmd_cleanup`'s per-worktree
    skip-reason logic never had a branch for the `held-claims`/`follow-up`
    buckets, so a worktree blocked by either silently vanished from
    `cleanup`'s report -- neither listed as skipped nor counted in any
    summary. Extracted `_cleanup_per_item_skip_reason` (now unit-tested) and
    added both buckets to it.
  - `docs/worktree-lifecycle.md` and the `worktree` skill were already
    correct from Phases 1-3.
- [x] Update `file-issue` guidance to proactively open a follow-up/claim on any
  issue the agent files for its current task. Already done in Phase 3 (the
  `worktree` skill's obligation-gate section); the cross-repo `file-issue`
  skill itself (aperture-labs-owned) still isn't touched -- unchanged from
  the Phase 3 note.

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

### Phase 7 - Reconcile deferred backlog

- [ ] Accept finality and obligation candidates only through
  [`migration-intake`](../migration-intake/README.md)'s deduplication and
  ownership gate.
- [ ] Revalidate accepted technical scope against the current lifecycle, claim,
  and follow-up contracts; return obsolete or unsafe candidates for explicit
  disposition.
- [ ] Place each accepted public tracker item in exactly one existing phase,
  extending this plan before implementation when necessary.
- [ ] Durable end-to-end lifecycle auditability: instrument session/handoff
  cutover transitions (creation, transfer, completion, abandonment) so the
  full audit trail is traceable, closing the remaining cross-link/session-
  state trace gaps beyond what the session-claim lifecycle (Phase 8) already
  covers. Tracked in
  [#3113](https://github.com/ThomasMichon/copilot-extensions/issues/3113).
- [ ] Claim-safe terminal reclamation: finish reclaiming terminal workspaces
  with obligation-preserving release semantics -- inbound-claim release,
  multi-claim safety, and historical adoption/status surfaces -- rather than
  as a standalone reclamation slice. Tracked in
  [#3114](https://github.com/ThomasMichon/copilot-extensions/issues/3114).
- [ ] Keep fixtures synthetic and independent of any adopting worktree registry.

### Phase 8 - Session-claim lifecycle (proposed 2026-09-14; designed 2026-09-16; build started 2026-09-17)

Operator idea (see the dated Request above): a worktree should hold a claim on
every Copilot session that opens inside it, not just track `sessions:` as
descriptive metadata (the current model -- see `tracking.SessionEntry` /
`register_session`/`deregister_session`). Making the relationship a first-class
**claim** means the existing obligation machinery (held-claims blocks
cleanup/finalize, the closure descriptor's blocker set, `claims sweep`'s
never-wedge reclaim) applies to *session occupancy* the same way it already
applies to a borrowed CodeSpace or a cross-repo worktree -- closing the
`inbound-obligation` blocker code Phase 4 reserved but never wired.

Grounding (confirmed before proposing, not assumed):
- `sessionStart`/`sessionEnd` hooks already exist and already call
  `register_session`/`deregister_session` (`hooks.json`); a claim-open/claim-
  settle call would ride the same hook, not a new one.
- A command-based `userPromptSubmitted` hook genuinely exists on current
  Copilot CLI hosts (`docs/architecture.md` § the `additionalContext`
  resume-robustness note) -- its *text* output is discarded by the host, but
  it runs with real side effects, which is all a re-claim needs. No new host
  hook type is required, but `agent-worktrees` has **never registered one**
  (`hooks.json` has no `userPromptSubmit` entry today) -- this is new
  plumbing, not a rewire of something existing.
- `/clear` vs `/new` semantics (does `/new` truly leave no `sessionEnd`?) are
  **operator-asserted, not yet independently confirmed** against the current
  CLI host -- confirm before relying on it, since building a reclaim path on a
  wrong assumption here would misfire.

Both of the above were re-verified independently during the 2026-09-16 design
session (the `docs/architecture.md` citation above turned out to describe
`sessionStart`'s `additionalContext` behavior, not `userPromptSubmitted` --
the real confirmation source is the public hooks reference, cited below).

Design (resolved 2026-09-16, this design session -- see the matching Journal
entry): grounded against the real code (`tracking.py`, `finalize.py`,
`sweep.py`, `__main__.py`'s handoff-cutover retirement path,
`session_catalog._maybe_reap_fsmonitor`) and against the public Copilot CLI
hooks reference. Both assumptions the 2026-09-14 entry flagged as unconfirmed
are now confirmed (see bullets 9-10 below); nothing in this design is still
open pending host verification.

Plan (in progress -- first sub-slice built 2026-09-17, PR #2824;
reviewed/buildable, replaces the prior proposal-only list):
- [x] Add `"session"` to `ResourceKind`/`ResourceClaim.kind`'s vocabulary
  (`tracking.py:380-382`, `:499-508`) -- outbound, not a second `sessions:`
  list. `ref` reuses the existing qualified-ref grammar via
  `format_claim_ref(machine, project, worktree_id, session=session_id)`
  (`tracking.py:432-471`), i.e. `<machine>/<project>/<worktree_id>#<session_id>`,
  so `parse_claim_ref` (`tracking.py:473-497`) needs no change.
- [x] `cmd_register_session`/`tracking.register_session`
  (`__main__.py:24830`, `tracking.py:4823`): in the same locked-record
  transaction that creates/updates the `SessionEntry`, call
  `tracking.add_resource_claim(record, ResourceClaim(kind="session",
  ref=self_session_ref, state=obligations.ACTIVE, note="live Copilot
  session"), save=False)` before the existing save. No new reopen logic is
  needed: `add_resource_claim`'s existing `_claim_reopens_owner`/
  `reopen_finalized_owner` path (`tracking.py:3930-3971`, `:3993-4041`)
  already reopens a finalized owner for any new live claim of any kind (the
  Phase 1 held-claims fix the 2026-09-14 entry's "[x] Reopen" checkbox
  already validated) -- this is a pure application of existing machinery,
  not a new mechanism.
- [x] Add `release_resource_claim(record, ref, *, save=True)` to
  `tracking.py`, mirroring `settle_resource_claim` (`tracking.py:4057-4083`)
  but writing `state = obligations.RELEASED`. Call it from
  `cmd_deregister_session`/`tracking.deregister_session`
  (`__main__.py:25523`, `tracking.py:5115`) for the ending session's own
  ref, right after `_end_session_activation`, so `sessionEnd` releases (not
  just settles) the claim outright -- matching the Plan's "clean process
  exit" requirement and `ResourceClaim.state`'s existing
  active/at-rest/released vocabulary (`tracking.py:510-536`).
- [x] Settle-on-finalize: in `validate_and_finalize` (`finalize.py:1334`),
  resolve the invoking session id the same way `cmd_register_session` does
  (`_activate_session_binding`/payload-CWD resolution, `__main__.py:24873-
  24894`) and call `tracking.settle_resource_claim(record,
  current_session_ref, disposition=obligations.AT_REST)` *before*
  `_assert_obligations_settled` runs (`finalize.py:1421-1429`) -- so the
  invoking session's own claim is settled by finalize itself, never left
  for the operator to settle by hand, and never blocks the hard gate.
- [x] `_assert_obligations_settled` (`finalize.py:1136-1233`): exclude
  `kind == "session"` claims from the hard-blocking `unsettled` computation
  at `finalize.py:1183` (`unsettled = [c for c in record.resources if
  c.is_unsettled and c.kind != "session"]`). Session claims never
  hard-block finalize; they get their own advisory pass (next bullet), per
  this Plan's own "advisory (not hard-blocking)" framing -- the authoritative
  text below, not the handoff-note paraphrase that suggested a hard block
  with an `--abandon` escape, which this design deliberately does not
  follow.
- [x] Add an advisory-only `_advise_other_live_sessions(record,
  current_session_ref)` in `finalize.py`, called from
  `validate_and_finalize` right after the settle-current-session step
  above. It finds every OTHER live (`ResourceClaim.is_live`,
  `tracking.py:383-390`) `kind == "session"` claim and, if any exist, lists
  each one (`kind`/`ref`/`note`, mirroring the existing claim-listing
  style at `finalize.py:1215-1220`) via `output.warn(...)` -- but always
  returns/proceeds; it must never return `False`. This is the "name them
  and ask" behavior from the Plan bullet below, scoped to advisory (warn +
  continue) rather than an interactive block, since most finalize
  invocations here are non-interactive/agent-driven.
- [x] Handoff-cutover settle: in `_handoff_cutover_retire_result`
  (`__main__.py`), right after the pane is confirmed retired and the
  predecessor Copilot process is confirmed reaped (`overall_ok and
  pane_confirmed_retired`), a new `_settle_predecessor_session_claim`
  helper calls `tracking.settle_resource_claim(record,
  predecessor_session_ref, disposition=obligations.AT_REST)` for the
  predecessor session -- reusing the existing settle primitive, not
  re-deriving handoff completion, and settling (not releasing) since the
  predecessor process may still be winding down. Runs for *every* confirmed
  retire (bare or token-bearing), since `link_handoff` (the token-bearing
  path) only transitions `SessionEntry.state`, never the Phase 8 resource
  claim; never resurrects an already-`released` claim (a raced
  `deregister_session`), mirroring `finalize.py`'s
  `_settle_current_session_claim` guard.
- [ ] Extend the existing never-wedge sweep resolvers `claim_gone`/
  `claim_safe` (`sweep.py:397-431`, dispatched from `make_resolvers`/
  `self_heal`, `sweep.py:432-472`) with a `claim.kind == "session"` branch,
  reusing `session_catalog._maybe_reap_fsmonitor`'s liveness-transition
  pattern (`session_catalog.py:379-417`): level-triggered (act on the
  current non-live observation, not an edge), cooldown-throttled, and
  corroborated against a real PID/process check for that specific session
  id before the claim is treated as gone -- so a crashed/killed session
  cannot wedge finalize forever, same invariant every other claim kind
  already gets from this sweep. `claim_safe` for `kind == "session"` mirrors
  `claim_gone`'s own verdict (gone implies safe) rather than running a
  second, separate probe: unlike the `worktree` kind (which must prove the
  child's branch landed upstream before calling it safe), a session claim
  carries no separate at-risk payload of its own -- any uncommitted work the
  session left behind is exactly what Phase 9's `local_dirtiness`/
  `open_claims` facts already surface independently, so there is nothing
  further for this claim kind's own `safe_of` to check.
- [ ] Register a `userPromptSubmit` hook in `hooks.json` alongside the
  existing `sessionStart`/`sessionEnd` entries (`hooks.json:20-33`): a new
  lightweight command hook invoking a new CLI subcommand (e.g.
  `session-reopen-nudge`, mirroring `bind-nudge`'s shape at
  `__main__.py:24455-24458`) that, when the current session's own claim
  exists and is not live, calls `add_resource_claim(...)` to reactivate it
  -- composing with the existing `add_resource_claim`/
  `reopen_finalized_owner` path (the second bullet above), not a new
  reopen mechanism. The hook's own text output is not relied upon:
  `userPromptSubmitted`'s `modifiedPrompt` field is documented as honored
  only for SDK programmatic hooks, never for command hooks, so this design
  only depends on the command's side effect (the CLI call), matching the
  effort's "runs with real side effects even though text output is
  discarded" framing.
- [x] Confirmed (2026-09-16, this design session, replacing "operator-
  asserted, not yet independently confirmed"): the public GitHub Copilot
  CLI hooks reference documents `userPromptSubmitted` as firing on every
  submitted prompt on live CLI hosts ("Fires ... for the prompt supplied");
  a command-type entry runs with real side effects regardless of whether
  its output is applied. Separately, neither `/new` nor `/clear` fires
  `sessionEnd`/`sessionStart` -- only real process start/exit do -- which
  is exactly why the "user keeps talking after finalize, same process,
  same session id" case is real: the session claim's `sessionStart`/
  `sessionEnd` boundary already spans across `/new`/`/clear`, so no
  separate `/clear`-vs-`/new` special-casing is needed anywhere in this
  design; the `userPromptSubmit` reopen hook above is the sole and
  sufficient reopen path. Worth noting explicitly: the reopen hook's own
  trigger condition (the claim is not currently `active`) is *already*
  invariant to which slash command a host reports -- the confirmation above
  is corroborating evidence that the case genuinely arises, not something
  the reopen mechanism itself depends on. A future host whose `/clear`/
  `/new` behavior differs from what was confirmed here would not require
  any design change, only a different frequency of the same reopen path
  firing.

### Phase 9 - Decompose derived status into sub-state facts with pursued freshness

Carves the 2026-09-15 request above into concrete work. Redefines the
`FINAL`/`MERGED` split (Phase 4-5) from an implicit "cached vs refreshed"
special case on one fact into a general model: a fixed set of independently
named, independently freshness-tracked facts, with an unconfirmed fact marked
in place rather than spawning a parallel state. Builds on the existing
`ClosureDescriptor`/`assemble_closure_descriptor` (Phase 4) and the resident
accelerator (`classify_daemon.py`/`cmd_status_monitor`) rather than replacing
either.

- [x] Name the sub-state facts explicitly in the descriptor: checkpoint
  activity (turns since last checkpoint), upstream-containment (branch at or
  ahead of `origin/[main|master]`), local dirtiness, open downstream claims,
  and pending unclaimed handoff. Each fact keeps its own value independent of
  the others (no collapsing into a single label until render time). Landed:
  `prune.FACT_NAMES`/`ClosureDescriptor.facts`, PR #2754.
- [x] Add a per-fact freshness flag (confirmed vs. unconfirmed/asterisked) to
  the descriptor, replacing the current all-or-nothing
  `evidence_mode`/`evidence_complete` pair that only ever gates the single
  FINAL/MERGED distinction. `FINAL` requires upstream-containment AND
  claims-clear to both be independently confirmed; either one alone being
  stale marks only that fact, not the whole verdict. Landed alongside the
  above (`DESCRIPTOR_VERSION` bumped 1 -> 2); `checkpoint_activity` and
  `local_dirtiness` are always-confirmed (locally computed), while
  `upstream_containment`/`open_claims` share the existing fetch-freshness
  input for now (independent per-fact tracking, not yet independent
  SOURCES of freshness -- that's the repo-scoped ledger below).
- [x] Add a repo-scoped freshness ledger (last-confirmed-fetch timestamp per
  repo, not per worktree) that any worktree's classify pass can read without
  itself having fetched -- so a fetch performed by any sibling worktree's
  finalize/pr-merge, or the periodic sweep below, immediately counts as
  current evidence for every worktree of that repo. Landed:
  `tracking.record_repo_fetch_confirmed`/`repo_fetch_confirmed_at`/
  `is_repo_fetch_fresh` (a single machine-wide `repo-freshness.json` under
  the validated registry root, keyed by repo, guarded by `_RecordLock`'s
  best-effort/non-blocking mode); wired into `assemble_closure_descriptor`'s
  new `repo_fetch_fresh` parameter, which extends ONLY
  `upstream_containment`'s confirmation (not `open_claims` -- the ledger
  covers git upstream refs specifically, not claim/provider state). The
  THREE existing `__main__.py` closure-descriptor call sites now both READ
  `is_repo_fetch_fresh(rec.repo)` and WRITE
  `record_repo_fetch_confirmed(rec.repo)` whenever that call's own fetch
  just succeeded -- this is the "wherever a fetch already happens" writer,
  not yet the dedicated periodic sweep or the `pr-merge`/`finalize`/`sync`
  operation-triggered writers below (still separate, unstarted Plan items).
- [x] Extend the resident status-monitor (`cmd_status_monitor`) with a
  periodic per-repo revalidation sweep (default on the order of once a
  minute, configurable) that fetches and reclassifies upstream-containment
  for every repo it is tracking, publishing the refreshed ledger entry -- an
  addition to the existing resident accelerator, not a second daemon. Landed:
  `session_catalog.ResidentSessionReconciler._maybe_refresh_repo_freshness`,
  called unconditionally (NOT mux-gated, unlike the fsmonitor reap) from
  `_index_record` for every record on every `step()`. Throttled per-repo
  (60s cooldown, `_REPO_FRESHNESS_SWEEP_COOLDOWN_S`) so N worktrees of one
  repo cost one `git fetch` per window, not N; skips the fetch entirely when
  `tracking.is_repo_fetch_fresh` already says current (avoids a redundant
  fetch on top of one the ad hoc slice-2 writer, or an earlier sweep tick,
  just performed).
- [~] Add operation-triggered recompute signals from `pr-merge`, `finalize`'s
  own fetch, `sync`, and claim settle/release, so the affected repo's (or
  worktree's, for worktree-scoped facts) freshness is refreshed promptly
  instead of waiting out the next periodic sweep. Landed the repo-scoped
  half: every existing `git_ops.fetch(...)` call site in `finalize.py`
  (`push_changes`'s initial fetch + its non-fast-forward retry,
  `_push_changes_pr`, `_push_changes_pr_refspec`, `validate_and_finalize`)
  and `pr_ops.py` (`create_pr`'s pre-rebase fetch,
  `_pull_forward_recommendation` -- fired specifically on a just-merged PR,
  the literal "pr-merge" trigger) and `__main__.py`'s `cmd_sync` now call
  `tracking.record_repo_fetch_confirmed(record.repo)` immediately after
  their own fetch succeeds, instead of only the periodic sweep or an
  incidental later `__main__.py` closure-descriptor call benefiting. NOT
  landed: **claim settle/release** is a different axis entirely (worktree-
  scoped `open_claims`/held-claims state, not the repo-scoped git-upstream
  ledger) -- held claims and follow-ups are always locally accurate with no
  fetch/staleness concept, so there is no ledger write for them to trigger;
  if a cache-invalidation gap turns out to matter there, it is a distinct,
  still-unscoped follow-up, not silently folded into this bullet.
- [x] Add pending-handoff as a descriptor fact, read from context-handoff's
  baton schema (read-only; agent-worktrees does not compose or consume a
  handoff -- see the `mux-companion` vision's schema-read-only boundary for
  the same rule applied to a different consumer). Landed via
  `rec.pending_handoffs` -- agent-worktrees' own already-existing,
  already-tested, cooperative tracking of opened-but-unlinked session
  handoffs (a predecessor recorded a token, no successor session linked
  yet), populated by the existing `note-handoff`/`bind-nudge` commands, not
  a NEW coupling to context-handoff's own baton file format. Read-only in
  this function (never composed/mutated here); always `confirmed` (a local
  tracking-record read, no fetch/staleness concept); purely informational
  -- does NOT gate `FINAL`.
- [x] Render the marker convention (an asterisk, or the compact-text
  equivalent) on any individual unconfirmed fact across `list --json`, the
  mux/PSMux status segment, and the Picker -- replacing today's implicit
  "COMPLETED reads MERGED unless freshly fetched" special case with the
  general per-fact marker. Landed for `list --json` and the mux/PSMux
  status segment: `ClosureDescriptor.compact` now appends `U*`/`OC*` for an
  unconfirmed `upstream_containment`/`open_claims` fact respectively,
  independent of each other, of the `C<N>`/`F<N>` markers, and of the base
  label (not just `MERGED` -- any state can carry either marker). The mux
  segment already renders `compact` directly, so it inherited this for
  free. The Picker now consumes it too: `worktree-manager`'s picker_tui
  gained an always-on second (detail) row per worktree (generalizing the
  pre-existing, previously-conditional live-pulse sub-line), which carries
  the `compact` suffix (everything after the base label); freed the
  `RELATION` column from 7 truncated text cells to a single icon
  (`●`/`◐`/`⇒`/`■`/`?`/` `) to make room, with the full word now only ever
  needed on that second row (though not currently repeated there -- see
  Journal for what was deliberately left out).
- [x] Update `docs/cli-reference.md`, `docs/mux.md`, and
  `docs/worktree-lifecycle.md` (all touched by PR #2679/#2681 for the old
  FINAL/MERGED split) for the decomposed model, and update the
  `mux-companion` vision's Companion explainer view to render the named
  facts and their individual freshness rather than a single label. All
  three reality docs now updated for the marker convention. The
  `mux-companion` vision (and the parent `visions/plugins/agent-worktrees`
  vision) turned out to need NO further edit -- both were already updated
  to the decomposed-facts model in PR #2734 (before this Phase 9
  implementation even started), and both already describe the "named
  facts, individually confirmed/stale" shape this session actually built.
- [x] Mixed-version safety: an older/newer descriptor version (or a payload
  missing the new per-fact freshness fields) degrades the same way Phase 4's
  `interpret_descriptor_payload` already degrades an unsupported version --
  never silently promoted to confirmed. Bumping `DESCRIPTOR_VERSION` to 2
  makes the existing exact-version check reject a v1 payload automatically
  (no code change needed); now exercised by a dedicated test against a
  REALISTIC hand-built v1-shaped payload (top-level
  `evidence_mode`/`evidence_complete`, no `facts` key at all), plus a test
  documenting that `interpret_descriptor_payload` never reads `facts`
  in the first place (the version check IS the whole safety net).

## Validation Plan

- [x] **Sub-state decomposition** (Phase 9): the descriptor names
  each of the five facts (checkpoint activity, upstream-containment, local
  dirtiness, open claims, pending handoff) independently, with its own
  freshness; `FINAL` requires upstream-containment and claims-clear both
  independently confirmed; a repo-wide fetch (background sweep, a sibling
  worktree's finalize/pr-merge) counts as fresh evidence for every worktree
  of that repo without each needing its own fetch; an unconfirmed fact
  renders with a marker on that fact alone, never as a separate whole state.
  Named facts + per-fact `confirmed` landed (`prune.py`); the repo-scoped
  ledger now lets a fetch performed by ANY worktree of a repo confirm
  `upstream_containment` for every other worktree of that repo without a new
  fetch (`tracking.is_repo_fetch_fresh`/`record_repo_fetch_confirmed`,
  wired into the 3 existing `__main__.py` closure-descriptor call sites) --
  but only the ad hoc "wherever a fetch already happens" writer, not yet the
  dedicated periodic sweep or `pr-merge`/`finalize`/`sync` triggered writers
  (separate, unstarted Plan items); `open_claims` still shares this call's
  own `evidence_mode` input (the ledger is git-upstream-specific, does not
  extend to claim/provider freshness). Marker rendering across surfaces
  also not yet built (still Phase 9 follow-up work). The resident
  status-monitor's periodic per-repo sweep now ALSO refreshes the ledger
  proactively (`session_catalog._maybe_refresh_repo_freshness`, 60s
  cooldown, unconditional/not mux-gated), so a repo stays fresh even when
  no worktree of it ever passes `--fetch` itself. `finalize`'s own fetch,
  `push-changes`, `create-pr`'s pre-rebase fetch, the post-merge
  pull-forward check (literally "pr-merge"), and `sync` now ALL record the
  ledger immediately on their own successful fetch too, closing most of the
  "operation-triggered recompute" bullet's gap between an operation
  happening and the periodic sweep's own 60s cadence noticing it.
  Claim-settle/release triggered writes remain explicitly out of scope (a
  different, worktree-scoped axis -- see that Plan bullet's own note).
  `compact` now also renders the `U*`/`OC*` per-fact marker (independent of
  each other, of the `C<N>`/`F<N>` markers, and of the base label) whenever
  the corresponding fact is unconfirmed; the mux/PSMux status segment
  inherits this automatically (it already renders `compact` directly).
  `pending_handoff` now reads `rec.pending_handoffs` (agent-worktrees' own
  already-tracked opened-but-unlinked session handoffs), always confirmed,
  purely informational (does not gate `FINAL`) -- all 5 facts are now
  real, none are stub placeholders. The Picker now renders `compact` too:
  an always-on second (detail) row per worktree in `worktree-manager`'s
  picker_tui (generalizing the pre-existing conditional live-pulse
  sub-line) carries the marker suffix, freed up by iconifying the
  `RELATION` column (7 truncated text cells -> 1 icon cell). Every Phase 9
  Plan bullet is now checked off.
- [ ] **Session-claim lifecycle** (Phase 8, designed 2026-09-16, build
  started 2026-09-17): a worktree's own live Copilot session is a held
  `kind="session"` claim; `register_session` opens it, `settle_resource_claim`
  settles it on finalize and on successful handoff cutover, a new
  `release_resource_claim` releases it on `sessionEnd`, and a new
  `userPromptSubmit` hook reopens it
  (via the existing `add_resource_claim`/`reopen_finalized_owner` path) after
  a premature settle. `finalize`'s hard obligation gate excludes `session`
  claims from `unsettled`; a new advisory-only `_advise_other_live_sessions`
  names any OTHER live session claims via `output.warn` without blocking.
  The never-wedge sweep (`sweep.py`'s `claim_gone`/`claim_safe`) gains a
  `session` branch reusing `_maybe_reap_fsmonitor`'s liveness-transition
  pattern. Not started -- design is reviewed/buildable; implementation is
  the next slice.
- [x] **Reopen:** adding a new claim to a retained finalized worktree succeeds,
  changes lifecycle state away from finalized (to `active`), and immediately
  removes prune eligibility (already covered by Phase 1's held-claims fix).
  Follow-up-triggered reopen is not yet testable -- `FollowUpRecord` doesn't
  exist (Phase 3).
- [ ] **Reopen history:** reopen output identifies released claims and re-homed
  child resources that were not restored by reopening. Not done -- `claims add`
  reports `reopened: true` but does not enumerate prior cascade-released
  resources.
- [x] **Finalize rollback:** a failure after entering `finalizing` restores a
  mutable stable state (tested directly against `_rollback_finalizing_freeze`
  and wired into both post-freeze failure paths in `validate_and_finalize`).
  Stale finalizing records predating this fix, or a crash that occurs before
  either failure handler runs, still have **no explicit operator-facing
  recovery path/verb** -- not done.
- [ ] **Claim-free:** active and at-rest claims both prevent `FINAL`; only
  released and abandoned claims are excluded from the held count, and abandoned
  claims remain visible in audit detail.
- [x] **Follow-up list:** multiple open obligations produce the exact count and
  list; resolving or transferring one changes the count atomically. Covered for
  add/resolve/dismiss (`test_tracking.py::TestFollowUpLedger`,
  `test_follow_ups_cmd.py`); no transfer path exists yet to test.
- [x] **Legacy:** a boolean-only `follow_up=true` record remains blocked
  (`effective_open_follow_up_count` falls back to it) -- but it does **not**
  yet "gain a safe explicit representation on its next mutation" (auto-
  materializing a legacy boolean into an itemized entry is unimplemented).
- [ ] **Ownership:** resource-claim and dispatch-task references do not transfer,
  settle, release, or complete the referenced object implicitly.
- [ ] **Git:** open/unmerged pull requests, dirty files, local-only commits, and
  unverified squash equivalence prevent `FINAL`.
- [ ] **Parity:** the same fixture produces byte-identical compact status text
  and matching semantic style metadata in list JSON, mux, and Picker.
- [ ] **Evidence parity:** the same live worktree rendered through cached,
  fetch-free, and refreshed evidence modes has consistent labels; incomplete
  evidence can only lower confidence, never promote to `FINAL`.
- [x] **Cleanup:** `cleanup` now enumerates the exact held-claims/open-follow-up
  reason per worktree (`cmd_cleanup`'s `_cleanup_per_item_skip_reason`, fixed
  this phase -- it previously silently dropped both buckets from the report
  entirely). `gc` already reported them via `classify_managed_worktree`'s
  `reason` (Phase 1). Neither yet consumes the descriptor's own `action`
  field directly (see the unchecked Phase 4/5 items) -- they still derive
  from `CleanupDisposition` directly, just correctly now. UNUSED/CONVO/GONE
  keep their existing distinct action categories (unchanged).
- [ ] **Blocker precedence:** an UNUSED, CONVO, GONE, or system record with a
  held claim or open follow-up is `blocked`, never `opt-in` or `record-reap`.
  **Not yet true:** `cleanup_disposition`'s held-claims/follow-up override only
  fires when the record is `finalized`/git-`completed`/`merged`; an
  UNUSED/CONVO record with a held claim does not currently get forced to
  `blocked`. Left unchecked deliberately.
- [x] **Destructive freshness:** cached/fetch-free evidence never authorizes
  deletion; the immediately-preceding refreshed recomputation must still be
  safe. Enforced in `assemble_closure_descriptor` (a non-`refreshed`/incomplete
  descriptor downgrades `safe` to `blocked`) with direct tests
  (`test_cached_evidence_never_reports_final_or_safe`,
  `test_incomplete_evidence_never_reports_final_or_safe`). Not yet wired to an
  actual pre-delete recompute call site (`cleanup`/`gc` don't consume the
  descriptor yet -- see the unchecked Phase 4 bullet above).
- [x] **Guidance:** no shipped instruction says finalized work cannot be
  resumed. Fixed the one that did:
  `scripts/conduct/worktree-conduct.md`'s "do not resume work after
  finalizing" (the deployed postToolUse nudge fragment). Every close-out
  path (`worktree` skill, `docs/worktree-lifecycle.md`, this conduct
  fragment) now instructs resolving obligations rather than treating
  finalize as terminal; the "transfer" half (offer/accept/decline) has no
  shipped path yet since that machinery isn't built (Phase 3 note).
- [ ] **Regression:** existing ACTIVE, DIRTY, WIP, UNUSED, CONVO, GONE, ORPHAN,
  and UNKNOWN behavior remains stable when no closure blockers exist.
- [ ] **Concurrency:** stale background record writers preserve every concurrent
  follow-up mutation through the ledger revision merge.
- [ ] **Mixed versions:** a remote without the descriptor, or with an
  unsupported descriptor version, is provisional and never prune-safe.
- [ ] **Bridge:** agent-bridge and its cockpit preserve the descriptor and do
  not drop rows into a permanent provisional state.
- [x] **Explained blockers:** every `blocked` or `unsafe` disposition carries at
  least one blocker from the closed code set (`prune.BLOCKER_CODES`) --
  `test_all_emitted_blocker_codes_are_in_the_closed_set`. Only the subset this
  descriptor can currently derive is emitted; the still-unwired codes
  (`active-effort`, `inbound-obligation`, `unverified-squash`, `live-session`,
  `checkout-missing`, `prune-review-required`, `incomplete-evidence`,
  `unsupported-descriptor`) are named in `assemble_closure_descriptor`'s
  docstring as not yet produced.

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

### 2026-09-13 - Hotfix landed ahead of the phased plan; scope confirmed
- Landed an unplanned, narrower fix in
  [PR #2592](https://github.com/ThomasMichon/copilot-extensions/pull/2592):
  `tracking.add_resource_claim`, `claims add`, and both `claim_handoffs` actor
  checks no longer reject a `finalized` owner (only `finalizing`/`orphaned`
  remain blocked). This closes the immediate "creator ownership is frozen"
  rejection reported in the Request, and updated `docs/worktree-lifecycle.md`
  + the `worktree` skill to match, but it is **not** the full Phase 2 design:
  it does not reopen the record's lifecycle state back to `active`, does not
  implement the reopen/freeze/rollback transaction, and does not touch
  `follow_up`/the descriptor at all. Phase 1-6 checkboxes remain unstarted;
  Phase 2's mutation-centralization item should absorb/supersede this hotfix
  rather than duplicate it.
- Operator resumed this effort via a fresh session, reconfirmed the standing
  request (see the dated Request addendum above), and added new scope: an
  agent should proactively open a follow-up (or claim) on any issue it files
  that's related to its current task, so a self-filed bug doesn't silently
  fall out of the worktree's obligations. Folded into Phase 3 and Phase 5.
- No further implementation done this session; still Draft, plan not yet
  submitted for the effort's own PR review gate.

### 2026-09-13 - Started execution; closed a safety gap PR #2592 left open
- Operator said "start it." Status moved **Draft -> Active**. Bound this
  worktree to the effort at Phase 1
  (`effort-focus bind ... --slice "Phase 1 - Lock the contracts with failing
  fixtures"`).
- While scoping Phase 1's "Git-settled record with held claims" fixture,
  found that PR #2592 (letting a finalized owner accept a new claim) had
  opened exactly the gap this effort exists to close: neither
  `prune.cleanup_disposition` nor `gc.classify_managed_worktree` ever
  consulted `rec.resources` at all, so a `finalized` record holding a fresh
  claim was cleanable/reapable regardless. Fixed both (new `held-claims`
  bucket/reason, gated the same way the existing `follow_up` override is),
  with regression tests. This is the prune-verdict slice of Phase 1's first
  bullet and part of Phase 4's "held claims + open follow-ups turn any base
  state into blocked" invariant -- landed early because it was a live safety
  gap, not merely a locked failing fixture.
- Explicitly NOT done this session: the unified closure descriptor (Phase 4),
  the `FollowUpRecord` ledger (Phase 3), the reopen/freeze/rollback
  transaction (Phase 2), and the list JSON / mux / Picker parity fixtures
  (rest of Phase 1). Next slice: either finish Phase 1's remaining fixtures
  (compatibility, concurrency, inventory) or move to Phase 2's centralized
  mutation/reopen transaction -- pick up from the Plan checklist above.

### 2026-09-13 - Phase 2: reopen transaction + a second wedge bug closed
- Operator said "continue." Bound a fresh worktree at Phase 2
  (`effort-focus bind ... --slice "Phase 2 - Make finalized records
  resumable"`).
- Implemented the centralized reopen transaction: `tracking._claim_reopens_owner`
  decides whether a mutation increases held obligations; `add_resource_claim`
  calls `reopen_finalized_owner` when it does. Idempotent replays, settling
  `active -> at-rest`, and adding an already-non-live claim never reopen.
  Added `WorktreeRecord.last_finalized_at` (preserves the historical
  finalize timestamp the reopen clears from `completed_at`) with YAML
  read/write round-trip. `claims add`'s CLI/JSON output now reports
  `reopened: true/false`.
- Follow-up-triggered and accepted-inbound-obligation reopen triggers are
  **not done** -- `FollowUpRecord` is Phase 3 and claim-handoff `accept` isn't
  implemented yet (`claim_handoffs.py` only has offer/decline/cancel so far).
- While implementing the freeze invariant checklist item, found a **second
  live wedge bug**: once `validate_and_finalize` freezes a record to
  `finalizing`, neither the post-freeze `lock.acquire()` `TimeoutError` path
  nor the outer `except Exception` cleanup-failure path ever reverted the
  status -- a lock-timeout or any exception during cleanup left the record
  permanently stuck at `finalizing` (which `add_resource_claim` hard-rejects,
  so there was no way back short of manual YAML surgery). Added
  `finalize._rollback_finalizing_freeze` (captures pre-freeze status, reverts
  on either failure path, no-ops if a concurrent process already resolved the
  record) with direct unit tests.
- **Not done:** a dedicated stale-`finalizing` recovery CLI verb (for a wedge
  predating this fix, or a crash that occurs before either failure handler
  runs); the reopen-history requirement (listing prior cascade-released
  resources in the reopen output); Phase 3-6 entirely.
- `python tools/run-plugin-tests.py agent-worktrees` -- 516 passed (full suite
  minus the same two pre-existing, unrelated `test_knowledge_plugins.py`
  failures noted in the prior entry).

### 2026-09-13 - Phase 3: itemized follow-up ledger + a third pre-existing gap closed
- Operator said "keep driving." Bound a fresh worktree at Phase 3
  (`effort-focus bind ... --slice "Phase 3 - Replace the boolean-only
  follow-up model"`).
- Added `tracking.FollowUpRecord`/`FollowUpRef` + `WorktreeRecord.follow_ups`
  (YAML round-tripped, emitted only when non-empty) and the CRUD primitives
  `add_follow_up`/`resolve_follow_up`/`dismiss_follow_up` +
  `effective_open_follow_up_count` (itemized open/pending-transfer items,
  falling back to the legacy boolean only when the ledger is empty -- no
  double counting). `add_follow_up` reopens a `finalized` owner through the
  same `reopen_finalized_owner` transaction Phase 2 built.
- New CLI verb `agent-worktrees follow-ups [id|add|resolve|dismiss]`
  (`--ref kind:value` repeatable on `add`; kinds include `issue` per the
  operator's "encourage the agent to claim bugs it files proactively" ask).
  `offer`/`accept`/`decline` transfer is **not implemented** -- there's no
  existing acceptance machinery to route through (`claim_handoffs.py` itself
  only has offer/decline/cancel).
- Wired `prune.cleanup_disposition`'s existing follow-up gate to
  `effective_open_follow_up_count` (was the raw `rec.follow_up` boolean) and
  the two `gc.classify_managed_worktree` call sites in `__main__.py` the same
  way, so an itemized open follow-up blocks cleanup/GC exactly like the
  legacy boolean did.
- **Found and fixed a third pre-existing gap** while implementing the
  "keep `status --follow-up` as compat" bullet: `effort-focus bind` calls
  `tracking.set_disposition(follow_up=True, ...)` directly, but
  `set_disposition` itself never reopened a `finalized` owner -- only the
  manual `status --follow-up` CLI path had its own explicit pre-check. So
  binding an effort to an already-finalized worktree set the flag but left
  `status: finalized` in place. Moved the reopen call into `set_disposition`
  itself (any `follow_up=True` assertion now reopens consistently), verified
  active-effort-binding compat was otherwise already correct pre-existing
  behavior (no new code needed there beyond this fix).
- Updated `docs/cli-reference.md` (new `follow-ups` row) and the `worktree`
  skill's obligation-gate section (proactive issue-claiming guidance, tying
  back to the operator's explicit ask from the prior session).
- **Not done:** cross-writer merge-by-highest-revision reconciliation for
  concurrent follow-up mutations (the per-item `revision` field exists but
  nothing merges by it yet); `offer`/`accept`/`decline` transfer; auto-
  materializing a legacy boolean into an itemized entry on its next mutation;
  Phase 4-6 entirely.
- `python tools/run-plugin-tests.py agent-worktrees` -- 525 passed (full suite
  minus the same two pre-existing, unrelated `test_knowledge_plugins.py`
  failures noted in prior entries).

### 2026-09-13 - Phase 4: the canonical closure descriptor
- Operator said "keep going" (and separately: route any handoff through the
  manual prompt path, not `trigger_handoff`, which is currently broken --
  noted for this session, not an effort concern). Bound a fresh worktree at
  Phase 4 (`effort-focus bind ... --slice "Phase 4 - Derive canonical
  finality once"`).
- Added `prune.ClosureDescriptor` / `assemble_closure_descriptor` (version 1):
  a pure function combining already-computed facts (git `WorktreeStateInfo`,
  `CleanupDisposition`, held-claim count, open-follow-up count) into the one
  canonical descriptor design.md specifies -- `label`/`style`/`compact`
  (`FINAL`/`MERGED` + `C<N>`/`F<N>` markers), `closure.final`, and a graded
  `action.disposition` (`safe`/`opt-in`/`blocked`/`unsafe`), all derived, never
  stored. `FINAL` requires refreshed+complete evidence, upstream-complete Git,
  zero held claims, zero open follow-ups, and not live -- a cached/fetch-free
  descriptor structurally cannot report `FINAL` or a `safe` action even when
  the underlying facts would otherwise qualify.
- Wired it into `list --json --classify`'s existing row-builder as an
  additive `closure` field (alongside the legacy `cleanup_bucket`/`state`
  fields, which are unchanged) -- the first real consumer, proving the
  descriptor is actually assembleable from live data rather than a paper
  design.
- **Not done this phase** (all explicitly named in the Plan/Validation Plan
  above, not silently dropped): `cleanup`/`gc` still consume
  `CleanupDisposition` directly, not the descriptor's action disposition
  (deliberately deferred to Phase 5 so this PR is one behavior change, not
  two); the GONE/managed-record-reap disposition path; the
  `active-effort`/`inbound-obligation`/`unverified-squash`/`live-session`/
  `checkout-missing`/`prune-review-required`/`incomplete-evidence`/
  `unsupported-descriptor` blocker codes; the UNUSED/CONVO+held-claim
  precedence rule (`cleanup_disposition`'s own held-claims override doesn't
  fire for those base states yet); the legacy/GC explicit preview/apply
  reconciliation command; Phase 5's mux/Picker/agent-bridge consumption and
  Phase 6's fleet migration.
- Observed one apparently-flaky, unrelated test
  (`test_handoff_cutover.py::TestPaneWrapperInitialPrompt::
  test_wrapper_appends_native_interactive_prompt`) fail once in a full-suite
  run; reproduced green in isolation both with and without this change's
  diff -- not investigated further as out of scope (pane/subprocess timing,
  nothing in the touched files).
- `python tools/run-plugin-tests.py agent-worktrees --subsuite-timeout 600` --
  533 passed (full suite minus the same two pre-existing, unrelated
  `test_knowledge_plugins.py` failures).

### 2026-09-14 - Phase 5: guidance fixed at the source + mixed-version safety
- Operator said "continue." Bound a fresh worktree at Phase 5
  (`effort-focus bind ... --slice "Phase 5 - Align every presentation and
  guidance surface"`).
- Found and fixed the **exact stale instruction the effort's Context section
  named**: `scripts/conduct/worktree-conduct.md` -- the deployed postToolUse
  nudge fragment every agent sees ("It's been N tool calls...") -- said "do
  not resume work after finalizing." Replaced with the correct guidance
  (finalized is not terminal; resuming reopens automatically) and added the
  held-claim/follow-up obligation language. Verified against the existing
  `test_worktree_conduct_fragment_migrated` byte-length cap (1,800 chars;
  landed at 1,465).
- While fixing that, found and fixed a **fourth pre-existing gap**:
  `cmd_cleanup`'s per-worktree skip-reason branch chain had no case for the
  `held-claims`/`follow-up` buckets, so a worktree blocked by either
  silently vanished from `cleanup`'s report -- neither listed as skipped nor
  counted in any summary bucket. Extracted `_cleanup_per_item_skip_reason`
  (now unit-tested) and added both buckets, matching the existing
  `claimed`/`open-pr`/`paired-pending` pattern.
- Added `prune.interpret_descriptor_payload` (mixed-version fleet safety):
  an exact `version == DESCRIPTOR_VERSION` match is trusted; a missing,
  malformed, older, OR newer payload reports `supported: False`, `final:
  False`, `action_disposition: "blocked"` regardless of what its own fields
  claim. Built and tested ahead of an actual remote/cockpit consumer (none
  exists yet).
- Checked off `list --json` publishing (done in Phase 4) and the two
  guidance bullets; left the largest Phase 5 item -- rewiring the mux status
  segment and the Textual Picker to consume the descriptor's exact
  label/style/compact text -- explicitly unstarted. That's real UI-surface
  work against existing golden/parity tests and deserves its own focused
  slice rather than being rushed alongside everything else landed today.
- `python tools/run-plugin-tests.py agent-worktrees --subsuite-timeout 600` --
  535 passed (full suite minus the same two pre-existing, unrelated
  `test_knowledge_plugins.py` failures; the previously-observed flaky
  handoff-cutover test did not recur this run).

### 2026-09-14 - Session-claim lifecycle proposed (Phase 8); a false alarm resolved
- Operator flagged that my Phase 5 conduct-fragment rewrite may have
  misread the original "do not resume work after finalizing" line's intent
  -- it was meant to mean "avoid extra chatty turns after finalizing so the
  worktree doesn't render as CONVO," not "don't take further action."
  Investigated before changing anything further: `git_ops.classify_worktree`
  sets `COMPLETED` (never `UNUSED`) whenever the worktree has commits, and
  `refine_state_with_session` only ever upgrades `UNUSED` to `CONVO`. Since
  `finalize` requires content already on the default branch, a finalized
  worktree always has commits, so it can **never** classify as `CONVO`
  regardless of conversation turns before or after. Operator confirmed this
  resolves it ("never mind") -- the Phase 5 wording stands as landed; no
  further conduct-fragment change made.
- Operator then proposed a substantial new mechanic: a worktree should hold
  a **claim on its own live Copilot session** (not just track `sessions:` as
  descriptive metadata), so the existing obligation machinery -- held-claims
  blocks cleanup/finalize, the closure descriptor's blocker set, the
  never-wedge reclaim sweep -- applies to session occupancy the same way it
  already applies to a borrowed CodeSpace. Three termination paths (finalize
  / handoff / `/clear`) plus a `userPromptSubmitted`-triggered re-claim for
  the "user keeps talking after finalize" edge case, plus an advisory (not
  hard-blocking) finalize-time check naming any other live session claims.
- Captured as a new **Phase 8** in the Plan (proposed, not started) --
  grounded against real code before writing it up: confirmed
  `sessionStart`/`sessionEnd` hooks and `register_session`/`deregister_session`
  already exist as the wiring point; confirmed a command-based
  `userPromptSubmitted` hook genuinely exists on current Copilot CLI hosts
  (`docs/architecture.md`'s resume-robustness note) even though
  `agent-worktrees` has never registered one; and flagged the `/clear`-vs-
  `/new` sessionEnd-firing assumption as operator-asserted but **not yet
  independently confirmed** -- a Phase 8 plan item, not a built fact.
- Deliberately **not implemented** this session -- this is new plumbing (a
  new claim kind, two new hook registrations, an advisory finalize check),
  and the effort's own review-gate discipline is "propose before you do":
  land the plan/design first (this entry + the Phase 8 plan section),
  implement in a dedicated future slice after it's had a chance to be
  reviewed rather than rushing it in alongside five other phases in one
  session.

### 2026-09-14 - Phase 5: agent-bridge closure-descriptor passthrough
- Resumed via a stranded/dormant worktree (the prior session's handoff never
  reached a live successor -- confirmed via `agent-worktrees head-session`
  showing `head_session: null` and no on-disk changes beyond the merged PR
  #2624 base). Rebased it onto current `main` and continued Phase 5 from the
  Plan checklist rather than treating the stuck handoff as this effort's
  concern.
- Picked the smallest well-bounded Phase 5 item left: "pass the descriptor
  through agent-bridge's allow-list projection." Added `--classify` to the
  worktree-discovery crawl's `list --json` invocation (local + SSH) and
  threaded a raw `closure` field through `_WorktreeEntry`/
  `_parse_worktree_list`/`to_dict()`. Deliberately opaque/pass-through only --
  agent-bridge does not interpret label/final-ness/action-disposition itself,
  since a cross-machine crawl can reach a different agent-worktrees version;
  only a future cockpit consumer calling `prune.interpret_descriptor_payload`
  may trust it. This mirrors how agent-bridge already treats agent-worktrees
  as an external subprocess dependency everywhere else in this file (never a
  direct Python import), so the change intentionally does not duplicate
  `interpret_descriptor_payload`'s logic inline.
- Added `test_parse_worktree_list_reads_closure_descriptor` and
  `..._closure_absent_when_not_classified` to `test_routes.py`.
- **Not done this session:** the mux status segment and Textual Picker
  rewiring (still the largest Phase 5 item, unchanged from prior entries);
  legends/filters/maintenance parity; the shared compact-text
  assemble+truncate function; the actual cockpit consumer that calls
  `interpret_descriptor_payload` on this newly-passed-through field. Next
  slice: either the mux/Picker rewiring (`picker_tui/derive.py`'s `_state`/
  `_bucket_from_raw` and the PSMux/TMux status segment both independently
  derive labels today) or Phase 6/7/8.
- `python tools/run-plugin-tests.py agent-bridge` -- 235/235 passed on the
  targeted `-k "worktree or routes"` slice; full suite 589 passed, 2 pre-
  existing unrelated `test_bootstrap_check_reconcile_opt_in.py` failures
  (confirmed identical on a clean stash, a Windows `sh`-script timing issue,
  nothing touched by this change).
- `python tools/run-plugin-tests.py agent-worktrees --subsuite-timeout 600` --
  535 passed, the same two pre-existing unrelated
  `test_knowledge_plugins.py` failures noted in every prior entry.

### 2026-09-14 - Phase 5: mux status segment now consumes the closure descriptor
- Continued from the agent-bridge closure-passthrough slice (PR #2636,
  merged as squash commit `56a85501c`) via the same worktree (reused after
  its `finalize`, rebased/reset onto the new `origin/main`).
- Rewired `_render_status_segment` (the PSMux/TMux status-bar renderer) to
  call `prune.cleanup_disposition` + `prune.assemble_closure_descriptor` on
  the worktree's held-claims count and `tracking.effective_open_follow_up_count`
  -- exactly the same inputs `list --json --classify`'s row builder already
  uses (Phase 4) -- instead of mapping the raw git `WorktreeState` straight to
  a label/color via the old `_SEGMENT_STYLE` table. Added
  `_DESCRIPTOR_STYLE_BG`, a color table keyed by the descriptor's own `style`
  token (`final`/`merged-blocked`/`active`/base-state-lowercase) rather than
  the raw state, with a new `merged-blocked` color (orange, colour208)
  distinct from WIP's amber. The rendered text is the descriptor's `compact`
  field, so a worktree with a held claim or open follow-up now shows `C<N>`/
  `F<N>` markers in the status bar too -- previously invisible there entirely.
  A fetch-free (cached) poll -- the segment's own default -- now correctly
  reads `MERGED` rather than `FINAL` for a COMPLETED worktree, per
  design.md's "cached evidence never authorizes FINAL" rule; only `--fetch`
  can produce a genuine `FINAL`. The CONVO turn-count refinement (session
  activity folded into `UNUSED`) is applied to the same `WorktreeStateInfo`
  passed into the descriptor (mirroring `_classify_record`'s existing
  pattern) so CONVO also gets descriptor treatment, not a special case.
  A worktree with no tracking record (can't compute claims/follow-ups) keeps
  the legacy raw-state fallback -- explicitly never FINAL, matching how it
  already behaved.
- Added 4 new tests to `test_status_segment.py` (fetched+clean -> FINAL,
  cached+otherwise-clean -> MERGED not FINAL, COMPLETED+held-claim -> MERGED
  with a `C1` marker, DIRTY+open-follow-up -> `F1` marker while keeping the
  DIRTY label) plus a `rec=` override on the existing `_wire` test helper so
  callers can inject claims/follow-ups.
- **Explicitly NOT done this session** (scoped out, not silently dropped):
  the Textual Picker (`picker_tui/derive.py`/`engine.py`). Investigated the
  wiring point (`_state(w)`/`_bucket_from_raw(w)` in `derive.py`) and found a
  real layout constraint the mux segment never had: `engine.py`'s `state`
  table column is a **fixed 6-char width** with an exact `C_STATE` dict
  lookup keyed on the bare label (`engine.py:310-322`, `("state", "state",
  6, "l", 4)`) -- folding `compact`'s `C<N>`/`F<N>` markers into that same
  cell would either overflow or need a wider/new column, which in turn needs
  golden/layout test updates. That's real UI-surface work, not a
  data-source swap, so it stays its own slice rather than being rushed here
  (per the effort's own repeated "deserves its own focused slice" note).
  Legends/filters/maintenance-preview parity (the next Plan bullet) has the
  same dependency and is equally unstarted.
- `python tools/run-plugin-tests.py agent-worktrees -k "status_segment"` --
  22 passed (18 existing + 4 new).
- `python tools/run-plugin-tests.py agent-worktrees --subsuite-timeout 600`
  -- 1 pre-existing unrelated failure
  (`test_controller_relations.py::test_controller_metadata_is_additive_to_json_surfaces`,
  an `AttributeError: _all_tracking_dirs` unrelated to this change --
  confirmed identical on a clean stash of this diff), rest passed.
- **Review-response fix (PR #2642):** a Copilot-reviewer HIGH finding caught
  a real gap: `evidence_mode` was `"refreshed"` whenever the CALLER requested
  `--fetch`, regardless of whether the fetch itself actually succeeded --
  `git_ops.classify_worktree`'s internal `git fetch` call used `check=False`
  and silently proceeded on stale local refs on failure, with no signal
  exposed. Added `WorktreeStateInfo.fetch_failed` (threaded through every
  return path of `_classify_git_state`, default `False`) and gated the
  segment's `evidence_mode` on `fetch and not info.fetch_failed`, so a failed
  fetch attempt (network down, remote unreachable) can no longer report a
  false `FINAL`. New tests: `test_git_ops.py`'s
  `TestClassifyGitStateFetchFailed` (successful fetch -> `False`; failed
  fetch -> `True`, state still resolves; no fetch requested -> always
  `False`) and `test_status_segment.py`'s
  `test_fetch_requested_but_failed_still_renders_merged_not_final`.
- **Second review-response round (PR #2642):** two more real findings, both
  fixed:
  - The no-tracking-record fallback path still resolved a COMPLETED state
    through the legacy `_SEGMENT_STYLE` table (which maps COMPLETED ->
    FINAL directly), contradicting its own comment claiming "never FINAL:
    that judgment needs a record." Fixed: COMPLETED with no record now
    explicitly renders MERGED (using the same `merged-blocked` color), since
    held claims/open follow-ups are unprovable without a record.
  - Every prior descriptor test used `plain=True`, so the actual styled
    (`bg=...`) branch that consumes `descriptor.style`/
    `_DESCRIPTOR_STYLE_BG` was never exercised -- a color-mapping regression
    would have passed silently. Added
    `test_completed_with_held_claim_uses_merged_blocked_color` and
    `test_completed_and_fetched_and_clean_uses_final_color` (both
    `plain=False`), plus
    `test_completed_without_tracking_record_renders_merged_never_final` for
    the fallback fix.
  - Also bumped `.github/plugin/marketplace.json`'s top-level
    `metadata.version` (CONTRIBUTING.md's agent-worktrees versioning table
    requires it alongside the per-plugin entry; `check-version-bump.py`
    doesn't enforce this field today -- a real automation gap the reviewer
    caught manually, worth a follow-up guard someday but out of scope here).
- `python tools/run-plugin-tests.py agent-worktrees -k "status_segment"` --
  26 passed (22 + 4 new).
- `python tools/run-plugin-tests.py agent-worktrees --subsuite-timeout 600`
  -- same single pre-existing unrelated failure, 584 passed.
- **Third review-response round (PR #2642):** while this PR was in review,
  an unrelated critical bug (#2650, a circular-import crash breaking every
  `agent-worktrees` CLI invocation) surfaced and was fixed/merged separately
  as PR #2653 -- see that PR's own history; not part of this effort. Also
  during this round, an unrelated module-size-split refactor
  (`agent-worktrees: properly break down the CLI root into smaller
  modules`, #2657) merged to `main`; this PR's commits were squashed and
  rebased past both, with each version-bump collision resolved as it came
  (dev104 through dev109 across several merges racing in from a very active
  main branch this session).
  - The reviewer caught 3 more real gaps, all in the `fetch_failed`
    propagation this effort's own earlier round added: (1) `classify_worktree`'s
    `subprocess.TimeoutExpired` handler returned `UNKNOWN` without setting
    `fetch_failed` -- a timeout mid-fetch gives no confirmation the fetch
    completed, so it must propagate `fetch_failed=fetch` too, not just an
    explicit nonzero-exit fetch failure. (2) The Phase-4 `_worktree_to_dict`
    call site (`list --json --classify`) still hardcoded
    `evidence_mode="refreshed"` unconditionally whenever `state_info` was
    supplied, never checking `state_info.fetch_failed` -- the exact same
    false-FINAL risk the status segment's own fix addressed, just at a
    different call site. Both fixed; new tests
    `TestClassifyGitStateFetchFailed::test_classify_worktree_timeout_with_fetch_reports_fetch_failed`
    and `test_closure_descriptor_wiring.py`'s
    `test_closure_downgrades_to_cached_when_fetch_failed`.
  - Two documentation nits: `_render_status_segment`'s own docstring still
    only described the legacy `FINAL`/raw-state contract (no mention of
    `MERGED` or the `C<N>`/`F<N>` markers) -- updated. The PR description's
    version-bump and test-count claims had drifted from the actual final
    values across several rebase rounds -- reconciled.
- `python tools/run-plugin-tests.py agent-worktrees -k "status_segment or
  classify_worktree_timeout or closure_descriptor_wiring or
  ClassifyGitStateFetchFailed"` -- 35 passed.
- `python tools/run-plugin-tests.py agent-worktrees --subsuite-timeout 600`
  -- 2 additional pre-existing unrelated failures surfaced by the
  module-size-split rebase (`test_ahp_command.py::
  test_direct_backend_refuses_active_hosted_binding` and
  `::test_ensure_rejects_finalizing_worktree`, both an `IndexError` on an
  empty output-capture list unrelated to fetch/closure logic -- confirmed
  identical with and without this round's diff via `git stash`), plus the
  same single pre-existing `test_controller_relations.py` failure noted in
  every prior entry.
- **Fourth review-response round (PR #2642):** the reviewer's persistence
  paid off -- caught that the third round's `fetch_failed` fix was still
  incomplete: `fetch_failed` alone stays `False` whenever no fetch was ever
  attempted (every actual current caller of `classify_worktree` for `list
  --json --classify` passes `fetch=False`), so both `assemble_closure_descriptor`
  call sites still reported `"refreshed"` for an ordinary fetch-free
  classification. Added `WorktreeStateInfo.fetch_requested` (mirrors
  `fetch_failed`'s propagation through every `_classify_git_state` return
  path and the timeout handler) and changed both call sites (the mux
  segment and the Phase-4 `_worktree_to_dict`) to gate `evidence_mode` on
  `fetch_requested and not fetch_failed`, not `fetch_failed` alone. Updated
  existing tests whose fixtures had encoded the old (buggy) assumption, and
  added `test_closure_downgrades_to_cached_when_no_fetch_requested` --
  exactly the real-world case (`list --json --classify` with no `--fetch`)
  the finding named.
- `python tools/run-plugin-tests.py agent-worktrees -k "status_segment or
  closure_descriptor_wiring or FetchFailed"` -- 34 passed.
- `python tools/run-plugin-tests.py agent-worktrees --subsuite-timeout 600`
  -- same pre-existing failures as the prior round, no new ones.

### 2026-09-15 - Picker label parity landed; Phase 9 proposed (decomposed sub-state facts)

- Landed the Picker half of Phase 5's "mux and Picker use the descriptor's
  exact... style token" bullet -- specifically the label/color sub-part, not
  the compact `C<N>`/`F<N>` marker/column-layout sub-part (still its own
  unstarted slice, per the 2026-09-14 entry above). `derive._state()` now
  prefers `w["closure"]["label"]` (FINAL/MERGED) for a `completed` worktree,
  falling back to legacy FINAL when no descriptor is present (older remote);
  `derive.bucket()` groups MERGED with FINAL under "completed";
  `engine.py`'s `C_STATE`/`MAINT_GROUP_ORDER` gained a MERGED entry (orange,
  matching the status bar's `merged-blocked` color) in both the
  agent-worktrees source and the worktree-manager transplant; `obscure.py`
  gave MERGED the same demo-mode priority as FINAL. PR #2679, PR #2681
  (docs: `cli-reference.md`/`picker.md`/`worktree-lifecycle.md` updated for
  the FINAL/MERGED split -- the first time those reality docs documented it
  at all).
- Prototyped a hotkey-summoned, in-Mux "Companion" popup (`visions/
  mux-companion`) as a separate, adjacent capability: PR #2687 (vision),
  #2693 (exit/focus prototype -- found empirically that `display-popup -E`
  auto-closes on any clean app exit, and that binding must be a bare
  script-path, not an inlined multi-word command), #2703 (v1: real
  status/lineage view backed by a new `agent-worktrees status-segment
  --json` verb, added because `list --json --classify --worktree-id <id>`
  still pays the resident classify daemon's whole-fleet negotiation cost
  even scoped to one id). Filed as issue #2696 (prototype findings). This
  Companion consumes whatever label the closure descriptor renders -- it is
  a presentation surface, not a rival computation -- so Phase 9's decomposed
  facts flow through it once Phase 9 lands, with no Companion-side change
  needed beyond the explainer text noted in Phase 9's Plan.
- Operator observed all their live worktrees read `MERGED`, never `FINAL` --
  traced to `evidence_mode` being scoped to a single `classify_worktree`
  call's own `fetch_requested`/`fetch_failed` flags (this effort's Phase 4/5
  work, confirmed working as designed) rather than to the repo-wide
  remote-tracking refs every sibling worktree of a repo actually shares (a
  `finalize`/`pr-merge`'s own `git fetch` already refreshes those refs for
  every worktree of that repo, on any git worktree setup, immediately -- the
  descriptor just never gets to claim that shared freshness). Discussed
  into a general redesign: name the derived status's inputs as independent
  sub-state facts, track freshness per-repo instead of per-call, maintain it
  actively (periodic sweep + operation-triggered recompute) via the existing
  resident accelerator, and mark an individual unconfirmed fact rather than
  doubling the state space for "unverified." Mined into `visions/plugins/
  agent-worktrees/README.md` (PR #2734, also fixed two rounds of unrelated
  pre-existing CI drift on `main` -- a module-size baseline gap hit twice
  and a dead import blocking the repo-wide ruff guard -- to land at all).
- Carved the vision delta into this effort's **Phase 9** (Plan +
  Validation Plan above) rather than a new effort: this is a direct
  continuation of Phase 4/5's "one canonical descriptor, every surface
  consumes it" intent, not a new subject. Not started; handed off for
  implementation.

### 2026-09-15 (continued) - Phase 9 slice 1: named facts + per-fact freshness landed

- Landed the first two Plan bullets: `prune.ClosureDescriptor` now carries a
  `facts` dict (`prune.FACT_NAMES`: `checkpoint_activity`,
  `upstream_containment`, `local_dirtiness`, `open_claims`,
  `pending_handoff`), each with its own `confirmed` freshness flag, replacing
  the old top-level `evidence_mode`/`evidence_complete` pair entirely.
  `checkpoint_activity`/`local_dirtiness` are always locally computed (always
  `confirmed`); `upstream_containment`/`open_claims` share the existing
  fetch-freshness input (independent per-fact tracking, not yet independent
  freshness SOURCES -- that's the repo-scoped ledger, still unstarted).
  `pending_handoff` always reports `confirmed: false`/`value: None` (not yet
  wired, per Plan). `FINAL` now derives from
  `facts["upstream_containment"]["confirmed"] and
  facts["open_claims"]["confirmed"]` instead of the old single
  `fresh_and_complete` flag. `DESCRIPTOR_VERSION` bumped 1 -> 2 (an
  incompatible shape change per its own doc comment); `interpret_
  descriptor_payload`'s existing exact-version check rejects a v1 payload
  automatically, no code change needed there for Plan item 8's mixed-version
  safety (not yet covered by a dedicated test, though).
- Wired `turn_count` into `assemble_closure_descriptor` (new optional kwarg,
  default 0) so `checkpoint_activity` has a real value; all three
  `__main__.py` call sites (`_worktree_to_dict`, the mux/PSMux status
  segment, and the `status-context` JSON path) already had a `turns`/`_turns`
  variable in scope and now pass it through.
- Updated `tests/test_prune.py` and `tests/test_closure_descriptor_wiring.py`
  for the new `facts` shape (removed all `evidence_mode`/`evidence_complete`
  assertions, added `facts[...]["confirmed"]` ones); added a
  `test_to_dict_shape` assertion that `set(payload["facts"]) ==
  set(prune.FACT_NAMES)`. `tests/test_prune.py`,
  `tests/test_closure_descriptor_wiring.py`, `tests/test_status_segment.py`
  (119 tests) all pass; a full-suite run hit only the same pre-existing,
  unrelated `test_ahp_command.py::test_direct_backend_refuses_active_hosted_
  binding` failure confirmed present on the unmodified checkout too (not
  this change's regression). `ruff check` finding counts are identical
  before/after (46, all pre-existing).
- Updated `docs/worktree-lifecycle.md` with an additive "Decomposed
  sub-state facts (Phase 9, in progress)" subsection describing the new
  `facts` shape; explicitly notes the `FINAL`/`MERGED` label rules on the
  surfaces are unchanged by this slice (rendering the per-fact marker is
  still unstarted, per Plan item 6).
- **Not done this session** (remaining Phase 9 Plan items, unstarted): the
  repo-scoped freshness ledger, the resident status-monitor's periodic
  per-repo revalidation sweep, operation-triggered recompute signals from
  `pr-merge`/`finalize`/`sync`/claim settle-release, `pending_handoff`'s
  real (read-only, context-handoff-baton-backed) wiring, the per-fact marker
  rendering across `list --json`/the status segment/the Picker, `docs/
  cli-reference.md`/`docs/mux.md` updates, the `mux-companion` vision's
  Companion explainer update, and a dedicated mixed-version-safety test for
  a v1 payload against the new v2 shape.

### 2026-09-15 (continued) - Phase 9 slice 2: repo-scoped freshness ledger landed

- Landed the repo-scoped freshness ledger Plan item. New
  `tracking.record_repo_fetch_confirmed(repo, at=...)` /
  `repo_fetch_confirmed_at(repo)` / `is_repo_fetch_fresh(repo,
  max_age_seconds=REPO_FRESHNESS_MAX_AGE_S, now=...)`: a single
  machine-wide, repo-keyed JSON file (`repo-freshness.json`, under
  `registry_paths.registry_path` -- the same validated root as the global
  config/projects/repos registries, NOT a per-project `tracking_dir()`,
  since `rec.repo` -- "owner/name" -- is a different namespace than a
  project's short name). Writes reuse `_RecordLock`'s existing
  ``blocking=False`` best-effort contract (a skipped write under contention
  just means slightly less shared freshness this pass; the next successful
  fetch self-heals it) plus `_atomic_write` for the replace itself. Default
  freshness window 120s (`REPO_FRESHNESS_MAX_AGE_S`), on the same order of
  magnitude as the still-unstarted periodic sweep's planned cadence.
- Wired a new `assemble_closure_descriptor(..., repo_fetch_fresh: bool =
  False)` parameter: it extends ONLY `upstream_containment`'s `confirmed`
  (`fresh_and_complete or repo_fetch_fresh`) -- deliberately NOT
  `open_claims`, since the ledger is specifically about git upstream refs,
  not claim/provider state (a repo-wide fetch says nothing about whether a
  held claim or a PR's merged-state was also just rechecked). `FINAL` and
  the `action_disposition == "safe"` gate both still require BOTH facts
  independently confirmed (unchanged two-fact rule from slice 1) -- so a
  repo-ledger hit alone confirms upstream-containment but cannot alone earn
  FINAL/safe for a worktree that still has real held claims/follow-ups.
- Wired the READ (`is_repo_fetch_fresh`) and WRITE
  (`record_repo_fetch_confirmed`, called whenever THAT call's own
  `fetch_requested and not fetch_failed`) into all three existing
  `__main__.py` closure-descriptor call sites (`_worktree_to_dict`, the
  mux/PSMux status segment, `status-context`'s JSON path) -- so any one of
  them fetching for its own worktree now immediately un-staleness every
  OTHER worktree of that repo, closing the exact gap the 2026-09-15 Journal
  entry above diagnosed (an operator's worktrees reading MERGED forever
  because `evidence_mode` was scoped to one call, never shared).
  Intentionally NOT yet done (separate Plan items): the resident
  status-monitor's own dedicated periodic per-repo sweep, and
  `pr-merge`/`finalize`/`sync`/claim-settle-triggered writes -- those still
  only benefit passively from whichever of the 3 wired call sites happens
  to run with `--fetch` first.
- Added `tests/test_repo_freshness.py` (9 cases: unrecorded/fresh/aged-out/
  per-repo-independence/empty-repo-no-op/corrupt-file/missing-parent-dir)
  and 2 new `TestClosureDescriptor` cases in `tests/test_prune.py`
  (`repo_fetch_fresh` confirms `upstream_containment` alone; does not alone
  earn FINAL when real claims are held). All of `test_repo_freshness.py` +
  `test_prune.py` + `test_closure_descriptor_wiring.py` +
  `test_status_segment.py` (129 tests), plus `test_tracking.py` +
  `test_status_context.py` (182 tests), pass. `ruff check` line-shift-only
  diff confirmed via before/after comparison (no new findings).

### 2026-09-15 (continued) - Phase 9 slice 3: periodic per-repo freshness sweep landed

- Landed the resident status-monitor's periodic per-repo revalidation sweep
  Plan item. New `session_catalog.ResidentSessionReconciler.
  _maybe_refresh_repo_freshness(record)`, called from `_index_record` for
  EVERY record on every `step()` tick -- unconditionally, unlike
  `_maybe_reap_fsmonitor`: git-fetch freshness has nothing to do with
  session/mux liveness, so gating it on `mux_fresh` would silently stop the
  sweep whenever a mux observation goes stale, defeating the point of an
  ACTIVE sweep (this matters concretely: the fsmonitor reap's own gate was
  exactly the kind of silent gap this effort chased down twice already).
- Throttled per-REPO (not per-worktree-id) via a new
  `_repo_freshness_checked` cooldown map, `_REPO_FRESHNESS_SWEEP_COOLDOWN_S
  = 60.0` -- so N worktrees of the same repo cost one `git fetch` per
  cooldown window, not N (mirrors the fsmonitor reap cooldown's own
  capped-map pattern, `_MAX_REPO_FRESHNESS_TRACKED = 512`). Before spawning
  a fetch, checks `tracking.is_repo_fetch_fresh(repo)` and skips entirely if
  already fresh -- so this sweep never adds a redundant fetch on top of one
  the slice-2 ad hoc `__main__.py` writer (or an earlier sweep tick) just
  performed a moment ago. On a successful fetch (`git fetch origin --quiet`
  against the record's own `worktree_path`, bounded to 15s), calls
  `tracking.record_repo_fetch_confirmed(repo)`; a failed/timed-out/missing
  fetch or a missing directory is silently fine (self-heals next window).
- Added 7 new tests to `tests/test_session_catalog.py`: fetches+records when
  stale, skips when already fresh, does not record on fetch failure, skips a
  missing directory, **runs with no `observe_mux` call at all** (proving the
  no-mux-gate claim), one fetch total for two worktrees of the same repo
  (per-repo not per-worktree throttling), and cooldown-throttled across
  ticks (mirroring the existing fsmonitor cooldown test's pattern exactly).
  `test_session_catalog.py` (25 tests) + `test_repo_freshness.py` +
  `test_prune.py` + `test_tracking.py` (293 tests total) pass; `ruff check`
  diff on `session_catalog.py` confirmed identical before/after (2
  pre-existing findings, unrelated to this change).
- **Not done this session** (remaining Phase 9 Plan items, unstarted):
  `pr-merge`/`finalize`/`sync`/claim-settle triggered ledger writes (these
  currently only benefit passively from the periodic sweep's own 60s
  cadence, not immediately on the operation itself), `pending_handoff`'s
  real wiring, per-fact marker rendering across surfaces, the remaining doc/
  vision updates, and the mixed-version-safety test.

### 2026-09-16 - Phase 9 slice 4: operation-triggered ledger writes (finalize/pr-merge/sync)

- Landed the git-fetch half of the "operation-triggered recompute signals"
  Plan bullet: every existing `git_ops.fetch(...)` call site that already
  performs a real fetch as part of its own work now ALSO records the
  repo-scoped freshness ledger immediately on success, instead of that
  fetch's freshness only reaching other worktrees via the periodic sweep's
  own 60s cadence (slice 3) or an incidental later `__main__.py`
  closure-descriptor call (slice 2).
  - `finalize.py`: `push_changes`'s initial fetch AND its non-fast-forward
    retry-fetch, `_push_changes_pr`, `_push_changes_pr_refspec`, and
    `validate_and_finalize`'s own fetch (the literal "finalize's own fetch"
    Plan wording) -- 5 call sites total, all guarded by `record and
    record.repo` (or just `record.repo` where the parameter type is
    non-Optional) since a couple of these paths tolerate a missing tracking
    record.
  - `pr_ops.py`: `create_pr`'s best-effort pre-rebase fetch, and
    `_pull_forward_recommendation` -- fired specifically when the active PR
    has just merged, i.e. the literal "pr-merge" trigger the Plan bullet
    names, even though `agent-worktrees pr-merge` itself calls a provider
    API rather than a local `git fetch` (recording confirmed WITHOUT an
    actual local fetch would be dishonest -- local remote-tracking refs
    genuinely aren't current until something fetches; this pull-forward
    check's own fetch is the closest real local-fetch trigger tied to a
    merge event).
  - `__main__.py`'s `cmd_sync`: records once per DISTINCT repo among the
    synced records after its own shared fetch (normally exactly one repo,
    since `tracking_dir()` is per-project) -- this function's own docstring
    already said "One fetch refreshes the shared upstream ref for every
    worktree of this repo," so this was the most literal match for the
    Plan's "sync" trigger.
  - Deliberately did NOT touch **claim settle/release**: it names a
    different axis (worktree-scoped `open_claims`/held-claims state), not
    the repo-scoped git-upstream ledger this slice is about. Held claims and
    follow-ups are always locally computed/accurate -- there is no
    fetch/staleness concept for the ledger to record there. Documented this
    split explicitly in the Plan bullet's own text rather than silently
    dropping it or half-implementing something speculative.
- Added `tests/test_repo_freshness_wiring.py`: 3 `push_changes`/finalize
  cases (records on the initial fetch even though squash later aborts;
  records on BOTH the initial fetch and the non-ff retry fetch; a
  record-less repo is tolerated, matching the pre-existing squash-abort
  tests' own fixture shape), 1 `cmd_sync` case (two records of the same
  repo -> exactly one ledger write), and 2 `_pull_forward_recommendation`
  cases (records on a successful fetch; does NOT record when the fetch
  raises). All via monkeypatched `git_ops.fetch`/spies on
  `tracking.record_repo_fetch_confirmed`, reusing `test_squash_abort.py`'s
  existing real-git-repo fixture helpers (`_make_repo`/`_git`) rather than
  inventing new ones.
- Verified: `test_repo_freshness_wiring.py` (6 tests) +
  `test_squash_abort.py` + `test_finalize_gate.py` +
  `test_finalize_precondition.py` + `test_finalize_rollback.py` +
  `test_repo_freshness.py` + `test_session_catalog.py` + `test_prune.py`
  (157 passed, 3 pre-existing POSIX-only skips on Windows) all pass.
  `test_pr_ops.py` (113 tests) independently confirmed pre-existing/
  environmental ~8-minute runtime on this machine BEFORE this change too
  (same 113 passed on a stashed baseline run) -- not a regression, just a
  slow file, so it wasn't re-run against the final diff. `ruff check`
  findings on every touched source file are identical before/after (35);
  the new test file's own findings (an unused import, an unused noqa, 2
  unused-unpacked-variables, one long line) were all fixed, not suppressed.
- **Not done this session** (remaining Phase 9 Plan items, unstarted):
  `pending_handoff`'s real wiring, per-fact marker rendering across
  surfaces, the remaining doc/vision updates (`docs/cli-reference.md`,
  `docs/mux.md`, the `mux-companion` vision), and the mixed-version-safety
  test. Claim-settle/release triggered writes are now explicitly scoped OUT
  of this bullet rather than left ambiguously "still pending" (see the Plan
  bullet's own note).

### 2026-09-16 (continued) - Phase 9 slice 5: per-fact freshness markers rendered

- Landed the visible payoff of the whole decomposition: `compact` now
  appends `U*`/`OC*` whenever `upstream_containment`/`open_claims`
  (respectively) is unconfirmed -- independent of each other, of the
  existing `C<N>`/`F<N>` held-claim/follow-up markers, and of the base
  label (a `DIRTY`/`WIP`/etc. worktree can carry either marker too, not
  only `MERGED`). `pending_handoff` is deliberately excluded from marker
  rendering: it always reports `confirmed=False` until wired, so marking it
  now would put a meaningless asterisk on every single row.
  `checkpoint_activity`/`local_dirtiness` are always confirmed, so never
  marked either.
- The mux/PSMux status segment (`__main__.py`'s status-segment path)
  already renders `descriptor.compact` directly as `block_label`, so it
  inherited both markers for free -- no code change needed there. `list
  --json`'s `closure.compact` field inherits the same way (same
  descriptor). The Picker does NOT yet consume `compact` at all (confirmed
  via `grep` -- only `prune.py`, `__main__.py`, and this slice's own tests
  reference `.compact`); rendering it there remains a separate, explicitly
  still-open Plan item, matching the 2026-09-15 Picker-label-parity entry's
  own scoping (label/color landed then, the compact marker/column layout
  did not).
- Updated `docs/worktree-lifecycle.md` (new "Per-fact freshness markers"
  subsection under the decomposed-facts section) and `docs/cli-reference.md`
  (status-segment table's marker description) for the new convention.
  `docs/mux.md` and the `mux-companion` vision's Companion explainer remain
  unstarted (both still read whatever `label`/`style` the descriptor
  produces today, not `compact`, so they are not WRONG, just not yet
  updated to mention the marker).
- Added 4 new `tests/test_prune.py::TestClosureDescriptor` cases: cached
  evidence marks both facts (`"MERGED U* OC*"`), a repo-ledger hit marks
  only `open_claims` (`"MERGED OC*"`), held claims and markers combine
  (`"MERGED C1 U* OC*"`), and `FINAL` never carries a marker. All of
  `test_prune.py` + `test_closure_descriptor_wiring.py` +
  `test_status_segment.py` (125 tests) pass; `ruff check` diff on
  `prune.py` confirmed identical before/after.
- **Not done this session** (remaining Phase 9 Plan items, unstarted):
  `pending_handoff`'s real wiring, the Picker's own `compact`-marker
  rendering (a separate, larger frontend slice), `docs/mux.md` +
  `mux-companion` vision updates, and the mixed-version-safety test.

### 2026-09-16 (continued) - Phase 9 slice 6: pending_handoff wired to a real fact

- Landed `pending_handoff`, the last of the five named facts that was still
  a stub. Reads `rec.pending_handoffs` -- agent-worktrees' own pre-existing,
  already-tested `WorktreeRecord` property (opened-but-unlinked session
  handoffs: a predecessor recorded a token, no successor session linked
  yet, populated cooperatively by the existing `note-handoff`/`bind-nudge`
  commands) -- rather than inventing a NEW coupling to context-handoff's
  own baton file format. This still satisfies the Plan's "read-only, never
  composed/consumed" boundary: `assemble_closure_descriptor` only READS
  `rec.pending_handoffs`, never mutates it.
- Always `confirmed` (a local tracking-record read has no fetch/staleness
  concept, same as `checkpoint_activity`/`local_dirtiness`) and purely
  informational -- deliberately does NOT gate `FINAL`: a pending handoff
  signals someone intends to resume the worktree, which is a distinct
  concern from whether its content is safely landed. Never renders a
  `compact` marker either, for the same reason (always confirmed).
- All 5 `FACT_NAMES` are now real, none are stub placeholders.
- Updated 1 existing test (`test_to_dict_shape`'s `pending_handoff`
  assertion flipped from the old stub's `confirmed is False` to the real
  `confirmed is True`/`count == 0`) and added
  `test_pending_handoff_reads_the_record_and_is_always_confirmed`
  (populates `rec.handoffs` with a pending `SessionHandoff`, asserts the
  fact's `count`/`tokens`, that `final`/`action_disposition` are
  unaffected, and that `compact` never carries a marker for it).
  `test_prune.py` + `test_closure_descriptor_wiring.py` +
  `test_status_segment.py` (126 tests) pass; `ruff check` diff on
  `prune.py`/`test_prune.py` confirmed identical before/after.
- **Not done this session** (remaining Phase 9 Plan items, unstarted): the
  Picker's own `compact`-marker rendering, `docs/mux.md` + `mux-companion`
  vision updates, and the mixed-version-safety test.

### 2026-09-16 (continued) - Phase 9 slice 7: mixed-version test + remaining docs

- Added the mixed-version-safety test the Plan called for: a REALISTIC,
  hand-built v1-shaped payload (top-level `evidence_mode`/`evidence_complete`,
  no `facts` key at all -- not just a v2 payload with its `version` field
  edited) is rejected by `interpret_descriptor_payload` the same way any
  other version mismatch is. A second test documents the actual mechanism
  explicitly: `interpret_descriptor_payload` never reads `facts` at all --
  the exact-version check IS the entire safety net, not per-field
  validation -- so this is a real behavioral test, not just a version-number
  tweak repeated.
- Updated `docs/mux.md` (a short addition after the existing status-bar
  paragraph, linking to `worktree-lifecycle.md`'s decomposed-facts section
  for the `C<N>`/`F<N>`/`U*`/`OC*` marker meanings).
- Investigated the `mux-companion` vision's Companion explainer bullet and
  the parent `visions/plugins/agent-worktrees` vision: **both already
  describe the decomposed-facts model** (named facts, each independently
  confirmed/stale) -- both were updated in PR #2734, landed BEFORE this
  Phase 9 implementation session even started. No vision-doc edit was
  needed; verified by reading both documents rather than assuming.
- All Plan bullets except the Picker's own `compact` rendering are now
  checked off. `test_prune.py` (92 tests) passes; `ruff check` diff
  confirmed identical before/after.
- **Remaining** (the one open Phase 9 Plan item): the Picker's own
  `compact`-marker rendering. Investigated: the Picker lives in a SEPARATE
  package (`worktree-manager/src/worktree_manager/production_picker/
  picker_tui/`, a transplant of `plugins/agent-worktrees`'s picker logic,
  per the existing "Picker label parity" pattern from PR #2679) --
  `derive.py`'s `_state()` currently reads only `w["closure"]["label"]`
  (FINAL/MERGED), never `w["closure"]["compact"]`, and `engine.py` (an
  ~8,000-line file) owns the actual fixed-width column rendering with
  golden-snapshot tests (`tests/production_picker/test_picker_capture.py`).
  This is a materially larger, higher-risk frontend slice than any prior
  one in this phase (touches rendered-text golden files in a UI I cannot
  visually verify from here) -- deliberately not attempted in this same
  pass; left as the final piece for a dedicated follow-up slice.

### 2026-09-16 (continued) - Phase 9 slice 8: Picker renders the markers too -- Phase 9 done

- Landed the last open Phase 9 Plan bullet, on operator direction: every
  worktree row in the Picker's native list is now TWO rows, not one --
  generalizing the pre-existing, previously-CONDITIONAL live-pulse
  sub-line (`build_data`'s decorative second `add()` call, gated on
  `live_pulse and live_intent`) into an always-rendered second (detail)
  line. That existing mechanism already proved variable per-row height was
  safe (the list's incremental/scroll code addresses rows by a `stop=("L",
  li)` tag, not a fixed line-height assumption) -- generalizing it was a
  much lower-risk path than inventing new multi-row list infrastructure.
- The second row now carries `derive._status_markers(w)` (new function:
  everything in `closure.compact` AFTER the base label -- so `"MERGED C1
  U* OC*"` yields `"C1 U* OC*"`, never re-showing the label itself, and
  degrading to `""` if `compact` doesn't actually start with `label`,
  e.g. a mixed-version payload) -- `C<N>`/`F<N>` tokens dim, `U*`/`OC*`
  tokens warn-styled so a stale fact stays scannable -- then the
  live-pulse glyph+intent as before, in that order. A row with neither
  renders a single dim `·` placeholder so the two-line rhythm is uniform
  across every row, not just the ones with something to say.
- Iconified the `RELATION` column (operator direction: "the RELATION
  column is enum values, so we could iconify it to make room") --
  `reciprocal.short_label`'s 6-value vocabulary (`BOUND`/`CONTROL`/
  `HANDOFF`/`TERM`/`AMBIG`/`""`) collapsed to one glyph each (`●`/`◐`/`⇒`/
  `■`/`?`/` `), shrinking the column from 7 cells (already truncated to
  "RELATI…" even at that width, per the pre-change golden) to 1. The
  freed width flows to the flex `title` column via the existing `fit()`
  column-fitter, same as before. Deliberately did NOT change the
  underlying `row["relation"]` data field (still the full text label) --
  only added an engine-side icon/style lookup at render time -- so
  `test_reciprocal_relation.py`'s existing `row["relation"] == "CONTROL"`-
  style assertions needed no change.
- The golden-snapshot risk that stopped the prior session turned out to be
  much lower than feared: `tests/production_picker/goldens/picker/
  worktrees_list.txt` is a PLAIN TEXT capture (not a pixel screenshot), so
  regenerating it via `AGENT_WORKTREES_UPDATE_GOLDENS=1` and reading the
  result directly was sufficient visual verification -- no separate
  rendering/screenshot tool was needed after all.
- Added `tests/production_picker/test_status_markers.py` (5 cases: no
  closure -> no markers, `FINAL` -> no markers, `MERGED` with a held claim
  and both unconfirmed facts -> `"C1 U* OC*"`, markers never repeat the
  base label, a mismatched `compact`/`label` pair degrades to no markers
  rather than mis-slicing) and 2 new cases in `test_reciprocal_relation.py`
  (the relation column is exactly 1 cell wide in both `ACTIVE_SPECS`/
  `LIST_SPECS`; every possible `short_label()` output maps to a defined
  icon AND style, cross-checked against the real function rather than a
  duplicated literal set).
- Verified: `test_status_markers.py` + `test_reciprocal_relation.py` +
  `test_picker_capture.py` (25 tests) pass; the FULL
  `tests/production_picker/` suite (525 passed, 2 skipped, plus 3
  pre-existing unrelated failures in `test_data_ssh_sources.py` --
  confirmed present on the unmodified checkout too, a Windows-path
  assumption unrelated to this change) also passes. `ruff check` diff on
  every touched file confirmed identical before/after (one new I001 in a
  test file's import order, fixed with `--fix` rather than left).
- **Phase 9 is now fully complete** -- every Plan and Validation Plan
  bullet is checked off. The effort's remaining open item is Phase 8
  (Session-claim lifecycle), still just a proposal (not reviewed or
  built).

### 2026-09-16 - Phase 8 design session (resumed via handoff)

- Resumed from a `context-handoff` baton (task `6da32566d54244b4be9ec26cab00b04f`)
  whose job was specifically to design (not yet build) Phase 8. Created a
  fresh `copilot-extensions` worktree for this session rather than reusing
  any prior path, per the handoff's own instruction and this effort's
  working pattern.
- Read Phase 8's existing Plan and Validation Plan text in full (both
  copies), then cross-referenced every piece of already-built machinery the
  handoff named: `ResourceClaim`/`WorktreeRecord.resources`/
  `pending_handoffs` and `SessionHandoff` (`tracking.py`); `register_session`/
  `deregister_session` and the session-lifecycle CLI surface (`__main__.py`);
  `_assert_obligations_settled`/`_pr_finalize_precondition` and the ordered
  `validate_and_finalize` call sequence (`finalize.py`); the handoff-cutover
  predecessor-retirement flow (`cmd_handoffs_check`/
  `_handoff_cutover_retire_result` in `__main__.py`, and `sessions_pane_
  retire.py`); and `session_catalog._maybe_reap_fsmonitor`'s liveness-
  transition sweep pattern. Also found the actual never-wedge dispatch point
  (`sweep.py`'s `claim_gone`/`claim_safe`/`make_resolvers`/`self_heal`),
  which the handoff hadn't named explicitly.
- Resolved both assumptions the 2026-09-14 entry flagged as unconfirmed:
  (1) confirmed via the public GitHub Copilot CLI hooks reference that a
  command-type `userPromptSubmitted` hook is real, fires on every submitted
  prompt, and executes its side effects regardless of whether its output
  (`modifiedPrompt`, SDK-only) is applied; (2) confirmed that neither `/new`
  nor `/clear` fires `sessionEnd`/`sessionStart` on current CLI hosts --
  only real process start/exit do -- which means the session claim's
  hook-defined boundary already spans across both, so the "user keeps
  talking after finalize" case is real and the `userPromptSubmit` reopen
  hook is necessary and sufficient (no separate `/clear`-vs-`/new`
  special-casing needed).
- Replaced Phase 8's proposal-only Plan bullets with concrete, buildable
  ones naming exact functions/files/line ranges for: the new `session`
  `ResourceClaim` kind and its ref grammar; wiring session-claim open into
  `register_session`; a new `release_resource_claim` for `sessionEnd`;
  settle-on-finalize ahead of the hard obligation gate; excluding `session`
  claims from that hard gate and adding a separate always-advisory
  `_advise_other_live_sessions` check (deliberately **not** a hard block
  with an `--abandon` escape, contrary to this handoff's own paraphrase --
  the effort doc's authoritative Plan text says "advisory (not
  hard-blocking)," and that governs); settle-on-handoff-cutover reusing
  `settle_resource_claim`; extending the sweep's `claim_gone`/`claim_safe`
  with a `session` branch; and the new `userPromptSubmit` hook plus a
  `bind-nudge`-shaped `session-reopen-nudge` command. Updated the
  Validation Plan's Phase 8 bullet to match.
- **Not implemented** this session -- this was a design-only
  handoff by explicit instruction ("Start designing that flow in a
  handoff"). The design above is reviewed/buildable (concrete function
  names, not vague intent, matching Phase 9's Plan style); implementation
  is the next slice, following the same working pattern validated across
  Phase 9's 8 slices (one small worktree per slice, targeted tests +
  ruff-diff-vs-baseline, Plan/Validation-Plan checkbox + Journal entry per
  slice, `create-pr` -> wait for review -> `pr-merge --now` -> `finalize`).
  No PRs opened, no code touched outside this effort doc.

### 2026-09-16 (continued) - Status sweep across the whole handoff chain

- A separate infrastructure incident occurred while the Phase 8 design
  handoff (task `6da32566d54244b4be9ec26cab00b04f`) was in flight: a
  retry-storm bug in the harness's own cutover/resume mechanism (unrelated
  to this effort's technical content) respawned a fresh session against the
  same stale handoff seed repeatedly -- 14 sessions total across ~55
  minutes -- producing **three independent Phase 8 design drafts**, not
  two as first assumed:
  1. PR #2790 (merged) -- the version reflected in this doc today.
  2. PR #2792 (closed as duplicate once #2790 landed).
  3. A **third draft, never pushed or reviewed**, sitting as a local,
     unpushed commit in a since-finalized scratch worktree
     (`operator-book2-win-20260916-111901-9750`). Discovered during this
     sweep; confirmed via `git merge-base --is-ancestor` that its content
     never reached `main` through any path. Diffed against the merged
     #2790 text and found genuinely different wording/structure (not a
     mechanical rebase artifact) -- each retry-storm session designed
     Phase 8 independently rather than one continuing another's work.
     **Deliberately not reconciled into this doc** -- the merged #2790
     design is already reviewed/buildable and is what this doc carries;
     folding a second, never-reviewed draft's wording in on top of it
     without operator direction would be an unrequested redesign, not a
     cleanup. Flagging here for the record rather than silently
     discarding it. The retry-storm bug itself is filed as
     [example-org/example-web-harness#431](https://github.com/example-org/example-web-harness/issues/431)
     (separately tracked; not this effort's concern to fix).
  - The scratch worktree holding that third draft, plus one unrelated idle
    stray worktree from the same busy window, were finalized/cleaned up as
    part of this sweep -- neither held any content not already on `main`
    or already superseded.
- Verified every Phase 9 PR (#2750, #2754, #2760, #2764, #2771, #2775,
  #2780, #2781, #2786) and the Phase 8 design PR (#2790) are actually
  `MERGED` on GitHub (not just claimed merged in a prior Journal entry),
  and that duplicate PR #2792 is `CLOSED`. Fixed one stale placeholder
  this doc had carried since Phase 9 slice 1's own entry ("PR TBD" ->
  "PR #2754"). No other content drift found -- every other Plan/Validation
  Plan checkbox and Journal claim checked out against the real PRs.
- Closed tracking issue
  [#2744](https://github.com/ThomasMichon/copilot-extensions/issues/2744)
  (Phase 9) as complete, and posted a status comment on umbrella issue
  [#1312](https://github.com/ThomasMichon/copilot-extensions/issues/1312):
  Phase 9 fully done, Phase 8 designed but not yet built.
- Cleaned up this effort's own worktree-tracking fallout on the
  example-web-harness worktree that drove the retry storm: both stale
  `pending_handoffs` ordinals (tokens `6da32566d54244b4be9ec26cab00b04f`
  and a third, separately-saved `8566349f91a644f4b262d4c4e9ca9ab5` baton
  that was never picked up) marked `cancelled` rather than left dangling;
  `head_session` was already correctly pointing at the live resuming
  session, so left untouched. Both corresponding agent-dispatch tasks
  resolved (`completed`/`abandoned`) to match. Unrelated to this effort's
  own technical content, but the direct cause of the duplicate-PR
  situation above, so recorded here for the same reason.

### 2026-09-16 (continued) - Reconciled the orphaned third Phase 8 draft

- Diffed the orphaned third draft (recovered from the still-reachable git
  object of the finalized scratch worktree's commit
  `985ecd31bf85ecd16c9cd3135510d51354e4cc5e`) against this doc's current,
  merged Phase 8 design line-by-line rather than assuming either
  "identical" or "worth replacing." Conclusion: the two designs are
  functionally equivalent on every substantive point (same claim kind,
  same open/settle/release/reopen call sites in spirit, same reopen
  trigger condition) -- neither supersedes the other, so **no wholesale
  replacement was warranted**.
- Found exactly one genuine content gap the orphaned draft filled that
  this doc's merged version left implicit: an explicit `safe_of` rationale
  for the sweep's new `kind == "session"` branch (gone implies safe, since
  a session claim carries no separate at-risk payload beyond what Phase
  9's `local_dirtiness`/`open_claims` facts already track). Folded that
  single clarification into the sweep Plan bullet above -- a future
  implementer no longer has to independently re-derive it.
- Also folded in one strengthening observation (not a new mechanism): the
  reopen hook's trigger condition (claim not currently `active`) is
  already invariant to which slash command a host reports, so the
  `/clear`-vs-`/new` confirmation this doc already carries is
  corroborating evidence the case arises, not something the design itself
  depends on. Added as a note on the existing `[x] Confirmed` bullet,
  not a new bullet -- it doesn't change what gets built.
- Deliberately did NOT merge in the orphaned draft's alternative citation
  of `tracking.link_handoff` (vs. this doc's `__main__.py`'s
  `_handoff_cutover_retire_result`) as the settle-on-cutover call site --
  both are plausible integration points for the same behavior, choosing
  between them is an implementation decision for whoever builds this
  slice, not a documentation gap to resolve now.

### 2026-09-17 - Phase 8 build started: session claim + register/deregister wiring

- First implementation sub-slice of Phase 8 (design was merged doc-only in
  PR #2790; this is the first build session). Landed in a fresh worktree,
  following Phase 9's own validated one-slice-per-worktree pattern.
- Added `"session"` to `ResourceKind` (`tracking.py`), exactly as designed:
  `ref` reuses the existing qualified `format_claim_ref(...,
  session=session_id)` grammar, no `parse_claim_ref` change needed.
- Wired `tracking.register_session` to journal a live `kind="session"`
  claim for the registering session in the same locked transaction as the
  `SessionEntry` create/update, reusing `add_resource_claim`'s existing
  dedup-by-ref and finalized-reopen behavior unchanged.
- Added `tracking.release_resource_claim` (mirrors `settle_resource_claim`
  but writes `RELEASED`) and wired `tracking.deregister_session` to release
  the ending session's own claim right after `_end_session_activation`, so
  a clean process exit releases (not just settles) the claim.
- Added 3 targeted tests (`test_tracking.py`): claim created on register,
  idempotent re-register does not duplicate the claim, claim released
  (not merely settled) on deregister. Full targeted run: 225 passed
  (`test_tracking.py` + `test_register_session.py`). `ruff check` on the
  touched file shows only the same 2 pre-existing E402/RUF100 findings
  present on `main` before this change (confirmed via `git stash` diff) --
  no new lint issues introduced. The full repo suite exceeds the bounded
  test-supervisor's 10-minute window (times out around 47% on this
  machine) even on `main`, so validation is scoped to the targeted files
  per this effort's own established practice.
- PR #2824's own automated review caught a real regression this slice
  would otherwise have shipped: journaling an `active` `session` claim
  with no matching gate exclusion yet would have hard-blocked
  `_assert_obligations_settled` for **every** worktree with a live
  session -- not a deferrable follow-up, since it broke `finalize` itself.
  Folded the fix into this same slice rather than shipping it broken:
  excluded `kind == "session"` from the gate's `unsettled` computation,
  settled the invoking session's own claim to `at-rest` in
  `validate_and_finalize` before the gate runs, and added the
  advisory-only `_advise_other_live_sessions` (warns, never blocks) for
  any OTHER live session claim. Also bumped `agent-worktrees`'s
  `module-size-baseline.json` ceiling (a deliberate, reviewed widening --
  the design explicitly grows this file, splitting it is out of scope for
  this slice), the marketplace catalog's own top-level `metadata.version`
  (missed on the first pass -- `agent-worktrees` is `plugins[0]`, which
  needs both fields per CONTRIBUTING.md), and populated `created_at` on
  the new session claim. Added 5 more targeted tests
  (`test_finalize_gate.py`) covering the exclusion, that it doesn't mask
  an unrelated unsettled claim, and the advisory pass's three cases (warns
  on another live session, silent when none, tolerates no record) -- 18
  passed in that file; 261 passed across the finalize/claim-handoff/
  tracking/register-session files together.
- A THIRD review round found the first finalize-path fix was still
  incomplete on three fronts, all folded in before merge: (1) the
  settle-current-session call mutated/saved the stale in-memory `record`
  loaded before the finalize flow's own locked work, so an interleaving
  `register_session`/`deregister_session` (each its own locked
  read-modify-write) could be silently overwritten -- fixed by reloading
  + settling inside a fresh `_RecordLock` transaction. (2) The success-path
  `release_all_resources` cascade released every live claim including
  `session`, which would tear down a still-running OTHER session's claim
  -- fixed by excluding `kind == "session"` from that cascade (and from
  `_rehome_abandoned_obligations`'s abandon-rehome selection, for the
  same reason). (3) A second, later "freeze" recheck immediately before
  marking the worktree `finalizing` still computed `unsettled` from every
  claim with no session exclusion, so it silently re-introduced the exact
  hard block the first fix removed -- fixed with the same `kind !=
  "session"` exclusion. Added 1 more targeted test
  (`test_release_all_resources_excludes_session_claims`); widened the
  `tracking.py`/`finalize.py` baseline ceilings again to match. 273
  passed across the same five test files.
- A FOURTH review round caught one more real race: the settle-current-
  session step used `settle_resource_claim` unconditionally, so a
  `sessionEnd` that released the claim moments before a retried/late
  finalize call for the SAME session id would get resurrected back to
  `at-rest` (held) -- a genuinely torn-down claim coming back to life.
  Fixed by checking the freshly-reloaded claim's own state first and
  skipping the settle when it is already `released`. Extracted the whole
  settle step into a small, directly testable
  `_settle_current_session_claim(yaml_path, record, session_id)` helper
  (previously inlined) and added 4 regression tests for it (settles an
  active claim, never resurrects a released one, no-op with no session id,
  no-op with no record) -- 277 passed across the same five test files.
  This round also raised a separate, real but explicitly out-of-scope gap:
  `_post_exit_gate`'s backstop call to `validate_and_finalize` runs in the
  *launcher's* environment, which never carries the exited child's own
  session id, so a crashed (not cleanly-exited) child's session claim is
  left `active` with no settlement path here. Documented as a deliberate
  scoping decision (a code comment on `validate_and_finalize`) rather than
  threading session-id plumbing through the launcher in this slice: the
  claim never blocks finalize (the exclusion already covers it) and its
  actual reclaim is precisely the still-deferred sweep `claim_gone`/
  `claim_safe` session-branch Plan bullet's job.
- Remaining Phase 8 bullets (handoff-cutover settle, the sweep
  `claim_gone`/`claim_safe` session branch, the `userPromptSubmit` reopen
  hook) are unstarted -- left for the next slice(s), each its own small
  worktree per the same pattern.

### 2026-09-22 - Phase 8 build continued: handoff-cutover settle (resumed via manual handoff)

- Resumed via the manual `/consume-handoff` path (deliberately not
  auto-triggered, per the retry-storm safety note on the prior handoff --
  gim-home/odsp-web-harness#431). Verified before starting: Phase 9 fully
  merged, Phase 8's first sub-slice (PR #2824) merged, no other open PR
  touching Phase 8.
- Built the second Phase 8 Plan bullet in a fresh `copilot-extensions`
  worktree: **handoff-cutover settle**. Added
  `_settle_predecessor_session_claim(wt_id, session_id)` in `__main__.py`
  and called it from `_handoff_cutover_retire_result` right after a
  confirmed retire (`overall_ok and pane_confirmed_retired`), settling the
  predecessor's `kind="session"` claim to `at-rest` via the existing
  `tracking.settle_resource_claim`.
- Deliberately made this call unconditional on `bare_retire` -- unlike the
  adjacent `_conclude_retired_predecessor` call (which only fires for a
  bare, token-less retire), the session *claim* needs settling on **every**
  confirmed retire, including the ordinary token-bearing handoff flow: that
  path's own `link_handoff` transitions `SessionEntry.state` to
  `handed-off` but never touches the Phase 8 resource claim, so without this
  the predecessor's claim would stay `active` forever after a successful
  token-mediated handoff.
- Mirrored the released-claim guard from `finalize.py`'s
  `_settle_current_session_claim` (PR #2824): never resurrects an
  already-`released` claim (a `deregister_session`/`sessionEnd` that raced
  ahead of this retire), by checking the claim's current state before
  settling. Wrapped the whole helper in `contextlib.suppress(Exception)`,
  matching `_conclude_retired_predecessor`'s own best-effort contract (an
  unresolvable project/tracking context must never fail the retire itself).
- Added 4 targeted tests to `test_handoff_cutover.py`: settles a bare
  retire's claim to `at-rest`; settles a token-bearing retire's claim
  (while leaving `link_handoff`'s own `handed-off` `SessionEntry` state
  untouched); does not resurrect an already-`released` claim; and the
  existing suite's own regressions. Full `test_handoff_cutover.py` run: 102
  passed (up from 98 pre-existing). `ruff check` on both touched files shows
  the same pre-existing counts as `main` (`__main__.py`: 27; the test file:
  4 -- confirmed via `git stash` diff, no new findings). Widened
  `tools/module-size-baseline.json`'s `__main__.py` ceiling from 8168 to
  8208 (the file's real current line count) and bumped `agent-worktrees`'s
  version to `1.5.5-dev243` (`plugin.json` + `pyproject.toml` +
  `.github/plugin/marketplace.json`'s per-plugin entry).
- Remaining Phase 8 bullets: the sweep `claim_gone`/`claim_safe` session
  branch, and the `userPromptSubmit` reopen hook -- each its own next
  slice/worktree.

