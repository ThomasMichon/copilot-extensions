# Reviewer-perspective action feeds

Review-first extension of the [canonical effort](README.md), coordinated by
#12947 under #6007. It extends the existing
[repository cache/event milestone](repository-cache-events-m1.md), not another
watcher, task queue or service. Existing integration, provider/native,
projection/replay, attention, ingress and lifecycle owners retain their scope.

## Requested outcome

Expose independently useful observational views for an explicitly selected
reviewer perspective: service/coverage status, current PRs and their states,
and priority-ordered expected next actions backed by PR activity. Preserve the
requested action/reason distinctions, illustrated with synthetic identities:

```yaml
next_actions:
  - pr: 7
    action: initial_verdict
    reason: author_create
  - pr: 8
    action: new_verdict
    reason: author_update
  - pr: 9
    action: rebase
    reason: stale_branch
```

These views must support subscriptions through SSE or WebSockets. The same
read model should make a round-robin reviewer coordinator and a future Dispatch
new-task/steer/nudge adapter straightforward to implement. With no active
consumer driving effects, the service remains observational and can be
spot-checked against separately admitted provider reads.

This is the portable capability capture. Repository/account selection and
consumer policy remain explicit adopter configuration. Source planning grants
no service deployment, credential, review, task-emission or production-write
authority.

## Vision and existing owners

