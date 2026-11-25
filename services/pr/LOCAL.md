# Local repository cache and continuing events

The explicit `repository-local/v1` profile uses the **same foreground
WatchService**, writer lease, ReplayStore, observer and drain as the connector
profile. It needs no running Core/Gateway for local ingress, no service proof key
or access-token enrollment, and creates no Dispatch emitter. Linux is the
supported local transport: HTTP over a private Unix socket, with kernel
`SO_PEERCRED` UID mapped to a configured principal/target/consumer. Headers and
bodies cannot supply caller identity. The socket and default client use the
serving user's OS identity; this is not a sandbox against equally privileged
processes under that same user.

The separately selected native executable and read-credential custodian remain
external owners. A chosen credential command may use an existing credential
worker/Gateway, but the local query/subscription transport does not require that
choice. The service accepts the existing typed observation-credential command
contract; it does not install, issue, enroll or repair credentials. A credential
outage leaves last-confirmed cache data with explicit coverage/freshness
information, not permission to select another account.

## Explicit configuration

Use an absolute JSON reference. For example, one configured GitHub repository:

```json
{
  "schema": "agent-pull-requests.service",
  "version": 2,
  "profile": "repository-local/v1",
  "root": "/data/pr-cache",
  "namespace": "installation-a",
  "interval": 5,
  "operation_seconds": 60,
  "drain_timeout": 60,
  "repository_cache": {
    "protocol": "agent-pull-requests.repository-cache/v1",
    "socket": "/data/pr-cache/local.sock",
    "peers": [{"uid": 1000, "principal": "reader-a"}],
    "repositories": [{
      "target": "example-github",
      "identity": {
        "namespace": "installation-a",
        "provider": "github",
        "endpoint": "https://github.com",
        "account": "account-a",
        "repo": "owner/repository"
      },
      "prefix": ["/usr/local/bin/agent-worktrees"],
      "source_config": "/run/config/github.yaml",
      "credential_command": [
        "/usr/local/bin/fleet-manager", "credential",
        "--config", "/run/config/credential-worker.json",
        "--source", "forge-reader", "--account", "account-a",
        "--forge", "{\"provider\":\"github\",\"api_base\":\"https://api.github.com\",\"repository\":\"owner/repository\",\"permission\":\"observe\",\"authentication\":\"bearer\"}"
      ]
    }],
    "bindings": [{
      "principal": "reader-a",
      "target": "example-github",
      "consumer": "local-reader"
    }]
  }
}
```

Paths and UID are examples, not enrollment or launch permission. Select the
actual installed native prefix and an already-authorized credential command.
An attributed interpreter prefix is also supported. The native source file
contains an explicit binding, without ambient registry/project intake:

```yaml
repos:
  selected:
    pr:
      provider: github
      api_base: https://github.com
      repository: owner/repository
```

For Gitea, add a distinct target/identity and admitted binding, using
`provider: gitea`, the selected HTTPS base (for example
`https://forge.example`), its explicit native source file and its existing
bearer observation credential scope. Use separate source files when the same
repository slug exists on different backends. Namespace/provider/endpoint/
account/repository all participate in the target key. Gitea's scope is now
accepted through the existing typed credential worker/controller/CLI; the
external custodian still verifies real permission/expiry/revocation. No token
value belongs in this configuration.

Unknown/duplicate fields, ambiguous peers/targets, unconfigured resources and
unsupported selected core/SDK/native contracts refuse. Provision/config parsing
does not acquire credentials or probe providers.
The optional top-level `native_read_concurrency` selects an integer from 1 to 4
for simultaneous native snapshot reads and pending-read admission in the existing
observer. Omission retains four and the legacy core call shape. Booleans, null,
nonintegers and out-of-range values refuse. Explicit selection qualifies the
actual executing WatchService and ReplayObserver keyword support before state,
service or provider effects; an older core refuses the selection rather than
silently reverting to four. This is a local Python API contract, independent of
package versions, with no retirement of the omitted/default form.

