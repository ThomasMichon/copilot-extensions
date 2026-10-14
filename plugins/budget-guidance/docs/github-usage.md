# Personal GitHub AI-credit usage reports

`github_usage.acquire_github_usage` is an opt-in provider API adapter over the
caller's **existing authenticated GitHub client**. It acquires a personal
billing report, not the requesting Copilot session's quota. It creates no
client, authenticates no account, stores no credential, and never refreshes,
retries, or changes global authentication. No hook or inert configuration can
authorize it.

## Host-owned invocation

```python
from budget_guidance.github_usage import acquire_github_usage

report = await acquire_github_usage(
    authenticated_transport,
    expected_login="example-user",
    year=2030,
    month=1,
    timeout=10,
)
```

The transport implements asynchronous
`get(path: str, *, max_bytes: int) -> GitHubResponse`. Its immutable response
holds `status_code: int` and `body: bytes`. The host owns the authenticated
HTTPS origin `https://api.github.com`, supported API version/headers, streaming
the body within the supplied byte bound, and cooperative cancellation. It must
not forward credentials across redirects or resolve arbitrary origins. Like
the [incumbent SDK reader](session-quota.md), this interface does not
authenticate a host-supplied Python object.

The adapter first reads `/user` and requires an exact case-insensitive match
with the explicit login. It then reads only
`/users/{login}/settings/billing/ai_credit/usage?year={year}&month={month}`.
The returned account and explicit monthly period must match, and unexpected
product/model/day filters are refused. A mismatch on `/user` prevents the
report request entirely. Both reads share one deadline, at most 30 seconds;
each response is bounded to 64 KiB and the report to 256 line items.
The explicit year must have four digits (1000 through 9999), and the month
must be 1 through 12; malformed invocations fail before either request.
Cancellation owns this acquisition only; it does not close or stop the host's
shared client. Unexpected host programming errors propagate.

## Facts, not an invented entitlement

The version `1` `copilot-extensions.github-ai-credit-usage` result preserves
source, requested login, account-verification status, acquisition time, period,
availability, and diagnostic codes. It allowlists AI-credit `grossQuantity`,
`discountQuantity`, and `netQuantity` totals as exact decimal strings.
Currency amounts and prices are **not** converted into usage or combined with
those quantities. Unknown units, invalid quantities, wrong accounts/periods,
and partially invalid reports withhold all totals.
Each quantity and aggregate is bounded to `1e18`, with at most 18 fractional
decimal places; unsupported precision is rejected rather than rounded to zero
or expanded into unbounded decimal output.

A successful report is `available` as a **report**, not as a current budget.
The API supplies neither included allowance, authoritative reset, nor provider
observation time. `freshness` therefore remains `unknown`,
`provider_observed_at` is `null`, and the result explicitly does not claim
independently verified requesting-session account identity. Its inert
`budget_reading` remains unavailable with all numeric budget values withheld;
401 and inaccessible/unsupported 404 responses likewise remain unavailable.
GitHub can use 403 for permission denial or rate limiting. Without an independent
rate-limit signal, this adapter conservatively reports
`github-forbidden-or-rate-limited` as an error, not a confirmed access denial.
Transport, timeout, throttling, malformed, and other HTTP failures remain errors.
It can be inspected or passed through
an [explicit external reader](external-readers.md), but cannot manufacture a
forecast or an actionable routing posture.

HTTP 401/403, inaccessible or unsupported endpoints, throttling, transport
failures, invalid JSON, oversize responses, and timeout have explicit states
without copying opaque provider response bodies. An empty report is unavailable,
never zero consumption. Explicit zero quantities in a nonempty valid report
remain report facts, not evidence of full remaining entitlement.

## Billing scope and sources

GitHub's [billing usage reference](https://docs.github.com/en/rest/billing/usage#get-billing-ai-credit-usage-report-for-a-user)
states that user endpoints report only usage billed directly to a personal
account. Organization/enterprise-managed licenses are excluded; an empty
personal report cannot prove no usage under such a license. Organization and
enterprise scope require separately authorized adapters and are not silently
queried by this helper.

The [official OpenAPI schema and examples](https://github.com/github/rest-api-description/blob/main/descriptions/api.github.com/api.github.com.json)
define the report fields and the personal endpoint's `ai-credits` unit.
The [usage-report tutorial](https://docs.github.com/en/billing/tutorials/automate-usage-reporting)
distinguishes usage quantities from billed currency. Provider authentication
prerequisites do not authorize the helper to obtain a new token or change the
host's credential policy; unavailable access stays unavailable.

See [budget-guidance](../README.md) for independent posture/status and
[provider quota evidence](provider-quota.md) for incumbent-session observations.
