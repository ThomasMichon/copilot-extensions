# Remote ACP Client Transport

- **Slug:** `remote-acp-client-transport`
- **Repo:** copilot-extensions
- **Branch(es):** independent plan and implementation PRs targeting `dev`
- **Created:** 2026-10-15
- **Status:** Blocked
- **Vision:** [agent-bridge / externally-hosted-acp-agents](../../../visions/plugins/agent-bridge/README.md#externally-hosted-acp-agents)
- **Umbrella issue:** #6908

## Guiding Intent

Make an already-running authenticated remote ACP agent a bridge client target,
without conflating connection ownership with ownership of its server, workspace,
or durable sessions. Reuse the ACP lifecycle and existing credential owners.
Both Streamable HTTP and WebSocket belong to the selected experimental profile.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Remote transport implementer | Generic plan, client transport, tests, documentation and PR stewardship | #6908 |
| Protocol decision owner | Resolve the pinned draft's conflicting lifecycle statements | #6908 |

## Coordination

- **Topology:** serial plan and implementation PRs against `dev`.
- **Host (owns PRs):** Remote transport implementer.
- **Delegates:** none.
- **Handoff:** #6908 and this canonical public effort retain the unresolved
  lifecycle decision and subsequent implementation obligation.
- No server implementation, deployment, rollout, fleet controller or
  application-specific adapter is owned here.
- Existing bridge workspace and installation repair efforts retain their
  scopes; this effort must not supersede their open contributions.

## Context

The incoming bridge `/acp` WebSocket face is the opposite protocol direction.
`AcpClient.start` and `start_streams` initialize the existing SDK connection
over subprocess/Session Host streams. They already provide prompt, cancel,
session load, permission and elicitation handling. A remote transport should
extend that connection seam, not introduce a second chat protocol.

[Design and validation detail](design.md) records the immutable RFD pin, newer
public Python SDK prior art, credential ownership, proposed configuration,
approval requirements, and the decision gate.
[Qualified wire contract](contract.md) fixes the selected profile identifier,
extension metadata, approval identity rules and configured target shape.

## Request

Public-safe capture of the requested generic capability:

> Goal: allow agent-bridge as a CLIENT to an already-running remote ACP HTTP(S)
> endpoint, reusing existing ACP lifecycle instead of local/SSH child spawning,
> and credential-relay/auth-injection ownership rather than a new credential store.

> User selected a PINNED EXPERIMENTAL Streamable HTTP + WebSocket remote-transport
> draft profile, NOT WS-only and NOT a private POST-chat dialect.

> Headless approval requirement: remote agent permission/tool/structured
> elicitation requests must use bridge's existing answer/relay-to-operator path.
> They cannot require the remote app's UI. Requests must be correlated and retain
> option/request identity; no autoapproval merely from textual yes, restored
> history or reconnect.

Additional requested constraints, summarized: no owned remote process or
workspace allocation; new/load sessions, streamed updates, prompt, cancel and
explicit close; preserve local/SSH/provider behavior; bounded frames, queues,
timeouts and auth refresh; verified TLS, no cross-origin credential forwarding,
no non-loopback cleartext by default, no blind prompt replay or silent transport
fallback. Validate independent synthetic HTTP/2/SSE and WebSocket consumers,
auth construction, approval roundtrips and server survival. Follow the
repository's reviewed plan, changefile, contained-test, PR and release policies;
do not deploy or modify installed payloads. Stop on a design contradiction.

Coordination clarification: durable application decision IDs are not transient
ACP request IDs. Preserve arbitrary opaque server-owned decision/tool-call IDs
and structured input integrity. Authorized agents may answer explicitly or
relay a human answer. Missing required approval capability must produce an
explicit refusal with no write, never a forced remote web UI.

Decision clarification: option A is explicitly selected. Durable conversation
history is distinct from connection-local resources; resume only through
advertised `session/load`. No live-inference continuity or automatic prompt
replay is promised. Experimental headless approval negotiation in namespaced
extension metadata is authorized, not represented as a standard ACP field.

## Plan

### Phase 1 — Reviewed intent and lifecycle decision

- [x] Verify a clean isolated source worktree, scoped publication identity,
  open #6908 and absence of a competing remote-client implementation.
- [x] Pin the public transport draft and inspect current public SDK prior art.
- [x] Record owner seams, a configuration proposal, lifecycle contradiction,
  concrete options and the validation matrix in [design.md](design.md).
- [x] Land reviewed generic intent through the normal `dev` PR policy.
- [x] Resolve decision D1 in #6908 before implementing. A reviewed plan alone
  does not select an option or authorize a wire-profile deviation.
- [x] Record the explicitly selected qualified profile and experimental
  approval negotiation in [contract.md](contract.md).
- [ ] Resolve SDK ownership decision D2: public factory interfaces cannot
  enforce the required transport bounds and redirect policy unchanged.

### Phase 2 — Scoped client implementation

- [ ] Recheck active contributions and dependency admission; qualify reusable
  SDK transport behavior against the selected profile and extend the existing
  ACP connection seam rather than copying a protocol stack.
- [ ] Add typed configured remote targets without worktree allocation,
  subprocess launch, process ownership or server termination.
- [ ] Extend the shared credential/auth-injection owner seam; keep host token
  acquisition, source scope gates and expiry handling in their existing owners.
- [ ] Implement both explicitly selected HTTP/2/SSE and WebSocket modes,
  cookies, capability negotiation, new/load, prompt, streamed updates, cancel,
  bounded transport resources and explicit connection close.
- [ ] Preserve live request/option identity across agent and operator answers;
  reject wrong-session, stale and replayed answers; keep application approvals
  outside blanket local-tool autoapproval.
- [ ] Persist only non-secret endpoint/profile/remote-session and negotiated
  capability evidence. Reconcile reconnect explicitly without prompt replay.

### Phase 3 — Validation, publication and completion

- [ ] Stage new tests before the contained runner; execute the focused matrix
  in [design.md](design.md), then local/SSH/provider regressions.
- [ ] Document configuration, experimental pin, auth setup, explicit reconnect
  and close semantics, resource limits and unsupported features.
- [ ] Add required plugin changefiles and run touched lint, install-contract
  and repository gates; complete documentation/cutover-impact review.
- [ ] Drive implementation PR review and required checks through actual merge.
- [ ] Record merged/pending-release or released/pending-rollout accurately;
  no runtime update/restart or deployment is authorized by this effort.
- [ ] Resolve every Plan and Validation Plan item before marking Done and
  releasing/finalizing ownership.

## Validation Plan

- [x] Effort/vision schema and changed-document links pass for this plan.
- [ ] D1 has a recorded explicit decision and any profile qualifications are
  reflected consistently in code, configuration, docs and synthetic peers.
- [ ] Unit coverage validates target admission, origin/identity-qualified auth,
  real header construction, bounds, protocol errors and callback identity.
- [ ] Independent synthetic remote process exercises actual HTTP/2 with
  connection/per-session SSE plus actual WebSocket upgrade and text frames.
- [ ] Both transports prove new/load, streaming, cancellation, explicit close
  and disconnect survival; unsupported load is an explicit refusal.
- [ ] Explicit agent answers, operator-relayed answers, rejection, stale/replayed
  answers, withdrawal, cancellation and cross-session isolation pass.
- [ ] Auth refusal/refresh, TLS refusal, redirects, cookie affinity, malformed
  frames, stalled streams and unknown outcome recovery pass without secrets
  entering output, persistent records or error messages.
- [ ] Local/SSH/provider behavior and changed shared-auth consumers retain
  their focused regressions; install/lint/contract gates pass.
- [ ] Every unexercised clean-room or external-venue lane has an exact recorded
  gap; synthetic or mocked coverage is never called live deployment evidence.

## Proposal

**D1 resolved: option A explicitly selected.** The distinct experimental profile
and approval metadata are recorded in [contract.md](contract.md). The bridge
must not claim unqualified RFD conformance.

**D2 blocks implementation:** the inspected SDK's public remote factories do
not expose the resource/redirect controls required by this effort. A wrapper
after `receive()` cannot bound the HTTP factory's private producer queue or SSE
parser. The WebSocket factory does not expose connector configuration. Select
either a bounded bridge-owned implementation of the SDK's public message-level
transport interface, or an upstream SDK enhancement with suitable public hooks;
do not monkey-patch private SDK classes. [Evidence and alternatives](design.md#decision-d2--sdk-transport-ownership).

## Journal

### 2026-10-15 — Explicit option A and SDK suitability gate

- Option A and namespaced experimental application-approval negotiation were
  explicitly selected. The wire contract now has its own qualified profile
  identifier and preserves opaque server decision/tool-call identity.
- Rechecked public SDK `main`; it still resolves to the inspected pin.
  Examined HTTP/WS factory signatures, producer queue, SSE parser and protocol
  response routing before any dependency installation or runtime edit.
- The public factory seams do not satisfy required bounds/redirect policy
  unchanged. D2 records the concrete choice between a bridge-owned bounded
  message transport and an SDK enhancement. No private patch was attempted.
- No runtime implementation or HTTP/2/WS execution evidence is claimed.

### 2026-10-15 — Reviewed intent merged; implementation remains blocked

- Plan PR #6911 merged to `dev` as
  `aac70d09d214962a5a00824e1f4c1e2a220b2748` after Copilot approval with
  no open findings and successful required CI, including the PR gate.
- Plan schema, new local links, file-size and whitespace checks passed.
  No runtime/integration test pass is claimed for this documentation-only slice.
- Next action: the protocol decision owner selects D1 and settles the
  application-required approval capability declaration through #6908.
  The implementation obligation remains open; the worktree is retained.
- This is a reviewed planning milestone, not a shipped transport, release or
  deployment. Runtime code and dependency changes await the decision.

### 2026-10-15 — Pinned investigation and decision gate

- Confirmed #6908 is open; no competing remote-client PR was identified.
- Inspected ACP client and permission/elicitation handling, shared
  credential-relay source/policy ownership, and agent-mcp auth injection.
- Pinned the RFD to `ccff4e7d2e431880225804a8c136c2ccfcb313d0`.
- Found newer Python SDK remote transport prior art at
  `bdb9ac7054fb2df76a922b60b356c3c65f8a8a64`; it cannot be assumed absent
  or consumed unchanged.
- Preserved D1 before runtime changes. No transport code, dependency change,
  installation, live server connection or deployment has been performed.
