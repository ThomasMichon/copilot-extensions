# credential-relay

**Opt-in resident account workers:** see
[`account-relay`](../../plugins/agent-index/docs/account-relay.md) for the dedicated
Bridge-owned account-only listener and exact source/account/recipient grants.
This mode neither changes legacy transports nor represents account tokens as
downscoped credentials.

Shared credential relay framework + host-credential sources for Copilot CLI
plugins (distribution `agent-credential-relay`, import module `credential_relay`).

Provides a single `CredentialRelayServer` (git-credential-protocol TCP server)
plus pluggable host-credential `CredentialSource` implementations. **agent-bridge**
runs the relay in its daemon and discovers per-target source profiles injected by
provider plugins (**agent-codespaces**, **agent-containers**), so the bridge core
no longer imports a provider package.

## Why a shared lib (not inside agent-bridge)

The agent-bridge daemon venv has every provider installed, but each provider also
runs in its own standalone venv that does **not** contain `agent_bridge` (e.g.
agent-codespaces `auth_preflight`). Shared relay code must therefore be importable
from every venv — hence a lib installed into each, vendored the same way as
`ssh-manager`.

## Contents

- `credential_relay.server` — `CredentialRelayServer`, `RelayPolicy`, `RelayStats`.
- `credential_relay.token_store` - strict token-map reads shared by providers
  and Bridge. Only file absence means an empty map; malformed JSON or a
  non-object root refuses. Windows access/sharing failures receive at most
  five bounded delays (0.62 seconds total); other I/O errors fail immediately.
  Authorization readers fail closed after that budget without rewriting state.
  This helper does not acquire credentials, cache secrets or own writer locks.
  The relay awaits synchronous authorization callbacks in worker threads, so
  their file retries cannot block unrelated requests or pings on its event loop.
- `credential_relay.sources` — `CredentialSource` protocol.
- `credential_relay.sources.{git_credential,gh_auth,az_login}` — generic
  host-credential sources (shell out to host `git` / `gh` / `az`).
- `credential_relay.token_gate.RelayTokenGate` — explicit immutable ownership of
  build-time validator/authorizer composition and static scope fallback. Builder
  callbacks retain any-match and remembered scope-denial behavior. This owner
  does not itself serialize callbacks or authorize a runtime handoff.
- `credential_relay.http_auth.SourceHttpAuth` binds a source to one exact
  origin/identity/generation context without another cache. Its default token
  scheme and rejection of resource/scope remain compatible; a constrained
  ASCII `scheme` and explicit `scoped=True` allow an already-approved source
  to honor exact resource/scope fields.
- `credential_relay.http_sources.HttpSourceRegistry` holds provider-qualified
  HTTP sources and refuses ambiguous references. Providers contribute with
  `RelayBuilder.add_http_source(reference, source, scoped=False)`. Registration
  is not caller approval: the consuming service still binds and authorizes
  the endpoint, identity, resource, scope and scheme before acquiring secrets.

## Wire protocol

```
<action>\n          # optional -- defaults to 'get'
protocol=https\n
host=github.com\n
\n                  # blank line terminates the request
```

Response is git-credential-protocol `key=value` text terminated by a blank line.

## Type checking and compatibility

Canonical Python source/test files are checked strictly on the Linux
and Windows analysis targets:

```text
python tools/typecheck.py lib-credential-relay --prepare
python tools/typecheck.py lib-credential-relay
```

The library remains dependency-free at runtime and supports Python 3.10 and
newer. Its four consumers (`agent-bridge`, `agent-codespaces`,
`agent-containers`, and `agent-mcp`) resolve editable canonical sources on Dev;
the release materializer owns their self-contained copies. Canonical admission
does not admit those consumers or qualify their fresh installation.

The account listener's lifecycle and publication record, builder keyword
options, source callbacks, and test fixtures have explicit contracts. Account
policy and Azure CLI JSON values remain dynamic at their existing validation
boundaries; annotations do not validate provider data, alter scope decisions,
or change credential expiry. OS-selected stdlib locking APIs retain their
actual signatures on both analysis targets, without requiring platform stubs.
