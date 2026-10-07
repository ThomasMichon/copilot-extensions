# Pattern: versioned singleton manager (service-manager stays coherent across a cutover)

> **Serves the vision:** *plugin-services* §Behaviors/`cutover-coherent-service-tracking`.
> **Companion patterns:** [`graceful-daemon-cutover`](graceful-daemon-cutover.md)
> (the drain/flip/retire protocol this pattern wraps, unchanged),
> [`service-lifecycle-supervision`](service-lifecycle-supervision.md) (the
> register-once launcher this pattern sits directly behind),
> [`process-slot-ownership`](process-slot-ownership.md) (generation self-retire
> and abandoned-passive reap — the *daemon-side* half of staying reconciled;
> this pattern is the *service-manager-side* half).
> **Origin:** a live incident where a coordinator's self-update cutover left
> its systemd unit crash-looping against its own already-live, untracked
> successor.

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
bookkeeping is now **wrong** until something external intervenes (a reboot, a
manual stop/clean-restart, or the survivor itself eventually exiting) — every
further restart attempt within that same window inherits the same mismatch,
since the manager has no record of which process, if any, is actually the
live one.

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

## Platform scope

**Linux and Windows only.** `PR_SET_CHILD_SUBREAPER` and `/proc` (the Linux
ancestry-walk mechanism below) are Linux-specific, not POSIX-general — macOS
has neither. This pattern does not define macOS behavior; a macOS adopter
needs an equivalent descendant-claiming primitive (there is no direct
`PR_SET_CHILD_SUBREAPER` analog on Darwin) before this pattern applies there.
Every mention of "POSIX" below means "Linux" specifically, kept as a shorthand
only where it mirrors an existing POSIX-labeled primitive elsewhere in this
suite (`agent_procutil`'s `detached_kwargs`, etc.) that itself degrades
gracefully off Linux.

## The shape

```
service manager (systemd unit / Scheduled Task)
      │  tracks this PID PERMANENTLY -- across every cutover, forever
      ▼
versioned singleton manager        ← NEW: thin, self-updating in place (see below)
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

1. **Claim every descendant, regardless of how many hops deep.** On Linux, mark
   itself a subreaper (`prctl(PR_SET_CHILD_SUBREAPER, 1)`) before spawning the
   daemon. A cutover's existing chain (old daemon → a detached `deploy`
   orchestrator → the new passive daemon, each spawned with its own
   `start_new_session=True` for unrelated reasons) is unaffected by session/
   process-group membership — kernel parent/child ancestry is what subreaper
   reparenting follows, so every one of those processes still reparents to the
   manager, not the ambient init, the moment its own direct parent exits. This
   closes the exact escape the origin incident showed (a cutover survivor
   reparenting to a WSL-session `/init`). On Windows, assign the manager to a
   **Job Object** it owns, and additionally mirror the Linux ancestry walk
   via `CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS)` +
   `th32ParentProcessID`, the same bounded upward-walk shape as
   `is_descendant_posix` — because Job membership alone cannot be trusted as
   proof of legitimacy here: the coordinator's own self-update spawn
   (`_spawn_self_deploy`) passes `detached_kwargs(breakaway=True)`, which
   sets `CREATE_BREAKAWAY_FROM_JOB` specifically so that spawn escapes
   whatever ambient job it inherits (originally meant to outlive a
   *different*, restrictive kill-on-close job elsewhere in the stack — not
   written with this manager's job in mind). A process outside the Job is
   therefore not automatically illegitimate, but Job membership also cannot
   be the thing that *grants* trust, or a stale `active.json` pid reused by
   a completely unrelated process would be blessed the moment the manager
   tried to re-capture it. **Ancestry is the trust decision; Job
   (re-)membership is purely bookkeeping for the crash-cleanup backstop
   (item 3) afterward.** Only once the Windows ancestry walk independently
   confirms a candidate pid is a genuine descendant does the manager call
   `AssignProcessToJobObject` (via a freshly opened `PROCESS_SET_QUOTA |
   PROCESS_TERMINATE` handle) to bring it back under the Job.
2. **On child-exit, read the daemon's own liveness record** (`zdd.routing`'s
   `active.json` — already how every `zdd` consumer tracks "who is live"
   today): a `pid`/`generation` entry that resolves to a process the
   platform ancestry walk confirms is genuinely the manager's own (Linux:
   `/proc`-based `ppid` walk; Windows: Toolhelp32-based `ParentProcessID`
   walk, per item 1 — **never** Job membership alone) means a **planned
   cutover** — adopt that pid as the new watched child and keep running, no
   exit. Anything else (no entry, a dead pid, a pid that fails the ancestry
   check) means a **real crash**. `active.json` itself carries no
   process-identity token (only a pid and a routing generation, which order
   publications but do not by themselves rule out **PID reuse** — the
   recorded pid exiting and an unrelated process reusing that exact number
   before this check runs). The manager closes that gap itself: it captures
   a process-start-time token (`zdd.diagnostics.process_start_time` — has a
   Linux and a Windows backend, used identically on both platforms here)
   for the candidate pid at the moment of adoption and re-verifies that
   token on every later liveness poll of the adopted process, the same
   freshest-practical-moment convention `reap_tree_posix` (below) already
   uses — a reused pid is therefore caught the moment its start time no
   longer matches, not trusted indefinitely off one lucky first read.
   Ancestry confirmation happens *before* this token is ever captured, so
   the token itself is always anchored to a process already independently
   known to be legitimate, never to whatever process merely happens to
   currently hold the recorded pid.
3. **Reap every other live descendant before exiting on a real crash.**
   Exiting the manager does **not** by itself clear its tree: a subreaper
   claim or Job Object membership only governs *reparenting*, not lifetime —
   a stray survivor from a half-finished cutover (an abandoned passive
   candidate, an orphaned `deploy` orchestrator) stays alive and simply
   reparents *again*, now untracked by anyone, the moment the manager exits.
   Left alone, that stray can later collide with the *next* manager's freshly
   spawned daemon on the same singleton guard or port bind — the identical
   failure class this pattern exists to prevent, one level removed. Before
   the manager actually exits on the real-crash path, it must enumerate every
   remaining live member of its own tree (Linux: every descendant a full
   `/proc` scan resolves as its own, via the same ancestry walk as above;
   Windows: every remaining Job member via
   `QueryInformationJobObject(JobObjectBasicProcessIdList)`) and terminate
   them. On Linux, identity-bound (`zdd.diagnostics.terminate_pid_if_identity`
   — never a bare kill-by-pid, for the same TOCTOU reason that primitive
   already exists). On Windows this is simpler and needs no separate
   identity check at all: `TerminateJobObject` kills every current job
   member in one call, scoped by Job membership rather than a reused numeric
   pid. This step only runs if the manager itself gets the chance to run it
   at all — see the crash invariant below for the case where it doesn't.
4. **The manager's own crash must not recreate this pattern's own failure
   class, one level up.** Subreaper status and non-kill-on-close Job
   membership are attributes of the *manager process* — they vanish the
   instant it does, whether that's the graceful real-crash exit in step 3
   or the manager itself being killed outright (no in-process code runs on
   `SIGKILL`; this case cannot be closed from inside the manager at all). A
   service manager that just restarts a fresh manager process after that
   leaves the **old** manager's tree (the daemon, any stray survivors) alive
   and now untracked by *anyone* — the new manager's subreaper/Job identity
   is a completely separate one. Closing this needs an **out-of-process**
   backstop the host's own service manager provides, not code inside this
   module: on Linux, a systemd unit's default `KillMode=control-group`
   already kills every process in the unit's cgroup (not just the tracked
   main pid) as part of the stop-then-start transition a `Restart=`
   directive drives — do not override it to `KillMode=process` for this
   unit. On Windows, the manager's Job additionally sets
   `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, so the OS itself terminates every
   remaining member the instant the job's last open handle closes (the
   manager's own, on any exit path including a hard kill) — reconciled with
   the manager's **own** clean self-update handoff (item 5 below) by having
   the successor manager duplicate its own handle to the *same* Job object
   before the predecessor exits, so that handoff is never itself the "last
   handle" closing.
