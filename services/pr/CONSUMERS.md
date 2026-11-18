# Observational reviewer-feed consumers

The [reviewer view](REVIEWER.md) is a common input to a round-robin reviewer
coordinator, a Dispatch adapter and an independent shadow evaluator. None is
enabled by selecting the view or reading its SSE stream. This document defines
the consumer boundary; it does not install a coordinator, register an emitter,
create a task or authorize a review, rebase, CI retry or merge.

## One observation owner, separate consumer custody

Use the existing local client and admitted repository target. Retain the full
namespace/provider/endpoint/account/repository identity, configured consumer,
selected reviewer and original registration. Do not equate repository strings
or PR numbers across accounts or forges. The observation account does not select
the identity used for a later write.

Polling `next-actions` and consuming its SSE view must have the same selection
semantics. The stream supplies current first pages, not an archive of transient
rankings or a complete repository dump. If `more` is true, use `next_after`
with that view's projection `cursor` to collect further action pages. A changed
cursor requires restarting the current view; do not combine offsets from
different rankings. Keep the existing page and aggregate consumer-work limits.
A bounded partial pass is acceptable when recorded as partial, not as proof
that an omitted PR has no action.

Preserve `unavailable`, inventory coverage, observation/expiry and independent
attention reasons. An empty action page with unavailable evidence is not an
all-clear. Recheck freshness before admitting work; an old buffered frame can
expire after the service produced it. A cache read never refreshes the forge.

An action's proposed work identity includes the full admitted scope, PR,
reviewer, action/reason, candidate head/base and original cause event. Hashing
those fields is a convenient opaque correlation key, not authentication.
Do not include rank, current poll time, page offset or stream connection ID:
unchanged refreshes and duplicate frames must retain the same proposed work.
Keep this identity distinct from both the long-lived task identity and the
event-delivery identity.

## Round-robin reviewer coordinator

Use one bounded pass through the current ranked view and the coordinator's
existing owned workers/tasks, not one forge watcher per worker.

1. Reconcile any previously attempted assignment or notification through its
   original owner before choosing another effect for that PR. A lost response,
   missing local note or transport timeout is not proof of nonacceptance.
2. Select an eligible action for the configured reviewer. `actor_role: author`
   remains an explanation for the author; a reviewer must not perform its
   `rebase/stale_branch` recommendation. Preserve independent review reasons
   instead of converting the author action into reviewer authority.
3. Use the existing worker/task association to hand off the selected action.
   Selection and frame delivery are not claims. Recheck the task's current
   owner, generation, holds and action candidate at its real admission boundary.
4. Reconcile actual publication through the existing review/write owner and
   fresh observed activity. A worker's statement, task submission, SSE ID or
   content marker alone does not establish that the current head was reviewed.
   Preserve unknown outcomes and newer author activity.
5. After a confirmed outcome, suspend the same retained worker through its
   ordinary task lifecycle and give another eligible PR its turn. Retain the
   coordinator's current position in its existing durable owner. A process
   restart must not discard an unresolved assignment or create another worker.

Round-robin fairness is consumer policy layered on the service's existing
ranking, not a second attention engine. Rotation must not rewrite pending age,
clear an action, ACK another consumer or fabricate handled feedback. A reviewer's
own confirmed echo must not request another verdict; if the cache cannot
establish that relationship, report unavailable evidence rather than declaring
the echo handled.

## Dispatch new-task and steer adapter

The actual task owner is
[`agent-dispatch`](../../plugins/agent-dispatch/README.md). Its existing reviewer
recipe, exact dedup lookup, steering and suspend owners supply the relevant
boundaries; this feed introduces no new queue or task state schema.

Render the existing `reviewer` recipe with an explicitly configured target and
landing model. Use `recipes.dedup_key_for` for its stable target identity,
not an event/candidate-specific replacement task key. Resolve the forge-qualified
target through trusted configuration: do not pass a bare non-GitHub
`owner/repo` to a helper that interprets that shorthand as GitHub. Keep the
Dispatch harness lane separate from the PR's code repository.

Use `DispatchClient.lookup_dedup(key, repo=lane)` for exact current association,
then inspect the returned task through its owner. A capped task roster, title
search or local cached absence is not a negative lookup. A failed, unsupported
or ambiguous lookup remains unknown. More than one conflicting association
requires reconciliation, not arbitrary selection.

