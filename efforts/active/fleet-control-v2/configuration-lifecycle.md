# Repeatable configuration lifecycle

Back to [Fleet Control V2](README.md). This is proposed implementation intent,
not installed behavior or live enrollment authority. It realizes the
machine-fleet `role-selective-system-lifecycle` and
`safe-template-and-live-refresh` vision requirements.

## Scope and existing owners

The operator requires a developer-repeatable product, not a one-time deployment:
selected harness template or user-owned configuration, cold bootstrap,
incremental updates, migrations, recovery and rollback.

The local CLI explicitly bootstraps the fleet from the user's logged-in
interactive machine, or recovers the selected Gateway when its API cannot yet
serve the operation. Setup composes the user-side heartbeat/authentication
pump, Gateway host, selected deployment manager and mesh relay. After readiness,
the Gateway is the central service for ordinary clients, not the owner of
whole-machine provisioning/removal. The local background credential/connection
role remains independent of the invoking CLI and is not another fleet coordinator.

The [bootstrap and authority amendment](bootstrap-and-authority.md) owns the
operator's clarification: the harness/knowledge repository pair defines
persistent desired configuration; Gateway maintains and refreshes that pairing,
prepares deployment deltas, and initially waits for fleet-manager confirmation
for every computed deployment. A source PR is not that confirmation. Private
configuration repositories are not assumed to have CI.

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
changed content or stale inventory requires re-admission. Initial reconciliation
computes and reports the delta, then waits for explicit confirmation through a
subscribed fleet-manager instance for ordinary as well as major changes. Do not
infer standing auto-apply authority from a risk classification, schedule or
repository commit.
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

After authenticated Gateway readiness is established, ordinary service/workload
mutations use its API. Whole-machine create/remove remains an explicit
operator-side fleet-manager/provider operation; Gateway can propose a need but
only repurpose already admitted hosts. Recovery is explicit and generation-fenced, not an automatic alternate
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
- [ ] Refresh both paired repositories on the declared schedule and an authorized
  driver request, and accept driver-delivered source updates under the same
  repository provenance; notify subscribed fleet-manager clients of the actual delta.
  Ordinary and major deployments remain pending until exact-plan confirmation,
  then reconcile through the common path.
- [ ] Prove only operator-side fleet-manager creates/removes whole machines,
  while Gateway/deployment-manager can repurpose already admitted capacity
  without obtaining provider administration.
- [ ] Prove slow-sync refresh/config dispatch works before Gateway connectivity
  and cannot activate unapproved changes, replace canonical repository intent,
  expose plaintext vault contents or regress newer accepted configuration.
- [ ] Exercise failed candidate activation, compatible rollback and refusal of
  irreversible data downgrade with forward-recovery evidence.
- [ ] Prove CLI exit does not terminate the Gateway or accepted remote workload.
- [ ] Prove real responder tunnel, token refresh/expiry, disconnect/reconnect,
  generation/revocation and wrong-recipient denial without secret logging.

Unit, simple process/transport E2E, fresh-environment and available live lanes
must record their actual evidence. Planner outputs, open ports and mock receipts
do not prove these lifecycle gates.
