"""Read installed finite-collection support without importing the selected package."""

from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import NoReturn, cast

from . import health_slots as slots

MAX_DECLARATION = 4096


def _stamp(info: os.stat_result) -> tuple[int, int, int, int]:
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise slots.HealthError("installed-job-collection-unsupported")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise slots.HealthError("installed-job-collection-unsupported")
        result[key] = value
    return result


def _invalid_constant(_value: str) -> NoReturn:
    raise slots.HealthError("installed-job-collection-unsupported")


def _read_metadata(package: Path) -> object:
    """Installed wheel-cache hardlinks are valid; indirect or changing input is not."""
    plain = cast(Callable[..., Path], slots._plain)
    path = package / "health-collection-v1.json"
    descriptor = None
    try:
        plain(package, directory=True)
        before = _stamp(path.lstat())
        if before[2] > MAX_DECLARATION:
            raise slots.HealthError("installed-job-collection-unsupported")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            if _stamp(os.fstat(stream.fileno())) != before:
                raise slots.HealthError("installed-job-metadata-changed")
            raw = stream.read(MAX_DECLARATION + 1)
            after = _stamp(os.fstat(stream.fileno()))
        plain(package, directory=True)
        if len(raw) > MAX_DECLARATION or before != after or before != _stamp(path.lstat()):
            raise slots.HealthError("installed-job-metadata-changed")
        return cast(object, json.loads(
            raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_invalid_constant,
        ))
    except OSError:
        raise slots.HealthError("installed-job-collection-unsupported") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def require_collection_support(python: Path) -> None:
    """Isolate Linux support to one package in the installed virtual environment."""
    if sys.platform != "linux":
        raise slots.HealthError("native-health-job-unsupported")
    environment = python.parent.parent
    packages = [
        path / "site-packages" / "agent_machines"
        for path in (environment / "lib").glob("python[0-9]*")
        if re.fullmatch(r"python3\.[0-9]+", path.name) is not None
        and (path / "site-packages" / "agent_machines").is_dir()
    ]
    if len(packages) != 1:
        raise slots.HealthError("installed-job-collection-unsupported")
    package = packages[0]
    if package.is_symlink() or not package.resolve().is_relative_to(environment.resolve()):
        raise slots.HealthError("installed-job-collection-unsupported")
    declaration = _read_metadata(package)
    if (
        type(declaration) is not dict
        or declaration.get("schema") != "agent-machines.health.collection"
        or type(declaration.get("version")) is not int or declaration["version"] != 1
        or type(declaration.get("operations")) is not list
        or not all(type(item) is str for item in declaration["operations"])
        or not {"preview", "apply", "installed-job"}.issubset(declaration["operations"])
    ):
        raise slots.HealthError("installed-job-collection-unsupported")
