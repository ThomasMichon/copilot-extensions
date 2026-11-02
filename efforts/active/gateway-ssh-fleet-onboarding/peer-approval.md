# Explicit peer enrollment approval

Parent: [Gateway and SSH Fleet Onboarding](README.md).
Tracker: [#8888](https://github.com/ThomasMichon/copilot-extensions/issues/8888).
Intent: [explicit peer approval](../../../visions/plugins/agent-ssh/README.md#explicit-peer-enrollment-approval).

The selected policy is `explicit-peer-approval`. The following contracts and
test decomposition are **agent-recommended implementation** of that behavior.
This source slice does not authorize installation, credential access, recovery
against a live deployment or retirement of a serving predecessor.

## Source boundary

Extend `services/ssh`'s configuration, transaction-owning store and Gateway
handler. An additive client contract composes the existing
`fleet_manager.ssh_directory` and `ServiceCaller`; it does not add another
transport, issuer, database owner, lifecycle controller or background worker.
Existing Fleet setup, installation, authentication/provider and shared CLI
reservations remain with their owners.

## Admission and recovery contract

- Absence of the optional policy preserves current configured-binding automatic
  admission. Selecting peer approval is a mandatory semantic, never a hint an
  older provider may ignore.
- The authenticated requester submits only its declared machine, environment,
  generation and public keys under its original request correlation.
- The service retains a bounded durable pending record. A fingerprint binds
  the exact public identity, target binding and request context. Pending state
  neither changes the active public directory nor grants native relay access.
- An already enrolled, currently admitted peer inspects that public record and
  approves its exact fingerprint through its own authenticated caller context.
  Caller-supplied approver labels, self-approval, changed content, stale target
  generations and unauthorized decisions refuse.
- Approval and active-directory publication commit through the existing store
  transaction. Notifications and native relay reconciliation observe only
  committed activation. Lost replies recover the original decision rather than
  creating a fresh enrollment or rotating keys.
- First-peer bootstrap or recovery is a distinct explicit operation. Its
  configured authority, confirmation and reason are required; no path invokes
  it because a peer, credential or response is unavailable.
- Private keys never enter this service or its public directory. Existing
  credential owners retain key creation, backup and local materialization.

## Compatibility and preserving state

The provider expands first. Its running service exposes the selected admission
contract through its own bounded API; consumers select the required policy
against that actual peer, not a package version or newer installed payload.
The effect boundary enforces the same selected policy. An older provider,
unknown contract or incompatible rollback fails before a required-policy
request can admit anything; there is no try-new-then-replay-old fallback.

Existing automatic-admission callers retain their meaning when that mode is
selected. A caller that cannot participate in explicit approval reports the
actual unsupported policy rather than treating pending state as readiness.
Retained public identities, generation floors, original requests and pending
decisions survive restart. Configuration changes cannot silently reinterpret
or approve a stale pending identity.

## Validation and exit

1. Schema/store controls cover absent-policy compatibility, fresh pending
   submission, exact approval, self/foreign/stale refusal, explicit first-peer
   recovery, duplicate/lost replies and transaction rollback.
2. Actual Gateway/ServiceCaller controls exercise requester, enrolled peer and
   configured recovery authorities with synthetic identities, including
   notification and native-relay projection boundaries.
3. Fresh/restarted private state and mixed-version fixtures prove no implicit
   activation, key rotation or unsupported-policy downgrade.
4. Use the existing contained standalone suite and an explicitly selected
   disposable service lane where available; no production account fixture,
   credential access or extra exhaustive required-CI matrix is introduced.
5. Source completion requires passing focused tests, applicable contract guards,
   current-head review and required CI through merge. Record exact evidence and
   retained release/adoption scope in the parent before concluding this slice.
