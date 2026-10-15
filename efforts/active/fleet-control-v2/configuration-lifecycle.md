# Repeatable configuration lifecycle

Back to [Fleet Control V2](README.md). This is proposed implementation intent,
not installed behavior or live enrollment authority. It realizes the
machine-fleet `role-selective-system-lifecycle` and
`safe-template-and-live-refresh` vision requirements.

## Scope and existing owners

The operator requires a developer-repeatable product, not a one-time deployment:
selected harness template or user-owned configuration, cold bootstrap,
incremental updates, migrations, recovery and rollback.

The local CLI bootstraps or recovers the explicitly selected Gateway when its
API cannot yet serve the operation. After readiness, the Gateway is the central
service for ordinary clients. The local background role is the authenticated
credential responder on logged-in provider machines, not another fleet
coordinator. The Gateway can subscribe to user-owned configuration and
self-update, with local validation/approval before major reconfiguration.
Private configuration repositories are not assumed to have CI.

Implementation detail below is **agent-recommended**:

- #6240 retains release/role/lifecycle ownership; #6424 retains managed thin
  client installation; #6030 retains the deployment-manager adapter; credential
  owners retain custody, issuance and responder lifecycle.
- Reuse the existing planner, release preflight, immutable slots, operation/
  receipt contracts and external deployment manager. Do not introduce a parallel
  installer, scheduler, ledger or update selector.
- Harness adapters resolve templates and writable user configuration.
  Provider-specific inventory, leases, quota, identity and credentials remain
  outside the portable core.

## Desired-state admission

Resolve selected template provenance, explicit adopter overrides, config schema,
trust policy, supported roles, fresh inventory and authoritative capacity.
Preserve unknown/custom resources. A template difference alone cannot authorize
retirement or data deletion.

Produce an inspectable plan bound to exact input revisions, policy, artifacts,
target identities and current-state assumptions. Approval binds that plan;
changed content or stale inventory requires re-admission. Classify risk before
automatic reconciliation. An unclassified or major change remains pending
until local validation/explicit approval; a repository commit is not approval.
Do not borrow a public repository's CI verdict for private desired state.

Config subscription discovers proposed revisions, not executable hooks from the
configuration repository. Invalid, unavailable or unapproved revisions preserve
the last valid deployment and expose a bounded blocked reason.

## Bootstrap and authority transition

Before a Gateway exists, admit only typed bootstrap/recovery operations through
the selected substrate and deployment owners. Do not expose arbitrary shell,
URL, executable or unchecked configuration forwarding.

Persist attributable operation/target receipts through the existing contracts.
Rerun reconciles actual resources and uncertain effects instead of allocating
duplicate capacity, containers, volumes, registrations or writers.

After authenticated Gateway readiness is established, ordinary mutations use its
API. Recovery is explicit and generation-fenced, not an automatic alternate
write path when a selected Gateway is offline. Accepted execution and durable
index state survive local CLI exit.

## Migration, update and rollback

Track current/target config, data, protocol and application generations.
Preflight compatible roles and storage, then execute ordered restartable steps
with effect-side fencing and truthful pending/unknown outcomes.

Stage immutable candidates independently of the running program. The existing
installed lifecycle authority activates/replaces the Gateway; it does not
overwrite its own running interpreter or silently select a different release.
An approved desired-state subscription uses the same admission/receipt path as
an explicit CLI update.

Retain a tested rollback target and declare which data/schema transitions are
reversible. Failed pre-transfer activation preserves the incumbent. After
irreversible migration or acknowledged writes, refuse unsafe downgrade and
provide explicit forward recovery rather than restore a stale backup/authority.
Rolling back an image is not proof of database rollback.

## Credential responder

Evaluate the existing credential owner's service before introducing a new
resident. Explicit installation maintains the authenticated tunnel/subscription
on an enrolled logged-in provider machine and responds to permitted fresh-token
requests. It is not hosted by a one-shot CLI and does not grant issuance merely
from fleet membership.

Bind request/reply to provider, account, audience, scope, recipient, correlation,
generation and validity. Provider capability must substantiate external-token
permissions; a source-labelled request cannot downscope an opaque broad token.
Unsupported scope fails honestly. Secrets never enter desired configuration,
approval records, task/audit data, images, argv or telemetry.

Expiry, reconnect, revocation, tunnel loss, disconnect, duplicate replies and
provider unavailability use the existing authenticated broker's bounded
uncertainty contract. No anonymous, wrong-account or stale-token fallback.

## Validation gates

- [ ] Cold bootstrap from no installed fleet, using a selected harness template
  and separately an explicit user configuration; actual admitted roles become
  service-ready.
- [ ] Repeat unchanged install/update without duplicate capacity, storage,
  registrations, services or authority.
- [ ] Preserve explicit overrides and unmanaged resources across template updates.
- [ ] Prove compatible ordered in-place migration with existing clients and data.
- [ ] Interrupt before and after each external effect; reconcile actual receipts
  and uncertain outcomes without blind reissue.
- [ ] Reject invalid config, stale plans and mismatched approval; preserve the
  last valid deployment.
- [ ] Subscribe to a proposed config revision; major/unclassified changes remain
  pending until exact local approval, then reconcile through the common path.
- [ ] Exercise failed candidate activation, compatible rollback and refusal of
  irreversible data downgrade with forward-recovery evidence.
- [ ] Prove CLI exit does not terminate the Gateway or accepted remote workload.
- [ ] Prove real responder tunnel, token refresh/expiry, disconnect/reconnect,
  generation/revocation and wrong-recipient denial without secret logging.

Unit, simple process/transport E2E, fresh-environment and available live lanes
must record their actual evidence. Planner outputs, open ports and mock receipts
do not prove these lifecycle gates.
