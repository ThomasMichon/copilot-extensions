"""Explicit launch-origin recording before ephemeral activation overrides."""

from __future__ import annotations

import sys
import os
from pathlib import Path

from zdd.private_protocol import PrivateIPCError, Refusal

from .effective_runtime import RuntimeOrigins
from .install_paths import effective_config_dir, install_dir
from .models import ServiceConfig


def executing_executable() -> Path:
    """Record this process's interpreter, never the predecessor's metadata."""
    try:
        if os.name == "nt":
            from zdd.private_windows_native import Native

            native = Native()
            return Path(native.process_image(native.k.GetCurrentProcess())).resolve(strict=True)
        if not sys.executable:
            raise PrivateIPCError(Refusal.AUTHORITY)
        return Path(sys.executable).resolve(strict=True)
    except (OSError, ValueError, RuntimeError):
        raise PrivateIPCError(Refusal.AUTHORITY) from None


def record_launch_origins(
    config: ServiceConfig, *, required_environment: tuple[str, ...],
) -> RuntimeOrigins:
    """Record the launch owner's roots and durable policy without reading secrets.

    Call before changing working directory, bind port or passive relay policy.
    Required environment has no implicit default; recording is not activation.
    """
    if type(config) is not ServiceConfig:
        raise PrivateIPCError(Refusal.AUTHORITY)
    try:
        return RuntimeOrigins(
            effective_config_dir().expanduser().resolve(), install_dir().expanduser().resolve(),
            executing_executable(), config.enable_credential_relay, config.port,
            required_environment,
        )
    except (OSError, ValueError, RuntimeError):
        raise PrivateIPCError(Refusal.AUTHORITY) from None
