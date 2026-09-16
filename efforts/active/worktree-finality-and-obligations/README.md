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
- [ ] Keep fixtures synthetic and independent of any adopting worktree registry.

### Phase 8 - Session-claim lifecycle (proposed 2026-09-14; NOT YET REVIEWED/BUILT)

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

Plan (not started):
- [ ] Add a `session` `ResourceClaim` kind (alongside
  `worktree|codespace|container|ssh|workdir|pr`) that a worktree holds on its
  own live Copilot session id -- outbound, not a second `sessions:` list.
- [ ] `sessionStart` opens/refreshes the claim (active) for the current
  session id when CWD resolves to a tracked worktree.
- [ ] Settle-on-finalize: a successful `finalize` settles (not releases) the
  *current* session's claim as part of its existing cascade, mirroring how it
  already settles the parent's claim on this worktree.
- [ ] Settle-on-handoff: the handoff-cutover lifecycle (already tracks
  predecessor/successor sessions) settles the predecessor's session claim on
  successful cutover -- reuse that machinery rather than re-deriving handoff
  completion here.
- [ ] `sessionEnd` releases the claim outright (a clean process exit, not just
  "safe").
- [ ] A `userPromptSubmitted` hook: if a new prompt arrives on a session whose
  claim was just settled/released (finalize completed, or handoff cut over),
  reopen it -- this is the mechanism that resolves the "user starts another
  turn after finalizing" case the operator flagged, and composes with the
  existing `reopen_finalized_owner` transaction (Phase 2) rather than
  duplicating it.
- [ ] `finalize` gains an advisory (not hard-blocking) check: if OTHER live
  session claims exist on this worktree besides the current one, name them
  (session ids) and ask the operator whether to ignore or sweep for
  follow-ups, rather than silently finalizing out from under a second live
  session or silently ignoring it.
- [ ] Reuse (not duplicate) the existing `claims sweep` never-wedge reclaim for
  a session claim whose process is confirmed gone -- a crashed/killed session
  must not wedge finalize forever, same invariant as every other claim kind.
- [ ] Confirm the `/clear`-vs-`/new` assumption above against the current host
  before relying on it for the reclaim path; degrade to "leave it active,
  surface it at finalize time" if unconfirmed, per the never-fabricate-a-
  verdict discipline the claim ledger already follows elsewhere.

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
  `prune.FACT_NAMES`/`ClosureDescriptor.facts`, PR TBD.
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
- [ ] Extend the resident status-monitor (`cmd_status_monitor`) with a
  periodic per-repo revalidation sweep (default on the order of once a
  minute, configurable) that fetches and reclassifies upstream-containment
  for every repo it is tracking, publishing the refreshed ledger entry -- an
  addition to the existing resident accelerator, not a second daemon.
- [ ] Add operation-triggered recompute signals from `pr-merge`, `finalize`'s
  own fetch, `sync`, and claim settle/release, so the affected repo's (or
  worktree's, for worktree-scoped facts) freshness is refreshed promptly
  instead of waiting out the next periodic sweep.
- [ ] Add pending-handoff as a descriptor fact, read from context-handoff's
  baton schema (read-only; agent-worktrees does not compose or consume a
  handoff -- see the `mux-companion` vision's schema-read-only boundary for
  the same rule applied to a different consumer).
- [ ] Render the marker convention (an asterisk, or the compact-text
  equivalent) on any individual unconfirmed fact across `list --json`, the
  mux/PSMux status segment, and the Picker -- replacing today's implicit
  "COMPLETED reads MERGED unless freshly fetched" special case with the
  general per-fact marker.
- [ ] Update `docs/cli-reference.md`, `docs/mux.md`, and
  `docs/worktree-lifecycle.md` (all touched by PR #2679/#2681 for the old
  FINAL/MERGED split) for the decomposed model, and update the
  `mux-companion` vision's Companion explainer view to render the named
  facts and their individual freshness rather than a single label.
- [ ] Mixed-version safety: an older/newer descriptor version (or a payload
  missing the new per-fact freshness fields) degrades the same way Phase 4's
  `interpret_descriptor_payload` already degrades an unsupported version --
  never silently promoted to confirmed. Partially covered: bumping
  `DESCRIPTOR_VERSION` to 2 makes `interpret_descriptor_payload`'s existing
  exact-version check reject a v1 payload automatically (no code change
  needed there); not yet exercised by a dedicated v1-vs-v2 payload test.

## Validation Plan

- [~] **Sub-state decomposition** (Phase 9): the descriptor names
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
  also not yet built (still Phase 9 follow-up work).
- [ ] **Session-claim lifecycle** (Phase 8, proposed): a worktree's own live
  Copilot session is a held claim; it settles on finalize, settles on a
  successful handoff cutover, releases on `sessionEnd`, and reopens on a
  `userPromptSubmitted` event after a premature settle; `finalize` names any
  OTHER live session claims and asks before proceeding. Not started.
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

