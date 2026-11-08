# Durable Goals and Steers

## Goal

> When I mention "goal", "mission" or "objective", the agent must update its effort to state it clearly. That way a handoff that points at the effort, or a re-read in a new context, shows the goal front and center. Fix efforts, handoffs, agent guidance and the new agenda system so they work together. User prompts should be relayed immediately into a durable location. Mechanically, the context-handoff extension should record user prompts and significant steers, leaving out skill loads, agent-to-subagent instructions and similar noise, and accumulate them into a list. After any session reset (observed as an event), force that corpus to be read back in. Acknowledge that a lot may have happened between steers, so each steer needs research before acting on it. The agent always sees this up front.

- **Slug:** `durable-goals-and-steers`
- **Repo:** copilot-extensions
- **Branch(es):** Independent reviewed plan and implementation PRs targeting `dev`
- **Created:** 2026-11-07
- **Status:** Done
- **Vision:** [Efforts](../../../../visions/plugins/efforts/README.md), durable
  effort contract and compact effort handoff
- **Umbrella issue:** [#11403](https://github.com/ThomasMichon/copilot-extensions/issues/11403)

## Guiding Intent

Operator objectives and significant amendments must remain visible across
context reconstruction. Current effort Goal is canonical; recorded prompts
retain evidence and chronology rather than silently becoming fresh authority.
This campaign closes the existing durable-effort-contract and session-relay
intent without adding a service, transcript index or mandatory sibling runtime.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Source driver | Plan, continuity/efforts integration, validation and PR ownership | Managed public source handle `buzzing-knob` |

## Coordination

Independent serial plan and implementation PRs. The source driver owns review,
CI and merge for every PR; no delegate or other participant's state is modified.
Handoffs point to this README and its Goal, current slice and immediate delta.

## Context

The extension already observes root `user.message`,
`session.context_cleared`, successful compaction and handoff consumption.
The current prompt observer retains a first prompt in module state, which is
not a durable steer history. Agenda projections currently label a root by its
source reference rather than presenting the canonical effort Goal.
Existing [continuity behavior](../../../../plugins/context-handoff/README.md) and
[agenda companion](../../../../plugins/efforts/skills/driving-effort-agendas/SKILL.md)
remain authoritative for lifecycle, ownership and optional adoption.

## Request

1. Preserve the operator directive in Goal and make goal/mission/objective
   amendments update the canonical effort immediately.
2. Durably accumulate genuine operator prompts and significant steers, excluding
   skill loads, subagent instructions and tool noise.
3. After observed context reconstruction or handoff consumption, front-load the
   corpus and current Goal; require research of each steer against current state
   before acting, preserving supersession, holds and original authority bounds.
4. Source agenda root goals and effort-backed handoffs from canonical Goal.

## Scope Approvals

Source inspection, implementation, isolated tests and ordinary reviewed PR
delivery to `dev` are assigned. Deployment remains adopter-owned; source merge
does not itself grant installation or restart authority. Preserve current
owner, host policy, permission and review gates. Corpus capture/replay is
evidence handling, never automatic execution or a new authorization evaluator.

## Plan

Implementation choices and sequencing below are agent recommendations.

### Phase 1 - Reviewed contract
- [x] Claim #11403 and inspect existing continuity, effort and agenda entrypoints.
- [x] Land this proposal through current-head review and required CI.

### Phase 2 - Goal and continuity
- [x] Add top Goal to canonical and repository templates; reconcile planning,
      capture and completion guidance without removing legacy effort support.
- [x] Implement a plugin-owned durable worktree-scoped prompt/steer corpus using
      existing local identity/state boundaries, no shared daemon or source-tree
      transcript writes. Preserve exact text, origin, event identity and ordering.
- [x] Filter root inputs by available origin metadata and known skill,
      subagent, notification and plugin-generated envelopes; do not promote peer
      instructions into operator identity. Retain significant authorized peer
      steers distinctly where supported.
- [x] Connect capture and replay to the actual extension lifecycle, reset/
      successful-compaction and consume boundaries, including reconnect startup.
      Persistence failure stays visible; retries must not lose or duplicate input.
- [x] Front-load current Goal and corpus reread, with no silent history truncation;
      expose bounded reading when the corpus exceeds one model message. Unsupported
      host admission remains explicitly unqualified, not claimed enforced.
- [x] Preserve authorization evidence and current holds; replay requests research,
      not re-execution of stale effects.

### Phase 3 - Agenda and handoff integration
- [x] Resolve effort Goal in existing standalone/managed agenda readers, retaining
      optional/manual and task/aside roots without inventing a second goal store.
- [x] Include canonical Goal/corpus pointers in generated and consumed handoffs.
- [x] Document standalone behavior, privacy/storage scope and supported replay
      boundaries; add changefiles for every affected plugin.
- [x] Land implementation through review and required CI.

### Phase 4 - Source closeout
- [x] Record actual merged PRs and observed release state separately from adoption.
- [x] Resolve validation, close tracking and archive the completed source campaign.
      Installed rollout and host smoke belong to each adopter's existing authority.

## Validation Plan

- [x] Focused tests fail before the fix for exact prompt preservation, repeated
      reset/reconnect/consume, duplicate event delivery, noise filtering,
      concurrent writes, worktree isolation and visible persistence failures.
- [x] Exercise actual registered extension event handlers with an SDK fixture,
      not only helper functions; prove replay does not capture itself.
- [x] Verify Goal changes reach agenda and handoff projections; legacy efforts
      without Goal remain usable with an explicit fallback.
- [x] Exercise corpus paging/order and ensure no constraints disappear silently
      when history exceeds one message/read budget.
- [x] Run affected-plugin tests and required lint/customization/contract checks
      through existing bounded runners; document meaningful host-proof limits.
- [x] Complete documentation-impact and publication-safety review.
- [x] Verify every source PR merged after accepted review and required CI;
      report release and installed adoption separately, never infer live rollout.

## Proposal

Prefer an SDK-independent corpus module plus an event-registration adapter in
the existing extension. Durable state is local to the continuing worktree;
the canonical effort remains versioned planning truth. Event-time writes
must settle before a reset/replay snapshot; asynchronous work is serialized
and failures are visible rather than ignored. Origin classification is not
authentication: inherited trust and original evidence remain with their owners.
Agenda reads use the current source document, not an independently mutable
root-goal string. Research may refine these choices within the Request.

## Journal

### 2026-11-07 - Proposal
- Claimed #11403 after public duplicate searches. Confirmed root clear and
  successful-compaction observations and existing prompt-receipt registration.
- Reconciled to durable-effort-contract, compact-effort-handoff and
  session-relay intent; retain payload-only and independent-plugin patterns.
- No installed runtime change, new daemon or other participant's state mutation.
- Plan PR #11408 merged to `dev` after current-head Copilot approval and
  passing checks. Source worktree reconciled to the merge. Implementation and
  qualification remain outstanding; the plan merge is not campaign completion.
- Implementation direction (agent-recommended): retain observable event
  registration rather than reintroducing callback hooks the native extension
  currently records as rejected. Serialize capture before replay snapshots;
  use immutable local records and exact event identities, with explicit
  collision/failure reporting. A replay uses system provenance, not operator
  provenance, and large histories require bounded, ordered continuation reads.
  Delivery acceptance is not evidence that the model semantically read it;
  native-host qualification must retain that distinction.
- Implemented worktree-incarnation-scoped immutable capture, origin-aware
  filtering, exact paged snapshots, reset/compaction/activity replay and
  fail-soft facts. Startup replay waits for activity rather than starting an
  idle turn. Native pressure deferral remains independent; mode off retains
  explicit reads without automatic replay.
- Canonical Goal now feeds live agenda reads and handoff facts. Historical
  accepted agenda observations retain source labels, independently verified
  through the export verifier after a Goal amendment. Detached state lanes
  use explicit absolute effort references; missing sources are labelled.
- Independent design review's blocking findings were corrected: idle-startup
  injection, corpus failure breaking handoff, redundant snapshot writes and
  repeated conflicting retries. Snapshot retention is intentionally unbounded
  to preserve durable pointers; missing-event-ID dedup and native semantic
  reading remain explicitly unqualified.
- Focused agenda/policy packet: 182 passed, 11 platform/prerequisite skips.
  Broader store/export packet: 181 passed, 37 skips. Windows symlink creation
  requires a privilege this host lacks; tests now report that prerequisite
  rather than failing before their assertions. No elevation or host setting
  change was used. The existing malformed-NUL state-home diagnostic was fixed.
- Shared Node packet reached 327 passed, 12 skips and one guidance-order
  assertion failure; the corrected guidance/intent residual passed all 27
  tests. SDK fixture tests exercise the production extension registration,
  not only helpers. These results do not qualify installed native scheduling
  or prove semantic reading by a model.
- Touched Python lint and the 13-plugin install contract passed. Scoped
  documentation checks have no errors; settings-scoped customization scan
  has zero blockers. Completion guidance was compacted to its existing 4 KiB
  template budget without weakening completion, review or authority gates.
- A disposable native SDK session on CLI 1.0.108-1, with no available tools
  and denied permissions, echoed the exact system-origin input with
  `source: "system"` intact. The helper stopped after receipt. This settles
  the origin-filter echo dependency; it is not model-reading, installed
  recorder/reset scheduling, or release qualification.
- Implementation PR #11432 received a Comment verdict with six actionable
  findings on its initial head. Corrected paired-owner Goal resolution,
  missing fresh facts in normal generation, transformed bridge delivery,
  complete serialized-page budgeting, stale-reference preservation and the
  legacy display guard. No Comment or old-head verdict was treated as approval.
- Review correction tests: all 15 corpus/Goal regressions pass; registered
  extension tests verify normal generation and transformed peer capture.
  The page-budget correction required the CLI regression to follow all next
  cursors instead of assuming exactly two pages. Focused agenda tests remain
  125 passed, 2 platform skips.
- Extracted durable-intent mechanics into the linked plugin documentation
  rather than expanding the existing README with a standalone deep section.
- The next current-head review marked those six findings resolved and identified
  four more: live replay config, CLI scope consistency, generated-reminder
  provenance and pending input with late session identity. Corrected all four,
  including the actual worktree-reminder producer and compatibility filtering
  of both content payload forms. Added the producer's patch changefile.
- Second-round focused packet: 27 passed, one platform skip; the actual
  worktree reminder event fixture passed through the bounded plugin runner.
  CLI paging and consumption are exercised from an unrelated shell directory
  with an explicitly bound fixture session. Live off/on transitions are
  exercised through the production extension registration.
- Goal reads now stop at their declared file bound, and snapshot construction
  retains sorting metadata rather than all prompt bodies in memory. An
  unavailable known binding remains visible even without a resolvable home.
- Third-round review identified stale-scope replay, optional emergency discovery,
  launch-directory config binding, rejected-send rearming and doubled Goal
  fragments. Corrected the class at each boundary: recheck scope after awaits,
  use explicitly unqualified cached emergency pointers, rebind asynchronous
  config loading/reloading without delaying SDK readiness, rearm failed sends
  on root activity, and replace existing source fragments with `#goal`.
  Added controlled-await and rejection regressions plus a production extension
  fixture with different launch/bound modes, live edits and emergency discovery
  counters. Review, release, installed qualification and closeout remain open.
- A subsequent current-head review's summary identified delayed identity during
  bare resume despite no inline findings. Corrected it before merge: defer
  unqualified replay/Goal discovery, resolve the continuing worktree once when
  identity arrives, and rehome only provisional pending inputs. The production
  fixture covers early null identity followed by tool-invocation identity, bound
  Goal/config, retained exact input and emergency storage. Changed packet:
  32 passed, one platform skip.
- The next review found the same late-identity class at slash-command entry.
  All command entrypoints and the corpus reader now adopt invocation identity
  before reading scope/config. Production regression variants cover file,
  dispatch-task and delivery-checkpoint consumption as the first late-identity
  command. The affected production fixture packet passes both tests.
- The following review summary identified a real agenda text-bound mismatch.
  Projection now measures the entire root string, including its source label,
  against the receiver's existing 4 KiB UTF-8 bound; overflow keeps a canonical
  reference rather than enlarging the wire contract. Actual receiver encode
  regressions cover Goal and legacy Guiding Intent at the exact bound and one
  byte over while preserving accepted historical records. Four focused tests
  and touched Python lint pass.
- Native SDK qualification of the source observer passed exact receipt,
  two actual handler-origin context-cleared events, full system-origin replay,
  fresh synthetic Goal reads and disconnect/resume replay. Display labels and
  transformed content are distinguished by the native host. This qualifies
  event delivery, not semantic reading by the model or installed adoption.
- Source implementation #11432 merged as `b0f90686cc` after the accepted
  current-head Copilot Comment verdict, resolved findings and required CI
  run 42439906865. Post-merge Dev CI 42440302374 also passed. Together with
  reviewed plan #11408, this completes the source campaign.
- Documentation-impact and public-artifact review are complete: durable
  behavior lives in the plugin documentation; no private tracker or adopter
  details are required by the source record. The current native proof covers
  delivery and exact replay, not semantic reading or every host/platform.
- At source closeout, automatic promotion is still in flight; no release or
  installed-adoption claim follows from the Dev merge. Adopters retain their
  existing release/update authority and installed qualification gates.
