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

The compatibility PowerShell installer compares generated coordinator and
supervisor launcher bytes under its existing installation publication mutex.
Byte-identical launchers are left untouched, including when a live reader
denies writes or replacement. Changed launchers use the existing bounded
atomic publisher: readers permitting replacement retain the complete old
script. Unqualified sharing conflicts remain visible publication failures.

Stable entrypoints resolve the selected runtime,
then forward in the same PowerShell process to its packaged
`agent_dispatch/service_launchers/*.ps1`. Configuration, logs, launch admission,
arguments and exit ownership stay with that call; no detached forwarding child
or new task is introduced. `-ResolveOnly` remains a read-only runtime lookup.
An older slot without this resource produces an explicit incompatibility error,
not a fallback into another generation's service code. Reinstalling an older
release regenerates its matching stable launcher; merely moving the runtime
marker backwards does not recreate an older launch contract.

Changed launchers use the bounded atomic publisher. Readers permitting
replacement retain the complete old script. On a Windows sharing conflict,
the incoming installer may retire an exact outgoing generated PowerShell
wrapper while holding its existing cross-version activation mutex. Restart
Manager supplies the actual file-holder creation identity; an admitted central
coordinator/supervisor child supplies its role and replaced-slot ownership.
Only that holder is terminated through a native process pin. The non-holder
central, its Jobs and protected children are not additional targets, and
ordinary supersession still owns their lifecycle.

The generation captured at successful activation must still be outgoing, and
the incoming selection is rechecked immediately before termination. Custom
scripts, unknown/foreign holders, ambiguous ancestry, current/reselected/future
generations and unrelated readers are not retirement targets. Missing ownership
does not authorize a PID/name sweep. Publication is retried once after this
bounded attempt; a remaining OS conflict is a visible failure naming the target,
never a successful unpublished change. The POSIX launch path and Source
application's separate scheduled-launcher publisher are unchanged.

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
duplicate new-style lanes. Native admission, process liveness, cycle start or
failure cannot establish readiness; stale versions/settings and unconfirmed
custody are not ready.
The profile marker is consumed before worker spawning, not inherited by workers.

The optional `completed_cycle` record retains a successful cycle's finite
timestamps, cycle ID, runtime version and native generation while the next
cycle publishes its current progress. A new generation or settings identity
cannot inherit that proof. Readers retain compatibility with older completed
status records; an invalid new record cannot fall back to older fields.
This is startup completion evidence, not a fresh health/progress claim.
Installer preflight reuses the supervisor's canonical parser without loading
unrelated queue commands. Source startup observes configured and primary-native
intent together, then keeps its separate admission-time policy check.
Capability detection and Source startup share one selected interpreter, retaining
the existing unsupported-probe fallback without treating startup-action failures
as permission to start a classic lane.

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

Clean classic exit removes only its own unchanged native status artifact before
releasing the profile lease. Discovery filters OS-confirmed dead/reused
incarnations before applying the live per-profile bound. Each scan reclaims at
most one dead file whose installation and exact file identity match its native
custody record; it never deletes another artifact or uses age as authority.
Unreadable or foreign files are not blindly removed. There is no
installation-wide file-count limit shared by unrelated profiles, and live
unobserved incumbents still prevent a duplicate start.

Marked `MODE=serve` masters retain the profile path and generation as explicit
master-only state after removing the environment markers. Same-version profile
edits use the existing bounded self-update handoff and its governance,
inhibition, draining and singleton gates; readiness waits through the
predecessor/successor publication gap rather than starting a competitor.
Both legacy successors and Source candidates receive the binding explicitly;
workers never inherit those markers. A Source candidate's private admission
arguments are not part of the effective supervisor-settings identity.
Malformed live profile arguments are recoverable configuration errors: parser
exits do not terminate the incumbent, and rejected input does not replace its
captured arguments or generation. A later valid edit can retry normally.
Live edits also reject the private `--source-candidate` admission flag before
selecting a target or committing arguments; that flag belongs only to the
existing candidate owner.
Live edits cannot change the master's machine/environment scope: that is a
different singleton and worker cohort, not a settings-only handoff. Such an
edit is rejected while the current master and its captured scope remain active.

Source handoff excludes the incumbent's own retained preparation and selects
only incoming effective settings (including declaration-discovery intent) and
the requested profile generation. These bindings are rechecked before releasing
the singleton. A same-version peer is an equivalent replacement only when those
settings, profile binding and authority match; a predecessor with older settings
or generation cannot make a new candidate stand down.
If a bound profile disappears or its read is refused during withdrawal, it is
noncurrent: the existing exact-candidate settlement and admission rollback apply.
Unconfirmed effects retain their custody instead of becoming successful absence
or permission to replay a launch.

Primary Source `MODE=serve` planning uses the same profile/CLI argument owner as
compatibility launch, retaining validated arguments through native startup.
Supported extras such as `--no-declared` and an explicit environment preserve
their declaration intent, authority and singleton scope. Explicitly disabled
modern discovery qualifies only the requested legacy cohort, or a known empty
cohort when legacy discovery is also off. Unknown options, passive-admission
arguments and nonresident modes are rejected before coordinator mutation.
Initial primary Source startup binds the exact parsed profile bytes and path
before launch. Repeated startup waits for that captured incoming generation's
completed readiness while the incumbent performs its existing handoff.
An optional argument-owner field in the existing master binding makes live
Source-primary edits reuse the same planner as startup: the default interval
remains 5 seconds and legacy discovery follows the configured cohort. Existing
compatibility and named bindings retain their 30-second/legacy-enabled defaults.
All binding markers, including that owner, are removed before worker spawning.
An older update producer need not know these optional markers. The new selected
Source candidate's existing admission owner can derive the binding when a valid
primary `MODE=serve` file already exists and its captured arguments, canonical
scope and authority match the actual candidate. Mismatched custom roles and
explicit compatibility/named bindings are not relabeled. Missing or unreadable
primary input does not establish a binding, and a later-created profile is not
silently attached to an already-running unmarked master.

The coordinator launcher's physical nested-slot fallback delegates to the
co-deployed canonical resolver, including its completion-marker and reparse
checks. It does not interpret the installation-root `.venv` alias as an
immutable selected slot.

Old readers can ignore the optional extension; old writers remain usable
through their existing compatibility lifecycle, but cannot supply new readiness
evidence. An unobserved incumbent is reported honestly, without starting a
duplicate or stopping it. Bounded timeout is not success and does not authorize
an automatic process sweep or a larger deadline.

These are source contracts. Fixture checks do not prove a clean non-admin host,
production lifetime survival, or authorized external adoption. Native fixture
admission uses its own SDK custody registry and prepared dependencies; inert
business substitutions are limited to resident invocations, not cache probes
or readiness clients.
