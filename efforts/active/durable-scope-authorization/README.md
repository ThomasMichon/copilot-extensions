# Durable Scope Authorization

- **Slug:** `durable-scope-authorization`
- **Repo:** copilot-extensions
- **Branch(es):** Managed, independent proposal and implementation PRs to `dev`
- **Created:** 2026-10-30
- **Status:** Draft
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

> In general, agents will have standing authority to file issues in specific
> places, make PRs to specific places, etc., based on their charter. Efforts
> should be able to pre-qualify scope approvals, and pre-approvals should be
> durably stamped into session checkpoints and handoffs as the user provides
> them upfront, or answers one-time prompts.

The operator also selected generic useful-autonomy guidance without blanket
deployment or uncontrolled-content authority. Specific deployment risk
preferences remain adopter-owned and are not defaults of this public plugin.

## Plan

### Phase 1 - Reviewed authority contract

- [ ] Reconcile standing intent and land this proposal before implementation.
- [ ] Define one procedural approval-evidence contract: original source,
  objective/charter, operations and destinations, resource/executing owners,
  effects and accepted blast radius, exclusions, limits, repetition/revision
  policy, validity and later narrowing/revocation.
- [ ] Preserve legacy efforts and existing valid approvals without requiring a
  new runtime evaluator, trust chain or approval schema adoption.

### Phase 2 - Useful autonomy and effort capture

- [ ] Add a concise, declared conduct module and task-time procedure for ordinary
  in-charter investigation, source fixes, bounded validation and designated
  issue/PR publication.
- [ ] Distinguish source/test authority from deployment, content mutation,
  disclosure, access enrollment and external-owner effects.
- [ ] Add optional canonical effort approval capture and reuse, with original
  source validation and explicit holds, revision pins, single-attempt limits,
  expiry, revocation and uncertain-outcome handling.
- [ ] Reuse existing charter/peer/elevation ownership and replace contradictory
  repeated-confirmation guidance only within the approved generic scope.

### Phase 3 - Continuity and delivery

- [ ] Carry approval evidence and limits in both custom handoff shapes and the
  existing host-owned native checkpoint procedure.
- [ ] Update generator/emergency-draft and inherited-continuation guidance to
  retain available evidence or explicitly name its absence; missing evidence
  requires bounded recovery before effects, not inferred permission.
- [ ] Update authoritative documentation and focused tests, add changefiles and
  drive implementation through normal review and merge.
- [ ] Record source/release status truthfully. Installed rollout remains with
  each adopter's separately authorized update owner, outside this source effort.

## Validation Plan

- [ ] Declaration, bounded kernel, discovery and offline fallback contracts pass.
- [ ] Synthetic cases distinguish ordinary charter work, explicit deployment/data
  pre-clearance, widened targets/effects/owners, current holds, revocation,
  expiry, revision pins, exhausted single attempts and unknown prior effects.
- [ ] Effort/template compatibility preserves absent optional sections and
  valid original approvals without promoting plans or summaries into grants.
- [ ] Both handoff shapes and native checkpoint guidance preserve evidence and
  exclusions; actual custom storage round-trips them without silent loss.
- [ ] Automatic drafts report unavailable evidence honestly; no test claims
  host checkpoint persistence or model compliance merely from text assertions.
- [ ] Public examples, issue/PR descriptions and source remain generic; no
  deployment/data grant, automatic tool approval, service or new dependency is
  introduced.
- [ ] Required review/CI pass, merged changes are journaled and every remaining
  gate is resolved or transferred to a named tracked objective before Done.

## Proposal

The procedural record, focused scenario rehearsal and generator absence reporting
are **agent-recommended implementation details**, not additional operator grants.
Actual permission enforcement stays with the existing execution/resource owners.

## Journal

### 2026-10-30 - Proposal

- Claimed #9736 and mapped the three existing owners through bounded read-only
  evidence tasks. No runtime, content or external-service changes were made.
- Captured the public-safe operator premise; personal deployment preferences
  are deliberately excluded from generic standing authority.