Lower concurrency reduces simultaneous subprocess demand; it does not increase
per-read capture, request or deadline budgets, change inventory/member
reconciliation fairness, or create a second executor, poller or cache. It also
does not guarantee that an entire population completes within its unchanged
cycle deadline. The same optional selection is supported by the filtered local
profile. The connector host JSON remains unchanged; Python embeddings may select
it explicitly through WatchService or ReplayObserver.
The socket cannot select the foreground readiness file, writer lease,
replay document/lock, or native-subscription filename; those existing owners
must not replace or misidentify the ingress endpoint.

The optional [native GitHub batch profile](../../plugins/agent-pull-requests/docs/snapshot-batches.md)
adds explicit `github_snapshot_batches: true` owner metadata and a per-repository
`snapshot_batch` protocol selection. Omission retains scalar acquisition.
Its additive adoption, durable primary/shared-secondary backoff, interrupted-read
refusal and compatible scalar rollback keep the same writer and cache owners.

## Provision and foreground lifetime

```text
agent-pull-requests-service provision --config /run/config/pr-cache.json
agent-pull-requests-service serve --config /run/config/pr-cache.json
agent-pull-requests-service ready --status /data/pr-cache/service-status.json
```

Provision is explicit **fresh dedicated state only**, with a private serving-user
root. The continuing layout is `repository_cache_version:2`, adding immutable
subscription intent and delivered/ACK identity to the current-state/event
extension. Prior/current readers cannot silently adopt each other's layout.
There is no implicit production migration, deletion, reset or recreation.
Serve adopts valid selected state and qualifies the executing native contract
before opening ingress. Adoption rechecks the private serving-user root and
refuses widened permissions without repairing them or probing credentials.
The existing connector configuration remains version 1,
with unchanged watch/report/workspace defaults.

The optional [reviewer-action query](REVIEWER.md) is selected separately with
`reviewer_actions: true`. It adds an explicit compatible-envelope adoption under
the same writer; it does not relax fresh provisioning or enable protected
workspace readers.

SIGINT/SIGTERM or the embedding stop event withdraws admission and joins native,
request and disk work before writer release. Slow cleanup keeps the **same**
foreground owner and exact cleanup handles until settled; a cancelled wait is
not proof that work stopped. A stale owned socket is adopted only after the
existing writer lease excludes another owner and an exact socket check confirms
it is not serving. No other service or process is killed.

## Supported client commands

Every command below connects only to the selected Unix HTTP socket. It does not
start the service, load provider credentials, read the state file or fetch a
forge. Namespace and target are explicit expected bindings.
The separately documented [comparison command](EVALUATION.md) uses these same
reads and a caller-supplied native reference; it does not subscribe or fetch a
provider.

```text
agent-pull-requests-service list --socket /data/pr-cache/local.sock --namespace installation-a
agent-pull-requests-service query --socket /data/pr-cache/local.sock --namespace installation-a --target example-github --limit 16
agent-pull-requests-service query --socket /data/pr-cache/local.sock --namespace installation-a --target example-github --number 7
agent-pull-requests-service subscribe --socket /data/pr-cache/local.sock --namespace installation-a --target example-github --request-id consumer-a --ttl 3600
agent-pull-requests-service inspect --socket /data/pr-cache/local.sock --namespace installation-a --target example-github --registration-id REGISTRATION_ID
agent-pull-requests-service events --socket /data/pr-cache/local.sock --namespace installation-a --target example-github --registration-id REGISTRATION_ID
agent-pull-requests-service acknowledge --socket /data/pr-cache/local.sock --namespace installation-a --target example-github --registration-id REGISTRATION_ID --event-id EVENT_ID
agent-pull-requests-service cancel --socket /data/pr-cache/local.sock --namespace installation-a --target example-github --registration-id REGISTRATION_ID
agent-pull-requests-service follow --socket /data/pr-cache/local.sock --namespace installation-a --target example-github --registration-id REGISTRATION_ID --timeout 60 --interval 1 --ack
```

