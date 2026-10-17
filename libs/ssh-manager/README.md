# ssh-manager

Shared SSH ControlMaster connection multiplexer for Copilot CLI plugins.

Provides a single `ConnectionManager` that owns one SSH ControlMaster
connection per remote host. Plugins that need SSH (agent-bridge,
agent-codespaces) import this library instead of spawning SSH directly.

## Features

- **Connection multiplexing** -- one ControlMaster per host, all commands
  share the same TCP connection
- **Pluggable config sources** -- `ConfigSource` protocol for SSH profile,
  CodeSpace, or custom SSH configurations
- **Health monitoring** -- `ssh -O check` with structured status reporting
- **Platform-aware** -- Unix sockets on Linux/macOS/WSL, direct-SSH fallback
  on native Windows
- **Async-first** -- built on asyncio, matches agent-bridge patterns
- **Owned Windows proxies** -- native proxy children run behind a per-SSH
  loopback broker with explicit no-window flags and byte-transparent pipes
- **Dial log** -- each connection attempt (`gh codespace ssh --config` fetch,
  ControlMaster start, direct-mode exec or stdio channel, health reconnect; dedicated
  port-forward and relay-channel ssh processes aren't logged yet) is one structured,
  redacted JSON line in a bounded per-target log under
  `~/.ssh-manager/dial-log/` (`SSH_MANAGER_DIAL_LOG_DIR` overrides it): kind,
  outcome (a direct-mode stdio channel records `spawned`: only its ssh start is
  known there, so it is neither `ok` nor a failure), elapsed time, attempt, account `pinned`/`ambient`, and a short
  stderr tail; user-only (0700 directory, 0600 files) on POSIX, one file per exact
  target (a readable prefix plus a digest). Recording is best-effort and never raises into a dial (on an event loop's thread it does no file I/O: a bounded background writer appends the line, dropping it when saturated);
  `ssh_manager.dial_log.summary()` counts dials per outcome over the last 10
  minutes and hour. File locks work from an empty lock file, without an
  unowned seed-byte write on Windows. Lock contention retains the two-second
  best-effort deadline; admitted records remain serialized without interleaving.
- **Safe carrier startup diagnostics** -- failed framed-HELLO negotiation reports
  its stage, exception type, observed pre-cleanup exit code (or unknown), and an
  allowlisted stderr reason. Stderr is drained concurrently with raw protocol
  stdout, including after successful startup; raw stderr, arguments and request
  payloads are never included in these diagnostics. A reason is a diagnostic
  hint, not proof of the remote failure's cause. `carrier.diagnostics()` adds
  `startup_failure` after failure and clears it on successful negotiation.
  Cleanup bounds the final stderr drain and closes its owned read transport
  even when a descendant keeps the remote pipe open; it does not change
  process-tree ownership. An exited transport is retired under the failure lock
  with its epoch invalidated before a replacement opens. Requests are registered
  only after startup and bound to that transport's epoch and output queue.
  Retirement fails the old transport's outstanding requests with the existing
  transport-loss error, including requests without a timeout; it neither fails
  fresh startup waiters nor resubmits operations on a replacement. Ownership
  metadata lasts only as long as the corresponding pending request. Retirement
  uses the existing transport-failure path, preserving subscription termination,
  eligible subscription restoration, reconnect backoff and automatic recovery.
  Existing reconnect/idle-retirement ownership is established before cancellable
  process cleanup so interruption cannot orphan live owners.

## Usage

```python
from ssh_manager import ConnectionManager, SSHProfileSource

manager = ConnectionManager()
source = SSHProfileSource(host_alias="my-server")

info = await manager.ensure_connected("my-server", source)
result = await manager.exec_command("my-server", "uname -a")
print(result.stdout)

await manager.disconnect("my-server")
```

## Windows proxy lifecycle

Managed commands, forwards, and probes use
`ssh_manager.proxy.create_ssh_subprocess`. On Windows, a configured
`ProxyCommand` runs in an owned loopback broker. OpenSSH launches a windowless
binary stdio client, which authenticates to that broker using a per-launch
capability. HostName, Port, credential-path expansion, and host-key checks stay
unchanged. The client and broker forward opaque bytes without a terminal or
text decoder.
Native Windows OpenSSH and Git for Windows retain their respective proxy-command
quoting and shell semantics.

Windows SSH roots and broker-owned proxy children omit Win32-OpenSSH's inherited
`c28fc6f98a2c44abbbd89d6a3037d0d9_POSIX_FD_STATE` environment key. That metadata
describes an ancestor's file descriptors, not the new redirected handles; passing
it through can silently break SSH stdio. All other ambient or explicitly supplied
environment entries are preserved without modifying the caller's environment.
An explicit empty environment stays empty. When the descriptor-state key is
absent, the original environment argument is left unchanged, including ambient
inheritance. POSIX environment handling is unchanged.

The broker lives in the calling process and closes with its SSH root.
`terminate_ssh_process_tree` also awaits broker cleanup on timeout or
cancellation. No persistent broker service or cached loopback port is created.
POSIX transports retain native ProxyCommand execution.

## As a Dependency

In your plugin's `pyproject.toml`:

```toml
dependencies = [
    "agent-ssh-manager @ file:///${PROJECT_ROOT}/../../libs/ssh-manager",
]
```

Or install both libraries into a development virtual environment:

```bash
uv pip install -e libs/agent-procutil -e libs/ssh-manager
```
