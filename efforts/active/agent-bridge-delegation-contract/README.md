# agent-bridge Delegation Contract

- **Slug:** `agent-bridge-delegation-contract`
- **Repo:** copilot-extensions
- **Branch(es):** one issue-bound PR worktree to `main`
- **Created:** 2026-08-31
- **Status:** Active
- **Vision:** advances
  [`visions/plugins/agent-bridge`](../../../visions/plugins/agent-bridge/README.md)
  §Features/`task-shaped-delegation-control`,
  `attention-boundary-subscriptions`, `bounded-delegated-results`, and
  `single-stream-message-admission`, plus
  §Behaviors/`attention-requires-a-live-relationship`
- **Umbrella issue:** [#1449](https://github.com/ThomasMichon/copilot-extensions/issues/1449)
- **Parent effort:**
  [`agent-bridge-delegation-convergence`](../agent-bridge-delegation-convergence/README.md)
  / [#1448](https://github.com/ThomasMichon/copilot-extensions/issues/1448)
- **Related issues:** [#1460](https://github.com/ThomasMichon/copilot-extensions/issues/1460)
  (contract evolution) ·
  [#1468](https://github.com/ThomasMichon/copilot-extensions/issues/1468)
  (wire/durable baseline) ·
  [#1138](https://github.com/ThomasMichon/copilot-extensions/issues/1138)
  (immutable event identity) ·
  [#84](https://github.com/ThomasMichon/copilot-extensions/issues/84)
  (ground-layer session head and lineage) ·
  [#1045](https://github.com/ThomasMichon/copilot-extensions/issues/1045)
  (progress inspection) ·
  [#112](https://github.com/ThomasMichon/copilot-extensions/issues/112)
  (handoff) ·
  [#22](https://github.com/ThomasMichon/copilot-extensions/issues/22)
  (terminal reconciliation) ·
  [#48](https://github.com/ThomasMichon/copilot-extensions/issues/48)
  (worktree ownership) ·
  [#60](https://github.com/ThomasMichon/copilot-extensions/issues/60)
  (live-session liveness) ·
  [#1266](https://github.com/ThomasMichon/copilot-extensions/issues/1266)
  (AHP convergence)

## Guiding Intent

Freeze the semantic contract before implementing a new delegation facade or
writer.

The bridge already has durable sessions, event cursors, queueing, live-session
messaging, interruption, stop/resume, and handoff. This stretch documents how
those current mechanisms map to one logical delegated-agent model, where the
mapping is incomplete, and which stable meanings later slices must share.

The output is a reality document, not a runtime change. It names the logical
identity, caller relationships, attention reasons, and message-idempotency
scope that #1450–#1454 and #1506 consume. Wire-shape provenance and
compatibility machinery remain owned by #1460/#1468.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Delegation contract driver | Baseline, semantic decisions, PR, and issue closure | This issue-bound worktree |
| Contract foundation lane | Registers the contract before a new writer/default is enabled | #1460/#1468 |

## Coordination

- **Topology:** one documentation-only PR; no runtime writer or default changes.
- **Host (owns PRs):** delegation contract driver.
- **Delegates:** none.
- **Handoff:** merge the contract, close #1449, and leave implementation to the
  numbered child issues under #1448. On closure this effort becomes
  `Done; pending archive` in the active index, then moves to the dated archive.

## Context

The current bridge exposes several identities and control paths: bridge session
ID, ACP session ID, worktree ID, caller ID/cursor, live interactive session ID,
and worktree handle. It also emits events that imply caller attention, but
`wait` currently follows only turn settlement. Live-session messages have an
idempotency key, while the ordinary hosted-session prompt queue does not.

Without one documented semantic waist, each implementation slice could choose a
different identity, attention enum, or idempotency scope. This stretch prevents
that divergence without blocking the existing #1460 contract-foundation work.

## Request

> Carve an effort through an achievable stretch and get started.

The selected stretch is #1449: document and land the delegated-agent lifecycle
and attention baseline without changing runtime behavior.

## Plan

### Phase 1 — Capture current behavior

- [x] Inventory the CLI and authenticated API control surfaces.
- [x] Record current lifecycle, identity, queue, cursor, wait, and live-session
      admission semantics.
- [x] Distinguish current runtime guarantees from gaps owned by later issues.

### Phase 2 — Freeze target semantics

- [x] Define one logical delegated-agent reference derived from the
      authoritative worktree/session lineage rather than adding a rival
      identity store.
- [x] Define attached, subscribed, observer, and detached caller relationships.
- [x] Define stable attention reasons and settlement rules.
- [x] Define logical-message idempotency scope, conflict behavior, succession,
      and retention.
- [x] Record ownership boundaries with #1460/#1468, #1138, #1045, #112, #22,
      #48, #60, and #1266.

### Phase 3 — Publish and transfer

- [x] Link the contract from agent-bridge's README and architecture.
- [x] Mark the parent effort's Phase 0 contract items complete or transferred.
- [ ] Record the landed PR on #1449 and release the issue claim.

## Validation Plan

- [x] Every statement of current behavior is traceable to current source.
- [x] The target contract separates logical delegate state, session state,
      transport liveness, and attention reasons.
- [x] Recoverable connection churn does not become an attention reason.
- [x] Detached callers receive no false future-wake guarantee.
- [x] Idempotency semantics cover retry, payload conflict, restart, queue, and
      successor handoff.
- [x] The document creates no competing wire registry, event identity, AHP
      state machine, or runtime writer.
- [x] Repository documentation consistency checks pass.

## Proposal

Add `plugins/agent-bridge/docs/delegation-contract.md` as the semantic baseline.
It records both the current surface and the target contract, clearly labeling
which behavior exists and which issue owns each delta. Link it from the plugin
README and architecture, then transfer registration to #1460/#1468 before any
new writer or default is enabled.

## Journal

### 2026-08-31 — Kickoff

- Claimed #1449 with dispatch task `2cef4ca9d550495095961fde1d512753`.
- Selected a documentation-only contract baseline as the first achievable
  stretch.
- No runtime behavior changes are in scope.

### 2026-08-31 — Contract ready for publication

- Inventoried current CLI, API, lifecycle, cursor, queue, wait, resync, peek,
  handoff, and represented-session behavior.
- Defined the lineage-derived delegate reference, attention-result matrix, and
  logical-message idempotency contract.
- Linked the baseline from the plugin README and architecture and completed the
  parent effort's Phase 0 semantic items.
- Left only PR publication, issue completion, and claim release open.
