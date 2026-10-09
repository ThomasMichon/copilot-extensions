# Phase 7 - Daemon rotation: client resolution boundaries

- **Parent effort:** [README.md](README.md#phase-7--health-updating--presets-ongoing)
- **Tracks:** [#5001](https://github.com/ThomasMichon/copilot-extensions/issues/5001),
  Phase 3; prerequisite design input for Phases 2 and 4.
- **Status:** Proposed design; runtime implementation and retirement remain gated.
- **Vision reconciliation:** closes the existing installer's
  [self-updating](../../../visions/installer/README.md#self-updating) intent and
  preserves session hosting's
  [capability-honest control](../../../visions/session-hosting/README.md#capability-honest-control)
  and [provider-owned retirement](../../../visions/session-hosting/README.md#provider-owned-retirement).
  No new standing capability or vision revision is proposed.
- **Patterns:** [graceful daemon cutover](../../../docs/patterns/graceful-daemon-cutover.md)
  and [local endpoint discovery](../../../docs/patterns/local-endpoint-discovery.md).
- **Scope:** define and validate client behavior before implementing the
  superseded-daemon retirement helper. This document does not add a sweep,
  terminate a process, or implement per-client wire attribution.

## Reconcile the proposal with the current implementation

The issue describes an already-connected mux client remaining pinned until a
new Copilot turn. That is not the lifetime of the current managed-mux data RPCs:

| Surface | Current behavior | Design consequence |
|---------|------------------|--------------------|
| [Coalescing client](../../../libs/work-coalescing-singleton/src/work_coalescing_singleton/client.py), `_send_recv` | Opens and closes a socket for each message. A `request` and its later `release` use separate connections. | A new logical request, not a model-turn boundary, is the endpoint-selection boundary. |
| [Status publisher](../../../plugins/agent-worktrees/src/agent_worktrees/mux_status_link.py), `push_status_via_daemon` | Reads routing and the control token on each call, falls back to the legacy rendezvous when needed, creates a fresh client ID, and releases that ID in `finally` against the selected endpoint. | Current status calls already re-resolve between requests. Prove that contract before adding a reconnect mechanism. |
| [Live publisher](../../../worktree-manager/src/worktree_manager/mux_daemon_live.py), `mux_live_with_boot` | Resolves the monitor lock for each publication and keeps the chosen endpoint for that request and release. | Preserve per-publication resolution in the reverse direction; do not confuse the monitor endpoint with the Manager endpoint. |
| [Control client](../../../worktree-manager/src/worktree_manager/mux_daemon_cutover.py), `ControlClient` | Keeps its target host/port for health, drain, undrain, and shutdown. Successful drain can separately adopt the active replacement. | Lifecycle commands must continue targeting the intended predecessor; automatically retargeting shutdown to the active replacement would be unsafe. |
| [Coalescing server](../../../libs/work-coalescing-singleton/src/work_coalescing_singleton/server.py) | Subscriber IDs have liveness stamps and TTL reaping. Accepted-handler count is separate. | Subscriber count is neither a persistent socket count nor proof of current transactional work. |

These are current-code observations, not evidence that every old installed
generation has the same behavior. Inventory and compatibility validation must
classify older generations rather than extrapolating from today's source.

The production status-monitor path in
[`agent_worktrees.__main__`](../../../plugins/agent-worktrees/src/agent_worktrees/__main__.py)
calls `publish_managed_session_status`. Identical option values intentionally
produce no RPC; managed sessions do not fall back to direct option writes when
the Manager call fails. A daemon rotation must not depend on an unchanged
status render producing a heartbeat.

## Terms and ownership

- **Mux attachment:** a human terminal client attached to a native mux session.
  The mapping's `attached_clients`, refreshed by #5573, describes this signal.
- **RPC subscriber:** an opaque coalescing-client ID, possibly awaiting TTL
  cleanup after a failed release. It does not identify a worktree or a human
  terminal attachment.
- **In-flight work:** accepted RPC handlers, actual status-option application,
  and the resident loop's fenced mutations. A health snapshot alone does not
  prove the drain boundary is closed.
- **Execution session:** the native mux pane and its hosted process. Retiring
  a mux-companion daemon does not authorize retiring these separate resources.

The Phase 1 wire-attribution gap remains separate. Neither a root-wide mapping
list nor a terminal count proves which daemon owns a particular RPC subscriber.

## Client contract

The following clarifications are **agent-recommended**, grounded in the
existing code and the parent issue's no-interruption requirement.

1. **Resolve before a new data operation.** Use the owning component's resolved
   root and existing routing/rendezvous helpers. Never carry a data endpoint
   across successive logical requests merely because a prior call succeeded.
   Do not introduce machine paths, another installation root, or a new global
   discovery registry.
2. **Pin one operation and its release.** Once a request selects endpoint A,
   its response handling and subscriber release remain on A even if routing
   flips to B while the request is in flight. The next request selects B.
   No in-flight request is migrated or replayed merely because the route changed.
3. **Preserve failure and ordering semantics.** Keep existing bounded deadlines,
   structured unavailable/draining results, render ordering fences, and managed
   ownership. Do not turn a missed Manager response into a successful direct
   write, advance the published-value cache on failure, or silently resend an
   uncertain mutation to another endpoint.
4. **Preserve deliberate no-op renders.** Native mux option state and mapping
   fences survive a companion-daemon swap. Unchanged values need not generate
   another RPC just to retire a predecessor; any additional migration
   invalidation must be justified by a real consumer-state loss and tested.
5. **Keep control targets distinct.** A retire operation may resolve the active
   replacement for readiness/adoption, but predecessor health, drain, recovery,
   and shutdown remain bound to the validated predecessor identity. Never
   inherit the data plane's automatic next-operation selection for shutdown.
6. **Make legacy compatibility explicit.** Retain the current legacy-rendezvous
   fallback until its callers are reconciled through the normal update flow.
   A missing routed token is not permission to retire the legacy target. If an
   older client caches a target or an older daemon cannot guarantee the required
   drain contract, report that limitation and leave its retirement tracked.

## Consequences for the Phase 2 retirement helper

This is a safety contract, not permission to implement the helper before the
design and client-conformance gates clear.

- Serialize retirement with activation through the existing root-scoped cutover
  coordination. Revalidate the candidate's identity and routing role immediately
  before mutation; a snapshot marked superseded can become active again.
- Protect the effective working route, including `previous` when the routing
  resolver selects it, and any still-required legacy fallback. The JSON
  `active` field alone is not enough to classify a disposable endpoint.
- Confirm that the replacement can actually accept the required work. A TCP
  listener, a generic health `ready` value, or a successful spawn is not a
  substitute for the existing readiness/adoption contract.
- Use `force=False`. Closing admission precedes checking the full drain
  predicate, including loop mutation. A successful health probe or zero
  terminal attachments is not that predicate.
- Require a demonstrated compatible drain contract for the candidate generation.
  Legacy/unknown capability is a visible skip, not a success-shaped result.
  Record the compatibility evidence before enabling automated retirement; do
  not invent a minimum version from an unpromoted source commit.
- On failure, preserve or recover the predecessor through the existing lifecycle
  contract. Do not add bare PID termination as a fallback for unconfirmed drain.
  A shutdown acknowledgement is not proof of exit; observe exit without acting
  on a reused PID.
- Never stop the native mux server, a pane, or a hosted Copilot process as a side
  effect of retiring the companion. Keep the issue open for unsupported legacy
  generations rather than claiming universal cleanup.

## Ordered implementation and validation gate

After this design clears review:

1. **Prove current data-call conformance.**
   Extend the existing status/live-link tests with two real coalescing endpoints.
   Publish route A, issue a call, flip to B, and issue another call. Assert that
   the second call reaches B and every release reaches the endpoint that accepted
   its own request. Repeat with a route flip during a blocked A request; A must
   finish without replay and the next operation must resolve B.
2. **Prove failure and compatibility behavior.**
   Cover missing/torn routing, a live-starting active endpoint, selected previous
   endpoint, missing routed token, legacy-only rendezvous, failed release/TTL
   cleanup, and unavailable responses. Published caches must remain unchanged on
   failure. Verify the real status-monitor consumer path, not only a helper mock.
   Record which older-generation contracts are supported or still blocked.
3. **Change client code only for a demonstrated gap.**
   Reuse current discovery, request, release, and lifecycle helpers. A
   conformance-only result is valid if existing production behavior already
   satisfies the contract; do not add a persistent reconnect loop to satisfy the
   proposal's original wording.
4. **Then implement Phase 2 under its own reviewed slice.**
   Validate active/previous/legacy protection, identity mismatch and PID reuse,
   promotion/readiness, admissions closing before drain, concurrent accepted
   requests, loop mutation, timeout/recovery, and observed exit. Keep target-bound
   control separate from next-operation data resolution.
5. **Then wire and validate Phase 4.**
   Reuse the declared resident owner/cooldown rather than adding a daemon.
   Preserve a read-only dry-run and explicit apply semantics. Validate staggered
   traffic and bounded retries, then exercise a live supported-generation fleet.
   Reconcile unsupported generations explicitly before any broader completion
   claim.

## Validation plan

- [ ] Demonstrate request-bound endpoint selection and release with two real
      endpoints, including a route flip during an in-flight request.
- [ ] Exercise the actual managed-status consumer and its unchanged-value cache;
      ensure retirement does not rely on a no-op render issuing an RPC.
- [ ] Validate reverse live-publication discovery without retargeting the
      predecessor's control operations.
- [ ] Exercise active-starting, previous, legacy, missing-token, and uncertain
      response cases with explicit failure/compatibility outcomes.
- [ ] Verify subscriber release/TTL behavior independently of terminal attachment
      counts and accepted-handler/loop drain state.
- [ ] Record supported legacy behavior and any named remaining blocker before
      retirement implementation or automatic application.
- [ ] Record platform coverage and a live supported-generation check for the
      retirement/sweep implementation, with explicit reasons for unavailable lanes.

All boxes remain open at design publication. No runtime conformance,
compatibility migration, or retirement completion is implied by this document.
