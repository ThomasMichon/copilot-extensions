# credential-relay

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
