# Central push-backed state and subscriptions

Back to [Fleet Control V2](README.md). This is an **agent-recommended design
amendment**, not a delivered store, service cutover or adoption grant.
It realizes [machine-fleet](../../../visions/machine-fleet/README.md)'s shared
backing-store and resumable-observation intent through existing #5862.

## Requested outcome

Worktree Manager's fleet view, other UX surfaces, CLI tools and API clients
consume one central current-state and subscription plane. Authorized machines
and containers push changes; a client request does not trigger an independent
fleet-wide SSH probe, record scrape or history walk.

One selected Gateway deployment hosts this plane. A selected containerized
deployment is not accompanied by a second native or prototype Gateway serving
as a competing coordinator. An existing credential coordinator/controller is
a separate provider role, not automatically a duplicate Gateway to stop.
Actual adopter deployments, leases and endpoint/data custody stay outside
this generic proposal.

## Reuse and ownership

| Owner | Retained responsibility |
| --- | --- |
| #5862 / this amendment | Coherent observation, accepted-update projection and query/subscription recovery |
| [#3761 authoritative daemon](../agent-worktrees-authoritative-daemon/README.md) | Origin worktree agency/write authority, schema-bounded current projections and write-through mutation seam |
| [#352 Worktree Manager](../worktree-manager-control-plane/README.md) | Client UX, launch/mux presentation and its own host mappings, not a global agency database |
| #5789 / #6443 | Principal/connector authentication, authorized routing and Gateway integration |
| #6240 / #6424 | Selected role release, immutable construction, durable admission, health and activation |
| Existing task/session/provider owners | Canonical operations, claims, outcomes, credentials, resource fences and substrate lifecycle |

The central store initially holds projections of unmigrated authorities.
It never creates a rival task queue, worktree registry, claim writer,
credential cache or arbitrary remote execution surface. A client mutation goes
through its existing named owner/admission/receipt contract; subscriptions may
observe that outcome but never execute it again. Existing local standalone use
and the Manager's local mux companion remain independent of fleet adoption.

## Minimal target shape

```text
machine/container origin authorities
  -> committed current-state publisher / outbound connector
  -> selected Gateway: authenticated, fenced update admission
  -> durable accepted updates + indexed current projection + publish cursor
  -> one scoped query / snapshot / subscription contract
  -> Worktree Manager | other UX | CLI | API clients
```

**Initial implementation recommendation:** add one current-state module to the
selected Gateway role, not another Gateway process or daemon hidden in a UI.
Use a small local SQLite store on attributable persistent storage, with one
active storage owner. Commit an accepted update, its current projection and
publication position together before acknowledging or exposing it.
No external message broker or network database is required for the first slice.
Do not append foreign tables to the existing strict
[operation ledger](../../../fleet-manager/docs/operation-ledger.md) or treat its
outbox as a telemetry queue; observation storage has a separate explicit schema
and lifecycle while commands retain their existing owner.

Read models are bounded by the requested current entity/page, not accumulated
journals, claims graphs or fleet cardinality. Maintain query indexes and current
facts on the writer path. Historical task outcomes, transcripts and telemetry
remain with their existing owners; the observation feed is not an unbounded
second copy of those histories.

## Origin admission and push

- Bind every update to an authenticated, authorized logical origin and
  installation/producer incarnation. A machine, its WSL environment and each
  managed container remain distinct identities. Names, network reachability,
  a supplied role or an empty filter are not grants.
- Reuse the origin owner's committed projection, current revision and explicit
  coverage. Connector epochs, allocation generations and entity revisions have
  different purposes; do not substitute one for another.
- Accept identical retries idempotently. Refuse a conflicting update at the
  same revision, stale incarnation or cross-origin write without replacing
  newer state. Wall-clock timestamps do not decide which writer wins.
- An origin restart/re-enrollment requires an attributable incarnation
  transition, not permission to reset a sequence and overwrite a prior owner.
  Origin removal requires explicit owned tombstone/retirement evidence.
- Bound messages, update rates and local pending state. Coalescing unsent
  current observations is allowed only with an explicit snapshot/resync
  contract; it cannot discard canonical operation outcomes or obligations.
  Overflow and discontinuity are visible, never silently complete.
- Existing work continues outside the publisher's lifetime. A failed observer
  reports degraded coverage rather than blocking an already accepted agent or
  claiming it stopped. Heartbeat expiry never releases a resource or claim.

## Requests and subscriptions

One versioned, capability-honest service contract supports current point reads,
bounded filtered pages, coherent snapshots and subscriptions for every client.
All readers see the same committed facts and explicit provenance, source
revision, freshness and coverage. Keep UI formatting/glyphs outside stored
domain facts; CLI and headless consumers are first-class clients.
Coherence is at a central committed position with per-origin coverage, not a
claim of simultaneous source observations or a transaction across every host.

Snapshot creation and its subscription position must form one consistent
boundary: no missed update between initial query and stream attachment.
Reconnect resumes a valid retained cursor or explicitly requires a fresh
snapshot. Bounded retention, invalidated cursors and source gaps are surfaced;
an empty replay is not evidence of no change when coverage was lost.
Slow subscribers have isolated queues and bounded backpressure, with explicit
disconnect/resync rather than blocking producers or other clients.

Authorize both ingestion and reads/subscriptions by origin, fleet, project,
entity and permitted fields. Apply scope before lookup, aggregation or event
delivery. Presence/count metadata and subscription errors must not disclose
another scope. Recheck revocation and expiry for long-lived connections.
Publishing state, reading state and invoking lifecycle commands are separate
capabilities. Tokens, credential contents and unselected transcript bodies do
not enter records, events, logs or fixtures.

## Lifecycle and adoption

Use the selected Gateway's existing release, installer/update and activation
owners. Keep persistent state outside immutable runtime bytes. Inspect and
version the store; refuse foreign/corrupt/unsupported state. Upgrade and rollback
must preserve acknowledged updates and current cursor/projection consistency.
Never restore an old database over acknowledged post-upgrade writes.

A containerized adopter uses its one selected deployment and owned persistent
storage. Before changing an existing endpoint or retiring a process, verify
consumer routes, live leases, data custody and rollback through its current
owner. A process called a coordinator may own credentials rather than a
duplicate Gateway. Do not stop it or drop a volume merely to simplify a diagram.
No healthy process, expired heartbeat or unresolvable owner proves safe takeover.
Cross-host relocation, replicated writers and authority migration are later
explicitly fenced operations, not benefits inferred from an HTTP health probe.

## Validation gates and rollout boundary

- [ ] Review this amendment and agree the current-projection/push, principal,
  storage and client interfaces with the retained owners before runtime edits.
- [ ] Unit: immutable bounded update shapes, identity/scope/revision conflicts,
  duplicate retries, stale incarnation, tombstones, query grants and cursor gaps.
- [ ] Separate-process E2E: two machine origins plus a container origin push
  actual committed changes; independent UX/CLI/API clients query and subscribe
  through the same Gateway/store boundary without per-client remote polling.
- [ ] Crash at update/commit/ack and snapshot/subscribe boundaries. Restart and
  replay/resnapshot preserve newer facts, coherent cursors and original
  operation responsibility without duplicate commands.
- [ ] Bound slow/offline origins and subscribers, bursts, retention overflow,
  revocation, mixed versions and growing journals/fleet size. Prove point/page
  read cost and queue/resource limits, not only a happy-path final response.
- [ ] Fresh Windows/Linux producer and containerized Gateway installation with
  real authenticated transport and persistent store; no plugin/cache dependency
  for installed service operation. Record clean-room and available live tiers.
- [ ] Authorized adopter convergence: one serving Gateway/storage writer,
  preserved credentials/data/consumers and honest rollback or blocked recovery.
  Verify actual workload/route/subscription behavior, not health alone.

This plan performs no runtime mutation, credential enrollment, deployment,
capacity expansion, source retirement or live lease handoff. The larger effort
remains Active until every original and amended Plan/Validation item is resolved.
