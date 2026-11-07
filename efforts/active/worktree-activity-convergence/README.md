# Worktree Activity Convergence

- **Slug:** `worktree-activity-convergence`
- **Repo:** copilot-extensions
- **Branch(es):** dependency-ordered PRs against `dev`
- **Created:** 2026-11-07
- **Status:** Active
- **Vision:** `visions/picker/README.md` (`live-not-snapshot`, informed selection); `visions/plugins/agent-worktrees/README.md` (resident authoritative live-state database)
- **Umbrella issue:** #11195
- **Related owners:** #5953 retirement; #6748 publication convergence; #2619 event-driven production

## Guiding Intent

One worktree lifecycle action should produce trustworthy live status without a
second click or a periodic-poll delay. The Picker should distinguish muxed from
bare execution, order worktrees by real conversation activity, and make status
selection explicit instead of burying recently used completed work.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Driver | Proposal, bounded implementation, validation, PR stewardship and authorized local qualification | #11195; managed source worktree `lopsided-crate` |
| Existing owners | Native retirement, source-qualified publication and broader event-production contracts | #5953, #6748, #2619 and their existing efforts/PRs; ownership retained |

## Coordination

- **Topology:** reviewed proposal first, followed by dependency-ordered source PRs.
- **Host (owns PRs):** Driver.
- **Delegates:** none assigned.
- **Handoff:** retain this README, current slice, exact source/test evidence,
  live obligations and approval bounds; acceptance is not transferred by presence.
- This is the canonical effort for the combined user-facing objective, not a
  replacement for the
  [source-qualified publication effort](../source-qualified-publication/README.md).
  Preserve that effort's F1 credit, remaining acceptance, reserved shared hunks
  and existing coordinator/resource custody.
- Before editing an overlapping held implementation boundary, identify the
  exact needed files/hunks and reconcile its existing owner through the normal
  repository coordination surface. An issue consolidation or this proposal
  does not appoint an owner of a reserved F2/F3 implementation.
- Reuse existing retirement, observation/invalidation and source/feed owners.
  Do not add a competing roster, process killer, service or background poller.
- Do not close #5953, #6748, #2619 or their retained outcomes from this effort's
  narrower delivery.

## Context

The current source already has identity-bound retirement, a post-Reclaim
publication wait, current-worktree feeds, source-qualified row identities and
selection remapping. These are starting points, not proof the reported flows
converge. The inspected `USED` derivation selects resume/start/completion time,
not actual message activity. Existing client-side sort/search operates inside
fixed lifecycle sections.

Relevant boundaries:

- `plugins/agent-worktrees/src/agent_worktrees/reclaim_cli.py`: native effects,
  state clearance and post-effect publication are distinct outcomes.
- `worktree-manager/src/worktree_manager/production_picker/picker_tui/maintenance.py`
  and `engine_input.py`: action completion and subsequent source refresh.
- Current row/observation contracts and resident mutation intake: keep one
  authoritative producer and monotonic source-qualified consumer state.
- `derive.py`, `listview.py` and native Picker regions: recency, grouping,
  filtering, focus and selection.
- [Service contract evolution](../../../docs/patterns/service-contract-evolution.md):
  independently updated producers and consumers must remain compatible.

## Request

Original operator request, verbatim:

> Next issue: Attempting "Reclaim" operations never seem to work on the first try. We expect "Reclaim" to drive until the worktree's PROC or LIVE statuses are gone.
> We also now have an issue where sessions which are clearly muxed are showing as PROC
> Finally, the USED column is not reflecting most-recent message activity for a worktree. It makes it difficult to identify the worktrees I was just using. We should consider un-splitting "Recent" and "Completed" groups, instead provided Sort/Group/Filter options somehow to control selection by status as opposed purely by recency of use.

Follow-up, verbatim:

> We also have a bug where starting a worktree doesn't push status straight into the agent-worktrees daemon to be pushed into open worktree-manager instances. I'd expect instant updates driven by events, not reliance on polling.

The operator then selected the proposed layout:

> Keep Active separate; combine Recent + Completed, with Sort/Group/Status-filter controls.

The operator also accepted local rollout after release through the normal
Manager/Worktrees update flow, preserving live sessions and configuration.
That choice does not authorize testing Reclaim against active operator worktrees.

## Scope Approvals

- Source inspection, implementation and repository-compliant publication cover
  the four reported behaviors and the selected list controls.
