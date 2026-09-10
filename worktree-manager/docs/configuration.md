# Worktree Manager configuration

The Manager's user-owned configuration lives at
`~/.worktree-manager/config.toml`. It contains control-plane preferences and
provider settings that must not be committed to a project. The existing
`[source]` update-source table and the AHP provider table coexist in this file;
`worktree-manager source set/reset` preserves all non-source tables.

## Same-machine AHP provider

AHP is explicitly selected when creating or recreating a hosted session.
Configuring an endpoint only makes the provider available. Resuming a worktree
whose persisted execution leg is `active` or `unknown` forces that provider
even when the default-off AHP checkbox is unchecked; an unsupported provider
fails closed. A disposed or absent binding may launch directly.

```toml
[ahp]
endpoint_url = "ws://127.0.0.1:8765"
account = "example-user"
protocol_versions = ["0.7.0"]
auth_resource = "https://api.github.com"
connect_timeout_seconds = 10
lifecycle_timeout_seconds = 30
```

| Key | Default | Meaning |
|-----|---------|---------|
| `endpoint_url` | `""` | Required when AHP is selected. Must be a loopback `ws://` URL with an explicit port and no credentials, query, or fragment. |
| `account` | `""` | Optional expected repository account. When set, it must exactly match the account resolved by `agent-worktrees repos account-for`; an empty value accepts that resolved account. |
| `protocol_versions` | `["0.7.0"]` | Non-empty list offered during AHP initialization. |
| `auth_resource` | `"https://api.github.com"` | Resource sent in the AHP authentication request. |
| `connect_timeout_seconds` | `10` | Positive connection and ordinary request timeout. |
| `lifecycle_timeout_seconds` | `30` | Positive create/dispose timeout floor. |

The Manager resolves the repository account and mints its token only through
the pinned `agent-worktrees` process boundary. The token is held in memory,
passed to the AHP controller and the launched Copilot child, and is never
written to configuration, execution-leg state, logs, or command arguments.
Selecting AHP creates or verifies a session whose listed working directory
matches the exact worktree returned by `agent-worktrees resolve --json`, then
persists an opaque `provider = "ahp"` execution leg through an engine-owned
lifecycle reservation held against the finalize fence. Concurrent ensure,
dispose, and finalize operations cannot publish stale bindings; a newly created
session is disposed if publication fails. Endpoint, account, protocol, host, or
path mismatches fail closed.

The production Picker offers **Dispose hosted session** only for a local
AHP-owned worktree in `active` or `unknown` state. This explicit destructive
action verifies the exact worktree binding, disposes the host session, and marks
the execution leg `disposed`; normal finalization is then available. Closing
the terminal client never disposes the hosted session implicitly.
