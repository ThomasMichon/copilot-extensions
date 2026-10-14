# agent-dispatch: fleet authority -- centralized queue, federated local
# coordinators, and a projected relay for untrusted containers

- **Slug:** `agent-dispatch-fleet-authority`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-phase PRs against `dev`
- **Created:** 2026-10-13
- **Status:** Draft -- operator reviewed 2026-10-13: scope confirmed with one
  addition (push notification with a long-interval poll backstop, not
  poll-only); Phase 2 (projected relay) starts with direct scoped-token
  calls; container/DevBox-aware fleet host selection is in scope now, not
  deferred. Proceeding to Phase 1 implementation.
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
4. **No push/subscribe transport.** Today's only work-acquisition patterns
   are: spawn-locally, `FleetSpawner`-push-via-SSH-to-a-host-it-selects, or
   satellite-polls-its-own-affinitied-work. There is no "the coordinator
   pushes a task notification to a subscribed supervisor" channel --
   everything is poll/heartbeat-driven. **Operator decision (2026-10-13):
   build push, with the existing poll as a long-interval backstop** (not
   poll-only) -- throughput and avoiding over-polling matter. A **webhook**
   (coordinator calls back into the supervisor) is the wrong shape here: it
   would require the satellite role's deliberately **outbound-only, no-
   inbound-listener** contract (`federation_runner.py:156-180`,
   `satellite_work_intake.py:1-24`) to grow an inbound port, contradicting
   that effort's own stated security posture. The right shape is an
   **outbound long-poll** -- the supervisor still only ever makes outbound
   HTTP requests, preserving the no-inbound-listener invariant -- that
   blocks server-side until a new affinitied task appears or a bounded
   timeout elapses, then is immediately re-issued. This mirrors an existing
   architectural idiom already in this codebase: `queue_run_waiters.py`'s
   durable waiter-row + wake-on-commit pattern (via
   `queue_notifications.QueueNotificationMixin`, already used for detached-
   run resume) -- a new "assignment waiter" is the same idiom applied to "a
   queued task matching my affinity appeared" instead of "task X reached a
   terminal state." See Phase 2.
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
This is the security-critical core of the "projected relay": binding a
caller's asserted `worker_id`/task identity to something the server
actually verifies, reusing the existing CAS/epoch fields rather than
inventing new durable state.

- [ ] Add a **task-scoped capability token**: an HMAC derived from the
  coordinator's `control_token` (mirroring the existing
  `coordinator_auth.scoped_control_token` pattern) over
  `(task_id, worker_id, owner_session_id, coordinator_epoch, exp)`. Minted
  by the coordinator at the moment a task is assigned/claimed for a given
  `worker_id` (claim-confirm and satellite/fleet-push assignment paths).
  No new durable row: verification recomputes the HMAC from the task's
  **current** DB state, so a token naturally invalidates the instant the
  task's owner/session/epoch changes (reassignment, completion, fencing
  takeover) -- no revocation list needed.
- [ ] Add a FastAPI dependency (new module, `task_scope_auth.py`, or a
  function alongside `coordinator_auth.py`) that, when a request presents
  a task-scoped token (a distinct header, e.g.
  `X-Agent-Dispatch-Task-Token`, rather than the shared bearer), derives
  `worker_id`/`owner_session_id` **from the verified token** and ignores/
  rejects any mismatching value in the request body -- closing the
  caller-can-assert-any-worker_id gap named above. The shared bearer
  remains valid for trusted callers (supervisors, the operator CLI); the
  task-scoped token is the **narrower** credential a projected relay hands
  to a container.
- [ ] Wire `FencingGuard.check()` onto the task-mutation routes in
  `coordinator_tasks.py` (claim-confirm, progress, complete, fail, resume)
  using the epoch carried in the request's fencing context -- closing the
  declared-but-unattached gap in `lease.py`. A write from a writer whose
  epoch has been superseded is now actually rejected at the HTTP layer,
  not just theoretically fenceable.
