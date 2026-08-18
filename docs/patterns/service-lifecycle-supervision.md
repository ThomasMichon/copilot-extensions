# Pattern: service-lifecycle-supervision

**Serves:** *Vision plugin-services* §Features/`platform-native-lifecycle`,
`uniform-deploy-contract`.
**Exemplars:** agent-dispatch, agent-bridge, agent-vault.

## Problem

A runtime-service plugin's daemon must be **always-on**: it starts without an
interactive session, survives logout, and restarts on failure — identically on
Windows and Linux/WSL, without the user writing service definitions by hand.

## Standard approach

**Supervise with the host OS's own per-user service facility** — one contract,
two backends:

- **Windows:** a **Scheduled Task** (the analogue of a systemd user unit) —
  trigger `AtLogOn`, restart-on-failure, no execution time limit (long-running
  server), run whether on battery or not.
- **Linux/WSL:** a **systemd *user* unit** — `Restart=on-failure`,
  `WantedBy=default.target`.

**One installer, lifecycle verbs.** A single installer per OS
(`scripts/install.ps1` / `scripts/install.sh`) exposes
`install | update | status | start | stop | uninstall`, so the plugin's own
service management and any downstream service framework drive it the same way.

**A thin launcher owns environment.** The task/unit runs a small launcher that
loads the editable `service.env`, then execs the venv interpreter
(`python -m <pkg> serve`). Config lives in `service.env`, not baked into the
task — edit-and-restart, never re-register to change a value.

**Default-on where it belongs.** The service installs and starts by default on a
host that should run it; a client-only host opts out (`--no-service` /
`-NoService`).

### Gotchas this pattern encodes

- **The workgroup-principal trap.** Register the Windows task's principal from the
  *current identity* (`[WindowsIdentity]::GetCurrent().Name`), **not**
  `%USERDOMAIN%\%USERNAME%` — on a non-domain (workgroup) machine `USERDOMAIN` is
  `WORKGROUP`, which is not a resolvable security principal and fails registration.
- **Register once; refresh in place on update — never re-register.** First-time
  registration needs elevation; *changing* an existing task's registration can also
  fail "Access is denied". So register the task **once** with an action that points
  at a **stable launcher path** which resolves the active slot at run time (via the
  `current-version` marker — see
  [`durable-vs-versioned-runtime`](durable-vs-versioned-runtime.md)). The task
  *definition* then never changes across updates, so an update never needs to
  re-register. To pick up the new build, an update **restarts the existing task**
  non-elevated — kill the (possibly detached, see below) daemon, then `Start` the
  task — or hands off via [`graceful-daemon-cutover`](graceful-daemon-cutover.md)
  for a daemon holding in-flight work. `Set-ScheduledTask` (an in-place edit of an
  existing task you own) is non-elevated and fine for a trigger/action tweak; a
  forced `Register -Force` is only for a principal change (interactive ⇄ S4U).
- **Guard a legacy-migration stop on the real link path, not the built slot.** If
  the installer stops the daemon to release a *legacy* runtime dir before the first
  versioned migration, gate that stop on the **actual link path** (e.g. `.venv`),
  which is normally absent under the junction-free marker model. Do **not** gate it
  on the variable that a versioned refactor may have repointed at the
  freshly-built `versions/<v>` slot — that dir is *always* a real, non-link
  directory, so such a guard fires on **every** update and force-stops the daemon
  each time, defeating a non-elevated in-place refresh (agent-dispatch regression,
  fixed by restoring the real-path guard).
- **Kill the detached child, not just the task.** On Windows the launcher is run
  under `conhost.exe --headless`, which **detaches** the pwsh+python from the task's
  tracked process tree — so `Stop-ScheduledTask` alone does not kill the live
  daemon. A restart-on-update must terminate the detached `python -m <pkg> …`
  process by command line before `Start-ScheduledTask`, or the old build survives
  and the new one stands down on the single-instance lease.
- **Supervision ≠ binding.** The supervisor keeps the process *alive*; it does not
  make a contended endpoint bind. Endpoint contention is the endpoint pattern's
  job — a service that flaps "up then exits" is usually an endpoint problem, not a
  supervision one (see [local-endpoint-discovery](local-endpoint-discovery.md)).

## Rationale

Platform-native supervision gives auto-start, keep-alive, and restart for free on
each OS, with no bespoke watchdog. A uniform verb set + `service.env` means a
human or an automated fleet reasons about every plugin service identically.

## See Also

- Intent: [`visions/plugin-services/`](../../visions/plugin-services/README.md)
- Hub: [`docs/patterns/`](README.md) · Deploy contract: [`install-contract.md`](../install-contract.md)
