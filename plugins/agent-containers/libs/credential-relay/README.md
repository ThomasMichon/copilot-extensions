# credential-relay

Shared credential relay framework + host-credential sources for Copilot CLI
plugins (distribution `agent-credential-relay`, import module `credential_relay`).

Provides a single `CredentialRelayServer` (git-credential-protocol TCP server)
plus pluggable host-credential `CredentialSource` implementations. **agent-bridge**
runs the relay in its daemon and discovers per-target source profiles from
provider plugins (**agent-codespaces**, **agent-containers**). Current providers
publish those profiles over their own CLI seams (for example
`agent-containers relay-profile`) with an in-process import kept as a fallback, so
a provider fix can reach bridge without requiring a bridge redeploy.

## Why a shared lib (not inside agent-bridge)

Each provider runs in its own standalone venv that does **not** contain
`agent_bridge`, while agent-bridge owns the long-running relay process. Shared
relay code must therefore be importable from both sides — hence a lib installed
into each relevant venv, vendored the same way as `ssh-manager`.

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
