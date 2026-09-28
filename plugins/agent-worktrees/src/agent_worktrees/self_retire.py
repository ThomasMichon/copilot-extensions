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
) -> bool:
    """Whether a live, strictly newer routed status-monitor superseded us."""
    table = read_table(config_dir)
    return _lib_is_superseded(
        table, my_pid, my_generation, is_listening=is_listening
    )
