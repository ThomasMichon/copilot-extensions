# Persistent SSH Carrier and Push Supervision

- **Slug:** `persistent-ssh-carrier`
- **Repo:** copilot-extensions
- **Branch(es):** proposal PR followed by serial per-phase implementation PRs
- **Created:** 2026-09-02
- **Status:** Active
- **Vision:** closes
  [`visions/plugins/agent-dispatch`](../../../visions/plugins/agent-dispatch/README.md)
  §Behaviors/`react-to-turn-end` and advances
  [`visions/plugins/agent-bridge`](../../../visions/plugins/agent-bridge/README.md)
  §Features/`attention-boundary-subscriptions` and
  `cursor-stable-event-replay`
- **Umbrella issue:** #1763
- **Sub-issues:** #1777 (carrier foundation) · #1778 (remote Bridge
  operations) · #1779 (Dispatch event wake) · #1780 (command migration and
  deployed evidence)

## Guiding Intent

Make remote agent supervision genuinely event-driven without giving the
supervisor its own transport pool. Agent Bridge owns one persistent,
reconnecting SSH carrier for each remote host identity and multiplexes exact
session event subscriptions and bridge command requests over it. Agent Dispatch
subscribes through Agent Bridge's public process/service boundary, wakes
promptly on durable turn and lifecycle events, and retains ordinary periodic
reconciliation only as the correctness floor.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Carrier driver | Agent Bridge carrier protocol, host connection ownership, remote operations, tests, and landing | Serial isolated worktrees |
| Supervision driver | Agent Dispatch subscription lifecycle, wake integration, fallback, and runtime evidence | Serial isolated worktrees after the carrier contract lands |
| Attention-waits lane | General caller-facing attention projection and settlement semantics | #1450 and `agent-bridge-attention-waits` |

## Coordination

- **Topology:** one reviewed proposal followed by serial Agent Bridge and Agent
  Dispatch slices; one carrier/protocol writer at a time.
- **Host (owns PRs):** carrier driver.
- **Delegates:** the attention-waits lane owns general caller attention
  semantics. This effort owns remote host transport, exact-session event
  delivery to supervisors, and the Dispatch wake consumer; it consumes existing
  durable event identities rather than defining a competing event vocabulary
  or a second caller-facing wait command.
- **Handoff:** each merged phase updates this effort before the next phase
  changes a protocol writer or deployed supervisor behavior.

## Context

Agent Dispatch previously accelerated supervision by sampling every embodied
owner's derived state every two seconds. Remote owners made each sample a fresh
SSH command, so several supervisor lanes could continuously consume CPU and
churn SSH processes even when no lifecycle transition occurred. #1763 removed
that short reactive loop and restored fixed-interval reconciliation as immediate
containment.

Agent Bridge already owns durable per-session event logs, SSE replay from stable
event IDs, caller delivery cursors, reconnecting session hosts, and the shared
`ssh-manager` library. On POSIX, `ssh-manager` can reuse OpenSSH ControlMaster;
native Windows currently falls back to one direct SSH process per operation.
Dedicated endpoint forwards also remain intentionally independent processes.
None of those existing shapes supplies the cross-platform, daemon-owned,
multi-request carrier required by remote supervision.

The repository's ownership and independence patterns require:

- Agent Bridge, not Agent Dispatch, owns remote SSH lifecycle and host identity.
- Dispatch communicates through a stable CLI or authenticated local service
  contract and never imports Bridge runtime internals.
- A missing or incompatible Bridge darkens prompt turn-end acceleration but
  does not break periodic supervision.
- Durable event/cursor authority remains in the hosting Bridge. The carrier
  transports requests and replayable events; it does not create a second event
  ledger or cursor store.
- Local, remote, reconnecting, and mixed-version behavior fail or degrade before
  authoritative side effects.

## Request

> Replace supervisor SSH turn polling with push events and a shared carrier.

## Plan

### Phase 1 — Review the carrier and ownership contract

- [x] Land this proposal before implementation.
- [x] Fix the ownership boundary: one Agent Bridge daemon owns one carrier per
      normalized SSH connection identity; Dispatch owns subscriptions and wake
      policy, never SSH processes.
