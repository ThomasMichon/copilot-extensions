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
   **Job Object** it owns, created with `JOB_OBJECT_LIMIT_BREAKAWAY_OK` set:
   `_spawn_self_deploy`'s existing `CREATE_BREAKAWAY_FROM_JOB` flag on the
   child's own `CreateProcess` call is necessary but not sufficient —
   breakaway additionally requires the Job itself to permit it, or that
   spawn fails outright before this pattern's own re-capture ever gets a
   chance to run (the repository's own existing implementation already
   requires both flags together: `libs/agent-procutil/src/agent_procutil/
   __init__.py:65-73,252-254`). Successor *legitimacy*, however, is **not**
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
   the chain is still guaranteed alive, via a shared, one-time instrumented
   **passive-spawn contract** (see "What does not change" below for why this
   is a migration each Windows adopter's own `spawn_passive` callback must
   make, not one single internal call site) rather than a suite-wide
   requirement on every spawn site generally: `deploy`'s own spawn of the
   passive daemon (inside `zdd.cutover`'s Windows breakaway path, driven by
   whichever adopter callback now calls the shared contract) creates
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
   across. This channel is **not** a one-shot inherited pipe tied to a
   single spawn event — a cutover is not a one-time occurrence: the passive
   daemon promoted by *this* cutover eventually becomes active and, in its
   own turn, triggers its *own* `_spawn_self_deploy` for the *next*
   cutover, with no ancestry relationship back to this manager left to
   inherit a handle through. The manager therefore listens on one **durable,
   well-known named pipe** (`\\.\pipe\<name>` on Windows, a Unix domain
   socket on Linux if ever needed there, deterministically named from
   `manager_state_dir` the same way the Job's and the bridge's own pipe
   already are, ACL-restricted to the same principal) for its **entire
   lifetime**, not just around one spawn — every `deploy` invocation, for
   every daemon generation this manager ever supervises, connects to this
   same fixed address to perform the handle-registration dance below,
   rather than depending on an inheritance chain that only the very first
   generation could ever have had. `deploy` connects, writes the duplicated
   handle's numeric value plus the candidate's pid over it. The manager
   reads that message, records the `(handle, pid)` pair in its own registry,
   calls `AssignProcessToJobObject` (via a freshly opened
   `PROCESS_SET_QUOTA | PROCESS_TERMINATE` handle on the same object) to
   bring the still-suspended candidate under the Job, and only then writes
   an acknowledgement back over the same connection. `deploy` waits for that
   acknowledgement before calling `ResumeThread` — so the candidate is
   registered in the manager's table and already Job-assigned before it ever
   runs a single instruction, closing the ordering gap a bare
   "duplicate-then-resume" sequence would leave open. `deploy` itself does
   **not** forward anything about this channel or the manager's own process
   into its own spawn of the passive daemon at all: it connects to the
   manager's well-known pipe directly (no inheritance needed from either
   side), and separately duplicates a handle to the passive daemon it just
   created and sends that into the manager over the same connection — the
   passive daemon never needs, and never receives, a handle to the manager
   itself, or any knowledge of this channel. Because a `HANDLE` is a
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
   registered handle for it) means a **real crash**. Linux adoption additionally
   requires the route's publication-time `process_start_time` token. The
   routing library records this optional field when publishing a live PID;
   a spawner holding an earlier baseline can supply it and publication
   refuses a changed or unverifiable identity. Legacy routes without this
   token remain readable by ordinary clients but are not adoptable by an
   identity-bound manager.

   Sampling a fresh token only when adopting a stale route is insufficient:
   the PID may already belong to another manager descendant before either
   sample or the ancestry walk starts. The manager requires both identity
   reads bracketing ancestry validation to match the **published** token,
   not merely each other. It persists that validated baseline in its own
   `daemon` record under `manager_state_dir` and compares every later poll
   and post-exec recovery to it. Missing identity or a mismatch never
   authorizes adoption. On Windows this comparison remains supplementary
   to spawn-time handle custody; routing metadata does not replace the
   native handle-registration contract.
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
   the manager-scoped `manager_state_dir` — never the daemon's own
   `config_dir`, per the Consumer contract below), and the Job additionally
   sets `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, so the OS itself terminates
   every remaining member the instant the job's *last* open handle closes.
   These two properties combine differently depending on *why* the new
   manager is starting, and the two cases must not be conflated: on a
   **bridged self-update handoff** (item 5), the bridge already holds an
   open handle to this exact named Job before the old manager exits, so
   `KILL_ON_JOB_CLOSE` never fires and the new manager genuinely **reopens
   the same, still-live Job by name** (`OpenJobObject`) to join it. On a
   **real crash**, by contrast, nothing holds the Job open — the old
   manager's handle was the last one, `KILL_ON_JOB_CLOSE` already fired,
   every member (including the Job object itself) is gone, and there is no
   "same Job" left to reopen: the new manager must **create a fresh Job**
   (the same deterministic name is fine to reuse now that the old kernel
   object is destroyed) and assign the newly-spawned daemon to it the usual
   way, exactly as if this were the very first launch. The manager's own
   startup logic branches on exactly this: consult the `handoff` record in
   `manager_state_dir` first — present and its bridge still alive means
   reopen-and-take-over (item 5); absent or stale means create fresh.
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
   **persisted**, not carried in memory: the manager continuously writes a
   `daemon` record (the watched pid and its `process_start_time` token)
   into a **manager-scoped state directory distinct from `config_dir`** —
   see the Consumer contract below for why these cannot share one path —
   and every entry to `run()` checks that state before considering a
   spawn. **Linux state recovery is scoped to same-process exec:** both the
   recorded manager identity and the watched daemon's baseline/ancestry
   must still validate. An exec preserves the manager's kernel ownership;
   a new process after a real crash does not inherit the old subreaper's
   ancestry merely by reading its state file. Systemd's required
   control-group cleanup removes the old tree before that restart. If an
   old incumbent nevertheless survives, bootstrap refuses it rather than
   claiming foreign ownership or spawning a duplicate. A matching PID/start
   token alone never authorizes cross-manager Linux recovery.
   Windows has no pid-preserving exec equivalent, so a real process
   boundary is unavoidable there — but unlike the daemon's own cutover, the
   **manager** has no in-flight request to protect across that boundary; it
   is a pure supervisor, so a genuine Task-Scheduler-driven restart (not a
   spawn-a-permanent-second-instance-and-quietly-keep-running-it shape) is
   the correct mechanism, not a workaround to avoid. On Windows, though,
   the `daemon` record's pid/token alone is **not** sufficient for safe
   re-adoption the way it is on Linux: item 1 deliberately treats ancestry
   and Job membership as mere bookkeeping and the duplicated **handle** as
   the actual trust decision, and that trusted handle is a per-process
   resource — it does not survive the old manager's exit any more than its
   in-memory registry does, and a Job's membership list can show that a
   process remains without proving it is the specific daemon this pattern
   already trusts (a stray survivor sharing the Job could otherwise be
   adopted, or a duplicate spawned, by mistake). The bridge is therefore
   not just a handle-continuity helper for the Job — it is also the
   **relay** for the one trusted handle that actually matters:
   1. Before updating, the old manager (already Task-tracked) spawns the
      bridge and **waits for an explicit bridge-ready acknowledgement**
      before doing anything else — "spawns the bridge" alone is not
      sufficient: the old manager could exit (triggering step 2's last-
      handle-closes trigger) before the bridge is even scheduled to run,
      which would kill the live daemon as collateral, the exact failure
      this whole bridge exists to prevent. The bridge, once scheduled,
      makes itself **fully self-sufficient** before ever signaling ready,
      in a specific order chosen so the old manager — which is still alive
      and blocking throughout this entire sequence, not just its first
      step — can detect and abort on any failure along the way: (a) the
      bridge first requests the **legitimate** trusted daemon handle from
      the old manager itself, over the inherited pipe — the old manager
      duplicates the handle it already holds (from item 1's own spawn-time
      chain, the only legitimate source of Windows trust this pattern
      recognizes; see the note below on why an independently-established
      handle is not an acceptable substitute) into the bridge via
      `DuplicateHandle`, and the bridge acks receipt before anything else
      proceeds; (b) only then does it call `OpenJobObject` on the same
      **named** Job (item 4); (c) it creates a **named pipe**
      (`\\.\pipe\<name>`, the name deterministically derived from
      `manager_state_dir` the same way the Job's own name already is,
      created with a discretionary ACL restricting connection to the same
      principal the manager itself runs as) and starts listening on it —
      this is the channel the *next* manager, launched independently by
      Task Scheduler with no inherited handle or pipe of its own, will use
      to request the daemon handle in step 3; an inherited pipe (as item 1
      uses for `deploy`, itself a direct child of the process holding the
      other end) cannot serve this purpose, since the new manager process
      shares no ancestry with the bridge at all. **Trust model note:** this
      relay — not an independently re-derived `OpenProcess` plus
      baseline-token check — is deliberately the *only* way the bridge ever
      acquires its daemon handle, consistent with item 2's own Windows
      rule that pid/token revalidation alone is insufficient and only
      spawn-time handle custody (here, relayed custody from a process that
      itself legitimately held it) constitutes real trust; a bare
      `OpenProcess` fallback would quietly reintroduce the exact class of
      unproven-identity risk item 1 and item 2 exist to close out, so this
      pattern does not offer one anywhere, on either side of the handoff.
      Doing the relay **first**, before the bridge does anything else, also
      means the old manager is never asked to exit before this one
      precondition is already durably true — unlike basing readiness on a
      handle the bridge tries to establish independently later, there is no
      window where "the old manager might die" and "the bridge might lack
      a legitimate handle" can coincide, since the relay happens while the
      old manager is still alive and blocking on exactly this acknowledgement.
      (d) **the bridge itself —
      not the old manager — persists the `handoff` record** (its own pid,
      `process_start_time` token, and the named pipe's identifier,
      distinct from the `daemon` record above, in the same manager-scoped
      state directory, written atomically via write-to-temp-then-rename)
      as the **last** step of becoming self-sufficient, strictly **before**
      it ever signals ready to anyone. This is deliberate: making the old
      manager responsible for this write, even as its *only* remaining
      action, would still leave a window between the bridge's
      self-sufficiency (already holding the Job open, already preventing
      `KILL_ON_JOB_CLOSE`) and that write actually landing — if the old
      manager died in exactly that window, the bridge would be alive and
      blocking kill-on-close with **no record describing it at all**, and
      a crash-triggered
      restart would see nothing and take the create-fresh path, spawning a
      second daemon beside the survivor. Making the bridge respon­sible for
      its own record eliminates the window entirely: the bridge is already
      the one thing this design treats as independently durable across the
      old manager's death (that is the entire reason it exists), so by
      construction nothing can make the bridge self-sufficient-and-alive
      without the record also already being true — there is no second
      party, and therefore no gap between two parties' actions, left to
      race. Only *after* its own record is durably written does the bridge
      signal ready back to the old manager over the inherited pipe
      (mirroring the IPC channel item 1 already uses for `deploy`); the old
      manager's own role shrinks to simply **waiting for that signal**
      before proceeding to step 2 — it writes nothing itself. If the
      handle-relay acknowledgement, or the later bridge-ready signal, does
      not arrive within a bounded timeout, the old
      manager **aborts the update** and keeps running as the current
      version rather than proceeding blind — a failed or slow bridge is a
      reason to retry later, never a reason to exit without
      handle-continuity confirmed. The persisted `daemon` record itself is
      **never** overwritten by the bridge's identity — the two records have
      different lifetimes, different purposes, and (now) different
      writers: conflating them would let the bridge's own identity
      silently clobber the daemon's.
   2. Only now does the old manager exit — and it exits with its **normal
      success status**, not a special sentinel: `cutover-coherent-service-
      tracking` (the vision behavior this whole pattern serves) requires a
      planned handoff to be reflected to the service manager as
      intentional, never as a crash to retry — a *non-zero* "self-update"
      exit code would have Task Scheduler's own native restart-on-failure
      accounting (`RestartCount`/`RestartInterval`) classify this exit as a
      failure and consume a retry slot, directly contradicting that
      guarantee. The re-launch is therefore **not** driven by Task
      Scheduler's restart-on-failure policy at all: the **bridge** itself
      — already confirmed self-sufficient, already holding the one
      durable record of what needs adopting — explicitly re-invokes the
      registered task (`schtasks /run /tn <task name>`, or the equivalent
      `ITaskService` COM call) once it has confirmed the old manager's
      exit — a condition the bridge can observe directly, since it was
      spawned as the old manager's own child and already holds (or can
      trivially acquire at spawn time) an inheritable handle to it,
      satisfied by a single `WaitForSingleObject`. A single `/run` call
      immediately after that wait is not itself reliable, though: these
      tasks run with `MultipleInstancesPolicy=IgnoreNew` (already required
      elsewhere in this suite —
      [`service-lifecycle-supervision`](service-lifecycle-supervision.md),
      `plugins/agent-bridge/scripts/install.ps1`), so Task Scheduler's own
      internal state can still report the prior instance as "Running" for
      a brief interval after the tracked process has already exited —
      `/run` requested in exactly that window is silently accepted and
      launches nothing. The bridge therefore does not fire one
      `/run` and assume success: after the `WaitForSingleObject` confirms
      process exit, it polls the task's own state (`schtasks /query`, or
      `IRegisteredTask::State`) until it reports non-`Running`, issues
      `/run`, and then waits for the **new manager's own handshake** (a
      connection on its named pipe, per step 3) as the actual success
      signal — retrying the poll-then-run sequence, bounded by the same
      overall adoption deadline item 5 already defines, rather than
      trusting a single fire-and-forget invocation. This replaces a
      restart-on-failure heuristic that was
      never the right trigger for an intentional handoff. This sidesteps
      the retry-exhaustion failure mode entirely too — a bridge-driven
      re-run has no `RestartCount` to run out of — leaving that policy free
      to mean exactly what it already means elsewhere: a genuine,
      unplanned crash.
   3. This explicit re-run launches the stable launcher fresh. **This new
      process is the permanently Task-tracked successor from this point
      on** — never a check-and-exit shim, and never a second, independent,
      permanently-running instance the Task stays blind to. Before
      entering its own `run()` loop, it reads the
      transient `handoff` record, finds the bridge's recorded pid/token,
      opens its *own* handle to the same named Job via `OpenJobObject`
      (now three handles briefly overlap: the exiting bridge waits on
      this) and — opening a handle alone does not make the new manager
      *itself* a member — immediately calls `AssignProcessToJobObject` to
      join the Job. This is not optional bookkeeping: item 1's whole
      Windows containment model depends on the manager being a Job member
      so that future daemon spawns inherit containment the same way the
      very first launch's spawns did; skipping this step here would let
      every subsequent daemon spawned by this restarted manager silently
      escape the Job and survive a later manager crash unreaped.
      `AssignProcessToJobObject` can itself fail if Task Scheduler already
      placed the launcher in its own Job and the platform does not permit
      nesting: Windows 8 / Server 2012 and later support nested Jobs, which
      this pattern already requires as a baseline (consistent with the
      `CREATE_BREAKAWAY_FROM_JOB` + `JOB_OBJECT_LIMIT_BREAKAWAY_OK`
      requirement in item 1); on an unsupported older platform this call
      failing is a hard adoption failure — the manager must treat it the
      same as a failed bracket (abort re-adoption, log the platform
      mismatch) rather than silently continuing unassigned. The new
      manager also — this is the part a Job-membership check alone cannot
      provide — connects to the bridge's named pipe (its identifier read
      from the `handoff` record) and requests the bridge duplicate *its*
      daemon handle onward into the new manager, the same
      handle-plus-IPC-acknowledgement shape as step 1's own transfer, now
      carried over this named-pipe rendezvous instead of an inherited one
      since the new manager and the bridge share no process ancestry at
      all. Only once the new manager holds its own duplicated daemon handle
      does it cross-check that handle's pid against the persisted `daemon`
      record's baseline token via item 2's adoption check, and publish its
      own liveness over the bridge's. This is the **same** persisted-
      identity re-adoption path item 5's Linux discussion above defines,
      not a Windows-specific special case: the
      new manager re-adopts the already-recorded watched daemon and never
      calls `spawn` here — calling `spawn` on this path would create a
      second, redundant daemon racing the one the bridge has been holding
      alive the whole time.
   4. Only once the new manager's own Job membership, daemon handle, and
      Job handle are all confirmed does it signal the bridge to exit (e.g.
      a named event). The bridge closing its handles is now safe — the new
      manager already holds its own, independent copies of both.
   A new manager with **no** `handoff` record to work from (the ordinary
   real-crash case, or a bridge that has already cleaned up and exited)
   has **no safe way to re-adopt the daemon directly** — there is
   deliberately no `OpenProcess`-plus-baseline-token fallback here, for the
   same trust-model reason step 1(a) gives: a pid/token match alone is not
   proof of legitimate custody on Windows, only a relayed, spawn-time-
   derived handle is, and with no bridge alive there is nothing left to
   relay one from. Such a new manager therefore always takes the ordinary
   `spawn` path, exactly as if this were a genuine crash — which, by the
   time this matters, it has become: the bridge's own fail-closed cleanup
   (below) ensures that whenever its handoff record is gone, the daemon it
   was protecting is gone too, not merely unreachable. The bridge does not
   simply wait on its named pipe forever: it carries a **bounded adoption
   deadline** (started the moment it finishes step 1's handoff) for when no
   new manager ever connects — the bridge-triggered re-run itself failing,
   or a new manager crashing before it reaches step 3, are real failure
   modes this pattern must not leave unhandled. On expiry, the bridge, in
   one uninterrupted sequence with no intervening work (no I/O, no wait, no
   scheduling point that could let another process observe an inconsistent
   state in between): marks the `handoff` record **abandoned** (a fast,
   atomic rename/flag flip) and *immediately* closes its own Job handle —
   triggering `KILL_ON_JOB_CLOSE` for itself, the daemon, and every other
   Job member in the same kernel operation, since the bridge is itself a
   Job member (inherited from the old manager that spawned it) and cannot
   survive past that point to do anything further. Because the two steps
   run back-to-back with nothing observable in between, the brief window in
   which the record reads "abandoned" but the daemon has not yet actually
   been terminated is not a window any other process gets a meaningful
   chance to act within — and even in the vanishingly unlikely case a new
   manager's `spawn` call and the Job's kill both land within that same
   instant, the result is a daemon that dies virtually immediately after
   being created, not a permanent duplicate: a bounded, self-resolving
   residual, never an indefinite untracked-survivor state. Left unhandled
   entirely, a hung bridge would instead hold the Job open indefinitely,
   silently preventing `KILL_ON_JOB_CLOSE` from ever protecting against
   exactly the stray-survivor class item 3/4 exist to close — recreating,
   via a different path, the same untracked-daemon failure this entire
   pattern is for.
   From here the new manager proceeds exactly like any other launch,
   entering `run()` and supervising the daemon going forward — it never
   exits early, so a later crash of *this* instance still triggers a real
   Task restart the same way item 4 already requires. The bridge is a
   belt-and-suspenders relay only (Job-handle continuity *and* the trusted
   daemon-handle transfer); it never does any of the manager's own
   supervisory work and never outlives the handoff it exists for.

> **Implementation status.** The shared library supplies a **Linux-only**
> `zdd.singleton_manager` API, with native pidfd custody, subreaper ownership,
> persisted state/lease recovery, bounded descendant cleanup, and a
> consumer-provided update-argv resolver polled before child exit. Real
> subprocess tests exercise detached cutovers, orphan cleanup, exec preserving
> the manager PID without a duplicate spawn, and exec while a successor is
> still pending. See [`libs/zdd/README.md`](../../libs/zdd/README.md) for the
> executable API rather than treating every cross-platform design mechanism
> below as shipped code. Consumer launcher wiring and real systemd restart
> proof remain open. **Windows is still design-only and explicitly rejected
> by the native backend**; it requires its own implementation and real
> Job/Scheduled Task tests before adoption is production-ready. The
> [implementation effort](../../efforts/active/versioned-singleton-manager/README.md)
> owns these remaining delivery and validation gates.

## What does **not** change

- `zdd.cutover.CutoverOrchestrator`, `zdd.routing`, `zdd.breadcrumb`,
  `zdd.diagnostics` — the drain/flip/retire **protocol and semantics** are
  unchanged: the manager is a pure **additive** layer outside the existing,
  already-proven cutover logic; it observes the same `active.json` every
  other consumer already reads, and it never drives, blocks, or
  participates in a drain. This does **not** mean zero lines change inside
  `zdd.cutover` itself, though: the next bullet's Windows adapter
  instrumentation (`CREATE_SUSPENDED`, the manager IPC channel, handle
  duplication) lands at `deploy`'s own spawn of the passive daemon, which
  *is* inside `zdd.cutover`'s own Windows breakaway path. Treat this as
  "the orchestration behavior is unchanged" — never as "no code in
  `zdd.cutover` changes at all," which an implementer could otherwise read
  as license to skip the required Windows trust-boundary work below.
- The self-update loop's `_spawn_self_deploy` (or any installer-driven
  activation trigger) — unchanged on Linux: it already detaches its `deploy`
  orchestrator (`start_new_session=True`), and the manager's subreaper claim
  composes with that transparently regardless, with no re-check needed at
  this call site at all. On Windows its existing `CREATE_BREAKAWAY_FROM_JOB`
  flag also stays unchanged. The call-site reality here is **not** "one
  known call site inside `zdd.cutover`": `CutoverOrchestrator` itself only
  *calls* a consumer-supplied `spawn_passive: Callable[[int], Handle]`
  callback (`libs/zdd/src/zdd/cutover.py`) — it never creates the passive
  process itself. Each adopter owns its own callback (today:
  `agent_dispatch/coordinator_cli.py`'s and `agent_bridge/venue_cli.py`'s
  own passive-spawn implementations), so item 1's Windows instrumentation
  (`CREATE_SUSPENDED`, the manager IPC channel, handle duplication, Job
  assignment, acknowledgement) cannot be added at a single internal
  `zdd.cutover` call site. Instead, `zdd`
  must define one **shared passive-spawn contract** implementing that
  instrumentation once (a `zdd.cutover.windows_spawn_passive_with_manager`
  helper, or equivalent), and every existing Windows adopter's own
  `spawn_passive` callback migrates to call it instead of reimplementing
  process creation ad hoc. This is still a bounded, one-time integration
  cost — not a requirement that spawn sites *outside* this one contract
  become manager-aware — but it is a **multi-consumer migration**, not a
  single call-site edit, and any implementation PR must account for both
  named adopters explicitly.
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
extra step first: it checks the `handoff` record in `manager_state_dir` (see
Consumer contract below) for a pending self-update's **bridge** (item 5)
and, if one is recorded, takes over both its Job handle and its relayed
daemon handle, cross-checks the daemon handle's pid against the persisted
`daemon` record, and signals the bridge to exit before proceeding — a
handle-continuity-and-identity-relay bridge, never a second, separate
always-running manager instance that the Task stays blind to. Either way (a
real crash, the very first launch, or a self-update handoff), the launcher's
own process is the one and only thing `run()` ever executes as, and it is
the one and only thing the Scheduled Task is ever tracking from this point
forward.

## Consumer contract

A daemon adopts this pattern with **zero code changes**, as long as it already
meets the `zdd` consumer contract (publishes `active.json` via
`zdd.routing.publish_active`, as every `graceful-daemon-cutover` adopter
already does) — **zero daemon-protocol changes**, not zero changes to this
suite's Windows cutover machinery: the Windows mechanisms above (item 1's
`CREATE_SUSPENDED` creation, manager IPC, handle duplication, Job
assignment, and acknowledgement) are a required, one-time **shared
passive-spawn contract** that `zdd.cutover` must define once, which every
existing Windows adopter's own `spawn_passive` callback (today:
`agent_dispatch/coordinator_cli.py`, `agent_bridge/venue_cli.py`) migrates
to call instead of reimplementing process creation ad hoc — not something
every daemon author writes, but also not something an implementer may skip
or treat as optional, or this pattern's entire Windows trust boundary goes
missing silently for whichever adopter hasn't migrated. The manager itself
is wired in exactly once, at the launcher boundary. Two distinct paths are
required, not one: `config_dir` is the daemon's **own** `zdd.routing`
liveness record (`active.json`), unowned and unwritten by the manager;
`manager_state_dir` is a **separate** directory the manager owns entirely,
holding its own `daemon` record (item 5's persisted watched-pid/token, used
for re-adoption across any restart) and, transiently during a Windows
self-update, the `handoff` record (the bridge's pid/token). The manager's
own bookkeeping must never collide with, or be mistaken for, the daemon's
own routing publication it only ever reads.

```
systemd ExecStart / Scheduled Task Action
    -> <stable launcher, resolves current-version marker, unchanged on
        Linux; on Windows, additionally takes over a pending self-update
        bridge's Job handle and daemon handle (per "What does change"
        above) if one is recorded, before proceeding>
    -> zdd.singleton_manager.run(
           config_dir=...,                 # the daemon's own active.json (zdd.routing); read-only to the manager
           manager_state_dir=...,          # the manager's own daemon/handoff records; owned by the manager
           spawn=lambda: windowless_daemon_spawn(resolved_python, "-m", "my_daemon", "serve"),
       )
