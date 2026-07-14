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
- **Re-registration vs. update.** Overwriting an existing task's registration can
  fail "Access is denied"; the task already references the venv, so a package
  update alone re-points it. Prefer update over force-re-register.
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
