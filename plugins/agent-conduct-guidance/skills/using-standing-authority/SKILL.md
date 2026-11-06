---
name: using-standing-authority
description: >
  Captures and reuses operator pre-clearance within an established charter,
  distinguishes ordinary useful work from deployment/data effects, and carries
  original approval evidence and limits through continuity. Use when qualifying
  a scope approval, avoiding a redundant permission question, or recovering a
  standing authorization. Trigger phrases include:
  - 'pre-clear this scope'
  - 'record a standing approval'
  - 'approval blast radius'
  - 'carry approval through a handoff'
---

# Using Standing Authority

This is a task-time procedure, not an approval engine. The ambient
`useful-autonomy` module owns the standing policy. Existing execution, resource,
repository and security owners retain enforcement.

## 1. Identify the actual authority and action

Read the applicable operator-established charter, assignment and existing
approval source. Identify the proposed operation, destinations, target selector,
executing/resource owners and effects. Repository trust, installed tooling,
credential availability, a peer label or a plan does not itself grant authority.
Use the existing peer procedure when intake or leadership authority is disputed.

Ordinary task-relevant inspection, source fixes, bounded validation and
designated issue/PR publication need no extra generic permission question when
the charter covers them. Diagnose a concrete failure read-only before choosing
a remedy; a slow review is not a failure. Preserve stricter applicable gates.

An author agent keeps its full context and git history for the PRs it opened, so
managing its own PRs and the branches it created is part of that delivery and
never needs an operator approval prompt. That covers rebasing onto the target
branch, resolving understood in-scope conflicts, pushing or force-pushing its
own branch (with a lease, through the repository's supported push workflow),
regenerating derived artifacts with the repository's own generators after a
rebase, restoring verified source attribution and `Co-authored-by` marks,
reconciling squash-merge history and bookkeeping without losing work, fixing
actionable CI findings and rerunning the affected checks, re-requesting review
and replying to review threads. Confirm actual prior merge/content state before
reconciliation.

A mechanical rebase or conflict-only push is not a new revision of intent. It
does not count against any review-round, revision or attempt cap and does not
spend a pre-clearance; such caps bind intent-changing revisions. Required review
and CI still apply to the rebased head, including any fresh review the push
triggers.

Preserve authored content and other owners' work (including commits someone else
pushed to the branch), required review and CI, and explicit holds. Unknown prior
effects need reconciliation, not replay. Ask for intent-changing conflicts or
missing authority for destructive discard, deployment, access changes or
unrelated service repair; ordinary PR maintenance does not waive those
boundaries. Branches and PRs the agent did not create stay with their owners.

## 2. Qualify the effect scope proportionately

Before an effect outside ordinary charter work, obtain only the facts needed to
understand affected owners/consumers, data and information flow, disruption,
scale and normal recovery. Use existing source/target declarations and bounded
preflight rather than an invented fleet-wide audit.

Deployment, personal/non-source-controlled data mutation, mail sending,
disclosure, access enrollment and externally controlled services require an
applicable explicit grant. An operator may pre-clear a low-blast-radius
deployment class across an entire owned target selector and accept its recovery
costs. That does not pre-clear a prototype's content changes, third-party
resources, new credentials or unrelated disruption. Knowledge of blast radius
informs the decision; confidence and easy recovery cannot supply it.

## 3. Capture the approval when it arrives

Record upfront instructions and answers to one-time prompts immediately,
before the next consequential action. Use one canonical effort/charter record
when available, otherwise the current owner's existing checkpoint. The
[approval-evidence record](references/approval-evidence.md) describes its fields
and examples; no new command, schema adoption or storage service is required.

Keep the original source/reference and necessary exact wording separate from
the agent's interpretation. Record only granted scope; preserve partial
approvals, refusals, unresolved choices and missing bounds. A reviewed plan is
not operational approval. Minimize evidence and respect its publication/privacy
boundary; do not copy a private transcript into a public effort.

## 4. Reuse without broadening or consuming it by convention

Compare the real action with the current record and known later directives.
An ordinary source revision, corrected command or retry within the covered
operations, selector and effects needs fresh execution evidence, not fresh
operator consent. Do not add a commit pin or one-attempt restriction merely
because the agent recorded a fingerprint.

Read a grant by its stated operation class and wording, not by the narrowest
invocation it first covered. "As needed", "whenever required" or "do not re-ask"
covers later invocations of that class within its targets, effects and limits.
A failure followed by a reviewed fix is an ordinary bounded retry of the same
operation, not new scope, unless the fix changes the operations, targets,
owners, information flow or effects.