This closes the existing
[role-aware fair-attention](../../../visions/plugins/agent-pull-requests/README.md#role-aware-fair-attention),
[shared observation](../../../visions/plugins/agent-pull-requests/README.md#centralized-watcher-and-registrar),
[service-consumer](../../../visions/plugins/agent-pull-requests/README.md#author-and-reviewer-service-consumers)
and [durable delivery](../../../visions/plugins/agent-pull-requests/README.md#durable-transport-appropriate-delivery)
intent. SSE is a recommended initial realization of one-way delivery, not a new
north star. Follow existing
[contract evolution](../../../docs/patterns/service-contract-evolution.md)
and [local ingress](../../../services/pr/LOCAL.md), preserving old callers.

| Existing owner | Reuse and remaining seam |
|---|---|
| `RepositoryProjection`, `ReplayStore`, repository subscriptions | Current facts/events, one namespace/writer and durable registration/delivery positions; add only the bounded projection metadata actually needed, through its existing versioned owner |
| `attention.project` and `attention.rank` | Inert reason projection, priority and aging; do not add another attention policy engine |
| Optional retained `AttentionReader` | Preserve protected workspace/feedback routes; its required write/conversation owners and envelope versions are not prerequisites for a read-only repository-cache feed |
| Standalone local host, Unix ingress and local CLI/client | Add capability-directed query/stream operations here, not a second HTTP service or a direct state-file reader |
| Native/provider snapshot and account-qualified acquisition | Qualify actual reviewer/head/activity completeness here; feed reads never fetch a provider or select another credential |
| Dispatch/task and reviewer publication owners | Own task association, claiming, nudging, suspension and confirmed feedback; a recommendation grants none of these effects |

## Evidence and projection contract

Every result is scoped by namespace, provider, endpoint, repository, selected
reviewer identity and the admitted target/consumer. It retains current
candidate/head, causing activity/event identity, observation time, freshness
and coverage. Querying the queue or adding subscribers cannot increase forge
traffic.

The current cache retains bounded author/candidate/mergeability facts, review
actor/state/head references and activity fingerprints. Those fields alone
are not a universal proof of complete review history or who caused every
change. The first slice must qualify the exact evidence needed for each
action. Missing, truncated, stale or unclassified evidence stays unavailable;
an empty review array is not automatically an initial review, and a matching
marker is not confirmed handled feedback.

Reuse attention projection/ranking from the existing owners. Preserve stable
pending cause/time and aging across unchanged refreshes/restart; do not use
the latest poll timestamp as a new pending start. If minimal extra metadata
is necessary, declare its version/adoption/retention through the current
repository projection and complete-envelope owner. Do not copy PR bodies,
create a second conversation archive or silently reinitialize retained state.
Protected publication/workspace adoption is not required merely to read this
observational view.

`initial_verdict/author_create` identifies a supported initial-review cause;
`new_verdict/author_update` identifies supported newer author-side candidate
or conversation activity relative to actual reviewer evidence;
`rebase/stale_branch` preserves the qualified stale/conflicting-candidate
condition. Action ownership stays explicit: a reviewer-facing queue can
explain a required author action without authorizing the reviewer to perform it.
Retain independent actionable reasons rather than letting an example vocabulary
erase existing CI/follow-up/merge distinctions.

## Serial plan and exits

- [x] **Review the contract.** Completed;
  [review and next-slice record](journal/2026/11.16%20Reviewer%20action%20feed%20contract.md).
  Retain all broader M1/Author/Reviewer/provider/client/fleet outcomes.
- [ ] **Qualify activity evidence and the cache read.** Select the minimal
  existing native/projection seam, then expose a bounded reviewer-perspective
  `next_actions` page through the actual service and local client. This is the
  first independently testable runtime slice, not an optional factory alone.
  Keep status and PR-list semantics compatible.
- [x] **Stream the same views.** Source implementation merged in #13038.
  Implement SSE over the existing local ingress
  as the initial one-way transport, covering status, PR-list and next-action
  changes. Preserve exact registration/cursor scope, reconnect/resume, explicit
  gaps and bounded slow-consumer behavior. Any separately hosted realization
  must advertise actual support; no new TCP listener is implied.
- [ ] **Qualify inert consumers.** Document and test a round-robin coordinator
  and Dispatch adapter against the same feed/identities without activating
  either. Associate existing/suspended work through the actual task owner;
  enqueue/steer/nudge only when separately authorized and materially justified.
  The shared [consumer contract](../../../services/pr/CONSUMERS.md) identifies
  the actual task/recipe/steering owners. The bounded source reference and five
  cache-backed controls merged in #13055; actual task-owner adapter and durable
  request-owner interoperability remain separate qualification.
- [ ] **Qualify observational comparison.** Supply a bounded evaluator path
  comparing current state and causes with independently admitted provider
  reads, distinguishing disagreement from stale/unavailable evidence. Runtime
  source, release, installation, running proof and real-provider agreement
  remain separate exits.
  The [finite local comparison CLI and pure reference](../../../services/pr/EVALUATION.md) keep
  current-fact differences, stale/unavailable evidence and unproven causal
  ordering distinct. The local client reads one PR and first action page under
  one finite deadline, using an independently supplied native envelope; it
  performs no provider acquisition, subscription, ACK or automatic retry.
  Actual admitted reference acquisition and provider agreement remain open.

## Streaming and consumer boundaries

Reuse the current registration, event, delivery and ACK owner. SSE delivery or
`Last-Event-ID` does not mean an action was performed, an event was handled or
another registration advanced. Do not ACK merely because bytes were written.
Resume must retain the original admitted consumer/target and expose a gap rather
than fabricate a new baseline. Existing polling consumers remain supported.

Keep response/frame bytes, client count, buffering, original request lifetime
and cleanup finite. An expiring stream can reconnect through the same admission
and cursor contract; it cannot silently renew authority. Foreground withdrawal
must stop new streams and join existing request/native/disk owners, including
slow or disconnected subscribers, without a replacement lifecycle controller.

A round-robin coordinator selects one eligible next action, delegates through
its existing worker/task owner, reconciles actual completion and advances.
Selection is not a claim or handled write. Confirmed observed feedback must not
hide newer author activity or make the reviewer's own echo request another
verdict. A future emitter uses stable PR/action/cause/task association and the
existing suspension/steer contract, not one forge poller per worker. Unchanged
observations remain NoOp rather than repeated nudges.

## Validation plan

- [ ] Cover initial creation, newer author candidate/conversation activity,
  actual reviewer baseline, stale branches, terminal/draft/selection changes,
  multiple actors and explicit missing/partial/stale evidence.
- [ ] Verify deterministic priority and stable pending/aging through unchanged
  refresh, restart and compatible adoption; preserve unresolved delivery and
  original cause identities.
- [ ] Exercise the actual service/client query with one shared observation
  owner, target/account isolation, bounded pages and no query-triggered forge
  reads or protected-write prerequisite.
- [x] Exercise all three feeds, resume/reconnect, duplicate delivery, gaps,
  cancellation, original expiry, slow-consumer backpressure and graceful drain.
  Local Linux source controls merged in #13038; release, installation and
  actual provider qualification remain separate exits.
- [ ] Prove consumer examples are inert, use existing task association and
  genuine feedback/activity, and neither duplicate tasks nor wake unchanged
  suspended work.
- [ ] Keep evaluator source tests distinct from separately authorized
  real-provider comparison and operational installation/consumer activation.

## See Also

- [Canonical effort and retained acceptance](README.md)
- [Repository cache/event milestone](repository-cache-events-m1.md)
- [Filter definitions and owned population demand](filter-subscriptions.md)
- [Author/Reviewer perspectives](author-reviewer-perspectives.md)
- [Local service interface](../../../services/pr/LOCAL.md)
