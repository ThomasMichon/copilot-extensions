"""Bind broker publication to its installation's canonical runtime retention."""

from __future__ import annotations

import importlib.util
import stat
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .windows_task_broker import Registration


def _generation(config: Registration) -> str | None:
    relative = Path(config.entrypoint).relative_to(config.root)
    if relative.parts[:1] != ("versions",):
        return None
    if len(relative.parts) < 4:
        raise ValueError("broker action must be inside its immutable runtime")
    return relative.parts[1]


def replace(config: Registration, registrations: list[Registration]) -> None:
    """Persist before external mutation; release only after recovery settles.

    Non-versioned independent installations have no canonical slot collector.
    Numbered installations must ship the canonical retention helper: a missing
    helper is not permission to rely on this installer's process lifetime.
    """
    generation = _generation(config)
    root = Path(config.root)
    if generation is None:
        if (root / "versions").exists():
            raise ValueError("broker action is outside the installation's runtime slots")
        return
    generations = []
    for registration in registrations:
        if registration.root != config.root or registration.owner != config.owner:
            raise ValueError("broker retention names another installation")
        retained = _generation(registration)
        if retained is None:
            raise ValueError("broker transition mixes runtime ownership models")
        generations.append(retained)
    source = root / "versions" / generation / ".runtime-bootstrap" / "versioned_runtime_leases.py"
    for path in (source, *source.parents):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("broker runtime retention helper must not be linked")
    if not source.is_file():
        raise ValueError("broker runtime retention helper is unavailable")
    spec = importlib.util.spec_from_file_location("_broker_runtime_retention", source)
    if spec is None or spec.loader is None:
        raise ValueError("broker runtime retention helper is unavailable")
    runtime = importlib.util.module_from_spec(spec)
    # Do not create bytecode or mutate the qualified immutable helper.
    exec(compile(source.read_bytes(), str(source), "exec"), runtime.__dict__)
    expected = runtime.read_runtime_references(root).get(config.owner, [])
    runtime.replace_runtime_references(root, config.owner, generations, expected=expected)
