# Mux Functional Continuity

## Goal

Keep native Open/resume and useful, truthful mux status working while avoiding
PSMux client startup for passive observation.

- **Slug:** `mux-functional-continuity`
- **Repo:** copilot-extensions
- **Branch(es):** managed per-phase PRs targeting `dev`
- **Created:** 2026-11-08
- **Status:** Active
- **Vision:** [Session Hosting](../../../visions/session-hosting/README.md);
  [Agent Remote Driver](../../../visions/plugins/agent-remote-driver/README.md)
- **Umbrella issue:** #11544

## Guiding Intent

Observation suppression alone is not functional continuity. Users still need
to open a cold session, reattach to an existing session without replacing its
conversation, and distinguish fresh mux presence/client counts from an
unavailable observation. Preserve physical mux ownership in Driver, durable
agency in Worktrees and presentation in Manager.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Source driver | Plan, implementation, validation and PR stewardship | Managed source worktree and this effort |

## Coordination

- **Topology:** sequential plan, complete-flow implementation and completion PRs.
- **Host (owns PRs):** Source driver.
- **Delegates:** none.
- **Handoff:** continue from the first unresolved Plan and Validation Plan item.
  Preserve unknown effects and any separately required authority.

## Context

PSMux v3.4.0's [client entrypoint](https://github.com/psmux/psmux/blob/v3.4.0/src/main.rs)
performs cleanup before ordinary query/control-mode handling.
Its [session module](https://github.com/psmux/psmux/blob/v3.4.0/src/session.rs)
also exposes authenticated, finite socket queries against native session
endpoints. Qualify that existing provider path rather than starting another
CLI to observe or creating a parallel registry/control service.

The existing Driver core owns finite mux creation. Its creation lease and
discovery polls must converge with the same observation owner used by
Worktrees and Manager. A process, an endpoint file, a cached mapping and
successful prompt admission are not interchangeable mux-presence evidence.

This closes a bounded Session Hosting/Driver vision delta, rather than
extending their ownership model. Follow the existing
[patterns](../../../docs/patterns/README.md), especially component ownership,
honest observations and
[independently updated contracts](../../../docs/patterns/service-contract-evolution.md).
Preserve #10627 creation readiness, #7212 attachment/process observations,
#8669 installation-independent sessions, #10765 core convergence and #4564
hosting owners; this effort does not claim their broader acceptance.

## Request

> Looks like until psmux fixes this, it remains a risk. But we need to ensure
> that the rest of our system still works somehow. We still need to be able
> to resume/open sessions, and report mux status in our listings

The quote is public-safe and retained verbatim apart from line wrapping.
The mechanism and phased plan below are **agent-recommended**, not additional
operator requirements. Capture checked against the original request:
open/resume and listings remain required, and residual provider risk is not
silently treated as repaired.

## Scope Approvals

Source improvements, bounded isolated validation and normal reviewed
publication are in scope. This record grants no rollout, shared-host mux
probe, service change, restart/teardown, terminal takeover, registry repair,
credential export or access change. A reviewed plan does not authorize those
effects. Native provider execution belongs only in a qualified disposable
environment, never a shared host merely given a different mux namespace.

## Plan

The following implementation sequence is agent-recommended.

### Phase 1 - Review the functional-continuity contract

- [x] Land this plan after review, retaining complete-flow acceptance (#11547).

### Phase 2 - Deliver and prove one complete consumer flow

- [ ] Qualify a bounded, authenticated native PSMux observation inside the
  existing Driver owner; return fresh presence and available client counts,
  explicit absence only when established, and unknown/unsupported otherwise.
  Do not launch PSMux, repair files, enumerate unrelated process state, expose
  keys or add a background observer/parallel registry.
- [ ] Integrate Driver creation/check-or-join, Worktrees Open/resume and Manager
  discovery/listing with that observation. Existing mux means reattach, not
  duplicate creation or replay; unknown cannot become death or authorize
  destructive Restore/Reclaim. Preserve explicit foreground attach/create
  actions and disclose their remaining provider startup risk.
- [ ] Preserve tmux behavior and independently updated callers. Advertise the
  actual provider's supported finite contract; unsupported older peers retain
  an honest degraded result without falling back to passive PSMux CLI probes.
- [ ] Update living Driver/Worktrees/Manager guidance and publish the complete
  functional flow through required source checks, CI and review to merge.

### Phase 3 - Reconcile completion

- [ ] Re-read the canonical effort after implementation merge, reconcile all
  Plan/Validation items and journal merged work without claiming deployment.
- [ ] Archive Done through review after every item passes or has an explicit
  named tracked transfer; retain any blocked native qualification honestly.

## Validation Plan

These validation obligations are agent-recommended.

- [ ] Exercise the real finite wire/consumer boundary with synthetic native
  endpoints: fresh present and client counts, absent registry, stale endpoint,
  authentication refusal, timeout, malformed/oversized response, changed session
  identity and unsupported provider. Assert zero passive PSMux subprocesses,
  zero registry mutation and no key/command leakage.
- [ ] Prove cold Open creation and existing-session resume/reattach through
  Worktrees and Manager entry points, not only helper functions. Check no
  duplicate creation/seed, unknown-vs-absent handling, useful listing status,
  conservative Restore/Reclaim, and mixed-version/tmux compatibility.
- [ ] Run focused unit and simple subprocess integration tests, touched-code
  lint and applicable repository/install-contract checks. Stage new files
  before contained runners snapshot the source.
- [ ] Qualify the exact candidate against native PSMux in a disposable Windows
  lane, including fresh first-touch, existing-session reattach, independent
  attachment preservation and responsive listings. Never use a shared live
  host as this lane. If unavailable, record the unpassed gate and its concrete
  tracked owner rather than substituting a protocol fake or claiming success.
- [ ] Record elapsed observation/listing and open/resume overhead using the
  consumer path; no per-render provider subprocess or unbounded polling.
- [ ] Pass required CI/review at the exact published head, verify merge, and
  distinguish merged source, release promotion and separately authorized rollout.

## Proposal

Use the native authenticated query path as a finite Driver adapter, reusing
provider-owned endpoint files only as rendezvous hints. Validate the response
against the requested session before reporting live presence; failure is
unknown, not absence. Keep the existing creation lease and uncertain-outcome
protection. Select the adapter through the existing Driver core boundary,
not Manager-specific socket code. Protocol details and compatibility are
implementation proof obligations, not claims that native qualification passed.

## Journal

### 2026-11-08 - Close the catalog publication race

- Candidate `63d4fef294` passed all eight native cases on PSMux 3.3.8
  (86.71 seconds) and all eight on PSMux 3.4.0 (76.66 seconds), with required
  CI green. The subsequent review confirmed the pane, recovery, namespace and
  Finalize findings resolved.
- Complete catalog discovery must include live identities confirmed after
  its initial directory scan. It now unions the qualified inventory with
  directory hints before namespace filtering, instead of discarding identities
  published between those reads.
- Regressions publish a real synthetic endpoint during inventory, after the
  directory scan, in both default and explicit namespaces. All 29 focused
  wire cases passed; unrelated namespaces remain excluded. Fresh native/CI
  evidence and a passing review remain required for the new source.

### 2026-11-08 - Preserve pane, recovery and namespace boundaries

- The final authenticated identity now names the current pane. Window-local
  active flags cannot make a normal multi-window session lose its current-pane
  target; a two-window wire regression covers that distinction.
- Authoritative Windows batches raise on unobserved rows. Only the display
  API retains partial unknown facts; reclaim reachability and detached-server
  teardown tests verify that unknown never authorizes selecting live targets.
- Driver absence uses namespace-qualified registration identities while still
  verifying every native server. The strict legacy name-only inventory retains
  its duplicate-name ambiguity refusal.
- Preserved the existing non-managed Finalize menu entry, whose executor owns
  eligibility checks, without enabling Restore/Reclaim/Stop on unknown rows.
- Focused contained results: Driver wire 27 passed, Worktrees 113 passed with
  one POSIX-only skip, Manager wire/Picker 28 passed; all 17 changed source
  modules meet the unchanged ceilings. Fresh native qualification, CI and a
  subsequent passing review remain required before merge.

### 2026-11-08 - Both native versions qualify

- Candidate `06aa22c3c4` passed all eight native cases on PSMux 3.3.8
  (63.03 seconds) and all eight on PSMux 3.4.0 (61.33 seconds). Cold Open,
  same-pane reattach, independent attachments, fresh listings, explicit
  retirement and both concurrent launch modes passed with the original bounds.
- Reconciled concurrent effort-index changes without losing either entry;
  range-diff retained the implementation patches.
- Required CI then identified one excess line in the main module against its
  shrink-only ceiling. Moved enumeration outcome handling into the existing
  monitor runtime helper instead of widening the ceiling. Nine focused native
  read-only/tmux publication and failure cases passed; all 17 changed source
  modules meet their existing limits. Required CI/review remain merge gates.

### 2026-11-08 - Resume existing PR and resolve legacy framing

- Resumed existing-PR stewardship. Exact candidate `d3ba7acc2c` again passed
  PSMux 3.4.0; PSMux 3.3.8 consistently returned only pane rows across the
  existing first-client wait. Failure diagnostics rejected the transient-read
  hypothesis: its compound request did not return client or final identity
  replies.
- Split the three fixed read-only queries across separate authenticated
  connections, retaining one deadline, aggregate byte cap, final incarnation
  fence and unchanged rendezvous checks. This corrects the observed framing
  mismatch rather than weakening unknown or extending the acceptance budget.
- Focused contained wire checks passed 24 cases, including a one-command
  endpoint, shared deadline and authentication refusal on a later reply.
  The shipped Manager finite-consumer checks passed five cases. Exact-source
  native qualification and review remain required before merge.

### 2026-11-08 - Current-provider flow qualified; older-provider gate retained

- Candidate `942592ce88` passed all eight native PSMux 3.4.0 consumer cases
  in 77.86 seconds, including cold Open/reattach/listings and both concurrent
  launch modes. Its required CI passed.
- PSMux 3.3.8 passed seven cases; cold Open reached pane acknowledgment, then
  the first immediate socket observation returned unknown. The fixture now
  waits for the required first client within its existing 40-second bound,
  retaining the same count/identity assertions and failure diagnostics.
  This correction has not yet passed native qualification; unknown is not
  treated as presence.
- Focused local wire checks passed 23 cases, Manager finite-consumer checks
  passed five, and the complete daemon census/module-size checks passed.
- Preserve the draft implementation PR and this active effort. During the
  operator-directed pause, do not start a completion stretch or another manual
  native dispatch. Resume with exact-candidate older-provider qualification;
  required review and merge remain outstanding. No rollout occurred.

### 2026-11-08 - Native boundary diagnosis

- Disposable PSMux 3.4.0 proved cold Open, same-pane reattach, two independent
  clients, fresh consumer listings and exact retirement. Its concurrent
  warm-server case remained uncertain after dispatch; no creation was replayed.
- The same packet on PSMux 3.3.8 passed both concurrent launch cases but
  exposed unsupported identity fields in its client-list formatter. Restricted
  client rows to its supported session name and added a final authenticated
  identity marker, matching the pane rows before accepting any client count.
  Missing, changed or trailing identity boundaries remain unknown.
- Focused synthetic wire regressions passed 23 cases. Native failure output
  now retains bounded fixed-query replies and complete-inventory reasons,
  excluding authentication, requests and pane argv. This is diagnostic evidence,
  not a relaxation of the native acceptance deadline.
- Earlier acquisition failures occurred before tests; the public release-API
  fallback retains the same pinned digest. Required CI also found a stale
  process-launch census after removing a launcher subprocess; corrected the
  census to the actual three sites without exempting any remaining launch.
- Exact-source native qualification, required CI and review remain pending.
  No shared-host observation, installation or deployment occurred.

### 2026-11-08 - Candidate ready for disposable native qualification

- Reconciled concurrent #11549 instead of replacing its safe cold-stop proof.
  Hoisted its complete native inventory into Driver with existing Worktrees
  imports retained. Missing registrations alone cannot establish absence;
  incomplete native server coverage stays unknown. Only provider processes
  are inspected; no unrelated process data is retained.
- Preserved strict prompted-Resume behavior and added fresh positive pane/client
  observation for listings and Open. The existing final-cold pane check now
  uses Driver too, without a passive CLI escape.
- After reconciliation, the contained upstream strict/listing/admission packet
  passed 108 cases with one POSIX-only skip. Native qualification remains pending.
- Implemented finite Driver socket observation, selected-provider PowerShell
  reads, Worktrees presence/listing and Manager discovery/client refresh.
  Unknown observations still prevent duplicate creation and destructive recovery.
- Kept periodic PSMux CLI painting disabled. Fresh monitor catalog/roster
  publication does not re-enable discovery or provider writes. Foreground
  attach/create retains the disclosed provider startup risk.
- Contained checks: Driver wire/creation 41 passed; Worktrees listing/admission
  117 passed, one POSIX-only skip; monitor preservation three passed; Manager
  status/wire 107 passed, with discovery/launch regressions separately passing;
  focused workflow contract passed. Install contract and touched-code lint passed.
- Native Windows qualification remains unpassed. The focused `mux-continuity`
  portfolio supplies digest-pinned PSMux 3.4.0 and actual cold Open/reattach,
  independent-client and consumer-listing assertions. Publish a draft candidate,
  run that exact branch, then resolve native results before readiness/merge.
- No shared-host PSMux probe, rollout, service change or retained-source
  finalization occurred.

### 2026-11-08 - Plan accepted

- #11547 merged after Copilot approval and successful required checks. Pulled
  forward onto the reviewed plan before implementation.
- Next: qualify finite Driver-owned native observation and connect the complete
  Open/resume/list consumer flow. No live shared-host probes or rollout.

### 2026-11-08 - Kickoff

- Claimed #11544 after checking existing open scopes. Existing broader owners
  remain intact.
- Public provider source exposes a non-CLI query candidate; no live provider
  probe or implementation was performed.
- Plan submitted before execution. Original request and source-only limits
  checked; no deployment or cleanup authority inferred.