- [x] Fix the protocol boundary: a versioned framed stdio protocol over one
      long-lived `ssh <host> agent-bridge carrier --stdio` process supports
      concurrent request/response operations and event subscriptions without
      exposing Bridge Python internals.
- [x] Keep this contract distinct from #1450: that effort defines general
      attention settlement, while this effort transports exact durable session
      events and wakes Dispatch to reconcile.

### Phase 2 — Add the persistent host carrier (#1777)

- [x] Extend `ssh-manager`'s existing connection ownership rather than adding a
      second host registry. Key the carrier by the complete normalized SSH
      connection identity, not a display alias alone.
- [x] Add a small versioned hello plus length-prefixed binary request, response,
      event, heartbeat, cancellation, and error envelopes with bounded frame
      sizes and request IDs.
- [x] Start one remote `agent-bridge carrier --stdio` endpoint through
      `ssh-manager.open_stdio_channel`; multiplex concurrent logical operations
      over that process on every platform, including native Windows. Resolve and
      quote the remote entrypoint through a cross-platform helper rather than
      POSIX-only shell quoting.
- [x] Reconnect with bounded backoff after transport loss, fail pending
      non-replayable requests explicitly, and let replayable event subscribers
      resume from their last durable event position.
- [x] Emit protocol heartbeats and mark a carrier degraded when its heartbeat or
      a subscription's progress deadline expires, even if the SSH process has
      not exited.
- [x] Bound per-request queues and total buffered output so one slow subscriber
      cannot stall unrelated requests or grow memory without limit.
- [x] Make the remote endpoint exit on stdin EOF or parent-carrier loss. Retire
      the local carrier after a bounded idle period with no requests or
      subscriptions, and reap both process trees deterministically on daemon
      shutdown or cutover.
- [x] Expose carrier health and active request/subscription counts through
      Agent Bridge diagnostics without logging prompts, event payloads, tokens,
      or SSH secrets.

### Phase 3 — Expose narrow remote Bridge operations (#1778)

- [x] Implement only the operations required by existing remote Bridge
      consumers first: exact session status, live-session resolution, and
      cursor-based session event subscription.
- [x] Preserve the hosting Bridge's session IDs, event IDs, event names, and
      replay semantics exactly; the local daemon is a transport proxy, not a
      second authority.
- [x] Add an authenticated local Agent Bridge API/CLI surface for remote
      operations so clients identify a host and exact session without learning
      carrier state or SSH arguments.
- [x] Give every supervisor lane a distinct, stable `caller_id` propagated to
      the hosting Bridge. A supervision cursor must never reuse or advance an
      operator, attention-wait, or other consumer's delivery cursor.
- [x] Report event-log rebuild, cursor invalidation, or any detected replay gap
      as an explicit control envelope that immediately wakes full
      reconciliation; never translate a stale cursor into a quiet empty stream.
- [x] Make unsupported protocol versions and operations fail before opening a
      subscription or issuing a remote command.
- [x] Re-establish remote operations and subscriptions from the last
      acknowledged cursor after either a remote carrier reconnect or a local
      Agent Bridge daemon restart/zero-downtime cutover.
- [x] Retain existing direct transport as a bounded compatibility fallback only
      for operations not yet migrated; never run a parallel direct event poll.

### Phase 4 — Wake Agent Dispatch from pushed lifecycle events (#1779)

- [x] Add one long-lived local Agent Bridge subscription client per supervisor
      process, multiplexing every reservation for that lane. Never spawn one CLI
      child or HTTP stream per reservation.
- [x] Key subscriptions by the reservation's exact Bridge host/session identity
      and stable lane-specific caller identity.
- [x] Subscribe to existing durable lifecycle boundaries including
      `session_state_changed`, `assistant.turn_end`, shutdown, handoff, and
      terminal events; reconnect from the acknowledged cursor without losing an
      idle or completion boundary.
- [x] Coalesce bursts into a supervisor wake signal and run the ordinary
      reconciliation pass; event delivery changes latency, never correctness or
      lifecycle authority.
