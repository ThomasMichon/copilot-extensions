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

from .config_sources import ConfigSource, SSHConfig, SSHProfileSource
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
    "ConnectionInfo",
    "ConnectionManager",
    "HealthStatus",
    "LockHolder",
    "MultiplexMode",
    "PlatformInfo",
    "SSHConfig",
    "SSHProfileSource",
    "TargetBusyError",
    "TargetLock",
    "check_health",
    "detect_platform",
    "ensure_healthy",
    "get_default_manager",
    "locks_dir",
    "pid_alive",
]
