# Gateway-hosted Vault availability selection

`fleet_manager.gateway_vault_available` is an opt-in, in-process source-selection
core, not an installed service or an enabled Gateway route. It closes the bounded
selection part of the [Gateway vision](../../visions/machine-fleet/gateway/README.md)
and [Vault's explicit noninteractive/attended distinction](../../visions/vault/README.md#discovery-never-starts-a-ceremony).
Vault/providers retain credential lifecycle, custody and durable authentication
transaction authority. This core adds no token cache, issuer, subscription,
transaction store, notification transport, process or listener.

## Owner and provider contracts

An embedding owner constructs a `TokenKey` from the original authenticated
request and its independently authorized mapping. Provider, account, tenant,
audience, resource, sorted unique scopes, consumer and access binding all
participate in affinity. The binding is an owner-defined identity of the complete
authorized consumer/source mapping, not a transferable capability. All fields
are required; unknown access/expiry metadata must not be fabricated from a source
authorization lease or a broad account credential.

`AvailableTokenCoordinator(sources)` calls `sources(key)` at admission, before
provider dispatch and before accepting each result. That callback must revalidate
the original caller and each exact source binding and return at most 32 currently
connected, policy-admitted `AvailableSource` objects. Permission failures
propagate; a withdrawn/disconnected source cannot win. A `SourceIdentity` binds
Controller, connection incarnation and the selected source. A changed provider
object also invalidates an outstanding result. An advertisement alone is not
an admitted source. This synchronous policy callback must be cheap and avoid
blocking I/O. The phase deadline is rechecked after policy validation and
immediately before provider dispatch; delayed work never starts a refresh after
its budget has expired.

Each explicitly code-registered provider implements
`fleet-manager.vault-available/v1`: `get_available(key, deadline=...)` returns
`SourceAvailability`. The deadline is absolute on the running event loop's
monotonic clock. The operation permits only cached reads or refresh with a
provider guarantee that no user interaction can start. `AvailableToken`
distinguishes `cache` from `silent-refresh`, carries the exact key and actual
token expiry, and omits the credential from its representation. The provider
still owns validating token bytes, access restrictions, expiry and custody;
metadata equality is not cryptographic token verification.

A contract declaration is compatibility information, not provider authorization.
The owner must not wrap an opaque legacy `resolve`, CLI login or browser flow
and label it noninteractive. Missing support produces `unsupported` without
calling the provider. Existing fixed account-source, sealed credential delivery
and service-channel contracts are unchanged; none is silently promoted to this
new port.

## Selection, results and lifetime

Sources start in most-recent same-key success order, concurrently within one
shared phase of at most 30 seconds. A slow preferred source does not consume
each alternative's budget in sequence. Simultaneously ready valid responses use
that order; otherwise the first valid currently authorized response wins.
Expiry and current source/caller policy are checked before return. Affinity is
bounded metadata only (256 keys by default), never token bytes or authority.
Reconnection may retain Controller/source preference but must supply its new
admitted incarnation.

`lookup` returns `available`, `no-source`, `cache-exhausted`, `unavailable` or
`uncertain`, with typed per-source observations. Only an all-cache-miss result is
`cache-exhausted`; failure, unsupported providers and stalls do not masquerade
as misses or proof of MFA. A caller may shorten, never extend, the phase budget.
No outcome starts interactive authentication or sends a device notice.

Losing or timed-out reads receive cancellation. Cancellation is not settlement:
an uncooperative adapter remains owned and counted against the global probe
limit (128 by default), and the same key/source cannot be dispatched again until
it settles. Its late result cannot fulfil the returned lookup or update affinity.
The coordinator yields cancellation without awaiting an unbounded adapter.
The owner calls `stop()` to close admission/cancel reads and `drain(timeout=...)`
to observe cleanup. `False` retains unresolved responsibility; neither expiry
nor object disposal proves cleanup. Use one coordinator per event loop.

## Integration and compatibility boundary

This source seam is not yet a multi-Controller service-offer registry, a
remote-dispatch API or a consumer route. Actual service-channel/source composition
must preserve original caller authorization, exact custodian grants and
installation/incarnation bindings. Providers expand support first; consumers
select it only from an admitted running peer's declared contract. Unsupported
older peers stay unavailable for this optional lane, with no attempt to replay
an opaque operation. Old fixed-route consumers keep their existing API during
upgrade or rollback.

Auth-needed notices, explicit device-local action, fresh provider nonce/timeouts,
durable demand recovery and first authorized interactive fulfilment remain
separate implementation and qualification. That work must compose Vault's
transaction authority rather than add a second one. No production credential
acquisition, Controller enrollment, runtime update or deployment is activated.

Focused source controls:

```text
PYTEST_ADDOPTS="-o pythonpath=src -o python_files=test_gateway_vault_available.py" python tools/run-standalone-tests.py fleet-manager
```

The contained runner exercises real asyncio scheduling/cancellation with synthetic
providers. It does not qualify channel transport, native provider interaction,
fresh installation or live-device adoption.
For installed-package evidence, rebuild the managed test package with the same
runner's `--prepare`, omit `pythonpath=src`, and repeat the focused selection.
