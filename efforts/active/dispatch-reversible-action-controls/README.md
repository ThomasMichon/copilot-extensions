# Dispatch Reversible Action Controls

- **Slug:** `dispatch-reversible-action-controls`
- **Repo:** copilot-extensions
- **Branch(es):** independent reviewed source slices targeting `dev`
- **Created:** 2026-10-27
- **Status:** Active
- **Vision:** [agent-dispatch](../../../visions/plugins/agent-dispatch/README.md#reversible-action-inhibition)
- **Umbrella issue:** #9334

## Guiding Intent

Keep the coordinator, supervisor and lane polling loops available while an
operator temporarily inhibits selected actions. Clearing an inhibition lets the
existing consumer resume the action on its next eligible poll or caller retry;
normal restoration must not require a manual process restart.

Do not conflate an action KillSwitch with service removal, static startup
configuration, a successful deployment or authority to discard existing work.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| `placid-staple` | Source plan and bounded Dispatch implementation | Managed source PRs linked from #9334 |
| Repository reviewer | Independent plan and implementation review | Ordinary pull-request review |

## Coordination

- **Topology:** reviewed proposal, then dependent implementation PRs.
- **Host (owns PRs):** `placid-staple`.
- **Delegates:** none assigned.
- **Handoff:** issue, effort checklists and merged source receipts; preserve
  unresolved release and installed-consumer qualification explicitly.
- Existing #6820 preparation/sequencing and #7694 launch-maintenance owners
  remain authoritative. Do not build a competing updater or take their broader
  runtime-transaction work into this slice.

## Context

Current Dispatch self-update enablement is initialized independently for the
coordinator and supervisor. The shared bootstrap switch covers adopting
default-installation session-start reconciliation, not all resident actions.
Registration overrides remove a lane from the master's desired set, allowing
the master to stay up but winding down that lane.

The native update path also reconciles supervisor definitions after coordinator
cutover. Its graceful-only option prevents coordinator restart fallback; it
does not preserve the supervisor. Omitting supervisor installation removes its
units and is not an inhibition control.

**Reconciliation:** extend the Dispatch vision with live action inhibition,
while preserving existing override precedence and independent process
lifetimes. Follow `docs/patterns/service-lifecycle-supervision.md`,
`docs/patterns/service-contract-evolution.md` and `docs/install-contract.md`.
No new daemon, scheduler, task state or per-worker identity is required.

## Request

Public-safe statement of the requested generic behavior:

> KillSwitches restore functionality when cleared, ideally without a manual
> process restart. Polling consumers re-check each iteration; on-demand tools
> re-check when called again. Place inhibition only where retry or re-check is
> reliable. Do not prevent services from starting merely because an action is
> inhibited.

Implementation and controlled Dispatch adoption are requested. This public
source plan does not authorize an arbitrary installation change, foreign
worker termination or cross-plugin rollout.

## Plan

### Phase 1 - Review the action contract

- [x] Land this proposal through independent review before implementation.
- [x] Distinguish shared bootstrap state, installation-scoped action inhibition,
      static self-update configuration and existing lane-stop overrides.
      New controls must not silently reinterpret the latter two.
- [x] Define effective-state reporting through existing CLI/status/logging
      surfaces. Report the affected action, scope, reason and state source
      without exposing private task content or credentials.

### Phase 2 - Implement the recurring action boundaries

- [x] Reuse existing installation ownership and atomic operator-state mutation
      primitives, not a second control service. Keep namespaced installations
      independent of the legacy global bootstrap file.
- [x] Re-read effective state before coordinator and supervisor automatic
      replacement. Keep their polls alive while inhibited; clear must resume
      normal eligibility checks without a manual restart. Existing static
      configuration remains an independent veto.
- [x] Keep lane polling and existing-work settlement alive while new-job
      admission is inhibited. Guard the actual reservation/claim/launch paths
      used by supported consumers, not only a UI toggle or candidate listing.
      Document the exact scope rather than claiming a global fence from a
      local supervisor check.
- [x] Preserve original task/reservation/session identity and independent
      safety checks. Inhibition does not consume work, exhaust retry budgets,
      clear claims, authorize replay or classify an unknown effect as absent.

### Phase 3 - Qualify the maintenance boundaries

- [ ] Guard optional automatic runtime reconciliation and the explicit
      activation/coordinator/supervisor maintenance effects addressed by the
      control contract. Service startup from an available qualified runtime
      remains possible; missing prerequisites fail explicitly.
- [ ] Cover both installer-induced supervisor reconciliation and resident
      self-update. Skipping one while the other can replace the same supervisor
      does not establish preservation.
- [ ] Resolve required action policy before active-state publication; check
      current denial again at consequential boundaries. Freeze only one action
      attempt, not the daemon lifetime. Each later poll/retry is a fresh check.
- [ ] Keep explicit preparation separate from adoption using existing runtime
      ownership primitives; do not claim a new transactional rollback protocol.
      Coordinate any missing prepare-only primitive with #6820 rather than
      mutating active selectors as a staging shortcut.
- [ ] Implement equivalent POSIX and PowerShell maintenance behavior. An
      inhibited required effect is not a successful full update; retain exact
      partial-effect evidence on real downstream failures.

### Phase 4 - Release and qualify actual consumers

- [ ] Add changefiles, update the owning documentation and pass the relevant
      bounded tests, lint, install-contract and required repository gates.
- [ ] Merge reviewed implementation and verify the actual released `main`
      content, not ancestry or package-version assumptions alone.
- [ ] Under the installation owner's separate rollout authority, qualify
      installed commands and running consumers. An old process cannot acquire
      support merely because a new state file exists.
- [ ] Record running adoption or transfer that remaining gate to a named
      tracked objective before declaring this effort Done.

## Validation Plan

- [ ] Start each supported service with inhibition active: it reaches readiness,
      keeps status and polling available, and makes zero selected effect calls.
- [ ] Clear state without restarting the consumer and observe the next eligible
      poll admit the action. Job admission retains its consumer PID; automatic
      update may itself replace the process after admission.
- [ ] Exercise repeated on/clear cycles and a long-lived on-demand caller that
      retries. No process-lifetime denial cache or extra watcher is required.
- [ ] Assert actual effect-boundary calls are absent, not merely a reported
      disabled flag. Cover coordinator replacement, native supervisor
      reconciliation, resident successor launch and new-job admission.
- [ ] Preserve existing pending/active work and settlement while admission is
      inhibited. Unpause does not discard independent lane overrides, bypass
      exclusivity or produce a second worker for retained execution.
- [ ] Cover missing and malformed state, strict boolean validation, linked or
      foreign inputs, namespaced/custom-root isolation and explicit old-consumer
      refusal. Invalid explicit action policy must be visible while service
      availability and future re-check remain intact.
- [ ] Preserve existing default behavior and legacy bootstrap compatibility.
      Test default-installation fallback state paths and static configuration
      independently of dynamic inhibition.
- [ ] Exercise both native installer frontends for maintenance changes, with a
      contained process-boundary control where practical. Source fixtures do
      not establish running native-account adoption.

## Proposal

The checklists are proposed implementation means. The requested restoration and
service-availability behavior is normative; exact CLI spelling and state layout
must reuse the owning source conventions and be documented before publication.
Initial tests must prove the current gap before adding the guards.

## Journal

### 2026-10-27 - Proposal

- Claimed #9334 after checking related bootstrap, lifecycle and launch-maintenance
  work. The adjacent maintenance owners remain separate.
- Traced process-initialized self-update settings, the lane desired-set override
  and the update's unconditional supervisor-reconciliation call.
- Proposal only: no runtime implementation or installed control change yet.

### 2026-10-27 - Proposal reviewed

- #9337 merged as `e61e81219c` after the current-head Copilot review and required
  checks passed. Its sole Low finding requested the PR's documentation-impact
  assessment; that metadata was corrected and the thread resolved.
- Re-read the complete Plan and Validation Plan. Only proposal review is
  complete; action readers, consumers, maintenance controls and running
  qualification remain open.
- Begin with a behavioral regression against the actual supervisor decision:
  active shared state must leave it and its lane alive, then clearing state
  must let that same consumer admit automatic replacement.

### 2026-10-27 - Resident self-update implementation

- The initial two actual-supervisor regression cases failed at the unguarded
  replacement boundary before implementation, then passed with live checks.
- Added a dependency-free bounded policy reader, an installation-aware runtime
  adapter, and local `actions contract|status|inhibit|clear` commands. They reuse
  the existing serialized atomic mutation primitive but never reinterpret the
  lane-stop override file.
- Current command support advertises only the two actual resident consumers:
  coordinator replacement and supervisor self-update. Native maintenance and
  worker-admission actions must be added by subsequent reviewed slices, not
  advertised prematurely. The [owning guide](../../../plugins/agent-dispatch/docs/action-controls.md)
  documents this limit and running-consumer qualification.
- The actual coordinator lifespan loop remains ready through inhibited polls
  and admits replacement after state is cleared; supervisor tests also cover
  retaining its lane and lease. Static startup settings remain independent.
- Source implementation and contained tests are not release, installed adoption
  or completion of this effort. Next: review this bounded resident slice, then
  implement the remaining admission and native maintenance boundaries before
  controlled running qualification.

### 2026-10-27 - Resident review corrections

- #9345's current-head independent review identified three portability gaps:
  shared-directory probing could escape the reader's error boundary, Windows
  newline translation could invalidate a near-limit policy after publication,
  and synthetic home isolation omitted Windows `USERPROFILE`.
- Moved primary/fallback selection inside the bounded reader's shared-state
  error handling. Permission errors remain visible legacy fail-open decisions,
  and later checks observe restored access rather than a dead polling loop.
- The existing atomic writer now emits UTF-8/LF on every platform, matching the
  pre-publication byte limit. A maximum-size policy remains readable/clearable.
  Added regressions for both fixes and set both home variables in shared-state
  consumer tests. These portable contained cases do not assert native installed
  Windows adoption.

### 2026-10-27 - Resident slice merged

- #9345 merged as `25303a9926` after current-head independent review, correction
  of all Medium findings and required checks. Re-running its exact attribution
  job validated the repaired description without changing the source head.
- Only the resident-control mechanism items above are complete. Admission,
  native maintenance, release and actual consumer qualification remain open.
- Next: guard coordinator claim/reservation mutations and supervisor new-launch
  attempts while leaving existing-work settlement and polling available.

### 2026-10-27 - Worker admission

- Five actual coordinator/lane/redrive regressions failed at their effect
  boundaries before implementation. The coordinator now injects an explicit
  queue admission callback; pure library embeddings remain policy-neutral.
- Claims recheck before mutation, new reservations check before consuming an
  attempt, and redrives check before their startup fence. Existing-work
  settlement remains available. A single already-admitted launch can finish;
  later attempts recheck rather than caching denial for the daemon lifetime.
- The [owning guide](../../../plugins/agent-dispatch/docs/action-controls.md)
  records supported scopes, explicit REST/MCP denial, retry behavior and skew
  limits. Final bounded selection: 112 cases passed, including actual HTTP MCP
  retry against the same coordinator, pending identity and independent holds.
- Native maintenance, release and installed-consumer qualification remain open.
- Followed the same launch trace through restricted startup reconciliation and
  optional one-shot handoff fallback. Two further regressions proved both
  bypasses before adding guards ahead of preparation/attempt consumption.

### 2026-10-27 - Admission review corrections

- #9351's independent review found atomic create-and-claim bypassing the
  callback, and a launched script treating startup denial as terminal failure.
  Actual REST/script regressions reproduced both gaps.
- Guarded atomic create-and-claim after deduplication and before mutation, with
  a second check before claim publication. A denial rolls back task/audit and
  supersession writes rather than leaving a partially accepted request.
- Added a structured retryable REST denial and same-worker script startup retry.
  Cancellation preserves the unclaimed task. Gone unclaimed scripts retain
  their reservation while inhibited, then use the existing failed-spawn retry
  after clear; claimed scripts keep their prior failure semantics.
- A second review found the pending flag cleared before validating a returned
  claim. An independent hold applied during inhibition could therefore trigger
  terminal abandonment after clear, although the script never owned the task.
  Keep the pending flag until the claim is a mapping for this exact task.
  Regressions cover a real coordinator hold/clear transition and all invalid
  claim shapes without start or abandonment.
- Extended the pending-cancellation guard to normal context exit after the
  script handles cancellation internally. No terminal report is required or
  sent while admission is still pending, whether cancellation escapes or is
  caught by the body; claimed script terminal reporting remains unchanged.
