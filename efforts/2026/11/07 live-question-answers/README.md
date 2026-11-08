# Live Question Answers

- **Slug:** `live-question-answers`
- **Repo:** copilot-extensions
- **Branch(es):** independent plan and implementation PRs targeting `dev`
- **Created:** 2026-11-07
- **Status:** Done
- **Vision:** [interactive-session-representation](../../../../visions/plugins/agent-bridge/README.md#interactive-session-representation), [evidence-backed-session-presence](../../../../visions/plugins/agent-bridge/README.md#evidence-backed-session-presence)
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
Apply the [contract-evolution pattern](../../../../docs/patterns/service-contract-evolution.md)
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
- [x] Land this proposal through ordinary review before implementation.

### Phase 2 - Correlated question roundtrip
- [x] Forward native requested/completed question events and expose truthful
  pending form, native request id, family, generation and answer availability.
- [x] Add capability-gated client/CLI and authenticated service admission,
  scoped to the exact live request and generation, with attributable audit.
- [x] Apply answers through the existing extension control channel and actual
  pending-question RPCs, never ordinary prompts or permission operations.
- [x] Preserve single-use admission and honest accepted/rejected/unknown
  outcomes through terminal/remote races, reconnect and restart. Replayed
  history alone cannot mint live answer authority.
- [x] Update canonical usage/contract docs and changefiles. Change Dispatch
  only if a genuine caller needs it; task cards remain separate.

### Phase 3 - Delivery and reconciliation
- [x] Merge reviewed implementation with required CI, reconcile #4418 and
  archive this effort. Report source merge separately from release/rollout.

## Validation Plan

- [x] Focused Python and Node tests cover both question families, exact
  request/session/generation checks, malformed answers, duplicate and late
  answers, terminal-first completion, supersession, restart and unsupported SDK.
- [x] Authenticated HTTP admission and actual extension control execution
  preserve read-only permissions and never call abort/send as an answer.
- [x] Exercise a newly created isolated real interactive question when
  supported, checking native completion and same-conversation continuation;
  distinguish native evidence from mocked transport and contributed tools.
  If fundamentally unavailable, record the precise SDK limitation and link a
  tracked supported alternative rather than claiming success.
- [x] Deferred to `#4418`: separate native legacy `user_input` qualification;
  the current built-in structured form is native-qualified and both ordinary
  pending-RPC families have focused protocol tests. Do not close the umbrella
  as though that remaining native compatibility qualification ran.
- [x] Run affected lint, unit/contract checks and mandatory source gates;
  audit public artifacts for private context.
- [x] Deferred to `#6917`: qualify the separate Windows full-capture
  retained-writer fixture deadline under MR9/MRV10 without weakening the
  existing assertion.
- [x] Verify actual provider merge and resolve every remaining gate or
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

### 2026-11-07 - Reviewed plan accepted

- #11297 merged after current-head Copilot approval and required green checks.
- Implementation uses a fresh extension-owned question snapshot plus the
  existing durable control queue. Historical representation remains observation,
  not answer authority. Question-only polling does not admit prompt, mode,
  permission or policy controls, including for retained-inbox sessions.
- Ordinary pending-question RPCs are distinct from the SDK's trusted-human
  operations; this path never calls the latter or manufactures human evidence.

### 2026-11-07 - Source roundtrip qualified

- Implemented fresh extension snapshots, native request/generation admission,
  isolated question controls, durable original-scope outcomes and CLI/client
  capability negotiation. No Dispatch change is necessary: task cards are a
  different authority and this path needs no dispatched task.
- The private offline Windows fixture on Copilot CLI 1.0.108-1 displayed the
  actual built-in form, accepted its answer through HTTP and the bundled
  extension, completed the tool call and resumed the same conversation. It
  required one user prompt, no abort, native completion correlation, cleared
  represented state and a completed terminal history row with the answer.
- Native loading exposed the observer's unnecessary `approveAll` declaration:
  removed that unused handler instead of granting extension permission authority.
- Latest focused question/extension/native packet: 12 passed. Existing
  question/mode/readiness control packet: 27 passed. Two actor-free response
  validators passed and join the required unit packet. Representation/result
  regressions also passed; native legacy text-input qualification stays #4418.
- Install and version guards passed. The documentation guard initially raced
  transient build inputs during parallel preparation; the named temporary
  directory was confirmed absent afterward and a sequential guard passed.
- Current HTTP-52 contract evidence is being expanded and recaptured before
  publication readiness; historical generations remain unchanged.

### 2026-11-07 - Contract portfolio corrections

- The local full Windows capture encountered the retained writer fixture's
  existing 15-second communication deadline; #11311 owns that separate
  qualification investigation. No deadline or assertion was weakened.
- The source-pinned hosted capture ran all 281 cases and exposed a missing
  portable receiving asset plus an omitted aggregate capability observation.
  Added `questions.mjs` to the canonical packaged closure and observed the
  actual health capability in the independent vector comparison.
- All four affected receiver/aggregate cases now pass locally. The complete
  current capture and separate verification will run against the corrected
  published checkpoint; a partial pass is not publication evidence.

### 2026-11-07 - Qualified checkpoint and rollover correction

- The complete 281-case capture, independent no-write verification and
  historical publication guards passed in the source-pinned hosted run.
  Its native publication owner materialized the verified outputs; the
  independent local contract guard passed with 1,061 retained fixtures.
- A final source check reproduced a retained acknowledgment after rollover
  with zero original-scope retry attempts. Recovery now retries that receipt
  against its original native ID, never the answer RPC. Missing/mismatched
  old audit records remain visible without blocking new snapshots.
- Added focused rollover/lost-evidence controls. The corrected final source
  will receive fresh capture and verification before publication readiness.

### 2026-11-07 - Final source acceptance

- The final source checkpoint passed the complete 281-case capture,
  independent verification, historical publication guards and export.
  Verified source-bound outputs were materialized through the native owner;
  the independent contract guard passed again with 1,061 retained fixtures.
- All 11 question-extension controls passed, including rollover and unavailable
  original audit recovery. The real native form and actual extension-loader
  packet passed again after that correction.
- Enforced type pre-validation passed for all 40 currently admitted strict
  projects on both declared targets. Bridge remains independently pending
  type admission; no broader qualification is inferred.
- Source implementation and evidence are ready for normal review and CI.
  Merge, effort closeout and finalization remain open. No rollout occurred.

### 2026-11-07 - Review correction

- The first review identified uncharged correlation strings in passive result
  snapshots. Both full values now reserve their text budget before emission;
  insufficient room omits the pair rather than truncating an exact identifier.
- Added four real snapshot-builder regressions covering both native families,
  low budgets, atomic pair omission and truthful usage. The focused result
  regression packet passed 22 cases.
- The current HTTP-52 portfolio includes these controls (285 total). The PR
  is temporarily draft while the corrected source receives fresh evidence;
  no review, CI or permission gate is bypassed.

### 2026-11-07 - Corrected source requalified

- The corrected source passed all 285 HTTP-52 capture cases, independent
  no-write verification, immutable historical publication guards and canonical
  export in Actions run 42371932573.
- The native artifact owner materialized all 150 verified outputs against
  the exact source checkpoint. The independent contract guard passed with
  two contracts and 1,061 retained fixtures; historical generations are intact.
- The budget correction and its evidence are ready for renewed ordinary
  review. Required CI, actual merge and effort closeout remain open.

### 2026-11-07 - Source delivery complete

- #11309 merged to `dev` as `e87c2fb1cc3ba2b024d8f50045689d076974ec01`
  after Copilot approved the exact final head with no active findings and the
  ready-for-review required CI passed. The managed post-merge reconciliation
  verified the squash-merged content before aligning this source branch.
- The source supports authenticated, attributable, single-use answers to
  actual pending interactive forms without aborting or replacing the turn.
  Tool permissions and trusted-human operations remain outside the path.
- #4418 remains open for separate native legacy `user_input` qualification;
  #6917 (MR9/MRV10) retains the independent Windows full-capture fixture
  investigation after tracking-only retirement of #11311.
  Neither gap is represented as qualified by the current structured-form
  native proof or the complete Linux contract portfolio.
- This source-work effort is complete and archived. Release promotion and
  any shared-host rollout are separate operations, not performed or authorized
  by this effort; no pre-existing session was answered or interrupted.

### 2026-11-07 - Closeout review reconciliation

- The closeout review identified a concurrent tracking-only consolidation:
  #11311 was retired NOT_PLANNED after its complete unfinished outcome was
  received by open #6917 as MR9/MRV10. Updated the explicit transfer and index;
  the historical source-research journal remains unchanged.
- Repaired the inbound backlog journal link to this dated archive. No native
  Windows capture qualification or resource release follows from tracker
  retirement, and no implementation behavior changed.