- [x] Treat heartbeat expiry, cursor invalidation, and a local subscription
      client exit as degraded event acceleration: wake one immediate full pass,
      report degraded health, reconnect with backoff, and continue the ordinary
      interval without tight retry polling.
- [x] Add and remove subscriptions as reservations are spawned, rebound,
      suspended, completed, abandoned, or proven gone.
- [x] Degrade to exactly the configured periodic interval when Agent Bridge,
      the host carrier, the remote session, or the selected protocol is
      unavailable.

### Phase 5 — Consolidate remote Bridge command traffic (#1780)

- [x] Route current remote Agent Bridge status, live-session resolve, create,
      stop/end, and nudge operations through the local Bridge remote-operation
      contract where their semantics are supported.
- [x] Remove the corresponding raw `ssh` call sites from Agent Dispatch only
      after equivalent timeout, tri-state liveness, output, and failure behavior
      is covered.
- [x] Keep bounded raw SSH as the standalone compatibility path when the
      optional local Agent Bridge capability is absent. It must never run beside
      a carrier-backed event subscription or become a short-interval fallback.
- [x] Keep non-Bridge arbitrary shell work outside the initial protocol; add a
      generic execution operation only if a concrete caller needs it and its
      authorization, cancellation, and output bounds are reviewed.
- [ ] Prove all event and migrated command traffic to one host shares the same
      carrier process while independent hosts remain isolated.

### Phase 6 — Deploy, measure, and document

- [ ] Update Agent Bridge and Agent Dispatch architecture/CLI documentation with
      carrier ownership, protocol negotiation, cursor recovery, and fallback.
- [ ] Deploy merged versions through the unified updater without stopping
      healthy supervisors as an emergency workaround.
- [ ] Measure at least one full supervisor interval with all configured lanes:
      aggregate/per-process CPU, persistent and child SSH process counts,
      distinct SSH PIDs, console creation, free memory, event-to-wake latency,
      and reconnect behavior.
- [ ] Meet concrete steady-state targets: aggregate idle supervisor CPU at or
      below 5% of one logical core; exactly one healthy carrier SSH PID per
      actively observed host; no new carrier PID during a ten-minute
      failure-free watch; zero visible console creation; and p95 durable-event
      to reconciliation-start latency at or below two seconds.
- [ ] Close #1763 only after the primary wake path is pushed and cursor-based,
      SSH outreach is bounded by the shared carrier, and periodic reconciliation
      remains the sole fallback.

## Validation Plan

- [x] Carrier unit tests cover framing, concurrent request correlation,
      cancellation, bounded queues, malformed/oversized frames, clean shutdown,
      child failure, heartbeat/staleness expiry, reconnect backoff, remote
      exit-on-EOF, local idle teardown, and protocol mismatch.
- [ ] Cross-platform process tests prove one carrier process per connection
      identity on Windows and POSIX and no duplicate carrier under concurrent
      first use.
- [ ] Event tests prove idle, turn-end, shutdown, terminal, and handoff
      boundaries survive disconnect/reconnect and replay exactly from the
      consumer's distinct durable cursor. Log rebuild and cursor invalidation
      produce an explicit gap signal and immediate reconciliation.
- [x] Dispatch tests prove event bursts coalesce to prompt reconciliation,
      subscription removal follows reservation lifecycle, and missing or stale
      Bridge capability falls back to one ordinary interval without tight
      polling.
- [x] Dispatch process tests prove local subscription clients are O(supervisor
      lanes), not O(active reservations), and recover across a local Agent
      Bridge daemon cutover without stalling supervision.
- [x] Migration tests prove each replaced raw SSH operation retains its prior
      timeout and tri-state safety behavior before the old path is removed.
- [x] Mixed-version tests cover new local/old remote, old local/new remote,
      unsupported operations, and reconnect during a runtime cutover.
- [x] `python tools/run-plugin-tests.py agent-bridge --max-processes 32
      --max-memory-mb 4096` and the equivalent Agent Dispatch suite pass for
      every implementation slice.
- [x] Shared-library tests, version consistency, generated payload guards, and
      `python tools/check-install-contract.py` pass before publication.
