# Service-owned protected-cache maintenance

The selected running service exposes metadata-only maintenance of its existing
protected records. This is a provider-first boundary: the ordinary
`cache-populate`, `cache-verify` and `cache-status` CLI commands have not yet
migrated to it. It does not change ordinary `get --cache-only`, which still
never contacts the service.

## Running contract and compatibility

The existing full `ping` response adds `cache_maintenance`:

```json
{
  "contract": "agent-vault.cache-maintenance",
  "version": 1,
  "operations": ["populate", "verify"],
  "owner": "selected-service",
  "returns_credentials": false
}
```

This describes the executing resident, not a newer installed package or CLI.
Old callers ignore the additive property and retain their existing operations.
An older provider omits it; that means unknown maintenance support, not successful
enforcement. Consumers must select a supported running contract before sending
the new action. Provider rollback to a resident without this declaration needs
an explicit unsupported outcome, not a guessed invocation or effectful
probe-and-replay. No provider retirement window is established by this addition.

## Request and result

Send one entry/field reference over the existing selected, authorized transport:

```json
{
  "action": "cache-maintenance",
  "operation": "verify",
  "entry": "/Account",
  "field": "password",
  "kpdb": "/absolute/selected.kdbx",
  "group": "Personal",
  "max_age_seconds": 3600
}
```

`operation` is `populate` or `verify`. `field` defaults to `password`;
`max_age_seconds` is optional and, when supplied, must be a nonnegative integer.
Database, group and installation selection keep their existing transport and
cache-owner rules. Root anchors and standard field aliases use the existing
normalization owner. This action neither supplies repository policy nor loads
client configuration or code in the service.

Successful responses contain only `ok`, `contract`, `version`, `present` and
`owner`. Verification may successfully report `present: false`; it never
falls back to a backend read or unlock. Presence means a valid bound protected
record, not backend entry existence, service memory alone or an unattributed
legacy slot. Optional freshness, invalidation and pending rotation remain
authoritative.

Population requires an existing unlock and performs one authoritative read
through the current protected-cache owner. It never runs an unlock provider or
dialog, even if a caller sends `allow_prompt`. A successful backend read is not
enough: the value and generation must match a protected readback before
`present: true` is returned. Neither success nor failure returns a credential.
Missing values, locked state, unavailable protection and unverified writes have
fixed, credential-free failures. Unknown effects are not permission to replay.

## Custody and validation

The action uses existing request admission, foreground priority, credential
custody, cache revocation and connection settlement. It adds no worker, timer,
batch queue, endpoint, protection scope or client-side key/cache write. One
request admits one existing bounded credential operation; shutdown/cutover
retains that operation until settlement.

`tests/test_cache_maintenance.py` covers current and legacy requests over the
real newline-framed JSON transport, metadata-only responses, locked hits/misses,
selection/freshness mismatches, failed writeback, root/group namesakes, aliases,
invalid input and transition refusal. These are contained service tests, not
actor-free unit admission or installed/native-protection acceptance.

See also: [architecture](architecture.md), [unlock warming](cache-warming.md),
and [version-skew-safe contracts](../../../docs/patterns/service-contract-evolution.md).
