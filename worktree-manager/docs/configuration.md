# Worktree Manager configuration

The Manager's user-owned configuration lives at
`~/.worktree-manager/config.toml`. It contains control-plane preferences and
provider settings that must not be committed to a project. The existing
`[source]` update-source, AHP provider and restart-recovery tables coexist in this file;
`worktree-manager source set/reset` preserves all non-source tables.

## Restart recovery requests

Both new behaviors default off. Existing bounded, report-only evidence sampling
is independent of these preferences.

```toml
[restart_recovery]
shutdown_notifications = false
automatic_restore = false
```

These are strictly boolean local requests, not capability attestations or
authorization. Unknown keys and invalid values visibly block recovery rather
than silently enabling a default. Source updates preserve this table. No
command here writes enrollment, installs a host startup service, changes a
system policy, or resumes a conversation.

`shutdown_notifications = true` requests the native Linux logind transport in
the existing active mux daemon. It acquires a delay inhibitor before subscribing
to shutdown signals; denied access is a blocker, never a reason to elevate.
The connection and FD exist only while the daemon holds its active lease and
admits loop mutations. Passive generations do not connect. WSL teardown, Windows,
macOS and containers are not qualified by Linux logind support.

The transport reports `qualified: false`: private D-Bus/FD-transfer tests do not
prove that the resident loop always services notifications within the native
delay budget while other work blocks it. This timing gate must close before
automatic recovery can rely on that notification. `automatic_restore = true`
currently reports a blocker because current-owner admission and acknowledged
exact-conversation restart Resume are not qualified; it never launches.

`worktree-manager mux-daemon recovery-evidence` is read-only and includes the
requested capability states. It does not connect to the system bus to establish
runtime readiness: a requested Linux notifier is `unconfirmed`, unsupported
platforms are `blocked`, and disabled requests are `disabled`. Requested but
unconfirmed/blocked capabilities, invalid configuration and unavailable
evidence produce a JSON blocker and nonzero exit. The resident daemon's
authenticated health response additionally exposes `restart_recovery` with its
actual notification transport state, admission and qualification.

## Picker logical machine roster

The production Picker reads the project's existing `machines.yaml` registry
(canonical `.agent-worktrees/machines.yaml`, additive legacy root registry,
or the existing knowledge overlay fallback). It consumes the shared
`machine_transport` normalization without rewriting those files.

A legacy Windows/WSL entry named `host` becomes two independent logical
machines, `host` (Windows) and `host-wsl` (WSL). Both are Profiles source
columns **and** target rows; WSL is not excluded from the source axis.
Explicit `execution_platform` entries retain their declared keys and aliases.
Generated key, alias, or duplicate-profile collisions refuse the roster rather
than silently selecting one execution space.

`physical_host` groups only visible labels: for example, `desk-win` and
`ubuntu` associated with `desk` display as `desk Win` and `desk WSL`.
Source IDs, matrix keys, SSH profiles, and persisted machine ownership retain
their logical identities. Local detection selects the running platform's
logical entry, so the Windows and WSL siblings never both become local sources.
The internal `legacy_key` marker allows an owner-derived legacy projection to
follow the known local platform; an explicitly declared logical selector on
the wrong platform fails closed rather than selecting its sibling.
Standalone native Windows/Linux entries continue to work without a sibling.

This is a read-only compatibility projection, not a persisted-state migration:
historical rows are not rewritten, existing canonical-over-legacy merge
precedence remains intact, and no bridge-address or environment dimension is
added. Transport configuration and provisioning remain owned by their existing
consumers, not by Picker display grouping.

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
