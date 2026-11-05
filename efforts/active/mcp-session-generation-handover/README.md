# MCP Session Generation Handover

- **Slug:** `mcp-session-generation-handover`
- **Repo:** copilot-extensions
- **Branch(es):** Independent reviewed source PRs targeting `dev`
- **Created:** 2026-11-04
- **Status:** Draft
- **Vision:** [Authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement), [version-skew-safe service contracts](../../../docs/patterns/service-contract-evolution.md)
- **Umbrella issue:** #10486
- **Related issues:** #9716, #10377

## Guiding Intent

An update must make fresh `agent-mcp` invocations use the incoming runtime and
serving generation without terminating existing clients. A client-owned stdio
wrapper may retain its original code, but an adopted wrapper must reattach to
the replacement shared runtime without losing its MCP session. Leaving the old
heavy host serving existing sessions indefinitely is not that guarantee.

Incoming readiness and complete current selection define installation success.
Incumbent recovery, ownership metadata, process-census failures and predecessor
retirement must not veto installation. Configuration, credentials, repositories,
databases, sessions and uncertain effects survive. Retirement remains separately
owned and excludes client/session preservers.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| battered-sprocket | Coordinator, implementation integration and PR owner | Managed source worktree |
| Session-path evidence | Read-only client, host and upstream-state analysis | Coordinator delegation |
| Activation evidence | Read-only installer, selection and retirement analysis | Coordinator delegation |
| Keeper evidence | Read-only existing lifecycle/transport primitive analysis | Coordinator delegation |

## Coordination

- **Topology:** Sequential plan and implementation PRs; no parallel source writers.
- **Host (owns PRs):** battered-sprocket.
- **Delegates:** The evidence roles above do not mutate source or run live services.
- **Handoff:** Findings return to the coordinator. Any implementation delegation
  receives explicit file ownership and preserves this effort's completion gate.
- Preserve the existing installer, source-construction and census owners.
  This effort does not replace the installer or waive native ownership checks.

## Context

#10486 records the missing session-preserving handover boundary. The existing
thin forwarder can attach to a shared host, but its documented host-loss path
exits rather than reattaching. Initialization, upstream sessions, notifications
and accepted calls cannot simply be replayed as a new session.

An old host can remain active while newer code is selected on disk. Fresh
retirement can also refuse an uncertain process census before installation
metadata and serving state converge. Silencing that refusal, killing old
interpreters or rewriting a route is not a session-preservation solution.

