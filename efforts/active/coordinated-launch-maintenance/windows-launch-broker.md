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
- [x] Implement the canonical broker protocol and owning installer integration;
  retain current public process-control behavior and explicit failure reporting.
- [x] Unit tests cover wrong owner/instance, stale registration, malformed and
  over-budget input, missing prerequisites, failed stage/adoption/resume,
  disconnect/ambiguous acknowledgement and retryable cleanup failure.
- [x] A native negative fixture puts the candidate and caller in different
  immediate Jobs under one shared outer cleanup Job. It must not receive a
  durable-success result. The control dies when that outer Job closes.
- [x] An approved isolated Windows lane activates the actual owned broker,
  closes all fixture-owned caller Jobs after acknowledged launch and observes
  the resident independently through two complete cycles. Prove actual PID
  and exit/control semantics, exact argv/environment/log delivery, bounded
  control reaping and zero visible windows or foreground changes.
  _Two actual protocol cycles, all caller-Job closures, exact delivery, actual
  cleanup and hidden own console passed in run 39876801692. Candidate
  `ef7096e75e` additionally passed run 40630822365 with a visible, PID-owned
  GUI canary, confirmed foreground observation and canary retirement before
  5,755 lifecycle observation samples. No new visible window or foreground
  change was observed during both actual protocol cycles and installer work._
- [x] Exercise clean first adoption, repeat adoption, update, failed-update
  rollback, status, stop and uninstall in that approved disposable lane.
  Assert no stale registrations or abandoned owned instances remain.
  _Run 39878472832 exercised the actual lifecycle, including unhealthy action
  refusal and restoration after an injected publication failure. Both native
  resident cycles and final owned cleanup passed._
- [x] Prove replaced payload/checkout paths can disappear without breaking
  the installed broker. Test simultaneous starts and generation cutover or
  demonstrate the finite-worker exemption above.
  _Run 40634644449 qualified candidate `c7c964084a`: the actual cell factory,
  retained canonical API/completion and cold/repeat native Task adoption;
  deletion of the disposable payload before two concurrent installed callers;
  distinct native residents surviving closure of both caller Jobs; owned
  child retirement and Task removal. Application packages remain synthetic;
  this does not establish a released full-product construction contract._
- [x] Exercise the actual maintenance and execution-driver consumers with
  the broker: handoff precedes a gated installer, equivalent requests coalesce,
  distinct scopes finish, and explicit update cannot overlap mutation.
  _Exact candidate `3f92119913ff893fe8d3035d744ec601c7c1fb1a` passed all three
  jobs in run 40660567152. Real Worktrees cold construction and Task adoption,
  gated automatic SSH provisioning, equivalent pending coalescence, distinct
  scopes and manual-update exclusion passed. Handoff took 2.125 seconds before
  the real installer gate opened. All submitter Jobs closed before release;
  canonical request, project, both runtime and Task cleanup were confirmed.
  The positive GUI/foreground canary retired, followed by 16,715 samples with
  zero new visible windows or foreground changes. The complete finite-removal
  packet passed 192 cases with five platform skips. This is disposable-host
  qualification, not installed rollout._
- [x] Run shared portable suites and native POSIX regressions so the Windows
  edge does not alter non-Windows lifetime or dependency behavior.
  _The bounded Linux lane passed 97 process cases with one platform skip and
  62 finite execution cases with two Windows-only skips in run 40632392498;
  both lanes also passed exact candidate `c7c964084a` in run 40634644449.
  The existing strict shell interpreter-custody check rejects the hosted
  tool-cache mode `0777`; the fixture selects and verifies the owned OS
  interpreter (`0755`) rather than weakening that production guard._
- [ ] Reconcile documentation, source guards and all consumer changefiles.
  Merge only after required review and CI, with no unresolved lifetime finding.
- [ ] Observe promotion separately. Before installed rollout, obtain the
  deployment authorization and obey the existing installation owner/holds.
  No source merge or mock registration is a running-state qualification.

### Real consumer qualification lane

