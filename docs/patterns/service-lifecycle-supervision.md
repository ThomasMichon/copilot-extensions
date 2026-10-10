# Pattern: service-lifecycle-supervision

**Serves:** *Vision plugin-services* §Features/`platform-native-lifecycle`,
`least-privilege-lifecycle-tier`, `uniform-deploy-contract`; §Behaviors/
`register-once-cutover-on-update`, `payload-remains-replaceable`.
**Exemplars:** agent-dispatch, agent-bridge, agent-vault.

## Problem

A runtime-service plugin needs an explicit availability contract: some helpers
need only start with the user's session, while durable daemons must survive
logout, restart on failure, or start before login. The installer must realize
that contract consistently on Windows and Linux/WSL without the user writing
service definitions by hand or granting more privilege than the contract needs.

## Lifecycle pecking order

Choose the **lowest tier that satisfies the service's real availability
contract**. Escalation is deliberate; availability alone is not permission to
install the most privileged supervisor.

1. **User-mode ensure/auto-run (default, no elevation).** The daemon is a plain
   user process started through one idempotent, health-gated ensure path shared
   by `install`, `update`, `start`, and session-start readiness. On Windows this
   path must not depend on a Scheduled Task; on POSIX a systemd user unit may
   realize the same unprivileged contract.
2. **Scheduled activation (opt-in).** Adds a login/startup trigger around the
   same user-mode start path; it is not a prerequisite for `start` or `stop`.
   A user-owned Windows Scheduled Task normally remains limited and
   non-elevated. If a requested task shape genuinely requires elevation, only a
   dedicated task-registration action elevates that one step. Persistent
   systemd-user operation may similarly require one-time lingering enrollment.
3. **Installed system service.** Use a system-level service only for a concrete
   requirement such as pre-login startup, system identity, or stronger
   isolation that the user-scoped tiers cannot provide.
4. **Container-managed service.** In an explicitly containerized deployment,
   the plugin declares its lifecycle and health contract while the container
   orchestration layer owns start, restart, and replacement. The plugin does not
   also register a competing host supervisor, and its normal standalone host
   installation remains self-supervising without that orchestrator.

For a tier-2 Windows Scheduled Task, prefer an `AtLogOn` trigger, `RunLevel
Limited`, no execution time limit for a long-running server, and operation on
battery. Registration is idempotent and update-in-place; a missing task that
cannot be registered non-elevated produces remediation rather than elevating the
installer. For Linux/WSL, declare whether systemd-user lingering is part of the
selected availability contract.

## Standard approach

**One installer, lifecycle verbs.** A single installer per OS
(`scripts/install.ps1` / `scripts/install.sh`) exposes
`install | update | status | start | stop | uninstall`, so the plugin's own
service management and any downstream service framework drive it the same way.
The default user-mode ensure and every higher-tier trigger converge on the same
start path; registration never gates service startup.

**Declare the chosen tier.** Each service-bearing plugin records its tier,
availability promises, platform mappings, and reason for any escalation in its
architecture documentation. An audit can then distinguish an intentional
system service or container deployment from an accidental privilege increase.

**Register a stable launcher once.** Every durable host lifecycle binding points
at a small launcher in a fixed, durable location. The launcher loads the
editable `service.env`, resolves the active immutable runtime slot, changes into
a durable runtime/state working directory, then execs the interpreter
(`python -m <pkg> serve`). Config and version selection live behind this
boundary, never in the task/unit definition.

**Cut over behind the stable boundary.** An update installs a new immutable slot
without changing the supervisor definition. A live daemon follows
[`graceful-daemon-cutover`](graceful-daemon-cutover.md): start the new slot
passive, health-gate it, flip routing, drain the predecessor, then retire it.
The update path does not re-register the supervisor or ask for elevation again.

**Default-on where it belongs.** The service installs and starts by default on a
host that should run it; a client-only host opts out (`--no-service` /
`-NoService`).

