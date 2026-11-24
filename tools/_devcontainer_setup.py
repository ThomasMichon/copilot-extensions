"""Bounded container startup with live diagnostics and separate result metadata."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def bring_up(executable: str, repository: Path, instance_label: str, config_path: Path) -> str:
    args = [
        executable, "up",
        "--workspace-folder", str(repository),
        "--config", str(config_path),
        "--id-label", f"devcontainer-test-isolation.instance={instance_label}",
        "--log-level", "debug",
    ]
    result = subprocess.run(args, stdout=subprocess.PIPE, text=True, timeout=1800)
    if result.returncode != 0:
        raise SystemExit(
            f"devcontainer up failed (exit {result.returncode}): "
            f"{result.stdout.strip() or 'see setup stderr above'}"
        )
    container_id = None
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        container_id = record.get("containerId") or container_id
    if not isinstance(container_id, str) or not container_id:
        raise SystemExit("could not determine containerId from `devcontainer up` output")
    return container_id
