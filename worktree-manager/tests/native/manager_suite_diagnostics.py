"""Read installed planning facts and retain bounded advertised request logs."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
from typing import Any

MAX_LOG_BYTES = 4 * 1024 * 1024


def _engine_probe() -> dict[str, Any]:
    import agent_worktrees
    from agent_worktrees import config, reconcile, update_runtime, update_runtime_plan

    config.set_active_project("example")
    payload = reconcile.core_installed_payload_dir("agent-worktrees")
    targets = update_runtime._registered_plugin_targets()
    engine, modules, registered = update_runtime_plan.plan_portfolio(
        payload, "windows", None, force=True, targets=targets, force_runtimes=(),
    )
    steps = ([engine] if engine is not None else []) + list(modules) + list(registered)
    return {
        "engine": {"root": str(config.install_dir()), "prefix": sys.prefix,
                   "module": agent_worktrees.__file__},
        "portfolio": {step.name: {
            "eligible": step.eligible, "status": step.status,
            "root": str(step.runtime_root) if step.runtime_root else None,
            "payload": str(step.payload), "argv": list(step.argv),
        } for step in steps},
    }


def retain_driver_logs(case: Path, output: str) -> None:
    identifiers = sorted(set(re.findall(r"\bUpdate request ([0-9a-f]{32})\b", output)))
    if not identifiers:
        return
    root = case / "h" / ".copilot-extensions" / "update-driver"
    destination = case / "driver-logs"
    records = []
    for identifier in identifiers:
        record: dict = {"request_id": identifier}
        try:
            source = root / (identifier + ".log")
            if source.is_symlink() or not source.resolve().is_relative_to(case.resolve()):
                raise ValueError("Request log escapes the owned fixture")
            with source.open("rb") as stream:
                size = stream.seek(0, os.SEEK_END)
                stream.seek(max(0, size - MAX_LOG_BYTES))
                data = stream.read(MAX_LOG_BYTES)
            destination.mkdir(exist_ok=True)
            (destination / source.name).write_bytes(data)
            record.update(bytes=size, retained_bytes=len(data), truncated=size > len(data))
        except Exception as error:
            record["error"] = f"{type(error).__name__}: {error}"
        records.append(record)
    try:
        (case / "driver-diagnostics.json").write_text(
            json.dumps(records, indent=2), encoding="utf-8",
        )
    except OSError as error:
        print(f"Driver diagnostic write failed: {error}", file=sys.stderr)
