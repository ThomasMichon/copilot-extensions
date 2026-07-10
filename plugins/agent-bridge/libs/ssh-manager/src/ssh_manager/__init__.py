"""ssh-manager -- shared SSH ControlMaster connection multiplexer.

Provides a single ConnectionManager that owns one SSH ControlMaster
connection per remote host. Plugins that need SSH import this library
instead of spawning SSH directly.

Usage::

    from ssh_manager import ConnectionManager, SSHProfileSource

    manager = ConnectionManager()
    source = SSHProfileSource(host_alias="my-server")

    info = await manager.ensure_connected("my-server", source)
    result = await manager.exec_command("my-server", "uname -a")
    print(result.stdout)

    await manager.disconnect("my-server")
"""

from .codespace_source import CodespaceConfigSource
from .config_sources import ConfigSource, SSHConfig, SSHProfileSource
from .forward import LocalForward, build_forward_ssh_args, pick_free_local_port
from .health import HealthStatus, check_health, ensure_healthy
from .locks import LockHolder, TargetBusyError, TargetLock, locks_dir, pid_alive
from .manager import (
    CommandResult,
    ConnectionInfo,
    ConnectionManager,
    get_default_manager,
)
from .platform import MultiplexMode, PlatformInfo, detect_platform

__all__ = [
    "CommandResult",
    "ConfigSource",
    "CodespaceConfigSource",
    "ConnectionInfo",
    "ConnectionManager",
    "HealthStatus",
    "LockHolder",
    "LocalForward",
    "MultiplexMode",
    "PlatformInfo",
    "SSHConfig",
    "SSHProfileSource",
    "TargetBusyError",
    "TargetLock",
    "build_forward_ssh_args",
    "check_health",
    "detect_platform",
    "ensure_healthy",
    "get_default_manager",
    "locks_dir",
    "pick_free_local_port",
    "pid_alive",
]
