# Capacity-grid loadouts and configurable Actions runners

Parent and completion gate: [Linux role fleet](README.md).
Tracked scope: #7192 under #6030 and #6236.
Standing intent: [composable loadouts](../../../visions/machine-fleet/loadouts/README.md)
and [confirmed Gateway outfitting](../../../visions/machine-fleet/gateway/README.md#confirmed-complete-outfitting).

## Operator-selected outcome

Fleet-manager is the designer and confirmation surface, not a client-side
deployment executor. Plugins and repositories contribute reusable templates
with configurable slots. The knowledge repository's registrar actualizes them
into the final container/configuration composition and allocations targeting
specific named machines.

A grid-based loadout compares CPU, memory, disk and other supported resource
dimensions against capacity. Selecting an Actions runner role must include its
supporting control, credential, networking and exposure requirements, not just
an application-container count.

The Gateway-side controller pulls published sources, prepares a deployment
against actual admitted state, notifies fleet-manager and waits for approval
of that exact plan. After confirmation it outfits and verifies the complete
declared capability within an observable duration, or reports a precise
blocked, failed, partial or uncertain outcome.

The operator explicitly requested the upstream vision amendment followed by
the pieces needed to make this repository's Actions runner capacity
configurable through that flow. The portable role must still accept a selected
repository/organization rather than hardcoding one adopter's hosts or account.

## Existing seams and retained owners

The following are observed source boundaries, not unimplemented prerequisites
to rebuild:

- The [role/provider compiler](../../../fleet-manager/docs/role-deployment-plans.md)
  already parses explicit non-secret role bindings and projects named Komodo
  Stacks. It performs no deployment, credential resolution or bootstrap.
  Its generic extension preservation is not a first-class runner consumer.
- The [repository preparation contract](repository-config-preparation.md)
  already requires controller-owned acquisition, prepared notifications and
  exact confirmation. It is reviewed intent, not a delivered effect executor.
- Gateway routing, runtime admission, selected-release and client lifecycle
  APIs retain their existing scopes. #6240/#6424/#6241 and #6681 own their
  integration; open #8000 retains its Gateway/SSH/Vault leaves.
- #8352 retains the trusted base-image slice. A shared trusted harness image
  is not automatically an admitted Actions job image.
- The existing machine provider owns target identity, host lifecycle, capacity
  and lease/fencing. Fleet composition cannot rename a task lease, acquire
  another host or assume service ownership from a running process.
- Official ARC owns the Kubernetes runner adapter. Komodo can execute its
  selected deployment contract or manage an explicitly qualified enclosing
  Compose service; it does not directly supervise ARC's individual pods.

## Execution plan

This sequencing is **agent-recommended** and begins only after the amended
vision and parent plan clear review.

### Adoption-first priority

The operator's latest direction prioritizes formally adopting the **existing**
ARC runners into fleet-manager and the Gateway controller, preventing unrelated
work from interfering, and replacing manual tweaks with formal Kubernetes-style
control. The following implementation sequence is **agent-recommended**:

- Reuse the existing published fleet declaration and actual host/cluster/release
  identities. Prepare an explicit adopt-in-place transition; do not provision
  another pool, recreate the cluster, reset runner capacity or replace a running
  Helm release just to obtain a new ownership marker.
- Establish durable provider/resource and service custody through their existing
  owners. Bind the runner role to the admitted fleet/controller installation,
  not this agent's session, worktree or expiring task lease. Unrelated borrowing,
  task cleanup and agent retirement must not take or destroy adopted resources.
- Bind the real controller to published desired state, current authorization,
  selected artifacts and complete support owners. Verify actual existing-state
  adoption before enabling reconciliation. ARC remains the sole runner/pod
  lifecycle owner; the Gateway/fleet controller manages its declared release
  contract rather than becoming a second Kubernetes scheduler.
- Prove current-state inspection, unchanged repeat, drift refusal or supported
  repair, controller/client restart independence and preservation of running
  jobs, identities and isolation. Stale or unavailable source/authority must not
  silently reset the pool or widen its configuration.
- Stop ad hoc tuning once controller ownership is established. Retire the
  protective agent lease/renewal only after real durable adoption is acknowledged
  by the existing resource owner and its protection against unrelated work is
  verified. Renaming a lease holder or allowing its timer to expire is not that
  transition.

Live adoption still requires inspection and approval of its exact prepared
transition. This priority does not implicitly authorize destructive rollout,
credential transfer, new capacity or workflow routing. A source merge,
configuration row, observer callback or synthetic receipt is not proof of
durable ownership or a functioning controller.

### 1. Template and allocation composition

Extend the existing installed fleet-manager package rather than adding a
parallel registrar or planner.

- Discover code-owned/repository-contributed templates through an explicit
  supported contribution contract, with source/version provenance.
- Actualize configured slots into named role instances and preserve existing
  adopter extensions, overrides and custom allocations.
- Account separately for reservations, limits, observations, headroom,
  persistent storage and unknown/stale evidence. Bound runner capacity against
  every relevant resource dimension, including shared support allocations.
- Produce the final desired composition for the knowledge repository's
  existing publication owner. A normal client operation must not mutate a
  remote container manager or silently publish into a guessed repository.

### 2. Source acquisition and prepared-plan delivery

- Reuse the adopted repository-pair and selected-release owners to acquire
  bounded published snapshots. Retain exact selected source identities.
- Compile the published composition against fresh admitted target/provider
  state and explicit credential/configuration references.
- Publish prepared/blocked state and a scoped notification through the
  existing Gateway observation/subscription boundary.
- Separate plan integrity, source selection, caller authorization and effect
  admission. Merely verifying a saved plan cannot authorize deployment.
- Expose unsupported lifecycle/provider integration as a blocker; do not make
  a pure projection look like a request accepted by Komodo or Kubernetes.

### 3. Confirmation and bounded owner execution

- Accept a thin-client confirmation of one exact prepared generation, not
  arbitrary Compose, command, environment or credential payloads.
- Revalidate material source, placement, capacity and authority assumptions.
  Changed assumptions require a new proposal and decision.
- Execute fixed named operations through the existing managed owner, retaining
  idempotency/correlation and uncertain outcomes without blind replay.
- Outfit the complete dependency closure, including declared auth relays,
  tunnels, API/UX routes and control-side services. Charge their resources to
  the appropriate named targets.
- Enforce and observe the approved outfitting duration. Readiness requires
  evidence from every required capability owner; timeout cannot imply either
  success or that an uncertain side effect stopped.
- Keep manager/controller bootstrap independent of their serving API. Preserve
  the previous valid serving state, retained data and accepted work through
  supported failure recovery.

### 4. First-class Actions/ARC role

- Carry selected forge scope, runner group/scale-set identity, architecture,
  pinned artifacts, ephemeral minimum/maximum, host/job resources, toolchain,
  trust/egress policy and credential references to a real supported adapter.
- Interpret maximum capacity as runner execution capacity, not duplicate
  controller replicas. Respect named-host placement and the existing
  Kubernetes/ARC controller's scheduling authority.
- Preserve controller/listener versus job credential separation, restricted
  workload isolation and explicit missing-tool failures.
- Implement inspect, first convergence, unchanged repeat, bounded update,
  drift reporting, healthy recovery, drain and receipt-owned explicit removal.
  Deleting a source allocation alone is not destructive consent.
- Keep workflow routing, hosted/self-hosted selection and any fallback as
  separately reviewed repository policy. A healthy runner is not proof of
  automatic additive routing.

## Validation

- Pure units prove multidimensional fit/refusal, exact boundaries, stale or
  incomplete inventory, missing slots, unsupported templates, dependency cycles,
  shared-allocation deduplication and preservation of custom registrar state.
- The installed client consumes the contributed runner template and produces
  a publication-ready desired composition through its real existing source
  owner. An unknown preserved extension does not satisfy this check.
- A real Git consumer proves publication followed by controller-owned pull;
  the confirmation request contains no replacement configuration blob.
- Actual Gateway integration proves prepared-plan notification, exact-generation
  confirmation and changed/revoked/missing-authority refusal with no effect.
- Real owner adapters prove unchanged repeat, partial failure, restart/reconnect,
  timeout/uncertain completion and no duplicate writer. Mock callbacks alone do
  not establish managed deployment or live readiness.
- A disposable clean-room lane proves required support roles, independent
  bootstrap, full capability readiness and bounded failed-update recovery.
- Native qualification covers the thin client and supported platform edges.
  Available external-venue proof uses the actual selected deployment adapter;
  any unavailable tier has an explicit reason and retained acceptance item.
- Live adoption requires a separately inspected and approved prepared plan.
  Verify existing lease/custody, in-flight work, admission, isolation, credential
  redaction, drain/removal preservation and one real authorized Actions job on
  the declared scale set. Report installed, running, authenticated, ready and
  used separately.
- Existing-pool adoption proves durable provider/service custody, refusal of
  unrelated agent borrowing/cleanup, unchanged ARC identity/capacity/isolation
  and session-independent controller reconciliation. Only then discharge the
  temporary agent-held resource obligation through its existing canonical owner;
  a task-lease renewal or changed holder is not permanent adoption.
- Every source slice follows ordinary required CI, actual review, exact-head
  merge verification and a documentation-impact assessment. No source PR or
  planner result authorizes a live pool migration, routing change, new capacity
  or credential transfer.

## Completion boundary

This sub-plan is complete only when the runner declaration traverses the
designer/publication/pull/preparation/notification/confirmation/managed-owner
path and its retained lifecycle and actual-job acceptance pass, or unresolved
items are explicitly accepted by named tracked owners. A schema, offline
proposal, rendered Helm values or synthetic successful callback is not that
outcome. The parent effort remains Active until its own full completion gate.