**Outlive the launching command, not its cleanup policy.** A successful
`install`/`update`/ensure may close a caller-owned Windows kill-on-close Job.
Durable daemons must explicitly opt out through their canonical launch
primitive while bounded helpers remain contained. Headlessness, readiness
before return, and survival of the immediate parent are independent properties;
verify readiness again after closing the caller's Job. Keep test containment
suppression intact and use an isolated production-lifetime regression.
See [`windows-background-process-launch`](windows-background-process-launch.md)
for the ownership/visibility distinction and supported primitives.

### Gotchas this pattern encodes

- **The workgroup-principal trap.** Register the Windows task's principal from the
  *current identity* (`[WindowsIdentity]::GetCurrent().Name`), **not**
  `%USERDOMAIN%\%USERNAME%` — on a non-domain (workgroup) machine `USERDOMAIN` is
  `WORKGROUP`, which is not a resolvable security principal and fails registration.
- **Register once; refresh in place on update — never re-register.** Register a
  normal per-user task without elevation when the platform permits it. A task
  shape that genuinely requires elevation is created only through the dedicated,
  opt-in registration action; the normal installer remains non-elevated. Point
  the task at a **stable launcher path** which resolves the active slot at run
  time (via the `current-version` marker — see
  [`durable-vs-versioned-runtime`](durable-vs-versioned-runtime.md)). The task
  *definition* then never changes across updates, so an update never needs to
  re-register. A live daemon picks up the new build through
  [`graceful-daemon-cutover`](graceful-daemon-cutover.md); a bounded restart is
  not the routine version-update path. `Set-ScheduledTask` (an in-place edit of
  an existing task you own) is non-elevated and fine for a trigger/action tweak;
  a forced `Register -Force` is only for a principal change (interactive ⇄ S4U).
