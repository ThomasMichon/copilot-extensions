# credential-relay

Shared credential relay framework + host-credential sources for Copilot CLI
plugins (distribution `agent-credential-relay`, import module `credential_relay`).

Provides a single `CredentialRelayServer` (git-credential-protocol TCP server)
plus pluggable host-credential `CredentialSource` implementations. **agent-bridge**
runs the relay in its daemon and discovers per-target source profiles injected by
provider plugins (**agent-codespaces**, **agent-containers**), so the bridge core
no longer imports a provider package.

## Why a shared lib (not inside agent-bridge)

agent-bridge and each provider run in separate standalone venvs. Providers expose
their relay profile over their own CLI (`relay-profile`), while agent-bridge
hosts the shared server and applies those profiles. Shared relay code must
therefore be importable from every participating venv — hence a lib installed
into each, vendored the same way as `ssh-manager`.

## Contents

- `credential_relay.server` — `CredentialRelayServer`, `RelayPolicy`, `RelayStats`.
- `credential_relay.sources` — `CredentialSource` protocol.
- `credential_relay.sources.{git_credential,gh_auth,az_login}` — generic
  host-credential sources (shell out to host `git` / `gh` / `az`).

## Wire protocol

```
<action>\n          # optional -- defaults to 'get'
protocol=https\n
host=github.com\n
\n                  # blank line terminates the request
```

Response is git-credential-protocol `key=value` text terminated by a blank line.
