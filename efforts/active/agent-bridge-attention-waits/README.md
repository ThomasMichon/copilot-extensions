# Agent-Bridge Attention Waits

- **Slug:** `agent-bridge-attention-waits`
- **Repo:** copilot-extensions
- **Branch(es):** one proposal PR followed by independently reviewable
  implementation and archive PRs from the delegation-contract driver worktree
- **Created:** 2026-09-01
- **Status:** Draft
- **Vision:** closes
  [`visions/plugins/agent-bridge`](../../../visions/plugins/agent-bridge/README.md)
  §Features/`attention-boundary-subscriptions` and
  §Behaviors/`attention-requires-a-live-relationship`
- **Umbrella issue:** [#1450](https://github.com/ThomasMichon/copilot-extensions/issues/1450)
- **Sub-issues:** [#22](https://github.com/ThomasMichon/copilot-extensions/issues/22)
  supplies authoritative terminal reconciliation;
  [#112](https://github.com/ThomasMichon/copilot-extensions/issues/112) supplies
  the handoff primitive;
  [#1460](https://github.com/ThomasMichon/copilot-extensions/issues/1460) and
  [#1468](https://github.com/ThomasMichon/copilot-extensions/issues/1468) own
  registered contract generations and writer fencing

## Guiding Intent

Let an attached caller wait for the next selected reason it must act without
confusing that attention boundary with transport completion, one successful
turn, or a detached scheduling promise.

The wait should survive recoverable frontend, daemon, Session Host, and target
transport churn by replaying from durable state. It should settle
deterministically at the earliest selected durable boundary, with a bounded
reason and enough identity and position to read or answer the target, while
leaving terminal reconciliation, succession authority, and contract
registration in their existing owning layers.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Delegation contract driver | Attention projection, authenticated wait API, CLI behavior, tests, docs, and landing | Isolated issue-bound worktree |
| Terminal reconciliation lane | Emits authoritative terminal evidence after unrecoverable transport loss | #22 |
| Contract foundation lane | Registers semantic generations and mixed-version writer gates | #1460 and #1468 |

## Coordination

- **Topology:** one attention projection over the existing durable event,
  session, succession, and bounded-result surfaces, plus only the narrow event
  correlation needed to make an unresolved permission answerable.
- **Host (owns PRs):** delegation contract driver.
- **Delegates:** #22 owns terminal-event creation and reachability
  classification; #112 owns successor creation; #1138 owns the general
  immutable-reference contract; #1460 owns registered session-pinned contract
  selection and default enablement. This effort consumes those authorities
  without adding a competing lifecycle, succession, or compatibility store.
- **Handoff:** land the proposal first, sync forward, then land the API/CLI
  implementation and focused validation before archiving this effort and
  reconciling the parent delegation-convergence effort.

## Context

The existing `wait` command follows the caller's delivery cursor until the
current turn settles. It already reconnects across transient daemon and session
registration gaps, flushes events before acknowledging them, and avoids
declaring completion until terminal backlog is drained. It does not yet return
a structured settlement, stop promptly for parked input or unresolved
permission, or follow a deliberate successor under an explicit compatibility
rule.

The bounded result surface added by #1452 already projects the authoritative
logical delegate, snapshot and current session identities, pending input,
attention state, latest result, and an opaque cursor-neutral position. That
position is intentionally scoped to one session event log and cannot cross a
handoff. The handoff primitive persists predecessor/successor links and emits
`session_handoff` on both streams. This effort therefore needs a distinct,
opaque attention position that names the logical delegate and the currently
observed lineage segment without becoming a mutable cursor or subscription
ledger.

The repository patterns require a right-sized, independently installable
surface, one authoritative owner per mutable fact, discovered endpoints,
cross-platform parity, and loud incompatibility. A stateless read projection
and retained HTTP/CLI invocation satisfy those constraints without introducing
a scheduler or callback service.

## Request

> Make a retained wait or subscription settle when the delegated agent needs
> caller attention, rather than only when one successful turn becomes idle.

## Plan

### Phase 1 — Define authoritative reason and position semantics

- [ ] Add one stable attention-reason enum and response model covering
      `turn_complete`, `turn_cancelled`, `failed`, `input_required`,
      `permission_required`, `unreachable`, `policy_required`,
      `contract_changed`, `stopped`, and `ended`.
- [ ] Publish a reason-source matrix naming each reason's authoritative durable
      event or row, correlation identity, withdrawal/resolution signal, restart
      behavior, bounded reference, current availability, and prerequisite
      owner.
- [ ] Define settlement as the earliest selected durable boundary after the
      supplied attention position, so retrying from the same position returns
      the same logical settlement even if current state has since advanced.
- [ ] Add a distinct opaque attention position containing the logical delegate,
      observed session/continuity/event boundary, and current lineage segment;
      do not reinterpret a session-local result position as cross-session.
- [ ] Keep settlement stateless: no subscription row, delivery cursor owner,
      lifecycle status, succession fact, or compatibility selection is added.
- [ ] Define timeout as a non-settled bounded response, not a false attention
      reason or a terminal verdict.

### Phase 2 — Add the authenticated attention wait

- [ ] Add a protocol-gated authenticated session attention endpoint that first
      scans already-durable boundaries, then performs one bounded long-poll on
      the current event log until a selected reason settles or the request
      timeout expires.
- [ ] Accept a caller-selected reason set and a cursor-neutral opaque starting
      position without advancing any delivery cursor.
- [ ] Return the observed session and exact settlement boundary needed for a
      rendering client to flush through that event before exiting.
- [ ] Make daemon/frontend reconnect a client responsibility: re-resolve and
      reissue the bounded request from the last attention position until
      recovery succeeds or authoritative terminal evidence maps to `failed` or
      `unreachable`.
- [ ] Map parked elicitation to its existing stable tool-call reference. Add a
      stable live permission-request correlation and authenticated resolver only
      for the currently unresolved request; emit durable request and resolution
      events without adding a separate permission store.

### Phase 3 — Follow compatible succession

- [ ] On `session_handoff`, resolve the authoritative successor and continue
      from the successor's event-log origin when the retained client can prove
      the successor serves the selected attention protocol.
- [ ] Give successor resolution precedence over the predecessor's later
      `stopped` transition, so a compatible handoff does not settle as a stop.
- [ ] Settle with `contract_changed`, the successor identity, and the last
      durable predecessor boundary only when client capability re-probing
      explicitly rejects the selected protocol or reason set.
- [ ] Treat an unavailable or inconclusive compatibility probe as recoverable
      transport uncertainty: retry within policy, then await authoritative
      `failed` or `unreachable` evidence rather than inventing
      `contract_changed`.
- [ ] Keep this compatibility result client-synthesized and ephemeral until
      #1460 provides registered, session-pinned generations; do not claim that a
      daemon-wide HTTP version is a durable per-session contract selection.

### Phase 4 — Make the CLI wait attention-oriented

- [ ] Refactor `agent-bridge wait` around one coordinator/state machine that
      shares the attention evaluator with the HTTP endpoint; do not run an
      independent long-poll beside the SSE delivery loop.
- [ ] In human mode, retain the low-noise SSE feed and do not exit until content
      through the settlement boundary is flushed and acknowledged. In JSON
      mode, use the cursor-neutral attention request only and never open or
      acknowledge the delivery stream.
- [ ] Add repeatable reason selection and machine-readable JSON settlement.
      Keep bare `wait` turn-only across protocol generations; require explicit
      `--attention REASON` or `--all-attention` for the new semantics until
      #1460/#1506 gate a default change.
- [ ] Preserve a tolerant legacy path for bare turn-only wait against an older
      daemon; new attention options fail before waiting when the protocol is
      unavailable.
- [ ] Render each reason distinctly and identify the current or successor
      session plus the returned durable position without exposing raw event
      protocol details.

### Phase 5 — Document and reconcile

- [ ] Update the delegation contract and agent-bridge CLI/API documentation with
      selected reasons, timeout semantics, cursor neutrality, retained versus
      detached behavior, and the temporary compatibility rule.
- [ ] Reconcile #1450 and the parent
      `agent-bridge-delegation-convergence` Phase 1B checklist after the
      implementation and validation PRs merge.
- [ ] Archive this focused effort and record the next unblocked delegation
      convergence slice.

## Validation Plan

- [ ] Focused model and route tests cover every currently authoritative
      attention reason, immediate durable settlement, selected-reason
      filtering, timeout, bounded references, deterministic retry from the same
      position, and exact JSON shape.
- [ ] CLI tests cover distinct human output for every reason and exact JSON
      output without consuming another caller's delivery cursor.
- [ ] Streaming tests prove recoverable daemon/session interruption resumes
      by reissuing from the retained position and does not settle prematurely.
- [ ] Terminal tests prove one authoritative failed/unreachable transition
      settles exactly once and later replay returns the same bounded outcome.
- [ ] Handoff tests prove a compatible successor continues the wait and an
      incompatible successor returns `contract_changed` with successor identity.
- [ ] Input and permission tests prove parked requests release the caller with
      an answerable live request reference while the underlying turn remains
      parked, and prove replay after resolution reports the original boundary
      plus current request availability honestly.
- [ ] Race tests cover attention-first, feed-first, process interruption,
      reconnect, and handoff switching without lost output or cursor movement
      for undelivered content.
- [ ] Mixed-version tests prove legacy turn-only wait remains available where
      safe and new attention options fail before waiting against an older
      daemon.
- [ ] The focused agent-bridge test selection, Python lint, version consistency,
      generated payload, and install-contract gates pass.
- [ ] Required pull-request CI remains within its fast contract lane; any
      subprocess-heavy reconnect matrix stays in focused or scheduled coverage.

## Proposal

Introduce an additive HTTP protocol generation for a deterministic attention
projection and one bounded long-poll request. The request names the session or
logical delegate, selected reasons, an optional opaque attention position, and
a bounded timeout. The response always carries:

- whether the request settled;
- the selected attention reason when settled;
- logical delegate, observed session, current session, and optional successor
  identity;
- the opaque attention position at the exact observed or settlement boundary;
- at most one bounded result, input, permission, policy, or terminal reference;
- an explicit compatibility or evidence limitation when the full reason cannot
  be proven.

The attention position is separate from a result position. It names the logical
delegate and one lineage segment's durable event boundary. A handoff advances
the segment to the successor's event-log origin; replay from a predecessor
position deterministically encounters the same handoff before scanning the
successor. The earliest selected durable fact wins. Exactly-once therefore
means deterministic logical settlement from a position, not exactly-once HTTP
delivery.

The endpoint scans durable evidence before waiting, so an already-recorded
input, permission, terminal, or completion boundary returns immediately even if
current state has advanced. It never advances a delivery cursor. A timeout
returns `settled: false` with current identity and position; it does not invent
a `timeout` attention reason. If the daemon connection itself is lost, a
retained client re-resolves the endpoint and reissues from that position.

`agent-bridge wait` remains the attached operating-system wake channel but uses
one state machine, not simultaneous SSE and long-poll consumers. Human mode
streams and acknowledges rendered content, evaluates the same pure attention
mapping after each durable event or heartbeat, and exits only after rendering
through the settlement boundary. JSON mode uses only the cursor-neutral
attention request and does not touch the delivery cursor. Bare `wait` preserves
historical turn-only behavior; explicit `--attention REASON` and
`--all-attention` opt into the new semantics until the contract/default gate
authorizes a broader default.

A handoff event redirects evaluation to the authoritative successor without
changing the logical delegate. Until registered session-pinned semantic
generations land through #1460, the retained client re-probes the successor's
serving daemon. A daemon-wide protocol mismatch is only a client-observed
incompatibility result, not a persisted claim about the successor's selected
contract. The probe is tri-state: compatible, explicitly incompatible, or
indeterminate. Only explicit incompatibility settles `contract_changed`;
indeterminate reachability or discovery failures remain inside reconnect policy
until authoritative terminal evidence exists. No provisional compatibility
fact is persisted.

The authoritative reason-source matrix begins with these ownership rules:

| Reason | Durable authority | Availability in this effort |
|--------|-------------------|-----------------------------|
| `turn_complete` | first matching `turn_complete` event and completed turn row | implemented |
| `turn_cancelled` | `turn_complete.stop_reason` that proves explicit cancellation | implemented when the event distinguishes cancellation; otherwise evidence-limited |
| `failed` | terminal failed session state plus its fatal/terminal event evidence; generic recoverable `error` events are insufficient | implemented only from explicitly terminal evidence |
| `input_required` | `ask_user_request.tool_call_id`, with later answer/withdrawal events describing current availability | implemented |
| `permission_required` | correlated `permission_request`, plus resolution event and live resolver ownership | implemented for a live unresolved request; replay reports if it is no longer answerable |
| `unreachable` | #22 terminal event explicitly classified as exhausted reachability loss | projection implemented; live production remains gated on #22 |
| `policy_required` | a dedicated durable policy-boundary event with action correlation, bounded decision reference, and resolution event | gated until such an owning event exists; never inferred from `context_critical` or generic warnings |
| `contract_changed` | client capability failure while following an explicit successor | client-synthesized until #1460 supplies session-pinned selection |
| `stopped` | durable deliberate stopped transition, except a predecessor superseded by compatible handoff | implemented |
| `ended` | durable deliberate ended transition | implemented |

## Journal

### 2026-09-01 — Proposal

- Claimed #1450 after confirming there was no public claim, active dispatch
  reservation, or matching open pull request.
- Reconciled the slice to the frozen delegation contract, the existing bounded
  result and succession surfaces, and the repository's one-owner/right-sized
  design invariants.
- Selected a deterministic earliest-boundary attention projection, a distinct
  lineage-aware position, and one coordinated HTTP/CLI evaluator. Kept terminal
  emission, scheduling, contract registration, and succession ownership outside
  the slice.
