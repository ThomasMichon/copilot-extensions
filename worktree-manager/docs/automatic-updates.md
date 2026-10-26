# Automatic updates

Manager owns an optional, installation-local update worker. It is **off by
default** and independent of the Picker. Opening or discovering a repository
never enrolls it. This is a background trigger over `worktree-manager update`,
not another installer, source selector, release controller or service manager.

## Explicit enrollment

Use the installed Manager, after its normal installation has completed:

```text
worktree-manager automatic-updates enable --project example
worktree-manager automatic-updates enable --project example --apply --json
worktree-manager automatic-updates status --json
```

The first invocation previews without writing. `--apply` records consent,
registers user startup and attempts immediate activation. Read the returned
worker readiness and startup result, not just a registry entry or unit file.
The default interval is 3,600 seconds; `--interval-seconds` accepts 300-86,400.
`--project` must name an already registered project; Manager refuses missing
context rather than starting an updater that cannot address the harness.
The first cycle is due immediately. Failures defer with bounded backoff, up to
one day; they do not start another concurrent update.

An explicitly executed trusted harness setup can recommend enrollment **at its
end, after installing Manager**:

```text
worktree-manager automatic-updates enroll --harness example-setup --project example --apply --json
```

The command is the programmatic setup seam. Its caller owns approval to run
setup; Manager neither executes repository setup code nor rediscovers opt-ins.
The durable policy records the setup's provenance and addressed project.
Repeating the same enrollment is idempotent. Conflicting enrollment is refused,
and an explicit local user disable takes precedence over a setup preference.
A matching existing selection keeps its original provenance rather than being
rewritten as a new harness enrollment.

The initial implementation addresses the selected **user-local legacy
installation at the standard per-user Manager root**. Alternate roots and
explicit marketplace installation contexts require durable bindings shared
with legacy controllers, so enrollment refuses them rather than hiding an
owner from those controllers or silently updating another cell.
Source-backed recurring deployment remains subject to
its separate source-selection and publication prerequisites.

## Consent, disable and exclusive ownership

Policy is strict, versioned JSON in
`<Manager root>/automatic-updates.json`; a missing file means no enrollment.
Malformed policy is a blocker, not a default. `ownership --json` reads it
without creating state.

```text
worktree-manager automatic-updates disable --apply --json
worktree-manager automatic-updates external --controller example-controller --apply --json
```

Disable the previous owner before selecting another. A running update retains
its execution lease: policy changes defer while an update holds that lease.
After disable is recorded, an idle worker exits on its next policy poll; it
does not kill an active installer. External ownership requires the old worker
to have finished draining. Existing startup registrations are not silently
removed or another owner's process stopped. A dormant Manager startup command
refuses to start an updater while policy is disabled or externally owned.
Disable persists the user's preference even when legacy verification is
blocked, but reports that blocker instead of claiming an old direct scheduled
job has been suppressed. Such jobs still need explicit retirement.

An external controller uses the same admitted canonical path:

```text
worktree-manager update --automatic-trigger external --controller example-controller --project example
```

Every Manager update, including an explicit foreground update, enters the
canonical durable update queue. The retained executor checks automatic
ownership again under the installation's maintenance transaction before any
installation work. A queued request does not bypass a later disable or
ownership change. Explicit foreground updates remain supported. An external
controller must use this seam rather than another concurrent installer.

Agent Machines keeps repository synchronization, maintenance-safe restore and
host/connectivity maintenance. Its automatic suite-update phase and legacy
fleet schedules honor the handoff. Enrollment inventories old fleet triggers
and refuses until they have been explicitly retired and their in-flight work
checked. It does not stop or delete them. See
[Agent Machines resources](../../plugins/agent-machines/docs/resources.md)
for that owner's lifecycle.

## Startup and platform lifetime

Windows uses **current-user Autorun**, not Task Scheduler or an elevated
service. The bootstrap is windowless; the worker's canonical daemon launch
must survive the setup caller's Job cleanup. Autorun starts at **user logon**,
not before logon or while that user is logged out.

