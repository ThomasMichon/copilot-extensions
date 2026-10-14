# Phase 6: ACP fidelity and SDK driving feasibility

Parent: [CLI-Default Bridging](README.md).
Tracking: [#6647](https://github.com/ThomasMichon/copilot-extensions/issues/6647).

This is an investigation, not authorization to migrate agent-bridge or change
its default. Its recommendations are agent-recommended. Phase 5's existing
validation gate and Phase 6's independence from that gate remain unchanged.

## Evidence boundary

The receiver investigation used the development source containing commits
`235fb089965cb829cab8a2117f949e6c8f4b4729` and
`d42feed500523d37c9b1cf8d436f9b798bb78792`, plus the installed
`agent-client-protocol` **0.12.1** dependency. Those public changes landed as
[#5279](https://github.com/ThomasMichon/copilot-extensions/pull/5279) and
[#5666](https://github.com/ThomasMichon/copilot-extensions/pull/5666), after
the initial Phase 6 investigation was planned.

Receiver behavior, an available SDK API, and native sender behavior are
different evidence categories. Synthetic notifications prove routing and
interpretation, not that a particular native CLI emits an event, honors a
subscription, or delivers its terminal state without loss. No native ACP
wire capture or native-version-floor validation is claimed here.

## Track (a): widen the existing ACP pipe

### The pipe already exists, for a narrower purpose

The original Phase 6 premise is partly superseded:

- [`acp_subagents.py`](../../../plugins/agent-bridge/src/agent_bridge/acp_subagents.py)
  defines `COPILOT_SESSION_EVENT_METHOD` as
  `github.com/copilot/sessionEvent`, requests a bounded event-type list
  through `initialize.clientCapabilities._meta["github.com/copilot"].events`,
  and retains top-level request metadata for compatibility.
- `SubagentAttribution.install_route` registers that exact notification on
  the ACP connection's router. The method is **not** underscore-prefixed;
  it uses an explicit `Route`, not the generic ACP extension-method hook.
- `SubagentAttribution._process_raw_event` records
  `subagent_started`, `subagent_completed`, and `subagent_failed` events.
  `tool_call_fields` also reads a tool update's own
  `_meta["github.com/copilot"].agentId`.

The existing implementation and its compatibility behavior are documented in
[agent-bridge architecture](../../../plugins/agent-bridge/docs/architecture.md#session-management).
The exact source symbols above are the evidence anchors if section names move.
There is no reason to invent another notification namespace merely to prove
that vendor data can cross ACP.

There is also public native-sender evidence, distinct from this investigation's
synthetic probe:
[#5660](https://github.com/ThomasMichon/copilot-extensions/issues/5660) reports
direct ACP probes on **Copilot CLI 1.0.93**. Its reporter observed no raw events
with top-level metadata and observed raw notifications with metadata inside
`clientCapabilities`. This supports reusing the feed; it does not prove a
minimum supported version, idle-event coverage, or lossless state delivery.

### What is still missing

The current subscription requests six attribution-related event types. It does
**not** request or interpret `assistant.idle`, `session.idle`, or
`session.background_tasks_changed`. Receiving a synthetic notification with
one of those types through the registered route produces no bridge event.

More importantly, the existing feed is deliberately **best-effort attribution**:
its source and architecture documentation allow absent or dropped mirrored
events, including under backpressure. Invalid-parameter rejection retries
without the subscription; an agent that ignores it can simply produce no raw
events. That is an acceptable loss of optional attribution, but not evidence
that a running session is safe to tear down.

The authoritative-state question remains separate:

- `AcpClient._scan_background_tasks` still populates the active-background
  map from terminal tool-result text. Raw lifecycle attribution does not
  update that map.
- `AcpClient._maybe_open_out_of_turn` and `_settle_out_of_turn` still bracket
  late content with a synthesized running/idle boundary.
- Root-loop idle, session-wide idle, a background sub-agent becoming parked,
  and an attached shell still running are different states. A sub-agent
  lifecycle event alone cannot settle all four.

These are findings about this receiver, not a claim that native ACP traffic
cannot already carry additional events.

### Metadata placement matters

`_BridgeClientImpl.session_update` still ignores its enclosing notification's
`**kwargs`. In ACP 0.12.1, `MessageRouter._make_func` expands the enclosing
notification's `_meta` into those kwargs.

That does **not** mean all update metadata is discarded: metadata nested on the
typed `update` remains in `update.field_meta`, which the current attribution
code already consumes. A sender/receiver proposal must specify the exact
envelope level, namespace, and schema; adding `_meta` at an arbitrary level
does not establish interoperability.

The other available mechanism remains valid:
`MessageRouter.handle_extension_notification` handles underscore-prefixed
methods, and `MessageRouter.__call__` strips the first underscore before
invoking that handler. A new method would require matching sender support
and receiver registration. It is an alternative, not something required
merely because ACP is the transport.

### Recommendation and implementation boundary

**Go for a bounded ACP-state experiment; no-go for deleting the heuristics
or changing teardown authority yet.** Start with the existing vendor feed.
Determine which needed event types the supported native versions actually
honor before proposing another sender API.

Before using that feed for lifecycle decisions, a separately reviewed change
must establish:

1. **Capability and version evidence:** distinguish a successfully accepted
   request from a supported state contract. Silence is unknown, never proof
   of no work.
2. **Explicit state semantics:** distinguish root-loop idle from session-wide
   idle and account for attached shells, background-agent reactivation,
   parked interactions, cancellation, and failures.
3. **Continuity or reconciliation:** prove authoritative delivery, or provide
   a gap-detectable stream and a fresh state snapshot after drops, reconnects,
   or restart. Existing best-effort attribution is insufficient on its own.
4. **Safe reduction:** scope state to the correct session and generation;
   prevent replay or stale completion from clearing newer work. Preserve
   conservative behavior for unsupported versions and unknown state.

A native sender change is conditional on those experiments: it may be needed
for additional event coverage or delivery/snapshot guarantees, but the present
evidence does not establish that every desired event needs a new sender
implementation. Receiver work is definitely still needed for idle/background
state interpretation, whatever sender shape is selected.

### Validation performed and remaining proof

An in-process probe used the actual ACP 0.12.1 router/schema and current
`acp_subagents.py`. All five assertions passed:

| Assertion | Result |
|-----------|--------|
| Enclosing notification metadata reaches callback kwargs | Confirmed |
| Nested update metadata remains on the typed update | Confirmed |
| The registered vendor route dispatches synthetic sub-agent lifecycle data | Confirmed |
| Current idle/background-state notifications are ignored | Confirmed |
| Generic underscore extension dispatch removes its prefix | Confirmed |

Existing reproducible receiver coverage is in
[`test_acp_subagent_attribution.py`](../../../plugins/agent-bridge/tests/test_acp_subagent_attribution.py),
particularly `test_session_event_route_dispatches_notification`,
`test_initialize_requests_raw_event_feed_in_client_capabilities`, and
`test_tool_call_tagged_from_update_meta`. This investigation did not rerun the
plugin suite or claim that those tests establish native sender behavior.

The next experiment must capture a synthetic workload's native ACP stream at
an explicitly recorded native version: ordinary turn completion; main-loop
idle while a background agent or attached shell runs; background completion
and reactivation; cancellation; replay/resume; and a deliberately slow
consumer. Compare observed state against the SDK reference and verify the
unknown/reconciliation path, not just the happy-path event names.

## Track (b): an SDK-backed driving adapter

### API evidence, not an already-proven migration

The inspected distribution was **Copilot CLI 1.0.88**, build `52f75603`,
whose bundled SDK source identifies **`@github/copilot-sdk`
1.0.14-preview.1**, protocol version 3. These identify the inspected pair;
they are not a supported-version selection for this effort or proof about
another venue.

Within that distribution, `copilot-sdk/` supplies these evidence anchors:

| Capability | SDK evidence |
|------------|--------------|
| Create an owned session | `CopilotClient.createSession` in `index.js`; `SessionConfig` in `types.d.ts` |
| Resume a session | `CopilotClient.resumeSession`, including `continuePendingWork` |
| Join the inherited interactive session | `joinSession` in `extension.js`; requires inherited `SESSION_ID` |
| Distinguish loop-idle from session-idle | `AssistantIdleEvent` and `IdleEvent` in `generated/session-events.d.ts` |
| Observe sub-agent lifecycle | `SubagentStartedEvent`, `SubagentCompletedEvent`, and `SubagentFailedEvent` |
| Reconcile task observations | `session.rpc.tasks.list()` in `generated/rpc.d.ts`, an experimental API |
| Receive task-list invalidation | `BackgroundTasksChangedEvent`: ephemeral, with **empty data**, not a task inventory |
| Register permissions and questions | `SessionConfig.onPermissionRequest`, `onUserInputRequest`, and `onElicitationRequest` |
| Connect without ACP | `RuntimeConnection.forStdio`, `forTcp`, and `forUri` in `types.d.ts` |
| Send, observe, detach, abort | `CopilotSession.send`, `on`, `getEvents`, `disconnect`, and `abort` in `session.d.ts` |

Those APIs make a bounded adapter proof plausible. They do not prove reliable
native emission, interactive-controller takeover, or cross-version callback
ownership. In particular:

- `session.background_tasks_changed` tells a consumer to refresh state; it
  does not itself provide that state. The current remote driver forwards
  events but does not expose `tasks.list()` through its HTTP interface.
- The task inventory includes agent, client, and shell tasks. `idle` can mean
  waiting for input rather than terminal, and client tasks can be `orphaned`
  with unknown external execution state. Shell `attachmentMode` distinguishes
  attached and detached PTYs, not independently proven idle-blocking
  semantics. Processing and queued work have additional state surfaces.
  An empty task list alone is therefore not safe-to-reap authority.
- `sendAndWait` waits for session-wide idle in the inspected implementation.
  Blindly substituting it for bridge prompt completion changes behavior while
  background work remains active; preserve the distinction deliberately.
- `disconnect` calls session detach. It is not an equivalent of owned runtime
  shutdown, `/exit`, or deletion of a session's persisted history.
- Legacy user input requires answer text; structured elicitation has
  accept/decline/cancel. Existence of those creation/resume callbacks does not
  prove that a later joining extension can take ownership of another
  controller's pending interaction.

### Preserve the Session Host boundary

The present driver is concretely `AcpClient`, including result accumulation,
pending input, process identity, and background-state access. Relevant bridge
boundaries are:

- [`session_host_connection.py`](../../../plugins/agent-bridge/src/agent_bridge/session_host_connection.py):
  `_connect_via_session_host` spawns/attaches and creates or loads the ACP
  session.
- [`session_prompts.py`](../../../plugins/agent-bridge/src/agent_bridge/session_prompts.py):
  `_run_prompt` records the result and the bridge's terminal boundary.
- [`session_lifecycle.py`](../../../plugins/agent-bridge/src/agent_bridge/session_lifecycle.py):
  pending-input resolution, explicit stop versus redeploy detach, and owned
  termination.
- [`session_host/host.py`](../../../plugins/agent-bridge/src/agent_bridge/session_host/host.py)
  and
  [`host_index.py`](../../../plugins/agent-bridge/src/agent_bridge/session_host/host_index.py):
  independent child lifetime, sequenced replay/acknowledgment, and generation
  claims.

An SDK client connecting directly from the bridge daemon is not a drop-in
replacement for that independent recorder and ownership boundary. Host frame
inspection also recognizes ACP `session/prompt` requests; merely changing
the child's argv would not preserve its semantics.

**Agent-recommended adapter sketch:** a survivable, venue-local SDK adapter
owns the runtime subscription and exposes typed create/resume/send/abort,
correlated permission/input operations, state snapshots, non-destructive
detach, and deliberate termination. Keep bridge HTTP/SSE, turn persistence,
admission, generation claims, credential-relay supervision, and remote
recovery. Give adapter events a stable sequence/cursor and reconcile
ephemeral state after a gap. Reuse Host ownership/spawner concepts, but review
the adapter protocol explicitly rather than treating raw ACP frames as generic
SDK messages.

A Node adapter has direct evidence from the inspected bundled SDK. A Python
adapter is a candidate, not an established dependency: the public
[Python SDK documentation](https://github.com/github/copilot-sdk/blob/main/python/README.md)
describes create/resume/events and transports, but no pinned Python package
was installed or parity-tested in this investigation. Select the language only
after comparing pinned contracts and deployment cost. Avoid making an
experimental in-process FFI path the initial proof.

Driving an **existing mux-hosted interactive session** is a separate path.
`joinSession` joins that inherited session; the CLI still owns launch,
terminal behavior, and process exit. SDK events do not provide exclusive
remote-driver claims, safe human takeover, or a `/clear`/`/exit` lifecycle
bridge. Phase 2 and Phase 3 remain necessary either way.

### Which structural problems change?

| Concern | SDK migration effect |
|---------|----------------------|
| Idle/background reconstruction | Native types and task-query APIs offer a stronger source, subject to delivery and reconciliation proof |
| Structured sub-agent attribution | Already partly available over the current ACP vendor feed; not a uniquely new SDK benefit |
| Startup/resume latency | Unknown; no comparative measurement, and the SDK still starts/connects and creates/resumes a runtime |
| Bridge restart and replay | Not solved by transport selection; retain independent recording and reconcile ephemeral state |
| Single driver and stale mutation | Not solved; preserve generation fencing and implement Phase 2's separate mux-driver arbitration |
| Permissions and elicitation | API hooks exist; creator/joiner ownership and pending-request recovery still require native proof |
| Cross-venue credentials and cleanup | Provider claims, relay/forwards, dormancy, and owned descendant cleanup remain bridge/venue obligations |
| Protocol/version skew | Changes shape rather than disappears; pin and test the SDK/native API pair |

The remote driver's queue is bounded and can drop old events; its forward-only
SSE surface is not an authoritative replay mechanism. SDK event availability
does not repair that independently. SDK `stop()` also does not establish the
same descendant-cleanup guarantee as the bridge's owned process-tree teardown.

### Concrete compatibility prerequisite

The research also found a separate defect in the baseline remote-driver adapter,
tracked by [#6664](https://github.com/ThomasMichon/copilot-extensions/issues/6664):
it called `session.send({content, mode})`, while the inspected SDK constructs
the request from `MessageOptions.prompt`.

A synthetic probe imported the actual bundled `CopilotSession` and used a
fake RPC connection without launching a runtime. All four cases confirmed
the boundary behavior:

| Input to the actual SDK | Constructed request |
|-------------------------|---------------------|
| `{content, mode}` | No prompt; `content` is not serialized either |
| `{prompt, mode}` | Prompt preserved |
| String overload | String normalized to prompt |
| Both content and prompt | Prompt wins; content is ignored |

That is a confirmed request-construction defect for the inspected pair, not a
reproduced native rejection. The adapter correction and send/steer regression
coverage subsequently merged to `dev` in
[#6669](https://github.com/ThomasMichon/copilot-extensions/pull/6669), preserving
HTTP `content` input. This does not establish a release, rollout, or native
version floor. An accepted HTTP request by itself does not prove that the SDK
delivered its text. Historical launch-presence results remain historical
results, not a compatibility guarantee for every SDK/native pair.

### Recommendation and separate-effort boundary

**Go for a separately reviewed SDK-adapter feasibility proof; no-go for
production migration inside this effort.** Do not replace ACP merely to obtain
structured sub-agent events that already have a narrower route. Compare
authoritative idle/task-state coverage and recovery against track (a).

The follow-on would need pinned-version API tests; real create/resume,
send/abort and interaction tests; bridge crash/cutover survival; generation
fencing; replay plus ephemeral-state reconciliation; explicit detach/stop/end
and descendant-cleanup tests; timing comparisons; and only then authorized
cross-venue parity. Joined interactive ownership must be tested separately
from SDK-owned headless sessions.

No adapter, Python dependency, native runtime, or external venue was installed
or launched by this research. These findings do not close Phase 1's version
floor, Phase 2-4, or any default-promotion validation track.