- **Nothing pins the plugin payload.** The supervisor's action, working
  directory, and long-lived descendants must resolve to stable launcher,
  installed-runtime, or durable-state paths — never the marketplace payload
  directory. On Windows, do not assume PowerShell's provider location is the
  Win32 process current directory inherited by a detached child: set the child
  process `WorkingDirectory` explicitly, or call
  `[IO.Directory]::SetCurrentDirectory()` before spawning it. `Set-Location`
  alone is not a sufficient payload-lock escape. Verify this invariant anywhere
  the service installer or launcher creates a process that outlives the
  invocation. Payload-local shims and hooks follow the corresponding rule in
  [`runtime-agent-plugin` § Give the agent an attributable command](runtime-agent-plugin.md#3-give-the-agent-an-attributable-command).
- **Generalize "nothing pins the plugin payload" to every deletable checkout,
  not just the marketplace payload directory.** The same hazard applies
  identically to an **anchor repo checkout**, a **worktree checkout**, and a
  **session-state folder**: all four are locations something else (a worktree
  manager, a cleanup sweep, a session-retirement reaper) may move, rename, or
  delete out from under a long-running process that never asked to be told
  first. A long-running **daemon** or **task worker** — anything whose
  lifetime is not bounded to "this one invocation" — must resolve its working
  directory, and the working directory of anything it spawns that outlives it,
  to a **versioned runtime slot** (`~/.<plugin>/versions/<v>`) or a **durable,
  user-home-style state location** (`~/.<plugin>/...`, see
  [`durable-vs-versioned-runtime`](durable-vs-versioned-runtime.md) for the
  split when the two need to live on separate lifecycles) — never a repo
  checkout, worktree checkout, or session folder, no matter how convenient
  that cwd is at spawn time.
  - **The one sanctioned exception: a process guaranteed to terminate before
    the folder could be removed.** A quick single-shot request (one RPC, one
    CLI invocation, answered and exited before any caller could plausibly
    delete the folder it ran from) or a genuinely **short-lived** daemon/task
    worker whose own lifetime is explicitly bounded to that folder's lifetime
    (this suite's `launch_session` and a per-worktree status-updater are the
    existing exemplars) may use the repo/worktree folder as cwd — and, to
    match Copilot CLI's own convention, specifically the **worktree root**.
    The moment a process might **outlive** the worktree/session that spawned
    it (a resident daemon, a detached background task, anything registered
    with a supervisor per this pattern), this exception no longer applies.
- **A tier-1/2 daemon on a logon-trigger-only schedule needs to cycle itself,
  not wait for a reboot.** A user-mode Scheduled Task registered with
  `MultipleInstancesPolicy=IgnoreNew` and no periodic trigger only relaunches
  the daemon at the next logon/reboot — an operator can easily go a long time
  between those, leaving a stale daemon (and every subprocess it spawns)
  running an old version well after a new one is published. Give the daemon
  its own live version-staleness check (poll the `current-version` marker, see
  [`durable-vs-versioned-runtime`](durable-vs-versioned-runtime.md)) and, once
  stale, hand off to the newer interpreter itself — either the full
  [`graceful-daemon-cutover`](graceful-daemon-cutover.md) protocol when the
  daemon owns a shared endpoint/routing table, or, for a plain singleton
  daemon with no in-flight request to drain (agent-dispatch's `supervise
  serve`, #2259), the lighter shape: wind down owned units, release the
  single-instance lease, spawn a successor on the new interpreter with the
  same argv, then exit. Make it **default-ON with an opt-out env var**, not
  opt-in — this harness's launch paths have no protocol for flipping an
  opt-in flag before a daemon's first boot, so an opt-in gate here simply
  never activates for anyone. Validate the handoff mechanism end-to-end (real
  process spawn, real single-instance lease release/reacquire, real successor
  argv) with a Tier-P clean-room scenario — a fully deterministic, agent-free
  simulation of a stale marker + an installed successor slot — *before*
  flipping the default on; see `validating-in-clean-room`. Don't rely on the
  Scheduled Task's own `RestartCount`/`RestartInterval` crash-restart policy
  for this — it retries a **failed** launch on the *same* (stale) interpreter
  a bounded number of times, it does not know about a newer published slot.
- **Guard a legacy-migration stop on the real link path, not the built slot.** If
  the installer stops the daemon to release a *legacy* runtime dir before the first
  versioned migration, gate that stop on the **actual link path** (e.g. `.venv`),
  which is normally absent under the junction-free marker model. Do **not** gate it
  on the variable that a versioned refactor may have repointed at the
  freshly-built `versions/<v>` slot — that dir is *always* a real, non-link
  directory, so such a guard fires on **every** update and force-stops the daemon
  each time, defeating a non-elevated in-place refresh (agent-dispatch regression,
  fixed by restoring the real-path guard).
- **Kill the detached child, not just the task.** On Windows a launcher may run
  under `conhost.exe --headless`, which **detaches** the pwsh+python from the task's
  tracked process tree — so `Stop-ScheduledTask` alone does not kill the live
  daemon. An explicit stop, uninstall, or cutover fallback must terminate the
  detached `python -m <pkg> …` process by command line, or the old build
  survives and the next generation stands down on the single-instance lease.
- **Supervision ≠ binding.** The supervisor keeps the process *alive*; it does not
  make a contended endpoint bind. Endpoint contention is the endpoint pattern's
  job — a service that flaps "up then exits" is usually an endpoint problem, not a
  supervision one (see [local-endpoint-discovery](local-endpoint-discovery.md)).

## Rationale

Least-privilege tier selection avoids recurring elevation and unnecessary
system-wide footprint. User-mode and platform-native lifecycle primitives
supply start, keep-alive, and restart behavior without a bespoke watchdog; the
stable launcher keeps registration independent of runtime generations. A
uniform verb set + `service.env` means a human or an automated fleet reasons
about every plugin service identically.

## See Also

- Intent: [`visions/plugin-services/`](../../visions/plugin-services/README.md)
- Hub: [`docs/patterns/`](README.md) · Deploy contract:
  [`install-contract.md`](../install-contract.md)
- Teardown of unregistered, per-unit-of-work helper processes (not a
  supervised service): [`ephemeral-process-reaping`](ephemeral-process-reaping.md)
- Keeping the registered supervisor's own liveness tracking coherent across a
  **spawn-new-retire-old** cutover (the coordinator shape, not the
  restart-in-place successor-with-same-argv shape this doc already covers):
  [`versioned-singleton-manager`](versioned-singleton-manager.md)