- [ ] Deployed evidence shows negligible idle supervisor CPU, no visible console
      creation, one persistent SSH carrier per active host, bounded transient
      child processes, and stable memory across reconnect.

## Proposal

Use a portable application-level carrier rather than treating OpenSSH
ControlMaster as the cross-platform contract. Agent Bridge starts one long-lived
SSH stdio process per normalized host connection identity. The remote command is
an Agent Bridge-owned carrier endpoint that speaks a small versioned framed
protocol. The local daemon multiplexes logical request IDs and subscription IDs
over that process, so native Windows receives the same single-connection shape
as POSIX without a Dispatch-owned SSH pool or platform-specific socket
assumption.

The cheaper alternative is one existing `LocalForward` per host plus the remote
Bridge's current HTTP/SSE API. It is not the selected contract because the
remote daemon publishes a dynamic endpoint that changes across cutover, the
forward must bind that port before connection establishment, and the local
client would need the remote daemon's bearer token copied off-host. Replacing a
forward across endpoint movement would add a second local endpoint-lifetime
contract, while exporting the token would widen its trust boundary. A remote
carrier endpoint instead resolves the remote daemon locally on each operation or
resubscription, keeps its token on the host, and preserves one reconnect seam.
The implementation should reuse `LocalForward` and existing HTTP/SSE machinery
internally where they remain useful, but it must not make forwarded-port
discovery and remote-token distribution the public host-carrier contract.

The carrier's first operation set is deliberately narrow: remote session status,
live-session resolution, and replayable session events. Each event subscription
names an exact hosting-Bridge session ID and a last durable event position. The
remote endpoint reads its existing event authority and emits unchanged event
identities; after reconnect, the local side resubscribes from the last confirmed
position under a distinct stable supervisory caller identity. Event-log rebuild
or cursor invalidation is an explicit gap signal, never an empty success. The
proxy stores no second event log. Request failures are correlated and bounded,
subscription transport loss is recoverable, protocol heartbeat detects
half-open silence, and protocol mismatch fails before work.

Agent Dispatch reaches this through one long-lived local Agent Bridge
subscription client per supervisor process. That client multiplexes every
reservation in the lane and retains a distinct caller cursor for each exact
session; it never creates a process per reservation. Selected durable events
become a coalesced wake primitive. A wake runs the same reconciliation pass that
the timer runs. Missing events can delay a pass until the ordinary interval but
cannot change correctness; heartbeat expiry, cursor gaps, and local Bridge
cutover force an immediate full pass before reconnect, and no fallback
introduces a second short polling loop.

After the event path is stable, existing remote Agent Bridge command operations
move through the same carrier one at a time. Arbitrary remote shell execution is
not part of the initial contract: it would widen authorization and output
semantics before a demonstrated caller requires it. This keeps the first
end-to-end slice small enough to validate while establishing the ownership and
transport foundation #1763 requires.

## Journal

### 2026-09-04 — Remote command consolidation

- Phase 4 merged through #2011 and deployed Agent Bridge `0.4.0-dev432` plus
  Agent Dispatch `0.1.2-dev7` through the unified updater. Bridge used its
  zero-downtime cutover and Dispatch rebuilt/re-adopted its supervisor
  generations without stopping healthy work.
- Began #1780 by extending the reviewed carrier operation vocabulary with
  version-2 create, stop, end, and represented live-message requests while
  preserving version-1 status, live-resolution, event, and acknowledgement
  traffic during rolling upgrades.
- Dispatch now reaches remote fleet create, status/activity, end,
  worktree-to-live-session resolution, and queued steering/redrive through an
  independent authenticated local HTTP adapter. Raw SSH remains only when the
  local Bridge endpoint, authentication, or required protocol generation is
  absent; a carrier timeout or remote rejection never starts parallel direct
  outreach.
- Carrier cancellation during session creation now waits for the non-cancellable
  host-local start/submit thread and reclaims any late-created session before
  propagating cancellation, preventing an untracked duplicate body on retry.
  Cleanup transport failures and repeated cancellation cannot replace the
  caller's original cancellation.
