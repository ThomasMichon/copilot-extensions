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
There is no production migration, deletion, reset or implicit recreation.
Serve adopts valid selected state and qualifies the executing native contract
before opening ingress. Adoption rechecks the private serving-user root and
refuses widened permissions without repairing them or probing credentials.
The existing connector configuration remains version 1,
with unchanged watch/report/workspace defaults.

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

```text
agent-pull-requests-service list --socket /data/pr-cache/local.sock --namespace installation-a
agent-pull-requests-service query --socket /data/pr-cache/local.sock --namespace installation-a --target example-github --limit 16
agent-pull-requests-service query --socket /data/pr-cache/local.sock --namespace installation-a --target example-github --number 7
agent-pull-requests-service subscribe --socket /data/pr-cache/local.sock --namespace installation-a --target example-github --request-id consumer-a --ttl 3600
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
operations are `POST /v1/repositories/TARGET/{query,subscribe,inspect,events,acknowledge,cancel}`
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

Source limits are eight admitted requests/connections, 8 KiB headers, 32 KiB
request bodies, 1 MiB selected responses, query/event pages of 1..16 and
subscription TTL 1..86,400 seconds. Follow intervals are 1..300 seconds and
polling windows 1..3,600 seconds. The original monotonic follow deadline bounds
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
