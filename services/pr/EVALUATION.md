# Finite reviewer-feed comparison

The [packaged comparator](src/agent_pull_requests_service/evaluation.py) compares
one selected cached PR and its reviewer action with an independently supplied
native observation. The [example imports](examples/evaluate_feed.py) remain
compatible. The pure comparator performs no acquisition or effects; the finite
local CLI below adds cache queries, not a polling loop or activated evaluator.

## Compare through the local client

```text
agent-pull-requests-service compare --socket /data/pr-cache/local.sock --namespace installation-a --target example-gitea --consumer comparison-reader --reviewer reviewer-a --number 7 --reference /data/reference.json --timeout 60
```

The reference is an ordinary JSON file of at most 1 MiB with exactly these
fields:

| Field | Value |
|---|---|
| `schema` | `agent-pull-requests.comparison-reference` |
| `version` | `1` |
| `identity` | Full `namespace`, `provider`, `endpoint`, `account` and `repo` selection |
| `observed_at` | Acquisition owner's integer Unix timestamp |
| `snapshot` | Original `agent-worktrees.pr-snapshot` version-1 native envelope for the selected PR |

The acquisition owner supplies that file through the admitted path below.
Reference metadata is not authenticated acquisition evidence or permission to
fetch a provider. The CLI validates its bounded input and selected identity,
discovers the running local reviewer capability, and requests the selected PR
and first 16 actions once. Capability discovery and both requests share one
finite deadline of 1..60 seconds; an existing earlier client deadline is retained.
There is no automatic retry, pagination, subscription, ACK, native/provider read,
service startup or persistent write.

A missing action on a nonterminal first page remains unavailable, not proof
that the PR has no action. Concurrent cache movement also remains unavailable;
the command does not keep querying until it obtains agreement. Use another
separately bounded comparison when appropriate, preserving actual acquisition
times and original evidence.

The JSON report retains full selection, acquisition/comparison times and both
cache event cursors alongside the per-field and action outcomes. Exit zero
means a report was produced, including a `different`, `stale` or `unavailable`
result; it is not an all-clear. Exit two means input or local transport/support
was unavailable. Unsupported core/client combinations refuse this operation
without changing the existing query commands or falling back to forge reads.

## Acquire through the existing owners

The CLI uses the [local query](LOCAL.md) and
[reviewer view](REVIEWER.md); callers can also supply those views directly to
the pure comparator. Retain the full configured namespace/provider/
endpoint/account/repository, consumer and reviewer. The query `cursor` and
action view `event_cursor` must agree; otherwise the example reports unavailable
and the caller can perform another bounded coherent read. A current SSE sample
already supplies those views atomically, but its bounded first pages may omit
the selected PR.
Retain the action request's actual offset in `after`. A terminal continuation
page with `more: false` proves no later actions, not absence of earlier ones;
an absent action is unavailable unless complete coverage starting at zero is
established.

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
Author and action-actor logins compare case-insensitively, consistently with
the reviewer projection. A different actor remains a difference; service,
account, consumer and requested-reviewer scope bindings remain exact.

Missing members/facts, incomplete inventory, mixed cache generations, unknown
reference history, expired/buffered evidence and mismatched candidates never
become a successful action comparison. A field difference records two observed
values; separate reads can race, so it is not itself a service-defect verdict.
Buffered actions must satisfy both their original expiry and the comparison's
current freshness budget; refreshed pull facts cannot renew an older action.
Keep acquisition times and original cursors with the external evidence owner.
Do not silently rebaseline or repeat an uncertain effect to make reports agree.

The example independently checks supported initial-review and author-owned
conflict recommendations from current native facts. It deliberately does not
re-run the cache's attention projection as its own oracle. A single current
snapshot cannot establish that an older review was observed before a later
candidate transition; causal follow-up ordering therefore remains unavailable
without independent historical evidence. Current-head agreement is not proof
of a reviewed base, handled feedback or publication authority.

The [pure source controls](tests/test_feed_evaluation_example.py) exercise actual
cache projection/query pages with separately constructed native envelopes:
initial/conflict agreement, changed/unknown candidates, terminal differences,
partial review coverage, causal-order unavailability, stale or future evidence,
mixed cache views and full scope refusal. These are comparison mechanics,
not actual acquisition, provider agreement or an installed evaluator.
The [client controls](tests/test_evaluation_client.py) additionally exercise the
real Unix HTTP/CLI with the actual cache, no query-triggered provider or delivery
effects, bounded reference parsing, original deadlines and mixed/partial-view
refusals. They do not qualify an installed or live-provider comparison.

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