Output is JSON. `subscribe` returns a current baseline page and the durable
registration. Repeating the **same request ID and intent** reconciles the
original registration/expiry and never resets an advanced ACK. Changed intent
or target refuses. Save returned IDs and reuse them after disconnect/restart;
never create a new logical subscription merely because a response was lost.

Without `--after`, a new subscription atomically starts after its returned
baseline cursor. `--after CURSOR` explicitly resumes retained history. Continue
baseline pagination with query `--after LAST_PR_NUMBER`; later events remain
protected by the active registration. `events --after SEQUENCE` can page within
an already-admitted delivery position; ACK is cumulative through the selected
delivered event. Independent registrations retain independent positions.

`follow` is a finite repeated cache event read, not a series of terminal waits
or forge pollers. `--ack` explicitly chooses ACK after successful output; without
it, events remain unacknowledged for original-ID replay. Output is not proof of
downstream task handling. Cancellation/expiry prevents new delivery while
already-delivered unsettled IDs remain reconcilable and protected until ACK.

## HTTP contract and limits

`GET /v1/repositories` lists only the admitted principal's configured targets.
`GET /v1/repositories/TARGET/capabilities` advertises the **running** protocol,
namespace, complete repository key, storage support and budgets. Other
operations are `POST /v1/repositories/TARGET/{query,subscribe,inspect,inspect-request,events,acknowledge,cancel}`
with JSON:

```json
{
  "protocol": "agent-pull-requests.repository-cache/v1",
  "namespace": "installation-a",
  "repository_key": "KEY_FROM_CAPABILITIES",
  "request": {"registration_id": "REGISTRATION_ID"}
}
```

The operation's request fields match the CLI. Scope comes from OS admission,
not the expected-binding fields. Missing/prior support refuses; clients never
fall back to one-shot watch, a state-file read or an ambient account.
The separately selected [reviewer-action capability](REVIEWER.md) adds
`next-actions` and finite local SSE subscriptions under this same envelope.

### Original-request recovery

The running capability response advertises this optional provider expansion:

```json
{
  "request_recovery": {
    "protocol": "agent-pull-requests.repository-request-recovery/v1",
    "inspection": "inspect-request",
    "cutoff": "not_after",
    "clock": "unix-seconds",
    "expiry": "min-ttl-cutoff"
  }
}
```

Select this support before sending the new HTTP forms. The existing generic
programmatic client's `request` method can carry them; the CLI's existing
commands are unchanged. Missing support must not trigger a new-form attempt
followed by a legacy retry.

`inspect-request` takes `{"request_id": "ORIGINAL_REQUEST_ID"}`. It reads only
the admitted repository and consumer's local receipt, returning `request_id`,
provider Unix-seconds `now`, `subscription` and `status`. A retained receipt has
its original registration, request and current `active`, `expired` or
`cancelled` status. Absence returns `subscription: null` and `status:
"not_found"`. Inspection neither creates nor renews a registration, advances a
cursor, trims history or fetches a provider.

`subscribe` additionally accepts optional integer Unix-seconds `not_after`.
Persist it with the original request, alongside unchanged `request_id`, TTL
and `after`. A new registration must be admitted before that cutoff, including
after a writer-lock wait and at the commit checkpoint; its expiry is the
earlier of the TTL-based expiry and the cutoff. Reconciliation of a retained
request still returns the original registration after expiry. Changed intent
refuses. Legacy requests without `not_after` keep their old shape and behavior.

Recover a lost response by `inspect-request`, not by repeating `subscribe`.
Expired, fully acknowledged receipts can be collected after their existing
retention window. The immutable cutoff prevents a late original bounded
request from recreating a collected registration. It is not permission to
reuse collected request IDs with later cutoffs or changed intent.

