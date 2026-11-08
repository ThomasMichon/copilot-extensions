"""Installation-local Windows scheduling intent, never repository preferences."""

from pathlib import Path

import yaml


def windows_scheduled_task(home: Path) -> str:
    path = home / "config.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("installation config.yaml must be a mapping")
    policy = data.get("windows_scheduled_task", "preserve")
    if not isinstance(policy, str) or policy not in ("preserve", "enabled", "absent"):
        raise ValueError("windows_scheduled_task must be preserve, enabled, or absent")
    return policy
