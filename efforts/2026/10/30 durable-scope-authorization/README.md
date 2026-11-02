# Durable Scope Authorization

- **Slug:** `durable-scope-authorization`
- **Repo:** copilot-extensions
- **Branch(es):** Managed, independent proposal and implementation PRs to `dev`
- **Created:** 2026-10-30
- **Status:** Done
- **Vision:** `harness-guidance` useful autonomy and `plugins/efforts` durable scoped authorization
- **Umbrella issue:** #9736

## Guiding Intent

Agents should continue useful work inside an established charter without
repeated permission ceremonies. Explicit operator pre-clearance should survive
ordinary source revisions, bounded retries and continuity boundaries without
expanding the original authority.

Deployment, personal or non-source-controlled data, external information flow
and resources outside the operator's control remain separate effect scopes.
Known blast radius and accepted recovery costs inform an operator's decision;
agent confidence or expected recoverability never creates permission.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Source coordinator | Canonical plan, cohesive implementation, validation and PR completion | Managed source worktree under an eligible external harness |
| Evidence delegates | Read-only conduct, effort and continuity owner maps | Bounded tasks; no independent publication or effect authority |

## Coordination

- **Topology:** One reviewed proposal, then cohesive implementation PRs as needed.
- **Host (owns PRs):** Source coordinator.
- **Delegates:** Evidence delegates supply non-overlapping owner maps; the
  coordinator owns the final contract and integration.
- **Handoff:** Preserve the canonical effort pointer, current slice, original
  authorization evidence and limits, unresolved gates and in-flight obligations.
- **Authority:** This effort permits generic source work and bounded synthetic
  validation, not runtime rollout, personal-data mutation, new privileges,
  changes to third-party resources or review/security bypasses.

## Context

The existing conduct plugin preserves already-authorized peer scope. Efforts
captures original requests and some charter approvals. Handoff stores preserve
authored Markdown, but current templates and automatic drafts do not consistently
carry granted permissions and their limits.

Extend these owners instead of introducing a grant broker, permission
auto-responder, new service or mandatory sibling dependency. Preserve literal
source evidence separately from a participant's interpretation.

## Request

Public-safe verbatim excerpt of the operator's request:

> I like the guidance you supply above; we can apply it generally in
> agent-conduct-guidance upstream, except as it pertains to "deploy" or
> manipulating content that isn't obviously source-controlled, or is data and
> services not controlled by the Operator.
>
> In general, agents will have standing authority to file issues in specific
> places, make PRs to specific places, etc., based on their charter. Efforts
> should be able to pre-qualify scope approvals, and pre-approvals should be
> durably stamped into session checkpoints and handoffs as the user provides
> them upfront, or answers one-time prompts.

The operator also selected generic useful-autonomy guidance without blanket
deployment or uncontrolled-content authority. Specific deployment risk
preferences remain adopter-owned and are not defaults of this public plugin.

## Scope Approvals

The original operator instruction above authorizes the generic source change.
The coordinator's current-owner checkpoint retains its original reference and
the private, task-specific context without publishing it here.

- **Allowed:** the Plan's generic source/docs changes, bounded synthetic tests
  and source corrections, and normal issue/PR publication in this repository.
- **Excluded:** runtime rollout, personal/non-source-controlled content,
  disclosure, external-owner effects, new privileges and policy bypasses.
- **Continuity:** retain the original decision and these bounds through
  checkpoint/handoff; review, CI and existing execution owners remain binding.
- **Changes:** no effect-scope expansion has been granted.

## Plan

### Phase 1 - Reviewed authority contract

- [x] Reconcile standing intent and land this proposal before implementation.
- [x] Define one procedural approval-evidence contract: original source,
  objective/charter, operations and destinations, resource/executing owners,
  effects and accepted blast radius, exclusions, limits, repetition/revision
  policy, validity and later narrowing/revocation.
- [x] Preserve legacy efforts and existing valid approvals without requiring a
  new runtime evaluator, trust chain or approval schema adoption.

### Phase 2 - Useful autonomy and effort capture

- [x] Add a concise, declared conduct module and task-time procedure for ordinary
  in-charter investigation, source fixes, bounded validation and designated
  issue/PR publication.
- [x] Distinguish source/test authority from deployment, content mutation,
  disclosure, access enrollment and external-owner effects.
- [x] Add optional canonical effort approval capture and reuse, with original
  source validation and explicit holds, revision pins, single-attempt limits,
  expiry, revocation and uncertain-outcome handling.
- [x] Reuse existing charter/peer/elevation ownership and replace contradictory
  repeated-confirmation guidance only within the approved generic scope.

### Phase 3 - Continuity and delivery

- [x] Carry approval evidence and limits in both custom handoff shapes and the
  existing host-owned native checkpoint procedure.
