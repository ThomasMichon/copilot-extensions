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
    -> settle remaining outgoing retirement obligations
    -> retain last-good state, recovery evidence and unfinished obligations
```

The [effort outline](../../efforts/active/source-managed-runtime-installs/wheel-first-contributors.md#ideal-install-and-update-sequence)
adds bootstrap, background preparation, atomic-toolset and failure sequencing.
The sequence is a target, not executable CLI syntax. In particular, a
portfolio-wide atomic selector and complete stage-time readiness are not
established by today's generated per-app installers.

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

## Additional contract for a resident service

1. **Prepare independently.** An admitted incoming generation is constructed
   independently and preferably starts beside the incumbent on its own endpoint.
   Ordinary update/repair does not depend on old IPC, uninstall or artifact
   deletion. Bounded cold takeover may retire the exact verified predecessor
   before incoming readiness; exclusive resources still require old-owner exclusion.
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
   actual predecessor exit before exclusive takeover or cold replay. The
   install caller may return after admitted startup; retirement remains a
   recorded owner obligation, not an unobserved background success.
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
Callbacks may therefore use the existing selector owner without waiting for
their installer to release a lock. A callback that confirms the same selected
generation is not a competing winner; the final check accepts the canonical
LF/CRLF marker forms, while preserving a genuinely different selection.
Setup and publication failures before startup retain guarded rollback. Once a
nonempty startup action is invoked, a failure, timeout or lost reply has an
**unknown** outcome: it may already have admitted work. The installer raises
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
