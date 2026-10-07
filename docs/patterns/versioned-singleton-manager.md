# Pattern: versioned singleton manager (service-manager stays coherent across a cutover)

> **Serves the vision:** *plugin-services* §Behaviors/`cutover-coherent-service-tracking`.
> **Companion patterns:** [`graceful-daemon-cutover`](graceful-daemon-cutover.md)
> (the drain/flip/retire protocol this pattern wraps, unchanged),
> [`service-lifecycle-supervision`](service-lifecycle-supervision.md) (the
> register-once launcher this pattern sits directly behind),
> [`process-slot-ownership`](process-slot-ownership.md) (generation self-retire
> and abandoned-passive reap — the *daemon-side* half of staying reconciled;
> this pattern is the *service-manager-side* half).
> **Origin:** aperture-labs effort `agent-daemon-live-update`, Phase 4 — a live
> incident where a coordinator's self-update cutover left its systemd unit
> crash-looping against its own already-live, untracked successor.

## The problem

[`graceful-daemon-cutover`](graceful-daemon-cutover.md) already solves
zero-downtime for a service's **external** behavior: the new version is
health-gated, the routing table flips, the old version drains and retires —
clients never see a dropped request or a double-run job. But a
**spawn-new-retire-old** cutover (as opposed to a restart-in-place) is
necessarily **two overlapping processes**, and the host's native service
manager (a systemd unit, a Windows Scheduled Task) tracks only **one**. When
the manager's own tracked process is the one that retires, the manager's
restart/failure accounting has no way to know the retirement was intentional —
it sees its process exit and, per `Restart=on-failure` (or the Scheduled
Task's own restart policy), tries to start a fresh instance. That fresh
instance is then correctly refused by the service's own singleton guard (a
`CoordinatorAlreadyLiveError`-style check), since the cutover's survivor
already holds the active route — outside the service manager's own process
tree, unreachable by it, and already live.

The result: a restart loop that never resolves on its own. The cost is small
(each refused start exits almost immediately), but the service manager's own
bookkeeping is now **permanently wrong** — every future reboot inherits the
same mismatch, since the manager has no record of which process, if any, is
actually the live one.

This is not a new problem for *this suite*: the lighter
[`service-lifecycle-supervision`](service-lifecycle-supervision.md)
restart-in-place shape (the embody supervisor's `supervise serve`) already
solves it for daemons with **no in-flight request to drain** — wind down owned
units, release the single-instance lease, spawn a successor **with the same
argv**, then exit with a sentinel code (`RELOAD_EXIT_CODE`) the service
manager is configured to treat as "restart me, don't count this as a failure"
(systemd: `SuccessExitStatus=`/`RestartForceExitStatus=`; Windows: the
launcher's own restart-loop recognizes the code). That trick works precisely
*because* there is only ever one process at a time — the successor **is** the
thing the service manager restarts into.

A daemon that owns a shared endpoint and in-flight, non-resumable work (the
coordinator) cannot collapse to that single-process shape — the whole point of
`graceful-daemon-cutover` is the brief overlap. This pattern is what closes the
gap for *that* case, without touching the already-correct cutover protocol
itself.

## The shape

```
service manager (systemd unit / Scheduled Task)
      │  tracks this PID PERMANENTLY -- across every cutover, forever
      ▼
versioned singleton manager        ← NEW: thin, version-agnostic, never itself updated
      │  spawns, as its own child
      ▼
versioned daemon (coordinator)     ← the thing that actually gets replaced
      │  self-update / installer activation triggers graceful-daemon-cutover,
      │  spawning a NEW instance of itself beside the old one (see below)
      ▼
(new) versioned daemon             ← the old daemon's own child/grandchild,
                                      reparented to the manager when the old
                                      daemon retires
```

The manager is deliberately **dumb** relative to `zdd.cutover`: it does not
drive, understand, or participate in the drain/flip/retire sequence. It only
answers one question, each time its direct child exits: *is a live, legitimate
successor already running in my own tree?*

1. **Claim every descendant, regardless of how many hops deep.** On POSIX, mark
   itself a subreaper (`prctl(PR_SET_CHILD_SUBREAPER, 1)`) before spawning the
   daemon. A cutover's existing chain (old daemon → a detached `deploy`
   orchestrator → the new passive daemon, each spawned with its own
   `start_new_session=True` for unrelated reasons) is unaffected by session/
   process-group membership — kernel parent/child ancestry is what subreaper
   reparenting follows, so every one of those processes still reparents to the
   manager, not the ambient init, the moment its own direct parent exits. This
   closes the exact escape the origin incident showed (a cutover survivor
   reparenting to a WSL-session `/init`). On Windows, assign the manager (and
   therefore every spawned descendant, transitively, since Job membership is
   inherited by default) to a **Job Object** it owns — Job membership doesn't
   depend on parent/child linkage at all, so it needs no subreaper-equivalent
   trick and survives an intermediate process's exit even more simply.
2. **On child-exit, read the daemon's own liveness record** (`zdd.routing`'s
   `active.json` — already how every `zdd` consumer tracks "who is live"
   today): a `pid`/`generation` entry that resolves to a process still in the
   manager's own tree (POSIX: still alive and still its descendant per
   `/proc`; Windows: still a member of the owned Job) means a **planned
   cutover** — adopt that pid as the new watched child and keep running, no
   exit. Anything else (no entry, a dead pid, a pid outside the tree) means a
   **real crash** — the manager exits too, so the service manager's own
   `Restart=on-failure` fires against a clean slate.
