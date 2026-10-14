# Temporary Docker resource obligations

**Technical boundary:** Generic ownership ledger and allocation-fence source
implementation validated; Docker producer integration and live validation pending.
**Coordination:** #6109.
**Canonical effort:** [Vendored Installer Engine](README.md).

This is a coupled validation-infrastructure slice, not another installer
adopter or a replacement completion gate for the parent effort.

## Request and scope

> We clearly are missing "claims" for containers. For temporary containers, we need to make worktrees responsible for them before they may finalize.

Required outcome: durable creator obligations and an actual finalize gate for
temporary Docker resources, established automatically before allocation.
Manual naming, reminders and post-allocation `claims add` are insufficient.

Operator clarification, verbatim:

> Bacally, "fleet" containers are borrowed or leased, but disposable containers must be claimed and owned

**Two distinct lifecycles:** fleet containers are pre-existing reusable resources
borrowed through leases and returned to the pool when work is safe. Disposable
containers are created resources owned by their creating worktree; a finished
test, stopped container or released borrowing lease does not discharge that
ownership. The worktree must remove its disposable resource with verified
close-out, or transfer ownership through an explicitly accepted handoff, before
it may finalize. Do not retrofit disposable deletion requirements onto fleet
borrow/return semantics.

**2026-10-13 operator-selected storage/cleanup choice, verbatim:**

> retain-volumes

**Choice summary:** keep separately named temporary volumes owned and held.
Do not replace them with container-scoped tmpfs as a workaround, and do not
automatically delete a volume by name after a birth-identity inspection.
Docker's current CLI/client removal interface exposes a name and force option,
not a birth-conditional deletion primitive. A separate identity-safe volume
cleanup mechanism must be reviewed before automated volume deletion is enabled.
Container-ID lifecycle work and read-only volume verification can proceed;
volume retention does not satisfy the full close-out or parent completion gate.
This choice authorizes source design, not live allocation, cleanup or rollout.

**2026-10-14 ownership-system continuation, verbatim:**

> Got it. Continue driving. Come up with a system which ensures we can track what owns what.

**Scope summary:** continue the disposable Docker accountability system below,
using the existing ownership ledger rather than a competing container registry.
The operator delegated source design and continued execution; this adds no
live allocation/removal or rollout authority and does not reverse `retain-volumes`.

Cover `tools/clean-room/run.sh`, both Linux and Windows arms of `run.ps1`,
their authentication/identity/result-permission helper containers, and
`tools/run_tests_in_devcontainer.py` with its temporary workspace volume.
Cover successful removal, failed launch, interruption, retained diagnostic
containers and cleanup failures. Preserve reports and shared images.

Do not change Index hooks, runtime behavior, installer-engine behavior or
unrelated fleet policy. Do not claim to intercept arbitrary external
`docker run` commands: only integrated producers acquire these guarantees.

## Architectural alignment