- The structured answer selected `active-plus-unified-recent` and
  `local_rollout=true`. Deployment is limited to the driver's explicitly
  selected local installation after normal release, not fleet rollout.
  Concrete private installation identity and original interaction evidence
  remain with the driver's current-owner record.
- Use isolated, owned process/mux/worktree fixtures. No arbitrary live Reclaim,
  foreground-session shutdown, foreign-owner termination, privilege escalation,
  authentication change or review/CI bypass is authorized.
- Preserve reachable mux owners, caller/self protection, PID/birth identity,
  concurrent new owners, repositories, credentials, sessions and databases.
  Keep automatic-update preferences unchanged.
- Ordinary scoped revisions and bounded retries remain within this request.
  Unknown effect outcomes require owner-led reconciliation, not replay.

## Plan

Implementation choices below are **agent-recommended** ways to realize the
requested outcomes; proposed support is not a claim of installed capability.

### P0 - Reviewed intent and bounded ownership
- [x] Confirm current source, deployed/provider capabilities and existing
  issue/PR/file custody; distinguish a source defect from missing delivery.
- [x] Review and merge this proposal before functional implementation (#11199).

### P1 - Trustworthy lifecycle completion
- [x] Reproduce the first-attempt Reclaim failure through its actual action,
  engine, resident publication and Picker consumer path.
- [x] Make one action wait for confirmed retirement and authoritative row
  convergence, or retain an explicit pending/failed outcome. Do not retry
  termination merely to obtain publication or optimistically erase live facts.
- [x] Correct mux classification using the actual owning mux/provider facts,
  including managed Windows muxes and conventional tmux/PSMux; preserve safe
  Stop/Reclaim/Repair eligibility.
- [x] Keep newly concurrent, foreign, reused-PID, caller and reachable mux
  owners protected rather than chasing them to manufacture a cleared row.

### P2 - Conversation recency and explicit view controls
- [x] Publish/use the canonical most-recent worktree message-activity fact,
  including relevant registered sessions, rather than resume time, heartbeat,
  record mtime or an unrelated observation timestamp.
- [x] Use the same timestamp for USED display and recency sorting, with
  timezone-correct comparison and documented legacy/unavailable fallbacks.
- [x] Keep Active separate and combine Recent plus Completed by default;
  expose discoverable Sort, Group and Status-filter controls alongside search.
- [x] Preserve source/ownership boundaries, hidden-row policy, stable focus,
  multiselection and exact action targets across filtering and live reordering.
  Explicit status filters must disclose their active state, including when
  they hide otherwise visible live rows.
- [x] If a new wire fact or operation is required, review its concrete
  provider-first expansion and actual-runtime negotiation before enabling the
  caller. Preserve strict unknown-argument rejection, old/new combinations,
  rollback and truthful missing support; do not relabel an existing fact.

### P3 - Event-driven launch and connected flow
- [x] Notify the existing resident from the successful authoritative
  start/resume mutation boundary, including paths not launched by an agent tool.
- [x] Push the resulting qualified row/delta through the existing feed and UI
  intake to already-open Manager instances without periodic-poll, reconnect or
  caller-side fleet-scan dependence.
- [x] Prove the connected start/message/Reclaim sequence with polling disabled,
  including independent open consumers, delayed events and a concurrent owner.
  Keep destructive effects separate from publication confirmation.

### P4 - Publication, release and selected local rollout
- [ ] Complete focused source/native validation, documentation and changefiles;
  steward the implementation PRs through required review, CI and actual merge.
- [ ] Observe Main promotion of the accepted correction, then use the normal
  supported local Manager/Worktrees updater while preserving installer custody.
- [ ] Verify actual selected installed code, applicable serving generation and
  the original Picker consumer; a version marker or accepted update request
  alone is insufficient.
- [ ] Resolve every Plan/Validation item or explicitly transfer a broader
  existing-owner obligation, set Done and complete normal effort archival and
  worktree/claim settlement.

## Validation Plan

- [x] Fail-first Reclaim regression: confirmed native exit, bound-state cleanup
  and gated publication are separate; release publication and converge the
  same qualified row without another destructive operation or source reconnect.
- [x] Isolated native owned-process fixture: delayed child/root exit, stale
  locks, unreachable mux residue and concurrent owner; preserve reachable mux,
  caller, foreign and reused-PID controls and retain unconfirmed outcomes.
- [x] Mux fixtures cover managed Windows and conventional mux ownership,
  partial/stale observations and the actual process ancestry/owner boundary;
  absence of evidence must not fabricate a bare or stopped session.
- [x] Message-recency cases cover an old but just-used worktree, completed
  work, multiple registered sessions, user/assistant activity, timezone/skew,
  missing/legacy facts and non-message observation/heartbeat controls.
- [x] Real native Textual controls/goldens cover default Active/unified Recent,
  Sort/Group/Status/search, narrow terminals, sticky headers, stable selection,
  source-colliding IDs, modal updates and independent action eligibility.
- [x] Actual start/resume mutation to resident to held feed to open Picker
  integration passes with periodic producers and UI reload/reconnect disabled;
  test repeated starts, multiple consumers, delayed/out-of-order updates and
  truthful owner/source replacement.
- [x] Any changed contract has focused old/new producer/consumer, capability,
  rollback and unsupported-operation controls, not package-version assumptions.
- [ ] Run the existing contained runners, applicable native lanes, lint,
  module-size/install/contract checks and mandatory exact-head CI. Record
  source, platform, installed and performance evidence separately; retain
  unavailable broader qualification with its actual existing owner.
- [ ] The authorized local update has its own terminal successful result;
  installed code and original consumer behavior match this effort's accepted
  source, with configuration and protected consumers preserved.

## Proposal

Keep the existing lifecycle and live-state authorities. Repair producer
notification, fact selection and consumer convergence rather than hiding stale
rows or accelerating polls. The default list has Active and a unified Recent
section; users select other grouping, status filters and sorting explicitly.

The concrete message-fact compatibility and any shared receipt boundary remain
implementation-review gates, not permission to bypass existing contract or
source ownership.

### Current notification slice (agent-recommended)

Reuse the existing authenticated loopback hook transport. A serving owner
advertises `worktree-change-v1` only when its worktree-change callback is present.
The send-only `worktreeChanged` frame retains version, token, owner generation
and a one-second expiry, with one `cwd` payload: an absolute path of at most
4,096 UTF-8 bytes, or null for a coalesced owned-roster wake. Stale generations,
malformed frames and closed/unadmitted owners
decline it without a tool-policy invocation or reply.

Native record commits defer bounded metadata-only wakes through the existing
record/pair release owner, without opting into full-record or receipt capture.
Post-Reclaim confirmation sends once even when its hints were unchanged.
Both send only after checking
that actual endpoint capability; a missing/legacy owner neither boots a
service nor receives guessed new syntax. Sending is not acceptance or
publication acknowledgement. Reclaim still proves the same current source's
cleared row; it does not repeat termination to obtain that proof.

Existing `mux-live-v1` wire shapes remain unchanged. Committed mapping fact
changes or restored freshness wake publication; timestamp-only heartbeats do
not. The resident producer consumes its own fresh, project-qualified managed
mux cache as well as native mux observations.

This slice does not implement a new source-qualified receipt API, private
reducer or alternate consumer store. The concrete message-fact contract remains
part of P2 review.

### Message and view expansion (agent-recommended)

The provider adds the optional bounded `message_activity` object, version 1,
with `last_message_at` or null. Registered, non-detached session message events
and the passive extension's message-only timestamp supply it; no observation,
intent, heartbeat or filesystem time is relabeled as a message. The existing
turns cache gains compatible metadata and rebuilds legacy entries once.
Stable logs neither decode history again nor rewrite unchanged metadata.
Windows file identity uses the existing native opened-handle stamp.

The receiver selects the actual object's supported version, not a package
version. Absent, invalid or unknown optional facts retain the legacy
resume/completion/start fallback; display and sort use the same usable instant.
The old strict advisory validator can report expanded saved rows unavailable,
but an elected rolled-back provider can publish its own legacy rows normally.
This is a data expansion and capability-gated optional wake, not new CLI syntax
or a mandatory policy whose enforcement an old consumer could silently lose.
No prior field or operation is retired.

The only projection declaration change is admission of this bounded object in
the current row's existing field/type sets. It does not occupy reserved F2
receipt operations, F3 private reducer/source-store wiring, or their shared
entry/import hunks.

The view's default groups are Active and unified Recent. Explicit status and
group choices remain local to the window; they do not create machine
preferences. Status counts remain independent of grouping. Native row
fingerprints follow visible order, so a message-driven reorder cannot be
mistaken for a content-only repaint. Existing source-qualified keys preserve
focus, selection, range anchors and action targets.

## Journal

### 2026-11-07 - Source qualification complete; delivery remains open
- Canonical Windows backend packets passed: 321 passed / 4 skipped, followed
  by the corrected residual groups at 183 passed and 135 passed. A
  platform-initialization subprocess in one fixture is now pinned separately
  from its no-mux-query assertion. Sixty-second per-item diagnostic bounds
  retain the production operation deadlines and process containment.
- Final Manager controls, connected event-only flow and capture packet:
  44 passed. Native navigation, incremental repaint and large-viewport
  checks: 14 passed. Additional feed/list/control and capture/bare-orphan
  groups supplied independent passing coverage; these are not a claim that
  the entire portfolio ran in one uninterrupted sequence.
- A real queued-highlight race could overwrite keyed focus after a live
  reorder: native events now reject superseded indices and replaced option
  objects. The regression explicitly delivers a pre-rebuild highlight after
  message reordering. Genuine native arrow, multiselection and scroll-home
  behavior remains covered. Initial native mounting also refreshes the
  children, and capture requests a paint rather than relying on a later tick.
- Ruff F/E9, whitespace, module-size, install-contract, documentation and
  effort-structure checks passed. The changed runtime scopes remain pending
  MyPy admission. An optional broader probe passed 37 admitted projects for
  both declared platforms, but the unrelated service-index checker exceeded
  its existing 120-second limit; no full-portfolio typecheck pass is claimed.
- First-touch behavior uses empty, isolated record/session stores and real
  selected-peer transports, not an installer/bootstrap change. No external
  development venue was needed for this local native consumer. The authorized
  installed-runtime check remains after review, merge and Main promotion.
- P4 and the exact-head CI/rollout validation items remain open. No live
  operator worktree was reclaimed and no installed runtime has been patched.

### 2026-11-07 - Functional candidate and contained consumer qualification
- Implemented record/mux mutation wakes through the existing resident,
  release-safe metadata dispatch, managed-mux publication, message recency and
  explicit list controls. No alternate service, roster or receipt store was
  introduced.
- The connected fixture exercises an authoritative saved Resume, managed mux
  changes and message activity through actual hook/held-feed sockets and the
  real producer into two independent consumers and an already-open native
  Textual Picker. Periodic production, UI repoll and reconnect are disabled.
  A gated post-Reclaim publication converges after release with one mocked
  native retirement, not another destructive action. Separate existing
  owned-process tests cover actual native exit and identity/concurrent-owner
  protections.
- Native event-subscription and selected-peer fixtures cover message-only
  sidecars, root/delegate distinctions, the real catalog field layout,
  unsupported peers and source/installation mismatch. Incremental metadata
  cases cover partial lines, replacement, unchanged-log work counts and
  timezone ordering.
- The expanded native corpus exposed stale fixtures: invalid runtime
  generation identity, an assertion racing socket-handler completion,
  capture before the first painted setup, and outdated configuration/decision
  expectations. Fixtures retain the actual current contracts; no runtime
  admission or lifecycle guard was weakened.
- Final contained runner packets and current-head publication gates remain
  open at this checkpoint. The campaign is Active, not Done. No live operator
  session was reclaimed or updated.

### 2026-11-07 - Proposal accepted; bounded implementation begins
- #11199 merged after exact-head Copilot approval and required green checks.
  The source workspace was pulled forward through the managed continuation.
- Initial implementation uses the existing managed-mux observation and
  producer paths; no reserved receipt/reducer implementation is taken over.
  Reconcile any further shared boundary before editing it.
- The source and deployed engine already contain native-exit and publication
  waits. A wait alone does not prove a mutation wakes the producer; that
  connected behavior remains to be reproduced.
- All implementation, integrated validation and authorized rollout gates remain
  open. No live operator process was reclaimed.

### 2026-11-07 - Request captured and proposal prepared
- Captured both original requests and the operator's layout/rollout choices.
- Read the existing retirement and source-qualified publication ownership;
  retained their broader duties and holds rather than claiming completion.
- Confirmed existing native-exit/publication paths and resume-based USED
  derivation; reproduction and implementation are not yet claimed.
- Proposal only: no functional source, deployed runtime or live worktree process
  was modified.
