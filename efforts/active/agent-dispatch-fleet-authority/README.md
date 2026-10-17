# agent-dispatch: fleet authority -- centralized queue, federated local
# coordinators, and a projected relay for untrusted containers

- **Slug:** `agent-dispatch-fleet-authority`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-phase PRs against `dev`
- **Created:** 2026-10-13
- **Status:** Active. Design merged in #6618; initial task grants merged in
  #6630. Check-in found missing deny-by-default route authorization and
  transactional incarnation fences; those corrections merged in #6665.
  Phase 2 assignment push merged in #6698; the strict task-only client merged
  in #6718. Trusted restricted-worker delivery and renewal are next.
  Direct restricted-worker calls, container placement
  and explicit local/central authority remain the completion gate.
- **Vision:** [`machine-fleet`](../../../visions/machine-fleet/README.md):
  `authenticated-scoped-and-revocable` (routing/control authorized per
  principal, service, operation, fleet scope, target), `one-owner-per-kind-
  of-state` (agent-dispatch keeps owning task/claim state; nothing here
  creates a second queue), `recoverable-not-connection-owned` (accepted work
  survives controller/connection replacement), `repeatable-role-fleet-
  adoption` (containerized role deployment on admitted hosts). Extends the
  packaging work already landed in
  [`agent-dispatch-standalone-container`](../agent-dispatch-standalone-container/README.md)
  (the `services/dispatch` distribution + container image this effort
  deploys centrally) rather than duplicating it.
