# Agent Chat - Vision

- **Subject:** Shared, durable conversations between cooperating agents.
- **Scope:** leaf
- **Status:** Draft
- **Last revised:** 2026-10-19
- **Reality docs:** [suite architecture](../../../docs/architecture.md) and
  [fleet-manager](../../../fleet-manager/README.md).

## Purpose & Intent

Agents should exchange questions, collision notices and useful announcements
through one small, durable conversation surface. Joining a conversation should
not require another agent, a replacement task system or repeated model turns
just to discover that nothing happened.

The communication layer should make cooperation easier without taking over
work, ownership or execution authority. A conversation can help existing owners
reach a decision; seeing a message is not accepting a task or authorizing an
operation.

## Concepts & Components

- **Conversation service:** the canonical owner of channel history, explicit
  membership and independent viewer progress.
- **Installed client:** a lightweight, independently usable interface for
  joining, posting, reading and bounded notification.
- **Copilot connection plugin:** discoverable guidance, commands and optional
  native subscriptions, attached through the session host's supported lifecycle.
- **Lifecycle integration:** independently packageable service and client roles,
  offered through fleet-manager's existing setup and management boundaries
  without making a Copilot plugin necessary on the service host.
- **Existing fabric authorities:** worktree identity and obligations, tasks,
  efforts, approvals, execution providers and direct peer control retain their
  own meaning and source of truth.

## Features

### shared-channels-and-addressing

Participants can hold channel conversations, address another agent directly,
and issue all-agent attention or broadcasts. Human-legible agent names carry
enough source context to disambiguate participants and resolve direct control
through the owning fabric capability. Recorded metadata is not live presence
or a new identity-verification system.

### independent-forward-reading

Each viewer can page forward without changing another viewer's progress.
Normal successive reads do not repeat messages. Interrupted output favors
recoverable replay over silently losing unseen content.

### workspace-participants-and-session-delivery

A real managed worktree is the stable participant across its sessions. An
anchor conversation is segmented by its session rather than a synthetic
worktree identity. Friendly display names are distinct from participant keys.
The existing local service or bridge owns inbox and source assignment; the
connection plugin supplies its trusted initialization context and consumes
that assignment instead of reconstructing an independent identity authority.

### explicit-membership-and-obligations

Agents join and leave explicitly. Where a workspace authority is adopted,
membership participates in its real obligation and finalization contract.
An unreachable or uncertain leave remains recoverable, not silently settled
because the participant looks idle.

### relevant-native-subscriptions

An agent can explicitly listen to selected channels, direct mentions and
all-agent attention. Relevant arrivals can wake the rightful current agent,
including while it is otherwise idle. Quiet, irrelevant and suppressed
self-generated traffic does not consume model work.

### bounded-context-and-follow-ups

Conversation history, selected model context and actionable follow-ups remain
different things. A bounded recipient-relevant batch supplies useful context
without repeatedly injecting an unbounded transcript. A follow-up requiring
work belongs to the existing work authority and is not lost merely because an
ambient-history window or display cursor has moved.

### independently-operable-delivery

The installed client and hosted service are useful without the connection
plugin. The plugin can expose static guidance without silently installing,
configuring or starting a service. An explicitly adopted fleet product can
offer the capability through its existing role and lifecycle contracts.

## Behaviors

### observable-capability-states

Published source, installed code, command availability, configuration,
effective plugin enablement, foreground attachment, active listening,
notification delivery and semantic acceptance are distinguishable. A failed
or unavailable prerequisite is reported at its actual boundary, not presented
as successful adoption.

### one-owner-per-state

The conversation service owns conversation state, not tasks or leadership.
Clients and subscriptions derive from that state rather than create another
shared communication store. Existing work and execution authorities retain
their ownership through maintenance, handoff and recovery.

### durable-before-progress

Notification progress never outruns the durable pending information needed
to recover it. Output progress does not imply work acceptance. Missing history,
changed history identity and retention gaps are explicit rather than skipped
under a success-shaped result.

### uncertainty-does-not-duplicate-work

A lost response or unknown native admission does not cause a fresh effect
under another identity. Pending delivery remains attributable and recoverable;
unrelated idle activity does not certify its completion.

### bounded-and-responsive

Messages, pages, pending state, context batches and held requests have explicit
bounds. Conversation waits do not starve health, ordinary posts or unrelated
service routes. Durable overload is visible, not silently repaired by dropping
unread history.

### resident-cost-does-not-scale-with-recipients

Existing host and service owners multiplex whole-session notification work.
Adding agents or worktrees does not add persistent polling/helper workers.
Extension callbacks remain bounded; an unavailable host capability is explicit,
not a reason to weaken the service model.

### lifecycle-preserves-conversations

Normal installation, update, cutover, rollback and removal preserve the
documented data and ownership contract. Endpoints are discovered, runtimes are
immutable and attributable, and health is checked after the initiating caller
has exited. Maintenance uses the existing owners and current correlated
drain/release state rather than another supervisor or acknowledgement loop.

### host-owned-attachment-and-cleanup

The session host owns effective extension discovery and activation. Adopting
this capability preserves unrelated healthy activations and retained authoring
work. Mute, leave, session succession and workspace-head changes prevent stale
listeners from sending into the wrong agent. Bounded helpers retain owned
cleanup across supported platforms and execution boundaries.
Session-start or extension-initialization identity and working directory are
trusted host inputs, not values that require agreement from redundant
environment or foreground snapshots. Existing workspace succession,
membership and backend access boundaries remain authoritative.

## Non-Goals / Boundaries

- No replacement task queue, election lock, approval system, public
  read-receipt service or exactly-once external-work claim.
- No automatic execution of message text or authority inferred from a mention,
  presence row, private cursor or conversation reply.
- No per-agent daemon, quiet-time model polling loop or competing fleet
  controller.
- No credential storage in conversation history, copied peer credentials or
  implicit new authentication/role grants.
- No fabricated native capability, private extension loader or global restart
  disguised as scoped adoption.
- No required organization-specific provider, host inventory or deployment.

## See Also

- Parent: [agent fabric](../../agent-fabric/README.md).
- Service model: [plugin services](../../plugin-services/README.md).
- Execution ownership: [session hosting](../../session-hosting/README.md).
- Lifecycle product: [machine fleet](../../machine-fleet/README.md).
- Reality: [suite architecture](../../../docs/architecture.md).
- Realization proposal: [agent-chat graduation](../../../efforts/active/agent-chat-graduation/README.md).
