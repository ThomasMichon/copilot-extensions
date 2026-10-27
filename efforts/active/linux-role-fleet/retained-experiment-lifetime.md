# Retained experiment lifetime

Back to [Linux role fleet](README.md) and its
[persistent reproducer](persistent-isolated-reproducer.md).
Existing shared lifecycle owner: #8403 and
[Leased Daemon Instances](../leased-daemon-instances/README.md).

**Status: source proposal only; review before shared lifecycle or service-fixture
implementation.** This does not transfer the existing owner's compiler-free
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

## Plan

- [ ] Coordinate this bounded amendment with #8403's existing integration owner;
  preserve its separate source/native-binding work and resource custody.
- [ ] Review the existing-owner classification/admission and compatibility
  extension before changing shared code or wiring retained service processes.
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
- [ ] Required CI stays focused. Real lease-interval, process-loss, persistence
  and fault cases use explicitly selected bounded integration lanes, with
  unavailable platforms recorded rather than replaced by fake success.

No item here closes the broader logging, guest/source/runtime, native START,
ordinary Dispatch recovery, release or running-adoption gates.
