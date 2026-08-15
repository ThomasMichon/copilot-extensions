# credential-relay

Shared credential relay framework + host-credential sources for Copilot CLI
plugins (distribution `agent-credential-relay`, import module `credential_relay`).

Provides pluggable host-credential `CredentialSource` implementations and, for
plugins that need it, a `CredentialRelayServer` (git-credential-protocol TCP
server). **agent-mcp uses the sources directly** (`az_login`, `gh_auth`,
`git_credential`) inside its local bridge process; it does not import or call
agent-bridge. Other plugins may run the relay server in their own daemon/process
and share the same sources.

## Why a shared lib (not inside one plugin)

Runtime plugins run in standalone venvs and must not depend on each other's
packages. Shared credential code therefore lives in a small vendored lib that can
be installed into each runtime that needs host credentials, whether it embeds the
sources directly (agent-mcp) or exposes them through a relay server.

## Contents

- `credential_relay.server` — optional `CredentialRelayServer`, `RelayPolicy`,
  `RelayStats`.
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
