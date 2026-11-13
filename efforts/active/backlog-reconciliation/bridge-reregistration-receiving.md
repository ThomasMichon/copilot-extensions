# Bridge re-registration receiving

Back to [Backlog Reconciliation](README.md).

## Proposed route and effect boundary

Receive #12057's complete re-registration/no-consumption contract in existing
OPEN #6394. Its target-to-delivery outcome already includes receipt stages,
stale-admission attention, exact receiver compatibility and retirement; the
current discussion explicitly names a receiver with no active poller.

This is one independently progressable failure-mode lane, not proof of a shared
cause, a new inbox owner or a completed repair. Preserve the entire current
body, all comments/history and all fourteen incumbent unchecked rows
BT1-BT7/BTV1-BTV7. Append the two rows below, making **sixteen unchecked rows**.
No incumbent byte or check state changes; no exhaustive native-case total is
asserted.

## Complete direct receiving addition

Append this complete section to the current body:

```markdown
## Re-registered live sessions and stale inbox consumption

Retain #12057 as a distinct failure mode of the existing target-to-delivery
contract. The observed live/idle receiver kept heartbeating after process
re-registration while 21 messages, including control:replay-history rows,
remained admitted over about 21 hours with no SDK acceptance, claim, delivery
or consumption. Healthy deliveries to other live sessions distinguish this
report from an established whole-daemon outage. The un-rearmed consumer loop
is a hypothesis, not a confirmed root cause.

### Direct Plan addition

- [ ] BT8 - On re-registration with a new process PID/birth under the retained
  session identity, re-arm inbox consumption through its existing owner and
  replay pending admitted messages in order. Independently detect the live,
  idle receiver with admitted rows older than a bounded age and no recent
  pull. Expose explicit attention in live-sessions list/status AND sender
  receipt reads, and emit a structured WARNING. Registration/heartbeat
  freshness alone is not consumer health; SDK acceptance, consumption and
  assistant response remain distinct. Preserve the one ledger, identity,
  ordering, attribution, idempotency, exact-target, freshness and retirement
  contracts, including the original queue controls.

### Global Validation addition

- [ ] BTV8 - Use an isolated owned re-registration/pending-message scenario
  with changed process PID/birth and the same session identity. Prove ordered
  progress for ordinary steer/queue and control:replay-history rows, truthful
  separate receipt stages, and no loss/duplication across reconnect or
  retirement. Stop consumer pulls while keeping the receiver live/idle and
  heartbeating; after the bounded age, verify list/status attention, sender
  receipt visibility and the structured WARNING, with healthy active-puller,
  empty-inbox, busy and intentionally muted controls. Do not equate a reply
  timeout, admission or process restart with delivery.

### Continuing ownership and acceptance

The existing represented-inbox, source, publisher and release custodians
remain. This independent lane neither resolves the retained nudge's
phase-versus-follow-up decision nor accepts the proposed file-drop design.
It authorizes no daemon restart, live replay, forced interruption, resource
release or second queue. Keep all existing tests/review/CI/release/authorized
adoption gates. #12057 remains open until the reviewed complete receiver is
accepted; any redundant-tracking retirement is a later separate judgment
and does not mark its technical work completed.
```

## Complete source crosswalk

| #12057 obligation | Direct receiving clause |
|---|---|
| Same session identity, new PID/process birth | BT8/BTV8 preserve the actual re-registration boundary. |
| Re-arm consumption and replay pending admitted messages in order | BT8 supplies the direct owner obligation; BTV8 verifies ordered progress without loss/duplication. |
| Ordinary steer/queue AND `control:replay-history` | Incident and BTV8 retain both ordinary and control-row coverage. |
| Live, idle, fresh heartbeat, aged admitted messages and no recent pull | BT8/BTV8 retain the complete predicate; registration freshness is not consumption health. |
| Explicit list/status attention AND sender receipt visibility | BT8/BTV8 directly require both surfaces, not just a log or an issue link. |
| Structured WARNING | BT8 requires it and BTV8 verifies it with negative controls. |
| Twenty-one messages over about twenty-one hours, healthy peers and tentative cause | Incident paragraph retains useful signatures and labels the consumer-loop explanation a hypothesis. |
| Preserve message/receipt, ordering, identity, freshness, retirement and ownership | BT8, BTV8, continuing ownership and untouched incumbent Plan/Validation retain these independently. |

## Review, actual acceptance and separate retirement

Land this complete proposal through normal review. Before an issue amendment,
refresh whole source/receiver qualification, current bodies/edit identities,
the receiver's rename history and existing owner conditions. Reconcile a
changed incumbent; read-then-write checks are not atomic provider preconditions.

If still qualified and compatible, append only the reviewed section and read
back the complete actual body, fourteen incumbent check states, new two
unchecked rows and unchanged discussion/history. Keep #6394 OPEN. Only then
separately adjudicate #12057's redundant-tracking retirement, with a specific
public explanation and verified `NOT_PLANNED` state, never technical completion.
Journal the actual effect through review.

The retained represented-inbox source/publisher, nudge phase decision and
release/adoption responsibilities stay with their existing owners. This intake
does not close #6394, decide its nudge's phase, accept a degraded file-drop
design, transfer custody or authorize live replay, restart or resource release.