5. **The manager updates itself in place, without the service manager ever
   noticing.** "Version-agnostic" does not make a stale manager binary safe
   on its own — it still needs an update path, just like the daemon it
   watches. On Linux, the manager periodically re-checks the `current-version`
   marker and, on a change, calls `os.execve` to replace its own process
   image with the newer version's entry point: `execve` preserves the pid,
   open file descriptors, and (per the Linux `prctl(2)` manual page)
   subreaper status, so the service manager's own tracked identity never
   changes and no handoff is needed at all. Windows has no pid-preserving
   exec equivalent, so there the manager instead adopts
   [`service-lifecycle-supervision`](service-lifecycle-supervision.md)'s own
   existing restart-in-place shape directly: wind down, duplicate its Job
   handle into a spawned successor (per item 4), spawn that successor on
   the newer interpreter with the same argv, exit with the sentinel code its
   attached launcher's own restart-loop recognizes (never bare Scheduled-Task
   `RestartCount`, which retries the same stale interpreter, not a newer
   slot) — the same mechanism the embody supervisor already uses, applied to
   the manager rather than invented fresh.

> **Validation status.** This pattern is a **design, not yet an
> implementation** — the code (the `zdd.singleton_manager` module, both
> platform backends, and its own unit + stress tests) lands in a follow-up
> PR, not this one. The intent is for the Linux mechanisms above (subreaper
> reparenting, `/proc`-based ancestry, identity-bound reaping, `execve`
> self-update) to be exercised against real subprocesses and real kernel
> primitives before that PR lands; the **Windows** mechanisms additionally
> need a real Windows-host process-boundary test (kill the manager outright,
> confirm the daemon and every descendant die with it; force a self-update
> handoff, confirm the Job survives it) before Windows adoption is
> considered production-ready, not merely code-complete. Nothing in this
> section should be read as already-validated.