```

The sample `spawn` above is **not** a bare `subprocess.Popen` — on Windows,
the real coordinator adopter has recurring console descendants and already
requires the platform-aware launch primitive
(`agent_dispatch/coordinator_cli.py`'s own `windowless_daemon_kwargs()`,
per the established launch rule in
[`windows-background-process-launch`](windows-background-process-launch.md)
that a `pythonw.exe`-style root must not spawn console-subsystem descendants
without that primitive). This consumer contract **requires** the
platform-aware daemon spawn primitive, not bare `Popen`, precisely because
the manager itself runs windowless on Windows (per "What does change"
above) — any adopter whose `spawn` callback skips this can silently
reintroduce visible terminal windows the moment this pattern is adopted.

`zdd.singleton_manager.run` blocks for the manager's own lifetime (mirroring
`agent_dispatch serve`'s own blocking contract, so the service manager's
view — "is the unit's main process still running" — needs no other change).
This holds on every launch, Windows included: `run()` is always what the
launcher ends up blocked inside, never skipped in favor of exiting early.

## Validation

The Linux unit/stress tiers below are now delivered (`libs/zdd/tests/`,
224 passing through the bounded test-supervisor as of this PR); the
end-to-end real-systemd and Windows-specific tiers remain planned, tracked
by the [implementation effort](../../efforts/active/versioned-singleton-manager/README.md).

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
