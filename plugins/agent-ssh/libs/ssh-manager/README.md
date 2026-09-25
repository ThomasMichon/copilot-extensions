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

## As a Dependency

In your plugin's `pyproject.toml`:

```toml
dependencies = [
    "ssh-manager @ file:///${PROJECT_ROOT}/../../libs/ssh-manager",
]
```

Or install editable for development:

```bash
pip install -e libs/ssh-manager
```
