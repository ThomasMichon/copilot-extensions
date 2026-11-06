"""Legacy receiving-transport adapter for the independently built Host."""

from __future__ import annotations

import os
from pathlib import Path

import agent_procutil  # noqa: F401
import agent_remote_driver  # noqa: F401
from agent_remote_driver.session_host import bundle as _core
from agent_remote_driver.session_host.bundle import _PROCUTIL_MODULES  # noqa: F401


def bundle_source_hash() -> str:
    """Preserve the existing receiving contract and its cache identity."""
    return _core.bundle_source_hash(legacy=True)


def bundle_filename(source_hash: str | None = None) -> str:
    """Name the explicitly selected legacy receiving export."""
    return _core.bundle_filename(source_hash, legacy=True)


def build_session_host_bundle(
    dest_dir: str | os.PathLike[str] | None = None,
) -> tuple[Path, str]:
    """Export canonical core bytes plus import-only legacy receiving adapters."""
    return _core.build_session_host_bundle(dest_dir, legacy=True)