The separately bounded `windows-broker-consumers` job uses a credential-free
private profile on a disposable hosted Windows runner. It materializes the
actual Worktrees and SSH payload closures, then runs the canonical Worktrees
`provision` action without contained-test suppression. Automatic maintenance
uses the real `launch-update automatic --no-update --with-reconcile --detach`
consumer and a missing SSH runtime, not a substituted planner or product.
The fixture holds the SSH installer's own exclusion file until detached
handoff, equivalent-request coalescence, distinct scopes and pending manual
Worktrees runtime update have been observed. Every submitter Job is closed
before the installer gate is released. Canonical uninstall and native Task
inspection own the cleanup proof; errors remain failures.

The source trace also corrected the native installation adapters' missing
`engine-launch-update` execution-owner case. It retains the existing
authenticated participant rather than creating another queue or bypassing
admission. Unknown backend and changed-request refusals remain unchanged.

This lane is not yet a passing qualification result. It neither changes the
four-minute component lane or six-minute portable lane nor authorizes shared
host installation, activation or rollout.

The first real-consumer run, `40638624361`, stopped before construction:
the source materializer correctly refused an obsolete checked-in ZDD shadow
under Worktrees, whose dependency already points to canonical `libs/zdd`.
The shadow is removed rather than weakening materialization or keeping a second
implementation. A tracked-source assembly regression covers both actual
consumer payloads. The same source check found a duplicate Codespaces snapshot
dependency-map key that prevents native PowerShell parsing; the duplicate
entry is removed. Component and portable jobs passed that run, independently
of the failed consumer gate.

The next candidate, `54476e89c5`, passed source assembly but the real cold
installer's first interpreter query failed and the positive desktop witness
observed one new window and foreground change in run `40639776702`.
The next source correction routes admission queries, launcher inventory and
native installer handoffs through the already-owned no-window helper. Its
streaming mode drains both native pipes concurrently into the caller's
existing streams; merely leaving child output unredirected loses it under a
headless PowerShell parent. Both native PowerShell hosts now preserve exact
stdout, stderr and nonzero status in focused controls. Full hosted consumer
qualification remains open.

Run `40641771527` passed the component and portable jobs. Its real cold entry
returned exit 1 without captured command output, and automatic bootstrap
discovery prevented confirmed cleanup. This remains a failed disposable-lane
result. The fixture now invokes installation and removal from the declared
project directory, retains the same explicit outside bootstrap interpreter
for both, and records bounded durable request outcomes/logs before cleanup.
The `windows_broker_consumers` dispatch flag runs only this fifteen-minute
lane for fixture diagnostics; it does not weaken the existing component or
portable gates or their time limits.

The consumer-only run `40642611330` retained a completed
`engine-native-install` request with exit 1 and an empty log. Canonical
Worktrees removal succeeded with the explicit outside interpreter, but this
is still failed cold-construction evidence, not a maintenance pass. Native
adapters now explicitly bind child stdout/stderr to the current executor's
streams and report a nonzero installer result. The copied no-install native
driver/reentry control verifies both sentinels occur exactly once in the
owning durable request log; it does not substitute for the real consumer lane.

Run `40643790035` then exposed the actual prerequisite failure: the sanitized
fixture had retained `PATH` but dropped `PATHEXT`, so native PowerShell could
not discover the installed Git executable. A real no-install application
lookup reproduced that failure before the correction and passes on both
PowerShell hosts afterward. The fixture retains this non-credential OS
prerequisite case-insensitively while continuing to exclude unrelated
credentials. No production prerequisite or lifetime guard is relaxed.

Run `40644461712` constructed the actual cold Worktrees runtime, adopted its
Task and handed automatic reconciliation to the real gated SSH installer.
It then correctly refused manual update from an unadopted project. The next
fixture uses actual headless/no-agent project registration and deregistration.
It checks coalescence of equivalent **pending** wakeups: an already-running
dynamic plan is not a pinned source target and must not absorb a later wakeup.
Cleanup waits for every admitted request before invoking canonical removers,
including on failure; it never races a partially constructed runtime.
After removal deletes the entrypoint, Task absence is checked through the
canonical read-only native inspector using the previously validated owned
definition, not through activation/file admission. The bounded lane also
executes the complete finite-removal regression packet instead of waiving the
earlier host-specific census failures.

