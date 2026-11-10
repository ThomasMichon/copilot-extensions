# Bridge delivery observation and coalesced status

- **Slug:** `bridge-delivery-status`
- **Repo:** `ThomasMichon/copilot-extensions`
- **Created:** 2026-11-07
- **Status:** Active
- **Umbrella issue:** [#11448](https://github.com/ThomasMichon/copilot-extensions/issues/11448)
- **Related:** #6394, #4023; independent registration #11445 and roster #11298

## Guiding Intent

Let agents observe exact model-facing message consumption independently of a
target's turn completion, and subscribe to selected agents' attention/status
changes without transcript sweeps or a steering message per event.

This closes the Bridge vision's
[delegation control and attention boundary](../../../visions/plugins/agent-bridge/README.md#delegation-control-and-attention-boundary)
and ownership-clear event view. It follows
[version-skew-safe contracts](../../../docs/patterns/service-contract-evolution.md)
and the existing service's lifecycle rather than introducing another daemon.

## Participants

| Participant | Role | Reached via |
|-------------|------|-------------|
| bridge-status-owner | Accountable source owner; proposal, implementation, review and merge | Issue #11448 and resulting PRs |
| subscription-worker | Bounded source delegate: Phase C and remote source composition; no rollout | Parent owner and issue #11448 |
| adopting-supervisor | Consumer acceptance after separately authorized installation | Public contract feedback on #11448; adopter-owned deployment |

## Coordination

- **Topology:** dependency-ordered, individually green PRs.
- **Host:** bridge-status-owner.
- **Delegates:** subscription-worker owns independent host feed/coalescing source
  in an isolated worktree; the host retains delivery receipts and final acceptance.
- **Ownership:** #6394 retains original inbox repair and its existing
  follow-ups; #11445 retains registration recovery; #11298 retains passive
  roster freshness. Coordinate overlapping source instead of duplicating work.
- **Completion:** source through merge plus qualified consumer evidence, or
  explicit tracked transfer of withheld-deployment acceptance. No rollout,
  session repair or permission answering is authorized by this plan.

## Request

Public-safe capture of the requested behavior; deployment-specific identifying
context is omitted. The following excerpts preserve the requested constraints:

> calling agents really want to know that the steer has been sent to the LLM, not just received in the inbox by the extension.
>
> We'll need a way to avoid being super-spammy: one notification that "watched agents have new status updates" and then a file/DB pointer at the recent feed of events should suffice. We don't need a steer message per other-agent event.
>
> We want to avoid thrashing SSH or CPU on target devices, to ensure we can leverage SSE from the remote daemon services as much as possible

**Summary (not a verbatim replacement):** investigate receipts and actual SDK
hooks before designing; expose pending-question/permission blocking rather than
indefinite SDK acceptance; reshape delivery waiting for continuously running
agents; register interest in question, idle/turn-end, consumption, stalled and
error events across hosts; share one host stream; coalesce unread notices with
a feed pointer; preserve a normalized layer a future federated Bridge can serve.

## Context

Source investigation established:

- The existing inbox persists `admitted`, `sdk_accepted` and `consumed`.
  Consumption matches a root attributed `user.message` to an envelope message ID.
  Delivery defaults to non-aborting immediate steering; queue and interrupt
  remain explicit. Consume-before-send-return is already handled.
- The native runtime removed SDK callback hooks. Existing extensions observe
  session events instead of registering `onUserPromptSubmitted`.
- Current native SDK declarations describe transformed user input as
  model-facing and `turnId` as the loop turn that consumed it. This is a
  mechanical boundary, not semantic comprehension or network-transmission
  proof. Do not label SDK acceptance or unrelated assistant output as delivery.
- Live sends return an admission receipt by default; `--wait-reply` separately
  waits finitely for post-consumption output. A delivery wait must not wait for
  an entire continuously running turn.
- Represented session events already feed read-only SSE and turn/progress
  projections. Those tails are process-lifetime evidence. Fresh native question
  snapshots exist; represented permissions stay read-only.
- Remote per-session and multiplexed SSE already exists. It is not yet a
  retained normalized host-wide status subscription with unread coalescing.

## Proposed Contract

The following details are **agent-recommended realization**, subject to source
and review findings; they do not extend action or deployment authority.

- Keep monotonic receipt facts and reversible current delivery attention
  separate. Fresh question/permission state may explain an unconsumed receipt;
  stale or absent state must explicitly remain unknown. Completed consumption
  must not regress when another question opens.
- Add an explicit finite `send --wait` delivery-boundary option; preserve
  admission-by-default and separate `--wait-reply`. An accepted timeout or
  blocked observation never invites a resend. Unsupported new semantics are
  refused before admission, not silently downgraded.
- Reuse exact native event identity and the one existing inbox ledger. Prefer
  ID correlation; do not content-match arbitrary operator text.
- Publish normalized metadata-only status transitions from the authoritative
  event paths, never scrape transcripts. Keep question content in the owning
  detail API. Preserve root/sub-agent distinctions and evidence availability.
- Retain bounded feed records and watcher cursors in the existing Bridge
  database. An explicit replay gap requires reconciliation rather than silently
  returning an empty history.
- One host feed stream serves all chosen agents. Reuse existing authenticated
  carrier admission for remote streams, with provider-first capability checks.
  Local subscription state fans out to watchers without one target stream or
  polling subprocess per watched agent.
- An unread notification is an existing inbox message with a feed pointer.
  Further matching transitions update the retained feed, not the inbox.
  Reading a feed page alone is cursor-neutral; explicit acknowledgement rearms
  notification after the acknowledged range. A concurrent new event must remain
  unread. Notification consumption alone is not acknowledgement of feed contents.
- Bound retained records, selected agents, subscribers, queues and reconnect
  buffers. Overflow and unavailable hosts remain explicit. Stalled is a silence
  observation, never authority to cancel or recreate.

## Plan

### Phase A - Confirm event and receipt seams

- [x] Establish receipt, native hook, represented SSE and remote multiplexing baseline.
- [x] Complete source mapping of host fanout, pending attention and selected-runtime compatibility seams.
- [x] Record the exact supported model-facing consumption guarantee and any unavailable stronger signal.

### Phase B - Delivery observation

- [x] Expose freshness-qualified current question/permission attention beside exact receipts.
- [x] Add finite delivery waiting and strict CLI/current-prior capability behavior.
- [x] Preserve default admission, reply waits, idempotency, exact targeting and retained-inbox ownership.
- [x] Merge the independently green provider/consumer slice after required review and checks: #11496, merged to dev; not deployed.

### Phase C - Host feed and coalescing

- [x] Add normalized bounded durable status transitions to existing authoritative producers.
- [x] Add selected-agent/event subscriptions, cursor-neutral reads and explicit acknowledgement.
- [x] Admit at most one unread existing-inbox notice; qualify race-safe rearming.
- [ ] Merge the local complete consumer flow after required review and checks.

### Phase D - Remote composition and adoption

- [ ] Compose one authenticated normalized host stream per selected remote daemon, not per agent.
- [ ] Qualify reconnect/replay gaps, rollback and bounded backpressure without SSH/transcript sweeps.
- [ ] Document consumer operation and obtain separately authorized installed evidence, or transfer it to a named tracked objective.

## Validation Plan

- [ ] Re-read public capture against the request: no weakened coalescing, push, cost or authority constraint.
- [x] Land this proposal through review before implementing new behavior.
- [x] Delivery controls cover exact receipt ordering, consume-before-return, duplicate/nested/unrelated events, stale attention and blocked/unblocked transitions.
- [x] Real parser/HTTP/extension fixtures prove finite delivery waits, no false delivery or reply, and unsupported-contract refusal before effects across supported skew/rollback.
- [x] Fresh delivery database/extension fixtures prove first-touch behavior, not only reconciliation.
- [ ] Coalescing tests prove one unread notice, cursor-neutral paging, partial/full acknowledgement, concurrent-event rearm and restart recovery.
- [ ] Host stream tests prove selection/root filtering, unavailable evidence, bounded queues, replay gaps and one stream per host.
- [ ] Required local lint, source contract checks, review and exact-head CI pass for each implementation PR.
- [ ] Consumer evidence identifies source/runtime generation and actual operations; release and deployment are reported separately.
- [ ] Resolve or explicitly transfer every Plan/Validation item before Status: Done.

## Proposal

Expand providers before clients. Reuse the current Bridge, inbox, database,
carrier and session event paths; do not add another service or authority plane.
Each implementation PR includes documentation/contract impact and its actual
validation evidence, including honest reasons for unavailable native lanes.

## Journal

### 2026-11-07 - Subscription prerequisite integration

- Receipt source #11496 and registration identity recovery #11508 are merged
  to dev. The subscription slice preserves their serving state, contract owners,
  probe portfolio and retained-extension VM linker repairs.
- Source qualification now proceeds as a production draft PR, followed by
  canonical capture against its public head. Installed adoption, promotion
  and deployment remain separate acceptance work under #11448.
- Integrated qualification passed 64 isolated Python controls and the retained
  extension VM lane with experimental module support: seven passed, zero
  skipped. Three subsequent targeted controls qualify the generated-notice
  feedback exclusion while preserving ordinary root receipt consumption.

### 2026-11-07 - Delivery source qualified before publication

- Integrated the newer mux improvements and preserved their upstream evidence.
  Exact public checkpoint `28529938b1` passed capture and independent no-write
  replay, each with all 337 required cases. Receipt production code is unchanged
  from the earlier approved source. The contract checker passed with two
  contracts and 1,073 fixtures; fresh review and CI still gate merge.
- Genuine HTTP-52 capture and a separate no-write replay each passed all 334
  required cases against source checkpoint `4e247f0760`.
  The contract checker passed with two contracts and 1,073 fixtures.
- The complete portfolio exposed a portable receiving-bundle omission; the
  new extension module is now in the canonical packaged-assets manifest.
  Both joined portable consumers pass, alongside 18 delivery controls.
- Fifteen capture transport/binding controls retain complete-portfolio,
  finite aggregate-budget and actual CLI launch/entry/parent verification.
  The Windows module reader uses the shared direct-interpreter convention,
  preserving its selected venv without a redirector ancestry gap.
- Source qualification is not an installed release, review, merge or deployment.
  Subscription publication and separately authorized adoption remain open.

### 2026-11-07 - Proposal reviewed; delivery implementation

- #11449 merged after Copilot approval and required green checks.
- Transitioned source owner from Phase A to Phase B. A bounded source delegate
  owns independent Phase C/remote composition; the parent retains completion.
- Delivery observations use existing mechanical consumption, not an unavailable
  model-transmission callback. Fresh metadata-only attention is separate from
  durable receipts; joining mid-turn preserves unknown absence.
- Initial implementation is local-only; qualified remote delivery waiting remains
  explicit unsupported-before-send pending its separate provider composition.
- Focused testing exposed old HTTP test adapters missing endpoint-pin arguments;
  repair retains and asserts that guard instead of disabling it.

### 2026-11-07 - Subscription source slice

- subscription-worker takes Phase C host feed/coalescing and Phase D source
  composition, independently of delivery observation APIs.
- This slice closes the Bridge delegation control and attention boundary.
  Existing inbox delivery, registration recovery and roster freshness keep
  their existing owners. No installation or consumer adoption is claimed.
- Implemented the local durable metadata feed, selected subscriptions, explicit
  acknowledgement and inbox-transaction notice coalescing, plus one shared
  authenticated host carrier stream per selected remote daemon.
- The focused source controls pass: 39 HTTP/client/carrier cases, including
  fresh storage, retention gaps, restart, nested filtering, race-safe
  acknowledgement, stale inbox refusal and provider rollback. An earlier
  coupled inbox/event packet passed 148 tests.
- First-touch schema/adoption controls pass. Candidate integration prepares its
  idle task outside the routing fence and loads static routes before transfer
  deadlines; focused authenticated serving and actual native IPC controls pass.
  Windows native deadline variability was investigated with stage probes, not
  hidden by changing production deadlines. Full installed adoption remains
  unqualified.
- Native installed/remote adoption remains with bridge-status-owner under
  #11448; these isolated source results do not authorize deployment.

### 2026-11-07 - Initial proposal

- Filed #11448 after searching existing receipt/subscription coordination.
- Preserved #6394's baseline and independent #11445/#11298 owners.
- Native declarations and existing extension source disprove reliance on an
  `onUserPromptSubmitted` callback and limit the delivery guarantee to observed
  model-facing agent-loop consumption unless stronger evidence becomes available.