| Observed association | Proposed next step, only after separate effect admission |
|---|---|
| Conclusively absent, with no unresolved prior creation | Create through the ordinary deduplicated task owner using the existing recipe and original request identity. Reconcile its acknowledgement; do not spawn a worker directly. |
| Queued, reserved, claimed or actively executing | Preserve the same task and owner. Notify only when materially new activity needs delivery; unchanged input is NoOp, not a new task. |
| Suspended under its existing owner | Prepare an ordinary scoped steer/nudge with a distinct request identity for materially new activity. Only retries or reconciliation of that same notification reuse its identity and recovery record; wake through the existing task owner, not a separate process launcher. |
| Held, awaiting operator input, failed or terminal | Preserve that state and its original decision/settlement owner. A feed item does not answer a form, clear a hold, retry a failed operation or reopen completed work. |
| Unknown lookup or mutation result | Reconcile the original operation through the same owner. Do not create a replacement task or infer that a missing live dedup result settles an older operation. |

`DispatchClient.steer` supports the owner's current status/revision and
complete `expected_scope` (`queue`, `installation`, `machine`), with an
idempotency key and prepared-request recovery. Obtain those values from the
actual task owner, not from PR metadata. Negotiate the executing coordinator's
support before relying on those semantics; a prior coordinator silently
ignoring fields is not equivalent enforcement. Retain the original prepared
request after uncertainty instead of reconstructing it with a new key.
Materially new activity needs a distinct prepared request and identity, even
when the retained task has suspended again. Do not reuse an earlier accepted
notification's key for a changed payload or task revision: that can conflict
or replay the old acceptance without delivering another wake. An unchanged
action remains NoOp, not a reason to allocate another notification identity.

A stable task key prevents overlapping live review tasks; it is not an
exactly-once notification guarantee. Conversely, an action correlation key
prevents unchanged-input nudges only when the existing consumer owner durably
records accepted or unresolved requests. Neither key proves publication or
authorizes a task takeover. This contract does not claim that a normal
Dispatch startup already selects a repository-feed adapter.

## Delivery is not task handling

Keep three receipts distinct: PR-service event delivery, task-owner acceptance
and confirmed review/publication. Writing an SSE frame proves none of the last
two. Do not ACK on printing, selecting a row or attempting a task mutation.

If a separately activated consumer chooses ACK, first durably retain enough
original scope/event/task/request identity in its existing owner to reconcile
accepted or unknown effects. ACK only through the original registration and
delivered event IDs. Cumulative ACK must not skip an earlier unsettled delivery.
Task completion does not authorize ACKing another registration, and ACK never
marks the underlying PR action handled. Newer candidate activity stays visible.

Reconnect uses the same registration and scoped SSE ID. Duplicate current views
are expected. A gap or expired registration requires explicit recovery while
retaining unresolved task effects; it is not permission to create a fresh
baseline and forget them. Polling remains supported when streaming is absent.

## Executable inert reference

The source-only [reference planner](examples/reviewer_feed.py) exercises these
boundaries without becoming a shipped client command or an activated adapter.
It accepts a current `next-actions` page, an independently configured `Scope`,
retained notification evidence and an exact task-lookup callback. It returns at
most one `create`, `steer`, `noop`, `reconcile` or `read-page` proposal.
It never calls a task mutation, starts a worker, persists a request, ACKs an
event or marks a PR action handled.

The embedding maps `lookup(action)` to the existing task owner's exact
recipe/dedup lookup in the configured harness lane. `Task` is a detached test/
example projection, not a new task-owner wire response or an authority grant.
Its complete queue/installation/machine scope must match the configured
expectation. Missing, foreign or unknown association remains reconciliation.
The same applies when a prior accepted/unknown notification remains retained
but the task no longer appears in the live dedup lookup.

The example's `Notification` records come from the embedding's existing durable
request owner. Their acceptance means task-notification acceptance, not review
publication. Preserve original prepared requests and uncertain effects there.
A returned `notification_key` correlates a proposed action and selected task
revision; it is not itself a prepared request or an automatic retry license.
Persist the owner's real request identity before effects, reuse it only for
that notification's retries, and reconcile before proposing another mutation.

