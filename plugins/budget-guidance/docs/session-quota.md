# Session-owned Copilot quota

The quota reader is the first acquisition slice of the
[budget-aware routing effort](../../../efforts/active/budget-aware-model-routing/README.md).
It reads the existing requesting SDK session, not a newly created client or the
machine's global account. It does not resume sessions, refresh provider state,
consume pending warnings, change authentication, or stop a client.

## Foreground extension tool

When the host offers `budget_guidance_session_quota`, invoke it on demand with
an empty object for the default `premium_interactions` category. Optional
arguments are `category`, `providerId`, `quotaId`, `timeoutSeconds` (greater
than zero, default 10, at most 30), and the paired `expectedLogin` and
`expectedHost`. Provider evidence is copied only for an explicitly selected
provider and optional quota ID. Unknown provider enums or malformed known
fields fail closed rather than becoming optimistic defaults.
Quantities must be finite JSON numbers within the `1e18` magnitude bound,
matching the existing Python capture reader. Numeric strings are rejected,
not rounded or silently converted into successful captures.

The payload entry `extensions/budget-guidance/extension.mjs` uses the standard
plugin `extensions/<name>/extension.mjs` discovery convention; it requires no
new manifest extension key, dependency installation, client, or process.
It calls `joinSession` once, registers one tool, and performs no startup
account reads. The actual invocation ID must match the joined session before
and after the public `session.rpc.quota.get()` call. Optional account matching
uses `session.rpc.gitHubAuth.getStatus()` around that read and reports the same
observed-not-atomic scope as the Python helper below.

The tool returns structured text JSON with `status: captured`, an allowlisted
`capture` using the existing version-1 schema below, and explicit freshness and
raw-reset limits. `captured` describes transport acquisition, not a fresh or
usable budget. Runtime version is honestly `unknown`: no documented version
API is assumed. To inspect offline, save only the `capture` object through an
explicit caller-owned workflow and use the existing CLI. The tool writes no
file, and the CLI never upgrades it to authenticated live evidence.

Failure returns `resultType: failure` with `status: unavailable` or `error`,
a fixed diagnostic, and `capture: null`; no quota counters, raw auth responses,
opaque SDK errors, upgrade links, or arbitrary provider metadata are returned.
The entire quota input and entire rendered result are each bounded to 64 KiB
UTF-8, including nested discarded metadata. Serialization counts escaped
UTF-8 bytes incrementally and stops before materializing an oversize JSON
string; nesting beyond 64 levels or cyclic/non-JSON objects fails explicitly.
The success response is serialized once. A host without the public quota or
requested auth-status API is unsupported. Never use a newer SDK declaration as
proof that the current host implements it, or use a private connection or
credential-bearing auth union as a fallback.

One deadline covers the whole acquisition. Only one read can be in flight.
After timeout, an uncancellable public SDK call retains that slot until it
settles; late results are ignored and cannot start the next RPC in the chain.
The adapter never aborts or disconnects the foreground conversation. A reload
or host lifecycle change remains the host/operator's decision, not an automatic
recovery action. Implementation and fixture qualification do not authorize live
enablement, runtime update, or rollout.

The isolated contract test `tests/test_host_quota.py` requires Node.js with
`node:test` support alongside the ordinary Python test dependencies. The Node
suite copies the real entry into temporary storage and provisions its own fake
SDK before importing it, so direct `node --test` discovery never imports a
real SDK or depends on the Python wrapper having run first. No live extension
reload or account network request occurs. The default Python test-isolation
image does not currently include Node.js: run this focused test
through the bounded local plugin runner on a Node-equipped host, and run the
remaining suite through the devcontainer runner with `-k 'not host_quota'`.
The Node test remains required on the normal Node-equipped CI host; its absence
is a visible missing-tool failure, not a silently skipped qualification.

## Incumbent SDK integration