For cancellation with an unknown registration, retain the original request
obligation. Cancel a found registration through the existing `cancel`
operation; inspect again after an uncertain result. Absence before the cutoff
does not exclude delayed acceptance. A provider observation at or beyond the
original cutoff excludes further acceptance of that same request, and any
accepted registration has expired by then. Already-delivered unacknowledged
events remain retained. Clock rollback, unavailable inspection or a changed
binding is not settlement. A `repository_request_expired` HTTP 410 recommends
`inspect-request`; it must not be interpreted as proof that an earlier or
just-committed attempt had no effect.

The strict continuing-state reader accepts legacy requests and bounded
requests together. A prior strict reader cannot interpret the added intent
and refuses without rewriting it. This provider expansion does not perform
reader lifecycle adoption, install a new runtime, promise transparent rollback,
or enable a Dispatch consumer.

### Explicit expected-consumer client

The optional programmatic `BoundRepositoryClient` reuses that Unix transport
while selecting the complete expected repository and OS-admitted consumer from
trusted configuration, not the first capability response:

```python
from pathlib import Path
from agent_pull_requests.repository_contract import RepositoryIdentity
from agent_pull_requests_service.bound_local_client import BoundRepositoryClient

client = BoundRepositoryClient(
    Path("/data/pr-cache/local.sock"), "example-github",
    identity=RepositoryIdentity(
        "installation-a", "github", "https://github.com", "account-a", "owner/repository",
    ),
    consumer="local-reader",
)
support = client.negotiate()
```

Negotiation reads the executing server's existing capabilities route. It
requires the exact repository identity/key, namespace, target and
`consumer_binding` with protocol
`agent-pull-requests.repository-consumer/v1` and the configured consumer.
Missing or incompatible support refuses; it does not select an older wire form,
register a subscription, prepare a task or start anything. Callers still check
the selected reviewer/stream support before relying on those optional features.
The first request also negotiates automatically if no selection exists.

The client sends `expected_consumer` beside the ordinary envelope fields on
every operation, including SSE. The server compares it with the consumer
already derived from the transport principal and target, before registration,
delivery or ACK effects. It is an expectation, never caller identity or
authority. A changed OS binding returns `repository_consumer_mismatch`;
original request/registration identities remain with their existing owner.
Changing this client object's selected transport or repository metadata refuses
instead of retargeting a retained request.

Ordinary clients still send their original four-field envelopes and retain
their existing semantics. The optional assertion is advertised/enforced only
for this repository profile, not silently added to filtered or watch profiles.
No CLI selector, Dispatch dependency, consumer factory or operational enrollment
is introduced. Linux remains the supported private local transport.

### Asynchronous bound requests

An embedding with an existing event loop can explicitly construct
`async_local_client.AsyncBoundRepositoryClient` with the same path, target,
identity and consumer arguments. Its `await client.negotiate()` and
`await client.request(operation, payload)` use native asyncio Unix streams,
not blocking calls or executor threads. They reuse the same expected-selection
validator and expected-consumer envelope. The synchronous client is unchanged.

Each call permits one in-flight operation and bounds negotiation plus the
requested exchange by `operation_seconds` (positive and at most 30 seconds).
An optional absolute monotonic `client.deadline` further shortens that budget;
an elapsed deadline refuses before IPC. Request intent is detached before the
first await, so changing a caller's dictionary cannot replace an emitted request.
The current operations use shallow scalar request fields. Before copying, the
client admits at most eight fields with bounded keys, strings and numeric
values; nested collections and oversized inputs refuse before encoding or IPC.
Headers, request bodies and response bodies retain the local byte budgets.
The client rejects ambiguous framing rather than adding another HTTP profile.

For `inspect-request` and `subscribe` with `not_after`, the asynchronous client
reads fresh executing capabilities inside each call's original budget before
sending the new form, even when its repository binding was already selected.
Missing or incompatible original-request recovery support refuses before POST;
it does not disable legacy operations or retry through an older form. This
also covers a provider rollback after an earlier successful negotiation.
Embeddings must account for that capability exchange in their aggregate call
budget. A lost bounded-subscribe result can be recovered with
`await client.request("inspect-request", {"request_id": original_id})`,
then cancelled by its returned registration ID. Retain the immutable cutoff
and uncertain-effect obligations described above; lookup is not renewal.

