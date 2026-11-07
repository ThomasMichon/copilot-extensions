# MCP Connection Custody and Backend Handover

[Effort](README.md)

This is the **agent-recommended prospective design**, not an implemented
protocol or a claim that existing peers support it.

## Ownership and process classification

The client-bound outer stdio conduit preserves Copilot's pipes and client
identity. It is not a new per-client service daemon. Keep heavy policy/decorator
execution and connection custody shared within the addressed installation and
compatible identity boundary; do not introduce a supervisor tree per session.
Use MCP-owned components composed with existing runtime retention, rendezvous,
private IPC and native-lifetime owners, not a universal new broker.

Stable custody owns upstream connections/processes, upstream session identity,
framing, accepted message/outcome ledgers and bounded delivery queues. It does
not retain the entire old `BridgeSession` or old policy/decorator graph as the
implementation of later calls.

The replaceable backend owns current session-processing code, decorators,
policy decisions, routing and tool execution. After a successful session
handover, all newly admitted calls run through the incoming backend's code.
Already-accepted old calls retain their original execution owner until they
settle; there is no promise to relocate live coroutines or terminate them safely.

The implementation must prove that this classification conforms to the
plugin-services process-count contract before being admitted. If a necessary
client conduit needs a normative clarification, review that narrow clarification
before implementing it; do not infer a general per-session-service exception.

## Internal contract

The operator selected the bounded fresh-code slice with **explicit direct
fallbacks**, tracking broader warm reuse separately under #10486. HTTP,
dynamic-auth, routed and CLI sharing remain unsupported in that slice.
Stdio sharing retains actual inherited cwd and credential significance;
there is no declared `ServerSpec.cwd` override to manufacture equivalence.
Direct fallback pays cold authentication/startup/initialization costs while
preserving current-code admission and no-replay semantics. Broader warm
eligibility and the full custody contract below are follow-up work, not new
incoming gates or a claim that the whole effort is Done.

Introduce an MCP-owned versioned custody/attachment contract, independent of
package versions, through the existing attach/discovery boundary. The first
contract must declare its identity, supported range and capabilities for
resuming a logical session, fencing execution epochs and importing a quiescent
checkpoint. Publish declarations with the actual immutable runtime and advertise
support from the running peer.

Use opaque session identity, owner-bound authentication, attachment epochs and
monotonic transport sequences. Transport retransmission is not a new MCP tool
call: deduplicate by the custody protocol's session/sequence identity, not merely
by JSON-RPC ID. Preserve client/target/credential separation and authenticate
the then-current incoming endpoint before resuming.

Represent transfer state as validated versioned data, not Python objects or
pickle. Examples include negotiated protocol/capabilities, internal correlation
counters, deferred catalogs/loaded-tool sets, rename maps, compatible gate-cache
data, storage namespaces and routing-target identities. Treat changed policy or
configuration as a compatibility/admission decision, never an excuse to import
old authorization decisions into incompatible new semantics.

## Planned handover sequence

1. Prepare and independently authenticate the incoming backend and publish its
   current invocation/routing selections. Fresh clients use it immediately;
   an old client's quiescence must not block new installation or fresh admission.
   Authentic incoming readiness, every applicable current selector, command,
   route and registration, and actual fresh execution by that physical
   generation establish incoming completion. Qualify and land this slice before
   full session handover; outgoing recovery, census, control-home idleness,
   provenance and cleanup cannot veto it.
2. Fence new application work for the migrating session and apply bounded
   backpressure. Continue responses to server-originated requests, cancellation
   and other traffic necessary to settle already-admitted work.
3. Keep the old backend's exclusive epoch until its complete pipeline dispatches,
   generated subcalls and response transformation settle. Custody retains final
   responses and notifications until acknowledged.
4. Export compatible data only at a quiescent boundary. Construct new backend
   code objects and import that state over the existing custody-owned upstream
   connections, without replaying upstream initialization.
5. Commit a new exclusive epoch, fence old-backend sends and release queued work
   through the new pipeline. Detaching the old pipeline must not close custody's
   upstream connection or the outer client pipes.
6. Retire the outgoing central backend through its owner once accepted work and
   transferred state no longer require it. Client preservers and independently
   owned upstream resources are excluded from central retirement.

Retirement is a separately bounded, role- and generation-scoped operation.
Revalidate native identity, installation, explicit central role and then-current
selection/registration at the actual mutation boundary. Unprovable cleanup is
reported as deferred, not converted into incoming failure. Neither an installer
force flag nor a central parent's name authorizes terminating protected state
holders. Necessary durable-state compatibility and conflicting-work admission
remain part of safe incoming readiness, not optional cleanup.

Before commit, preparation failure may resume the old execution epoch after
ownership revalidation. After commit or an uncertain effect, do not blindly
retry a mutation, start a new upstream session, roll back over a newer owner or
discard an unacknowledged result.

There is no guaranteed finite completion time for an already-running mutation.
Installation and fresh-client readiness remain independent; report a particular
session's incomplete handover honestly rather than forcing it to finish.
Keeper death or backend death during non-checkpointed application work is an
explicit uncertainty boundary, not a claimed exactly-once crash-recovery feature.

## Compatibility, lifecycle and adoption

Expand capable custody/backend providers before new consumers require resume
semantics. Existing caller syntax stays unchanged. New callers must distinguish
supported legacy baseline behavior from mandatory handover support; unknown or
missing metadata is not evidence that an old host can transfer a session.
Preserve strict unknown-argument rejection and supported provider rollback.

EOF/disconnect or verified owning-session death initiates exact client-resource
settlement. Losing only a replaceable backend link is not client death, and an
interposed wrapper parent's exit is not sufficient ownership evidence.

A pre-feature live wrapper and its host-owned Python session graph cannot
acquire this protocol retroactively. Preserve those live resources and record
the explicit adoption hold. Do not silently substitute old-host draining for
the reattachment acceptance test, live-patch a peer, replay a conversation, or
claim that a new relay around an old pipeline implements current backend code.

## Qualification

Every Plan and Validation Plan item governs whole-effort completion; incoming
installation completion does not assert session handover or effort completion.
In particular, real
Windows and POSIX process/transport tests must distinguish old wrapper version,
current backend version, stable upstream identity and current handling of a
post-handover call. Include an in-flight mutation, notification/server request,
lost acknowledgment, partial frame, bounded queue pressure, failed preparation,
changed ownership/configuration, supported rollback, legacy peer and final EOF.
Verify multi-client process counts and old cleanup versus current selection.
Keep the required CI packet focused; broader native/clean-room qualification
uses the repository's existing explicit test tiers.

The first publication slice also exercises an incoming-ready/fresh-current
positive with old keepers, opaque outgoing ownership and occupied old control
state, paired with genuine incoming source/write/permission/health negatives.
Those controls do not replace the required reattachment witness or certify an
old capable host as the incoming generation.
