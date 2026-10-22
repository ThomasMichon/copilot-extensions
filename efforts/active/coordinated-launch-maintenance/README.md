# Coordinated Launch Maintenance

- **Slug:** `coordinated-launch-maintenance`
- **Repo:** copilot-extensions
- **Branch(es):** managed per-slice PRs against `dev`
- **Created:** 2026-10-21
- **Status:** Draft
- **Vision:** `visions/installer/README.md` (adopted out-of-band automatic maintenance); `visions/picker/README.md` (responsive session launch)
- **Umbrella issue:** #7694

## Guiding Intent

An already-healthy session must not wait for unrelated runtime installation.
Keep adopted automatic maintenance available, coordinated and observable rather
than disabling updates or starting an independent updater for every launch.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Launch driver | Design, implementation, review, release and qualified acceptance | Managed source `welded-circuit`; #7694 |

## Coordination

- **Topology:** sequential source PRs against `dev`.
- **Host (owns PRs):** Launch driver.
- **Delegates:** none.
- **Handoff:** canonical effort and #7694; no silent ownership transfer.
- #6812 retains formatting, telemetry and operation-cost audit ownership.
- #6820 retains historical-command migration and its paused constructor handshake.
- #7667 retains its contributor-owned installer/context implementation.
- #6237/#3336 retain release control. This effort does not authorize retries,
  installed mutation, service restarts or another concurrent updater.

## Context

The launch scripts stage a marketplace download but join it before handoff,
then run bootstrap maintenance and two plugin-reconcile passes synchronously.
Only one core installer branch is detached. Several healthy launches can
therefore wait behind slow installation; a detached child alone does not prove
coalescence, distinct-project delivery or survival of caller Job cleanup.

This closes the installer's existing adopted, out-of-band maintenance intent,
using the process-lifetime and operation-custody patterns, not a new service
framework. No standing daemon, OS registration or changed dependency policy
is needed for bounded on-demand maintenance.

## Request

Public-safe literal excerpt: "Installation is just dragging out and blocking
other worktree starts".

Settled direction (summary): use coordinated-background automatic maintenance,
not permanent update opt-outs; preserve the running installation and do not
start another updater as an investigative shortcut.

Confirmed effort slug: `coordinated-launch-maintenance`.

## Plan

### Phase 1 - Reviewed contract
- [ ] Land this plan through ordinary review before implementation.
- [ ] Coordinate the bounded scope on #6812/#6820 without taking over their owners.

### Phase 2 - Coordinated maintenance

The following implementation choices are **agent-recommended** mechanisms for
the requested coordinated-background behavior.

- [ ] Move optional stage/bootstrap/reconcile work off the healthy handoff path
  on both PowerShell and Bash; remove the join and inline-download fallback
  from ordinary launches. Preserve explicit blocking recovery behavior.
- [ ] Use one bounded on-demand owner and durable pending requests. Coalesce
  equivalent requests without losing a different project, repository, source,
  installation context or stronger requested operation.
- [ ] Share operation custody with explicit update/reconcile entrypoints;
  retain canonical installer locks and execution admission. A busy owner is
  pending work, not permission for another installer.
- [ ] Preserve exact action argv, scoped environment/unsets and project
  context. Re-plan against the current installed state rather than execute a
  stale or foreign action. Keep runtime replacement and retention safety.
- [ ] Use the canonical headless lifetime primitive, explicit logs and durable
  working directory. No inherited-Job fallback presented as durable success;
  preserve contained-test suppression and bounded child-tree cleanup.
- [ ] Make queued/running/failed/completed outcomes inspectable and truthful.
  Interrupted work must not be reported complete or retried over a possibly
  surviving child without ownership/completion evidence.
- [ ] Preserve `WORKTREE_NO_UPDATE` and `WORKTREE_NO_RECONCILE` independently,
  explicit update behavior, clean JSON/stdout and required first-use safety.
  Missing prerequisites cannot be called a healthy launch merely to avoid waiting.

### Phase 3 - Release and acceptance
- [ ] Land source, documentation and changefiles through review and required CI.
- [ ] Observe legitimate promotion, then use the authorized normal rollout
  owner; do not start a competing update to finish this effort.
- [ ] Record installed handoff, maintenance completion and failure evidence.
  Close this effort only when every validation item is resolved or explicitly
  transferred to a named tracked objective.

## Validation Plan

All detailed scenarios below are **agent-recommended** acceptance checks.

- [ ] Unit tests cover queue equivalence, distinct contexts, stronger requests,
  owner contention, crash/ambiguous-child handling and truthful terminal states.
- [ ] Real shell fixture holds an installer behind a gate while two healthy
  launches reach handoff before that gate opens; maintenance remains automatic
  and single-owned. Measure handoff independently of installer completion.
- [ ] Prove a different project's pending request is eventually processed,
  and manual update/reconcile cannot overlap its mutation boundary.
- [ ] Exercise exact argv/environment, admission refusal, malformed plans,
  failed spawn, failed installer, opt-outs and explicit blocking mode.
- [ ] Prove first-use/missing-prerequisite handling in an isolated fresh home.
- [ ] On Windows, exercise the actual production worker spawn from a
  kill-on-close Job, close caller containment after acknowledged dispatch,
  and independently observe continued maintenance. Verify bounded-child
  control reaping, no visible console/focus change, and owned-tree cleanup.
- [ ] Exercise native POSIX handoff, owner exit and pending-request delivery.
- [ ] Run targeted contained suites, lint, install-contract and source guards;
  record native/live lanes separately from mocked wiring.
- [ ] Verify a promoted installed release through the normal authorized flow;
  a payload version or queued receipt alone is not installed/running proof.

## Proposal

No new permanent service or periodic scheduler. The launcher submits optional
work to its installed owner's bounded coordinated maintenance path and proceeds
on the healthy retained runtime. The owner drains admitted requests using the
existing planners/installers and then exits. Coordination must cover explicit
mutation entrypoints as well as launch requests; a lock around only the new
worker is insufficient. Machine-local request/status/log data never enters
repository history.

## Journal

### 2026-10-21 - Kickoff

- Traced foreground stage join, bootstrap and two-pass reconciliation on both
  shells. Existing progress work did not create the serial maintenance path.
- Claimed #7694 and created isolated source `welded-circuit`; no installed
  updater, running launch, service or registry was changed.
- Confirmed the effort slug. Compared the captured request and settled
  direction with the operator input; implementation/test mechanisms are
  explicitly marked agent-recommended.
