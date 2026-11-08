# Windows scheduling and classic-lane readiness

Scheduling is not capability enablement. Clean install, update and first use do
not require Task Scheduler, elevation, a local account, or an alternate startup
registration. Coordinator lifecycle readiness uses the existing `deploy`/cutover
owner; client on-demand startup retains `_ensure-coordinator`. Configured
classic supervisors retain their detached native process owner, label gates and
per-profile behavior. Neither task absence nor scheduling reconciliation grants
permission to stop workers, retire process inventories, or erase state.

## Installation policy

The optional `<installation-root>\installation.json` is user-owned configuration:

```json
{
  "version": 1,
  "installation": {
    "root": "D:\\AgentData\\dispatch",
    "local": {
      "windows_scheduled_task": "preserve",
      "start_coordinator": true,
      "start_supervisor": true
    }
  }
}
```

`root` must name this exact installation. Unknown properties, invalid values,
foreign roots, unreadable files and refused native queries are errors. An absent
default file means `preserve`; an explicitly selected missing file is an error.
Keep private preferences in this local file, not in public source.

* **preserve** (default): missing tasks stay absent; existing custom definitions,
  triggers and enablement are untouched. No HKCU Run/Startup/service substitute
  is installed. Runtime readiness remains independent. Known generated launcher
  code can refresh behind its stable binding without rewriting a task.
* **enabled**: explicitly requests compatibility Scheduled Task activation.
  The ordinary compatibility installer retains its coordinator/supervisor
  registration shapes. Source application startup creates missing ordinary
  stable activation launchers only for this explicit mode; they resolve the
  selected slot and invoke the existing `_ensure-coordinator` or `__source-start`
  application owner.
  Custom launcher content and disabled tasks stay unchanged; known generated
  launchers are updated for the selected runtime. Existing custom registrations
  are not required to match the generated action when nothing changes; exact
  ownership validation still applies to new registrations and retirement.
* **absent**: retires only identity-validated coordinator/supervisor-family
  registrations. Normal installation still ensures independently enabled
  incoming capabilities; absence is not `NoService` or `NoSupervisor`.

The two `start_*` booleans are independent, durable start intent. A disabled
native task also vetoes direct startup. Before retiring a disabled registration,
the corresponding start intent must be false in the installation-root policy,
so removing the native registration cannot accidentally erase its veto.
Scheduling never clears action controls, maintenance or automatic-update
inhibition. Unknown observations are not readiness.

`install.ps1 -InstallationConfig <path>` resolves relative selectors against the
original filesystem working directory, before staging. Task-only operations
support this selector directly. The canonical driver now carries this selector
through its admitted context; native re-entry refuses changed or foreign
selectors. Older providers still trigger the installer's pre-driver refusal
rather than dropping the override. The installation-root policy remains
available without that additional provider support.

## Task-only retirement

```powershell
.\scripts\install.ps1 -Action task-retire -InstallDir D:\AgentData\dispatch `
  -ScheduledTaskNames agent-dispatch-<installation-suffix>
```

Select exact names from the intended installation, never a wildcard. Non-legacy
roots use the existing installation suffix; the legacy root retains its names.
This early entrypoint does not construct/select an incoming runtime, start or
stop a daemon/task, modify startup keys, or uninstall application state.
Missing tasks are idempotent. Explicit retirement requires native query/removal
support. Foreign folders/actions, ambiguous names, query refusal, changed task
identity and unconfirmed removal fail instead of becoming successful absence.
The known family is the coordinator, primary supervisor and named supervisor
profiles. A similarly named scheduler-tick registration has no registration
owner in this implementation and is not authorized by its name.

## Additive classic completed-cycle observations

The selected runtime's `supervisor_lane_status` module advertises support for
`classic-completed-cycle-v1`. The installer probes that actual interpreter
before invoking the adapter; it never sends new syntax to an unqualified
downlevel CLI.
Source application startup uses the same classic adapter for existing primary
and named profiles, retaining their individual disabled-native-task vetoes;
it does not require migrating those profiles into a master.

For installer-owned profiles only, the existing classic lane publishes an
optional `lane_readiness` extension through `supervisor_status_io`: profile and
settings identities, profile-content generation, runtime version, native
PID/birth/custody and a finite completed cycle. The per-profile lease prevents
duplicate new-style lanes. Native admission, process liveness, cycle start,
failed cycles, stale versions/settings and unconfirmed custody are not ready.
The profile marker is consumed before worker spawning, not inherited by workers.

A classic lane observes selected-version/profile supersession at a cycle
boundary and leaves its loop without stopping its workers. The incoming ensure
waits for its own completed cycle. There is no migration to a master daemon.
Existing `serve` mode retains its own role and singleton contract.
Generated scheduled activation uses that same profile-bound ensure and lease,
not an unmarked classic loop. Admission output has a separate log so the
resident's native lifetime owner can open its own log independently.

Discovery skips unreadable predecessor files, but a held lease without a
readable, native-custody-validated current record remains starting/unknown.
Historical inventory must account for every live incumbent. When native
incumbent absence cannot be established on a platform, a new classic start
refuses; an already observed ready owner can still be reused. This does not
disable standalone classic commands or select a master as a substitute.
Master cycle-start publication never inherits a predecessor's optional
readiness identity when the new incarnation did not supply one.

Old readers can ignore the optional extension; old writers remain usable
through their existing compatibility lifecycle, but cannot supply new readiness
evidence. An unobserved incumbent is reported honestly, without starting a
duplicate or stopping it. Bounded timeout is not success and does not authorize
an automatic process sweep or a larger deadline.

These are source contracts. Fixture checks do not prove a clean non-admin host,
production lifetime survival, or authorized external adoption.
