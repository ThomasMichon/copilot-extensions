# Cache-only reviewer action queries

The optional reviewer view extends the existing
[local repository service](LOCAL.md), not its credential, acquisition or
publication authority. It uses the same ReplayStore transaction, observation
cycle, private Unix ingress and local client. Queries do not fetch a forge,
claim tasks, post feedback, advance delivery or write handled state.

## Select and discover support

Set `"reviewer_actions": true` in the trusted `repository-local/v1` host
configuration. The default is false. Parsing remains passive; this is not a
service launch, provider qualification or deployment grant.

The distinct generation-three `repository-filtered/v1` profile remains
unchanged and does not advertise reviewer actions. Selecting this view with
filtered storage refuses before acquisition or state adoption; the query
cannot bypass a registration's filtered visibility through the broader
repository route.

The running target's capabilities then include `reviewer_actions` with
protocol `agent-pull-requests.reviewer-actions/v1`, storage
`repository_activity_version:1`, operation `next-actions`, `effects: none`
and `forge_reads: false`. Its `page_cursor: projection-sha256` support qualifies
the continuation fence. Clients discover this actual support before using the
operation. Older hosts or an unselected profile refuse; there is no fallback
to direct provider reads, another account or the optional protected
workspace/feedback readers.

```text
agent-pull-requests-service next-actions --socket /data/pr-cache/local.sock --namespace installation-a --target example-gitea --reviewer reviewer-a --limit 16
```

The corresponding HTTP operation is
`POST /v1/repositories/TARGET/next-actions` under the existing cache request
envelope and OS-admitted principal/target/consumer binding. Its request requires
`reviewer`; optional fields are `after`, `limit`, `cursor` and
`max_age_seconds`. Select the actual acquired actor login, including a GitHub
App's `[bot]` suffix; aliases are not substituted. `after` is an action-list
offset, not a PR number. Pages
contain at most 16 actions; the freshness default is 1,800 seconds, selectable
in 1..86,400. A nonzero `after` requires the returned `cursor`, a fingerprint
of the actual ranked view and its scope/options. Eligibility changes from
expiry, snapshot failure/recovery or ranking changes reject continuation before
applying the offset, even when no durable event was emitted. Ordinary clock
movement with an identical eligible view does not invalidate it.

The separate numeric `event_cursor` retains the durable event position.
The projection fingerprint is not an event ID, delivery position, ACK or
authority. A changed view requires restarting the query, not silently merging
different positions or inventing a new continuing registration.

## Evidence and action ownership

Each result retains the full repository identity, reviewer, consumer, event
cursor, current inventory status, `next_actions` and explicit `unavailable`
reasons. Action rows retain full head/base candidate IDs, causing event and
original pending time, observation/expiry, actual selected review metadata,
priority and independent attention reasons.

The existing pure attention owner projects and ages the queue:

- `initial_verdict/author_create` requires qualified history with no submitted
  verdict from the selected reviewer. Draft and `REQUEST_REVIEW` records are
  not submitted verdicts.
- `new_verdict/author_update` requires the current submitted review set to have
  been observed before a later cached candidate transition, with no review
  matching the current head. Inequality or reported review timestamps alone
  do not prove this order: a newly observed record is
  `review-order-unavailable`, including an older draft submitted later during
  non-atomic acquisition. Returned `review` metadata is a supporting record,
  not a claim that its reported timestamp proves the latest submission.
- `rebase/stale_branch` is an author-owned conflicting-candidate action. A
  reviewer-facing recommendation does not authorize the reviewer to rebase;
  independent review reasons remain visible.

Terminal, draft and self-review interests do not become reviewer actions.
Missing author/candidate identity, incomplete history, invalid reviewer
timestamps and stale/unavailable evidence remain explicit. Blank submitted-review
actors make the review evidence unavailable; actorless draft/request records
remain non-verdicts. A review matching
the current head alone does not establish its reviewed base: that result is
`reviewed-base-unknown`, not fabricated handled feedback.
An absent, empty or whitespace-only author remains `author-unavailable`,
including for author-owned rebase recommendations; it cannot establish that the
selected reviewer is a different actor.

When a submitted record from the selected reviewer is dismissed, the unchanged
unavailable-item shape reports `reason: review-dismissed` instead of the generic
`review-activity-unavailable`. This is a diagnostic refinement, not a new
eligibility rule: dismissal does not prove that the reviewer never acted, that
a prior review still applies, or that its head precedes the current candidate.
The query preserves the cached history and does not manufacture an initial or
follow-up verdict. Consumers must retain unavailable coverage regardless of
the reason string; reasons are explanatory, not an exhaustive action enum.
Independent author-owned rebase recommendations and higher-priority missing
or stale evidence keep their existing behavior.

