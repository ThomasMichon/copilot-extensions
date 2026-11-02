"""Status-monitor generation supersession checks."""

from __future__ import annotations

from single_instance_lease import is_listening as _is_listening
from single_instance_lease import is_superseded as _lib_is_superseded
from zdd import routing

__all__ = ["_is_listening", "is_superseded"]


def is_superseded(
    config_dir,
    my_pid: int,
    my_generation: int,
    *,
    read_table=routing.read_table,
    is_listening=_is_listening,
    require_ready: bool = False,
) -> bool:
    """Whether a live, strictly newer routed status-monitor superseded us."""
    table = read_table(config_dir)
    if require_ready:
        from . import status_monitor_cutover as cutover
        from . import status_monitor_recovery_contract as recovery

        raw = table.get("active") if isinstance(table, dict) else None
        endpoint = routing.Endpoint.from_dict(raw) if isinstance(raw, dict) else None
        if endpoint is None or endpoint.pid == my_pid:
            return False
        return (
            recovery.successor_healthy(
                endpoint.client_host, endpoint.port, cutover.ControlClient, expected=endpoint,
            )
            and read_table(config_dir) == table
        )
    return _lib_is_superseded(
        table, my_pid, my_generation, is_listening=is_listening
    )
