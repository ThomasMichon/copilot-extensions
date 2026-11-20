"""Actor-free expiry controls retain live-Job checks and native query errors."""

from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "libs" / "agent-procutil" / "src"))
from agent_procutil import _job_scopes as scopes


@pytest.fixture
def native(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    class Boolean:
        def __init__(self) -> None:
            self.value = False

    module = ModuleType("ctypes")
    wintypes = ModuleType("ctypes.wintypes")
    setattr(wintypes, "BOOL", Boolean)
    setattr(module, "wintypes", wintypes)
    setattr(module, "byref", lambda value: SimpleNamespace(_obj=value))
    setattr(module, "get_last_error", lambda: 2)
    setattr(module, "WinError", lambda code: OSError(code, "scope unavailable"))
    monkeypatch.setitem(sys.modules, "ctypes", module)
    monkeypatch.setitem(sys.modules, "ctypes.wintypes", wintypes)
    return module


@pytest.mark.parametrize("retained", [False, True])
def test_absent_scope_does_not_hide_existing_scope(
    monkeypatch: pytest.MonkeyPatch, native: ModuleType, retained: bool,
) -> None:
    kernel = Mock()
    kernel.OpenJobObjectW.side_effect = [0, 10]
    kernel.CloseHandle.return_value = True

    def membership(process: int, job: int, inside: Any) -> bool:
        inside._obj.value = retained
        return True

    kernel.IsProcessInJob.side_effect = membership
    monkeypatch.setattr(scopes, "_api", lambda: kernel)
    environment = {scopes.ENVIRONMENT_KEY: ";".join((scopes.new_name(), scopes.new_name()))}
    with scopes.query_scopes(environment) as verify:
        if retained:
            with pytest.raises(OSError, match="launcher-owned cleanup Job"):
                verify(123)
        else:
            verify(123)
    kernel.IsProcessInJob.assert_called_once()
    kernel.CloseHandle.assert_called_once_with(10)


@pytest.mark.parametrize("error", [0, 3, 6])
def test_non_absence_error_closes_already_pinned_scopes(
    monkeypatch: pytest.MonkeyPatch, native: ModuleType, error: int,
) -> None:
    kernel = Mock()
    kernel.OpenJobObjectW.side_effect = [10, 0]
    monkeypatch.setattr(scopes, "_api", lambda: kernel)
    monkeypatch.setattr(native, "get_last_error", lambda: error)
    environment = {scopes.ENVIRONMENT_KEY: ";".join((scopes.new_name(), scopes.new_name()))}
    with pytest.raises(OSError, match="unavailable"):
        with scopes.query_scopes(environment):
            pytest.fail("unavailable scope admitted")
    kernel.CloseHandle.assert_called_once_with(10)
