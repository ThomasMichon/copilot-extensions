"""Platform detection and ControlPath handling for SSH multiplexing."""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class MultiplexMode(Enum):
    """How SSH connections are multiplexed on this platform."""

    CONTROL_MASTER = "control_master"  # Unix sockets (Linux/macOS/WSL)
    DIRECT = "direct"  # No multiplexing (native Windows fallback)


@dataclass(frozen=True)
class PlatformInfo:
    """Platform capabilities for SSH multiplexing."""

    mode: MultiplexMode
    socket_dir: Path
    max_socket_path: int  # max chars for Unix socket path

    @property
    def supports_control_master(self) -> bool:
        return self.mode == MultiplexMode.CONTROL_MASTER


def detect_platform() -> PlatformInfo:
    """Detect platform capabilities for SSH multiplexing.

    Linux/macOS/WSL: ControlMaster with Unix sockets.
    Native Windows: Direct SSH (no multiplexing).
    """
    if sys.platform == "win32" and not _is_wsl():
        socket_dir = Path.home() / ".ssh-manager" / "sockets"
        return PlatformInfo(
            mode=MultiplexMode.DIRECT,
            socket_dir=socket_dir,
            max_socket_path=260,  # Windows MAX_PATH, not really used
        )

    socket_dir = Path.home() / ".ssh-manager" / "sockets"
    return PlatformInfo(
        mode=MultiplexMode.CONTROL_MASTER,
        socket_dir=socket_dir,
        max_socket_path=108,  # Unix socket path limit
    )


def _is_wsl() -> bool:
    """Detect if running inside WSL (which supports Unix sockets)."""
    if sys.platform != "win32":
        return False
    # WSL sets this in /proc, but we're on win32 so check env
    return "WSL_DISTRO_NAME" in os.environ


def ensure_socket_dir(platform: PlatformInfo) -> None:
    """Create the socket directory if it doesn't exist."""
    platform.socket_dir.mkdir(parents=True, exist_ok=True)
    if platform.supports_control_master:
        # Restrict permissions on Unix
        try:
            platform.socket_dir.chmod(0o700)
        except OSError:
            pass  # Best-effort on platforms that don't support chmod


def socket_path_for_host(
    platform: PlatformInfo,
    host: str,
    user: str | None = None,
    port: int | None = None,
) -> Path:
    """Generate a short, unique socket path for a host.

    Uses a hash to keep paths under the 108-char Unix socket limit.
    The identity includes user, host, and port so different SSH targets
    to the same hostname get distinct sockets.
    """
    identity = f"{user or ''}@{host}:{port or 22}"
    short_hash = hashlib.sha256(identity.encode()).hexdigest()[:12]
    socket_name = f"{host[:20]}-{short_hash}"
    return platform.socket_dir / socket_name