- Follow-up review also tightened mutation validation: stop/end flags are strict
  booleans, expected-session and idempotency guards are bounded safe identifiers,
  and raw-SSH stop fallback failures normalize to `False` rather than escaping
  the existing best-effort API.
- The final complete managed portfolios pass 2,158 Agent Bridge tests with 13
  skips and 1,839 Agent Dispatch tests with 3 skips. The focused migration sets
  pass 57 Bridge tests and 127 Dispatch tests. Version consistency, version
  allocation, generated payload, install-contract, contract-registry,
  compilation, and fatal Ruff guards pass.
- Pulled forward onto #2017 after upstream consumed the original Agent Dispatch
  allocation. Publication then advanced again while the PR opened, so the final
  versions are Agent Bridge `0.4.0-dev433` and Agent Dispatch `0.1.2-dev11`.
- Provider review found two adapter boundary gaps. Malformed local Bridge auth
  YAML now degrades as absent capability, and the health capability probe shares
  the caller's total operation timeout instead of adding a fixed five seconds.

### 2026-09-04 — Agent Dispatch event wake

- Added Agent Bridge HTTP protocol generation 13 and authenticated
  `POST /api/v1/remote/events`, which multiplexes a bounded set of exact
  host/session/caller subscriptions over one local SSE response while preserving
  the hosting Bridge's durable cursors and existing persistent carrier leases.
- Added one independent event-wake worker per long-lived Dispatch supervisor.
  Fleet reservation identities become one replaceable aggregate subscription
  set; registered supervisors derive stable lane caller IDs, pushed lifecycle
  boundaries coalesce into the ordinary reconciliation pass, and one-shot
  supervision starts no background worker.
- Kept periodic reconciliation as the sole correctness floor. Missing or stale
  Bridge capability, carrier loss, stream exit, and control envelopes wake one
  immediate full pass per outage generation and reconnect with bounded backoff;
  heartbeat or event progress is required before a later failure begins a new
  outage generation.
- Cursor acknowledgement occurs only after reconciliation. Identified
  invalidation and replay-gap controls carry the authoritative head and
  continuity so the completed pass can reset that generation; a crash before
  acknowledgement replays the boundary. Generation checks also prevent a stream
  opened concurrently with close or subscription replacement from being
  published late.
- Published version allocations Agent Bridge `0.4.0-dev432` and Agent Dispatch
  `0.1.2-dev7`. The complete Dispatch portfolio passed 1,797 tests with 3
  skips before final-review fixes; the affected final Dispatch set passes 172
  tests. The full Bridge run reached 2,278 passes with 14 skips and one
  independently reproduced unchanged `origin/main` mock-signature failure; the
  affected remote, cursor, contract, and database sets pass 79 and 65 tests
  after the fixes. Install-contract, version, payload, and Ruff guards pass.
- Three independent review findings drove cursor-generation recovery,
  connection-establishment cancellation, and persistent-fault outage
  coalescing regressions. The final high-confidence re-review reported no
  remaining significant issues.
- Provider review found that aggregate streams discarded carrier
  `tool_progress` envelopes, which could falsely trip the local read timeout
  during active remote work. Aggregate streams now forward tool progress as
  non-waking SSE comments, matching the single-subscription keepalive contract.
  The same review cycle exposed and corrected incomplete generation-13 contract
  registry evidence while preserving generation 12 as the previous fixture.

### 2026-09-03 — Remote Bridge operations

- Landed the Phase 3 implementation through #1944, then landed #2000 to make
  abandoned Agent Bridge SSE iterators close safely and publish Agent Bridge
  `0.4.0-dev431`. Both changes were deployed through the unified updater before
  Phase 4 publication work resumed.
- Added the carrier operation router for exact owned-session status, exact
  represented live-session resolution, cursor acknowledgement, and replayable
  event subscriptions. The far-side carrier authenticates to its host-local
  Bridge and reuses the existing HTTP status, live-session, SSE, and cursor
  authorities; no Dispatch import or second session/event ledger was added.
- Added authenticated local `/api/v1/remote/{host}/...` and `agent-bridge
  remote` surfaces. Callers provide only a topology host, exact hosting-Bridge
  session ID, and a required stable consumer-specific `caller_id`; topology
  resolution selects the SSH alias, user, port, and remote shell internally.
