"""Compatibility entrypoint for Core's standalone Host observations."""

from __future__ import annotations

from typing import Any

from agent_procutil.daemon_observation import observe_instances
from agent_remote_driver.session_host.osutil import process_start_time
from agent_remote_driver.session_host.registered_inventory import (
    registered_hosts as _registered_hosts,
)


def registered_hosts() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return _registered_hosts(observer=observe_instances, birth_reader=process_start_time)
