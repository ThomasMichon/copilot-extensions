# agent-bridge Delegated Result Snapshots

- **Slug:** `agent-bridge-delegated-result-snapshots`
- **Repo:** copilot-extensions
- **Branch(es):** one issue-bound PR worktree to `main`
- **Created:** 2026-09-01
- **Status:** Active
- **Vision:** advances
  [`visions/plugins/agent-bridge`](../../../visions/plugins/agent-bridge/README.md)
  §Features/`bounded-delegated-results` and
  `task-shaped-delegation-control`
- **Umbrella issue:** [#1452](https://github.com/ThomasMichon/copilot-extensions/issues/1452)
- **Parent effort:**
  [`agent-bridge-delegation-convergence`](../agent-bridge-delegation-convergence/README.md)
  / [#1448](https://github.com/ThomasMichon/copilot-extensions/issues/1448)
- **Related issues:** [#1045](https://github.com/ThomasMichon/copilot-extensions/issues/1045)
  (liveness and progress inspection) ·
  [#1138](https://github.com/ThomasMichon/copilot-extensions/issues/1138)
  (immutable event and projection references) ·
  [#1449](https://github.com/ThomasMichon/copilot-extensions/issues/1449)
  (delegation contract baseline)

## Guiding Intent

Give a caller a fixed-size, cursor-neutral account of what a delegated agent
has produced.

The ordinary result path should combine current lifecycle and attention state,
the latest completed result, and a bounded incremental work summary. It should
not require a caller to ingest the complete SSE stream, reasoning text, or
verbose tool output. Full event fidelity remains an explicit recovery and
expansion path.

The projection must stay truthful across the two target classes agent-bridge
already represents. Bridge-owned ACP sessions can recover completed turns from
durable storage. Interactive sessions expose only their in-memory translated
tail, so they must advertise reduced fidelity and unavailable durable detail
instead of returning a success-shaped omission.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Bounded-result driver | Projection contract, implementation, tests, PR, and issue closure | This issue-bound worktree |
| Progress-inspection lane | Supplies current liveness and active-work fields without a competing status model | #1045 |
| Reference-contract lane | Owns the general immutable-reference contract; this slice uses opaque tokens over existing event-log continuity and turn identities | #1138 |

## Coordination

- **Topology:** one reviewed proposal PR followed by reader-only implementation
  PRs for bridge-owned and represented sessions; no new durable state writer or
  default.
- **Host (owns PRs):** bounded-result driver.
- **Delegates:** none.
- **Handoff:** merge the bounded readers, update the parent effort's Phase 1A
  items, close #1452, and leave broader progress proof and reference-contract
  evolution to #1045/#1138. The opaque position/reference format is a narrow
  #1138 coordination seam: callers must not parse it, and #1138 may replace its
  encoding behind compatibility readers.

## Context

The current bridge already has the raw ingredients but exposes them through
separate surfaces:

- `status` returns lifecycle, context use, pending input, cursor lag, and the
  existing low-noise progress fields.
- persisted turn rows retain the latest completed response and stop reason
  across daemon restart.
- event logs provide cursor-neutral random access and an internal,
  restart-stable `log_epoch` derived from the first durable event; rebuild
  rotates that epoch.
- represented interactive sessions translate a bounded live SDK tail into the
  same event vocabulary, but intentionally do not persist it.

The result reader should compose those owners rather than adding another
progress payload, transcript store, or cursor. A caller-held result position is
an opaque bridge-issued token. The bridge may encode its current event-log
continuity and event ID in that token, but callers cannot parse or compare it.
Supplying a stale token must report a history discontinuity instead of
reinterpreting the event ID against rebuilt history.

## Request

> Give callers a bounded account of a delegated agent's current and accumulated
> work without requiring them to consume the complete SSE transcript or verbose
> tool stream.

## Plan

### Phase 1 — Freeze the bounded reader contract

- [x] Define one response shared by bridge-owned and represented sessions:
      logical delegate/current-session identity, fidelity, current state,
      latest result, incremental work, position, and truncation.
- [x] Reuse the existing event-log continuity and persisted turn index behind
      opaque bridge-issued tokens; add no competing identity, public epoch
      shape, cursor, transcript, or durable writer.
- [x] Define deterministic text, item-count, and total-response bounds plus
      explicit history discontinuity and unavailable-detail states.
- [x] Define field-level availability (`available`, `unknown_after_restart`,
      `unsupported_for_target`, or `not_yet_observed`) so missing evidence is
      never rendered as an empty success.
- [x] Reuse #1449's attention vocabulary without adding wait/subscription
      semantics owned by #1450.
- [x] Define succession behavior: a predecessor snapshot names its successor,
      and a logical-delegate/worktree read resolves the current head when that
      authority is available.
- [x] Define additive mixed-version behavior: an older daemon reports the
      result reader unavailable/upgrade-required before any side effect, while
      capability registration remains with #1460/#1468.

### Phase 2 — Build the bridge-owned projection

- [x] Add a pure bounded projection helper that suppresses reasoning and verbose
      tool content by default while preserving agent messages, tool lifecycle,
      requests, failures, and turn settlement.
- [x] Compose bridge-owned current state from the existing status/progress
      fields and latest completed result from persisted turns.
- [x] Make incremental reads cursor-neutral: caller A's position never reads or
      advances caller B's delivery cursor.
- [x] Add a fixed-cost latest-turn query rather than loading complete turn
      history.
- [x] Treat an interrupted or incomplete persisted turn as partial/unavailable,
      never as an empty successful result.
- [x] Issue no position before the event log has an origin-derived continuity
      identity; return an explicit no-history state instead.

### Phase 3 — Expose the bridge-owned reader

- [x] Add typed authenticated HTTP responses for owned result snapshots plus
      opaque-token-validated explicit event expansion.
- [x] Add matching client methods and a human-readable/`--json` CLI command.
- [x] Make persisted-but-not-loaded and terminal session states explicit.
- [x] Document bounds, opaque position semantics, expansion, rebuild behavior,
      restart behavior, and mixed-version degradation.

### Phase 4 — Add represented-session parity

- [ ] Project represented sessions through the same typed shape from their
      translated in-memory tail.
- [ ] Mark represented event positions as process-lifetime-only and report
      durable latest-turn recovery and durable expansion unsupported.
- [ ] Preserve read-only pending input and permission evidence while reporting
      fields the SDK does not supply as unavailable.

### Phase 5 — Validate and publish

- [ ] Cover full/reduced fidelity, latest-result recovery, incremental reads,
      deterministic truncation, position mismatch, succession, and cursor
      neutrality.
- [ ] Run the focused agent-bridge suite and repository contract/version gates.
- [ ] Bump every agent-bridge version surface, publish and merge the PR, then
      reconcile the parent effort and #1452 claim.

## Validation Plan

- [ ] A bridge-owned session returns its latest completed result after a daemon
      restart without reading the full event history.
- [ ] A turn interrupted by restart reports a partial/unavailable result with
      its stop reason rather than an empty successful result.
- [ ] A no-position read returns a bounded latest window; a positioned read
      returns a bounded contiguous increment and a resumable next position.
- [ ] A snapshot before the first event has `position: null`; the first
      origin-derived position does not create a false discontinuity.
- [ ] Two callers can use independent positions without creating or advancing
      delivery-cursor rows.
- [ ] Every free-text field, collection, and total response stays within
      documented deterministic bounds.
- [ ] Reasoning content and non-terminal tool output are absent by default;
      explicit token-validated event expansion remains available.
- [ ] A rebuilt event log rejects an old opaque token and reports discontinuity
      rather than resolving the same event ID to different history.
- [ ] A parked input request whose current truth was lost across restart is
      reported unknown/unavailable rather than absent.
- [ ] A predecessor snapshot names the successor, and a logical-delegate read
      follows the current authoritative head without merging their histories.
- [ ] A represented session reports reduced fidelity, in-memory retention, and
      unavailable durable latest-turn detail explicitly.
- [ ] A represented-session position becomes explicitly discontinuous after a
      bridge restart and is never compared with an owned-session position.
- [ ] A new client against an older daemon reports result snapshots unsupported
      without mutating session or cursor state.
- [ ] Existing `status`, `read`, SSE, delivery-cursor, and resync behavior
      remains compatible.

## Proposal

Add a reader-only result snapshot over the bridge's existing state.

The snapshot position and expansion reference are opaque bridge-issued tokens.
The initial implementation may encode the existing `EventLog` continuity and
event ID, but that encoding is private and versioned. The bridge issues no token
until the event log has an origin-derived continuity identity. It validates a
token before reading; a rebuild, represented-log restart, different target, or
unsupported token version returns an explicit discontinuity rather than
resolving the same event ID against different history. #1138 remains free to
replace the private encoding behind compatibility readers.

The default snapshot uses a fixed latest window. Supplying a position returns a
contiguous bounded increment after that position and a next position; it never
acks or mutates a delivery cursor. The latest completed result comes from a
fixed-cost persisted-turn query and is independently bounded. Interrupted turns
and in-memory-only pending input carry field-level availability rather than
empty success values. Event summaries retain useful assistant output and
lifecycle milestones while collapsing reasoning and tool detail.

Bridge-owned and represented sessions share the response shape but land in
separate implementation increments. Represented sessions set reduced-fidelity
markers and expose only the current in-memory translated tail; durable
latest-turn recovery and durable expansion are reported unavailable. Attention
uses #1449's frozen reasons but creates no subscription behavior. #1045 remains
the owner of richer liveness/progress proof, #1450 remains the owner of wait
settlement, #1506 remains the owner of the compact cross-target facade, and
#1138 remains the owner of the broader immutable-reference contract.

## Journal

### 2026-09-01 — Kickoff

- Claimed #1452 through the repository's issue-claim protocol.
- Selected a reader-only projection that composes existing status, turn, event,
  and represented-session state.
- Kept event-log continuity private behind opaque positions instead of exposing
  the telemetry epoch as a public contract.
- Added field-level availability, restart/interruption, succession, and
  mixed-version requirements after design review.

### 2026-09-01 — Bridge-owned reader implemented

- Added protocol-gated `result` and `result/detail` HTTP/client/CLI surfaces for
  bridge-owned sessions and authoritative worktree handles.
- Added opaque position/detail tokens, cursor-neutral latest and incremental
  windows, fixed-cost latest-turn recovery, deterministic text/item bounds, and
  explicit restart/rebuild discontinuity.
- Kept current liveness/progress data derived from the existing status owners;
  no durable schema or writer was added.
- Covered latest-result recovery, interrupted turns, pending-input uncertainty,
  rebuild invalidation, worktree resolution, succession, expansion, truncation,
  cursor neutrality, CLI rendering, and old-daemon capability gating.
