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
restart-in-place shape (the embody supervisor's `supervise serve`) is often
described as solving it for daemons with **no in-flight request to drain** —
wind down owned units, release the single-instance lease, spawn a successor
**with the same argv**, then exit with a sentinel code
(`SELF_UPDATE_EXIT_CODE`). **That description does not hold up under closer
inspection, and this pattern does not rely on it being true:** the daemon
pre-spawns its own successor *before* exiting, rather than the service
manager performing the replacement — so unless the service manager is
specifically configured to treat that exit code as success (confirmed
absent from the real deployed systemd unit: no `SuccessExitStatus=`), its own
native restart (`Restart=on-failure`) still fires against the *predecessor's*
exit, starting a second, redundant instance that races the one already
spawned. Whether that race is harmless in practice (the two instances'
shared singleton lease serializes them) or itself a crash-loop (the loser
re-exits non-zero every `RestartSec`, forever, on exactly the same shape this
whole pattern exists to close) is an open question about *existing, already-
shipped code* — out of scope to resolve here, but this pattern must not cite
it as a proven safe exemplar to delegate to. Tracked as
[#5574](https://github.com/ThomasMichon/copilot-extensions/issues/5574) (the
deployed unit has no `SuccessExitStatus=` for this exit code); that issue, not
this pattern's own Validation section, owns resolving it.

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
   **Job Object** it owns. Successor *legitimacy*, however, is **not**
   established by a post-hoc Toolhelp/PPID walk at child-exit time — the
   chain the manager must validate (old daemon → breakaway `deploy` → passive
   daemon) is one where `/shutdown` only *requests* the old daemon's exit and
   `deploy` can return and exit independently, so by the time the manager
   gets around to snapshotting ancestry, one or more intermediaries may
   already be gone — their ancestry entries absent from the snapshot, and
   their numeric pids already free to be reused by something unrelated. A
   walk run *after the fact* cannot be made durable against that: it has
   nothing to re-check if the thing it needs to check has already vanished.
   Instead, provenance is captured **at spawn time**, while every process in
   the chain is still guaranteed alive, via one narrowly scoped
   instrumentation point rather than a suite-wide requirement: `deploy`'s own
   spawn of the passive daemon (already inside `zdd.cutover`'s Windows
   breakaway path, the one known call site that produces this chain) creates
   it with `CREATE_SUSPENDED` — `CreateProcess` can schedule the child's
   primary thread before it even returns to the caller, so without this flag
   there is no guarantee the passive daemon is still unstarted (and has not
   already spawned descendants of its own) by the time the steps below run.
   With the child suspended, `deploy` duplicates a handle to it — via
   `DuplicateHandle`, whose target is the manager's own process — before the
   child has any chance to run at all. `DuplicateHandle` only returns the
   resulting handle *value* to the thread that called it (`deploy`'s own);
   it does not by itself tell the *manager* what value landed in the
   manager's own handle table, so a dedicated IPC channel carries that value
   across: `deploy` was spawned with an inherited pipe/socket back to the
   manager (mirroring the gate-pipe shape `_launch_gated` already uses
   elsewhere in this suite) and writes the duplicated handle's numeric value
   plus the candidate's pid over it. The manager reads that message, records
   the `(handle, pid)` pair in its own registry, calls
   `AssignProcessToJobObject` (via a freshly opened
   `PROCESS_SET_QUOTA | PROCESS_TERMINATE` handle on the same object) to
   bring the still-suspended candidate under the Job, and only then writes
   an acknowledgement back over the same channel. `deploy` waits for that
   acknowledgement before calling `ResumeThread` — so the candidate is
   registered in the manager's table and already Job-assigned before it ever
   runs a single instruction, closing the ordering gap a bare
   "duplicate-then-resume" sequence would leave open. The manager's own
   `hProcess`, needed as `DuplicateHandle`'s target, is threaded down to
   `deploy` as an inheritable handle value, but **scoped**, not blanket: a
   bare `bInheritHandles=TRUE` at `_spawn_self_deploy`'s own `CreateProcess`
   call would inherit *every* inheritable handle the manager's spawn call
   currently holds into `deploy` — and, if `deploy` naively forwarded the
   same flag onward, into the passive daemon too, needlessly exposing the
   manager's own process handle to code that has no legitimate use for it.
   Use this repository's existing scoped-inheritance pattern instead
   (`plugins/agent-dispatch/src/agent_dispatch/companion.py`'s
   `_launch_gated`: `STARTUPINFO.lpAttributeList = {"handle_list": [handle]}`
   plus `close_fds=True`) at `_spawn_self_deploy`'s own spawn, so only the
   one intended handle (and the IPC pipe endpoint) reaches `deploy` —
   nothing else inheritable leaks across that boundary. `deploy` itself does
   **not** forward that handle into its own spawn of the passive daemon at
   all: it already holds the manager handle from its own inherited set, so
   it duplicates a *separate* handle to the passive daemon it just created
   and sends that into the manager — the passive daemon never needs, and
   never receives, a handle to the manager itself. Because a `HANDLE` is a
   reference to the exact kernel process object, not a reusable numeric pid,
   the manager's registry of duplicated handles is immune to the PID-reuse
   failure a post-hoc walk cannot escape, and it does not depend on any
   intermediary still being alive later to prove its own ancestry — the
   proof was already captured the moment it mattered. **Handle custody,
   established at spawn time, is the trust decision; Job (re-)membership is
   purely bookkeeping for the crash-cleanup backstop (item 3) afterward.**
2. **On child-exit, read the daemon's own liveness record** (`zdd.routing`'s
   `active.json` — already how every `zdd` consumer tracks "who is live"
   today): a `pid`/`generation` entry is checked against the manager's own
   handle registry from item 1 — Linux: a live `/proc`-based `ppid` walk
   confirming the pid is genuinely the manager's own descendant; Windows:
   does the manager already hold a duplicated handle whose `GetProcessId`
   resolves to this exact pid? **Never** Job membership alone, and on
   Windows **never** a fresh Toolhelp snapshot taken at this point — only a
   handle captured back at spawn time. A match means a **planned
   cutover** — adopt that pid (that handle, on Windows) as the new watched
   child and keep running, no exit. Anything else (no entry, a dead pid, no
   registered handle for it) means a **real crash**. On Linux, `active.json`
   itself carries no process-identity token (only a pid and a routing
   generation, which order publications but do not by themselves rule out
   **PID reuse** — the recorded pid exiting and an unrelated process reusing
   that exact number before this check runs). Checking ancestry and only
   *afterward* reading a fresh identity token would leave a TOCTOU window of
   its own — the validated descendant could exit, and its pid get reused,
   in the gap between the ancestry check and the token read. The manager
   closes the whole window, not just half of it: it reads a
   process-start-time token (`zdd.diagnostics.process_start_time` — has a
   Linux and a Windows backend, used identically on both platforms here)
   for the candidate pid **immediately before** running the ancestry check,
   runs the ancestry check, then reads the same token **again immediately
   after** — and only adopts the pid if both reads returned a non-`None`
   value and the two values are equal. A mismatch (or either read coming
   back empty) means the pid changed identity somewhere across the check
   and is treated as a real crash, never adopted. This same
   read-check-read-and-require-both-to-match bracket is re-run on every
   later liveness poll of the adopted process, the same freshest-practical-
   moment convention `reap_tree_posix` (below) already uses — a reused pid
   is therefore caught the moment either side of the bracket no longer
   matches, not trusted indefinitely off one lucky first read. (On Windows
   this token bracket is a secondary belt-and-suspenders check — the
   duplicated handle itself already rules out pid reuse by construction,
   since it names the kernel object directly rather than a numeric pid.)
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
   unit. On Windows, the manager's Job is **named** (deterministically, from
   the manager-scoped `config_dir`), not anonymous, specifically so any
   process — a crash-triggered restart, or the bridge in item 5 below — can
   reopen the exact same Job by name rather than depending on a handle
   handed down from a predecessor that might not get the chance to hand
   anything down at all. The Job additionally sets
   `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, so the OS itself terminates every
   remaining member the instant the job's *last* open handle closes — which
   a graceful self-update handoff must never let happen, since the live
   daemon (a Job member throughout) would be killed as collateral. Item 5
   below describes the bridge that guarantees a second, overlapping handle
   is already open before the predecessor's own handle closes, so the
   self-update handoff is never itself the "last handle" closing.
5. **The manager updates itself in place, without ever leaving the service
   manager untracking a live process — or losing track of the daemon it was
   already watching.** "Version-agnostic" does not make a stale manager
   binary safe on its own — it still needs an update path, just like the
   daemon it watches. On Linux, the manager periodically re-checks the
   `current-version` marker and, on a change, calls `os.execve` to replace
   its own process image with the newer version's entry point: `execve`
   preserves the pid, open file descriptors, and (per the Linux `prctl(2)`
   manual page) subreaper status, so the service manager's own tracked
   identity never changes and no handoff is needed at all — genuinely
   clean, no second process, no race with anything. `execve` does **not**,
   however, preserve any in-memory state — the new image starts from a
   blank slate, with no record of which pid it was already watching. The
   new image must not treat this as a fresh launch and call `spawn` again,
   which would create a second, redundant daemon contender racing the one
   already live. Instead, the identity of the currently watched daemon is
   **persisted**, not carried in memory: the manager writes the watched
   pid and its `process_start_time` token into the manager-scoped
   `active.json` continuously (it already writes this record for the
   bridge-handoff case above, and reuses the exact same record here), and
   on every entry to `run()` — a fresh launch, a crash restart, *and* an
   `execve` self-update alike — the manager's first action is to check that
   record before ever considering a call to `spawn`: if it names a pid that
   passes the same read-check-read ancestry/token bracket item 2 already
   defines, the manager re-adopts that pid as the watched child and resumes
   supervising it; only an empty or failed-bracket record means there is
   truly nothing to adopt, and `spawn` is the right call. This is one
   single adoption path, not a special case for `execve` — item 2's bracket
   check is what both a routine child-exit poll and a just-exec'd fresh
   image call into.
   Windows has no pid-preserving exec equivalent, so a real process
   boundary is unavoidable there — but unlike the daemon's own cutover, the
   **manager** has no in-flight request to protect across that boundary; it
   is a pure supervisor, so a genuine Task-Scheduler-driven restart (not a
   spawn-a-permanent-second-instance-and-quietly-keep-running-it shape) is
   the correct mechanism, not a workaround to avoid. The remaining problem
   is narrower than "replace the manager": it is purely "keep the Job's
   last-handle-closes trigger from firing against the live daemon during
   the gap between the old manager's exit and the new one's startup." A
   minimal, deliberately short-lived **bridge** process closes exactly that
   gap and nothing more:
   1. Before updating, the old manager (already Task-tracked) spawns the
      bridge and **waits for an explicit bridge-ready acknowledgement**
      before doing anything else — "spawns the bridge" alone is not
      sufficient: the old manager could exit (triggering step 2's last-
      handle-closes trigger) before the bridge is even scheduled to run,
      which would kill the live daemon as collateral, the exact failure
      this whole bridge exists to prevent. The bridge, once scheduled,
      calls `OpenJobObject` on the same **named** Job (item 4) and writes a
      ready signal back to the old manager over an inherited pipe
      (mirroring the IPC channel item 1 already uses for `deploy`); only
      once the old manager has read that signal does it record the
      bridge's pid and `process_start_time` token in the manager-scoped
      `active.json` (distinct from the daemon's own, exactly as before) and
      proceed to step 2. If the acknowledgement does not arrive within a
      bounded timeout, the old manager **aborts the update** and keeps
      running as the current version rather than proceeding blind — a
      failed or slow bridge is a reason to retry later, never a reason to
      exit without handle-continuity confirmed.
   2. Only now does the old manager exit — and it exits with a
      **documented, non-zero self-update exit status** (distinct from a
      real crash's own exit codes, and distinct from Linux's
      `SELF_UPDATE_EXIT_CODE`, which this path does not reuse): Task
      Scheduler's restart action is a *restart-on-failure* policy
      (`RestartCount`/`RestartInterval`), so an ordinary success exit is not
      guaranteed to trigger step 3 at all — a graceful-looking exit could
      leave only the bridge and daemon alive, with no Task-tracked process
      and no pending restart. The manager deliberately does **not** attempt
      to suppress the restart here (unlike Linux, which needs no restart at
      all thanks to `execve`): a real restart is wanted every time, so the
      non-zero status is chosen specifically to satisfy the Task's own
      failure condition, and the stable launcher (below) preserves that
      exact exit code unchanged on every path it wraps, rather than
      translating or swallowing it.
   3. Task Scheduler's restart policy fires and launches the stable
      launcher fresh. **This new process is the permanently Task-tracked
      successor from this point on** — never a check-and-exit shim, and
      never a second, independent, permanently-running instance the Task
      stays blind to. Before entering its own `run()` loop, it reads the
      manager-scoped `active.json`, finds the bridge's recorded pid/token,
      opens its *own* handle to the same named Job (now three handles
      briefly overlap: the exiting bridge waits on this), confirms via the
      Job's own membership list that the daemon is still present, and
      publishes its own liveness record over the bridge's. This is the
      **same** persisted-identity re-adoption path item 5's Linux
      discussion above defines, not a Windows-specific special case: the
      new manager re-adopts the already-recorded watched daemon and never
      calls `spawn` here — calling `spawn` on this path would create a
      second, redundant daemon racing the one the bridge has been holding
      alive the whole time.
   4. Only once the new manager's handle is confirmed open does it signal
      the bridge to exit (e.g. a named event). The bridge closing its
      handle is now safe — the new manager already holds its own.
   From here the new manager proceeds exactly like any other launch,
   entering `run()` and supervising the daemon going forward — it never
   exits early, so a later crash of *this* instance still triggers a real
   Task restart the same way item 4 already requires. The bridge is a
   belt-and-suspenders handle-continuity helper only; it never does any of
   the manager's own supervisory work and never outlives the handoff it
   exists for.

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
  composes with that transparently regardless, with no re-check needed at
  this call site at all. On Windows its existing `CREATE_BREAKAWAY_FROM_JOB`
  flag also stays unchanged, but (per item 1's redesign) this one known call
  site — and `deploy`'s own subsequent spawn of the passive daemon — gains a
  small, scoped instrumentation point: threading the manager's inheritable
  handle down and duplicating a handle to the passive daemon into the
  manager at creation time. This is a bounded addition to one specific,
  already-`zdd`-aware chain, not a requirement that spawn sites across the
  suite generally become manager-aware.
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
the Scheduled Task's own tracked process **is** the manager — the same
single process, start to finish, whether this is the manager's first launch
or a restart the Task's own native policy fired. Do not adopt this pattern on
Windows without first closing that gap for the manager's own launch path
specifically — it does not need to be closed for every launcher in the
suite, only for whichever one now carries the permanently-tracked manager.

There is exactly **one** outer-identity model across this whole pattern on
Windows, used consistently everywhere above: the Scheduled Task always
re-invokes the same stable launcher fresh on every (re)start, real crash or
self-update handoff alike, and that fresh process **always** becomes
`zdd.singleton_manager.run()`'s own tracked lifetime — it never checks for a
successor and exits early instead of running. On Windows only (unneeded on
Linux, where `execve` means the manager's own tracked identity never changes
and the launcher is never re-invoked at all), that launcher performs one
extra step first: it checks the manager-scoped `active.json` for a pending
self-update's **bridge** (item 5) and, if one is recorded, takes over its
Job handle and signals it to exit before proceeding — a handle-continuity
bridge, never a second, separate always-running manager instance that the
Task stays blind to. Either way (a real crash, the very first launch, or a
self-update handoff), the launcher's own process is the one and only thing
`run()` ever executes as, and it is the one and only thing the Scheduled
Task is ever tracking from this point forward.

## Consumer contract

A daemon adopts this pattern with **zero code changes**, as long as it already
meets the `zdd` consumer contract (publishes `active.json` via
`zdd.routing.publish_active`, as every `graceful-daemon-cutover` adopter
already does). The manager is wired in exactly once, at the launcher
boundary:

```
systemd ExecStart / Scheduled Task Action
    -> <stable launcher, resolves current-version marker, unchanged on
        Linux; on Windows, additionally takes over a pending self-update
        bridge's Job handle (per "What does change" above) if one is
        recorded, before proceeding>
    -> zdd.singleton_manager.run(
           config_dir=...,                 # where active.json lives
           spawn=lambda: subprocess.Popen([resolved_python, "-m", "my_daemon", "serve"]),
       )
```

`zdd.singleton_manager.run` blocks for the manager's own lifetime (mirroring
`agent_dispatch serve`'s own blocking contract, so the service manager's
view — "is the unit's main process still running" — needs no other change).
This holds on every launch, Windows included: `run()` is always what the
launcher ends up blocked inside, never skipped in favor of exiting early.

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
