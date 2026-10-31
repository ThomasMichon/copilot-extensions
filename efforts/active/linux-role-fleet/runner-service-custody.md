# Permanent runner service custody

Back to the [runner-loadout plan](runner-loadouts.md) and
[Linux role fleet](README.md). Tracked under #7192/#6030/#6236.

## Selected direction and boundary

The operator accepted the proposal to reuse the existing canonical Git-backed
coordination store for a distinct permanent service-custody record. This is
not a second database, a task-lease holder rename, a larger TTL or a requirement
to reach the Gateway before recognizing an adopted resource.

This proposal realizes the existing machine-fleet intent for
`repeatable-role-fleet-adoption` and `bounded-desired-state-control`.
The implementation details below are **agent-recommended** and require source
review before execution. Acceptance of this design does not authorize a live
transition, controller installation, credential transfer, capacity/routing
change or discharge of temporary protection.

The canonical resource/coordination owner supplies storage, compare-and-swap
and history. Fleet-manager supplies the approved service identity and exact
adoption operation. Each substrate provider enforces resource admission;
the existing Gateway hosts its in-process controller module. ARC alone owns
runner pods. No new daemon, registry service, installer or Kubernetes scheduler
is introduced.

## Canonical record and compatibility

- Add a separately identified, strictly versioned service-custody envelope to
  the existing resource coordination owner. Reuse the selected private store,
  canonical provider resource key and physical compare-and-swap slot. A
  separately checked ref would permit lease acquisition to race adoption.
- A service record has an explicit custody identity and state, fleet/controller
  installation, provider instance and immutable resource identity, named
  allocation, exact approved adoption digest and retained acceptance evidence.
  No agent session, worktree, lease TTL or heartbeat determines its lifetime.
  Store only bounded non-secret references/digests, never credentials.
- Preserve the original task history as the accepted transition's predecessor.
  Do not rewrite old records, delete refs, fabricate past receipts or silently
  reinterpret a version-1 lease as permanent ownership.
- Old task acquire, takeover, renew, transfer, release and stale-record cleanup
  must not modify active service custody, regardless of elapsed time or caller
  holder text. Retirement is a separately admitted service-owner transition;
  config deletion, controller absence and agent finalization cannot retire it.
- Declare support against the selected running resource/provider generations,
  not marketplace versions. Expand readers and guarded operations before
  enabling writers. Retain legacy task semantics for ordinary task resources.
  An unknown custody envelope is not an absent or expired resource.
- A strict old lease parser is insufficient protection: legacy provider paths
  may treat unavailable coordination as permission for local/forced borrowing.
  Admission must exclude such incompatible paths before live adoption. A
  provider rollback that cannot enforce custody must refuse managed actions,
  not silently restore legacy borrowing or destructive overrides.

## Provider enforcement and effect races

The provider's existing pool, borrow, lifecycle, provisioning, workspace and
connection-cleanup owners must honor custody internally; callers must not need
an optional safety flag. Nonmutating inventory can remain available.

An adopted service resource is unavailable to ordinary task borrowing.
Task-lease overrides do not authorize stop, delete, reprovision, workspace
mutation or connection teardown of that service. Supported service operations
require their actual current service-owner authorization and exact resource
scope, not a caller-supplied installation label or a matching digest alone.
Read-only access retains its existing authorization and does not grant writes.

Every effectful path must serialize its admission against the same canonical
resource slot as adoption. Inspect-then-execute alone has a race: a previously
admitted stop can run after custody changes. Reuse an existing suitable
effect-admission owner where available; otherwise extend the resource owner's
CAS protocol with a bounded, durable in-flight operation claim. Adoption must
refuse an unsettled competing effect. Native settlement or its explicit owning
recovery, not a timer or another task lease, clears uncertain responsibility.
Do not imply that the substrate accepts a distributed fencing token if its
actual API does not support one.

Unavailable, malformed or unauthenticated canonical evidence blocks guarded
operations. Gateway unavailability does not erase the durable record or permit
an unrelated agent to borrow/delete the resource. A retained cache may report
historical custody but cannot authorize a new effect or prove current absence.
Scope enforcement to the managed provider surface; do not promise protection
against independent platform administrators or unmediated native tools.

## Adopt in place and preserve responsibility

1. Prepare a bounded proposal while the original protective task lease remains
   live and renewable. Bind the exact current resource, controller installation,
   selected configuration/artifacts, support owners and current authorization.
   A proposal is not custody and cannot displace another owner.
2. Inspect actual host/cluster/release/scale-set identities, capacity, isolation,
   policy and in-flight work through their existing owners. Reconcile published
   source drift before approval. Do not apply a generic profile, retag a release
   or run Helm merely to obtain an ownership marker.
3. Require the selected provider and controller to accept this exact transition,
   including enforced compatibility, no competing in-flight effect and durable
   restart behavior. Use the original verified identity/current admission
   boundary; no serialized permit or synthetic successful callback suffices.
4. Atomically publish accepted service custody using the freshly inspected task
   fence, with its current validity rechecked by the canonical owner. Race with
   renewal, release, takeover or provider effects refuses stale input. Never
   release and reacquire, and never force an expired or foreign lease.
5. Verify canonical readback and actual provider acknowledgment, preserved
   incumbent state and session-independent reconciliation. A lost publication
   reply remains uncertain until that same operation is inspected; do not retry
   an effect blindly or silently revert to a task lease.
6. Discharge the temporary agent obligation only through the accepted ownership
   transition and verified protection. Stop its renewal only after canonical
   acceptance is established. Uncertain adoption retains responsibility and
   explicit recovery; neither abandoning a session nor lease expiry resolves it.

No-write incumbent inspection/adoption must be supported independently of the
current fixed ARC convergence profile, which refuses unowned releases and does
not reproduce every incumbent policy. Permanent host custody is necessary but
not proof of workload readiness, lifecycle support or actual Actions job use.

## Plan and validation

- [ ] Review this protocol and source appointment through the ordinary PR gate.
- [ ] Extend the existing canonical resource owner with strict custody records,
  mixed-history validation, exact CAS transitions and truthful CLI outcomes.
- [ ] Integrate the actual provider guards, including forced/legacy paths,
  outstanding native effects, outage refusal and upgrade/rollback admission.
- [ ] Bind the existing Gateway/Fleet admission and incumbent-preserving ARC
  inspection path; preserve current release, credential and support owners.
- [ ] Prove real separate-process task/adoption/effect races, lost replies,
  interrupted acceptance, restart and exact-history retirement refusal using
  an isolated Git remote and real existing-owner consumers.
- [ ] Qualify installed current/prior provider and caller combinations. A new
  reader rejecting an unknown field does not prove an old provider blocks it.
- [ ] Prepare and obtain approval for the actual live transition, then prove
  unchanged identity/capacity/isolation/in-flight work, unrelated-action refusal,
  controller outage/restart independence and canonical obligation discharge.

Required unit/contract checks remain fast and change-scoped. Real Git,
multi-process, installed, native and live lanes retain their separate portfolio
owners and explicit results; missing lanes remain open rather than being
substituted with mocks. Existing broader runner lifecycle and job acceptance
remain in the parent plan.