- [ ] Unit tests: token mint/verify round-trip; token invalidated by
  reassignment/completion/epoch bump; a caller-asserted `worker_id`
  mismatching a presented token's bound identity is rejected; a stale-
  epoch write is rejected post-fencing.

### Phase 2 -- Push assignment notification with poll backstop
Operator decision: try push for throughput, keep poll as a long-interval
backstop (not poll-only). Builds on the `queue_run_waiters.py` durable-
waiter + wake-on-commit idiom already in this codebase (used today for
detached-run resume), applied to a new predicate.

- [ ] Add an **assignment waiter**: a new coordinator endpoint (e.g.
  `GET /tasks/await-assignment`) that long-polls -- blocks server-side,
  bounded by a request timeout (e.g. 30-60s), until a `queued` task
  matching the caller's affinity (`target_machine`, satellite/peer
  identity) appears, then returns it immediately; on timeout it returns an
  empty result and the caller re-issues. This is **outbound-only from the
  supervisor's perspective** -- it never opens an inbound listener on the
  satellite/peer, preserving the existing no-inbound-listener contract
  (`federation_runner.py`'s satellite gate) a webhook would have broken.
- [ ] Wire the wake side through the existing
  `queue_notifications.QueueNotificationMixin` idiom: a new queue-commit
  notifier fires when a task is enqueued/reassigned with a `target_machine`
  affinity, releasing any blocked `await-assignment` call(s) matching that
  affinity (mirroring how `run_waiters` already wakes on commit for a
  different predicate).
- [ ] Update `SatelliteWorkIntake`/the ordinary peer work-intake loop to
  call `await-assignment` as its primary signal, falling back to today's
  periodic poll at a **longer** interval than today's default as a
  backstop -- covers connection drops, coordinator restarts, and anything
  the long-poll itself misses. Keep the existing poll path's code
  unchanged (same affinity read, `satellite_work_intake.py:195-225`) --
  only the trigger cadence/source changes.
- [ ] Decide bounded concurrency/fairness: a long-poll holds a server
  worker/connection for its timeout window; cap concurrent waiters (one
  per distinct target_machine/affinity is the expected shape for this
  fleet size) and document the limit.
- [ ] Unit tests: a waiter registered before a matching task is enqueued
  wakes promptly; one registered with no matching task times out cleanly
  and the poll backstop still finds the task on its next cycle; multiple
  waiters with different affinities don't cross-wake each other.

### Phase 3 -- Projected relay component
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

### Phase 4 -- Centralization deployment pattern (configuration, not new code)
- [ ] Document (in `services/dispatch/README.md` and/or
  `docs/patterns/`) the recommended fleet-centralization shape: one
  `services/dispatch` instance (container or native) as the pinned
  `AGENT_DISPATCH_SHARED_URL`; every other machine's agent-dispatch
  configured `peer` or `satellite` only (never `coordinator`/`standby`)
  against that URL. This requires **no new code** -- `config.
  FEDERATION_ROLES` already supports exactly this as a config value.
- [ ] For a machine that genuinely needs to keep running its own local
  coordinator (e.g. offline-capable), document the federation reading of
  "syncing/projecting data to ensure authority is clear": configure it as
  a `standby`-eligible peer against the shared URL so the fenced-epoch
  lease (already shipped) is the actual authority arbiter, and ensure
  Phase 1's fencing-guard wiring means a fenced-out local coordinator's
  stale writes are genuinely rejected, not merely directory-invisible.
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
- [ ] Phase 1: unit tests for token mint/verify/invalidation + fencing-guard
  rejection, run against the real `coordinator_tasks.py` routes (not just
  the token/fencing library functions in isolation).
- [ ] Phase 2: a long-poll waiter wakes promptly on a matching enqueue, and
  the poll backstop still finds work if the wake is missed -- proven with
  a real timing test, not just a mocked notifier call.
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