Run `40645819307` passed actual cold construction, Task adoption and gated SSH
maintenance, but manual update lost its project-only registration after changing
into the installed payload. The ordinary-engine update now retains its invocation
directory; the separately prepared-source contract is unchanged. Canonical
cleanup also exposed two independent gaps: finite SSH removal did not recognize
its exact self-staging publisher, and legacy Worktrees removal left its adopted
Task installed despite returning zero.

Worktrees native uninstall now uses the existing execution driver, read-only
finite removal admission, canonical broker removal and finite runtime cleanup,
in that order. Both finite callbacks retain authenticated driver and teardown
custody. The fixed controller stack is checked against the running request,
source fingerprint, literal arguments, native ancestry and current PID/birth
identities; unrelated runtime/module consumers still refuse. Native uninstall
selects an external Python 3.11+ interpreter before withdrawal. The portable
finite adapter retains its existing 3.10 floor.

Local integrated installer controls passed 74 tests with two platform skips;
Manager archive and Bridge runtime compatibility packets passed 62 and 123 tests
respectively, with platform-specific skips. Runtime refactor and fixture controls
passed 179 tests with four skips after supplying their source import paths.
Install-contract, canonical Python lint and the full module-size audit passed
without widening a baseline. These are local source controls, not actual
maintenance or installed-removal qualification. The held draft still requires
the disposable consumer lane's real manual-update and Task-absence evidence.
The complete local finite-removal packet passed 187 tests with five skips
and refused one self-staged Bash control because another current-user Python
process had no inspectable image or command. That process had exited at the
subsequent read-only probe; the exact control then passed. This is a retained
fail-closed observation, not an exemption or a reason to weaken the census.
The complete packet must still pass on the disposable qualification host.

Run `40657192911` passed both original broker lanes and all 188 finite-removal
controls, with five platform skips. The real consumer completed cold Worktrees
construction, gated automatic SSH provisioning, equivalent pending coalescence,
distinct-project delivery, manual update and canonical SSH removal. Its positive
desktop canary retired, with no subsequent visible windows or focus changes.
Worktrees teardown still refused before broker removal: the finite callback
mistook its self-staged code directory for the original marketplace source when
revalidating the existing typed command binding. The outer interrupted-participant
error retained that underlying failure; it is not permission to weaken custody.

Finite removal now keeps the staged execution payload separate from its explicit
original source and project arguments, using the same arguments for initial
ownership, retained teardown custody and final revalidation. A differing source
is admitted only on the authenticated Worktrees driver path; the native controller
proof still requires that source to match the pinned request. Real typed-binding
and lifecycle controls accept the original source but reject the stage and a
different marketplace, without deleting runtime bytes or rewriting the manifest.
The focused typed-source/controller packet passed 61 tests and the contained
Worktrees adapter packet passed 31. The complete local finite suite passed
130 tests with three skips but again refused the Bash staging control on an
uninspectable current-user process. This retains the local host limitation;
the candidate still owes the complete disposable-host run.

Run `40659383718` passed all 191 finite-removal controls with five skips.
The real teardown's source and native-controller proofs passed, and canonical
Task absence was confirmed. Runtime deletion then refused because the standalone
Worktrees payload did not yet carry `installer-engine.ps1`. Worktrees now declares
that dependency through the existing native-resource sync owner, including the
matching POSIX asset. A disconnected copied-payload regression executes selective
native deletion while retaining configuration, unknown bytes and staged code.
That regression and the full sync-tool suite passed 66 tests with 49 platform
skips. This fixes packaging; the complete real consumer still requires a new
successful hosted run.

Run `40660567152` completed that qualification against exact candidate
`3f92119913ff893fe8d3035d744ec601c7c1fb1a`: original synthetic Windows activation
(1m48s), portable/native POSIX regression (51s), and actual consumer (5m16s)
all passed. Every consumer result and canonical cleanup field was true, including
Worktrees runtime deletion and broker Task absence. The source remains in held
draft #7833 pending explicit release of the review/merge hold. No shared-host
registration, update, installed recovery or rollout was performed or authorized.

An unavailable authorized Windows lane is a concrete qualification blocker,
not a license to register a test task on a shared host. Keep the containing
launch change unqualified until that lane is genuinely exercised.
