# AHP Compatibility Baseline

This document records the initial protocol-to-implementation comparison for the
[`agent-bridge-ahp-convergence`](README.md) effort. It is a planning baseline,
not a claim that agent-bridge currently implements AHP.

## Executive decision

agent-bridge is the correct place to expose an AHP host because it already owns
the durable session runtime, event storage, target resolution, reconnectable
delivery, and downstream ACP connections. The existing components provide a
credible substrate, but no current surface is an AHP implementation.

The required work is a durable protocol and shared-state layer, not a rename of
the REST API:

- **AHP upstream:** client-to-host discovery, capabilities, shared state,
  subscriptions, ordering, replay, tools, resources, and interaction.
- **ACP downstream:** host-to-agent runtime control.
- **Workspace providers below:** optional directory/workspace creation,
  identity, occupancy, and cleanup.
- **Clients above:** user interfaces, CLIs, IDEs, and automation consumers.

## Protocol baseline

The first target is the released
[AHP 0.8.0 tag](https://github.com/microsoft/agent-host-protocol/tree/v0.8.0).
It is explicitly a working draft. AHP 0.9.0 was released after this target was
reviewed; the first implementation slice deliberately stays on 0.8.0. The host
must negotiate a version and keep version-specific schemas isolated, and no
0.9.0 or later behavior may enter the 0.8.0 codec without a separate
compatibility review.

Phase 0 freezes the tagged generated JSON schemas, exported method maps, and
reducer fixtures as wire-compatibility inputs, with tagged prose supplying
behavioral intent. Any disagreement blocks implementation until the effort
records a deliberate compatibility decision. One known example already needs
that treatment: the tagged chat-channel prose says no `disposeChat` command
exists while the tagged command types expose one.

The frozen corpus, SHA-256 manifest, complete method classification, named
capability and state-prerequisite policy, and exception ledger are maintained
under
[`plugins/agent-bridge/tests/fixtures/ahp/v0.8.0/`](../../../plugins/agent-bridge/tests/fixtures/ahp/v0.8.0/).

AHP requires reliable ordered bidirectional delivery, but does not mandate
WebSocket, TCP, process discovery, a graphical client, Git worktrees, task
scheduling, mux processes, or federation. Those are host implementation choices
or extensions.

## Component roles

| Component | Role in an AHP architecture |
|-----------|-----------------------------|
| agent-bridge | AHP host and downstream ACP controller for bridge-owned resources; proxy/federator for native-owned resources |
| Session Host | Internal lifetime-preserving transport for ACP frames; never advertised as AHP |
| workspace provider | Supplies optional project/directory/workspace facts and lifecycle |
| native local host | Authoritative AHP host for the local sessions and primitives it owns |
| CLI/UI client | AHP client; presentation and operator interaction, not session authority |
| task scheduler | Optional extension or later automation-channel consumer, not AHP 0.8 core |

## Requirements matrix

The classifications are:

- **available through composition** - sufficient underlying semantics exist but
  need a real AHP projection and contract;
- **partially implemented** - an analogous surface exists with material semantic
  gaps;
- **extension-only** - useful bridge behavior outside AHP core;
- **missing** - no credible current implementation.

No row is currently **native/current AHP**.

| AHP area | Current classification | Evidence and required reconciliation |
|----------|------------------------|--------------------------------------|
| JSON-RPC framing and channels | **missing** | Existing surfaces are REST/SSE, ACP stdio/WebSocket, and a private Session Host envelope. Add channel-addressed AHP JSON-RPC. |
| Initialization and versioning | **missing** | Bridge HTTP and Session Host versions are independent. Add `initialize`, offered-version selection, implementation metadata, client capabilities, exact named agent capabilities, subscriptions, snapshots, and `serverSeq`. |
| Root agent discovery | **available through composition** | `routes/agents.py` and provider manifests already expose agents, models, venues, trust, readiness, and capabilities. Project them into authoritative root state. |
| Endpoint/process discovery | **extension-only** | AHP starts after transport establishment. Keep service endpoint discovery and process supervision in existing plugin patterns. |
| Working directories | **available through composition** | Target/workspace resolution supplies cwd, project, and workspace identity. Add URI-addressed access grants without requiring Git; more than one directory and directory-set mutation require the exact `multipleWorkingDirectories` capability. |
| Session creation/catalog | **partially implemented** | Bridge can create/list/end durable sessions. Add client-chosen AHP URIs, duplicate rejection, asynchronous ready/failure, paginated summaries, and subscribe-to-load. |
| Session continuity | **partially implemented** | Bridge ID, ACP ID, target, and workspace ID are separate. Define a stable mapping and prohibit recreation of a new ACP conversation beneath an unchanged AHP identity. |
| Reconnect | **partially implemented** | Existing per-session cursors and resume operations are useful internals. AHP reconnect uses logical `clientId`, `lastSeenServerSeq`, subscriptions, replay, or fresh snapshots. |
| Session/chat fork | **missing** | The upstream ACP adapter does not support fork. Every ready session still needs a catalogued `defaultChat`; additional chats require `multipleChats`, and fork/side-chat forms require their exact sub-capabilities. |
| Chats and turns | **partially implemented** | Turns and events exist, but no first-class AHP default-chat state, action origin, ordered response-part stream, or reconciliation model exists. |
| Rich prompt content | **missing** | `acp_agent.py` accepts rich blocks but `_extract_text` discards non-text content; HTTP turns are text-only. Preserve supported blocks or reject them explicitly. |
| Resources and lazy content | **missing** | No AHP resource operation or `ContentRef` surface exists. Add the standard read/write/list/watch and content-retrieval operations the host can authorize; reject unsupported, missing, or unauthorized requests with defined errors rather than inventing a generic capability bit. |
| Ordered state and replay | **partially implemented** | Per-session event IDs, ranges, cursors, and resync exist. They are not one endpoint-wide `serverSeq` space across channels, clients, and reconnects, nor do they provide snapshots, server action echoes, or optimistic reconciliation. |
| Event durability | **partially implemented** | Live events may be published before the SQLite writer commits them. Persist authoritative AHP actions before clients can acknowledge them. |
| Backpressure | **partially implemented** | Several queues are unbounded and represented interactive tails can drop events. AHP has limited delivery hints but still requires reliable transport behavior. |
| Cancellation and terminal state | **available through composition** | ACP cancel and bridge interrupt preserve the session. Map them to authoritative turn `cancelled`, `complete`, or `error` state. |
| Tools and confirmation | **partially implemented** | Tool events exist, but normal HTTP sessions auto-approve and lack a complete confirmation API. Implement durable AHP tool states before exposing the associated state and actions. |
| Elicitation | **partially implemented** | Form input can be answered over HTTP, but AHP requires durable request parts, stable IDs, drafts, and terminal accept/decline/cancel outcomes. |
| MCP | **partially implemented** | Downstream MCP configuration exists, but upstream configuration and restart persistence are incomplete. Optional MCP state and channels should remain absent until complete. |
| Authentication | **partially implemented** | Transport bearer authentication is valid out-of-band security. AHP protected-resource discovery and `authenticate` are absent and optional unless advertised. |
| Multiple clients | **partially implemented** | Multiple observers exist, while one downstream controller is enforced. Add AHP client identity and action reconciliation above that controller. |
| Interactive-session representation | **extension-only** | Useful reduced-fidelity bridge behavior, but not core AHP. Advertise fidelity honestly through a negotiated `x-` extension or out-of-band host profile, not a generic core capability. |
| Remote venues and federation | **extension-only** | SSH, CodeSpace, container, peer-bridge, and satellite reach exceed AHP's single client-host connection. Keep them behind host implementation and extensions. |
| Worktree finalization/handoff | **extension-only** | Workspace cleanup, mux control, head succession, and continuation briefs are bridge/provider policy, not AHP core. |
| Task dispatch/automation | **extension-only** | Released AHP 0.8 has no scheduler. Later automation channels may provide an optional mapping. |

## Highest-risk gaps

### Logical identity can currently be replaced

Prompt auto-resume can recreate a fresh downstream ACP conversation under the
same bridge handle when resume fails. That is useful recovery behavior for a
private API, but it cannot be exposed as continuity of one AHP session. The AHP
adapter must fail, create an explicit successor, or record an observable
identity transition.

### Existing replay is not AHP reconciliation

Per-session event IDs and delivery cursors do not provide AHP's single
endpoint-wide server ordering across channels, clients, and transport
reconnections, nor channel snapshots, origin echoes, rejected actions, and
multi-client optimistic reconciliation. A new protocol-neutral state reducer
and transactional action outbox are required.

### Rich content is accepted and discarded

The ACP signatures admit text, images, audio, and resources, but the current
adapter extracts text and loses the remaining blocks. An AHP host must never
advertise or accept a content form it will silently discard.

### Permissions are not durable shared state

Bridge-owned HTTP sessions auto-approve tool requests. Upstream ACP can forward
permissions, but the general host surface lacks durable pending-confirmation and
terminal denial/cancellation state. This must be modeled, not translated into a
transient notification.

### Represented sessions have weaker guarantees

Interactive-session representation is deliberately reduced-fidelity and
in-memory. It may remain a valuable extension, but it cannot claim the same
replay, mutation, permission, or elicitation guarantees as an owned AHP
session.

## Native host and copilotd boundary

`copilotd` is treated as a prospective native local AHP host, not as an
implementation detail agent-bridge may depend on before its contract is
released and stable.

The desired end state is:

1. A client speaks AHP to agent-bridge or to a native local host.
2. Baseline behavior, exact named capabilities, optional state, and negotiated
   `x-` extensions explain differences without client-specific endpoint
   vocabularies.
3. agent-bridge retains differentiated value: remote and venue routing, durable
   recovery policy, interactive representation, provider composition, and
   cross-host coordination.
4. Where a native host fully owns local workspace/session creation, that host
   remains the lifecycle and replay authority; agent-bridge may proxy or
   federate the resource through a released interface rather than maintaining a
   competing private implementation.
5. Feature detection and fallback preserve function when the native host is
   absent, older, or incompatible.

This follows `visions/native-convergence`: delegate a released native primitive,
keep the value the bridge uniquely supplies, and never make an unstable surface
load-bearing.

## Target internal architecture

The AHP implementation should be an adapter over one internal state service, not
a second orchestration stack beside REST:

```text
AHP clients         existing CLI/REST clients
     |                        |
 AHP adapter          compatibility adapters
     +-----------+------------+
                 |
    protocol-neutral domain state
  root/session/chat models + ownership
       identity + capability registry
                 |
      transactional AHP outbox
  snapshots + globally ordered actions
                 |
       session/workspace services
       /          |           \
 downstream ACP  providers   native-host proxy
                 |                 |
             Session Host    authoritative native host
```

For bridge-owned resources, protocol-neutral domain state owns lifecycle and the
AHP outbox owns client-visible ordering. Existing session manager, database,
resolver, and Session Host facilities should be reused behind that boundary,
but their private records must not become the public protocol schema by
accident. For native-owned resources, the native host remains authoritative;
the bridge does not create a second lifecycle ledger under an AHP-shaped name.
If those resources are federated through a bridge AHP endpoint, the bridge
projects their authoritative snapshots/actions into that endpoint's one
`serverSeq` space. Otherwise the native host remains a separate AHP endpoint.

## Minimum credible claim

The project may call agent-bridge an AHP 0.8 host only when an independent client
can:

1. initialize and negotiate 0.8;
2. subscribe to root state, discover an agent, and page through `listSessions`;
3. create and subscribe to a session, observe `session/ready`, resolve the
   matching `SessionState.defaultChat`, and subscribe to that chat;
4. submit one user turn and receive ordered response state;
5. cancel a turn and observe a durable terminal outcome;
6. disconnect, reconnect, reconcile from replay or snapshots, and re-fetch the
   session catalog whose notifications are not replayed;
7. dispose the created session;
8. receive defined errors for unsupported versions, invalid state
   prerequisites, unauthorized operations, and unsupported extensions rather
   than silent data loss.

MCP, additional chats, multiple working directories, rich resource mutation,
interactive representation, federation, worktrees, and task scheduling may
remain named-capability-gated, optional-state-gated, or extension-only. They
must not be implied by the core compatibility claim.

## Sources

- [AHP 0.8.0 tagged specification overview](https://github.com/microsoft/agent-host-protocol/blob/v0.8.0/docs/specification/overview.md)
- [AHP 0.8.0 lifecycle and reconnect](https://github.com/microsoft/agent-host-protocol/blob/v0.8.0/docs/specification/lifecycle.md)
- [AHP 0.8.0 root channel](https://github.com/microsoft/agent-host-protocol/blob/v0.8.0/docs/specification/root-channel.md)
- [AHP 0.8.0 session channel](https://github.com/microsoft/agent-host-protocol/blob/v0.8.0/docs/specification/session-channel.md)
- [AHP 0.8.0 chat channel](https://github.com/microsoft/agent-host-protocol/blob/v0.8.0/docs/specification/chat-channel.md)
- [AHP 0.8.0 subscriptions](https://github.com/microsoft/agent-host-protocol/blob/v0.8.0/docs/specification/subscriptions.md)
- [AHP 0.8.0 command schema](https://github.com/microsoft/agent-host-protocol/blob/v0.8.0/schema/commands.schema.json)
- [AHP 0.8.0 action schema](https://github.com/microsoft/agent-host-protocol/blob/v0.8.0/schema/actions.schema.json)
- [AHP 0.8.0 authentication](https://github.com/microsoft/agent-host-protocol/blob/v0.8.0/docs/specification/authentication.md)
- [`plugins/agent-bridge/docs/architecture.md`](../../../plugins/agent-bridge/docs/architecture.md)
- [`plugins/agent-bridge/src/agent_bridge/acp_agent.py`](../../../plugins/agent-bridge/src/agent_bridge/acp_agent.py)
- [`plugins/agent-bridge/src/agent_bridge/session_manager.py`](../../../plugins/agent-bridge/src/agent_bridge/session_manager.py)
- [`plugins/agent-bridge/src/agent_bridge/events.py`](../../../plugins/agent-bridge/src/agent_bridge/events.py)
- [`plugins/agent-bridge/src/agent_bridge/db.py`](../../../plugins/agent-bridge/src/agent_bridge/db.py)
- [`plugins/agent-bridge/src/agent_bridge/session_host/`](../../../plugins/agent-bridge/src/agent_bridge/session_host/)

## See Also

- Parent effort: [agent-bridge AHP Convergence](README.md)
- Related effort:
  [Native-Construct Convergence](../native-construct-convergence/README.md)
- Public coordination: [#1266](https://github.com/ThomasMichon/copilot-extensions/issues/1266)
