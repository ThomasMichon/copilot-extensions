# Fast application installation and cutover

**Contributor contract, not a claim of portfolio-wide implementation.**
This realizes the [Installer](../../visions/installer/README.md) and
[Runtime Assembly](../../visions/plugin-services/runtime-assembly/README.md)
visions. Manager and Worktrees supply the first bounded application-project
examples; other applications still require the qualification recorded in the
[canonical effort's app matrix](../../efforts/active/source-managed-runtime-installs/wheel-first-contributors.md).
That matrix owns progress and assignments; this page owns the common support
and test contract. Neither grants host installation or fleet rollout authority.

Read this alongside the [install contract](../install-contract.md),
[graceful cutover](graceful-daemon-cutover.md),
[service contract evolution](service-contract-evolution.md), and
[progress/deadlines](progress-and-timeouts.md). Existing source, transaction,
installation and resource owners remain authoritative. Do not introduce a
second builder, supervisor, selector or lifecycle protocol to implement a row.

## End-to-end architecture

The durable goal is
[authoritative runtime replacement](../../visions/installer/README.md#authoritative-runtime-replacement).
Explicit incoming installation does not depend on old recovery/ZDD, custody/
provenance, capability support or stale transaction/fence settlement. Automatic
holds remain automatic-only. The target success boundary is every requested
incoming readiness and all current pointers/routes/launchers/registrations,
independently of outgoing retirement. Per-app current evidence below does not
establish that complete contract.

```text
trusted upstream installer / explicit manual recovery entry
    -> select official Main or configured backing Git commit once
    -> existing promotion owner supplies snapshot + matching wheels/locks
    -> install and verify Manager only
    -> read selected/registered repositories and derive wanted local apps
    -> copy complete application projects into immutable version slots
    -> stage selected apps with bounded parallelism (incumbents unchanged)
    -> qualify every member required by the chosen activation strategy
    -> activate through preferred graceful cutover or bounded cold takeover
    -> verify real command/service readiness and record installation result
    -> outgoing central owners self-retire independently of install success
    -> optional one finite long-grace async central-only retirement fallback
    -> retain last-good state, recovery evidence and unfinished obligations
```

The [effort outline](../../efforts/active/source-managed-runtime-installs/wheel-first-contributors.md#ideal-install-and-update-sequence)
adds bootstrap, background preparation, atomic-toolset and failure sequencing.
The sequence is a target, not executable CLI syntax. In particular, a
portfolio-wide atomic selector and complete stage-time readiness are not
established by today's generated per-app installers.

### Prepare a selected application portfolio without installing it

The local source adapter exposes `--prepare-applications` with an explicit
`--source-checkout`, exact `--source-commit`, `--main-head`, `--branch dev|main`,
absolute `--uv` executable, new external `--output` directory and one or more
`--project` selections. Standalone keys use `standalone:<component>`; runtime
plugins use their declared project name. Optional `--application-extras` is a
JSON object mapping selected component names to extra lists. Select one
`--deadline` (absolute monotonic time) or `--timeout` (seconds).

Run `tools/local_install_source.py` from a dependency-prepared source environment.
It admits the clean merged source, uses the canonical version-preserving
composition and artifact owners, and keeps one toolchain alive for the profile
worker. Scratch checkout suppresses configured Git hooks for that command,
and composition/control Git commands disable configured filesystem monitors
per invocation, without changing repository settings.
Only after every selected snapshot passes
complete readback does it return a JSON component-to-snapshot map and write
`applications.json`.
Before composition it validates the existing output parent and ancestor chain
against unprivileged replacement and exclusively creates an owner-private root.
Linked parents and existing or partial output roots are refused without repair.
Readback verifies the emitted application's wheel copies against the retained
construction inputs, not just the snapshot's separate wheel directory.
Progress goes to stderr. No runtime is installed, selected or started.
Profile preparation runs with the preparer's already-running tooling interpreter,
not the minimal wheel-build environment. The build toolchain and application
target environment retain their separate identities. Concurrent compositions
bind changefile readers to their own scratch modules without replacing a
process-global import alias, and reserve distinct scratch paths atomically.

Windows application preparation selects a standard CPython interpreter from
the native version and wheel-platform tags. Unsupported interpreter variants
refuse before application output; no cross-target marker environment is added.

Unchanged complete sibling outputs may be reused after source, composition,
toolchain, wheel, profile target/extras/policy and application validation.
Automatic discovery first requires native owner-private directories protected
against other users' writes; shared or unverifiable directories are not reuse
sources. Completion hashes do not authorize execution of a retained toolchain.
Missing or incompatible retained outputs can lead to ordinary preparation;
a failed selected worker is an error, not permission to replay construction.
Deadline-bound profile, composition and Git children use the existing native
process owner, with descendant settlement reserved inside the original deadline.
The separate `--reuse-install-source` mode revalidates explicitly selected
first-party wheel inputs and never silently falls back to a build.
Neither mode is a new release-approval token or installation-completion claim.

## Support every application must provide

| Boundary | Required behavior | Existing implementation home |
|----------|-------------------|------------------------------|
| Source and dependencies | Consume the canonical promoted snapshot, associated first-party wheels and target-compatible lock/profile. Keep `pyproject.toml` authoritative, configured feeds and seven-day seasoning intact. Reuse complete outputs for an unchanged selected Git commit only when their existing compatibility/completion checks pass. Local promotion does not increment package versions. Missing outputs go back through their promotion owner; corrupt outputs are not a source-fallback signal. | [Project emitter](../../tools/runtime_application_project.py), [profile preparation](../../tools/runtime_profile.py), [promotion](../pipelines.md) |
| Copyable slot | Carry the complete project, lock, wheels, resources and all declared entry points into the version slot. Use a nested `.venv`; no editable runtime or deployed dependency on the original checkout, payload or staging path. | [Application installer](../../libs/installer-engine/runtime_application_install.py), [immutable runtime](durable-vs-versioned-runtime.md) |
| Slot-local front doors | App and `install` launchers run through the same locked UV project. Preserve arguments and exit codes in CMD, PowerShell and POSIX shells. Resolve the physical installation consistently when root overrides are absent; keep install, configuration/data and socket/control roots distinct where supported. Ambient Python/UV settings must not select another environment. | [Canonical launcher generator](../../libs/installer-engine/runtime_application_binstubs.py) |
| Stage only | `install --stage` prepares the candidate environment without changing global commands, current/last-good markers, provider/service registrations, active routes, durable live work or incumbent ownership. Target full staging also admits app-owned candidate readiness without publishing live work. A private candidate listener is allowed only through its existing passive/ownership contract. | Generated `install` entry; application candidate/readiness owner |
| Setup | Make app-specific setup repeatable, use declared arguments, preserve existing configuration/secrets/data and distinguish an absent optional integration from a broken required one. Stage cannot call a setup action that mutates active state. A CLI-only app may have no setup callback. | `application.json` setup declaration and owning app |
| Activate/start | Normal `install` may compose missing preparation. Reuse the qualified slot, publish through the existing transaction/selection owner, invoke the declared startup action and verify the actual installed generation. Return an explicit failed/partial outcome when readiness fails; an import, open TCP port or child PID alone is not success. | Application installer, existing app setup/start callbacks |
| Stable commands/registrations | Global management front doors select the current slot and delegate; they must not contain a mutable checkout or payload dependency. Preserve attributable payload-local agent commands and installation-cell rules. Migrate owned task/unit/provider registrations through their owner; preserve edited or foreign registrations. | Existing command, installation and service owners |
| Repeat and rollback | Reinstalling an unchanged healthy selected generation reuses its native owner, route, data handle and completion, rather than restarting it. Preserve a state-compatible last-good generation and an independently usable manual recovery path. | Versioned runtime plus app currentness/recovery |
| Plugin connector | Loading a plugin may discover/report the installed backend and emit bounded guidance. It must not build, install, repair, stamp, register providers or start services merely on session start. Exact selected commands remain attributable. | Owning plugin hook and command declarations |
| Timing and diagnostics | Measure the real cold, repeat and update front doors, including required preparation. Respect the actual caller deadline; the bounded core ordinary-install lane uses its existing 300-second budget. Report phase/child failure and elapsed time. Do not fix timeouts by lengthening waits or timing only an already-built inner step. | Existing installer and test runners |

### Common forward environment contract

The [standard-environment intent](../../visions/plugin-services/runtime-assembly/README.md#standard-python-environments)
applies across Python runtime producers and consumers. For the generated
application model, the project contains `pyproject.toml`, its exact `uv.lock`,
and a physical project-local `.venv`. The interpreter is relative to that
environment: `.venv/bin/python` on POSIX or `.venv\Scripts\python.exe` on
Windows, with ordinary `pyvenv.cfg` and environment-prefix behavior.
Installation, configuration/data and cache roots remain separate.

Windows projects select a version/OS/architecture-qualified interpreter through
their relocatable `.python-version` request, with architecture derived from the
admitted interpreter ABI tags rather than the host machine label. A synthetic
UV target-platform override must not rewrite dependency-marker values: the
profile and lock retain their exact admitted marker environment. Missing or
ambiguous Windows ABI inputs still refuse preparation.

Stored completion and command bytes do not establish current configuration or
live service readiness. An application with setup or startup callbacks still
enters its owner's idempotent configuration/readiness path on repeat; a generic
publication-only no-op is limited to applications without either callback.

Copy the application inputs, then construct the environment in its addressed
slot; do not treat an already-created virtual environment as a relocatable
artifact. Keep source and dependency synchronization with the installation
owner. A consumer must not use an ordinary auto-syncing `uv run` as a
readiness probe or let ambient environment settings redirect the selected
runtime. Direct execution of the qualified environment does not require
activation scripts.

Use the existing installation/command owner and canonical physical-environment
resolver rather than fabricating legacy receipts, flattening application
projects or adding aliases for stale consumers. Retain only the documented
compatibility interpretations that remain supported; they do not define the
forward layout. Bytecode belongs in the existing external cache, not a mutable
exception inside the completed slot.

These are convergence requirements, not a claim that every consumer is
converted. The [app matrix](../../efforts/active/source-managed-runtime-installs/wheel-first-contributors.md#per-app-tracking)
and [native-consumer slice](../../efforts/active/source-managed-runtime-installs/native-application-consumers.md)
retain their separate qualification boundaries. External references:
[uv project environments](https://docs.astral.sh/uv/concepts/projects/layout/#the-project-environment)
and [Python virtual environments](https://docs.python.org/3/library/venv.html).

### Manager first-party core and retained hosts

Manager's wheel embeds the selected first-party library core under its normal
import names, preserving class identity, required package resources and notices.
This includes the imported `agent_procutil`, `zdd`, `update_driver` and
`ssh_manager` code. A shared library's name is not evidence of an independent
service. Actual external tools and service hosts retain their existing owners.

Third-party packages remain ordinary locked distributions. Preserve their
`Requires-Dist`, extras, marker-dependent closure, native compatibility,
distribution metadata and feed/origin admission. Development-only tools are not
runtime dependencies. There is no third-party vendoring, metadata alias layer or
query-site rewrite implied by the first-party artifact boundary.

Reuse the canonical inventory, selected-source closure, standalone build/resource
hooks, native lock/profile and wheel inspectors. The existing
[lock-input generator](../../bundle/README.md) projects third-party requirements;
its output is inert, not a resolved or approved lock. The
[consolidated core artifact](../install-contract.md#consolidated-first-party-core-artifact)
is a manifest/ZIP of separate first-party wheels, not a monolithic Manager wheel.
Neither mechanism alone establishes Manager's new embedded-core contract.

The Manager producer must account for the selected first-party dependency edges
and their transitive third-party requirements in authoritative static declarations.
Its final wheel has one owning distribution and a complete native `RECORD`.
Do not merely remove first-party `Requires-Dist`, copy independent installation
receipts or invent installed library distributions. Keep build-input association
distinct from the installed Manager-plus-third-party distribution set; use the
existing identity, metadata and installed-origin/content owners.

For update/controller survival, prefer the existing exact-generation
`runtime_command` and slot-local native retention bootstrap. Preserve physical
interpreter/completion identity, retention before imports, process custody,
admission, drain and eventual retirement. Do not copy or relocate a ready venv
or introduce per-library services, permanent venvs or a satellite framework.
Source/provider-resource parity and import-only checks do not prove the final
artifact, actual serving or cutover survival.

The embedded Manager wheel belongs in its own application environment. Profiles
containing Manager reject conflicting payload ownership involving that wheel before
construction, including its embedded core combined with separate library distributions.
The default consolidated-core inventory and identity remain unchanged: an
incompatible combined installation refuses, rather than silently dropping Manager
or a required library. Historical unbundled inputs with no file collision retain
their normal admission path.
Profiles for other applications retain their existing admission behavior.

The standalone producer reads Manager's flat standard `first-party-core`
dependency group and existing source mappings. Its source association retains
the complete selected library directories as build resources, including their
documentation and tests. The canonical setuptools owner uses ordinary package
discovery and package-directory mappings, preserves declared data and LICENSE
notices, and includes the build inputs in a self-sufficient sdist. Static Manager
dependencies remain the ordinary third-party graph; the declaration regression
uses the existing root-scoped lock-input traversal without qualifying a feed or
removing the separate Linux local-wheel refusal.

Worktrees' source-aware hook CLI retains its existing `session-lifecycle --stdin
--source-bound` and `deregister-session --stdin --source-bound` operations.
The provider ships `scripts/hook-runtime-contract.json` inside its installed
`agent_worktrees/_runtime_assets` resources, with contract identity
`agent-worktrees.hook-runtime`, integer version 1 and additive capabilities.
Version 1 declares `session-lifecycle-v1`, `session-start-source-v1` and
`session-end-source-v1`; these names have the same meaning as the resident
handshake. Readers ignore unrelated fields/capabilities and require the specific
operation's support before sending lifecycle work. The descriptor comes from
the selected environment, not the current plugin. Package-version equality is
not a compatibility requirement. Missing or unsupported application metadata
reports unavailable lifecycle work without installation or an effectful retry.
Historical flat-slot callers retain their existing legacy compatibility path;
the descriptor and old CLI spelling remain supported through provider rollback.
Resident calls continue to qualify the running peer through its live handshake.

Worktrees' plugin hooks select their own payload helpers even when no historical
`bin` snapshot exists. The legacy command-publisher binding in the catalog still
describes that publisher, not ownership of an independently installed application.
An unresolved legacy binding is not evidence that the application is missing.
The existing read-only backend status and the exact payload-local command use
the selected completed runtime without claiming a legacy publication receipt or
requiring its package version to match the plugin. Independent source selection
does not waive installation-context or managed-publication policy.

**Current stage limitation:** the shared generated `install --stage` synchronizes
the environment and returns `staged`; it does not by itself prove canonical
completion, app readiness, active publication or successful installation.
Regular installation performs additional source/profile admission and
application callbacks. Track environment staging, candidate qualification and
activation separately; a declaration in `install_callbacks()` is not evidence
that its callback works from an installed wheel.

Internal callbacks such as `__source-setup` and `__source-start` are selected by
the owning declaration, not a universal operator API. Add receiver support and
mixed-version tests before changing a caller; do not make all apps implement
new command spelling merely to satisfy this document.

The Source application's activation child permits native daemon breakaway
through its retained process owner. Construction children remain contained by
default. This allowance does not prove daemon independence: the application
must still use attributed startup and verify survival after all caller-owned
cleanup Jobs close, while a restrictive outer Job remains an honest refusal.
The retained bootstrap checks native same-file interpreter-prefix identity,
so Windows extended-path spelling can load long native-extension paths without
accepting a different slot or bypassing completion and retention checks.
Source activation and callback children start from a safe Windows directory,
then enter the physical version slot inside that same isolated interpreter.
This preserves callback arguments, exit status and working-directory semantics
for long repair slots without changing their names.

## Additional contract for a resident service

1. **Prepare independently.** An admitted incoming generation is constructed
   independently and preferably starts beside the incumbent on its own endpoint.
   Ordinary update/repair does not depend on old IPC, uninstall or artifact
   deletion. Do not require old recovery or retirement proof before incoming
   startup; incoming selection/admission still prevents duplicate exclusive work.
2. **Be non-mutating before ownership.** Candidate hydration cannot mark the
   incumbent's sessions stopped, interrupt open turns, dequeue tasks, claim
   live hosts or start competing jobs. Register read-only/HTTP credential
   sources where supported, but do not reclaim a pinned relay/listener from a
   serving predecessor. Defer exclusive listeners and mutations until the
   existing ownership/exit boundary permits them.
3. **Publish actual readiness.** Bind readiness to the selected installed
   generation, native PID/birth identity, authentication, required data access
   and app-specific work admission. Publish discovery through the existing
   routing owner, not a second stable-port proxy or an installer-owned reaper.
4. **Prefer outgoing self-retirement.** It observes a ready selected successor,
   closes its own admission, preserves/checkpoints supported in-flight work,
   detaches transferable children and releases only its own claims. Confirm
   native exit before claiming that retirement actually completed. The
   installer returns after all requested incoming readiness and current
   selections, without joining retirement. An optional single finite async
   fallback has a longer-than-cutover grace and rechecks central role, native
   identity, installation and then-current generation at mutation. It excludes
   future/reselected generations, session hosts/keepers, session-owned Bridge/
   agent processes, foreground clients and protected connectivity; no periodic
   resident reaper is required.
5. **Keep recovery explicit.** Compatible transfer is preferred, not a
   universal zero-loss guarantee. Incompatible, unavailable and non-settling
   peers follow the existing bounded cold-takeover contract through the same
   authorized replacement owner, without another force/repair flag solely for
   unsupported graceful cutover. Report interruption and prove native identity,
   owned termination scope and exit/write exclusion. This does not authorize
   broad historical cleanup or termination of separately owned sessions.
   Consumers re-resolve and reconnect with bounded retry, reconciling unknown
   effects rather than blindly replaying them.
6. **Settle cancellation.** Async cancellation or a shutdown reply does not
   prove native threads, writers or processes exited. Retain the actual side
   effect's owner until completion, including event-loop teardown. Use
   [canonical process custody](../../libs/agent-procutil/README.md), headless
   spawning and leased test instances; no scheduled-task, privilege or
   untracked-daemon workaround.

### Failure/commit boundaries

| Failure point | Required result |
|---------------|-----------------|
| Acquisition, wheel admission or staging | Incumbent commands, selection, serving route, configuration and work remain unchanged; retain useful candidate diagnostics. |
| Setup/candidate readiness before publication | Do not mutate incumbent history or claim success. Dispose only of attributable candidate resources through their owner. |
| Selector published but serving data/route commit fails | App-specific guarded recovery restores the exact still-owned prior selection/route/handle, or reports an unsettled partial transition. No unconditional overwrite of a concurrent owner; settle the failed candidate before retry. |
| Serving commit succeeded, reply lost or retirement delayed | Observe actual current state and follow the existing commit-forward/recovery contract. Do not restore only a marker while the incoming service still owns live work, or report a timeout as success. |
| Existing layout cannot be recognized | Preserve the independent installation-application versus readiness/handoff outcome in [fresh-default installation](../../efforts/active/source-managed-runtime-installs/fresh-default-installation.md); do not invent a new receipt/layout gate or treat uncertainty as permission to delete old state. Actual I/O failure remains literal. |

The generated generic installer publishes the selected slot before invoking its
declared startup action, but advances `last-known-good` only after that action
acknowledges completion. The application still owns actual readiness verification;
a marker, process PID or arbitrary zero exit is not independent health evidence.
The final construction-input check runs inside the guarded transaction before
last-good and success records are written, not during post-success context exit.
Direct qualification consumers, including Manager's separate self-install owner,
retain their existing exit-time check; only this generic transaction owns its
final verification explicitly.
Application setup, startup/readiness and construction-input verification run
outside selector-update locking. Only launcher/selector writes, the final
success commit and guarded pre-start recovery use brief atomic updates.
The admitted application replaces differing existing ordinary files at its
declared global command names without requiring recognized incumbent launcher
bytes or old-runtime provenance. Other command names and application data are
not replacement targets. Linked, non-file or oversized targets remain literal
publication errors. The transaction snapshots prior bytes, detects a command
changed during preparation, and restores replaced bytes on pre-start failure;
it does not replay over a concurrent writer.
Callbacks may therefore use the existing selector owner without waiting for
their installer to release a lock. A callback that confirms the same selected
generation is not a competing winner; the final check accepts the canonical
LF/CRLF marker forms, while preserving a genuinely different selection.
Setup failures leave selection unchanged. An incomplete publication rolls back
before its original short critical section is released, so recovery cannot
undo a later installer's commit. Without a startup action, selection and success
commit together in that one short update. Otherwise, once publication commits,
a failure, timeout or lost reply has an **unknown** outcome: selected commands
may already have admitted work. The installer raises
`ApplicationStartupUncertain` with the selected `slot` and the original exception
as its cause, retains selection and management launchers, and does not attempt
native cleanup or infer that nothing started. Cancellation retains its original
exception after reporting the same uncertainty. Concurrent selector/launcher
winners are preserved.

This is a conservative change to failure defaults, not a new callback protocol:
existing arguments and deadlines are unchanged, and there is no new force flag,
daemon manager or ownership receipt. A startup failure does not create a new
success record or promote an unacknowledged candidate to last-known-good;
an existing record remains historical evidence, not proof of this attempt.
Reconcile actual serving/data ownership through the application's existing
lifecycle owner before retry or reverse selection. The generated installer does
not itself provide application recovery or automatically retry an unknown action.
Manager's separate `self-install --apply` owner is unchanged.

On Windows, construction health probes preserve long physical repair slots
without passing that slot as `CreateProcess`'s initial cwd, which rejects long
values even with extended-path spelling. The same selected isolated interpreter
enters the canonical native slot path before executing the original probe.
Source/profile, wheel-content, interpreter and completion checks remain intact;
no short-root alias, alternate environment or installed-slot rewrite is used.

### Read-only installed application Source

The copied canonical `runtime_application_install.py` exports
`qualify_installed_application(slot, *, component, command, entry_point, deadline=None)`.
Startup receivers call it in the existing isolated, no-bytecode (`-I -B`)
interpreter context, with their expected component and declared command/entry
point. It requires the caller's actual nested `.venv` interpreter, selected
numbered physical slot and matching canonical completion. It reuses the
constructor's Source/profile/construction association and bounded wheel checks,
then verifies frozen origins and immutable installed-environment metadata.
An inherited absolute monotonic deadline may narrow the existing 600-second
input-reading allowance; this does not increase the install caller's deadline.

The immutable `InstalledApplicationSource` result contains `slot`, `root`,
`python`, the original canonical `SourceIdentity` as `identity`, `payload_hash`
and `profile_lock_id`. A standalone service therefore retains component
`ssh-service` and its original payload assembly identity: its emitted
`uv-application-agent-ssh-service` wrapper is not treated as the original payload.
The Source library's factored `parse_source_identity()` validates claimed
document fields only. `read_source_identity()` still verifies the original
payload's package identity and actual assembly bytes.

The reader takes no lifecycle lease, publishes no completion or selection, starts no
process, and imports no application. It must not be replaced with a call to the
mutating `_qualified()` constructor. It also does **not** rerun application
health or grant root, principal, privacy, repository or provider admission.
Callers still own those gates and actual native readiness/custody.

This is provider-first API delivery through newly emitted canonical resources,
not a change to an existing callback protocol. Old copied helpers do not gain
the callable automatically; missing support refuses the dependent action,
without falling back to payload-only parsing, invented identities or a build.
No `source_descriptor` or installation-input schema change is required.

## App-specific implementation and acceptance

The [app matrix](../../efforts/active/source-managed-runtime-installs/wheel-first-contributors.md#per-app-tracking)
names the source and next acceptance for every runtime root. Each contribution
must carry a few regression tests for its changed behavior plus the following
real-consumer evidence, not just another mocked callback test:

- Fresh isolated install, stage-only no-effects, normal install, unchanged
  repeat, distinct-source update and state-compatible reverse selection.
  Exercise the real slot/global front doors with root overrides unset as well
  as supported explicit roots, on each claimed native platform/shell.
- App-owned readiness and a harmless real operation. Use credential-free
  fixture peers when appropriate; identify that scope and any untested external
  venue instead of calling a mock peer a production qualification.
- Failed incoming construction/start, locked endpoint/data-handle publication,
  refusal/timeout and successful retry, preserving incumbent state and open
  work. Exercise enabled optional services, not only relay-disabled cases.
- For residents, retain native identity/handles, close every fixture-owned
  caller Job/containment boundary and prove the same daemon survives. Prove
  displaced and finally stopped native actors exit, including owned children;
  SDK settlement alone is not every native-lifetime assertion.
- Preserve configuration, durable rows/files, authentication, ownership and
  unrelated resources. Retain failed-run evidence before ephemeral fixture
  cleanup; document exact source/profile, timing, outcome and remaining gaps.

Use the repository's existing [test portfolio](../../TESTING.md), clean-room
lanes and required CI/review. Extend an existing fixture or owner rather than
adding another portfolio runner. Publication, Main promotion, host rollout and
live adoption are separate outcomes.
