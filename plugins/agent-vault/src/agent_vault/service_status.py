"""Pure rendering for daemon, password-lifetime and cache-warming status."""

from __future__ import annotations

from collections.abc import Mapping

from .password_lifetime import format_password_lifetime
from .cache_warm_status import format_cache_warm


def format_service_status(
    response: Mapping[str, object], *, status: str, transport: str,
) -> str:
    daemon_lifetime = (
        "daemon=persistent" if response.get("daemon_persistent") is True
        else f"daemon idle TTL {response['ttl']}s"
    )
    password_lifetime = format_password_lifetime(response.get("password_lifetime"))
    warming = (
        f", cache warm={format_cache_warm(response['cache_warm'])}" if "cache_warm" in response else ""
    )
    return (
        f"Vault service running - PID {response['pid']}, "
        f"{daemon_lifetime}, password lifetime={password_lifetime}, "
        f"{response['cached']} cached, "
        f"status={status}, via {transport}{warming}"
    )
