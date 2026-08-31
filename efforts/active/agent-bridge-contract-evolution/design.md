# Agent-Bridge Contract Evolution Design

Back to the [parent effort](README.md).

## Design role

This foundation governs how agent-bridge contracts change; it is not itself a
new public protocol. Public faces keep their own semantics:

- AHP owns its standard host model and exact versioned capabilities.
- Delegation control owns its task-shaped lifecycle and attention/result model.
- ACP owns downstream agent-runtime behavior.
- Providers own venue lifecycle and trust declarations.

The foundation supplies shared negotiation, persistence, rollback, and
compatibility evidence beneath those faces.

At a released standard edge, the standard protocol performs negotiation. AHP
`initialize` and its exact named capabilities, and ACP's own protocol exchange,
are not preceded by a bridge-invented universal handshake. The foundation
records and pins the selected result for internal ownership and recovery.

## Compatibility vocabulary

| Identifier | Meaning | Rule |
|------------|---------|------|
| Implementation version | Build, package, or immutable runtime slot | Evidence and diagnostics only; never sufficient to infer semantics |
| Protocol generation | Framing, ordering, required meaning, or authority model | Select an adapter before side effects |
| Capability version | Optional semantic behavior within a compatible generation | Negotiate by name at the boundary that owns it |
| State schema | Durable representation and migration posture | Additive readers tolerate older state; semantic changes migrate or use a new record kind |
| Instance generation | One live daemon, host, provider, relay, or cutover owner | Pair with process-start identity so stale PIDs and endpoints cannot regain authority |

The same implementation may support several protocol generations, and two
different implementations may share a compatible semantic envelope. Package
version equality is neither required nor sufficient.

## Ownership map

| Layer | Owns | Must not own |
|------|------|--------------|
| Contract foundation | Registry, provenance, negotiation discipline, session pinning, bridge-owned durable-state tolerance, writer fencing, cutover participation, mixed-version gate | AHP method/capability semantics, delegation vocabulary, ACP payload interpretation, event/history identity, projection references, or rebuild-generation semantics owned by #1138 |
| AHP adapter | Released AHP codecs, channel state, standard capabilities, client reconciliation, host resource identity | Generic provider/relay/claim semantics or a competing bridge lifecycle ledger |
| Delegation control | Create/read/steer/wait/interrupt/end vocabulary, attention boundaries, bounded result shape | Venue-specific transport, AHP state model, detached wake-up fiction |
| Session Host adapter | Host-envelope framing, replay, child custody, frontend attach | Rewriting opaque ACP semantics or upgrading an existing session by changing defaults |
| Venue/namespace-provider boundary | Venue resolution, typed launch/lifecycle/trust metadata, refresh and cleanup ownership; distinct from #1266's AHP workspace-provider contract | Session or conversation authority; record semantics remain owned by the venue-provider plugin |
| Relay and bridge-side claim adapters | Versioned relay integration and translation of owner-defined fenced claim outcomes | Broadening authority because a newer peer happens to be installed, or redefining worktree-owned claim records |

## Initial contract inventory

| Contract | Current posture | Transformation target | Consumer |
|----------|-----------------|-----------------------|----------|
| Bridge HTTP/API | Numeric protocol range and tolerant JSON readers; capabilities inferred coarsely | Boundary-owned named capabilities, peer/instance identity, explicit range enforcement | Existing CLI/REST and delegation control |
| Session creation | Request carries a version; response advertises a range; semantics are not selected and persisted before effects | One accepted session contract persisted before claim, relay, provider, host, route, or launch mutation | Every new bridge-owned session |
| Session Host envelope | Private generation with replay cursor and opaque ACP payload; no explicit range/capability handshake | Explicit host generation/range/capabilities and adapter identity, without changing existing-host semantics | Frontend and surviving Session Hosts |
| AHP host edge | Planned released-version adapter | Exact AHP initialization, codecs, state prerequisites, and reconciliation registered with provenance | #1266 |
| Delegation control | Several bridge verbs and raw event/status surfaces | Compact lifecycle, attention, and bounded-result semantics carried over the selected session contract | #1448 |
| HostIndex and remote authority | Recovery records can reject or lose additive metadata; PID evidence can be ambiguous | Tolerant versioned records preserving unknown fields, owner generation, process-start evidence, and selected adapter | Recovery and frontend replacement |
| Runtime route and cutover | The [install contract](../../../docs/install-contract.md) owns immutable slots, `current-version`, last-known-good selection, and runtime rollback; related bridge authority resources can still move separately | Bridge-owned route, relay integration, provider-adapter refresh, and claim-adapter mutation participate in the existing install cutover transaction rather than creating another runtime authority | Deployment and rollback |
| Venue/namespace-provider manifest and launch | Stable process boundary with legacy command-shape coupling | Owner-defined lifecycle/trust/capabilities and typed launch metadata emitted beside legacy shape; this effort owns bridge-side readers, adapters, and fixtures only | Venue providers and session manager |
| Credential relay | Implicit profile/action semantics and loosely coupled rendezvous | Versioned source/action contract with owner generation and non-broadening authorization | Remote session bootstrap |
| Venue/worktree claims | Numeric conflict translation and optional fencing | Owner-defined structured outcomes consumed through a stable bridge adapter; worktree and venue plugins retain record and fencing semantics | Providers, bridge, and worktree authority |
| Event/evidence identity | Several cursor and sequence domains; rebuilds may change references | #1138-defined source authority and immutable history identity registered here with fixtures and provenance | #1138, AHP, and delegation results |

