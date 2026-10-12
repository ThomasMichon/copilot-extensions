# Proposed worker charter

This remains a **design-only draft**, not an executable declaration or
standing authorization. The operator agreed the authority baseline and the
four clarifications recorded in [README.md](README.md); pilot parameters,
live pilot execution, apply authority, and scheduling require their separate
approvals. Audit-first capability implementation is now approved; its initial
report-only recipe keeps proposed steering questions in the private report.

## Purpose and non-goals

Turn stale local worktree conclusions into accurate, durable states, then let
the owning lifecycle safely reclaim eligible workspaces.

The worker is not a backlog implementer, merge-policy bypass, session killer,
ownership adjudicator, missing-record fabricator, or force-removal service.
It has no target removal quota. Keeping a workspace for a named reason is a
successful safety outcome.

## Composition and ownership

| Component | Owns |
|-----------|------|
| agent-worktrees | Repository identity, liveness, lineage, claims, disposition, landing, finalization, and atomic removal |
| agent-dispatch | Accepted occurrence, deduplication, exclusive execution, progress, result, suspension, and supervision |
| Deterministic producer | Bounded candidate discovery and task creation; no semantic conclusion or cleanup |
| Agent worker | Evidence review and the explicitly approved conclusion actions |
| Operator/repository policy | Authority, tracker routing, transfer acceptance, review/merge gates, and pilot/default enablement |

Prefer a declared emitter and concurrency-one, label-scoped headless lane
under the existing singleton supervisor. Use attributed installed command
resolution; no checkout-pinned service, external timer, or rival daemon.
Keep the capability opt-in and unavailable rather than inventing missing
knowledge, authentication, or supervisor configuration.

A daily occurrence and a candidate are different identities. Occurrence
deduplication prevents timer replay; an exclusive key prevents overlapping
workers for the same machine/environment and project scope. Candidate-level
reservation must also prevent an interactive reviewer or another tidier from
simultaneously concluding the same workspace.

## Proposed first scope and budgets

- Local machine/environment only, explicit allowlisted project scopes.
- Oldest merged or independently proven landed worktrees first.
- Exclude the worker's own workspace, anchors, system/bridge-owned worktrees,
  live/unknown sessions, protected repositories, and ambiguous ownership.
- Suggested pilot limits: five candidates, 30 minutes, one agent at a time,
  zero source-code changes and no new downstream execution resources.
- Daily adoption, if later approved: one bounded occurrence, at most one
  pending/running sweep; coalesce missed days instead of replaying a backlog.
- Persist a resume cursor and evidence fingerprint. Revisit an unchanged hold
  only after a bounded cooldown or new evidence; proceed to later candidates
  so the oldest blocker does not consume every run.

Budgets are proposal values awaiting approval. The producer must not enqueue
work faster than the dedicated lane can conclude it. A budget limit records
remaining candidates and stops; it is never an excuse to reduce evidence or
skip gates.

The worker's own approved dispatch/run/report infrastructure is distinct
from downstream execution allocations. It must not create child worktrees,
worker sessions, executable tasks, borrowed venues, or implementation PRs to
finish a candidate's objective. An explicitly approved apply-mode filing
policy may permit issues or inert candidate-attention records; these records
must not launch execution or manufacture responsibility transfer. Audit mode
only proposes them in its report.

## Authority matrix

