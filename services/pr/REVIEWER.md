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

The native body-only Gitea reader currently leaves `reviews_complete` unknown
because hidden pending drafts can create empty intermediate pages.
Consequently this source does **not** yet qualify real Gitea initial/new-verdict
recommendations. Authoritative pagination evidence remains a separate native
qualification. Synthetic tests with explicit complete evidence establish the
query mechanics, not real-provider coverage. Conversation-only follow-up
causes, SSE/WebSocket feeds, round-robin/Dispatch activation and observational
provider comparison also remain separate exits.

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
- [Repository cache source contract](../../plugins/agent-pull-requests/docs/repository-cache.md)
- [Reviewer feed effort and remaining acceptance](../../efforts/active/durable-pr-watch-delegation/reviewer-action-feeds.md)