- [x] Update generator/emergency-draft and inherited-continuation guidance to
  retain available evidence or explicitly name its absence; missing evidence
  requires bounded recovery before effects, not inferred permission.
- [x] Update authoritative documentation and focused tests, add changefiles and
  drive implementation through normal review and merge.
- [x] Record source/release status truthfully. Installed rollout remains with
  each adopter's separately authorized update owner, outside this source effort.

## Validation Plan

- [x] Declaration, bounded kernel, discovery and offline fallback contracts pass.
- [x] Synthetic cases distinguish ordinary charter work, explicit deployment/data
  pre-clearance, widened targets/effects/owners, current holds, revocation,
  expiry, revision pins, exhausted single attempts and unknown prior effects.
- [x] Effort/template compatibility preserves absent optional sections and
  valid original approvals without promoting plans or summaries into grants.
- [x] Both handoff shapes and native checkpoint guidance preserve evidence and
  exclusions; actual custom storage round-trips them without silent loss.
- [x] Automatic drafts report unavailable evidence honestly; no test claims
  host checkpoint persistence or model compliance merely from text assertions.
- [x] Public examples, issue/PR descriptions and source remain generic; no
  deployment/data grant, automatic tool approval, service or new dependency is
  introduced.
- [x] Required review/CI pass, merged changes are journaled and every remaining
  gate is resolved or transferred to a named tracked objective before Done.

## Proposal

The procedural record, focused scenario rehearsal and generator absence reporting
are **agent-recommended implementation details**, not additional operator grants.
Actual permission enforcement stays with the existing execution/resource owners.

## Journal

### 2026-10-31 - Release verification and archive

- Verified the exact changed conduct kernel/declaration, effort procedure/template
  and continuity implementation in released Main content. Released versions are
  `agent-conduct-guidance 0.1.18-dev1`, `efforts 0.1.9-dev1` and
  `context-handoff 0.5.16-dev1`; no installed-machine rollout was performed here.
- A later, unrelated promotion candidate failed its remote-ref verification.
  The existing watchdog tracker [#9748](https://github.com/ThomasMichon/copilot-extensions/issues/9748)
  already has a focused repair owner. Earlier successful release content is
  preserved; no retry, branch mutation or guard bypass is part of this effort.
- Archived the completed source scope with original operator evidence and
  exclusions intact. Remaining adopter updates require their applicable
  pre-clearance and existing deployment owner.

### 2026-10-30 - Source completion

- Implementation #9741 merged after current-head Copilot approval with no open
  findings and required green CI. The managed source branch advanced onto the
  merged development commit.
- The exact published-head conduct packet passed 40 cases, effort packet passed
  74 with one platform-specific skip, and continuity packet passed 134. Actual
  durable file readback and consumption preserved the original approval limits.
- Source delivery is complete. Promotion remains with the existing
  [release-pipeline objective #3336](https://github.com/ThomasMichon/copilot-extensions/issues/3336):
  the latest observed workflow is queued; availability of this specific change
  on Main has not yet been verified. No installed rollout is claimed or authorized
  by this source effort.
- The pre-existing oversized continuity-skill advisory is tracked separately in
  [#9742](https://github.com/ThomasMichon/copilot-extensions/issues/9742); no broader
  workflow refactor or deployment is part of this completed source scope.
- Original operator evidence and effect exclusions are preserved. No deployment,
  personal-data, disclosure, access or external-owner grant was introduced.

### 2026-10-30 - Source and focused validation

- Implemented the declared conduct module and procedural record, optional effort
  capture/charter/template linkage, native checkpoint guidance and both handoff
  carriers. Original grants remain distinct from plans, caches and receipts.
- Added bounded optional `authorization_evidence` generation input. The actual
  formatter/codec and SDK event path preserve provided evidence; missing input
  remains explicit. Reconnect/refork loses only the documented convenience cache,
  not the required canonical checkpoint/effort record.
- Focused conduct, effort and continuity checks passed, including invalid-input,
  native-window clear and actual module-reimport cases. Existing source-contract,
  documentation and customization checks passed.
- Independent critique found only a cache-lifetime documentation/test gap; the
  source now states that limit and tests its recovery behavior. Review/merge and
  truthful release disposition remain open; no installed rollout occurred.

### 2026-10-30 - Reviewed implementation admission

- Proposal #9737 merged after current-head Copilot approval and required green
  CI. The same managed source worktree advanced onto the reviewed plan.
- Began the conduct/effort/continuity source implementation; deployment and
  uncontrolled-content effects remain excluded.

### 2026-10-30 - Proposal

- Claimed #9736 and mapped the three existing owners through bounded read-only
  evidence tasks. No runtime, content or external-service changes were made.
- Captured the public-safe operator premise; personal deployment preferences
  are deliberately excluded from generic standing authority.