An explicit pin, single attempt, deadline, budget, pause, revocation or
narrowing remains binding. For a revision pin or single-attempt limit,
explicit means the operator stated it, or chose it when a broader option was
also offered. A pin or one-shot limit the agent wrote into its own question, as
part of the only approving option, is the agent's proposal: record it under
interpretation as a precaution, not as an operator pin. Inspect the evidence
behind a precaution before relaxing it, within the granted class and effects;
never clear another owner's hold this way. This origin test covers only pins
and one-shot limits on a single invocation. A cap in an accepted class-level
rerun policy, and any budget, deadline or resource bound the operator accepted,
is part of the granted scope and binds whoever drafted it.
Record actual use when the grant limits repetitions; do not reset it at a new
session, branch or handoff. Unknown admission/outcome requires the existing
owner's observation/idempotency recovery, not blind replay.
An operation outside the selector, a changed owner/information flow or increased
blast radius needs the affected scope decision unless already covered.

## 5. Ask a real question, or continue

Use existing evidence and judgment for routine command choices, issue
deduplication, source repair, implementation details and effort slugs. Ask only
for unavailable information that changes the intended outcome, genuinely
missing effect authority, a material scope change or a separately reserved gate.

Before asking for approval of an operation, run this self-check:

1. Search the canonical record, current checkpoint and charter for a grant that
   covers this operation class, read by its stated wording.
2. Classify each recorded revision pin or one-shot limit by origin:
   operator-stated, operator-chosen over an offered broader option, or
   agent-proposed. Accepted rerun caps, budgets and deadlines bind as granted
   scope; once spent, ask for an amendment.
3. If a covering grant exists and only agent-proposed limits stand in the way,
   continue with fresh execution evidence. Ask only if a real gap remains.

Shape the question around the operation class. As the default option, name the
operations, target selector, effects and a bounded rerun policy, such as reruns
after reviewed fixes up to a stated cap. Offer narrower one-shot or
revision-pinned options only as clearly labeled alternatives. When the same
operation class would need approval a second time, ask once for class-level
pre-clearance instead of per invocation.

Before a blocking question, build the
[decision packet](references/decision-preparation.md) from authorized
investigation, planning and documentation. Preserve the affected branch and
original return through the adopted agenda when available, record a real
attempt/revisit or response reference, and pursue another accepted avenue.
The same reference defines an actionable wait: exact blocked action, source,
responsible owner, release condition and independently permitted work.
The clock, Autopilot and guessed presence do not grant authority. An explicit
all-work hold still stops the lane; unavailable approval alone does not.

When useful safe work is exhausted, state one concrete decision, recommendation,
ramifications and exact requested scope in the host's supported decision/wait
surface. Do not offer "Pause here" instead of already-authorized work, repeatedly
send the same incremental question, invent busywork or take another owner's work.

### Worked example: repeated qualification runs

An agent needs offline qualification runs in a disposable scratch area; nothing
outside that area changes.

- Anti-pattern: it asks "Run qualification at revision v2? One attempt; a
  failure spends it." The operator picks the only approving option. The run
  fails, a reviewed fix lands as v3, and the agent records the grant as spent
  and asks again, once per revision. An earlier "run these as needed; do not
  re-ask" answer is read as covering only its first invocation.
- Better: the self-check finds the earlier "as needed" grant for this class
  and continues. With no grant, the agent asks once: "Pre-clear offline
  qualification runs in disposable scratch for this effort, rerunning after
  each reviewed fix, at most N runs, stopping on any effect outside scratch?
  Alternative: one run at the current revision only." It records the answer
  with its origin. If the operator accepts the class, the cap of N runs binds
  as granted scope. If the operator picks the alternative, that limit binds.
  Exceeding either needs a real amendment.

### Worked example: rebasing an approved PR

An author agent's PR is approved and green, then a newer target-branch commit
conflicts with it.

- Anti-pattern: the agent asks the operator to approve the rebase push, then
  asks again after each rerun, three prompts for one mechanical update.
- Better: the agent rebases its own branch, resolves the understood conflict,
  regenerates derived artifacts with the repository's generators, reruns the
  affected checks and pushes with the supported workflow. It waits for any
  review the push triggers and for green CI, then continues to merge. The push
  spends no revision or review-round cap. It asks only if resolving the conflict
  would change the PR's intent or discard someone else's work.

## 6. Preserve authority through continuity

When approval changes, update the canonical record and current-owner checkpoint
before relying on the change. Stamp decision changes, not every tool call.
Carry its reference, applicable scope, exclusions, current holds and later
changes into every checkpoint or handoff. Preserve evidence not yet canonical
in the existing baton; never reduce all approvals to "user said yes."

A successor reads current canonical authority before effects and uses bounded
history only to repair a missing original answer or later countermand. Copied
records and consumption receipts are carriers, not new grants. If evidence
cannot be recovered, name the affected missing approval and continue other
already-authorized work. Native rollover keeps the same owner; an actual
leadership transfer still requires the existing owner's acceptance.

## Boundaries

- No automatic permission response, runtime grant evaluator, broker or service.
- No default deployment, data mutation, disclosure or third-party access grant.
- No review/CI/security bypass or replacement of exact-operation UAC consent.
- No assumption that declarative checks prove model compliance or tool approval.
- Efforts and handoffs enrich this procedure when present; none is required.
