# Interactive bootstrap and repository-pair authority

Back to [Fleet Control V2](README.md). This is an **operator-directed intent
amendment** and an **agent-recommended implementation/validation decomposition**,
not delivered bootstrap behavior, a source-ownership transfer or live deployment
authorization.

## Operator direction

The operator stated verbatim:

> My general expectation is that initial setup of the fleet requires using
> `fleet-manager` from a user's logged-in interactive machine.

And:

> fleet-manager is also the only thing that can create or remove whole machines;
> Gateway can only repurpose them.

The remainder is this **public-safe summary of the operator's request**, not a
verbatim transcript; adopter-specific harness, cloud and identity adapters are
abstracted rather than made dependencies of the portable product:

1. Fleet-manager establishes a user-side controller heartbeat/authentication
   pump, composing the provider, identity application, vault, local auth relay
   and required persistent SSH/tunnel reach. That installed service periodically
   follows the paired private repository and optionally slow-sync control data
   to refresh topology, public-key propagation and Gateway subscriptions.
2. Fleet-manager establishes the Gateway machine, selected container/Kubernetes
   controller and mesh relay. Machine existence, authentication and supported
   Gateway subscriptions are separate readiness conditions.
3. Fleet-manager updates the private repository's actual loadout through its
   normal publication flow and publishes the adopted slow-sync control data.
   That channel holds vault material and per-interactive-machine refresh/control
   information so another fleet-manager installation can repropagate or request
   refresh even before it has a Gateway connection. It is deliberately a
   slow-sync fallback/configuration-dispatch path, not the live control stream.
4. Gateway retains the adopted harness/knowledge repository pairing and refreshes
   it periodically or accepts source updates/refresh requests pushed by an
   authorized fleet-manager driver. Future webhooks use the same path.
   Repository changes cause Gateway and the selected
   deployment manager to compute the delta and deployment steps. Initially they
   wait for confirmation from a fleet-manager instance; subscribed users or
   agents discover and confirm the pending deployment.
5. The repository pair is authoritative for persistent desired configuration:
   the harness supplies reusable templates/behavior and the paired private
   repository specifies what should actually exist. Fleet-manager also supports
   local edits, live inspection, confirmation, diagnostics and temporary/preview
   adjustments. Persistent configuration must be PR-reviewed/merged upstream
   and propagate normally, rather than becoming a Gateway-local fork.

The provider-specific realization belongs to adopter-owned integration. These
requirements do not select a wire protocol, vault format, polling interval,
configuration schema, deployment package or concrete machine.

## Authority and source boundaries

The [continuity-backed first-touch contract](../gateway-ssh-fleet-onboarding/continuity-bootstrap.md)
extends this existing bootstrap boundary to new machines without prior Gateway
credentials or SSH. Standard upstream setup consumes explicit harness context
and an already-authenticated user source, then delegates scoped enrollment to
the existing authority. Discovery, bootstrap delegation, current enrollment
and SSH/peer admission remain distinct; the slow-sync store is not another
issuer or live authorization database.

| Boundary | Authoritative responsibility |
| --- | --- |
| Harness plus paired private repository | Reusable behavior/templates plus the actual persistent desired loadout and policy references, under the repositories' publication gates. |
| Operator-side fleet-manager management | Explicit interactive bootstrap/recovery and whole-machine creation/removal through existing provider owners. |
| User-side installed heartbeat/authentication pump | Declared provider-session continuity, credential/relay reach and admitted topology/subscription refresh, independent of a one-shot CLI's lifetime. |
| Gateway and selected deployment manager | Observed state, inspectable preparation and confirmed application to already admitted capacity; no whole-machine provider administration. |
| Fleet-manager user/agent client | Notifications, exact prepared-plan confirmation, local editing and admitted temporary/debug control; no implicit persistent configuration writer. |
| Adopted slow-sync store | Encrypted vault and scoped per-machine control/refresh exchange; not source-PR approval, current authorization or a second live task/claim ledger. |
| Existing identity/credential/policy owners | Current identity proof, enrolled principal/scope and revocation/freshness evidence, without copying credentials into source configuration. |

**Agent-recommended refinement:** interpret vault synchronization as encrypted
vault material under the existing vault owner's custody/unlock contract, never
plaintext secrets or an unlock key distributed beside the vault. Make
stale/conflicting sync records explicit; a delayed copy cannot roll back newer
accepted configuration or trigger an unapproved/destructive action.

