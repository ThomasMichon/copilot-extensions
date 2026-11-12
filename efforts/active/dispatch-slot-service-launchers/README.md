# Dispatch Slot Service Launchers

## Goal

> The file lock implies this binstub improperly gets held open instead of quickly dispatching into a version-slot binstub. The agent-dispatch incoming setup flow should be entitled to lock-break, usually be killing the outgoing process, if this situation occurs

The requested source behavior is a short-lived stable service entrypoint,
version-slot execution, and an incoming installer's bounded ability to retire
an outgoing central-service process that blocks replacement. The observed
exception alone does not establish its handle owner. Installer-owned readers
must close; retiring another process is not a substitute for that correction.

- **Slug:** `dispatch-slot-service-launchers`
- **Repo:** copilot-extensions
- **Branch(es):** separate proposal and implementation PRs against `dev`
- **Created:** 2026-11-11
- **Status:** Draft
- **Vision:** [authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement)
- **Umbrella issue:** #11875
- **Related ownership:** #11960 reader disposal; #10847 central retirement;
  #10377 upgrade recovery; #5579 wrapper lifetime

## Guiding Intent

Close existing authoritative replacement intent, not introduce another
installer, service manager, reaper, scheduler or generic process-killing command.
Known generated stable launchers must not retain an open file handle for the
service lifetime. Long-lived coordinator and supervisor execution belongs to
the selected immutable runtime generation. Applicable entrypoints must select
the incoming ready generation, while outgoing execution and protected workers
retain their own explicit lifetime and state.

An incoming setup may resolve a real publication conflict through the existing
bounded central-service replacement owner. Preserve native process identity,
installation scope, explicit service role and then-current generation checks
at the termination boundary. Unknown/foreign holders, session processes,
current or reselected generations and later successors are not targets.
A real unresolved OS publication failure remains a failure, not a false
successful installation or an indefinite predecessor-recovery wait.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Launcher driver | Proposal, distinct launcher-lifetime implementation, contained qualification and PR stewardship | Managed source `polished-kiln`; #11875 |
| Reader-fix contributor | Existing first-line reader disposal and its native qualification | #11960; ownership retained |
| Lifecycle owners | Existing central generation retirement and state-preservation contracts | #10847 and the installer contract; no transfer |

## Coordination

- **Topology:** reviewed proposal first, then a separate bounded implementation.
- **Host:** Launcher driver owns this effort and its source PRs only.
- **Delegates:** none.
- **Overlap:** do not duplicate, replace, merge prematurely or supersede #11960.
  Keep its reader correction and native validation hold with its contributor.
  Sequence intersecting installer changes after its accepted source, or obtain
  an explicit nonoverlapping edit agreement through that contributor.
- **Handoff:** this README, #11875, exact source/test evidence and outstanding
  dependencies; a plan or source merge is not rollout.
- **Parent:** this is a prerequisite slice, not ownership of the wider
  PR-service, provider-conformance, upgrade-recovery or retirement campaigns.

## Context

Dispatch's generated `serve-service.ps1` and `supervise-service.ps1` contain
runtime selection and long-lived invocation. A changed-content refresh can
fail in the atomic publisher after a coordinator cutover. The unchanged-byte
optimization does not prove that changed bytes can publish.

