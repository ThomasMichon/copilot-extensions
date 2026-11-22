"""Bounded commands for this selected installation's Driver service."""

from __future__ import annotations

import json
import re
import time

from . import service_lifecycle as lifecycle

_OPERATIONS = ("ensure", "status", "stop")


def main(arguments: list[str]) -> int:
    """Manage the selected service without reinstalling its plugin."""
    support = {
        "contract": "copilot-driver.service.commands", "version": 1,
        "operations": list(_OPERATIONS),
        "stop": {"instance_id_required": True},
    }
    if arguments == ["--support-json"]:
        print(json.dumps(support, sort_keys=True))
        return 0
    stopping = (
        len(arguments) == 3 and arguments[:2] == ["stop", "--instance-id"]
        and re.fullmatch(r"[a-f0-9]{32}", arguments[2]) is not None
    )
    if not stopping and (len(arguments) != 1 or arguments[0] not in ("ensure", "status")):
        raise ValueError(
            "Driver service requires exactly one supported operation: ensure, status, "
            "or stop --instance-id <id>"
        )
    deadline = time.monotonic() + 10
    binding = lifecycle.selected_source(deadline)
    if stopping:
        endpoint = lifecycle.stop(binding, arguments[2], deadline=deadline)
    elif arguments[0] == "ensure":
        endpoint = lifecycle.start(binding, deadline=deadline)
    else:
        endpoint = lifecycle._ready(binding.root, binding.slot.name, deadline)
    print(json.dumps({
        "contract": support["contract"], "version": support["version"],
        "state": "running" if endpoint is not None and not stopping else "stopped",
        "generation": binding.slot.name,
        "instance_id": endpoint.instance_id if endpoint is not None else (
            arguments[2] if stopping else None
        ),
        "pid": endpoint.pid if endpoint is not None else None,
    }, sort_keys=True))
    return 0
