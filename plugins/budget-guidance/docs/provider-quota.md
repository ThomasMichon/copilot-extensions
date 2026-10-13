# Provider-owned quota evidence

This optional mode extends the [session quota reader](session-quota.md). The
default compatibility-snapshot behavior remains unchanged. A host with an
existing SDK session can opt in to one explicit provider:

```python
reading = await acquire_session_quota(
    session,
    expected_session_id=requesting_session_id,
    runtime_version=host_runtime_version,
    provider_id="provider-example",
    quota_id="quota-example",
    max_age_seconds=300,
)
```

This still calls only the incumbent `session.rpc.quota.get()`; it never creates
or refreshes a client. The experimental
[ProviderQuotaState contract](https://github.com/github/copilot-sdk/blob/main/nodejs/src/generated/session-events.ts)
owns the evidence meanings. An older runtime without `providerQuotas` reports
the requested surface unavailable, not a fallback to optimistic legacy
counters. More than one matching quota requires an explicit quota ID. No
arbitrary first record or other provider can supply the requested scope.

## Independent dimensions

The reader keeps acquisition outcome, business access, capacity, observation
kind, quantity authority, units, and timing distinct:

- Failed/unavailable acquisition is not an observed service denial. No cached
  numeric success is substituted.
- Explicit denial, `hasQuota: false`, and exhaustion withhold balances even
  when cached counters are positive or entitlement is unlimited.
- Admission state is not a post-charge account read. Advisory quantities cannot
  become percentages, consumption, reset cadence, or forecast inputs.
- Only an `account_snapshot` with `authoritative_budget` quantities,
  recognized units, and `budgetMetadata` can provide measured consumption and
  entitlement. Consumption is never inferred from balance differences.
- Independently reported available/entitled quantities and compatible legacy
  counters must agree with the budget metadata before arithmetic is trusted.
  Conflicts withhold values; zero or negative quantities do not rewrite the
  service's access/capacity verdict.
- Requests, tokens, and AI credits remain distinct. Only AI credits enter the
  current pacing calculation. Unlimited entitlement never yields a finite
  budget.

Only selected provider/quota IDs and recognized enum states are rendered.
Labels, URLs, reasons, acquisition-error text, and unrelated account metadata
are not copied into output.

## Observation and reset evidence

`observedAt` is separate from this helper's `acquired_at` and `evaluated_at`.
A missing observation time stays unknown; a future time or an observation after
the capture's acquisition time is refused, and an
observation older than the configured bound is stale with balances withheld.
The default bound is 300 seconds, with a supported range of 0 through 86400.
It is a source-data freshness policy, not a worker throttle or sleep.

For an incumbent SDK read, qualifying provider observation within that bound
reports fresh. This means a recent account observation, **not** a ledger
settlement watermark or proof that the latest inference charge is included.

Unlimited entitlements pass these same observation checks before reporting
`unlimited`: missing evidence is unavailable, old evidence is stale, and
future or post-acquisition evidence is refused. The observed timestamp and
freshness remain visible without requiring finite counters or reset arithmetic.
Capture evidence remains unverified even when its timestamp passes the checks.

The provider's `budgetMetadata.resetAtEpochMs` is evaluated separately from
`snapshots[category].resetDateEpochMs`. Estimated, missing, zero, past, and
malformed reset evidence cannot supply a forecast horizon. A known nonzero
provider reset that has elapsed also withholds the previous period's balances,
even when an account observation itself is recent. A stale or invalid
compatibility reset is diagnosed rather than substituted for the provider's
fact. Two valid future reset times that disagree are contradictory; no date is
silently selected.

Fresh incumbent AI-credit readings with a valid provider reset reuse the
existing posture arithmetic for remaining allowance, overspend, days remaining,
and sustainable daily pace. Without attributable rate evidence, projected
consumption and concurrency remain unavailable; the warning band is `no-rate`
unless a separately established overspend already exists. This mode never
blocks an operator launch or mutates worker state.

## Capture inspection

```text
<budget-guidance catalog argv[0]> quota --capture capture.json --session-id session-example --provider-id provider-example --quota-id quota-example --at 2030-01-10T00:00:00Z --json
```

The existing 64 KiB capture bound and allowlist apply. Even if a file claims
successful provider acquisition, its timestamp is unverified and its
attribution remains caller-declared. It cannot grant itself incumbent RPC
authority or enable derived posture calculations. The caller can inspect
reported facts, but the helper does not authenticate arbitrary capture files.

Raw legacy account-authentication resets, independently validated same-period
rate history, launch guidance, and routing integration remain later slices of
the [active effort](../../../efforts/active/budget-aware-model-routing/README.md).

See [budget-guidance](../README.md) for static/manual posture support.
