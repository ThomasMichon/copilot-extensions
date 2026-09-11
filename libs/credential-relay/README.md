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

## Listener ownership

The relay binds loopback only. A requested port of `0` selects an OS-assigned
port. If a positive requested port is occupied, the server logs the collision
and binds an ephemeral port; consumers must follow the published actual port.
It never infers process ownership from a port or version and never terminates
the incumbent. A lifecycle owner must retire its attested predecessor before
the relay can reuse that predecessor's fixed port.

## Wire protocol

```
<action>\n          # optional -- defaults to 'get'
protocol=https\n
host=github.com\n
\n                  # blank line terminates the request
```

Response is git-credential-protocol `key=value` text terminated by a blank line.