Repository authority answers what should exist. It does not, by itself, prove
that a caller is currently authenticated or that its grant has not been revoked.
The retained authentication/admission interface still needs an actual current
policy-source adapter and fencing at retrieval/effect/emission.
Deployment confirmation does not postpone already-effective credential
expiry/revocation or admitted stop controls.

## Required flow

**Agent-recommended implementation decomposition:**

- Compose existing lifecycle/provider, credential, mesh and deployment owners
  through the same standalone fleet-manager product. Preserve thin-client-only
  installation; setup explicitly selects the installed pump/server roles.
- Resolve and retain the actual repository pairing and selected revisions.
  Discover updates from scheduled refresh or an authorized driver's source
  delivery/refresh request, retaining the same reviewed repository provenance;
  missing/unavailable sources preserve the last valid state and report why
  current reconciliation cannot complete.
- Prepare an inspectable desired/observed delta and ordered deployment proposal.
  Notify subscribed fleet-manager clients and wait for confirmation in the
  initial mode, for ordinary changes as well as major ones. Bind confirmation
  to actual plan inputs; source PR approval and deployment confirmation remain
  separate decisions.
- Send confirmed service/workload reconciliation through the owning deployment
  manager within existing admitted hosts. Route whole-machine create/remove
  exclusively through operator-side fleet-manager and the provider owner, not
  through an expanded Gateway grant.
- Publish persistent loadout edits through source PRs. Expose temporary/preview
  adjustments distinctly and do not silently commit them, overwrite the
  canonical desired loadout or report an unmerged source edit as propagated.
- Refresh slow-sync control information independently of Gateway connectivity.
  Reconcile duplicates, ordering/conflicts and current authorization before
  requesting work; cloud arrival is not executable code or deployment consent.

Reuse #6240/#6424 lifecycle, #5789/#6443 auth/routing, #6030/#5861 deployment
and machine-provider owners. The existing controller/credential owners keep
their implementations. This amendment assigns intent and proof obligations,
not another participant's files; the no-other-agent-coordination constraint
remains in force.

## Validation plan

All entries are **agent-recommended implementation obligations**, not tests
claimed to have run for this documentation-only amendment:

- [ ] Before runtime changes, confirm the reviewed vision/plan revisions and
  exclusive source/interface allocation with the retained implementation owners.
- [ ] From an authenticated interactive machine, compose actual initial setup:
  pump/heartbeat, credential/relay availability, admitted tunnel/mesh reach,
  Gateway host, deployment controller and authenticated subscriptions.
  Repeat without duplicate hosts, services, tunnels, keys or registrations.
- [ ] Prove the installed pump survives CLI exit and refreshes changed paired
  configuration, topology, public-key propagation and Gateway subscriptions.
  Distinguish provider renewal, human challenge/revocation and transport loss;
  do not add human-session age/presence limits or infer sign-out from idle/lock.
- [ ] Gateway retains the correct repository pair and observes scheduled refresh,
  an authorized driver-triggered refresh and driver-delivered source updates.
  Report both revisions, partial/unavailable sources and the actual desired/observed delta.
- [ ] Ordinary and major deployment changes remain pending until a subscribed
  fleet-manager user/agent confirms the actual prepared plan. Reject mismatched
  or stale confirmation; preserve the last valid serving generation on failure.
- [ ] Local config edits become persistent only through reviewed/merged source
  changes and normal propagation. Temporary debug/preview controls remain
  separately visible and do not rewrite persistent desired state.
- [ ] An independent second operator machine observes slow-sync refresh/control
  changes without an established Gateway connection. Exercise delayed,
  duplicate, conflicting and stale records without rollback or blind effects.
- [ ] Encrypted vault synchronization respects the existing vault custody and
  unlock boundary; no plaintext secrets enter repositories, control records,
  logs, command lines or notifications.
- [ ] Gateway/deployment-manager can repurpose admitted hosts but cannot create
  or remove whole machines. Explicit fleet-manager/provider operations retain
  capacity, destructive-action, live-work, claims and receipt safeguards.
- [ ] Record unit, real-process/transport E2E, fresh-environment and available
  live/platform evidence separately before adoption. No health response,
  constructed policy snapshot or planning output substitutes for serving proof.

## Completion and unchanged gates

This amendment clarifies the requested source of desired configuration and the
bootstrap/whole-machine authority split. It does not complete the installed
lifecycle, current-policy adapter, retained-owner source agreement, subscription,
platform or live adoption gates in the parent. No provider, vault, sync-store,
machine, subscription, credential, deployment or lease is changed by publishing
this plan.