3. **Never cache a version or a path.** The manager's only persistent
   responsibility is "is my tree's current occupant of the active role still
   alive" — it re-derives that from `active.json` every time, the same way the
   stable binstub beneath it re-derives `current-version` every time. A stale
   manager binary is never itself a staleness risk, since it carries no
   daemon-specific logic to go stale.

## What does **not** change

- `zdd.cutover.CutoverOrchestrator`, `zdd.routing`, `zdd.breadcrumb`,
  `zdd.diagnostics` — none of it. The manager is a pure **additive** layer
  outside the existing, already-proven cutover implementation; it observes
  the same `active.json` every other consumer already reads, and it never
  drives, blocks, or participates in a drain.
- The self-update loop's `_spawn_self_deploy` (or any installer-driven
  activation trigger) — unchanged. It already detaches its `deploy`
  orchestrator (`start_new_session=True`) for reasons unrelated to this
  pattern (surviving the triggering coordinator's own possible death); the
  manager's subreaper claim composes with that transparently.
- The stable, register-once launcher beneath the service manager (the
  `serve-service.sh` / Scheduled-Task-launcher layer from
  [`service-lifecycle-supervision`](service-lifecycle-supervision.md)) —
  unchanged. The manager slots in as what that launcher execs, resolving and
  spawning the real daemon itself rather than the launcher spawning the
  daemon directly.

## Consumer contract

A daemon adopts this pattern with **zero code changes**, as long as it already
meets the `zdd` consumer contract (publishes `active.json` via
`zdd.routing.publish_active`, as every `graceful-daemon-cutover` adopter
already does). The manager is wired in exactly once, at the launcher
boundary:

```
systemd ExecStart / Scheduled Task Action
    -> <stable launcher, resolves current-version marker, unchanged>
    -> zdd.singleton_manager.run(
           config_dir=...,                 # where active.json lives
           spawn=lambda: subprocess.Popen([resolved_python, "-m", "my_daemon", "serve"]),
       )
```

`zdd.singleton_manager.run` blocks for the manager's own lifetime (mirroring
`agent_dispatch serve`'s own blocking contract, so the service manager's
view — "is the unit's main process still running" — needs no other change).

## Validation

- **Unit (mocked):** every branch of the child-exit decision — planned-cutover
  adoption, real-crash propagation, the manager's own unexpected death, a
  `CHILD_SUBREAPER`/Job-membership check against a faked process tree — with
  no real subprocess spawned.
- **Stress (real subprocesses, no container needed):** rapid-fire restarts,
  concurrent/overlapping cutover attempts, `SIGKILL` injected mid-cutover,
  repeated crash-loop induction — exercising the real OS subreaper/Job-Object
  primitives directly on the test host. This class of coverage is explicitly
  new: it did not previously exist for any of this suite's process-spawning
  mechanics, which is part of why this exact failure mode went unnoticed
  until it hit a live host.
- **End-to-end (real service manager):** a real systemd unit's
  `Restart=on-failure` reconciling correctly across a real cutover needs a
  real systemd — Docker's clean-room base images do not run systemd as PID 1,
  so this is validated separately (`systemd-nspawn` or an equivalent
  systemd-capable container), as a narrower, standalone check rather than a
  clean-room scenario.

## See Also

- Intent: [`visions/plugin-services/`](../../visions/plugin-services/README.md)
  §Behaviors/`cutover-coherent-service-tracking`
- Hub: [`docs/patterns/`](README.md)
- [`graceful-daemon-cutover`](graceful-daemon-cutover.md) — the protocol this
  pattern wraps, unchanged
- [`service-lifecycle-supervision`](service-lifecycle-supervision.md) — the
  launcher boundary this pattern sits behind
- [`process-slot-ownership`](process-slot-ownership.md) — the daemon-side
  generation self-retire/abandoned-passive reap this pattern complements from
  the service-manager side