The experimental Copilot SDK surface is `session.rpc.quota.get()` in Python
(`session.quota.get` on the wire). The official
[generated Python RPC contract](https://github.com/github/copilot-sdk/blob/main/python/copilot/generated/rpc.py)
documents this as an incumbent session-owned projection **without a network
request**. The result's `to_dict()` uses the wire field names.

Use the SDK session already held by the host:

```python
from budget_guidance.quota import acquire_session_quota

reading = await acquire_session_quota(
    session,
    expected_session_id=requesting_session_id,
    runtime_version=host_runtime_version,
    timeout=10,
)
```

The supplied ID must match `session.session_id` before any RPC is attempted.
The helper calls only quota `get`, under an independent deadline of at most
30 seconds. A missing RPC surface, mismatched session, timeout, and transport
error are explicit diagnostics with no numeric fallback. Unexpected SDK errors
propagate to the host rather than becoming success-shaped quota readings.
This contract does not authenticate an arbitrary host-supplied Python object;
the host owns that trust boundary.

### Credential-free account matching

For a requesting GitHub account known to the host, use the opt-in account-aware
helper instead:

```python
from budget_guidance.session_identity import acquire_verified_session_quota

reading = await acquire_verified_session_quota(
    session,
    expected_session_id=requesting_session_id,
    expected_login="example-user",
    expected_host="https://github.com",
    runtime_version=host_runtime_version,
    timeout=10,
)
```

This calls the public `session.rpc.git_hub_auth.get_status()` before and after
the existing quota `get`. All three calls share one deadline of at most
30 seconds. Exact session ID, case-insensitive login, and normalized HTTPS
origin must match. A missing, unauthenticated, mismatched, unsupported, changed,
timed-out, or failed status cannot return even cached balances. Replacing the
session ID or RPC object during acquisition also withholds the result.
Login validation supports the underscore in Enterprise Managed User and setup
admin names while retaining GitHub's 39-character bound. The expected HTTPS
origin is supplied explicitly, including when using an enterprise host.

Only matching login/origin and a bounded status are added under
`account_identity`. Authentication type, plan, status message, tokens, raw
authentication objects, and other account metadata are not rendered. The host
still owns the existing SDK object's trust boundary; capture files cannot invoke
this helper or promote themselves to account-matched RPC evidence.

`account_identity.status: matched` means two observed status reads match.
`scope: session-github-auth` and `quota_binding: not-atomic` explicitly limit
that claim: this is not an atomic account/quota transaction, does not detect an
account switching away and back between observations, and does not establish
the billing identity of an arbitrary third-party provider. The runtime must
provide a stronger account-bound quota contract before consumers claim those
properties. Status reads do not refresh provider state or make compatibility
snapshots fresh; the existing provider freshness rules remain authoritative.

Older hosts without this public status method return unsupported rather than
creating a new client or inspecting the SDK's internal credential-bearing
authentication union. The original `acquire_session_quota` remains the narrower
session-only primitive and does not independently establish a GitHub account.
Unexpected quota-side SDK exceptions retain that primitive's propagation
behavior; they are not mislabeled as an unsupported authentication-status API.

## Bounded capture inspection

For offline inspection, keep only the projection and minimal attribution:

```json
{
  "schema": "copilot-extensions.copilot-quota-capture",
  "version": 1,
  "session_id": "session-example",
  "runtime_version": "runtime-example",
  "acquired_at": "2030-01-10T00:00:00Z",
  "projection": {
    "isTbbUser": true,
    "snapshots": {
      "premium_interactions": {
        "isUnlimitedEntitlement": false,
        "entitlementRequests": 1000,
        "usedRequests": 250,
        "tokenBasedBilling": true,
        "resetDateEpochMs": 1896134400000
      }
    }
  }
}
```

```text
<budget-guidance catalog argv[0]> quota --capture capture.json --session-id session-example --json
```

The capture is bounded to 64 KiB, including unknown metadata, and duplicate JSON
keys are rejected. Output is allowlisted: it excludes tokens, upgrade links,
provider account metadata, and opaque provider-quota state. The CLI always
labels attribution `caller-declared-capture`; a capture cannot claim live
session RPC authority. Do not store raw authentication responses or commit real
account data.

Output schema is `copilot-extensions.session-quota-reading`, version 1.
Numeric values are bounded, non-negative decimal strings. Token-based billing
and premium-request quantities remain distinct; conflicting or missing unit
flags cannot yield an AI-credit budget. Unlimited entitlements do not yield
finite allowance or arithmetic, including when the provider uses negative
sentinels. Each call reads the supplied entitlement anew, without persisting
one month's allocation as a recurring limit.

The optional provider `hasQuota` flag is validated as a boolean. An explicit
false produces unavailable with counters withheld, even if cached counters
suggest a positive balance or the unlimited-entitlement flag is true. Absence
of that optional flag remains unknown, not an invented true value. A true flag
does not establish provider observation freshness.

Additional usage (`overage`) is reported separately, not silently added to
`usedRequests`: the reader does not guess whether a provider includes it in the
consumed quantity.

`resetDateEpochMs` is interpreted as milliseconds, with zero/past, fractional,
out-of-range, and estimated resets diagnosed or rejected. A reset value is not
proof that provider observations are fresh.

## Deliberate limits

The compatibility snapshots do not supply provider-observation time. Output therefore
reports `availability: cached`, `freshness: unknown`, and
`provider_observed_at: null`, separately from `acquired_at`. It may show cached
balances and a future non-estimated reset, but **does not** feed them into a
confident forecast or concurrency recommendation. `calculated` and advice
`concurrency` remain null. Advice never blocks an operator launch or changes a
worker.

The opt-in [provider evidence mode](provider-quota.md) evaluates provider-owned
observation time and raw budget reset metadata when that SDK surface is
available. It does not relabel compatibility snapshots as fresh.

Raw legacy account reset acquisition/reconciliation still requires a safe
credential-free host projection; the public status/quota methods do not expose
the raw account reset. Do not read internal authentication objects to obtain it.
Same-period cycle-average pacing, static launch cues, and optional routing advice
have their own implemented contracts, not proof of raw-reset acquisition.
Unsupported hosts must report unavailable rather
than starting a new global-default client to fabricate requesting-account
evidence.

See [budget-guidance](../README.md) for the independent static posture command.
