---
name: review-pull-requests
description: >
  Charters the sustained pull-request reviewer role: configurable independent
  review lanes, retained per-PR follow-ups, repository-specific verdict and
  merge policies, and agent-to-agent communication through existing owners.
  Not a single code review, contributor sign-off, backlog bulk closure or
  service repair.
  Trigger phrases include:
  - 'set up sustained PR attention'
  - 'configure independent PR review lanes'
  - 'set up a continuous PR review flow'
  - 'charter a pull-request reviewer role'
---

# Review Pull Requests (Sustained Role)

Prepare an operator-authorized, repository-owned setup contract, then verify
the available adapters before starting it. This is guidance, not an executable
review engine or authorization to install services, create a worker fleet,
change branch policy or repair another owner's queue.

## Existing owners

This skill lives in agent-roles because it charters a sustained role over
existing owners, not because Dispatch is mandatory. Dispatch task/recipe
lifecycle, native Task or Bridge adapters all remain valid; a role charter need
not live in a recipe.
Resolve [guidance sources](references/setup-and-adapters.md#resolve-guidance-through-its-owners)
before binding roles or treating custom guidance as authority.

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

This skill owns the multi-reviewer coordination contract: census, eligibility,
concurrency, lane refill and coordinator responsibilities. A selected reviewer
charter owns one PR's judgment and same-PR follow-ups, including its permitted
publication/readback. Do not copy the loop policy into that charter or turn a
single reviewer into a second coordinator.

Read [setup and adapters](references/setup-and-adapters.md),
[coordination and recovery](references/coordination-and-recovery.md),
[communication](references/communication.md) and
[throughput and loop discipline](references/throughput-and-loops.md) when
performing the corresponding steps below. These references specialize this procedure, not the owners'
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
- A **positive integer concurrency target**; reject zero, negative, fractional
  and boolean values. Treat it as the desired minimum of active independent
  reviewers while eligible work and capacity exist, not an arbitrary ceiling.
  Refill promptly and use additional available capacity when authorized.
  Preserve explicit operator/host limits and budgets; record a genuine
  work/capacity shortage rather than launching dummy reviews or new resources.
  Per-worktree targets share machine capacity: leave headroom for other active
  worktrees and coordinators rather than multiplying the target blindly.
- Continuous mode or finite drain, with a concrete stop criterion and owner.
  An empty continuous census is not completion; finite drain requires a fresh,
  complete terminal census and settlement of the charter's obligations.
- Acting identities, reviewer rubric, coordinator/author roles, adapter
  selections, repository-specific review/merge policy and escalation route.
- Optional author nudges and cooldown/limit, existing observation cadence and
  owner-charter refresh cadence **only if explicitly requested**.
- Loop discipline: re-review triggers, per-PR round cap and time budget,
  unchanged-window count and backstop watch cadence, closed hold vocabulary
  with owner/revisit/default, and the consent follow-up route.

Do not inherit another adopter's holds, timing, merge exceptions or topology.

## 2. Discover policy and qualify adapters

Follow the setup reference's capability inventory. Resolve each target's actual
PR profile, current actor/authentication, contribution/review rubric and
provider/task bindings **before** prescribing review or landing policy.
Record the supported operation, owning command/tool, observed capability and
explicit unsupported behavior. A package version or tool name is not support.

Missing tools or bindings actually required by the selected owner mean a named
adapter blocker or a permitted manual handoff. Server write fences are a
capability, not a universal prerequisite: record the available revision binding,
freshness checks, readback and residual races instead of inventing a fence or
refusing an otherwise authorized provider. Do not invent a task, claim, session,
receipt or authorization. An unbound source API is not an installed loop.

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
Return genuine source/base/discussion updates to the original reviewer through
its retained handle; start a fresh context for an unrelated PR. If that context
is actually unavailable, use supported recovery or a documented handoff retaining
prior findings, replies, receipts and uncertainty, not a claim of preserved
conversation. Refill available active lanes while suspended reviews retain their
pending work and lineage.

Independent reviewers read exact candidate source **statically**: never execute
untrusted candidate code, author its fixes, supply author consent or merge it.
The coordinator owns census, assignment, integration and verified verdict
handling; the author retains the PR, worktree, fixes and consent. Changing a
lane or receiving an ACK transfers none of those resources. When taking over a
scope from a predecessor coordinator or reviewer, tell it and release or retire
its overlapping task, claims, watches and nudges through their owners after
accepted transfer; two reviewers never serve the same scope.

## 5. Review, post, reconcile and follow up

By default the independent reviewer owns each qualifying round end-to-end:
analysis, supported addressed bot-thread reconciliation, **direct task/owner-bound
verdict publication** and authoritative readback. Bind actual scope, author
independence and the retained handle once through the existing assignment owner
before authorized writes; do not require a coordinator's per-round approval or
release handshake. The reviewer asynchronously returns its actual posted,
verified receipt or real blocker. The coordinator is never the normal verdict
relay/editor: it monitors outcomes, coverage and unresolved obligations, and
retains census, follow-ups and authorized normal merge stewardship. Additional
coordinator checks must address a concrete risk or integration need, not impose
a duplicate per-write forge audit. Direct posting grants no author-fix, merge,
consent or resource-custody authority.

Apply the coordination reference's separate analysis, publication and readback
gates. Use supported provider preconditions and revision pinning without making
server fencing mandatory for every system. Otherwise refresh relevant candidate/
review/thread/check state immediately before writing, persist the exact reviewed
revision and intent, then verify authoritative readback and current state.
Fresh reads and commit pinning do not make a read-then-write sequence atomic;
retain historical effects and reassess on movement. Supplement partial reads
with complete evidence and preserve every actual owner-required safeguard.

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

Start a round only on a qualifying trigger: a new head, author input, an
overlapping base change or a relevant check/policy change. **Never republish an
identical verdict on an unchanged head without new input**; a timer, watch
renewal, unrelated merge or restart is not new input. Cap rounds without new
author input per PR, then report the loop once and stop. A watch timeout is a
normal no-change result: after the charter's unchanged-window count, back off
to the backstop cadence instead of renewing at a fixed short cadence. Apply
[throughput and loop discipline](references/throughput-and-loops.md).

Use the communication reference for same-context missing-input follow-ups,
author replies and explicitly enabled nudges. Prefer existing coalesced
subscriptions/events with their **actual returned cursors**. When webhooks or
a subscribable owner are unavailable, bounded shared polling at the authorized
cadence is legitimate; retain observation clocks, compare changes and back off
on errors. Do not invent cursors, duplicate collectors, repeatedly relaunch
reviewers or poll native Tasks that already provide completion notifications.

## 6. Land, settle or keep attending

Measure progress as **zero eligible PRs awaiting reviewer action**. A PR
leaves the active set only on an observed terminal state or an explicit
hand-back naming the next owner: author change, author consent, named input or
a hold with owner, revisit time and default. After approval without required
consent, re-check live state and send one bounded consent request per cooldown;
do not re-review to prompt it.

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
