"""Export portable engine ingress without Index's application dependencies."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any, cast

import pytest

_source = (
    Path(__file__).resolve().parents[3]
    / "plugins/agent-index/tests/test_managed_portable_engine_sh.py"
)
_spec = importlib.util.spec_from_file_location("_index_portable_engine_sh_source", _source)
assert _spec and _spec.loader
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
sys.path.insert(0, str(_source.parent))
try:
    _spec.loader.exec_module(_module)
finally:
    sys.path.pop(0)
_original = cast(Any, _module)
pytestmark = _original.pytestmark
prepared_helper = _original.prepared_helper
test_portable_engine_uses_prepared_selection_and_foreground_exec = (
    _original.test_portable_engine_uses_prepared_selection_and_foreground_exec
)
test_portable_engine_nested_helper_uninstall = _original.test_portable_engine_nested_helper_uninstall


@pytest.mark.parametrize("eventual_absence", [False, True])
def test_cleanup_observation_requires_absence_after_permission_error(
    monkeypatch: pytest.MonkeyPatch, eventual_absence: bool,
) -> None:
    ticks = iter([0.0, 0.1, 0.2, 1.0])
    monkeypatch.setattr(_original, "time", SimpleNamespace(
        monotonic=lambda: next(ticks), sleep=lambda _: None,
    ))
    calls = 0

    def probe() -> None:
        nonlocal calls
        calls += 1
        if eventual_absence and calls == 3:
            raise ProcessLookupError
        raise PermissionError

    if eventual_absence:
        _original._wait_for_absence(probe, 0.5)
    else:
        with pytest.raises(AssertionError, match="cleanup deadline"):
            _original._wait_for_absence(probe, 0.5)
    assert calls == 3