**Classification: vision-closing.** This realizes
[`agent-fabric` resource-accountability](../../../visions/agent-fabric/README.md#resource-accountability):
answer for allocated resources, settle incrementally and assert locally.
An unknown Docker status cannot erase an already-acknowledged active obligation.
It does not redefine the cleanliness verdict of a reusable borrowed resource.

Follow [`a-la-carte independence`](../../../docs/patterns/a-la-carte-independence.md)
and the existing claim-provider registry: ledger authority stays below venue
providers, optional composition does not install siblings, and callback commands
remain provenance-qualified. No new daemon, Docker SDK dependency in
agent-worktrees or finalize-time recursive Docker census is proposed.

## Confirmed source evidence

- `plugins/agent-worktrees/src/agent_worktrees/finalize.py`:
  `_assert_obligations_settled` blocks active non-session claims; the final
  `_RecordLock` check freezes ownership before cleanup. `pending-run:`
  reservations cannot be abandoned before real identity is journaled.
- `agent_worktrees/worktree_ops_cli.py::cmd_run` reserves a ledger entry
  before invoking its child and retains it when no resource can be parsed.
  It is not a general Docker producer protocol: it parses resource-shaped
  command output and does not bind daemon identity or prove Docker removal.
- `agent_worktrees/tracking_claim_write.py` owns transactional claim writes.
  The new API must compose with that authority and the finalize record lock,
  not write a parallel YAML store.
- `agent_worktrees/claim_providers.py` discovers provenance-qualified,
  payload-local provider callbacks. `agent_containers/claim_provider_cli.py`
  currently accepts a container name and operates against the current daemon;
  that identity is insufficient for temporary raw-Docker allocations.
- Clean-room wrappers allocate persistent, authentication and helper containers
  through raw Docker. Some paths remove a preexisting name or suppress cleanup
  errors; those behaviors cannot remain on integrated ownership paths.
- The devcontainer runner creates a bounded volume before `devcontainer up`,
  obtains its container ID afterward, and has separate teardown/orphan cleanup.
  Its host admission lease is not a creator obligation on a worktree.

## Reviewed design decisions

The following placements originated as **agent-recommended** choices and cleared
the plan-review gate in PR #6116. They govern the pending implementation of this
cross-owner lifecycle contract, not merely a call-site-only fix.

1. **Ledger authority stays in agent-worktrees.** Add a versioned generic
   resource-producer transaction API for reserve, bind, confirmed close-out and
   recovery. Use existing records, write authority and claim history; do not add
   a second authoritative store. Reject terminal/finalizing owners before
   allocation. Pending claims remain non-transferable until identity is bound.
2. **Docker mechanics stay outside agent-worktrees.** Factor a small independent
   Docker producer helper into a source-owned shared library, consumed explicitly
   by the source tools and vendored into agent-containers for its provider
   callbacks. Add a separate raw-Docker namespace through the existing provider
   registry, rather than treating these as fleet `container:<name>` refs.
   Missing agent-containers leaves status unknown and claims active; tool
   allocation/removal must not enable it, install it or bootstrap authentication.
   Agent-worktrees gains no upward import or hardcoded sibling invocation.
3. **Temporary lifetime is an explicit claim property.** A temporary container
   remains active while it exists, even if stopped, disconnected or its test
   process returned. Generic `at-rest`, release, sweep and handoff paths must
   not bypass that lifecycle restriction. A confirmed removal settles it;
   an explicitly accepted transfer moves responsibility without discarding it.
   Existing reusable/borrowed-resource settlement semantics stay unchanged.
   Classify lifetime at reservation: `disposable-owned` versus `fleet-borrowed`.
   A disposable obligation is not represented solely by a fleet borrowing lease.
4. **Bind immutable identity, not names.** Persist canonical daemon endpoint,
   authoritative daemon identity, full container ID, producer reservation token
   and owner evidence. Apply ownership labels at creation. Pin the endpoint for
   every later operation and verify daemon and labels before mutation.
   A volume has no container-style immutable ID: bind its daemon, unique name,
   creation evidence and reservation label, rejecting replacement instances.
   Names are display/selection aids only, never destructive authority.
   **Operator-selected clarification, 2026-10-13:** named volumes remain
   owned and held. Since the current Docker volume-removal interface cannot
   condition deletion on that birth evidence, automated volume removal is
   refused before mutation. Read-only identity/absence proof and explicitly
   accepted responsibility transfer remain supported; a separate cleanup
   mechanism must clear review before changing this restriction.
5. **Standalone runs are explicit, not fictitiously tracked.** Receive the exact
   ownership-tool argv and owner context through the producer interface; preserve
   child CWD/environment identity. An explicit valid owner can use an anchor's
   ledger where supported. With no ownership capability/owner, require explicit
   unowned diagnostic mode, emit a prominent untracked result and persist an
   identity receipt outside the checkout. This receipt is diagnostic evidence,
   not another claim ledger or a promise that finalize is gated.

The plan review explicitly covers these five choices, including the
shared-helper/provider placement, temporary-only settlement restriction and
explicit unowned standalone mode. A material change to that protocol requires
an amendment through the normal review flow; implementation is not permission
to bypass its invariants or the live cleanup confirmation gate.

## Transaction and failure requirements

- **Review-directed amendment, agent-recommended and operator accepted
  2026-10-12:** the generic allocation-in-progress transition must precede
  external creation. The allocator completes or binds only its attempt-specific
  fence; the retained monotonic epoch prevents stale absence through
  pending/allocating/pending ABA. Unknown or interrupted allocation stays owned.
  The exact ABI and authoritative quiescence-recovery contract are maintained
  in [the producer reference](../../../plugins/agent-worktrees/docs/resource-producer.md).
  This corrects the reviewed preallocation invariant; it does not authorize
  live allocation, cleanup, provider bootstrap or a second ledger.
- Reserve under the same ownership lock as finalize before the first Docker
  allocation. Persist endpoint/producer recovery evidence in the existing
  claim. Failure to reserve means no Docker allocation.
- Prefer create, bind, then start for containers. The durable reservation covers
  the create-to-bind interval. A failed bind forbids start or further mutation;
  retain the reservation and exact allocation evidence for repair.
- All wrapper helper allocations and devcontainer-created containers inherit
  born-at-creation labels. Bind each actual resource separately, without
  clearing its reservation first. Partial startup must remain attributable.
- Launch errors, timeouts, interruption and unknown daemon status retain an
  active reservation/claim. Settle a failed launch only after an authoritative
  probe of the pinned daemon confirms that the reservation created no resource.
- Cleanup validates exact identity and ownership immediately before removal,
  respects explicit cleanup authority, and records a verified absence receipt
  in the authoritative ledger afterward. Nonzero cleanup or verification exits
  fail visibly and leave the claim active. Never infer absence from an arbitrary
  inspect error, process exit, Docker name reuse or daemon mismatch.
- Named-volume retention is an explicit outcome, not successful full cleanup.
  Report the retained volume and active creator obligation when container
  cleanup completes; keep its finalize gate in force. No `down`, `prune`,
  disconnect, test-success or generic settlement path may turn that held
  volume into a released claim. A volume already absent may close only through
  the registered exact-subject, pinned-daemon proof, never a caller assertion.
- `down`, error cleanup and `prune` enumerate owned receipts, not broad name
  prefixes. No preemptive removal of another run's name, broad Docker prune,
  silently successful cleanup or label-only destructive selection.
- Retained credential/service containers remain held. Script return, disconnect,
  `--keep` and diagnostic retention never implicitly authorize `at-rest`.
- Recovery uses ledger reservations and authoritative label/daemon queries.
  Local receipts can assist diagnosis but cannot override ledger ownership.

## Registry-rooted ownership and daemon-session proof

**Agent-recommended implementation refinement:** satisfy the reviewed immutable
identity and confirmed-closeout contract with one ownership graph and a
daemon-bound operation session. This changes the Docker helper's transport
implementation, not the generic ledger ABI, fleet borrowing or cleanup authority.

- The existing ledger owns the edge from current owner to reservation and bound
  resource. A claim retains original creator, stable token, allocation epoch,
  canonical endpoint and immutable birth identity through accepted transfer.
  Docker labels carry creator/token/generation for recovery; they are evidence,
  not an alternative ownership store or destructive permission.
- Ownership views read that same graph. Owner-to-resource inspection includes
  pending, allocating, bound and retained obligations. Resource-to-owner lookup
  matches exact daemon/resource identity and follows accepted transfer evidence;
  incomplete records, duplicate owners or unavailable execution spaces remain
  explicit uncertainty, not an inferred unowned resource. A derived lookup
  view cannot override the locked producer transactions.
- Ordinary CLI snapshots are diagnostic only. Checking daemon A, listing on B,
  then checking A again cannot prove absence or authorize removal. Authoritative
  inventory, birth inspection and mutation must share one admitted daemon-bound
  connection for the entire operation, with no redirect or automatic reconnect.
  A broken/expired stream or incomplete response retains ownership.
- Route admission belongs to trusted provider configuration/code, never an
  owner-supplied claim field, caller assertion or successful diagnostic probe.
  The initial adapter covers explicitly admitted direct local Docker streams.
  Arbitrary TCP/SSH or intermediaries that can choose a backend per request
  remain unavailable for authoritative proof until a binding adapter clears
  separate review. A persistent stream alone is not proof of backend identity.
- The helper owns its bounded, windowless transport child and descendants;
  cleanup failure is visible. Session exit is not allocation quiescence and
  must never synthesize `allocation_finished`. No new background daemon, Docker
  SDK in agent-worktrees, registry or finalize-time Docker census is introduced.
- Automatic named-volume deletion remains refused. A current owner may close an
  already-absent volume only through the same authoritative exact-subject proof,
  or transfer a bound obligation through explicit acceptance.

The separate-process A/B/A fixture reproduces false absence in the original
CLI implementation. The source foundation now labels CLI results diagnostic-only
and refuses authoritative proof and automatic removal before invocation.
It does not implement the binding session or close-out contract yet.
Required source validation covers same-session identity,
absence and deletion; connection loss/reconnect refusal; nonbinding-route
admission; malformed/truncated/oversized responses; bounded child-tree cleanup;
and original-creator/current-owner visibility through retained resources and
accepted transfers. Native fixture proofs remain distinct from live Docker
acceptance. Producer enablement stays gated by the validation plan below.

## Validation plan

- [x] Source ledger ABI: verify begin/close serialization, in-progress refusal, generation-fenced
      completion/binding, stale absence through ABA, lost-fence quiescence
      recovery and interruption retention in isolated regression fixtures.
      Integrated producer enablement remains gated by the Docker/live items below.
- [x] Source ledger ABI: verify accepted retry after source reaping, recipient/incoming token
      uniqueness and preservation of already closed/transferred recipients in
      isolated regression fixtures.
- [x] Source ledger ABI: prove the real finalize entrypoint refuses an initially empty owner after
      reservation but before allocation; prove its final locked check rejects a
      concurrent producer or the producer refuses an already-finalizing owner.
      Proven by isolated local tracking/Git fixtures and a separate-process
      sidecar-lock contender in `test_resource_producer_finalize.py`, never an
      active parent worktree. Docker producer integration remains pending below.
- [ ] Prove running and stopped-but-existing containers block; exact verified
      removal settles; cleanup failure, outage, daemon mismatch, malformed
      response and interrupted allocation remain active.
- [ ] Prove failed launch settles only after confirmed no allocation; bind
      failure never starts/mutates the resource; repair can bind an allocation
      born with the reservation labels after the caller was interrupted.
- [ ] Prove rename does not change identity, reused names/recreated resources
      cannot be removed, owner/label mismatch refuses and endpoint changes
      cannot create a false absence verdict.
- [ ] Prove admitted single-daemon operation sessions reject A/B/A routing,
      reconnect, truncated/oversized responses and unavailable transport
      admission; confirm bounded transport-child cleanup.
- [ ] Prove ownership views preserve original creator and current owner,
      expose all allocation/retention phases and accepted transfers, and do
      not treat duplicate/unavailable records as unowned resources.
- [ ] Prove named volumes remain owned and held after container/test completion;
      automatic deletion refuses before a name-based mutation, retained status
      is visible, and finalize remains blocked. Review an identity-safe volume
      cleanup mechanism separately before enabling automated deletion; this
      open requirement is not discharged by container-only lifecycle proof.
- [ ] Prove no generic settle/release/sweep/disconnect path fabricates safe
      temporary-container state; explicit accepted transfer preserves ownership.
- [ ] Prove fleet lease return remains valid without deleting its reusable
      container, while the same return/disconnect operation cannot settle a
      disposable-owned container claim.
- [ ] Exercise actual Bash and PowerShell allocation/cleanup call sites with
      fake Docker and fake ownership-tool process boundaries, including auth,
      identity probes, permission helpers and both Windows/Linux arms.
      Exercise actual devcontainer runner success, partial startup and teardown.
- [ ] Exercise no-agent-worktrees/anchor behavior, explicit unowned persistence
      and exact argv/CWD/environment preservation. Prove no sibling provisioning
      or authentication bootstrap occurs to obtain ownership.
- [ ] Run focused suites through a bounded supervisor, then relevant guards,
      F/E9 lint, module ceilings and payload/vendoring checks. Add patch
      changefiles only for affected plugin payloads; preserve existing Index
      changefiles and do not hand-edit versions.
- [ ] After separate explicit cleanup authorization, perform a harmless live
      sleep-container proof with no host mounts, privilege, networking or auth.
      Verify the actual ledger/finalize gate, exact-ID cleanup and absence
      receipt. Mocked tests do not satisfy this live item.
- [ ] Report native PowerShell/Windows-container/macOS evidence separately;
      parser checks or simulated platform tests are not native lifecycle proof.

The generic ledger is source-validated. Docker identity callbacks, integrated
allocation/cleanup paths and live/native Docker lifecycle proofs remain pending.
Physical cleanup requires separate explicit authority. As those integrations
land, update worktree obligation docs, clean-room architecture/operator docs and
the test-flow docs to describe only the guarantees actually delivered.

## Allocation-fence rationale

An absence observation alone cannot prevent a creator from subsequently
allocating and binding a resource. Mandatory allocation begin/completion fencing
serializes allocation permission with claim close-out, preserving cleanup
responsibility across interruption and stale absence observations.

Close settles resource cleanup responsibility after verified removal or no
allocation; handoff transfers that responsibility to an accepting worktree.
Neither operation closes a Copilot session, a conversation or a service, and
ledger transitions do not authorize physical cleanup.
