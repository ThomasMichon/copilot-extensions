# Dynamic Reverse Reservations

- **Slug:** `dynamic-reverse-reservations`
- **Repo:** copilot-extensions
- **Branch model:** reviewed proposal, then bounded implementation and qualification.
- **Created:** 2026-10-22
- **Status:** Active
- **Vision:** [directional connectivity and credential continuity](../../../visions/machine-fleet/README.md#directional-connectivity-and-credential-continuity),
  [honest reachability and derived profiles](../../../visions/plugins/agent-ssh/README.md#honest-reachability),
  and [collision-free service endpoints](../../../visions/plugin-services/README.md).
- **Umbrella issue:** #7719

## Guiding Intent

Recover an authorized reverse-listener connection from address contention
without killing an unknown holder, broadening forwarding permissions or
publishing an endpoint that has not actually become ready. Preserve the
existing owners of identity, transport, discovery, supervision and lifecycle.

This campaign closes a realization gap and proposes a narrow revision of
existing endpoint intent for policy-constrained boundary transports. It does
not introduce a second machine registry, lease service, supervisor or Gateway.
The proposal includes coordinated vision and transport-pattern amendments;
that mechanism is not admitted for implementation until both clear review.
The first contribution is a finite, supplied-owner coordination primitive and
isolated regression proof, not production SSH recovery or live adoption.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Source contributor | Proposal, new bounded coordination source, focused regression and publication | #7719; `soldered-sprocket`, source root `jittery-wrench` |
| Existing finite-forward contributor | Retain finite candidate selection, actual SSH bind/process ownership and candidate-specific readiness | #7720; source `greased-kiln` |
| Existing transport/profile owners | Retain actual SSH execution, profile derivation and supported platform adapters | #63, #7206 and #5973 |
| Existing permission/enrollment owners | Retain authenticated participant policy, both forwarding directions and grant projection | #5789 and #6804 |
| Existing lifecycle/adoption owners | Retain supervision, restart custody, distribution and live activation | #6240, #6424, #6030 and #6681 |

## Coordination

- **Topology:** one proposal/source writer; normal serial reviewed PRs.
- **Host (owns PRs):** the source contributor owns this new bounded slice,
  not another contributor's existing PR, campaign or runtime.
- **Delegates:** none. This contribution does not launch another agent.
- **Source boundary:** only the new paths listed in
  [the design](design.md#source-and-integration-boundary), plus their ordinary
  documentation/changefile metadata after proposal review.
- **Handoff:** publish exact source, coverage and qualification boundaries on
  #7719. An unavailable former owner is not abandonment or a custody transfer.
  Reconcile any actual overlapping accepted reservation before implementation.
- **Retained ownership:** the archived Gateway campaign under #7257 and the
  existing policy/profile/lifecycle sources are not taken over. Shared
  integration changes require their own explicit source acceptance.
- **Finite-forward dependency:** #7720 owns the additive `ssh-manager`
  candidate/readiness leaf. This campaign coordinates admission, publication
  and uncertainty above that owner; it must not implement another candidate
  selector, retry loop or SSH process owner.

## Context

An SSH connection can carry several forwards. A required reverse bind failure
can terminate that connection and remove otherwise useful paths. A port that
accepts TCP is not necessarily the intended listener; PID age, a lease timeout
or an unavailable alias does not establish cleanup authority.

The existing `ssh-manager` forwarding helpers own actual SSH subprocesses,
platform differences and proxy cleanup. They are not an authenticated remote
reservation API. The existing `fleet-manager.gateway_ssh_policy` and
`gateway_ssh_runtime` leaves provide policy decisions and named-profile
reconciliation; neither supplies reverse-listener allocation. The service-role
Gateway's HTTP/response-stream profile explicitly refuses generic TCP.
The newly accepted #7720 contribution owns the missing finite-forward leaf;
its accepted source contract is an integration dependency, not an API inferred
from an issue or a reason to patch its source here.

The [design](design.md) reconciles those boundaries, the actual OpenSSH
permission constraint and the available qualification tiers. Existing local
endpoint publication and Gateway attachment generations are prior art, not
remote authentication or permission authority.

## Request

The operator's requested outcome:

> We'll need to isolate the issue and come up with a way to prevent these
> collisions use dynamic reservation flow with auto-repair

The operator subsequently authorized this existing contributor's narrow
upstream design/source slice, with claims and review first. The selected
response was:

> authorize-existing-session

That selection explicitly excludes new agents, edits to held
authentication/lifecycle code, changes to forwarding permissions, live repair
and rollout. These limits remain part of the request, not optional
implementation preferences.

The concrete API, finite-tuple strategy, state transitions and test matrix
below are **agent-recommended realization**, not additional operator requests
or an already supplied runtime contract.

## Plan

### Phase 1 - Reconciled and reviewed proposal

- [x] Establish the bounded public reservation and inspect existing
  Gateway/SSH policy, profile, forwarding and discovery seams.
- [x] Separate actual OpenSSH permission constraints from local rendezvous
  prior art and explicitly identify missing integration authority.
- [x] Review and land the corresponding
  [endpoint vision reconciliation](../../../visions/plugin-services/README.md#minimal-network-exposure),
  preserving ordinary OS-assigned ephemeral defaults and scoped, exclusive,
  temporary ownership for constrained boundary transports.
- [x] Review and land the explicit
  [constrained reverse-SSH pattern amendment](../../../docs/patterns/service-transport.md#constrained-reverse-ssh-listener-grants),
  or select a conforming approach, before any finite-positive-port engine
  implementation. No forwarding-permission expansion resolves this gate.
- [x] Review and land this canonical proposal before engine implementation.
  Keep #7719 open across the source and qualification phases.

### Phase 2 - Finite supplied-owner source

All steps in this phase are agent-recommended.
The proposal and its governing vision and pattern amendments cleared review
and merged in #7726. Integration still requires the actual accepted #7720
provider contract; a coordinator-facing protocol does not assert that the
transport provider already implements it.

- [x] Implement the strictly bounded new coordination module described in
  [design.md](design.md), with explicit typed conflict, refusal, unavailable
  and unknown outcomes.
- [ ] Compose the accepted #7720 finite-forward contract without duplicating
  candidate selection or process/reconnect ownership. Keep an unavailable
  production adapter explicit.
- [x] Retain the actual successful candidate resource; publish only after
  owner-verified target readiness and generation-checked admission.
- [x] Keep ambiguous bind/publication outcomes under their original
  correlation and prevent a delayed operation from retiring a successor.
- [x] Preserve finite current permission inputs, unrelated established
  connections and explicit exhaustion; never fall back to probing, wildcard
  grants, another registry or unowned cleanup.

### Phase 3 - Isolated qualification and source publication

All steps in this phase are agent-recommended.

- [x] First reproduce the occupied-listener failure with an isolated
  fixed-tuple control, then qualify candidate recovery and exhaustion using
  real retained loopback resources.
- [x] Cover simultaneous allocation, readiness refusal, failure around
  publication, lost acknowledgement and stale generation/cleanup events.
- [x] Qualify the installed, source-independent module/consumer boundary on
  supported native platforms; keep actual SSH and lifecycle proof distinct.
- [ ] Run the current required admitted unit packet, targeted tests and
  publication guards; obtain current-head review and CI, then merge through
  the managed source flow.
- [ ] Reconcile every validation item and record exact remaining integration
  obligations before closing this source campaign.

### Phase 4 - Explicit integration disposition

- [ ] Obtain a concrete accepted objective from the existing transport,
  permission/enrollment and lifecycle owners for any missing production
  adapter, authoritative discovery and supervisor integration.
- [ ] Either qualify an explicitly accepted integration within this source
  boundary or transfer it to that named tracked objective. A synthetic
  adapter is not an implemented production owner.
- [ ] Keep distribution, permission changes and authorized running adoption
  separate; no source-only result retires a working predecessor.

## Validation Plan

All implementation-specific selections are agent-recommended.

- [x] **Pure contracts:** bounded finite positive loopback tuples, identity/
  generation/correlation binding, stale/current comparisons and explicit
  unsupported or exhausted results. Data parsing supplies no authority.
- [x] **Real resource regression:** occupied old tuple remains owned and
  usable while a separately permitted candidate is actually bound and
  retained. No probe-close interval, unknown-holder termination or
  opportunistic socket reuse.
- [x] **Concurrency and uncertainty:** real bind arbitration, failed
  readiness, stale publication, lost acknowledgement, interrupted caller and
  delayed old renew/release cannot claim or withdraw another generation.
- [x] **Installed consumer:** noneditable artifact, no source checkout,
  actual callable consumer, separate Windows/Linux outcomes and cleanup
  observed before test containment can mask a leak.
- [ ] **Actual OpenSSH:** use only an existing isolated fixture and its
  already available credential/host-key material. If unavailable, retain an
  exact named integration gate; socket tests or mocked arguments do not
  satisfy this item.
- [ ] **Existing policy and lifecycle:** both `permitlisten` and peer
  `permitopen`, authenticated target identity, current direction policy,
  bounded existing-supervisor reconnect and independent connection
  preservation require the real owning adapter, not a fake callback.
- [x] **No live effects:** no deployment, key/enrollment provisioning,
  permission widening, shared service restart or unowned resource cleanup.
- [ ] **Publication:** source tests plus required admitted units and ordinary
  metadata/contract/lint checks; exact current review, green CI and actual
  merge. Release/rollout remains separately owned.

## Proposal

[Finite reservation coordination and integration contract](design.md).
No production API or runtime readiness is claimed by this proposal.

## Journal

### 2026-10-22 - Bounded source appointment

- Recorded the direct source appointment and its exclusions; created #7719
  after public issue/PR deduplication.
- Read actual Gateway SSH policy/profile owner protocols, service-profile
  limits, transport-provider contracts and shared forwarding behavior.
- Located no existing real `sshd` harness or reusable SSH credential fixture
  in the tracked forwarding/native test inventory. This limits the proposed
  qualification lane; it is not permission to provision credentials.
- Reconciled the separately accepted #7720 finite-forward source reservation:
  retain that contributor's candidate selection, readiness and actual SSH
  process ownership; narrow this proposal to its distinct supplied-owner
  admission/publication/uncertainty layer.
- Reconciled existing vision intent and prepared the proposal. No engine,
  retained policy/profile/lifecycle source or live resource was changed.
- Review identified that finite positive tuples depart from the previous
  unconditional OS-assigned forwarded-port pattern. Added the explicit
  pre-implementation gate and a narrow reviewed-pattern amendment rather than
  treating the permission constraint as an implicit exception.
- Further review identified the same unconditional requirement in the
  governing service vision. Reconciled its endpoint intent in place and gated
  implementation on the reviewed vision as well as the pattern, retaining
  ordinary defaults and forbidding widened permissions or unowned reclamation.

### 2026-10-22 - Reviewed intent merged; source slice begins

- Proposal #7726 actually merged as
  `5935a5d0bd81bb371235e7edf75be677cb5e0ae1`, with current-head Copilot
  approval and the real required metadata job passing. All three Phase 1
  review/merge gates are satisfied; this is not source-campaign completion.
- The independent #7746 npm preparation fix reached trusted Main through
  #7759. Ordinary PR metadata editing exercised that exact Main in
  run `39722198978`, job `119637983449`: all six dependencies retained their
  seven-day admission, npm installation took 506 ms, and source validation
  completed within the unchanged five-minute job. The managed provider merge
  then succeeded. #7741 records the complete recovery proof; neither the old
  cancelled executions nor synthetic same-named checks were misrepresented.
- Pulled the retained source workspace forward onto the actual reviewed plan.
  The managed sync refused the already-deleted merged PR ref; a clean-tree,
  backed rebase dropped only the exact already-merged proposal commits after
  comparing the merged effort, vision and transport-pattern bytes.
- Notified the #7720 owner that its governing vision/pattern dependency is
  satisfied. Its finite selection, actual SSH resource/readiness and reconnect
  source remains exclusive. The next slice is only the new supplied-owner
  admission, generation publication and uncertainty contract; no production
  transport adapter is inferred or claimed.

### 2026-10-22 - Bounded coordinator and isolated native qualification

- Implemented the new stdlib-only coordinator above one explicitly supplied
  owner. The immutable finite request, exact admission/candidate receipts,
  original-correlation journal requirements, one absolute operation budget,
  generation-checked publication and confirmed unpublished-only disposal
  remain distinct from authentication, actual SSH selection and supervision.
- The checked source items above refer to this callable contract and its
  isolated supplied-owner model, not a deployed adapter. The existing owner
  must independently authenticate identity and both forwarding directions and
  implement the documented journal/CAS/deadline boundaries. The actual #7720
  adapter remains pending its accepted callable contract.
- Windows CPython 3.12 executed all 73 focused cases: 65 pure contracts,
  seven real loopback resource cases and the source-independent installed
  wheel consumer. Owned sockets and concurrent callers were closed/joined
  before outer containment ended. Focused fatal Ruff checks passed.
- Linux CPython 3.14 executed the same 72 pure/socket cases successfully.
  Its initial isolated interpreter lacked pip; the missing test tooling was
  restored through the unchanged canonical configured-feed seasoning helper.
  An interpreter on a Windows-mounted filesystem then exceeded the existing
  30-second pip subprocess deadline. Repeating only that failed installed
  consumer with an ephemeral native-Linux-filesystem interpreter passed in
  5.17 seconds, retaining the same test and deadlines. The native test
  environment and package target were removed through their owning test
  lifecycle; this did not install a runtime or change host configuration.
- Added the owning API/evidence-boundary documentation and standalone patch
  changefile. No existing policy, enrollment, profile, shared SSH forwarding,
  lifecycle, installer, service configuration or forwarding grant changed.
- Actual OpenSSH, production discovery/journal integration and supervisor
  qualification remain open. These socket/installed-artifact results do not
  satisfy those tiers, authorize distribution or retire a predecessor.
- The existing transport owner then published its concrete #7779 candidate:
  an asynchronous finite-forward class with 1-16 exact tuples, original
  request/distinct attempt identity, retained process and an authenticated
  observer seam. Reconciled this coordinator to asynchronous owner hooks and
  the same 16-tuple ceiling before publication, without a blocking event-loop
  shim, grant truncation or guessed production adapter. Added explicit
  cancellation-custody coverage and documented the remaining-budget and
  reconnect-gate handoff requirements. That candidate's own review and
  source/production-owner integration remain separately gated.
- The aligned asynchronous source passed all 75 focused cases on both
  Windows CPython 3.12 and native-filesystem Linux CPython 3.14: 67 pure
  immediate-owner contracts and eight real-loop/socket/installed-artifact
  cases. Pure fixtures drive only nonsuspending coroutines, while native and
  installed consumers use the actual event loop. Fatal Ruff checks passed.
- The unchanged required Linux source-unit packet passed all 434 admitted
  nodes with coverage at the earlier committed coordinator source; final
  hosted current-head CI remains a separate publication gate. An additional
  manual Windows packet was refused by the effect observer on `ctypes.dlopen`,
  before producing proof. That negative platform result is recorded with the
  existing controller owner in
  [#6237](https://github.com/ThomasMichon/copilot-extensions/issues/6237#issuecomment-6333519387).
  No observer/manifest exception, skip or altered budget was introduced, and
  the Windows packet is not claimed as passing.

### 2026-10-22 - Review correction for supported Python 3.10 timeouts

- Review found that Python 3.10's `asyncio.TimeoutError` is distinct from
  built-in `TimeoutError` and could escape the documented unknown-result
  boundary. A real `asyncio.wait_for` witness on existing CPython 3.10.20
  failed all three original admission/reservation/publication cases against
  the prior source. No interpreter or SSH fixture was provisioned.
- Catch both timeout classes across the shared handler, retaining original
  operation custody and leaving caller cancellation/programming errors to
  propagate. Extend the actual event-loop witness to confirmed discard as
  well; no phase automatically resubmits an uncertain operation.
- The broad current-source Windows 3.12 and Linux 3.10 runs each passed 77
  cases, including their actual installed-wheel consumer; two new witness
  assertions initially used method names instead of the existing bounded
  phase reason names. Corrected those expectations and reran the complete
  four-case timeout family on both platforms: all passed. The 79 distinct
  focused cases are covered across those overlapping runs; no failed case
  was skipped or a deadline widened. Current-head provider review and CI
  remain required before merge.
