# Worktree Activity Convergence

- **Slug:** `worktree-activity-convergence`
- **Repo:** copilot-extensions
- **Branch(es):** dependency-ordered PRs against `dev`
- **Created:** 2026-11-07
- **Status:** Draft
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
- [ ] Confirm current source, deployed/provider capabilities and existing
  issue/PR/file custody; distinguish a source defect from missing delivery.
- [ ] Review and merge this proposal before functional implementation.

### P1 - Trustworthy lifecycle completion
- [ ] Reproduce the first-attempt Reclaim failure through its actual action,
  engine, resident publication and Picker consumer path.
- [ ] Make one action wait for confirmed retirement and authoritative row
  convergence, or retain an explicit pending/failed outcome. Do not retry
  termination merely to obtain publication or optimistically erase live facts.
- [ ] Correct mux classification using the actual owning mux/provider facts,
  including managed Windows muxes and conventional tmux/PSMux; preserve safe
  Stop/Reclaim/Repair eligibility.
- [ ] Keep newly concurrent, foreign, reused-PID, caller and reachable mux
  owners protected rather than chasing them to manufacture a cleared row.

### P2 - Conversation recency and explicit view controls
- [ ] Publish/use the canonical most-recent worktree message-activity fact,
  including relevant registered sessions, rather than resume time, heartbeat,
  record mtime or an unrelated observation timestamp.
- [ ] Use the same timestamp for USED display and recency sorting, with
  timezone-correct comparison and documented legacy/unavailable fallbacks.
- [ ] Keep Active separate and combine Recent plus Completed by default;
  expose discoverable Sort, Group and Status-filter controls alongside search.
- [ ] Preserve source/ownership boundaries, hidden-row policy, stable focus,
  multiselection and exact action targets across filtering and live reordering.
  Explicit status filters must disclose their active state, including when
  they hide otherwise visible live rows.
- [ ] If a new wire fact or operation is required, review its concrete
  provider-first expansion and actual-runtime negotiation before enabling the
  caller. Preserve strict unknown-argument rejection, old/new combinations,
  rollback and truthful missing support; do not relabel an existing fact.

### P3 - Event-driven launch and connected flow
- [ ] Notify the existing resident from the successful authoritative
  start/resume mutation boundary, including paths not launched by an agent tool.
- [ ] Push the resulting qualified row/delta through the existing feed and UI
  intake to already-open Manager instances without periodic-poll, reconnect or
  caller-side fleet-scan dependence.
- [ ] Prove the connected start/message/Reclaim sequence with polling disabled,
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

- [ ] Fail-first Reclaim regression: confirmed native exit, bound-state cleanup
  and gated publication are separate; release publication and converge the
  same qualified row without another destructive operation or source reconnect.
- [ ] Isolated native owned-process fixture: delayed child/root exit, stale
  locks, unreachable mux residue and concurrent owner; preserve reachable mux,
  caller, foreign and reused-PID controls and retain unconfirmed outcomes.
- [ ] Mux fixtures cover managed Windows and conventional mux ownership,
  partial/stale observations and the actual process ancestry/owner boundary;
  absence of evidence must not fabricate a bare or stopped session.
- [ ] Message-recency cases cover an old but just-used worktree, completed
  work, multiple registered sessions, user/assistant activity, timezone/skew,
  missing/legacy facts and non-message observation/heartbeat controls.
- [ ] Real native Textual controls/goldens cover default Active/unified Recent,
  Sort/Group/Status/search, narrow terminals, sticky headers, stable selection,
  source-colliding IDs, modal updates and independent action eligibility.
- [ ] Actual start/resume mutation to resident to held feed to open Picker
  integration passes with periodic producers and UI reload/reconnect disabled;
  test repeated starts, multiple consumers, delayed/out-of-order updates and
  truthful owner/source replacement.
- [ ] Any changed contract has focused old/new producer/consumer, capability,
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

## Journal

### 2026-11-07 - Request captured and proposal prepared
- Captured both original requests and the operator's layout/rollout choices.
- Read the existing retirement and source-qualified publication ownership;
  retained their broader duties and holds rather than claiming completion.
- Confirmed existing native-exit/publication paths and resume-based USED
  derivation; reproduction and implementation are not yet claimed.
- Proposal only: no functional source, deployed runtime or live worktree process
  was modified.