## What does **not** change

- `zdd.cutover.CutoverOrchestrator`, `zdd.routing`, `zdd.breadcrumb`,
  `zdd.diagnostics` — none of it. The manager is a pure **additive** layer
  outside the existing, already-proven cutover implementation; it observes
  the same `active.json` every other consumer already reads, and it never
  drives, blocks, or participates in a drain.
- The self-update loop's `_spawn_self_deploy` (or any installer-driven
  activation trigger) — unchanged on Linux: it already detaches its `deploy`
  orchestrator (`start_new_session=True`), and the manager's subreaper claim
  composes with that transparently regardless. On Windows its existing
  `CREATE_BREAKAWAY_FROM_JOB` flag also stays unchanged — the manager
  compensates on its own side (item 1's ancestry walk + re-capture) rather
  than requiring every spawn site across the suite to become manager-aware.
- The stable, register-once launcher beneath the service manager (the
  `serve-service.sh` / Scheduled-Task-launcher layer from
  [`service-lifecycle-supervision`](service-lifecycle-supervision.md)) on
  Linux — unchanged. The manager slots in as what that launcher execs,
  resolving and spawning the real daemon itself rather than the launcher
  spawning the daemon directly.

## What **does** change: the Windows launch shape

[`service-lifecycle-supervision`](service-lifecycle-supervision.md) documents
that the existing Scheduled-Task launch runs the daemon under
`conhost.exe --headless`, which **detaches** the actual
`python -m <pkg> serve` process from the task's own tracked process tree —
`Stop-ScheduledTask` does not kill it, and (the property this pattern
actually needs) the task cannot observe it exit either. Dropping this manager
in unchanged behind that same detached launch would silently defeat the whole
pattern on Windows: the Scheduled Task would track `conhost.exe`, not the
manager, so it could never restart the manager on a real crash in the first
place. This pattern's Windows adoption **requires** launching the manager
through a windowless-but-attached mechanism instead (mirroring
`agent_procutil.windowless_python`/`no_window_kwargs` — a `pythonw.exe`-style
direct launch with no console allocated and no `conhost` detachment hop), so
the Scheduled Task's own tracked process **is** the manager. Do not adopt this
pattern on Windows without first closing that gap for the manager's own
launch path specifically — it does not need to be closed for every launcher
in the suite, only for whichever one now carries the permanently-tracked
manager.

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

## Validation (planned — the implementation lands in a follow-up PR)

- **Unit (mocked):** every branch of the child-exit decision — planned-cutover
  adoption, real-crash propagation, the manager's own unexpected death,
  PID-reuse detection (an adopted pid whose identity token changes mid-watch
  is treated as gone, not trusted), a subreaper/Job-membership check against
  a faked process tree — with no real subprocess spawned.
- **Stress (real subprocesses, no container needed):** rapid-fire restarts,
  concurrent/overlapping cutover attempts, `SIGKILL` injected mid-cutover,
  repeated crash-loop induction, an orphan storm a real subreaper claim must
  reparent every member of — exercising the real OS primitives directly on
  the test host, on Linux. This class of coverage is explicitly new: it did
  not previously exist for any of this suite's process-spawning mechanics,
  which is part of why this exact failure mode went unnoticed until it hit
  a live host.
- **End-to-end (real service manager):** a real systemd unit's
  `Restart=on-failure` reconciling correctly across a real cutover needs a
  real systemd — Docker's clean-room base images do not run systemd as PID 1,
  so this is validated separately (`systemd-nspawn` or an equivalent
  systemd-capable container), as a narrower, standalone check rather than a
  clean-room scenario.
- **Windows-specific mechanisms** (Job breakaway re-capture, kill-on-close
  plus cross-process handle duplication for the manager's own self-update
  handoff) additionally need a **real Windows-host process-boundary test**
  (kill the manager outright, confirm the daemon and every descendant die
  with it; force a self-update handoff, confirm the Job survives it) before
  this pattern's Windows adoption is considered production-ready, not merely
  code-complete — ctypes logic authored without access to a Windows host to
  execute it against is not itself validation.

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
