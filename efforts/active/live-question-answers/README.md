# Live Question Answers

- **Slug:** `live-question-answers`
- **Repo:** copilot-extensions
- **Branch(es):** independent plan and implementation PRs targeting `dev`
- **Created:** 2026-11-07
- **Status:** Draft
- **Vision:** [interactive-session-representation](../../../visions/plugins/agent-bridge/README.md#interactive-session-representation), [evidence-backed-session-presence](../../../visions/plugins/agent-bridge/README.md#evidence-backed-session-presence)
- **Umbrella issue:** #4418

## Guiding Intent

Allow an authenticated operator or explicitly delegated trusted client to answer
the actual pending question in an interactive CLI session without aborting its
turn or taking over its process. Preserve the native request's identity and the
terminal's ability to answer first. Tool permissions remain terminal-owned.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Protocol driver | Plan, implementation, tests and PR stewardship | This effort and #4418 |

## Coordination

- **Topology:** serial plan and implementation PRs, isolated source worktree.
- **Host (owns PRs):** Protocol driver.
- **Delegates:** none.
- **Handoff:** preserve this effort, current PR and exact remaining acceptance.

## Context

#4418 already owns this capability and the inherited #2971 acceptance.
The live extension currently omits question lifecycle events; represented
`ask_user` tool starts are read-only. Owned ACP answers are a separate path.
Current SDK declarations expose `session.rpc.ui.handlePendingElicitation` and
`handlePendingUserInput`, with `success: false` for an already-resolved request.
Method declarations alone do not prove an actual joined interactive roundtrip.

This closes existing vision intent, rather than changing permission ownership.
Apply the [contract-evolution pattern](../../../docs/patterns/service-contract-evolution.md)
and the design principles for graceful composition, proportionate safeguards,
single ownership and platform parity. Reuse existing authenticated bridge
transport and the durable control inbox; do not add a daemon or Dispatch queue.

## Request

Public-safe capture of the assigned objective; private examples and destinations
are omitted, not transferred into public artifacts:

> Make it possible to ANSWER a live interactive Copilot CLI session's pending
> `ask_user` form through agent-bridge.
>
> Design and land (in copilot-extensions, agent-bridge plugin and its bundled
> live-session extension, plus any agent-dispatch touchpoint) an attributable,
> authorized path for an authenticated operator or a trusted host agent acting
> under explicit operator delegation to answer a pending ask_user/elicitation
> in a live interactive session. Preserve the safety boundary: tool-permission
> approvals stay operator-only; answers must be authenticated, scoped to the
> specific pending request id, single-use, and audited; a stale/mismatched
> answer must be refused.
>
> Do NOT answer, cancel or otherwise touch existing sessions belonging to
> other owners. Read-only inspection is fine for evidence.
>
> First check upstream issues/PRs and existing code and SDK capabilities for
> partial implementation or existing tracking. Plan via an effort if
> multi-step. Implement with tests, open PRs to `dev`, and drive them to merge.
> If the SDK fundamentally cannot inject an elicitation answer, document and
> track that limitation and land the best viable supported alternative.

## Scope Approvals

The assigned source-work scope covers inspection, isolated implementation,
bounded tests and normal issue/PR publication and merge. It does not authorize
answers to existing third-party sessions, tool approvals, interruption,
deployment, service restarts or shared runtime modification. Use only a newly
created isolated fixture for any native qualification. Authenticated transport
does not manufacture operator delegation; preserve caller attribution and
document the existing trusted-operator boundary.

## Plan

### Phase 1 - Reviewed contract
- [x] Find and claim #4418; inspect current bridge and SDK seams.
- [ ] Land this proposal through ordinary review before implementation.

### Phase 2 - Correlated question roundtrip
- [ ] Forward native requested/completed question events and expose truthful
  pending form, native request id, family, generation and answer availability.
- [ ] Add capability-gated client/CLI and authenticated service admission,
  scoped to the exact live request and generation, with attributable audit.
- [ ] Apply answers through the existing extension control channel and actual
  pending-question RPCs, never ordinary prompts or permission operations.
- [ ] Preserve single-use admission and honest accepted/rejected/unknown
  outcomes through terminal/remote races, reconnect and restart. Replayed
  history alone cannot mint live answer authority.
- [ ] Update canonical usage/contract docs and changefiles. Change Dispatch
  only if a genuine caller needs it; task cards remain separate.

### Phase 3 - Delivery and reconciliation
- [ ] Merge reviewed implementation with required CI, reconcile #4418 and
  archive this effort. Report source merge separately from release/rollout.

## Validation Plan

- [ ] Focused Python and Node tests cover both question families, exact
  request/session/generation checks, malformed answers, duplicate and late
  answers, terminal-first completion, supersession, restart and unsupported SDK.
- [ ] Authenticated HTTP admission and actual extension control execution
  preserve read-only permissions and never call abort/send as an answer.
- [ ] Exercise a newly created isolated real interactive question when
  supported, checking native completion and same-conversation continuation;
  distinguish native evidence from mocked transport and contributed tools.
  If fundamentally unavailable, record the precise SDK limitation and link a
  tracked supported alternative rather than claiming success.
- [ ] Run affected lint, unit/contract checks and mandatory source gates;
  audit public artifacts for private context.
- [ ] Verify actual provider merge and resolve every remaining gate or
  transfer it explicitly to a named tracked objective.

## Proposal

Reuse `control:*` inbox admission/claim/outcome machinery. A native request id
and an extension-lifetime generation distinguish consecutive identical forms
and prevent a reconnect or session alias from redirecting an old answer.
Observe actual SDK requests synchronously in memory; perform network and RPC
work off event callbacks. Only actual pending-question RPC success means
accepted. Claim timeout or lost acknowledgment remains unknown, never success
and never permission to replay. Unsupported clients retain manual terminal
answering. These are agent-recommended implementation choices within the
requested protocol-only scope.

## Journal

### 2026-11-07 - Initial research and proposal

- Found existing unassigned #4418 and claimed its protocol implementation;
  no matching implementation PR exists in the initial search.
- Read current bridge representation, owned answers and control inbox.
- Current bundled SDK declares both pending-question RPCs and all four
  lifecycle events. Actual joined-session qualification remains open.
- No existing session has been answered, cancelled or interrupted.