| Operation | Audit pilot | Possible approved apply pilot |
|-----------|-------------|-------------------------------|
| Read inventory, exact registered sessions, canonical objectives and live forge state | Yes | Yes |
| Determine proposed conclusion and cite evidence | Yes | Yes |
| Correct stale issue/effort bookkeeping | Propose only | Exact authorized record, resolved identity and writable worktree, normal publication flow |
| File a deduplicated unresolved question | Propose only | Only under explicit filing policy; preserve context, destination, owner/acceptance and requested decision |
| Post candidate steering/attention | Propose in report only | Only under an explicit posting policy; park the candidate, never the sweep or an execution allocation |
| Clear legacy follow-up | No | Only after its complete roster is proven done or an already-authorized durable transfer is accepted |
| Release an effort binding/archive effort | No | Only through the effort lifecycle after all gates are met, or explicit accepted transfer |
| Reconcile already-at-rest resource claims | No | Candidate-scoped reviewed preview, then supported apply; never session/active/unknown claims |
| Follow up on an unsettled child/PR | Read/report | Read/annotate or authorized owner notification; no new implementation, forced release, or remote shutdown |
| Finalize and clean one concluded worktree | No | Non-forced owning lifecycle, fresh evidence, complete objective, normal pair/claim/liveness gates |
| Source fix, rebase another branch, new product work, merge substantive PR | No | No; return or queue to rightful owner under established authority |
| Kill processes, discard files, reset branches, force-remove, rewrite registries, invent handoff | No | Never |

Reading a dormant session does not make the tidier its head. A standing charter
must grant only the named bookkeeping authority, and an exclusive reservation
must not masquerade as head succession or control transfer. Existing live-owner
restrictions remain in force.

## Evidence and decision procedure

1. Check command/runtime, coordinator, scoped credentials, and project health.
   A failure yields a degraded receipt, not an empty-candidate success.
2. Enumerate exact candidates; snapshot head/lifecycle/control revisions,
   local liveness, Git status including staged/untracked paths, PR state,
   inbound tasks, outbound claims, pairing, follow-ups, and effort binding.
3. Read the founding request, latest substantive turns, succession chain and
   canonical effort. Use a bounded transcript reader for long sessions.
   Truncated/unavailable evidence is unknown; never infer done from a final
   assistant sentence, tracked `finalized`, a clean tree, or one merged PR.
4. Classify each candidate as concluded, authorized-bookkeeping-pending,
   unfinished-owner-work, decision-needed, or evidence/safety-hold. List every
   outstanding item and its durable home; do not silently drop side requests.
5. In audit mode, record proposed actions and steering questions only in the
   approved report destination; do not post issues, cards, or comments.
   Tool-owned ephemeral evidence collation and the approved worker's own
   dispatch progress/result records are allowed, not candidate mutations.
   In approved apply mode,
   perform only the allowed items, land and verify any durable record update,
   then refresh the candidate snapshot before disposition/lifecycle changes.
6. Invoke ordinary selected-worktree finalization/cleanup only when the entire
   objective is concluded and all checks allow it. Preserve the exact refusal.
7. Verify each result independently and emit a compact complete receipt.
   A concluded-but-not-removed worktree remains visibly blocked, not "cleaned."

### Transfer is not issue creation

An issue records work; it does not by itself accept an old worktree's
responsibility. Transfer requires an existing operator authorization or
explicit recipient acceptance, an exact remaining roster, and a durable
receiving objective. Keep the original effort open until its lifecycle
permits release. If no recipient is available, retain the workspace and
record the proposed decision in the audit report, or post it only under an
approved apply-mode attention policy. Never create a recipient identity to
pass a gate.

### Mutation fencing is an implementation gate

Ordinary removal already re-checks its safety under the owning lifecycle
lock. Earlier semantic mutations also need protection: a new head, live
session, claim, user edit, or changed objective can invalidate an old review.
Discovery snapshots and pre-action re-reads alone are not a writer fence.

An explicitly approved read-only audit may identify fencing gaps without
implementing their remedies. Its conclusions are snapshot-bound proposals,
never cached authority for a later apply run.

Before implementing an apply-capable worker, prove that the existing owner
APIs can enforce candidate reservation plus expected revisions for each
authorized mutation, or add the
necessary ground-layer compare-and-apply operation through separate review.
Fail closed on conflicts. Never implement a parallel lock/registry that the
actual lifecycle writers ignore. Unknown-outcome writes require reconciliation
by operation identity before retry; no blind repeat posting or deletion.

## Downstream obligations

