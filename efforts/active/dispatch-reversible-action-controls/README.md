# Dispatch Reversible Action Controls

- **Slug:** `dispatch-reversible-action-controls`
- **Repo:** copilot-extensions
- **Branch(es):** independent reviewed source slices targeting `dev`
- **Created:** 2026-10-27
- **Status:** Draft
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

- [ ] Land this proposal through independent review before implementation.
- [ ] Distinguish shared bootstrap state, installation-scoped action inhibition,
      static self-update configuration and existing lane-stop overrides.
      New controls must not silently reinterpret the latter two.
- [ ] Define effective-state reporting through existing CLI/status/logging
      surfaces. Report the affected action, scope, reason and state source
      without exposing private task content or credentials.

### Phase 2 - Implement the recurring action boundaries

- [ ] Reuse existing installation ownership and atomic operator-state mutation
      primitives, not a second control service. Keep namespaced installations
      independent of the legacy global bootstrap file.
- [ ] Re-read effective state before coordinator and supervisor automatic
      replacement. Keep their polls alive while inhibited; clear must resume
      normal eligibility checks without a manual restart. Existing static
      configuration remains an independent veto.
- [ ] Keep lane polling and existing-work settlement alive while new-job
      admission is inhibited. Guard the actual reservation/claim/launch paths
      used by supported consumers, not only a UI toggle or candidate listing.
      Document the exact scope rather than claiming a global fence from a
      local supervisor check.
- [ ] Preserve original task/reservation/session identity and independent
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
