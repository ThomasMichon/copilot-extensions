# Delegated-agent contract

This document freezes the semantic baseline for controlling an agent through
agent-bridge. It separates what the bridge **does today** from the compact
delegation contract it is converging toward.

The target is native-sub-agent-like control semantics over agent-bridge's
broader execution substrate. It does not replace the existing CLI, HTTP/SSE,
ACP, Session Host, or future AHP contracts. It gives those surfaces one shared
meaning for identity, reading, steering, waiting, interruption, and retirement.

## Contract ownership

| Concern | Owner |
|---------|-------|
| Delegated-agent lifecycle, caller relationships, attention reasons, and logical-message semantics | This document and [#1449](https://github.com/ThomasMichon/copilot-extensions/issues/1449) |
| Version provenance, negotiation, tolerant readers, writer fencing, and mixed-version gates | [#1460](https://github.com/ThomasMichon/copilot-extensions/issues/1460) and [#1468](https://github.com/ThomasMichon/copilot-extensions/issues/1468) |
| Immutable event and projection references | [#1138](https://github.com/ThomasMichon/copilot-extensions/issues/1138) |
| Bounded progress/liveness inspection | [#1045](https://github.com/ThomasMichon/copilot-extensions/issues/1045) |
| Terminal reconciliation after transport loss | [#22](https://github.com/ThomasMichon/copilot-extensions/issues/22) |
| Session handoff mechanics | [#112](https://github.com/ThomasMichon/copilot-extensions/issues/112) |
| Interactive-session ownership and lease state | [#48](https://github.com/ThomasMichon/copilot-extensions/issues/48) and [#60](https://github.com/ThomasMichon/copilot-extensions/issues/60) |
| Managed worktree identity, current session head, and predecessor/successor lineage | agent-worktrees ground layer and [#84](https://github.com/ThomasMichon/copilot-extensions/issues/84) |
| AHP methods, state, capabilities, and native-host interoperability | [#1266](https://github.com/ThomasMichon/copilot-extensions/issues/1266) |

No new writer or default may rely on the target contract until its selected
generation is registered and gated through #1460/#1468.

## Current surface

The current bridge exposes session-oriented mechanisms rather than one
delegated-agent facade.

| Intent | Current CLI | Current authenticated API | Current result |
|--------|-------------|---------------------------|----------------|
| Create a fresh session | `create <agent> [prompt]` | `POST /api/v1/sessions` with `force_new` as needed | Bridge `session_id`, generated display name, session status |
| Reuse or create for this caller | `send <agent> <prompt>` | Client-side resolve/reuse followed by session creation when absent | Existing or new bridge session |
| Submit a turn | `send <session> <prompt>` | `POST /api/v1/sessions/{id}/turns` | Immediate `turn_index`, or a queued result when `queue=true` |
| Handle a busy hosted session | Default `send` refuses; `send ... --queue` preserves the turn; `send ... --force` ends/replaces it | Default prompt submission returns 409; `queue=true` persists FIFO work | Reject, queue identity/position, or a fresh unlinked session depending on the explicit mode |
| Manage pending prompts | Queue flags through `send` | Queue snapshot, delete-one, and clear-all endpoints | Durable FIFO queue maintenance |
| Observe a live turn | attached `send`, `wait`, or following `read` | `GET .../events` plus cursor acknowledgement | Collapsed event stream |
| Read accumulated events | `read`, `read --since`, `--tail`, `--range`, or `--event` | `GET .../events` or `GET .../events/range` | Event records; random access does not move the caller cursor |
| Inspect the downstream transcript without launching ACP | `peek` | Target-execution helper, not a public session route | Bounded Copilot `events.jsonl` snapshot and reuse verdict |
| Inspect current state | `status`, `session-usage`, `sessions` | Session, status, usage, queue, and cursor endpoints | Session/process/usage/progress fields |
| Interrupt one turn | control API; destructive `send --force` is a separate replacement path | `POST .../interrupt` | Current turn receives ACP `session/cancel`; session remains usable |
| Stop and preserve | `stop` | `POST .../stop` | Session becomes `stopped` and remains resumable |
| Resume | `resume` or automatic resume on later send | `POST .../resume` | Session returns to `idle` when recovery succeeds |
| End and remove | `end` | `DELETE /api/v1/sessions/{id}` | Bridge-owned session state is retired |
| Answer agent input | `answer` | `POST .../ask-user` | Parked elicitation resumes |
| Hand off context | `handoff` | `POST .../handoff` | A successor session is created and linked |
| Rebuild a damaged relay log | No CLI verb; the watchdog may trigger it internally | Authenticated `POST .../resync` | Event log is rebuilt from downstream replay and delivery cursors reset |
| Message a represented interactive session | `send <live-handle>` | `POST /api/v1/live-sessions/{id}/messages` | Durable inbox message ID; optional waited reply |

Source anchors:

- CLI grammar and behavior: `src/agent_bridge/__main__.py`.
- Public request/response models: `src/agent_bridge/models.py`.
- Hosted-session routes: `src/agent_bridge/routes/sessions.py`.
- Represented-session inbox: `src/agent_bridge/routes/live_sessions.py`.
- Lifecycle, queueing, handoff, and prompt execution:
  `src/agent_bridge/session_manager.py`.
- Durable session, event, cursor, prompt, and live-message records:
  `src/agent_bridge/db.py`.

## Current identity model

Several identifiers coexist today:

| Identity | Meaning today | Stability |
|----------|---------------|-----------|
| `agent_name` | Resolver/catalog target selected by a caller | Stable catalog name, not a conversation |
| bridge `session_id` | Primary CLI/HTTP handle for one bridge session row | Durable across stop/resume; explicit handoff creates a successor; `send --force` may instead replace a busy session without a succession link |
| ACP `session_id` | Downstream runtime's persisted conversation identity | Internal to the bridge session; may be recreated only through explicit recovery behavior |
| `worktree_id` | Ground-layer working-body identity, ownership boundary, and session-head scope | May host a succession of bridge-owned or interactive sessions |
| `caller_id` | Caller-affinity and delivery-cursor key | Stable only when the caller supplies or resolves it consistently |
| live interactive `session_id` | Extension registration for one running CLI process | Lease-bound and invalidated by expiry/takeover |
| worktree handle | Resolves the current ground-layer head for owned resume/handoff and the current registration for represented messaging | Stable cross-handoff address when the managed worktree is authoritative |

The target contract exposes a **logical delegated-agent reference** derived
from an existing authority rather than adding a rival mutable identity:

- For a managed worktree, the qualified ground-layer worktree identity and its
  current head/lineage are authoritative.
- For a target with no managed worktree, the initial bridge session is the
  lineage root and bridge-owned succession links remain authoritative.
- One logical delegate reference names one caller-visible line of work.
- Stop/resume preserves both logical delegate and bridge session identity.
- Handoff preserves the logical delegate but creates an explicit successor
  bridge session.
- The result of every control operation carries both the logical delegate ID
  and current bridge session ID.
- A caller never infers logical identity from PID, transport, host, venue, or
  ACP session ID, and the bridge does not copy the ground layer's current-head
  state into a second authority.

Until the compact reference is exposed uniformly, callers use the current
bridge `session_id` for one hosted session or the authoritative worktree handle
when they need to follow a managed succession chain. The implementation gap is
uniform projection, not invention of another lineage store.

## Current lifecycle

`SessionStatus` currently defines:

```text
created -> starting -> idle <-> running -> stopping -> stopped
                   \                    \             \
                    +--------------------+-------------> failed / ended
```

The exact reachable transitions are owned by `SessionManager`; the diagram is a
semantic summary, not a replacement state machine.

Session status, turn status, transport liveness, and caller attention are
different axes:

- **Session status** says whether the bridge session is starting, usable,
  running, stopped, failed, or ended.
- **Turn state** says whether one submitted prompt is active or settled.
- **Transport liveness** says whether the daemon, Session Host, downstream
  child, and ACP path remain reachable.
- **Attention** says whether a retained caller should be released to act.

Later implementations must not encode all four meanings in one overloaded
status enum.

## Current caller relationships

| Relationship | Current behavior | Target meaning |
|--------------|------------------|----------------|
| Attached | `send`, `wait`, or following `read` keeps a CLI process consuming the event stream | The process is an active attention subscription and may be released at a selected boundary |
| Observer | Status and random-access reads inspect durable state without owning the turn | Reads do not create a future wake-up relationship or advance another caller's cursor |
| Represented interactive peer | A registered CLI extension polls a durable inbox and may inject a `session.send` turn | Prompt control is experimental until single-stream admission is proven |
| Detached | `--no-wait` returns after creation/submission | Work continues durably, but the bridge cannot later wake a caller invocation that no longer exists |

An attached operating-system process is currently the reliable bridge from
external completion back into a host agent's scheduler. Durable state preserves
the target; it does not manufacture a callback into a disappeared caller.

## Current wait behavior

Current `wait` means **wait for the current turn to settle**:

- It follows the SSE stream and caller delivery cursor.
- It returns when the session is `idle`, `stopped`, `ended`, or `failed` and no
  unread events remain beyond the local cursor.
- Recoverable service reconnection is retried and resumes from the acknowledged
  cursor.
- `ask_user_request` and permission events are observable, but they are not a
  first-class structured wait result; a parked request may leave the turn
  running.
- A timeout returns control while the remote turn may still be running.

This is narrower than the target attention contract.

## Target attention contract

An **attention subscription** is an attached relationship with a declared set
of reasons that release the caller. It is separate from event delivery and from
session lifecycle.

The stable semantic reasons are:

| Reason | Meaning |
|--------|---------|
| `turn_complete` | The selected turn settled normally and its bounded result is available |
| `turn_cancelled` | The selected turn settled because it was explicitly interrupted |
| `failed` | The turn or logical delegate reached a non-recoverable failure |
| `input_required` | The agent is parked on an elicitation requiring caller input |
| `permission_required` | The agent is parked on a permission decision that the current host cannot resolve |
| `unreachable` | Recovery policy has exhausted a loss of reachability; a transient reconnect attempt is not this reason |
| `policy_required` | Context pressure, ownership conflict, or another declared policy requires a caller decision |
| `contract_changed` | A successor or recovered session cannot continue under the subscriber's selected contract |
| `stopped` | The session was deliberately preserved but is not currently executing |
| `ended` | The logical delegate or its current session was deliberately retired |

The observable mapping is:

| Current signal | Target attention result |
|----------------|-------------------------|
| `turn_complete` with a normal stop reason | `turn_complete` |
| `turn_complete` with a cancelled stop reason after explicit interrupt | `turn_cancelled` |
| `ask_user_request` while the turn is parked | `input_required` |
| `permission_request` while the host cannot resolve it | `permission_required` |
| `error` or terminal `failed` after the target remains reachable enough to report failure | `failed` |
| Disconnected liveness while recovery remains viable | No settlement; continue reconnect/replay |
| Exhausted reachability recovery with no authoritative terminal result | `unreachable` |
| Context/ownership/contract policy that cannot proceed automatically | `policy_required` |
| Compatible `session_handoff` | Continue on the successor without settling |
| Incompatible successor contract | `contract_changed` |
| Deliberate terminal session transition | `stopped` or `ended` |

Rules:

1. The subscriber chooses its attention-reason set; an interactive default may
   select all actionable reasons.
2. Settlement happens exactly once and carries the reason, logical delegate ID,
   current session ID, durable position, and any result/request/successor
   reference.
3. Recoverable daemon, frontend, SSH, Session Host, or ACP transport churn
   resumes inside the subscription and does not settle it.
4. Handoff carries the subscription to the successor when the selected contract
   remains compatible; otherwise it settles as `contract_changed`.
5. Observing an event does not itself advance a caller's durable cursor until
   the consumer acknowledges delivery.
6. A detached caller has no attention subscription. Later status and result
   reads must say so explicitly rather than implying a missed wake-up.
7. A queued steering operation is selected by its logical-message identity.
   When it later starts, the admission record gains the turn/current-session
   correlation that a waiter and bounded-result reader follow.

## Target logical-message idempotency

The current represented-session inbox supports an `idempotency_key`, but the
ordinary hosted-session prompt queue does not. The current database index makes
that live-message key globally unique across the table. Repeating it with the
same session, sender, body, reply target, and kind returns the original message;
reusing it for different content or a different session returns an
`idempotency_conflict`.

The current live-message key is therefore a narrower transport record mechanism,
not yet the shared logical-message contract.

The target idempotency identity is:

```text
(logical delegate ID, sender identity, caller-supplied idempotency key)
```

Rules:

1. The key is opaque to the bridge and names one logical steering operation.
2. Repeating the same tuple with the same normalized operation and payload
   returns the original admission result.
3. Reusing the tuple with different semantic content is a conflict; it never
   creates a second turn.
4. The identity survives client retry, queueing, daemon restart, stop/resume,
   and successor handoff.
5. Handoff does not mint a new idempotency scope because the logical delegate
   remains the same.
6. The record is retained while the logical delegate remains addressable and
   for at least the negotiated compatibility/rollback window after retirement.
   #1460 owns the concrete retention and migration gate.
7. Every acknowledgement identifies whether the message started immediately or
   queued, plus its logical-message, queue, turn, and current-session identity
   when known.

## Current-to-target delta

| Target capability | Current foundation | Remaining owner |
|-------------------|--------------------|-----------------|
| Stable lifecycle/attention vocabulary | Session statuses, events, queues, cursors, handoff links | [#1449](https://github.com/ThomasMichon/copilot-extensions/issues/1449), completed by this baseline |
| Bounded accumulated result | Status plus event/range reads | [#1452](https://github.com/ThomasMichon/copilot-extensions/issues/1452) |
| Attention-oriented wait | Turn-settlement stream | [#1450](https://github.com/ThomasMichon/copilot-extensions/issues/1450) |
| Queue-first steering | Opt-in hosted queue and separate live inbox | [#1451](https://github.com/ThomasMichon/copilot-extensions/issues/1451) |
| Safe represented-session admission | Freshness, takeover fencing, inbox idempotency | [#1453](https://github.com/ThomasMichon/copilot-extensions/issues/1453) |
| Compact task-shaped facade | Existing individual CLI/API verbs | [#1506](https://github.com/ThomasMichon/copilot-extensions/issues/1506) |
| AHP projection | AHP convergence plan | [#1454](https://github.com/ThomasMichon/copilot-extensions/issues/1454) and [#1266](https://github.com/ThomasMichon/copilot-extensions/issues/1266) |
| Contract registration and mixed-version safety | Existing HTTP generation and compatibility tests | [#1460](https://github.com/ThomasMichon/copilot-extensions/issues/1460) and [#1468](https://github.com/ThomasMichon/copilot-extensions/issues/1468) |

## Compatibility posture

- This baseline changes no current command, endpoint, state writer, or default.
- Existing bridge session IDs and event cursors retain their current meanings.
- Most new semantics are additive and capability-gated until #1460's
  reader-first and mixed-version gates permit a writer.
- Rescoping live-message idempotency from one global key to
  `(logical delegate, sender, key)`, and carrying it across a successor, requires
  an explicit #1460-owned index/data migration and compatibility adapter. It is
  not a purely additive writer change.
- A session pins its selected contract for its lifetime; a successor negotiates
  and records its own compatible selection.
- AHP remains a separate upstream host contract and ACP remains a downstream
  agent contract. Both may carry this semantic model without being renamed or
  flattened into it.
- Reduced-fidelity targets report unsupported control or missing evidence
  explicitly instead of returning a success-shaped omission.
