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

The broker lives in the calling process and closes with its SSH root.
`terminate_ssh_process_tree` also awaits broker cleanup on timeout or
cancellation. No persistent broker service or cached loopback port is created.
POSIX transports retain native ProxyCommand execution.

## As a Dependency

In your plugin's `pyproject.toml`:

```toml
dependencies = [
    "ssh-manager @ file:///${PROJECT_ROOT}/../../libs/ssh-manager",
]
```

Or install both libraries into a development virtual environment:

```bash
uv pip install -e libs/agent-procutil -e libs/ssh-manager
```
