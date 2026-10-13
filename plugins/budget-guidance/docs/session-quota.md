# Session-owned Copilot quota

The quota reader is the first acquisition slice of the
[budget-aware routing effort](../../../efforts/active/budget-aware-model-routing/README.md).
It reads the existing requesting SDK session, not a newly created client or the
machine's global account. It does not resume sessions, refresh provider state,
consume pending warnings, change authentication, or stop a client.

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

Raw legacy account reset reconciliation, attributable same-period rate history,
routing integration, and launch warnings remain subsequent effort slices.
Unsupported hosts must report unavailable rather
than starting a new global-default client to fabricate requesting-account
evidence.

See [budget-guidance](../README.md) for the independent static posture command.
