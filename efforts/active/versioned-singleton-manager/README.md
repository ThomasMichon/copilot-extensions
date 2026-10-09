# Versioned Singleton Manager

- **Slug:** `versioned-singleton-manager`
- **Repo:** copilot-extensions
- **Branch(es):** Serial pull requests against `dev`.
- **Created:** 2026-10-08
- **Status:** Active
- **Vision:** `visions/plugin-services` / `cutover-coherent-service-tracking`
  and `zero-downtime-cutover`. Vision-closing.
- **Umbrella issue:** [#5655](https://github.com/ThomasMichon/copilot-extensions/issues/5655)
- **Related:** [design PR #5556](https://github.com/ThomasMichon/copilot-extensions/pull/5556),
  [supervisor restart accounting #5574](https://github.com/ThomasMichon/copilot-extensions/issues/5574).

## Guiding Intent

Keep native service-manager tracking coherent while a versioned daemon cuts
over to a replacement. A reviewed design is not a running implementation:
complete the shared primitive, consumer integration, and real platform proof
before declaring the capability delivered.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Implementation driver | Shared primitive, consumer integration, serial PR ownership | Owning implementation worktree |
| Linux validation venue | Real process boundaries and systemd restart accounting | Isolated systemd-capable test container |
| Windows validation venue | Native Job and Scheduled Task proof | Dedicated Windows test venue |

## Coordination

- **Topology:** Serial, independently reviewable slices.
- **Host (owns PRs):** Implementation driver.
- **Delegates:** Bounded evidence or test work only; no concurrent edits to
  shared primitive or consumer callback contracts.
- **Handoff:** Preserve this README, the current phase, open PRs, and test
  evidence. Resume the first unresolved Plan/Validation Plan item.

## Context

The [versioned singleton manager pattern](../../../docs/patterns/versioned-singleton-manager.md)
defines the intended shared capability. An early prototype exists outside the
published source and predates much of the design. It is reusable evidence,
not accepted implementation or current platform validation.

`CutoverOrchestrator` invokes adopter-owned `spawn_passive` callbacks; it does
not create the processes itself. Integration must cover the dispatch
coordinator and bridge venue callbacks and their launchers, not only `zdd`.
Windows must remain explicitly unavailable until its native ownership and
handoff implementation passes its platform gate.

## Request

Public-safe summary of the continuing request: implement the reusable
versioned singleton manager, wire it into daemon supervision, and prove
cutover/crash behavior with unit tests, stress tests, a systemd container,
and a real Windows host. Continue until the implementation and validation
are complete, rather than stopping after the design PR.

## Plan

### Phase A - Shared identity and Linux manager

- [x] Recover and assess the prototype; reuse only mechanisms that meet
  the current contract. The old best-effort identity and PPID-only paths
  are not reused as a production backend.
- [x] Add backward-compatible publication-time identity. Landed in
  [PR #5803](https://github.com/ThomasMichon/copilot-extensions/pull/5803),
  including preservation through rollback/watchdog promotion.
- [ ] Require the published baseline and proven ownership before adopting
  successors; reject unverifiable candidates rather than sampling a new
  identity from an already-reused PID.
- [ ] Implement distinct routing and manager-state paths, atomic versioned
  state, singleton ownership, ancestry checks, stable identity baselines,
  zombie reaping, bounded successor discovery, and crash cleanup.
- [ ] Implement marker-driven Linux re-exec while polling the child; restore
  the validated watched process without invoking `spawn` a second time.
  Preserve pending cutover discovery across the exec boundary.
- [ ] Keep unsupported-platform handling explicit and fail closed; no
  best-effort Windows facade over the superseded PPID-walk prototype.
- [ ] Synchronize vendored consumers and add the required changefiles.

### Phase B - Windows ownership and handoff

- [ ] Implement typed native-handle ownership and a shared suspended-spawn
  registration primitive. Cover the first daemon, every passive generation,
  and the breakaway deploy orchestrator; no escaped intermediary may evade
  manager-crash cleanup.
- [ ] Define authenticated, reconnectable registration endpoints across daemon
  and manager generations; bind peer identities and handle rights rather
  than trusting claimed numeric handle values or same-user access alone.
- [ ] Implement Job configuration, containment verification, explicit
  re-assignment, scoped handle inheritance, and failure cleanup.
- [ ] Implement a durable phased handoff, serialized with fresh startup and
  terminal cleanup. Persist preparation before a bridge can preserve the
  Job; use a kernel synchronization primitive, not assumed unschedulable
  adjacent filesystem/handle operations.
- [ ] Preserve trusted daemon custody through the bridge, a bounded
  ownership-transfer deadline, and fail-closed cleanup even after a partial
  successor acquires its own Job handle.
- [ ] Re-run the registered task after successful intentional retirement;
  account for `IgnoreNew`, native task-state lag, and repeated updates without
  consuming crash-restart accounting.

### Phase C - Consumer integration

- [ ] Integrate both adopter-owned passive-spawn callbacks with the shared
  contract; preserve unmanaged launch behavior explicitly.
- [ ] Wire stable Linux launchers/systemd units and attached, windowless
  Windows Task actions to the manager; preserve normal endpoint discovery,
  environment snapshots, and version resolution.
- [ ] Align pattern documentation with the implemented contract, removing
  contradictory trust or cleanup claims without weakening the vision.
- [ ] Resolve #5574 or explicitly transfer its separate validation obligation
  to a named tracked objective before closing this effort.

### Phase D - Platform proof and release

- [ ] Land each slice through normal review; record actual merged outcomes.
- [ ] Run isolated systemd and native Windows proofs, including installer
  launch shapes, not only mock/native helper tests.
- [ ] Verify release and consumer deployment under the normal update flow;
  retain all required approval gates for live service changes.

## Validation Plan

- [ ] Unit proof: stale PID, missing identity, same-PID replacement between
  polls, publication/registration races, malformed state, and foreign routes
  never authorize adoption or termination of unrelated processes.
- [ ] Real Linux proof: repeated detached descendant cutovers, intermediate
  exits, child zombies, exec during cutover, and no duplicate spawn on exec.
- [ ] Crash/stress proof: bounded orphan cleanup, repeated/coincident
  cutovers, injected manager death, and cleanup while descendants fork.
- [ ] Real systemd proof: service MainPID remains coherent across cutovers;
  hard manager death leaves no old cgroup survivor before restart.
- [ ] Native Windows proof: consecutive daemon and manager updates retain
  Task tracking and Job containment; inherited/registered handles do not leak.
- [ ] Windows failure proof: missing readiness, interrupted preparation,
  bridge death, rejected Job assignment, task-state lag, partial adoption,
  and deadline expiry cannot leave an untracked live daemon indefinitely.
- [ ] Focused adopter regressions, vendored consistency, and required guards
  pass under the bounded test runner.
- [ ] Every platform claimed production-ready has real platform evidence;
  unresolved items remain open or use an explicit named transfer.

## Proposal

Serial Linux-first delivery, followed by native Windows implementation and
both consumer integrations. State-machine and source-identity refinements
above are **agent-recommended implementation hardening**, not new operator
requirements or permission to weaken the existing vision.

## Journal

### 2026-10-08 - Implementation resumed

- Design PR #5556 is merged; #5655 remains open.
- Established this target-local implementation contract. No manager code
  or platform production-readiness claim is delivered by this proposal.

### 2026-10-08 - Identity foundation landed; Linux manager implementation

- Plan PR #5801 and publication-identity PR #5803 are merged.
- The next slice implements a Linux-only pidfd-backed manager, durable
  state/lease recovery, bounded discovery, and actual re-exec/cutover
  subprocess proof. It does not wire a production launcher or close the
  systemd/Windows validation gates.
