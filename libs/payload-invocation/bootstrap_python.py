"""Publish inert bootstrap requests without changing application launchers."""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any


def expected_files(manifest: Path, data: dict[str, Any]) -> dict[Path, str]:
    destination = manifest.parent / "scripts/bootstrap-python-version.txt"
    regular = destination.is_file()
    occupied = regular or destination.is_symlink()
    if occupied:
        declared_inputs = [data.get("catalogGate")]
        dispatchers = data.get("payloadDispatcher")
        if isinstance(dispatchers, dict):
            declared_inputs.extend(dispatchers.values())
        for value in declared_inputs:
            if not isinstance(value, str) or not value:
                continue
            source = manifest.parent / value
            if source == destination or (regular and source.is_file() and source.samefile(destination)):
                if "bootstrapPython" in data:
                    raise ValueError(f"{manifest}: bootstrapPython output conflicts with a declared input")
                return {}
    if "bootstrapPython" not in data:
        if occupied:
            raise ValueError(f"{manifest}: obsolete bootstrap Python request; remove {destination.name}")
        return {}
    version = data["bootstrapPython"]
    if (
        not isinstance(version, str) or len(version) > 32
        or not re.fullmatch(r"3\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", version)
    ):
        raise ValueError(f"{manifest}: bootstrapPython must be an exact stable CPython version")
    return {destination: f"cpython@{version}\n"}
