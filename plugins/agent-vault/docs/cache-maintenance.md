# Service-owned protected-cache maintenance

The selected running service exposes metadata-only maintenance of its existing
protected records. The CLI uses it when ordinary configuration explicitly selects
`cache_maintenance: "selected-service"`. Absent that preference, or with explicit
`local`, the existing maintenance behavior remains unchanged. Ordinary
`get --cache-only` still never contacts the service.

## CLI opt-in and selection

```json
{
  "cache_maintenance": "selected-service",
  "cache_entries": ["/Account", ["Service", "username"]]
}
```

The shared target owner snapshots references and access policy once per command.
The CLI retains one resolved selection and the actual transport that answers
full-ping qualification. Per-entry requests invoke that retained callable or
native endpoint directly, without rediscovery or re-entering extension fallback.
A lost reply, exception or later `None` result is unconfirmed, never permission
to try another route. Transport labels do not establish which callable was chosen.
Qualification-time extension failures log a fixed warning, without the provider's
name or exception text. Ordinary callers retain their existing logging.
The CLI never activates
a service, reads/prepares/migrates a local cache, copies keys or falls back to
local protection after an unsupported provider or failed request.

Qualify `service-cache-maintenance-v1` on the selected executing CLI and the
running descriptor below before enabling the preference; a package version or
old reader ignoring configuration is not enforcement. `cache-populate` requires
an existing unlock. In selected-service mode its `--prompt` and legacy `--verify`
flags refuse before service contact: the latter checks backend existence in local
mode, not protected cache membership. Use `cache-verify` for protected records.

`cache-verify` retains its existing `present`/`total`/`missing` JSON and exit 2
for missing records. Selected-service status reports owner, configured-target
and present counts, missing references and `state: "inspected"`. With no targets
it reports `state: "no-targets"`, not protected-cache readiness. These counts
refer to the selected service, not caller-side storage.

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
cache-owner rules. An omitted database selector retains native service selection;
an explicitly supplied selector must be a nonempty string, validated before
default selection or database lookup. Root anchors and standard field aliases use the existing
normalization owner. This action neither supplies repository policy nor loads
client configuration or code in the service.
When the caller has no selected database, its maintenance sender leaves that
selector absent instead of injecting null. Explicit selectors, including an
explicit malformed null that the provider must reject, are not rewritten.

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
`tests/test_cache_maintenance_client.py` separately enters the real CLI and
existing JSON transport with a caller cache that must never be touched, including
password expiry, root/field bindings, unsupported/rolled-back providers, fixed
diagnostics, absence of replay or activation, strict policy and no-target status.

See also: [architecture](architecture.md), [unlock warming](cache-warming.md),
and [version-skew-safe contracts](../../../docs/patterns/service-contract-evolution.md).