Body-only/prior Gitea readers leave `reviews_complete` unknown because hidden
pending drafts can create empty intermediate pages. An explicitly selected native
reader can instead supply qualified authoritative pagination evidence through
the [native coverage contract](../../plugins/agent-worktrees/docs/pr-observation.md).
This query never infers that support from a package version or an empty array.
Synthetic tests with explicit complete evidence establish query mechanics, not
qualification of a particular installed provider. Conversation-only follow-up
causes, round-robin/Dispatch activation and observational
provider comparison also remain separate exits.

## Local SSE subscriptions

The same selected local ingress advertises `stream` protocol
`agent-pull-requests.repository-stream/v1`. Older or filtered hosts do not
advertise it. The finite Fleet connector does not claim streaming support.
Create or reconcile an ordinary continuing registration first, then use:

```text
agent-pull-requests-service stream --socket /data/pr-cache/local.sock --namespace installation-a --target example-gitea --registration-id REGISTRATION_ID --reviewer reviewer-a --timeout 60
```

`POST /v1/repositories/TARGET/stream` uses the normal admitted JSON envelope.
Its request requires `registration_id` and `reviewer`, with optional `limit`
(1..16), `max_age_seconds` (1..86,400), `seconds` (1..3,600) and `interval`
(1..300). The original ingress admission deadline is an independent ceiling,
including header/body reads, transaction waits, frame writes and cleanup.
The CLI emits JSON objects containing SSE `event`, `id` and `data`.

Each atomic sample projects `status`, `pulls` and `next-actions` from the same
cache transaction. Status includes registration expiry, delivery/ACK positions,
inventory and bounded event identity/kind/PR summaries. Pulls and actions are
bounded first pages with their existing explicit continuation semantics; they
are not a complete unpaginated repository dump. Use the existing query/events
operations for further pages or full retained event facts. A snapshot can be
newer than the accompanying delivery batch; its independent current event and
projection cursors remain visible. Streams reevaluate cached freshness/ranking
without requiring a new durable event and never trigger forge acquisition.
Full samples honor the selected interval even while event delivery is backlogged;
catch-up does not accelerate store validation or view reprojection.

Reconnect with `--last-event-id ID` (HTTP `Last-Event-ID`) and the same
registration, reviewer, page limit and freshness selection. The ID binds that
selection and its delivered event position. Resume emits current first-page
views again, not a replay archive of transient rankings; duplicate delivery is
expected. Missing original registration refuses, changed selection returns 409,
and an unavailable delivery position returns 410. After headers, an equivalent
failure is an SSE error. Recovery is explicit; no new baseline/registration or
expiry renewal is invented.

Writing a frame records delivery only, **never ACK or handled feedback**.
Existing explicit ACK and original-ID replay remain authoritative. Cancellation
or registration expiry cannot admit new event delivery; already delivered
unsettled identities stay protected. Eight shared ingress connections, 1-MiB
frames, 16-MiB streams, 256 chunks and original admission expiry bound slow
consumers. There is one sampled frame set per connection, no unbounded queue.
Disconnect/withdrawal closes admission and joins any started disk operation
before writer release. A premature EOF or cap closure retains IDs and is not
reported as a successful handled action.

## Existing-envelope adoption

Selection adds the orthogonal `repository_activity_version:1` extension to
the existing continuing cache, not another database or cache version. Fresh
provisioning can select it directly. For an existing valid continuing envelope,
foreground startup adopts it atomically under its existing writer lease,
operation lock and lifecycle checkpoint before opening ingress.

Adoption preserves the complete replay/review envelope, subscriptions, original
request and event identities, delivery/ACK positions and retained history.
Prior facts receive unknown activity fields; prior current rows have no
invented cause until a fresh qualified observation publishes one. Unchanged
candidate/conflict refreshes and restart retain their original cause and age.
Bounded content fingerprints retain the submitted records actually observed
and their pre-candidate frontier; they are not handled receipts or reviewed-base
proof. That minimal ordering evidence survives settled event-body retirement
and is retained only for current qualified review history.
The existing cache byte/event bounds still apply.

Old strict readers refuse the adopted extension. There is no implicit
downgrade, recreation, deletion, writer takeover or claim that a package version
proves compatibility. Existing reader/lifecycle exclusion and deployment gates
remain with their owners.

## See also

- [Local configuration, admission and lifetime](LOCAL.md)
- [Round-robin and Dispatch consumer boundaries](CONSUMERS.md)
- [Finite local comparison CLI and independent-reference contract](EVALUATION.md)
- [Repository cache source contract](../../plugins/agent-pull-requests/docs/repository-cache.md)
- [Reviewer feed effort and remaining acceptance](../../efforts/active/durable-pr-watch-delegation/reviewer-action-feeds.md)