The [install contract](../../../docs/install-contract.md#authoritative-replacement-and-completion)
and [service-contract evolution pattern](../../../docs/patterns/service-contract-evolution.md)
govern the implementation. Source review, promoted release and actual consumer
acceptance are distinct milestones.

## Request

The settled requirement is fresh-call generation selection **and** preserved
session reattachment, not merely an old-host drain. The operator's exact request
and reattachment selection are retained in
[inception-transcript.md](inception-transcript.md).

## Scope Approvals

- The operator requested a source-backed MCP retirement/update fix and selected
  reattachment when explicitly offered reattachment versus old-host draining.
- Existing live clients and state-preservation processes must not be force-killed.
  Their retained code cannot be live-patched to manufacture new protocol support.
- This reviewed plan does not itself grant rollout, elevation, service-restart,
  configuration-policy changes or destructive cleanup authority. Apply any
  separately established target-specific authorization at its existing boundary.

## Plan

The decomposition and implementation choices below are **agent-recommended**.

### Phase 1 - Reviewed contract

- [ ] Resolve concrete client/upstream state ownership and reuse existing
  lifecycle/transport primitives rather than inventing a parallel supervisor.
- [ ] Reconcile client-bound stdio conduits and shared custody against the
  [service process-count contract](../../../visions/plugin-services/README.md#process-count-scales-with-services-not-sessions).
  Heavy backends and custody services must be bounded by installation and
  identity-compatible pooling, not new per-client daemons or supervisor trees.
- [ ] Specify the versioned handover, admission and outcome contract, including
  the legacy-client adoption boundary and unsupported/rollback behavior.
- [ ] Land this prospective effort through normal automated review before code.

### Phase 2 - Session-preserving generation handover

- [ ] Keep the literal portable `agent-mcp bridge` entry and resolve current
  software for every fresh invocation.
- [ ] Retain a client-owned, installation-independent stdio lifetime boundary;
  replacement payloads and shared runtime slots must not invalidate it.
- [ ] Reattach adopted wrappers to the incoming shared runtime while preserving
  initialization, upstream session identity, notifications, ordering and
  accepted/in-flight/unknown call outcomes without blind mutation replay.
- [ ] Preserve identity/credential isolation, bridge configuration, decorators,
  routed targets and protocol-era compatibility.
- [ ] Self-retire client preservers on disconnect/EOF or verified owning-session
  termination; do not mistake a wrapper parent's exit for session completion.

### Phase 3 - Installer and retirement integration

- [ ] Publish incoming readiness, selectors, commands, routing and required
  registrations independently of predecessor drain and uncertain old cleanup.
- [ ] Close superseded admission through the existing owner and retire old
  central generations only after their protected state is transferred or
  drained. Exclude client preservers and independently owned resources.
- [ ] Keep explicit/manual versus automatic inhibition, source admission,
  actual permission/I/O failures and failed incoming readiness truthful.
- [ ] Preserve pre-feature live clients without claiming that their old code
  acquired a new handover protocol. Name any remaining legacy adoption hold.

### Phase 4 - Qualification, release and acceptance

- [ ] Run focused protocol and real-process regression controls on supported
  Windows and POSIX boundaries, plus practical clean-room install qualification.
- [ ] Update owning reality docs, contract declarations and per-plugin
  changefiles; land implementation through review and required CI.
- [ ] Verify promotion, then perform separately authorized installed-consumer
  acceptance. Record old-client preservation separately from actual reattachment.
- [ ] Resolve every plan and validation gate, or transfer an explicitly blocked
  legacy adoption item to its named tracked owner, before closing this effort.

## Validation Plan

- [ ] A real client remains connected through an update, its original wrapper
  remains alive, and subsequent requests are handled by the incoming runtime.
- [ ] A simultaneous fresh literal `agent-mcp bridge` invocation reaches the
  incoming runtime, not an advertised stale host or obsolete command redirect.
- [ ] Compare one client with multiple simultaneous clients and an update in
  progress: heavy backend/custody process counts remain service/installation
  bounded, client identities stay isolated, and no per-client daemon or
  supervisor tree is introduced.
- [ ] Session initialization, upstream identity, notifications and accepted
  calls survive handover; side-effecting calls are not duplicated on ambiguous
  responses, failed handover or transport interruption.
- [ ] Exercise current/prior peers, supported rollback and a pre-feature peer
  lacking reattachment. Unsupported support is explicit; live legacy clients
  are preserved rather than forcibly migrated.
- [ ] Failed candidate health, changed owner/route/selection and an old
  generation's late cleanup cannot corrupt or retire the current generation.
- [ ] EOF/disconnect and verified owner death settle exact client resources;
  an interposed parent exit does not destroy a still-owned live session.
- [ ] Actual old central retirement and client-preserver survival are verified
  independently. No process-name sweep, whole-root reset or manufactured receipt.
- [ ] Actual consumer evidence distinguishes source qualification, released
  payload, selected runtime and the generation that handles a tool call.

## Proposal

Prospective requirements and validation obligations only. The recommended
owner-composed design is specified in [handover-contract.md](handover-contract.md).
It separates stable connection custody from replaceable policy/decorator code;
retaining an entire old pipeline behind a new relay is not accepted as new
backend execution. This plan does not assert that reattachment or legacy live
migration already works.

## Journal

### 2026-11-04 - Kickoff

- Claimed #10486, resolved the source repository's effort adoption and captured
  the operator's explicit reattachment selection.
- Started bounded read-only evidence on client state, activation and reusable
  session/lifecycle primitives. No implementation or live migration performed.
- Source evidence found no existing generic MCP-session migration broker.
  Added a prospective quiescent-handover contract and explicit multi-client
  process-count validation in response to review.