- **Umbrella issue:** [#6617](https://github.com/ThomasMichon/copilot-extensions/issues/6617)

## Guiding Intent

Operator request (2026-10-13, verbatim intent): a centralized `dispatch`
service (like `services/index`) holds the task queue "on behalf of
everyone," with per-machine supervisors subscribed to it; a machine that
still runs its own local coordinator must "federate" with the central
daemon so authority is unambiguous; every fleet machine type (user-local,
Windows DevBox, CDE/container) should be able to receive tasks from the
central coordinator and post back updates; and for untrusted/identity-
managed containers we need a "projected relay" (possibly just the local
supervisor) that only lets a containerized agent read/write **its own**
task(s) with valid state transitions.

## Context

### What already exists (do not duplicate)

A dedicated research pass (2026-10-13, full citations in the Journal)
found that most of the *mechanics* this request needs are **already
built and shipped**, under names the operator's phrasing doesn't use
verbatim. This effort is deliberately scoped to the **real, precise gaps**,
not a rewrite:

| Already built | Where | Status |
|---|---|---|
| Peer/coordinator awareness directory (`register`/`heartbeat`/`discover_peers`) | `federation.py`, `satellites.py` (`FleetDirectory`) | Shipped (federation Phase 1) |
| Coordinator **discovery** (highest live epoch, not election) + two Rendezvous backends (hosted/Gateway, Dev Tunnels) | `federation.py`, `federation_runner.py`, `devtunnel_rendezvous.py` | Shipped (Phases 1-4) |
| Fenced-epoch coordinator lease + standby takeover | `lease.py` (`CoordinatorLease`, `_take_over`) | Shipped (federation Phase 2) |
| A constrained, outbound-only leaf role that pulls only its own affinitied work | `satellites.py` (`ROLE_SATELLITE`), `satellite_work_intake.py` | Shipped (satellite-agent-exposure effort) |
| Valid-state-transition table + ownership CAS (`owner`/`owner_session_id`) | `task_state_machine.py`, `queue_lifecycle.py` | Shipped |
| A centralized, container-runnable coordinator distribution | `services/dispatch/` + its `Dockerfile` | Shipped (`agent-dispatch-standalone-container` effort) |
| Pool-of-hosts spawn (Model C: origin-owned lease, liveness-gated SSH selection) | `fleet.py`, `embody.py` (`spawn_fleet_embodied_worker`/`spawn_fleet_headless_worker`) | Shipped |
| The strategic shape itself ("central Gateway + per-service authorities, no replacement registry/queue, containerized roles on admitted hosts") | `visions/machine-fleet/README.md` | Already the declared target state |

**The real gaps** (full evidence in the Journal's research-report citation):

1. **No connection-bound, per-worker/per-task authorization.** Every
   task-lifecycle HTTP route (`coordinator_tasks.py`) takes `worker_id` /
   `owner_session_id` straight out of the caller-supplied JSON body
   (e.g. `coordinator_tasks.py:590,669-786`). The only gate is a single
   shared bearer token (`coordinator_auth.py:_make_auth`) -- any holder of
   that token can assert **any** `worker_id` against **any** task. This is
   the exact missing piece for a "projected relay": the state-machine +
   ownership-CAS correctness layer already rejects an out-of-turn
   transition from the **wrong current owner**, but nothing stops a caller
   from lying about *which* owner it is in the first place.
2. **`FencingGuard` is declared but not wired.** `lease.py` ships a working
   `FencingGuard`/`FencedError` (`lease.py:269-298`) that rejects a stale
   (deposed) coordinator's directive by epoch -- but its own module
   docstring admits "no server-side wiring of the guard onto claim/write
   endpoints" (`lease.py:27-28`), confirmed by grep: no route in
   `coordinator_tasks.py` calls it. Without this, "federate so authority is
   clear" is only true at the directory/awareness layer, not at the actual
   task-write layer a fenced-out local coordinator could still reach.
3. **No container/DevBox-API-aware fleet host selection.** `FleetSpawner`
   (`fleet.py`) only knows SSH + `agent-worktrees`/`agent-bridge`-on-PATH
   liveness. A CDE/container or Dev Box with no SSH-reachable, pre-
   installed binstub has no existing route into fleet dispatch today.
4. **No subscribed assignment consumer.** Existing work-acquisition patterns
   are: spawn-locally, `FleetSpawner`-push-via-SSH-to-a-host-it-selects, or
   satellite-polls-its-own-affinitied-work. The coordinator already exposes
   SSE events with a subscription-ready barrier, but intake does not consume
   them. **Operator decision (2026-10-13):
   build push, with the existing poll as a long-interval backstop** (not
   poll-only) -- throughput and avoiding over-polling matter. A **webhook**
   (coordinator calls back into the supervisor) is the wrong shape here: it
   would require the satellite role's deliberately **outbound-only, no-
   inbound-listener** contract (`federation_runner.py:156-180`,
   `satellite_work_intake.py:1-24`) to grow an inbound port, contradicting
   that effort's own stated security posture. The right shape is an
   **outbound SSE subscription** to the existing event bus. The consumer
   reconciles the authoritative queue after each ready frame and relevant
   notification; a slower periodic read recovers non-replayed event gaps.
   This preserves the no-inbound-listener invariant without another durable
   assignment waiter or claim transport. See Phase 2.
5. **"Centralized ... with per-machine supervisors subscribed"** reads most
   naturally as a **deployment/configuration choice**, not new code: point
   every machine's `AGENT_DISPATCH_SHARED_URL` at one `services/dispatch`
   instance and never grant the `coordinator`/`standby` federation role to
   any other machine (`config.FEDERATION_ROLES`, `config.py:669` already
   supports `peer`/`coordinator`/`standby`/`satellite` as a pure config
   value). This is the low-friction reading and **this effort recommends
   it** over inventing a second, statically-pinned-authority code path --
   see Phase 3.

## Request

> We want a `dispatch` service that can hold the task queue on behalf of
> "everyone", with individual machines' supervisors subscribed to it. If a
> local machine still has its own coordinator service, it will need to
> "federate" with the centralized daemon, basically syncing or projecting
> data, to ensure that authority is clear. Every machine in a fleet,
> user-local, Windows DevBox, or CDE, should be able to receive tasks from
> the central coordinator and their agents can post back updates. For
> untrusted or identity-managed containers, we'll need a "projected relay"
> (which could just be the local supervisor) that ensures containerized
> agents can only read and write their tasks with valid state transitions.

## Plan

### Phase 0 -- Draft and review
- [x] Research pass: map existing federation/fleet/satellite/fencing
  infrastructure before proposing anything (see Journal for the full
  report). Confirmed no duplicate-effort risk; confirmed the real gaps
  listed above.
- [x] Draft this effort doc.
- [x] Review with the operator (2026-10-13): scope confirmed with one
  change -- build push notification (with poll as a long-interval
  backstop), not poll-only as originally recommended; Phase 2 (now Phase 3,
  projected relay) starts with direct scoped-token calls; container/
  DevBox-aware fleet host selection (now Phase 5) is in scope now, not
  deferred.
- [x] Open the umbrella issue ([#6617](https://github.com/ThomasMichon/copilot-extensions/issues/6617)).

### Phase 1 -- Task-scoped capability tokens (the authorization gap)
**Landed.** This is the security-critical core of the "projected relay":
binding a caller's asserted `worker_id`/task identity to something the
server actually verifies, reusing the existing CAS/epoch fields rather than
inventing new durable state.

- [x] Add a **task-scoped capability token**: an HMAC derived from the
  coordinator's `control_token` (mirroring the existing
  `coordinator_auth.scoped_control_token` pattern) over
  `(task_id, worker_id, owner_session_id, coordinator_epoch, exp)`. New
  module `task_scope.py` (`mint_task_scope_token`/`verify_task_scope_token`/
  `TaskScopeClaims`). Minted by the coordinator at `/tasks/{id}/start`
  (claim-confirm, the point ownership is actually established) via a new
  `task_token` field in its response. No new durable row: verification
  recomputes the HMAC from the task's **current** DB state (via
  `_resolve_task_authority` in `coordinator_tasks.py`), so a token
  naturally invalidates the instant the task's owner/session/epoch changes
  (reassignment, completion, fencing takeover) -- no revocation list
  needed. **Scope note:** minting happens only at `start`; the fleet-push/
  satellite assignment paths named in the original sketch don't yet mint
  one -- a satellite pulls its own work today (`satellite_work_intake.py`)
  and calls `start` itself with the full bearer, so this is not a gap for
  the satellite role specifically, only for a not-yet-built push-
  assignment path (see Phase 2).
- [x] Add the authorization layer: `coordinator_auth._make_auth` now
  accepts a task-scoped token presented via a distinct header
  (`X-Agent-Dispatch-Task-Token`, `task_scope.TASK_SCOPE_HEADER`) as an
  ALTERNATE credential to the shared/control bearer -- storing verified
  (but not yet task-row-checked) claims on `request.state.task_scope`.
  `coordinator_tasks._resolve_task_authority` is the actual authorization
  step: when a request carries scoped claims, it derives the effective
  `worker_id`/`owner_session_id` **from the verified token** (ignoring any
  mismatching body value), confirms the claims still match the task's
  CURRENT owner/`owner_session_id`, and checks the embedded epoch via
  `FencingGuard`. Wired on `start`, `progress`, `complete`, `abandon`, and
  `heartbeat`. **Scope note:** `yield`/`suspend`/`resume`/`release`/
  `set_card`/etc. are not yet wired -- `resume` in particular has
  non-trivial `adopt_session` branching that deserves its own careful pass
  rather than a mechanical copy-paste; tracked as a Phase-1 follow-up, not
  silently dropped.
- [x] Wire `FencingGuard.check()`/`is_valid()` onto the task-scoped-token
  path specifically (`_resolve_task_authority`'s epoch check) -- closing
  the declared-but-unattached gap in `lease.py` for token-authenticated
  callers. The guard is constructed directly from the coordinator's own
  in-process `FleetDirectory` (`FencingGuard(directory)` in
  `coordinator.py`'s `create_app`) -- no network hop, and an unregistered
  directory (no `FederationRunner` ticking) reports epoch 0, so a non-
  federated coordinator's behavior is unchanged. **Scope note:** full-
  bearer (shared-token) callers do not yet carry/check an epoch -- that
  would need an additive opt-in field for a federated local coordinator's
  own forwarded writes, deferred to Phase 4 (centralization) since it's
  only meaningful once a real federated deployment exists to test it
  against.
- [x] Unit tests: `tests/test_task_scope.py` (pure mint/verify/invalidation/
  tamper/expiry, no FastAPI) + `tests/test_task_scope_auth.py` (real
  coordinator routes: minting on `start`, scoped auth on `progress`/
  `complete`/`abandon`/`heartbeat`, cross-task rejection, worker_id-
  mismatch-in-body is overridden not trusted, reassignment invalidates the
  old token, forged-signature rejection, no-control-token-means-no-mint,
  and the fencing-guard rejecting a token from a superseded epoch) -- 18
  new tests, all passing. Full existing `coordinator`/`lease`/
  `federation`/`satellite`/`fencing`-related suites re-run clean (403 + 247
  passed) to confirm no regression to the unchanged full-bearer path.

### Phase 2 -- Push assignment notification with poll backstop
Operator decision: push-first delivery with slower polling as a correctness
backstop. Reuse the coordinator's existing `/events`, `EventBus`, and
subscription-ready frame; no additional long-poll endpoint is needed.

- [x] Subscribe outbound to coordinator SSE and reconcile the authoritative
  queue after each ready frame, including reconnect, so pre-subscription work
  cannot fall into a gap.
- [x] Coalesce relevant enqueue, assignment and capacity-release notifications
  by machine/repository affinity; wake the existing intake/supervision loop
  without making the event stream another claim authority.
- [x] Keep slower periodic reconciliation for missed events and disconnected
  streams. Bound reconnect/backoff and report degraded subscription health.
  Presence heartbeats remain independent of work-intake blocking.
- [x] Bound each subscription's queue, read/keepalive timing and shutdown.
  Disable or repoint subscriptions with their owning role/configuration.
- [x] Test actual enqueue-to-wake latency, pre-ready and reconnect gaps, event
  coalescing, cross-affinity isolation, missed-event poll recovery and teardown.

### Phase 3 -- Projected relay component
- [x] Implement a strict task-only client/CLI boundary before any ordinary
  endpoint discovery, autostart, identity lookup or fleet credential path.
  This is the worker call surface only; automatic delivery remains below.
- [x] Add control-authorized grant issuance/renewal fenced to the existing
  worker, native session, generation and coordinator epoch, with a reloadable
  private token-file client. Renewal neither starts/rebinds the task nor
  extends its lease. Provider delivery and supervisor renewal remain below.
- [ ] Implement the relay as an **operating mode of the existing local
  supervisor** (`supervisor.py`/`supervisor_daemon.py`), not a new
  standalone process -- per the operator's own "could just be the local
  supervisor" framing, and consistent with `visions/machine-fleet`'s
  "Fleet adapters route supported operations to these owners rather than
  building competing stores or supervisors."
- [ ] When the supervisor places a task into an untrusted/identity-managed
  container (a CDE, an ephemeral Dev Box session, any host where the
  operator does not want the shared bearer token to ever land), it mints
  the Phase-1 task-scoped token for that exact task/worker identity and
  injects **only that token** into the container (env var or a mounted
  read-only file) -- the shared/control bearer never enters the container.
  Renewal must recheck the current owner, native session and task generation;
  the worker cannot mint or renew through issuer authority of its own.
- [x] **Approach confirmed by the operator (2026-10-13): start with direct
  scoped-token calls** -- the containerized agent calls the coordinator's
  HTTP API directly using only the scoped token; the token alone already
  enforces the task/transition boundary (Phase 1). A stricter local
  reverse-proxy relay (hiding the coordinator's other routes entirely)
  remains a documented future option if a concrete threat model later
  requires it, but is not built in this phase.
- [ ] Tests + a clean-room scenario: a container holding only a task-
  scoped token can complete/fail/progress its own task, and is rejected
  (401/403, not merely ignored) attempting any other task or the shared-
  bearer-only routes.

### Phase 4 -- Central authority and local projections
Discovery and a standby role do not replicate queue
data or fence independent database writers. This phase requires implementation
and tests, not just configuration documentation. Centrally owned tasks retain
one central write authority; a local projection cannot claim them offline or
promote its independent database. Explicitly separate local-only tasks and
central task references. Any ownership migration must be acknowledged and
fenced at the durable state owner before activation.

- [ ] Document (in `services/dispatch/README.md` and/or
  `docs/patterns/`) the recommended fleet-centralization shape: one
  `services/dispatch` instance (container or native) as the pinned
  `AGENT_DISPATCH_SHARED_URL`; every other machine's agent-dispatch
  configured `peer` or `satellite` only (never `coordinator`/`standby`)
  against that URL, with explicit authenticated service authority.
- [ ] For a machine that genuinely needs to keep running its own local
  coordinator, implement explicitly local-only task authority alongside
  read projections/references for centrally owned tasks. Central mutations
  must reach the central writer; outages never promote the independent
  local database. Fence any deliberate ownership transfer at the durable owner.
- [ ] Clean-room scenario: extend `agent-dispatch-cutover` (or add a new
  scenario) to prove a `peer`/`satellite`-configured machine correctly
  federates against a central `services/dispatch` container -- i.e. this
  is validated as real end-to-end behavior, not left as prose.

### Phase 5 -- Container/DevBox-aware fleet host selection
Operator decision (2026-10-13): in scope now, alongside Phases 1-4, not
deferred.

- [ ] Extend `FleetSpawner`'s liveness/selection beyond SSH +
  `agent-worktrees`/`agent-bridge`-on-PATH to recognize a container or
  Dev Box pool-host kind, composing an existing lightweight container
  manager / authenticated host deployment mechanism per
  `visions/machine-fleet`'s "Role deployments and container managers"
  concept -- explicitly **not** a new scheduler.
- [ ] This phase has no existing precedent to extend (confirmed by
  research: zero `devbox`/`container` references in `fleet.py`/`embody.py`
  host-selection today) -- needs its own scoping decision on which
  container runtime / Dev Box control-plane API to target first. Likely
  sequencing: a container-exec host kind first (lowest-friction, composes
  an already-adopted container manager per the vision), Dev Box control-
  plane API as a follow-on slice once the container shape is proven.

## Validation Plan
- [x] Phase 1: unit tests for token mint/verify/invalidation + fencing-guard
  rejection, run against the real `coordinator_tasks.py` routes (not just
  the token/fencing library functions in isolation). Done -- see Journal.
- [x] Phase 2: real SSE enqueue-to-wake timing, ready/reconnect reconciliation,
  cross-affinity isolation and missed-event recovery through slower polling.
- [ ] Phase 3: a real clean-room container holding only a task-scoped
  token can only touch its own task; cross-task/cross-worker attempts are
  rejected.
- [ ] Phase 4: a real multi-instance clean-room run (one `services/dispatch`
  container as the shared coordinator, a second instance configured
  `peer`/`satellite`) proves federation end-to-end, not just unit-level.
- [ ] Phase 5: a clean-room (or equivalent isolated) run proves a task
  actually lands and runs on a container-kind pool host, not just that the
  liveness probe recognizes it.
- [ ] Existing plugin-delivered and standalone coordinator behavior remains
  unaffected for callers that don't present a task-scoped token (the
  shared-bearer path keeps working exactly as today).
- [ ] Touched-code lint, relevant suites, changefiles, documentation-impact
  review, and required CI before each phase's merge.

## Proposal
_Reviewed by the operator 2026-10-13 -- see Journal for the decisions
recorded against each open fork._

## Journal

### 2026-10-15 -- Returned provider argv supports appended agent arguments
- Review identified an actual parser defect in the returned task launch command:
  identity options after the container name interrupted `nargs="*"` parsing.
  Both physical/binding identity options now precede the container name.
- A regression drives the actual CLI parser with the returned command plus
  `-- --agent worker`, asserts the exact identities and forwarded arguments,
  and failed with argparse exit two before this correction. Final projection
  selection passes 63 in 6.08 seconds; touched Python lint passes. No task,
  credential or launch authority changed.

### 2026-10-15 -- Complete provider consumer acceptance remains runnable
- Review found the original opt-in Docker scenario still invoking the old
  builder signature. It now reserves and publishes the actual generation/nonce
  under host session admission before building its guest command, matching the
  production task lane rather than supplying an invented pair.
- The complete scenario exposed repeated immutable-executable discovery pushing
  cold worker readiness past its unchanged 30-second bound. Production now reuses
  verified paths only within one admission operation, keyed by physical ID,
  user, executable kind, start generation, configuration and mounts. No cache
  survives an operation. The builder no longer repeats root lock initialization
  already required by successful admission publication.
- The cache-invalidation and coupled native selection passes 184. The original
  complete Docker acceptance invocation passes one in 157.00 seconds with its
  pre-existing 300-second item / 420-second sub-suite / 480-second aggregate
  budgets and unchanged command/readiness limits. It exercises token privacy and
  rotation, surviving guest publishers, cold CLI execution, duplicate refusal,
  wrapper-only death and stop/remove/recreate deferral. No fixtures remain.
  Touched Python lint passes.

### 2026-10-15 -- Guest lifecycle closure and mixed-environment recovery
- Current-head #6745 review found that a delayed Docker exec could outlive its
  wrapper, resume after the final idle probe and start before destruction.
  The final action now closes root-owned guest admission under the worker lock.
  Each host launch/closure reserves a monotonic private generation before
  publication; an admitted launch also receives a fresh nonce. Workers check
  that exact pair after acquiring the guest lock. Old launches and surviving
  helpers cannot cross closure or restore an older admission after reopening.
- Added a cleanup-only kernel transaction for legacy admission and lifecycle
  records. Each environment can remove only its locally verified-dead writers;
  foreign and live writers remain protected. Normal admission does not inherit
  this exception. A two-environment regression proves sequential cleanup restores
  operation without treating blocked heartbeat expiry as evidence of death.
- Coupled native projection, replacement, lease and cutover contracts: 183
  passed. Actual offline, read-only, capability-dropped Docker guest proof:
  one passed in 24.04 seconds. It kills only the host wrapper before the guest
  lock, verifies the surviving guest, closes admission, then observes refusal
  with no application execution. Reopening rejects stale helpers and launch
  argv while a fresh admitted launch succeeds. Touched Python lint and the
  13-plugin install contract pass.
- The first Docker proof hit its unchanged test limit during repeated immutable
  executable discovery. Its final fixture reuses real verified resolutions
  only for the same physical instance, start generation, configuration and
  mounts; no production cache or timeout widening was introduced. The timed-out
  fixture was explicitly removed, and the final run left no fixture containers.
  Helpers also have bounded lifetimes and automatic Docker cleanup.
- This is provider/primitive acceptance, not real central-task delivery,
  supervisor renewal or first fleet enrollment. Those parent-effort gates
  remain open; promotion and authorized rollout remain automatic/external.

### 2026-10-15 -- Foreign legacy cutover cannot expire its own protection
- Current-head review of #6745 found a real migration defect: replacing the
  legacy lock pathname blocks old heartbeat writers, so elapsed heartbeat
  time cannot establish foreign-owner quiescence. Foreign legacy admission
  and lifecycle records now remain blocking regardless of expiry.
  The owning environment may proceed only after verifying its PID is dead.
- Both admission and hold regressions exercise initial refusal, expiry after
  blocked refresh, refused stale cleanup, unchanged record bytes and
  same-environment live/dead-PID verification. The coupled native lease and
  cutover selection passes 56; touched Python lint passes. Existing real
  mixed-version process proof remains in that selection. No service rollout,
  manual promotion or merge-gate bypass occurred.

### 2026-10-14 -- Provider review: active locks and surviving guest workers
- PR #6745's first review identified two real boundary gaps: the shared
  lease mutex could steal an aged live lock while projection was still
  performing Docker I/O, and host-wrapper PID admission could disappear
  while its guest worker remained alive.
- Admission, lifecycle and binding writers now share the existing
  kernel-owned private file lock rather than age-based unlink/recovery.
  Delayed concurrent preparation, native binding and rotation tests
  age an actively held lock and prove a contender cannot enter or roll
  back the incarnation/expiry.
- Guest execution holds `flock` through an immutable absolute executable
  and a stable root-owned read-only lock file. The actual Docker regression
  kills only the host wrapper, confirms its long-running guest is still
  alive, and observes a second explicit task launch refused with exit 75.
  No resident daemon or new credential authority is introduced.
- The contained lease/projection/helper selection passed 88 tests with
  one platform/opt-in skip. The fresh Docker credential and wrapper-death
  scenario passed; touched-code lint passed. These close the cited review
  defects, not the parent fleet/supervisor/central-authority gates.

### 2026-10-14 -- Restricted provider delivery and birth authority
- Added an explicit `dispatch_only` creation policy for restricted
  image-backed fleets. Provider creation pins a random, nonsecret birth
  identity in an immutable Docker label and an exclusive private record.
  Missing or corrupt birth records cannot be treated as a free container.
- Preparation seals that physical identity to one task, worker, generation,
  epoch and endpoint. The native conversation is bound once during delivery,
  before credential publication; interrupted delivery retains that fence.
  A different incarnation requires a newly created physical container.
- The host sends only the task credential over Docker stdin, through an
  immutable absolute Node helper, to an atomic root-owned `0444` file under
  protected `/run` state. The worker receives nonsecret pinned call fields and
  the file path. Ordinary launch/provider SSH cannot adopt the task-only
  member, and provider admission permits only one worker at a time.
- The native contained projection/admission/profile selection passed 220
  tests with three opt-in/platform skips. A disposable real Docker run proved
  two credential publications, nonroot read/no-write permissions, no token
  in retained state or Docker configuration, refusal to rebind task/native
  identities, and the actual fresh provider CLI launch/ordinary-peer denial.
  The first CLI fixture used integer CPU configuration rather than the
  loader's canonical float, correctly triggering the policy fingerprint
  guard; its corrected canonical fixture passed.
- The expanded ordinary CLI/fleet/provider-SSH compatibility selection passed
  304 tests with five opt-in/platform skips. Touched-code lint and the
  thirteen-plugin install contract passed.
- Publication caught growth past the CLI's shrink-only ceiling and the
  fleet module's cap. The task-only launch and exec registrar, plus the
  birth-aware image argument/publication seam, now live in the provider
  projection module without changing ordinary transport or monkeypatch
  boundaries. The complete native plugin suite passed both sub-suites
  (793 passed, 22 skipped), and the actual fresh Docker/CLI proof passed
  again after extraction. No module baseline was widened.
- This closes only the provider channel, not automatic supervisor scheduling,
  credential renewal delivery, admitted fleet selection, central/local
  projections, first-touch installation or real coordinator task execution.
  The opt-in channel composes the existing Docker provider and admission
  lifecycle; it introduces no new queue, credential issuer or daemon.

### 2026-10-15 -- Known supersession takes precedence over discovery outage
- Further #6722 review reproduced an active epoch-one lease renewing during a
  discovery-only outage after independently reading floor five. The lease now
  clears active status immediately on known supersession, before entering the
  old availability fallback. It neither renews the old entry nor withdraws or
  overwrites the successor when discovery is unavailable.
- The new regression failed before the two-line state correction. Final lease
  selection passes 41, including retained-floor HTTP takeover, same-floor
  discovery-outage heartbeat compatibility and the superseded-owner refusal.
  Touched Python lint passes. Required published-head review and CI remain gates.

### 2026-10-15 -- HTTP replacement leases consume the durable floor
- Follow-up #6722 review found the HTTP rendezvous still exposing only live
  coordinator presence. Added a non-cacheable `/directory/fencing-epoch` read,
  strict client validation and the hosted rendezvous getter used before lease
  takeover. An absent, malformed or unavailable authoritative floor never
  becomes an epoch-zero fallback; older authorities require an upgrade before
  a new coordinator/standby lease can activate.
- Real HTTP expiry and fresh-server construction against retained state both
  advance replacement leases from floor five to epoch six and reject epoch-five
  fencing tokens afterward. Directory epoch inputs and returned floors are
  bounded to SQLite's integer range; empty, invalid UTF-8 and malformed JSON
  report explicit client errors.
- Final coupled native grant, lease, directory, scoped-route and federation
  selection: 129 passed in 71.95 seconds. No ordinary test deadline, queue
  ownership, service installation or promotion behavior was changed.
- The complete Windows coverage run passed 605 tests with six platform skips,
  but correctly refused to earn a baseline: a native child crosses an opaque
  build-tool boundary, and copied SDK modules execute with `-I -S -B`, which
  suppresses interpreter startup hooks. Copied SDK bytes were verified against
  17 canonical modules. A separate design decision is required for genuine
  trace activation; no wrapper, argv rewrite, approximate attribution or
  exemption was silently introduced, and strict coverage activation remains off.

### 2026-10-15 -- Coordinator presence loss cannot lower task fencing
- Current-head review of #6722 reproduced epoch-zero renewal succeeding after
  an unread epoch-five registration expired. The task queue now durably retains
  a monotonic coordinator floor; registration and coordinator heartbeats commit
  that floor before advertising presence. Expiry, deregistration, demotion and
  restart cannot restore older task grants. A never-federated queue stays at zero.
- Fencing guards retain observed floors and lease takeover advances above the
  durable floor, including after restart or lower-epoch discovery. A restarted
  lease for the still-advertised coordinator adopts its actual retained epoch.
  This adds one queue-owned metadata table, not another queue or state authority.
- Coupled native grant, lease, directory, scoped-route and federation selection:
  117 passed; touched Python lint passes. Proof includes the originally failing
  real HTTP route, concurrent database writers, fresh app/queue construction,
  and a genuinely cold native child reading the durable floor.
- A disconnected, read-only existing Linux validation image lacks pytest.
  No dependency or installer configuration was changed to work around that
  absence. Required published-head CI remains the Linux runtime validation gate;
  real central-container enrollment remains an explicit campaign gate.

### 2026-10-15 -- Renewal narrowed and lightweight collection prototype
- Rebased #6722 while preserving upstream CI and scope contracts; removed
  this slice's incidental CI-budget edits and obsolete budget documentation.
  Grant issuance and credential-file consumption remain the only runtime
  behavior changes. Required CI and current-head review still gate merging.
- Rebased grant/CLI selection: 95 passed, one platform skip, and one native
  HTTP subprocess exceeded its unchanged 20-second bound. The isolated
  direct/file HTTP pair then passed twice under the original bounds.
  No deadline, fixture, skip, or CI bypass was changed.
- The lightweight child-attribution prototype eliminates coverage-package
  import and startup waits: median prelude 0.108 seconds, 13.2 times faster;
  maximum 0.579 seconds, so the uniform 0.2-second target is not yet met.
  The real two-node recovery pair passes both tests with its original
  deadlines and a contained wall-time ratio of 0.923 versus ordinary execution.
  Exact normal, abrupt-exit, forced-termination and thread-only attribution
  parity passed. Integration and producer-completeness accounting are next;
  no complete native baseline or strict CI activation is claimed.

### 2026-10-14 -- Coverage-selected fleet CI and provider review corrections
- Narrow renewal PR #6722 back to its issuance/consumption contract.
  Its incidental CI-budget changes force unrelated whole-fleet and bridge
  validation despite touching neither runtime. Retain the measured budget
  corrections under #6733 for the dedicated coverage/CI activation slice,
  rather than coupling that infrastructure bootstrap to renewal publication.
  Required checks still run; no timing gate or failing test is waived.
- The operator subsequently requested a massively more efficient profiling
  path, not timing-family exemptions. Child instrumentation optimization is
  now measured against the ordinary five-/fifteen-second boundaries and
  unchanged process concurrency; no fixture deadline is expanded.
- The operator requires coverage-based selection for the fleet lane, not a
  full suite, fixed smoke filter or increased full-suite timeout. Bootstrap
  the standalone dispatch service's trusted coverage collection/enrollment
  in the existing full promotion lane first; activate a strict selected-node
  required runner only after a real accepted baseline is available. Missing
  or stale evidence must then fail explicitly, without a full-suite fallback.
- PR #6745's current review found three remaining provider gaps: acknowledged
  expiry did not fence ambiguous credential delivery, live host admission
  refused a duplicate with the wrong exit code, and lifecycle only probed
  Copilot markers rather than the guest-held task lock. The correction keeps
  a separate attempted-expiry fence before publication, maps the specific
  busy refusal to exit 75, and probes guest liveness under the lifecycle hold
  both before rescue and before destruction.
- The native focused provider contracts passed 116 tests with three opt-in
  skips. Real Docker proved a live-wrapper duplicate is busy, a guest survives
  termination of only its wrapper, and stop/remove/recreate all defer after
  host admission is pruned. No module-size baseline or required CI gate was
  widened. This is provider lifetime proof, not central task execution.
- The provider PR's CI also exposed a retry fixture that monkeypatched shared
  standard-library sleep and counted unrelated subprocess timing. Isolating
  that module's clock instrumentation preserves real child conflicts and
  retries; all four native cases passed with containment state checks.
- Both renewal and provider PRs remain open. Renewal merges first; supervisor
  integration, container task isolation, central/local authority and fleet
  placement remain unresolved parent completion gates.
- A subsequent provider review correctly identified guest helpers surviving
  Docker-client timeouts and live legacy wrappers using the old lock protocol.
  Publication now holds a guest lock and checks its own monotonic expiry before
  replacing the credential. A real timed-out-client regression proves the
  surviving older guest publisher is rejected after a newer publication.
- Host lock cutover uses a private directory barrier rather than reusing an
  unlinkable legacy inode. It waits for legacy lock release and refuses live
  legacy admission/lifecycle writers without terminating their workers.
  Native Windows and isolated POSIX process proofs cover mixed-version
  exclusion; the expanded native lease/admission contracts passed 201 tests
  with four platform/opt-in skips. Existing explicit stale-corruption recovery
  remains available without admitting live legacy writers.
- The coverage prerequisite is PR #6783. Its first required guard refused
  dynamic runner routing, so the correction uses two literal native runner
  lanes rather than weakening that guard. Review also requires crash-child
  coverage persistence and compatibility checks, plus a narrowly optional
  YAML-only assertion in the minimal manual caller. These corrections and
  fresh baseline evidence remain pending; no strict selector is activated.

### 2026-10-14 -- Renewal PR blocked by a measured CI job timeout
- PR #6722's post-description run 38610631180 passed all bridge test
  batches and the shared SSH contract tests, but the job exceeded its
  eight-minute wall-clock limit during teardown and was cancelled. The
  required aggregate correctly refused merge.
- Tracked the fix as #6733: preserve the full bridge smoke selection and
  required timeout failure semantics, with a bounded ten-minute allowance
  for setup, the measured seven-minute-plus test run and teardown.
  Dispatch retains twelve minutes and other smoke plugins retain eight.
  Three native workflow contract checks and touched-code lint passed;
  the real repaired CI run must still prove completion before merge.
- No retry, CI bypass or manual promotion substitutes for that proof.
- The repaired-head run 38617667464 proved bridge completion within the
  new bound, but exposed the same teardown failure in the combined native
  Windows runner: every step succeeded and the job exceeded seven minutes
  at 7m5s. Extend only that job's overall allowance to nine minutes, retaining
  each selected diagnostic/wrapper/payload/fleet step's own limit. This
  remains part of #6733; the required aggregate is not waived.
- Run 38625257770 completed the bounded jobs but correctly failed both
  platform payload-CWD scope checks: their independent contract still
  expected the old seven-minute Windows aggregate. Update that coupled
  assertion to nine while preserving Linux's five-minute bound, exact path
  selection, native selectors and required aggregate membership.
- All affected post-CI, installation-context scope and payload-CWD scope
  contracts passed together: 115 native tests, plus touched-code lint.

### 2026-10-14 -- Trusted renewal and private credential-file boundary
- Added control-only `POST /tasks/{id}/task-grant` and
  `DispatchClient.task_grant()`. Every request must identify the current held
  ownership incarnation and epoch; workers cannot mint or renew, including
  when a task header accompanies a control header. Responses contain the
  fixed-lifetime token and expiry, are non-cacheable, and do not persist
  credentials in task metadata, events or the audit trail.
- The CLI reloads a bounded absolute private token file on each invocation.
  Incomplete, unreadable, malformed and mixed projections fail closed before
  discovery, credential lookup or ordinary handlers. The file contains only
  the token; task, endpoint and worker identity remain separately pinned.
- Native contained boundary selection passed 131 tests, including real TCP
  with fresh CLI processes for both direct and file credentials. The file
  path starts with an invalid grant, is explicitly denied, then accepts a
  control-issued renewal through atomic replacement and completes the task.
  Expanded issuance/epoch/binding and ordinary coordinator/client regression
  selection passed 203 tests. Ruff and the install-contract check passed.
- A final file-consumption check refuses non-regular files without opening
  a blocking named pipe and rejects non-ASCII/control bytes before HTTP
  header construction. Its native selection passed 14 tests with the POSIX
  FIFO case skipped on Windows; both real CLI credential modes passed again.
- Fresh Linux issuance/worker/scope selection passed 138 tests, including
  the real FIFO rejection and both native CLI credential modes. This is
  source-contract integration, not the unresolved admitted-container proof.
  Review identified that the file-mode disclosure assertion retained the
  original token; it now checks the renewed credential actually consumed.
- This closes only the issuance/consumption contract, not provider delivery.
  Container placement, per-incarnation file isolation, trusted supervisor
  renewal, real restricted-container proof, central/local projections and
  first-touch provisioning remain open. No installer changed in this slice;
  fresh CLI/coordinator integration was exercised without claiming that it
  proves a fresh container or external deployment.

### 2026-10-14 -- Strict client merged; trusted grant delivery next
- PR #6718 squash-merged as `e9d2d585861976679faec1c2e67c4f369dc0363d`
  after current-head Copilot approval with zero open findings and successful
  required CI. The corrected native worker/TLS selection passed 56; the
  fresh Linux correction selection passed eight. Earlier full scoped-client
  Linux proof passed 89. The source worktree is finalized; #6617 stays open.
- Phase 3's client boundary is resolved, not Phase 3 itself. Next implement
  trusted coordinator grant issuance/renewal and a private renewable worker
  projection, then integrate it with the existing supervisor and admitted
  provider launch. Bind the exact native session before delivering a grant or
  task prompt. Keep signing/fleet authority outside worker containers and
  credentials outside public task/agent metadata and prompts.
- Natural promotion is currently blocked by the already-owned receiving-
  authority fixture repair in #6693/#6696 and #6212: run 38589060318 failed
  the two owning-directory registration tests and the terminal-registration
  fixture, with no other failing job. #6696 remains open at
  `11bbaaaab5824eface3f9269b4b85db8d609995b`; its owner retains the fix,
  including two Medium findings in the latest review overview. No competing
  patch, merge/review bypass, pipeline retry or manual release is being made.
  Continue containerization while that owner clears the normal release gate.

### 2026-10-14 -- Phase 3 strict worker client implemented
- Task-only CLI mode activates before command handlers or coordinator
  autostart/discovery, with an explicit task, worker, URL and credential
  projection. Incomplete projections, mixed fleet credentials, different
  endpoints, other tasks and fleet/history/cleanup commands fail closed.
  Authorized reads and transitions use only the task header; no machine
  identity, credential command, environment proxy or redirect can widen it.
- Native ordinary CLI/client/task-authority regression selection passed 387
  with one platform skip. The final task-worker selection passed 48, including
  a real HTTP coordinator and eight fresh CLI process invocations: own reads,
  progress, heartbeat, structured completion and terminal invalidation, plus
  refusal of global/other-task commands. The fresh Linux candidate passed 89
  task-worker/task-authority/transport contracts, including the real CLI
  process path. Install-contract and touched-code lint pass.
- Documentation impact: the plugin README describes the explicit projection,
  narrower `show`/self-abandon behavior, exact global option spelling and
  server authorization boundary. This closes the scoped-client part of
  `machine-fleet`'s authenticated/scoped/revocable intent, without adding a
  second queue, daemon or credential issuer.
- This is only the client boundary. Trusted supervisor issuance, incarnation-
  bound delivery and renewal, real restricted-container execution, and the
  Phase 4/5 authority/provider proofs remain open. Nothing in this slice
  changes first-use installation or enrolls an operator's live fleet.
- Validation tiers: unit/real-route contracts plus simple end-to-end fresh CLI
  processes on native Windows and in a fresh Linux candidate with a governed
  dependency-feed fixture. No installer/first-provision path changed, so this
  slice does not claim that clean-room enrollment gate. A live fleet/provider
  execution is not exercised by a client-only slice; it remains a required
  campaign gate rather than being treated as satisfied by a generic image.
- Review/CI corrections: the first complete CI run found one old TLS test
  patching the factory's former module. Its patch now follows the extraction
  and still asserts actual HTTP/HTTPS verification arguments. Review also
  caught URL userinfo injecting Basic auth into direct Python scoped clients;
  shared endpoint validation now rejects it before constructing a transport.
  Missing/unreadable completion-result files report local validation errors
  without sending a request. The corrected native selection passes 56,
  including all worker contracts and the existing TLS regression.

### 2026-10-14 -- Phase 2 merged; restricted-worker delivery next
- PR #6698 squash-merged as `5e2f85636cc9d3e0f47965e71ccf0c041d27faf2`
  after current-head Copilot approval with zero open findings and required CI
  success. The umbrella #6617 remains open. Promotion stays automatic; no
  manual promotion or operator service rollout was triggered.
- The final production-route burst and admission corrections passed six
  targeted native contracts and the same six in a fresh Linux container.
  Earlier native full delivery/cutover selection passed 141; the preceding
  fresh Linux cutover selection passed 140 and credential follow-up passed 12.
  All proofs name their actual snapshots; none claim fresh fleet enrollment.
- Phase 2 Plan items are resolved. The campaign remains Active: Phase 3 must
  deliver task-only credentials through the trusted supervisor, pin restricted
  worker CLI calls before discovery/health/autostart or broad-token lookup,
  renew grants without disclosing issuer authority, and prove own-task access
  plus denial of other tasks in a real container. Phase 4 local projections
  and Phase 5 provider-backed container execution remain open.
- Continue in a fresh phase worktree. First implement the strict task-scoped
  client/CLI boundary, then integrate trusted credential delivery and renewal
  with existing admitted launch/provider seams; do not create another daemon
  or replace the fleet/queue owners.

### 2026-10-14 -- Phase 2 push-first intake implemented
- Reused `/events` and its ready barrier; added a cancelable async reader with
  bounded reads and comment keepalives. Its bounded dirty bit wakes a single
  existing intake, never another claim authority. Ready/reconnect always
  reconciles; 60-second polling remains the missed-event/disconnect backstop.
- Presence runs independently of intake discovery/spawn. Gate, shared URL and
  credential changes retire both owned threads before releasing the client;
  a still-running bounded intake defers replacement instead of racing it.
  Local serving-coordinator health reports connection and intake failure state.
- Native delivery selection: 189 passed; broader coordinator, board relay,
  MCP, client transport and intake selection: 340 passed. Final focused
  delivery/event run: 61 passed, including case-insensitive affinity,
  credential forwarding, finite timing, exponential backoff across repeated
  ready/EOF failures, partial thread-start and repeated-stop cleanup.
  Fresh Linux container: 59 delivery/event tests passed, including real TCP.
  The later minimum-Python compatibility fix passes the native 61-test run
  plus a built-candidate Python 3.10.20 keepalive/ready/cancellation exercise.
  Required CI remains a gate on the published head.
- Validation tiers: unit contracts plus simple end-to-end TCP enqueue, capacity
  release, startup/reconnect gaps, cancellation and latency below 1.5 seconds.
  The fresh Linux run used an explicit governed dependency-feed fixture and
  the contained test runner; it does not claim plugin first-use installation,
  independent-database migration or live fleet/container enrollment. Those
  acceptance proofs remain in later phases. No operator service was changed.
- Documentation impact: the plugin README documents polling/stream timings,
  compatibility fallback, independent presence, health and retiring ownership.
  This closes the reviewed delivery slice of the existing fleet vision;
  it does not extend standing intent or complete the fleet-authority effort.
- The first PR CI guard caught a real compatibility defect: keepalive caught
  only builtin `TimeoutError`, and the initial ready wait used the Python
  3.11-only `asyncio.timeout` despite the runtime's Python 3.10 minimum.
  Added the distinct legacy asyncio exception, used `wait_for` for the barrier,
  added portable regressions for both, and exercised the built candidate on
  actual Python 3.10.20. The CI failure was fixed, not waived or retried away.
- Cutover self-check found an actual lifecycle gap, not just a missing PR
  statement: outbound intake was neither route-gated nor counted by drain.
  Federation now admits directory/intake work only for the active, non-
  draining generation and tracks admitted work through the synchronous spawn
  boundary. Self-retirement closes admission first; demoted teardown does not
  withdraw the successor's stable directory identity. `/claim` rechecks drain
  inside its counted boundary, eliminating the same check/enter race.
- Native assignment/federation/lifecycle/self-retirement selection: 131
  passed, including real routing-table passive/promotion/demotion and a real
  HTTP drain staying busy during an admitted blocking launch. The TCP
  read-timeout test uses a 500ms bound (under 2s observed), avoiding a 50ms
  first-response scheduling race on Windows while still proving quiet reads
  terminate. Earlier 61/59 counts identify the native/Linux delivery-only
  snapshots above; they are not the enlarged cutover selection.
- Fresh Linux final cutover/events selection at `398594942`: 140 passed.
  A subsequent review found credential commands executing in the per-event
  enabled predicate. Moved resolution exclusively to the owning federation
  tick; child readers use the pinned cached credential, and owner-driven
  rotation still retires both old threads before replacement. A 10,000-event
  burst regression asserts command fetches run only on that owner tick.
  Native final assignment/events/federation/cutover selection: 141 passed.
- Linux credential/ready/retirement/events follow-up at `558df321a`: 12
  passed. A further review found route-table reads in the same event
  predicate. Readers now use cached route ownership; owner ticks and actual
  intake admission still refresh the live route. The burst regression uses
  the production server routing predicate and bounds filesystem reads while
  proving a post-flip intake is rejected before its next owner tick.
  Native production-route burst/admission/cutover regression: 6 passed;
  the other 140 selection tests passed before the route-spy fixture's keyword
  forwarding correction. Required CI gates the final published snapshot.

### 2026-10-14 -- Corrective task-authority PR merged; assignment push next
- PR #6665 merged as `b8d3ec6a710beb376ef655e5a715e7055b783b93` after
  current-head Copilot approval and required CI. Both review findings were
  fixed and resolved, including the reproduced terminal-result backfill race.
  Its worktree was finalized; Phase 2 uses a new isolated worktree.
- The campaign remains Active. Next is outbound coordinator SSE assignment
  notification, ready/reconnect reconciliation and slower poll recovery;
  restricted credential delivery, local projections and container placement
  still require their own implementation and end-to-end proof.

### 2026-10-13 -- Check-in and corrective authorization slice
- Operator requested a check-in before continuing. The campaign remains
  active; merged token helpers are not completion of fleet federation.
- A regression test proved a narrow task token could list `/tasks` globally
  (200 instead of 403): global authentication accepted it while unrelated
  routes had no scoped authorization. Added a deny-by-default route table,
  with own-task reads and four owned lifecycle verbs only. Scoped headers
  never downgrade to ambient bearer or unauthenticated local authority.
- Added task generation to the signed grant and generation/session comparisons
  inside progress, heartbeat, completion and abandonment write transactions.
  Token parsing rejects malformed signed shapes, non-finite expiry and invalid
  encoding explicitly. Restricted workers cannot call start to renew a grant.
- HTTP tests cover every unlisted coordinator route, cross-task reads,
  mixed credentials, no-token local configuration and replacement-session
  races. Queue progress/heartbeat methods moved into `queue_progress.py` to
  preserve the 1000-line source cap without changing trusted-call behavior.
- Corrected earlier overclaims: directory discovery does not supply replicated
  task data or durable writer fencing. Local standby against an independent
  SQLite database is not a safe central-authority migration.
- Found existing SSE, EventBus and ready-frame support while preparing the next
  slice. Phase 2 will reuse that real push transport, with slower reconciliation
  as a backstop, rather than introduce another long-poll protocol.
- Earlier proxy `status` exercises did not prove plugin first-use provision
  or offline fallback; that bootstrap validation remains open despite the
  sibling effort's earlier checkbox claim.
- Validation: 39 focused native tests pass, including a real loopback HTTP
  supervisor/worker round-trip. A fresh Linux container passed 37 scoped-token
  tests with an explicit governed-feed fixture after the default dependency
  fetch failed on TLS. The two additional mounted/documentation-surface checks
  added after that snapshot pass natively. Full affected native selection:
  389 passed, one platform skip. No live fleet enrollment, data migration or
  operator service change was exercised or authorized by this slice.
- Two earlier broad native runs timed out in the Windows reconcile completion
  fixture; its focused run and subsequent diagnostic broad run passed. Root
  cause remains unestablished, tracked in #6662. Added bounded synthetic
  failure evidence without increasing waits or skipping the test. Required CI
  remains a gate, not a waived check.
- Documentation impact: `plugins/agent-dispatch/docs/task-authority.md` records
  exact routes, authenticated-ingress prerequisites, transactional fences and
  deployment limitations. This is a below-altitude correction to the reviewed
  task-authority boundary, not new standing intent.
- PR #6665 review identified a remaining terminal-result backfill race: the
  completion retry branch wrote a non-null result before the incarnation
  check. Reproduced both completed and submitted cases, moved the check ahead
  of all completion mutations, and verified the completion/token selection:
  174 passed. Replaced superseded long-poll and unsafe standby checklists with
  one executable SSE/projection plan rather than leave contradictory notes.

### 2026-10-13 -- Kickoff + research pass
- Operator directive: build containerization support for agent-dispatch
  "like the index service" -- centralized queue, federated local
  coordinators, projected relay for untrusted containers with valid-
  state-transition enforcement.
- Before drafting anything, ran a dedicated research pass (background
  `research` sub-agent) over `plugins/agent-dispatch/src/agent_dispatch/`'s
  federation/fleet/satellite modules (`federation.py`, `federation_runner.py`,
  `satellites.py`, `satellite_work_intake.py`, `fleet.py`, `lease.py`,
  `coordinator_auth.py`, `coordinator_tasks.py`, `task_state_machine.py`,
  `queue_lifecycle.py`), `services/dispatch/` (README + Dockerfile),
  `visions/machine-fleet/README.md`, and bounded git-history queries for
  the "agent-dispatch-federation"/"satellite-agent-exposure" effort names
  referenced in code docstrings (concluded: those efforts' planning docs
  were never committed to this repo as `efforts/*` folders -- only their
  shipped code + PR commit messages survive; not a dangling-reference bug,
  just planning that happened outside this repo's git history).
- Headline finding: **federation, coordinator-discovery-via-fenced-epoch-
  lease, the satellite outbound-only leaf role, valid-state-transition
  enforcement, and a container-runnable centralized `services/dispatch`
  distribution are already fully shipped** -- this request's shape is
  largely already-declared target state in `visions/machine-fleet`, not
  a greenfield design. The real, precise gaps are: (1) no connection-bound
  per-worker/per-task authorization (any shared-bearer holder can assert
  any `worker_id`), (2) `FencingGuard` is implemented but never wired to
  the HTTP write routes, (3) no container/DevBox-aware fleet host
  selection, (4) no push/subscribe transport (recommend NOT building one
  -- the existing poll model already satisfies the ask), (5) "centralized
  with subscribed supervisors" is achievable today as a pure configuration
  choice (pin every other machine's federation role to `peer`/`satellite`
  against one shared `AGENT_DISPATCH_SHARED_URL`), not new code.
- Scoped this effort to exactly those gaps (Phases 1-4 above) rather than
  re-architecting anything already shipped. Also updated the sibling
  `agent-dispatch-standalone-container` effort's Journal/checkboxes with
  today's earlier, unrelated public-install-surface-indirection work
  (PR #6559) and its same-day clean-room validation, since that effort's
  own open Validation Plan item ("real end-to-end exercise of the network
  fetch path") was satisfied by that work.
- Next: operator review of this draft (Phase 0's remaining checkbox)
  before starting Phase 1 implementation.

### 2026-10-13 -- Operator review: scope confirmed, one addition
- Presented the gap analysis + proposed Phase 1-4 plan to the operator
  directly (not merely implied by proceeding) and asked three explicit
  questions, since this introduces a new security-sensitive authorization
  mechanism: (a) does the 5-gap scope look right, (b) Phase 2 (relay)
  approach -- direct scoped-token calls vs. a stricter local reverse-proxy
  from day one, (c) is container/DevBox-aware fleet spawn in scope now or
  a deferred follow-on.
- Operator answers:
  - Scope: **mostly right, with one change** -- build push notification
    with the existing poll as a **long-interval backstop**, not poll-only
    as this doc originally recommended ("push can help improve throughput
    and avoid over-polling; I always want to try push"). Added as a new
    Phase 2 (long-poll `await-assignment` endpoint, reusing the existing
    `queue_run_waiters.py` durable-waiter + wake-on-commit idiom; outbound-
    only from the supervisor's side so it doesn't reopen the satellite
    role's no-inbound-listener contract the way a webhook would have).
    Renumbered: old Phase 2 (relay) -> Phase 3, old Phase 3
    (centralization) -> Phase 4, old Phase 4 (container/DevBox fleet
    spawn) -> Phase 5.
  - Phase 3 (relay) approach: **direct** scoped-token calls, confirmed --
    start simple; the proxy option stays documented as a future option,
    not built now.
  - Phase 5 (container/DevBox-aware fleet spawn): **in scope now**,
    alongside Phases 1-4, not deferred. Recorded a likely sequencing
    (container-exec host kind first, Dev Box control-plane API as a
    follow-on slice) since this phase has zero existing precedent to
    extend.
- Updated this doc's Status, gap-analysis item 4, Plan (renumbered +
  Phase-2 content added), and Validation Plan to reflect all three
  decisions before starting any implementation.
- Next: open the umbrella issue, then start Phase 1 (task-scoped
  capability tokens + wiring `FencingGuard`) -- the foundational
  authorization layer every later phase (push-waiter auth, the relay,
  centralization's fencing guarantee) builds on.

### 2026-10-13 -- Phase 1 landed: task-scoped capability tokens + fencing guard
- New module `plugins/agent-dispatch/src/agent_dispatch/task_scope.py`:
  `mint_task_scope_token`/`verify_task_scope_token`/`TaskScopeClaims`/
  `TaskScopeTokenError`, HMAC-signed (keyed off `control_token`, mirroring
  `coordinator_auth.scoped_control_token`), self-contained (no durable
  row), with a defense-in-depth expiry (`DEFAULT_TTL_SECONDS = 900`)
  independent of the real invalidation mechanism (task-state recheck).
- `coordinator_auth._make_auth` now accepts the token via a distinct
  `X-Agent-Dispatch-Task-Token` header as an alternate credential to the
  shared/control bearer, storing verified claims on `request.state.
  task_scope` without itself deciding authorization -- that's
  `coordinator_tasks._resolve_task_authority`'s job per-route: re-check the
  claims against the task's CURRENT `owner`/`owner_session_id`, and the
  embedded fencing epoch against a real `FencingGuard` (now constructed in
  `create_app` directly from the coordinator's own in-process
  `FleetDirectory` -- no network hop; an unregistered/non-federated
  directory reports epoch 0, so this is a no-op for every coordinator that
  isn't actually federating today).
- Wired onto `start` (mints the token in its response once ownership +
  `owner_session_id` are established), `progress`, `complete`, `abandon`,
  `heartbeat`. Deliberately did NOT wire `yield`/`suspend`/`resume`/
  `release`/`set_card` in this slice -- `resume` especially has
  non-trivial `adopt_session`/`adopt_owner_session_id` branching that
  deserves its own careful pass, not a mechanical copy-paste; recorded as
  an explicit Phase-1 follow-up in the Plan above, not silently dropped.
- Found and fixed one subtlety while implementing `start`'s minting: must
  mint from the **post-mutation** `owner_session_id` (`result.get(
  "owner_session_id")`), not the pre-computed local variable -- `queue.
  start()` leaves the DB's `owner_session_id` unchanged when the caller
  passes `None`, so minting from the stale local variable could bind a
  token to a value the row never actually has.
- Tests: `tests/test_task_scope.py` (9 pure unit tests: round-trip,
  wrong-key rejection, tampered-payload rejection, malformed-token
  rejection, expiry, distinct-task non-collision) + `tests/
  test_task_scope_auth.py` (9 real-coordinator HTTP tests: mint-on-start,
  scoped-token-authenticates-with-no-shared-bearer-at-all, no-credential-
  rejected, cross-task rejection, body-worker_id-mismatch-is-overridden-
  not-trusted, reassignment invalidates the old token, forged-signature
  rejection, no-control-token-means-no-mint, fencing-guard rejects a token
  minted under a since-superseded epoch) -- 18/18 passing. Re-ran the
  existing `coordinator`-filtered suite (247 passed) and the broader
  `lease`/`federation`/`satellite`/`fencing`-filtered suite (403 passed, 1
  pre-existing unrelated skip) to confirm zero regression to the unchanged
  full-bearer path. `ruff check` clean on every touched/new file (two
  pre-existing `E501`s in `coordinator_tasks.py` untouched by this diff,
  confirmed via `git diff` -- not this change's to fix).
- `check-version-consistency.py` and `check-changefile-presence.py --base
  origin/dev` both pass; changefile added (`agent-dispatch`, minor --
  additive, backward-compatible: a caller presenting no task-scoped header
  behaves exactly as before).
- **CI's `check-module-size.py` caught `coordinator_tasks.py` exceeding its
  1000-line cap** (it was already at 997/1000 on `dev` -- this change's
  ~70 net new lines pushed it over). Split, not trimmed: extracted the
  ~20 Pydantic request-body models (`WorkerBody`, `ClaimBody`,
  `CompleteBody`, etc. -- pure data shapes, no route logic) into a new
  `coordinator_task_models.py`, and the two new authorization helpers
  (`resolve_task_authority`/`mint_task_token`) into `task_authority.py`
  (both already designed as free functions taking `queue`/`fencing_guard`/
  `control_token` explicitly, so the extraction needed no behavior change,
  only updated imports). `coordinator_tasks.py` is now 822 lines -- comfortably
  under cap with real margin for the Phase 2-5 routes still to come.
  Re-ran the full test/lint/guard sweep after the split: 371 tests collect,
  265 pass (247 coordinator + 18 task_scope), `ruff check` clean on every
  touched file, `check-module-size.py` now OK.
- Next: open a PR for Phase 1, then start Phase 2 (long-poll
  `await-assignment` waiter with poll backstop).
