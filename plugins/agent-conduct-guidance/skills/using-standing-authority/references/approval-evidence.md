# Approval evidence and scope

Use this procedural record in one existing operator-owned effort, charter or
checkpoint. It is not a signed capability, strict runtime schema or permission
resolver. [Using Standing Authority](../SKILL.md) owns the capture/reuse procedure.

## Record fields

Capture only applicable facts actually established by the operator or charter;
do not invent missing authority or turn an absent limit into unlimited effects.

| Field | Meaning |
|-------|---------|
| Record and original source | Stable local reference; original operator/charter decision reference, necessary exact wording and attributable issuer |
| Objective and charter | Continuing goal, applicable role and designated publication destinations |
| Allowed operations | What was cleared, including deployment/fix/rollback loops only when actually granted |
| Resources and owners | Target selector, data/content scope, executing and resource owners, affected consumers and information destinations |
| Effects and blast radius | Expected mutations/disruption/scale and the operator's accepted failure/recovery costs; not an agent-generated approval score |
| Conditions and exclusions | Review/release/authentication prerequisites, protected data/services, forbidden operations and unresolved decisions |
| Limits and repetition | Resource/time/disruption bounds, retry/revision policy and any explicit pin or single-attempt restriction |
| Validity and changes | Expiry/stop conditions, actual limited uses, current holds, narrowing/revocation/supersession and their original source references |
| Interpretation and unknowns | Clearly labeled agent interpretation and unresolved evidence, never promoted into the original grant |

A charter may already establish ordinary source/test/publication authority.
Reference it rather than asking again or creating another grant. A broad selector
such as all operator-owned enrolled tooling targets can cover changing members
when the original approval genuinely permits that class and scale; do not expand
it to unrelated data, owners or operations.

## Synthetic pre-clearance example

```text
Record: tooling-release-loop
Source: original operator decision reference, with the exact applicable answer
Objective: deliver the reviewed tooling fix through normal release and rollout
Operations: install/update the released tooling and its normal owned cutover;
            diagnose/correct/redeploy; normal rollback only within this scope
Targets/owners: the operator's enrolled tooling hosts; existing installer owner
Effects/accepted blast radius: temporary tooling disruption and stated recovery
                              costs; no personal-content mutation or disclosure
Conditions: normal review, release, provenance, authentication and live checks
Limits/repetition: use the operator's actual limits; repeated corrections allowed
Exclusions: mail, personal archives, external services, new grants, force bypasses
Validity: the original decision's stop/expiry conditions and current disposition
Changes: later holds, narrowed scope or revocation, with their original references
```

This example grants nothing. Fill references and bounds from an actual decision.
For a personal-data organizer, separately clear the data selector, preview/apply
mode, mutations, disclosure, recovery and repetition; tooling deployment
approval is not content authority.

## Decision cases

| Situation | Required action |
|-----------|-----------------|
| Charter permits a source fix, bounded tests and PR publication to the named repository | Continue through ordinary repository/testing gates without another generic permission question. |
| Agent wants to file the same task's issue in a different, unapproved destination | Obtain the missing publication scope; do not infer it from account access. |
| Operator pre-cleared the normal tooling deployment/fix loop across the owned target selector | Reuse that clearance while actual operations, owners, effects and limits still match. |
| New source revision or corrected command remains within an unpinned repeating scope | Refresh execution evidence and continue; do not manufacture a new approval boundary. |
| Original operator answer pins a revision or permits only one attempt | Preserve the pin/use limit; obtain a real amendment before exceeding it. |
| Prior invocation may already have produced effects | Reconcile through the existing owner with the original identity; do not blindly replay. |
| Deployment expands beyond the approved selector, owner or accepted effects | Ask only for the expanded effect scope and continue unaffected authorized work. |
| Low-risk tooling approval is reused for a prototype that mutates personal archives | Keep content mutation gated; deployment recoverability does not authorize data effects. |
| Readable credentials or a reachable third-party service are available | Preserve external-owner and disclosure gates; availability is not permission. |
| Later authoritative pause, expiry, narrowing or revocation applies | Record and honor it before effects; a copied older approval does not override it. |
| A reviewed plan, successful tool call or consumed handoff claims approval | Recover original authority; none of these records manufactures a grant. |
| Original approval evidence is missing or unreadable | Name the affected gap, use bounded evidence recovery and continue other authorized work. |
| A checkpoint/handoff is created after a one-time approval | Carry the canonical reference, applicable limits and current changes, not just "yes." |
| A routine effort name or issue-dedup choice is unresolved | Use existing intent and judgment unless the choice changes the requested outcome or authority. |
