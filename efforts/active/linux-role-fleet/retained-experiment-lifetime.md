# Retained experiment lifetime

Back to [Linux role fleet](README.md) and its
[persistent reproducer](persistent-isolated-reproducer.md).
Existing shared lifecycle owner: #8403 and
[Leased Daemon Instances](../leased-daemon-instances/README.md).

**Status: behavior and concrete API proposals reviewed in #9176 and #9183;
existing-owner integration settlement remains before implementation.** This does not
transfer the existing owner's compiler-free
Python/native-binding work or authorize provisioning, process actions or rollout.

## Selected behavior

The operator selected the literal value:

> retain-accepted-work

The selected option was presented as:

> Preserve accepted work; review an extension to the existing lifecycle owner (recommended)

The choice selects source/design work only, not permission to build or start
resources. It preserves the reproducer's existing requirement: client/driver
loss, expired case admission or unavailable monitoring refuses new cases without
automatically stopping accepted work. An explicitly authorized container or
service stop remains a distinct experiment action.

The contract and sequence below are **agent-recommended implementation means**.
They close the existing service-model
[`attributed-daemon-instances`](../../../visions/plugin-services/README.md#attributed-daemon-instances)
distinction between explicitly disposable tests and protected recoverable work.
They do not weaken ordinary test expiry or create a general host-side exemption.

## Current mismatch

`libs/agent-procutil` already owns attribution, process-birth identity, shared
instance records, test-lifecycle declarations, renewal, expiry and exact-artifact
cleanup. Both actual Bridge startup and Dispatch coordinator serving enter
through its `daemon_entrypoint` decorator.

Ordinary unattended tests have renewable finite leases and a hard maximum.
Interactive tests require their own finite pause-tolerant policy. The test
owner renews the declaration; the child independently observes expiry and
self-retires. Explicit teardown also expires and cleans its disposable instances.
An inherited test context overrides a service's production-purpose declaration.
These are intentional guarantees, not bugs to disable.

Using that disposable lifetime unchanged for a retained service fixture would
stop it after owner loss/expiry, contrary to the selected persistence behavior.
Calling it production merely to suppress expiry is not a solution. A longer
deadline only postpones the mismatch.

## Existing-owner extension boundary

- Require an explicit, narrowly scoped retained-experiment classification through
  the existing instance owner. Neither a temporary path, environment flag,
  container marker nor agent activity infers this class. Ordinary tests retain
  their current defaults and cannot silently opt into indefinite host daemons.
- Admit it only under an explicitly selected, isolated, retained container venue
  and existing resource custody. The lab controller first qualifies the actual
  physical resources and guest boundary; the service instance keeps its existing
  native process/source identity and original record. No Docker socket, host
  control API, new instance database or per-consumer attestation protocol enters
  the guest.
- Separate permission to admit a new case from the lifetime of already accepted
  work. Lost/stale storage monitoring, driver loss and expired case permission
  refuse new cases. They do not expire or relabel the accepted service instance,
  clear its task/Bridge history, replay an effect or grant teardown authority.
- Keep original task, creation, session and service authorities distinct.
  Reattaching to a retained experiment observes the same resources and existing
  journals. A free operation lock or dead original client is not settled work,
  accepted ownership transfer or permission to launch a replacement.
- Keep stop, fault injection, resource retirement and artifact deletion explicit
  and separately approved. The ordinary disposable-instance collector must hold
  retained experiment records, never reinterpret them as expired disposable
  state. Unknown custody, replaced paths/resources and unsettled effects remain
  visible holds, not automatic cleanup or success.
- Preserve production non-expiry, singleton/cutover behavior, ordinary disposable
  renewal/hard bounds, interactive policy, contained-test suppression and ongoing
  compiler-free platform work. Do not migrate existing records merely because a
  newer reader recognizes the new classification.

The existing owner must settle the precise admission/API and compatibility
representation during implementation review. This proposal requires the behavior
and its negative controls, not another parallel lifecycle supervisor or ledger.
Missing support refuses retained fixture startup; no monkeypatch or fabricated
installation/READY receipt supplies the capability.

## Concrete admission/API proposal

**Proposal reviewed in #9183; pending #8403 owner integration, not an implemented API.**
The proposed names below make the amendment reviewable; they do not appoint this
contributor to edit the shared library or its distributed bindings. The current
compiler-free Linux implementation remains the baseline, and its separately owned
Windows follow-through continues unchanged.

### Classification and reservation

Extend `Purpose` with `retained-experiment`, rather than representing retention as
production, an infinite `DaemonLeasePolicy` or an optional field old readers ignore.
Add an optional `retained_scope` to `DaemonInstanceSpec` and direct admission.
The scope is mandatory for this purpose, forbidden for the existing purposes, and
incompatible with a disposable lease or `finite-worker` lifecycle.

The proposed `RetainedExperimentScope` is a process-local input to the existing
reservation operation: the selected experiment/resource references plus its
existing custody check. It is not a new transferable grant, environment switch,
lease-renewal owner or persisted authorization receipt. The library must invoke
that check, not trust the object's type or caller-supplied observation dictionaries.
For this first consumer:

- The host controller qualifies the actual selected container, image, volumes,
  namespaces, resource limits and isolation through the lab's existing readers
  before invoking guest startup. Docker control remains on the host.
- The guest operation owner rechecks its actual state mount and operation custody.
  Reservation binds the original experiment/container references, guest mount
  identity and Linux boot/PID/mount/network namespace identities into the existing
  instance record. Child admission reobserves the guest-side identities before
  application work. Guest observations do not prove the host's Docker settings;
  neither half replaces the other.
- This is a trusted, explicitly selected controller-to-guest operation, not
  authentication of a hostile same-user process. A container marker, temporary
  HOME, local scope object or matching namespace number alone cannot select it.
  Missing host qualification or guest custody refuses the operation.

Keep `reserve_instance` / `spawn_instance`, process binding and inherited
`instance_environment(receipt)` as the launch path. Do not introduce a second
reservation, startup broker or shell `--retain` escape. Each retained resident
needs its own explicit reservation; an inherited disposable test context must
refuse a retained request before reservation rather than silently overriding it.
Conversely, no retained context propagates to all descendants. Managed session
children and finite peer operations retain their existing owners and lifetimes.

Direct admission without a reservation must supply the same qualified scope.
Nested admission may reuse the current instance only when purpose and retained
scope agree; the existing same-process early return must not let a production or
disposable guard silently satisfy a retained request.

### Record, expiry and settlement

Use the next incompatible instance-record schema, proposed as schema `3`, **only
for retained records**. Continue producing the current schemas for ordinary
production/disposable instances. Validate the new schema's purpose, resident
lifecycle and retained-resource fields together; absent or contradictory fields
are held/refused, never interpreted as an old default.

The retained record has no renewable lease, hard expiry or launcher-death
termination policy. The existing owner tracks the actual process/source identity
and declared lifecycle artifacts, but does not install its disposable expiry
monitor or invoke disposable tree supervision for this class. Closing its guard
records the existing finished outcome; it is not permission to delete accepted
work. Driver disappearance, missing monitoring and expired case permission remain
new-case refusals through the case owner, not lifecycle-expiry triggers.

Default `cleanup_instance` and installer reconciliation must report a retained
record as held, **including finished/dead instances**, without stopping processes
or deleting its record, evidence or artifacts. Production's current live-process
non-expiry branch alone is insufficient: ordinary cleanup can reclaim a dead
process's artifacts.

For explicitly approved retirement, propose a narrowly scoped addition to the
same cleanup operation: `retire_retained=True`, usable only with
`terminate=False` and the reattached exact experiment's qualified
`retained_scope`, rechecked by the cleanup owner. It does not grant stop authority.
The experiment owner first performs any separately approved exact-resource stop
and settles original task/session
work through those owners. Cleanup then requires the existing exact-process and
descendant-settlement proof; live, reused, unknown or unsettled identities remain
held. It may settle only declared lifecycle artifacts and the original instance
record, never recursively delete volumes, databases, source or experiment
evidence. The existing owner must settle this API and any required descendant
observation support before implementation; a dead primary PID is not tree proof.

### Capability and rollback

Expand the existing library/entrypoint readers first. The retained consumer uses
support declared by the exact library/runtime that will execute, separately from
package versions or a controller's newer payload. Reuse an existing suitable
runtime descriptor where available; no per-call prerequisite command, universal
handshake or installation-success marker is proposed.

Before creating a retained child, the selected launch and child-admission owners
must support this classification and schema. A supported historical reader that
does not recognize schema `3` must preserve the record as unsupported/held; it
must never translate it to production or disposable state. Qualify actual
Python, shell, PowerShell and packaged-runtime readers in the supported rollback
window. An unqualified reader/binding refuses retained use, without disabling its
ordinary operations. Do not infer native reader safety from the Python parser.

Do not migrate or relabel existing records, alter old-purpose defaults, or
enable consumers merely because provider source merged. Retained fixture wiring
follows capable provider integration; supported rollback must refuse new retained
launches before creation while preserving existing retained records. No failed
new-form launch is replayed through a legacy production/disposable invocation.

## Plan

- [x] Review the selected behavior proposal before implementation; #9176 merged.
- [x] Submit the concrete admission/API and compatibility proposal for automated
  source review; #9183 merged. This does not settle the existing owner's integration.
- [ ] Coordinate this bounded amendment with #8403's existing integration owner;
  preserve its separate source/native-binding work and resource custody.
- [ ] Review the existing-owner classification/admission and compatibility
  extension above before changing shared code or wiring retained service processes.
  Settle the proposed scope, record schema, selected-runtime support and explicit
  retirement seam with that owner; this text is not source-custody transfer.
- [ ] Implement it through the current instance record, admission, inspection and
  cleanup owners; update actual affected distributions and callers normally.
- [ ] Wire the minimal retained Bridge/Dispatch/peer fixture through those owners,
  preserving original task/journal and strict pre-effect boundaries.
- [ ] Obtain exact resource/lifecycle approval, then qualify actual retained
  container behavior. Source review and local synthetic controls are not that proof.

## Validation plan

- [ ] Pure controls reject implicit/malformed classification, host-side misuse,
  missing/foreign venue custody, stale/replaced resources and unsupported readers.
  No unknown record can become disposable or authorize a new effect.
- [ ] Cover disposable/retained context conflicts and nested-admission mismatch
  before reservation/application work. No scope object or environment value alone
  supplies actual venue qualification.
- [ ] Through actual supported prior/current parsers and packaged bindings, prove
  that old ordinary instances retain their semantics, new retained records are
  held by older collectors, and unsupported/rolled-back launch providers refuse
  before child creation. Unknown schemas never enter stop/delete paths.
- [ ] Existing unattended renewal, owner-loss expiry, hard-maximum and interactive
  tests still pass. Production non-expiry and exact-process/artifact refusal
  remain unchanged; the compiler-free platform contracts retain their own proof.
- [ ] Separately approved real-container controls accept one synthetic operation,
  disconnect/lose the client, cross the ordinary disposable lease interval, then
  observe the same service/accepted-work identity with no implicit stop or replay.
- [ ] Monitor loss, threshold exhaustion and expired case permission refuse a new
  case while the existing accepted operation and evidence remain intact.
- [ ] A competing driver, stale resource selection or interrupted launch cannot
  acquire or replace the original accepted work merely from an idle process/lock.
- [ ] A normal disposable collector holds the retained record. Explicit approved
  stop/retirement settles only the exact selected processes/resources and preserves
  evidence; injected failures and unknown cleanup retain the original custody.
  Include dead/finished retained records, changed resource identity, living
  descendants after primary exit and retirement without prior stop approval.
- [ ] Required CI stays focused. Real lease-interval, process-loss, persistence
  and fault cases use explicitly selected bounded integration lanes, with
  unavailable platforms recorded rather than replaced by fake success.

No item here closes the broader logging, guest/source/runtime, native START,
ordinary Dispatch recovery, release or running-adoption gates.