## Change classes

### Additive

An optional field, response field, message type, endpoint, or durable metadata
is additive only when every supported old reader ignores or preserves it safely.

### Capability-gated

An optional behavior is capability-gated when absence can be detected before
use and the fallback preserves ownership and identity. Examples include bounded
delegation results, non-displacing observation, typed provider launch metadata,
or a new relay action.

### Semantic tightening

Changing authorization, cleanup ownership, retry/idempotency, trust, redaction,
or which process owns lifetime is not a harmless validation improvement. It
requires a new semantic capability version or protocol generation selected and
pinned before use.

### Generation-breaking

Framing, ordering, cursor, replay, durability, identity, authority, or
force/fencing meaning changes require a parallel adapter and explicit migration.
Existing sessions remain on the adapter that understands their contract.

## Expand-to-contract rollout

1. **Expand readers.** Add tolerant readers, fixtures, provenance, and an
   enforceable isolation mechanism for old writers.
2. **Expand writers.** Dual-emit new data beside legacy shapes; do not prefer it.
3. **Prefer by contract.** Negotiate for new sessions or canary venues and
   persist the selection before side effects.
4. **Stop legacy writers.** Continue serving existing sessions and reading
   recoverable state.
5. **Retire adapters.** Require zero live and durable references plus an expired
   rollback horizon and an explicit retirement record.

## Support window

Each registry entry names its ordinary tested window and a bridge-contract
rollback window owned by this effort's contract registry. The window is the
minimum time and fleet evidence during which the prior semantic adapter remains
available after a new default is selected; it is distinct from runtime-slot
publication and `last-known-good`. The baseline expectation is the current
preferred generation plus the immediately previous supported generation, but
references override that convenience:

```text
support window =
  current preferred generation
  + every generation used by a live session or process
  + every generation used by recoverable durable state
  + every generation retained by the bridge-contract rollback window
```

A generation may therefore remain supported longer than one release when a
session, recoverable record, or bounded compatibility hold still needs it.
Package convergence alone never closes the window.

## Existing-session continuity

- Frontend replacement does not upgrade the Session Host that owns a child.
- Provider refresh may change reachability, but not reinterpret a persisted
  launch or ownership contract.
- Changing the preferred AHP, delegation, provider, or host generation affects
  new sessions only.
- An incompatible but live host remains attached to its immutable runtime as an
  explicit, policy-bounded compatibility hold. The hold escalates to deliberate
  drain, handoff, or operator disposition before its support window expires.
- A deliberate handoff creates a successor session that negotiates its own
  contract; it does not mutate the predecessor session's selected semantics.
- Moving source authority between a bridge Session Host and a native AHP host
  requires a separately proven handoff; it is never implied by a software
  update.

## Cutover authority

The repository's [install contract](../../../docs/install-contract.md) remains
the owner of immutable runtime slots, `current-version`, `last-known-good`, and
attributable installation receipts. It explicitly does not own plugin
application state, and this effort does not extend or mutate its markers.

After selecting an attributable immutable runtime, bridge-owned route
publication, relay integration, venue-provider adapter refresh, and
worktree-claim adapter mutation participate in a separate bridge-local operating
generation. The generation records the runtime installation receipt it is
compatible with and refuses adoption when that provenance no longer matches:

```text
PREPARED  -> candidate resources exist but cannot publish authority
COMMITTED -> mutations carrying this generation are accepted
ABORTED   -> candidate resources are cleaned up and cannot publish
```

Re-selecting an older immutable runtime does not itself roll back bridge
application state. Bridge rollback returns defaults and new traffic to an older
operating generation only when that generation can read every authoritative
record written since cutover and its recorded runtime receipt is available.
Otherwise the newer runtime remains responsible for the sessions and records it
owns, and the system reports a partial rollback rather than spawning duplicate
children.

## Failure behavior

- Supported peer without a requested capability: degrade before effects and
  name the missing capability.
- Unsupported peer generation: fail with both ranges and a remediation.
- Unversioned peer: use only the registered legacy baseline.
- Unknown provider trust or authority: deny expansion.
- Unsupported durable record: preserve it and report incompatibility.
- Ambiguous session ownership or transport: retain the current owner; do not
  recreate the conversation.
- Incompatible rollback reader: do not commit the irreversible writer.

## Retirement evidence

A generation or adapter is removable only when all of the following are true:

- no live daemon, host, provider, relay, or session uses it;
- no recoverable target, authority, claim, route, event, or rollback record
  references it;
- current and previous supported matrix arms pass without it;
- the bridge-contract rollback window has expired and every policy-bounded
  compatibility hold has been drained, handed off, or explicitly disposed; and
- an explicit retirement record names the evidence and decision.
