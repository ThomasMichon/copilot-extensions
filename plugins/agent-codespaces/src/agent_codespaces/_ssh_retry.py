"""``ssh_manager.exec_with_retry``, imported lazily like every other ssh_manager use here.

Every idempotent tunnel operation (probes, staging, settings merges, installs)
goes through this so a transient dev-tunnel reset is retried with backoff in
one place, not re-derived per call site.
"""
from __future__ import annotations

from typing import Any


async def exec_with_retry(manager: Any, host: str, command: str, **kwargs: Any) -> Any:
    """Retry ``manager.exec_command`` on transient SSH/tunnel failures (idempotent commands only)."""
    from ssh_manager import exec_with_retry as _exec_with_retry

    return await _exec_with_retry(manager, host, command, **kwargs)