- Kept actual hosting event IDs, names, payloads, and timestamps unchanged.
  Delivery acknowledgement remains remote and caller-scoped. Carrier reconnect
  deliberately drops the initial cursor precondition and resubscribes from the
  hosting Bridge's durable acknowledged cursor, which also lets a replacement
  local daemon recover after cutover.
- Made event-log rebuilds persist per-caller cursor invalidations. Controlled
  streams detect continuity changes and non-contiguous IDs and emit a
  cursor-neutral `bridge_control` / `full_reconcile` signal instead of an empty
  stream. Continuity-qualified acknowledgements reject stale-log acks.
- Extended the shared carrier server to stream async handler results and
  preserve structured remote error codes, synchronized all three vendored
  `ssh-manager` copies, and bumped the shared library plus Agent Bridge,
  Agent Codespaces, and Agent Containers runtime/marketplace versions.
- Focused Phase 3 coverage passes 159 tests and the complete shared
  `ssh-manager` platform/manager/carrier suite passes 65 tests. The official
  Agent Bridge guard portfolio passes; the five-shard run passed shards 1-3 and
  5, while shard 4's changed surfaces passed after isolating one unchanged
  `origin/main` mock-signature failure and rerunning an environment-sensitive
  session-selection surface through the official contained runner. Vendoring,
  documentation, install-contract, payload-invocation, diff, and fast Ruff
  guards pass. Repository-wide version consistency is currently blocked by an
  unrelated `origin/main` agent-index fallback mismatch; every version surface
  changed by this phase agrees.

### 2026-09-02 — Persistent carrier foundation

- Marked the reviewed ownership and protocol proposal complete and activated
  implementation.
- Added a bounded, versioned framed carrier protocol to `ssh-manager`, with one
  lifecycle per normalized connection identity, concurrent request correlation,
  replay-shaped subscriptions, cancellation, heartbeat/progress staleness,
  bounded buffering, reconnect backoff, idle retirement, and deterministic
  process-tree teardown.
- Added the Agent Bridge-owned `carrier --stdio` endpoint, cross-platform remote
  command construction, aggregate payload-free health diagnostics, focused
  unit/process coverage, and synchronized the vendored library copies.
- Independent bug reviews drove regression coverage for startup and retirement
  races, alias and tunnel identity, cancellation and buffer cleanup, negotiated
  frame direction, replay cursor correctness, complete reconnect restoration,
  stale transport generations, and independent-host startup. The final
  stop-ship review reported no findings.
- The focused shared-library suite passes 62 tests and the authoritative Agent
  Bridge runner passes all five sub-suites. The process-heavy Agent Codespaces
  installer-recovery file passes in isolation; its full Windows shard can exceed
  the existing per-subprocess timeout after cumulative host contention.
- Kept remote session operations and Agent Dispatch consumption out of this
  phase; those remain owned by #1778 and #1779.

### 2026-09-02 — Proposal

- Reconciled #1763 to Agent Dispatch's `react-to-turn-end` vision and Agent
  Bridge's attention-subscription and cursor-replay intent.
- Confirmed current reality: Agent Bridge already owns durable SSE events and a
  process-local `ssh-manager`; POSIX ControlMaster multiplexes operations, while
  native Windows deliberately uses direct SSH and dedicated forwards.
- Selected an Agent Bridge-owned, framed stdio carrier because it gives every
  platform one persistent host connection and preserves the plugin process
  boundary. Dispatch remains a client of Bridge's public contract.
- Rejected a forwarded remote HTTP/SSE port as the public carrier contract
  because dynamic daemon endpoints would require forward replacement and local
  endpoint coordination, while remote bearer-token distribution would widen the
  credential boundary.
- Added explicit supervisory cursor isolation, cursor-gap signaling,
  heartbeat/staleness detection, remote EOF exit, idle teardown, local Bridge
  cutover recovery, O(supervisor) local client fan-out, and measurable deployed
  thresholds after architecture review.
- Kept general attention settlement in #1450 and kept periodic reconciliation
  as the correctness floor.