Repeated views and a later suspension/revision with unchanged PR activity
produce no wake proposal. Materially new candidate/cause activity produces a
distinct proposal for the same task. `last_served_pr` rotates the bounded
current view; only confirmed consumer progress advances the embedding's
durable round-robin position. The example requests further pages instead of
acting on a partial ranking, and retains unavailable coverage on an empty
view. Retain the actual request offset in `after`; a nonzero offset refuses
planning even when the last page says `more: false`. It is intentionally a
bounded complete-first-page example, not an automatic multi-page collection loop.

The [source controls](tests/test_reviewer_feed_example.py) run actual ReplayStore/
projection/query pages with a task-owner double, including unchanged and newer
activity, unknown outcomes, absent/replaced/foreign tasks, holds, stale/partial
coverage and author-owned rebase recommendations. They assert no delivery/ACK
or cache mutation from planning. They do not qualify real Dispatch
interoperability, durable request-owner integration or consumer activation.

### Retained Dispatch task adapter

The separate [inert adapter](examples/reviewer_dispatch.py) connects the planner
to the existing client's `lookup_dedup`, `get` and `prepare_steer` methods.
It is source-only example code, not an installed service command or a new
dependency of the foreground host. Select the existing Dispatch client and
its finite transport deadline explicitly in the embedding.

Supply a trusted harness `lane` and bind `task_key` with the example's
`reviewer_task_key(configured_target, land=configured_landing)`. It delegates to
the existing `recipes.render_recipe` and `recipes.dedup_key_for`, preserving
the native target identity across landing-policy changes. This narrow example
requires an explicit HTTPS repository target, including for GitHub; it rejects
bare `owner/repo` shorthand instead of silently assigning a non-GitHub target
to GitHub. Do not derive the harness lane, target or landing policy from a feed
body.
Pass `retained_task` as the planner's lookup callback. It performs exact
lane-scoped association lookup, then rereads the same task and checks its
dedup key, complete owner-reported `draft_scope`, current steering revision,
finite-task kind, holds and pending operator form.

The adapter deliberately does **not** convert an empty lookup into a creation
proposal. Although that lookup proves no live occupant for its exact key, it
does not return the full queue/installation/machine attestation supplied by a
task detail. Creation remains with the separately qualified task owner.
Ambiguous, missing, foreign, replaced or unsupported task evidence therefore
stays reconciliation, never a roster search or replacement task. Read failures
propagate without fallback or retry.

For an eligible retained-task proposal, `prepare_notification` rechecks the
current task and calls the native client's read-only `prepare_steer`.
That owner negotiates support and freezes the real request; the adapter never
calls `steer`, `steer_prepared`, task creation, submission, persistence or ACK.
Choose the original request ID through the embedding's existing durable
request owner, preserve the returned complete envelope there **before**
separately admitted effects, and reconcile unknown requests before preparing
another. Preparation is not acceptance, a wake, freshness proof for buffered
PR evidence or permission to act.

The [source controls](tests/test_reviewer_dispatch_example.py) exercise actual
cache pages, native task wire-shaped doubles, the existing native reviewer
recipe, exact-lookup request construction and prepared steering implementation.
They cover capability refusal before the task request, distinct forge targets,
stable task identity across landing-policy changes and detached request identity.
The HTTP boundary remains a GET-only double; these controls do not qualify a
live coordinator transport, a deployed target configuration, durable request-owner
storage or consumer activation.

## Inert qualification and operational exits

Consumer examples/tests must exercise actual cache views with synthetic
activities, retained IDs and task-owner doubles or isolated queues. Include
unchanged refresh/reconnect, newer candidate activity while a task is suspended,
author-owned recommendations, partial/stale evidence, conflicting associations,
lost creation/steer replies and restart with an unresolved request. Assert no
new task or repeated wake for unchanged input and no ACK/handled write from
selection alone.

Those are source controls, not evidence that a real reviewer, emitter or
coordinator is running. Independently admitted provider comparison belongs to
the evaluator; it must retain the same request accounting and distinguish
disagreement from stale/unavailable evidence. Source review, release,
installation, provider agreement and consumer activation remain separate exits
in the [canonical feed plan](../../efforts/active/durable-pr-watch-delegation/reviewer-action-feeds.md).
