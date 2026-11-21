"""The factored reader preserves readonly enumeration and refusal semantics."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    import plugin_test_registry as registry
else:
    from tools import plugin_test_registry as registry


class EnumerationEnded(OSError):
    winerror = 259


class EnumerationDenied(OSError):
    winerror = 5


@pytest.mark.parametrize("outcome", ["finished", "missing", "denied"])
def test_snapshot_keeps_missing_empty_and_failed_reads_distinct(
    monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    opened: list[int] = []

    class Key:
        def __init__(self, hive: int) -> None:
            self.hive = hive

        def __enter__(self) -> Key:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

    def open_key(hive: int, subkey: str, reserved: int, access: int) -> Key:
        assert subkey.endswith("Environment") and reserved == 0 and access == 1
        opened.append(hive)
        if outcome == "missing":
            raise FileNotFoundError("fixture-missing")
        return Key(hive)

    def enum_value(key: Key, index: int) -> tuple[str, Any, int]:
        if outcome == "denied":
            raise EnumerationDenied("fixture-denied")
        if index:
            raise EnumerationEnded()
        return "Path", f"fixture-{key.hive}", 1

    monkeypatch.setitem(sys.modules, "winreg", SimpleNamespace(
        OpenKey=open_key, EnumValue=enum_value, KEY_READ=1,
        HKEY_CURRENT_USER=11, HKEY_LOCAL_MACHINE=22,
    ))
    assert registry.read_registry_environment("posix") is None
    assert not opened
    if outcome == "denied":
        with pytest.raises(EnumerationDenied, match="fixture-denied"):
            registry.read_registry_environment("nt")
        assert opened == [11]
    else:
        result = registry.read_registry_environment("nt")
        assert result is not None
        assert result.user == ({} if outcome == "missing" else {"Path": ("fixture-11", 1)})
        assert result.machine == ({} if outcome == "missing" else {"Path": ("fixture-22", 1)})
        assert opened == [11, 22]
