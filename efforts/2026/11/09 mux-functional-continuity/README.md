# Mux Functional Continuity

## Goal

Keep native Open/resume and useful, truthful mux status working while avoiding
PSMux client startup for passive observation.

- **Slug:** `mux-functional-continuity`
- **Repo:** copilot-extensions
- **Branch(es):** managed per-phase PRs targeting `dev`
- **Created:** 2026-11-08
- **Completed:** 2026-11-09
- **Status:** Done
- **Vision:** [Session Hosting](../../../../visions/session-hosting/README.md);
  [Agent Remote Driver](../../../../visions/plugins/agent-remote-driver/README.md)
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
- **Handoff:** no remaining implementation slice. Normal release promotion
  (#3336) and separately authorized installed adoption remain independent.

## Context

PSMux v3.4.0's [client entrypoint](https://github.com/psmux/psmux/blob/v3.4.0/src/main.rs)
performs cleanup before ordinary query/control-mode handling.
Its [session module](https://github.com/psmux/psmux/blob/v3.4.0/src/session.rs)
also exposes authenticated, finite socket queries against native session
endpoints. The delivered adapter qualifies that existing path without starting
another CLI to observe or adding a parallel registry/control service.

The existing Driver core owns finite mux creation. Worktrees retains durable
agency and Manager retains presentation. A process, an endpoint file, a cached
mapping and successful prompt admission are not interchangeable mux evidence.

This closes a bounded Session Hosting/Driver vision delta, rather than
extending their ownership model. The implementation follows the existing
[patterns](../../../../docs/patterns/README.md), especially component ownership,
honest observations and
[independently updated contracts](../../../../docs/patterns/service-contract-evolution.md).
The broader #10627 creation readiness, #7212 attachment/process observations,
#8669 installation-independent sessions, #10765 core convergence and #4564
hosting owners remain intact; this effort does not claim their acceptance.

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

- [x] Qualify bounded, authenticated Driver-owned native observation: fresh
  presence/client counts, complete-coverage absence, unknown/unsupported
  otherwise; no passive CLI, registry repair, key exposure or new observer.
- [x] Integrate creation/check-or-join, Worktrees Open/resume and Manager
  discovery/listing; preserve same-pane reattach, seed custody, recovery
  uncertainty and the explicit foreground startup-risk disclosure.
- [x] Preserve tmux and independently updated callers through actual selected
  provider support, with honest degradation and no passive CLI fallback.
- [x] Update living guidance and merge the complete flow through source checks,
  native qualification, required CI and approving review (#11564).

### Phase 3 - Reconcile completion

- [x] Re-read the canonical effort after implementation merge, reconcile
  Plan/Validation items and journal merged source without claiming deployment.
- [x] Prepare the dated Done archive and completion delta for the repository's
  normal review/merge gate; every implementation and validation item is resolved.

## Validation Plan

These validation obligations are agent-recommended; evidence is linked from
the [completion journal](journal/2026/11.09%20source-completion.md).

- [x] Exercise real finite wire/consumer boundaries: presence/counts, absence,
  stale/refused/malformed/oversized/changed/unsupported evidence; zero passive
  CLI, registry mutation and key/command leakage.
- [x] Prove cold Open and same-pane resume/reattach through consumer entry
  points, seed preservation, truthful listings, recovery refusal and
  mixed-version/tmux compatibility.
- [x] Pass focused unit/simple subprocess integration, touched-code lint and
  applicable repository/install-contract checks.
- [x] Qualify the exact source on disposable native Windows: fresh first-touch,
  reattach, independent attachments and responsive listings on PSMux 3.3.8/3.4.0;
  no shared live-host substitute.
- [x] Record consumer Open/reattach/listing timings and enforce bounded
  observation without per-render provider children or unbounded polling.
- [x] Pass required CI and approving review at the published implementation
  head, confirm merge, and keep source, release and installed state distinct.

## Journal

- [2026-11-09 - Source completion and validation](journal/2026/11.09%20source-completion.md)
- [2026-11-08 - Implementation and qualification](journal/2026/11.08%20implementation.md)