There are two distinct boundaries: the installer's first-line reader may itself
retain a handle (#11960), and an outgoing service wrapper may hold stable code.
Do not label either as the observed holder without native evidence.
Reuse [Windows scheduling](../../../plugins/agent-dispatch/docs/windows-scheduling.md),
the [installation contract](../../../docs/install-contract.md#authoritative-replacement-and-completion)
and [graceful cutover](../../../docs/patterns/graceful-daemon-cutover.md).

## Request

The Goal preserves the complete public-safe operator request verbatim.
Implementation was paused for unavailable Git tooling; a subsequent
"Check status here and resume" resumes this source assignment after tooling
restoration. It does not erase the earlier host update's failed outcome or
authorize another live update.

## Scope Approvals

- Source development, disposable caller-contained native fixtures, ordinary
  reviewed source publication and release observation are in scope.
- No shared service termination, installed-runtime patch, host repair,
  configuration/credential change, update retry or rollout is authorized here.
- A review accepts source design, not new host or product effects.
- Preserve the existing reader contribution, independent native holds, original
  parent acceptance and resources; neither this slice nor a phase closes them.

## Plan

The following decomposition is **agent-recommended**, not additional operator
wording or authority.

### Phase 1 - Review and establish the real lifetime boundary

- [ ] Review this proposal and reconcile intersecting ownership with #11960.
- [ ] Trace actual coordinator and supervisor entrypoints, slot retention,
  scheduled-task behavior and existing native retirement primitives. Record
  which reader/process holds a deny-delete handle in each isolated control.

### Phase 2 - Prove version-slot execution and replacement

- [ ] Publish immutable slot-local service launchers through the existing
  construction owner; reduce stable entrypoints to bounded selection/forwarding
  with all their file handles closed before long-lived execution.
- [ ] Preserve arguments, environment/configuration, logging, `-ResolveOnly`,
  scheduling/start policy, native containment and service exit semantics.
  Do not detach a child whose task or cleanup owner cannot retain custody.
- [ ] Compose existing incoming central-service retirement for an actual
  outgoing-process publication conflict. Bound the transition and recheck
  native identity, role, root and then-current selection before effects.
  Close self-held readers rather than trying to kill around them.
- [ ] Preserve custom launchers, unrelated holders, session/worker children
  and current/later generations. Surface target path and truthful incomplete
  publication when the remaining OS conflict cannot be resolved.

### Phase 3 - Publish and reconcile the slice

- [ ] Refresh directly affected documentation and changefiles; pass focused
  unit/native and mandatory source checks, then normal PR review and merge.
- [ ] Observe actual Main release separately from Dev merge. Record final source
  acceptance and any unavailable lanes; do not claim installed/live acceptance.
- [ ] Resolve every Plan/Validation item or transfer it to a named tracked
  objective before Done; preserve the parent gate and deployment hold.

## Validation Plan

- [ ] Failure-first controls distinguish a self-held header reader from a live
  outgoing wrapper's deny-delete handle on Windows PowerShell 5.1 and PowerShell 7.
- [ ] Exercise the actual generated stable entrypoint and slot launcher in a
  disposable root: the service stays functional while stable bytes are replaced,
  invocation/configuration survive, and owned processes settle on exit.
- [ ] Verify incoming setup retires only an identity-pinned outgoing central
  fixture when it actually blocks publication, and then publishes/selects the
  ready generation. Prove the conflict is resolved, not merely a pointer changed.
- [ ] Refuse/skip termination for foreign-root, unknown-role, reused-PID,
  current/reselected/later generations and protected session/worker controls.
  Unrelated deny-delete holders produce honest bounded publication failure.
- [ ] Prove fresh construction and old stable-launcher upgrade through real
  consumer boundaries, not only helper mocks; preserve database/configuration
  fixtures and scheduling policy.
- [ ] Check headlessness and caller containment with exact owned cleanup.
  Do not weaken existing deadlines/assertions or waive #11960's native hold.
- [ ] Run focused tests, touched-Python Ruff, installation-contract and required
  CI/review. Record clean-room and live-venue applicability and omissions.

## Proposal

Pending review. No implementation or rollout is claimed.

## Journal

### 2026-11-11 - Resume after tooling restoration

- Git is available again. Current #11960 remains open with an explicit native
  supervisor qualification hold; its contribution is not duplicated here.
- Claimed this distinct launcher-lifetime scope on #11875. The existing installer
  vision already requires authoritative incoming selection and bounded,
  central-only outgoing retirement; no vision extension is needed.
