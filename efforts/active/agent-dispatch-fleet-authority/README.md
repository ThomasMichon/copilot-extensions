# agent-dispatch: fleet authority -- centralized queue, federated local
# coordinators, and a projected relay for untrusted containers

- **Slug:** `agent-dispatch-fleet-authority`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-phase PRs against `dev`
- **Created:** 2026-10-13
- **Status:** Active. Design merged in #6618; initial task grants merged in
  #6630. Check-in found missing deny-by-default route authorization and
  transactional incarnation fences; fixing those before assignment push.
  Push-first delivery, direct restricted-worker calls, container placement
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

- [ ] Subscribe outbound to coordinator SSE and reconcile the authoritative
  queue after each ready frame, including reconnect, so pre-subscription work
  cannot fall into a gap.
- [ ] Coalesce relevant enqueue, assignment and capacity-release notifications
  by machine/repository affinity; wake the existing intake/supervision loop
  without making the event stream another claim authority.
- [ ] Keep slower periodic reconciliation for missed events and disconnected
  streams. Bound reconnect/backoff and report degraded subscription health.
  Presence heartbeats remain independent of work-intake blocking.
- [ ] Bound each subscription's queue, read/keepalive timing and shutdown.
  Disable or repoint subscriptions with their owning role/configuration.
- [ ] Test actual enqueue-to-wake latency, pre-ready and reconnect gaps, event
  coalescing, cross-affinity isolation, missed-event poll recovery and teardown.

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
- [ ] Phase 2: real SSE enqueue-to-wake timing, ready/reconnect reconciliation,
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
