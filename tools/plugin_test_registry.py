"""Read-only Windows environment snapshots for the existing test owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class _WindowsEnvironmentSnapshot:
    user: dict[str, tuple[Any, int]]
    machine: dict[str, tuple[Any, int]]


def read_registry_environment(platform: str) -> _WindowsEnvironmentSnapshot | None:
    if platform != "nt":
        return None
    import winreg

    def read(hive: int, subkey: str) -> dict[str, tuple[Any, int]]:
        values: dict[str, tuple[Any, int]] = {}
        try:
            key = getattr(winreg, "OpenKey")(hive, subkey, 0, getattr(winreg, "KEY_READ"))
        except FileNotFoundError:
            return values
        with key:
            index = 0
            while True:
                try:
                    name, value, value_type = getattr(winreg, "EnumValue")(key, index)
                except OSError as exc:
                    if getattr(exc, "winerror", None) != 259:
                        raise
                    break
                values[name] = (value, value_type)
                index += 1
        return values

    return _WindowsEnvironmentSnapshot(
        user=read(getattr(winreg, "HKEY_CURRENT_USER"), "Environment"),
        machine=read(
            getattr(winreg, "HKEY_LOCAL_MACHINE"),
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
    )