Linux uses a user systemd unit. Registration cannot create a missing user
manager, enable system-wide linger or keep a WSL distribution running.
Its deliberate execution PATH includes the user's `.local/bin` and standard
system tool directories, not temporary checkout/session paths.
Missing user-manager/session support is an explicit activation blocker.
WSL lifetime remains the lifetime of that distribution and its user manager.

The worker resolves the selected complete installed Manager afresh for each
cycle and invokes its fixed, immutable runtime through the canonical
slot-local retention bootstrap. Startup does not resolve dependencies or
rebuild an old payload-only slot; missing constructed completion is an
activation blocker. The resident's runtime identity names that retained slot,
not its payload subdirectory. It clears ephemeral session/runtime overrides and uses noninteractive
authentication. Missing credentials defer visibly instead of starting a login
ceremony. Canonical update telemetry retains the installation's actual result.
Worker logs and status live under `<Manager root>/automatic-updates/`.

The non-TTY worker explicitly prohibits OpenSSH askpass, including an inherited
force setting, without replacing the configured SSH transport or account setup.
Custom transports retain their own provider policy; this is not a credential
ceremony or a substitute authentication resolver.

For a registered Linux/WSL unit, the old native MainPID keeps its scheduling
lease while an authenticated, non-owning standby qualifies worker startup.
Before reporting standby readiness, the incoming runtime previews its owned
startup refresh without writes and validates the saved schedule. A changed
native bootstrap interpreter that cannot replace the active entry is a visible
qualification blocker, not permission to stop the healthy predecessor.
The standby must finish without acquiring scheduling authority before the
native restart is requested. Failed or unsettled qualification retains the old
controller; a changed selection or disabled policy is rechecked before
retirement. The unit, not the warmup child, owns the eventual replacement.
Status proves liveness with a local challenge/response, not a cached PID.

Worker logic itself also advances. At a safe cycle boundary a windowless
successor proves standby readiness before receiving the singleton lease.
Failure before activation retains the old controller; failed activation
attempts to recover its lease, never resumes scheduling without custody.
An identity-confirmed systemd unit instead exits with a restart-request
failure code so its native supervisor starts the stable launcher on the new
selection. It never pre-spawns a successor into a departing systemd cgroup.
Native stop signals reach the actual worker MainPID and request a cooperative
drain before a normal exit; they do not terminate an active update child.
Self-cycling is on whenever the optional worker is enabled; the diagnostic
opt-out `WORKTREE_MANAGER_AUTOMATIC_UPDATES_SELF_CYCLE=0` pauses only worker
replacement, not the fresh updater resolution or the separate enrollment gate.

A cycle exceeding its one-hour budget reports an overdue draining operation
and schedules no additional update until it completes. That is not permission
to terminate an installer mid-publication or replace a healthy selected
runtime with a failed candidate.

## Evidence and recovery

Unit/contained lifecycle tests cover consent, serialization, liveness, deferred
results, draining and handover failures. Those tests are **not** evidence of
native unelevated Windows startup, Job escape, zero visible-window/focus changes
or an advancing real background update. Native qualification must exercise the
exact registered startup command, at least two cycles and worker replacement;
startup registration and foreground exit zero are not substitutes.

On a blocked or absent worker, inspect `status --json`, `worker.log` and the
canonical update telemetry first. `ensure --json` attempts activation only
when Manager is already the selected owner. Do not add a second scheduler,
delete another owner's startup entry, force an installer or begin interactive
authentication unattended to work around a blocker.

See also: [Manager overview](../README.md) and
[resident lifecycle pattern](../../docs/patterns/service-lifecycle-supervision.md).

## Instance custody boundary

Scheduling and runtime-retention leases are installation-root-local. They are
not a shared cross-root daemon-instance inventory or independent disposable-test
expiry. Worker startup and detached launch also use the shared
`agent_procutil.daemon_instances` admission/instance contract delivered by
[Leased Daemon Instances](<../../efforts/2026/10/25 leased-daemon-instances/README.md>)
(#8403). Production workers have no expiry; explicit qualification runs pass the
shared disposable lifecycle declaration to their actual children. These
attribution and test-lifetime controls do not replace scheduling custody,
installation execution authority or native supervision.