Follow the owning resource's lifecycle, not the apparent age of its claim.
Already-at-rest claim reconciliation is bookkeeping; unresolved child work is
not. A merged downstream PR must be verified in its actual repository and
base. Remote absence, unreachable credentials, stale sessions, or an
uninspectable child are holds. No raw remote cleanup or global claim sweep.

In audit mode, propose grouping repeated systemic blockers under one existing
issue in the report. An apply pilot may post a bounded evidence delta only
under its approved filing policy. Filing that defect neither closes the
affected objective nor grants permission to bypass the defect's safety gate.

## Results and operator attention

### Candidate-scoped steering, not a blocked sweep

The worker may propose a structured question using dispatch's existing
steering/attention contract. Each question names one candidate, evidence
fingerprint, exact decision, allowed choices, and consequences. Audit mode
includes the question only in its report. An apply pilot may post it under
an explicit attention policy; posting parks that candidate, not the sweep.
The worker continues other eligible candidates and can finish the bounded
sweep with a `decision-needed` result.

An approved posted question must outlive the daily worker and deduplicate
across runs. A later answer may select a narrow follow-up only through
separately approved execution machinery and under the original candidate
reservation. The worker revalidates the current state before acting.
An answer is not retroactive cleanup permission if the head, claims, or
objective changed. No timeout implies consent, no invented response or
recipient, and no automatic full-sweep wake for each question.

Whether this uses a proposed, candidate-specific task/card or another existing
attention record remains a charter decision. Never park the sole daily sweep
task indefinitely merely to wait for one candidate's answer.

Each run records an occurrence ID, charter version/mode, candidate limits,
runtime health, budget used, and totals for reviewed/concluded/removed/held.
Each candidate records its stable identity, snapshot revisions, evidence
references, remaining roster, exact action/operation identities, verified
durable results, refusal codes, and next owner or required decision.

Keep private transcript content, paths, machines and context within the
configured private state/report destination. Public issue comments use
generic, repository-appropriate summaries. Resolve tracker identity and
policy independently; no global auth switch.

Require corroboration rather than accepting the worker's final prose as
truth. Verify state changes and forge publication from their owning surfaces;
verify removal through lifecycle results plus subsequent inventory. A trusted
evaluator may confirm the bounded run while retaining safety holds. An
unfinished sweep is not submitted merely to trigger retirement.

Deduplicate attention by candidate and evidence fingerprint. Show holds,
failed operations, pending transfers and budget exhaustion without daily
comment spam. Pause/disable prevents new occurrences; it does not destroy an
in-flight worker or its obligations. Emergency suspension retains resumable
progress and accepted task ownership.

## Rollout and acceptance decisions

The FINAL prerequisite #6063 is satisfied by #6070, released through #6233,
with installed behavior verified. Successful finalize stamps FINAL; routine
fetch-free polling preserves it, while genuinely reopened responsibility
invalidates the conclusion. Fresh removal authorization remains separate.
This completed prerequisite neither authorizes a pilot nor removes any
candidate evidence or safety check.

1. Complete the remaining charter decisions, including scope, evidence
   threshold/evaluator, private report destination, filing authority,
   reservation semantics and budgets. The design-only baseline agreement
   does not select these parameters.
2. Approve an audit-only pilot on named candidates; manually compare its
   conclusions with full source/session evidence.
3. Resolve mutation fencing before implementing apply capability, then
   approve a separate safe-action pilot with a specific operation allowlist.
   Test adversarial resume/edit/claim races and unknown-outcome retries.
4. Evaluate correct holds, false conclusions, preserved responsibility,
   duplicate records, net worktree growth/reduction, and cost per useful
   conclusion. Count the tidier's own allocations so it cannot improve a
   removal metric while leaking worker worktrees.
5. Decide opt-in daily scheduling. Default scheduling requires another
   explicit policy review; there is no automatic audit-to-apply promotion.

Open charter decisions: exact project selection; transcript completeness
standard; bookkeeping/filing and transfer authorization; candidate revision
fencing; trusted evaluator; quiet-worktree age/cooldown; run budgets; durable
report destination; and whether daily adoption is per-project or one
machine/environment-wide serial sweep.
