# Independent Windows launch broker amendment

Parent contract: [Coordinated Launch Maintenance](README.md).
Tracking: #6859 and #7694. This is a proposed source design, not an installed
service, successful qualification or permission to change a host.

The operator's `scheduling-only` decision separates this owned repair
(#6859/#7833) from #7788's delivery. No gate below is waived or implied complete;
the scheduling PR retains the existing execution driver's lifetime refusals.

## Authority and intent

The operator selected `source-redesign`: "Authorize the source-level broker
redesign; keep deployment separately gated". All mechanisms below are
**agent-recommended**. No task/service registration, installer execution,
shared-service restart, host update or rollout follows from this selection.

Preserve the parent's intent: healthy handoff does not join optional installers;
automatic maintenance stays coordinated rather than disabled. The broker owns
only process creation and lifetime transfer. It is not another installation
queue, updater, release controller or generic remote-execution service.

## Why the immediate-Job repair is insufficient

An ambient Windows Job need not be the caller's cleanup owner. Conversely,
absence from the caller's immediate Job does not prove independent lifetime.
The documented `QueryInformationJobObject(NULL)` query describes only the
immediate Job. A candidate in a different inner Job can still share an outer
kill-on-close Job. Closing that ancestor kills the candidate despite a passing
immediate membership check.

Keep both bootstrap and resident admission boundaries. Do not replace a
failed proof with a readiness probe, parent-exit observation, creation flag,
PID comparison or the positive nested-Job test alone. The repair must refuse
an unproven inherited lifetime and retain cleanup ownership.

## Architectural reconciliation

This closes the existing installer/picker responsive-launch intent and the
plugin-services vision's platform-native, least-privileged lifecycle and
replaceable-payload guarantees. It does not add intent for periodic maintenance,
machine enrollment, a mandatory sibling service or elevated operation.

Use the existing Windows launch, service lifecycle, immutable-runtime,
native-transport and graceful-cutover patterns. An installed broker is an
owned component of its consumer's installation, using the canonical shared
process implementation; do not require agent-bridge, Worktree Manager or a
new globally selected plugin merely to use `agent-procutil`.

The generic tier-1 user-mode ensure path remains the default for ordinary
services. This broker has a concrete additional requirement: its activation
must not inherit arbitrary submitting-command cleanup Jobs. Any scheduled
activation dependency is a deliberately selected lifecycle tier for this
component, not a new prerequisite for every service's normal start path.

## Proposed mechanism

### One independent activation boundary

Prefer the lowest-privilege Windows-owned activation that can demonstrate
the independent lifetime contract. The initial implementation candidate is
an explicitly adopted, user-scoped, on-demand Scheduled Task owned by the
consumer installer, with no periodic or logon trigger and no elevation.
Its stable action resolves the selected immutable broker runtime.

OS-owned activation provenance, the actual broker instance and native process
custody must jointly establish that the broker was not created beneath the
submitting command. Merely finding a task definition or an endpoint is not
that evidence. Qualification must include the shared-outer/different-inner
negative case and closure of every fixture-owned caller Job.

If this activation cannot be registered or cannot satisfy lifetime, identity,
headlessness and child transfer in an approved isolated lane, stop with a
precise unsupported/prerequisite result. Do not silently elevate, adopt a
system service, relax Job checks or substitute the old WMI inference.
An alternative privileged lifecycle needs its own explicit decision.

No normal spawn call installs or registers the broker. Configuration and the
consumer's canonical install/update flow own adoption and lifecycle. Missing,
stale or unavailable adoption is explicit; it is not permission to provision
inside a healthy handoff or start a foreground installer.

### Native local handoff

Use an OS-native, local-only named-pipe boundary scoped to the installation
and current user. Reuse existing canonical transport/lease helpers where
they satisfy the contract. Authenticate the submitting user and the owned
broker instance; an arbitrary same-named pipe is not authority.

Keep exact argv, explicit durable cwd, environment overrides/unsets and log
destinations. Credentials may travel only through the live authenticated
handoff when needed for the launched process; never persist them in task
arguments, registrations, request files, logs or discovery metadata.
Do not introduce shell evaluation or a network listener.

Stage the resident suspended, retain its actual native identity, and transfer
usable process control before resume and acknowledgement. Refuse a resident
still bound to a broker activation scope that will be destroyed when the
broker retires. A wrapper PID, queued receipt or early readiness response
is not successful ownership transfer.

Bound request size, handshake, launch, acknowledgement and cleanup. A client
disconnect or lost acknowledgement must not launch a duplicate or leave an
unowned suspended process. Preserve existing contained-test suppression;
contained calls neither activate the registered broker nor escape containment.

### Installer and generation ownership

The consumer's installer owns the stable registration, adopted configuration,
immutable runtime slots, endpoint discovery and standard lifecycle verbs.
Registrations and durable cwd never point into a payload, checkout, worktree
or session folder. Independent installation remains possible without Copilot
or another plugin.

Keep the broker finite/on-demand where the contract permits. If it must remain
resident, it takes on the full shared-endpoint graceful-cutover contract:
health-gated successor, one admission owner, drained accepted requests,
confirmed transfer and retained generations while anything still uses them.
Do not stop or re-register a healthy broker merely because a new slot exists.
If a finite implementation makes cutover unnecessary, demonstrate that every
accepted child has independent custody before the instance exits.

Status distinguishes installed, configured, activated, accepting, draining
and failed states. Stop/uninstall acts only on owned registrations and
identity-pinned processes, surfaces live-child obligations and preserves
unrelated state. No PID-only kill, age-based takeover or force reset.

### Integrate existing execution ownership

Use the execution driver landed in #7811 for actual installation mutation.
The launch layer may describe optional desired work, but must not retain a
competing mutation lock/owner beside that driver. Preserve distinct project,
source, installation and inherited-auth scopes without credential persistence.
First-use recovery, explicit update and reconciliation retain their canonical
admission and ownership paths.

The broker is a process-lifetime dependency, not evidence that every released
environment is constructable under the configured feed. Feed construction and
CI/promotion enforcement remain the separate parent gate.

## Implementation and validation gates

- [x] Land this amended contract through ordinary review before broker code.
- [x] Complete the other #6859 review corrections: enforce census deadlines
  during work, retain retryable descendant/Job handles after unconfirmed exit,
  and make synthetic PID fixtures independent of the runner PID.
- [ ] Implement the canonical broker protocol and owning installer integration;
  retain current public process-control behavior and explicit failure reporting.
- [ ] Unit tests cover wrong owner/instance, stale registration, malformed and
  over-budget input, missing prerequisites, failed stage/adoption/resume,
  disconnect/ambiguous acknowledgement and retryable cleanup failure.
- [x] A native negative fixture puts the candidate and caller in different
  immediate Jobs under one shared outer cleanup Job. It must not receive a
  durable-success result. The control dies when that outer Job closes.
- [ ] An approved isolated Windows lane activates the actual owned broker,
  closes all fixture-owned caller Jobs after acknowledged launch and observes
  the resident independently through two complete cycles. Prove actual PID
  and exit/control semantics, exact argv/environment/log delivery, bounded
  control reaping and zero visible windows or foreground changes.
  _Two actual protocol cycles, all caller-Job closures, exact delivery, actual
  cleanup and hidden own console passed in run 39876801692. Full
  desktop/foreground observation remains open._
- [x] Exercise clean first adoption, repeat adoption, update, failed-update
  rollback, status, stop and uninstall in that approved disposable lane.
  Assert no stale registrations or abandoned owned instances remain.
  _Run 39878472832 exercised the actual lifecycle, including unhealthy action
  refusal and restoration after an injected publication failure. Both native
  resident cycles and final owned cleanup passed._
- [ ] Prove replaced payload/checkout paths can disappear without breaking
  the installed broker. Test simultaneous starts and generation cutover or
  demonstrate the finite-worker exemption above.
- [ ] Exercise the actual maintenance and execution-driver consumers with
  the broker: handoff precedes a gated installer, equivalent requests coalesce,
  distinct scopes finish, and explicit update cannot overlap mutation.
- [ ] Run shared portable suites and native POSIX regressions so the Windows
  edge does not alter non-Windows lifetime or dependency behavior.
- [ ] Reconcile documentation, source guards and all consumer changefiles.
  Merge only after required review and CI, with no unresolved lifetime finding.
- [ ] Observe promotion separately. Before installed rollout, obtain the
  deployment authorization and obey the existing installation owner/holds.
  No source merge or mock registration is a running-state qualification.

An unavailable authorized Windows lane is a concrete qualification blocker,
not a license to register a test task on a shared host. Keep the containing
launch change unqualified until that lane is genuinely exercised.
