"""Explicit systemd property fixtures, never inferred success from blank output."""

from __future__ import annotations

from pathlib import Path


def unit_properties(
    unit: str,
    directory: Path,
    *,
    active: str | None = None,
    load: str = "loaded",
    job: int = 0,
    pid: int = 0,
    drop_ins: str = "",
    sub: str | None = None,
) -> str:
    timer = unit.endswith(".timer")
    state = active or ("active" if timer else "inactive")
    if load == "not-found":
        state = active or "inactive"
    properties = {
        "LoadState": load,
        "ActiveState": state,
        "SubState": sub or ("waiting" if timer and state == "active" else "dead"),
        "Job": str(job),
        "FragmentPath": str(directory / unit) if load == "loaded" else "",
        "DropInPaths": drop_ins,
    }
    if not timer:
        properties["MainPID"] = str(pid)
    return "\n".join(f"{key}={value}" for key, value in properties.items()) + "\n"


def fixture_unit_properties(unit: str, directory: Path) -> str:
    return unit_properties(
        unit,
        directory,
        load="loaded" if (directory / unit).is_file() else "not-found",
    )
