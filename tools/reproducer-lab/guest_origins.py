"""Finite explicit guest runtime observations through the existing process owner."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import fields

import guest_runtime
from guest_fixture import SOURCE, environment
from guest_operation import GuestOperation
from process import run
from runtime_origin_probe import PLUGINS, RuntimeOrigin


def inspect_runtimes(custody: GuestOperation) -> tuple[RuntimeOrigin, ...]:
    """Observe three prepared runtimes; never initialize or launch a service.

    Requires separately authorized, qualified resources/image/source, fresh host
    monitoring and no competing runtime writers. Nothing is persisted as an
    installation, readiness or replay receipt.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("runtime origin inspection must run outside an event loop")
    custody.assert_owned()
    identity = guest_runtime._runtime_identity()
    observations: list[RuntimeOrigin] = []
    for plugin in PLUGINS:
        custody.assert_owned()
        prefix = guest_runtime.RUNTIME / plugin
        result = run(
            [str(prefix / "bin/python3"), "-I", "-B",
             str(SOURCE / "tools/reproducer-lab/runtime_origin_probe.py"), plugin],
            timeout=10, max_stdout_bytes=16 * 1024, env=environment(),
        )
        if result.returncode:
            raise RuntimeError(
                f"{plugin} runtime origin probe failed ({result.returncode}): {result.stderr}"
            )
        data = json.loads(result.stdout)
        if not isinstance(data, dict) or set(data) != {field.name for field in fields(RuntimeOrigin)}:
            raise ValueError("runtime origin probe returned unexpected fields")
        for key in ("python_files", "python_bytes"):
            if type(data[key]) is not int or data[key] <= 0:
                raise ValueError("runtime origin probe returned invalid Python inventory")
        for key in set(data) - {"python_files", "python_bytes"}:
            if not isinstance(data[key], str) or not data[key]:
                raise ValueError("runtime origin probe returned invalid attribution")
        if (
            data["plugin"] != plugin or data["prefix"] != str(prefix)
            or data["executable"] != str(prefix / "bin/python3")
            or data["source_package"] != str(SOURCE / "plugins" / ("agent-" + plugin) / "src" / ("agent_" + plugin))
        ):
            raise ValueError("runtime origin probe returned a different runtime")
        custody.assert_owned()
        if not os.path.samestat(identity, guest_runtime._runtime_identity()):
            raise ValueError("runtime directory changed during origin inspection")
        observations.append(RuntimeOrigin(**data))
    return tuple(observations)
