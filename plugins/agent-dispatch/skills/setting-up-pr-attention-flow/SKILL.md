---
name: setting-up-pr-attention-flow
description: >
  Sets up sustained pull-request attention with configurable independent
  review lanes, retained per-PR follow-ups, repository-specific verdict and
  merge policies, and agent-to-agent communication through existing owners.
  Not a single code review, contributor sign-off, backlog bulk closure or
  service repair.
  Trigger phrases include:
  - 'set up sustained PR attention'
  - 'configure independent PR review lanes'
  - 'set up a continuous PR review flow'
---

# Setting Up PR Attention Flow

Prepare an operator-authorized, repository-owned setup contract, then verify
the available adapters before starting it. This is guidance, not an executable
review engine or authorization to install services, create a worker fleet,
change branch policy or repair another owner's queue.

## Existing owners

Use `efforts:planning-efforts` for a substantial setup's canonical plan and
review gate; `agent-worktrees:working-cross-repo` and
`agent-worktrees:worktree` for repository policy and resource custody.
Use `agent-conduct-guidance:using-multi-agent-rules-of-engagement` before peer
communication. Select the existing host's native Task/agent tools for in-session
work; use `agent-bridge:agent-bridge` for cross-boundary/live-peer transport,
not as a prerequisite for native Task. Discover PR operations from the existing
PR owner, optionally `agent-pull-requests`, and task operations from
`agent-dispatch`.
Resolve exact commands from their owning session catalogs, not ambient PATH.
Missing optional siblings do not require installing them: an existing host
adapter can serve the same bounded role if its actual contract qualifies.

Read [setup and adapters](references/setup-and-adapters.md),
[coordination and recovery](references/coordination-and-recovery.md) and
[communication](references/communication.md) when performing the corresponding
steps below. These references specialize this procedure, not the owners'
ambient policies.

## 1. Capture scope and original authority

Read the original grant and current repository instructions before selecting
policy. Record its authoritative reference, affected resources, allowed
effects, exclusions and revocation/hold conditions. Missing original authority
is a blocker, not something a charter, message or cached approval can supply.
Reuse settled choices; ask only about a genuinely missing setup choice.

Fill [the charter](assets/CHARTER.md) into reviewable target-repository content
under that repository's existing planning convention. Capture:

- Provider, repositories, bases, eligible PR population and excluded
  authors/forks; whether new inflow is included.
- A **positive integer lane limit**; reject zero, negative, fractional and
  boolean values. It bounds concurrently active independent reviewers,
  not the number of unresolved PRs or a permission to launch new resources.
- Continuous mode or finite drain, with a concrete stop criterion and owner.
  An empty continuous census is not completion; finite drain requires a fresh,
  complete terminal census and settlement of the charter's obligations.
- Acting identities, reviewer rubric, coordinator/author roles, adapter
  selections, repository-specific review/merge policy and escalation route.
- Optional author nudges and cooldown/limit, existing observation cadence and
  owner-charter refresh cadence **only if explicitly requested**.

Do not inherit another adopter's holds, timing, merge exceptions or topology.

## 2. Discover policy and qualify adapters

Follow the setup reference's capability inventory. Resolve each target's actual
PR profile, current actor/authentication, contribution/review rubric and
provider/task bindings **before** prescribing review or landing policy.
Record the supported operation, owning command/tool, observed capability and
explicit unsupported behavior. A package version or tool name is not support.

Missing task-bound review tools, original receipts or supported write fences
means a named adapter blocker, or a review-only/manual handoff permitted by
the repository. Do not invent a task, claim, session, receipt or authorization.
An unbound source API is not an installed operational loop.

## 3. Establish coordination and a complete census

Keep the authoritative charter reviewable. Put mutable coordination state
outside the candidate/source repository unless the repository already owns
that contract. One optional reference pattern is a session SQLite lane/task/
pending-work **index** plus a durable versioned JSON ledger, illustrated by
[synthetic coordination data](assets/ledger.example.json). Neither is an engine,
forge snapshot, task queue or authority.

Persist original bindings and complete receipts, all prior rounds, unresolved
findings/replies, holds/consent uncertainty, watchers/cursors and observed
terminal outcomes. Session SQL alone does not survive new-owner handoff.
Refresh a complete, paginated live forge census; partial/failed enumeration is
unknown or a lower bound, never an empty queue.

## 4. Assign independent lanes and retain context

Deduplicate by actual provider/repository/PR identity through the existing
task/host owner. Bind only naturally returned task and reviewer handles.
Keep one retained reviewer context per PR across genuine source/base/discussion
updates; start a fresh context for an unrelated PR. Refill available active
lanes promptly while suspended reviews retain their pending work and lineage.

Independent reviewers read exact candidate source **statically**: never execute
untrusted candidate code, author its fixes, supply author consent or merge it.
The coordinator owns census, assignment, integration and verified verdict
handling; the author retains the PR, worktree, fixes and consent. Changing a
lane or receiving an ACK transfers none of those resources.

## 5. Review, post, reconcile and follow up

Apply the coordination reference's separate analysis, publication and readback
gates. Fresh complete candidate/review/thread/check reads are evidence, not
atomicity. **Supported server write fences or atomic provider preconditions**
must bind a mutation to its actual candidate/round; naked read-then-write is
not a safe optimistic transaction. Supplement intentionally partial tool reads
with a fresh complete snapshot, never replace a server fence with that snapshot.

Retain exact original receipts and unknown provider effect IDs as null;
reconcile an uncertain post before retry. Verify actual official, non-dismissed
forge review authority and provider-native review/comment effect IDs as returned;
for a numeric-review-ID provider, verify its actual numeric review ID, not prose
approval or a tool's success footer. A Copilot native conversation UUID stays
null unless actually observed; it is not an extra prerequisite for valid static
review and cannot invalidate a legitimate verified forge post merely because
that UUID is unknown. Returned agent handles and forge effect IDs are distinct.
On movement, reprepare the same context and review the justified delta; use the
full gate for invalid lineage, uncertain coverage or escalation. Compatible
official retained-head authority may avoid duplicate reviews only when the
target's actual policy permits it.

Use the communication reference for same-context missing-input follow-ups,
author replies and explicitly enabled nudges. Wait through existing coalesced
subscriptions/events with their **actual returned cursors**. No polling,
relaunch or repeated status-message loop; respect bounded owner retry policy
and known transient backoff without losing the original operation identity.

## 6. Land, settle or keep attending

Select the target's automatic, agent-consent, authorized direct-self or human
merge adapter. Approval is not author consent. A direct-self profile does not
invent a missing consent-label gate, and a configured profile alone does not
authorize this actor. Required reviews/checks, conflicts, drafts and holds
remain independent gates.

Only an actually authorized existing normal consumer handler may provide
coordinator merge backup. No generic administrative bypass or self-deputization.
Verify the actual provider merge separately from review publication and consent;
settle owned effects/resources through their owners. Preserve unresolved work
and the parent completion gate in the selected checkpoint or accepted handoff.
Report readiness, review-only limitations or concrete blockers honestly.

## Setup acceptance

Before activation, walk the charter's failure cases with the actual adapter
owners and record results/unknowns. Static skill/example tests qualify only
documentation structure, not runtime, clean-room or live-provider behavior.
Return the charter location, adapter/policy table, lane choice, census
completeness, retained state/owner references, stop criterion and next action.
Do not declare a continuous service complete because setup or a PR merged.
