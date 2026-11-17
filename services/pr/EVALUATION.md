# Finite reviewer-feed comparison

The source-only [comparison example](examples/evaluate_feed.py) compares one
selected cached PR and its reviewer action with an independently supplied
native observation. It performs no provider read, service query, task effect,
ACK or persistent write. It is not a new polling loop or an activated evaluator.

## Acquire through the existing owners

Read the selected PR through the [local query](LOCAL.md) and its action through
the [reviewer view](REVIEWER.md). Retain the full configured namespace/provider/
endpoint/account/repository, consumer and reviewer. The query `cursor` and
action view `event_cursor` must agree; otherwise the example reports unavailable
and the caller can perform another bounded coherent read. A current SSE sample
already supplies those views atomically, but its bounded first pages may omit
the selected PR.

Obtain the independent snapshot through the existing
[native observation owner](../../plugins/agent-worktrees/docs/pr-observation.md),
with explicitly admitted target/account and the same credential and request-
accounting owners that govern the observer. Count actual upstream requests,
including pagination and retries, against the existing aggregate allowance.
An independent reference is not permission for an unmetered raw API client,
another credential, a new permanent cache or a second poller.

The acquisition owner supplies a `Reference(identity, snapshot, observed_at)`.
Its fields do not authenticate the caller or prove how a snapshot was acquired.
Verify that provenance through the actual owner before using real evidence.
Native envelope validation and the example's full scope check prevent accidental
cross-target comparison; they are not credential-adoption receipts.

## Interpret the bounded report

`compare` accepts both cache views, the reference, explicit scope, PR, current
time and a finite freshness selection. It returns per-field statuses for
head/base, state, merge flag, draft, author and mergeability, plus a separate
action status/reason. Outcomes are `agree`, `different`, `unavailable` and
`stale`. There is no repository-wide all-clear or automatic remedy.

Missing members/facts, incomplete inventory, mixed cache generations, unknown
reference history, expired/buffered evidence and mismatched candidates never
become a successful action comparison. A field difference records two observed
values; separate reads can race, so it is not itself a service-defect verdict.
Keep acquisition times and original cursors with the external evidence owner.
Do not silently rebaseline or repeat an uncertain effect to make reports agree.

The example independently checks supported initial-review and author-owned
conflict recommendations from current native facts. It deliberately does not
re-run the cache's attention projection as its own oracle. A single current
snapshot cannot establish that an older review was observed before a later
candidate transition; causal follow-up ordering therefore remains unavailable
without independent historical evidence. Current-head agreement is not proof
of a reviewed base, handled feedback or publication authority.

The [source controls](tests/test_feed_evaluation_example.py) exercise actual
cache projection/query pages with separately constructed native envelopes:
initial/conflict agreement, changed/unknown candidates, terminal differences,
partial review coverage, causal-order unavailability, stale or future evidence,
mixed cache views and full scope refusal. These are comparison mechanics,
not actual acquisition, provider agreement or an installed evaluator.

## Remaining operational acceptance

Keep the [canonical comparison exit](../../efforts/active/durable-pr-watch-delegation/reviewer-action-feeds.md)
open until separately authorized real-provider acquisition and comparison are
measured through their existing owners. Preserve unsupported causal checks
explicitly. This example does not qualify every action or provider, install
anything, relax resource/request limits or activate an emitter/reviewer.

## See also

- [Local cache and events](LOCAL.md)
- [Reviewer queries and stream](REVIEWER.md)
- [Inert consumer boundaries](CONSUMERS.md)