Cancellation closes and joins the owned connection, with up to one additional
second for close cleanup. It does not roll back an operation the service may
already have accepted. Preserve the original request/registration/deadline and
reconcile its result through the existing owner; there is no internal retry,
replacement request, ACK, task preparation or process fallback. This finite
request client does not implement SSE or enroll a Dispatch consumer.

Source limits are eight admitted requests/connections, 8 KiB headers, 32 KiB
request bodies, 1 MiB selected responses, query/event pages of 1..16 and
subscription TTL 1..86,400 seconds.
Byte paging includes each operation's full registration/subscription envelope,
so a near-limit baseline or event page is shortened rather than rejected after
the durable registration/delivery operation.
Follow intervals are 1..300 seconds and polling windows 1..3,600 seconds.
The original monotonic follow deadline bounds
capability discovery, event reads, ACK and every socket send/receive; a slow
or trickling peer cannot reset that window. No new ACK starts after expiry.
History gaps return 410 and require explicit baseline/new-subscription recovery,
not silent cursor advancement or successful ACK. Backpressure preserves
unsettled deliveries instead of evicting them.

State remains the existing namespace-owned `watch-replay.json` and operation
lock below `root`: at most 16 repositories, 400 current/terminal summaries per
repository, 128 logical registrations, 4,096 events and a 16 MiB cache extension.
Version 2 reserves 1 MiB within that extension for registration/delivery/ACK
progress, so a full facts/history payload cannot prevent the ACK that relieves
pressure. Only settled old history may evict; newly published and unresolved
delivery events remain protected.
Eviction measures cache sizes once and subtracts each settled event's encoded
bytes; it does not repeatedly serialize the full cache under the shared lock.
Settled expired registration metadata may retire after its documented
one-day reconciliation window; unresolved delivered IDs are not discarded.
Terminal/cache capacity backpressure is explicit, never a data-reset operation.

Local requests wait for the existing shared transaction lock only within their
original admission budget, without extending expiry or creating another writer.
Response flushing and connection cleanup are bounded too; withdrawal aborts only
owned socket transports while any started storage operation remains joined.
`follow` drains all retained delivered pages even after cancellation/expiry.
CLI diagnostics preserve permanent history-gap/binding reasons instead of
instructing users to blindly retry every failure as an outage.

Acquisition cadence uses `interval` (1..300 seconds, default 5); operation and
drain budgets use their existing 1..300-second selectors (default 60).
Inventory's emitted 4-page/200-member/60-second arguments, passive probe 15-second
timeout, four-reader bound, 32 configured-only reads, and native 64-request/
capture bounds are current fixed adapter limits, not fictional configuration
knobs. Queries/subscriber counts do not multiply acquisition. See
[the source contract](../../plugins/agent-pull-requests/docs/repository-cache.md)
for exact source ownership and partial/departure/freshness semantics.

These are supported source interfaces and synthetic qualification surfaces.
They do **not** authorize real traffic, retained hosting, enrollment, installation
or activation. Release, installed/running support, authorized target footprint
and actual live read-only events remain separate acceptance.

## Source qualification

The standalone manual test lane runs `test_local_foreground.py`,
`test_local_lifetime.py` and `test_local_recovery.py` with fixture-owned
GitHub/Gitea native processes and fresh explicitly provisioned state. It
exercises the packaged foreground and actual CLI, independent continuing
consumers across original-root restart, later-event follow, degraded readiness
with usable queries, and exact native/disk drain and replacement recovery.
The [M1 source map](../../efforts/active/durable-pr-watch-delegation/repository-cache-events-m1.md#source-evidence-map)
links complementary scope, schema, gap and pressure controls.
These fixtures neither select a retained deployment artifact nor supply real
provider/credential authority.
