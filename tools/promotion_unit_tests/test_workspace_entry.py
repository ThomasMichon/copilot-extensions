"""Pure process-entry selection, not native stream or lifetime qualification."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    path = (
        Path(__file__).resolve().parents[2] / "plugins" / "agent-pull-requests"
        / "src" / "agent_pull_requests" / "_process_entry.py"
    )
    spec = importlib.util.spec_from_file_location("promotion_workspace_entry", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_python_selection_preserves_wrappers_without_unwrapping(
    api: ModuleType, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Wrapper:
        @property
        def buffer(self) -> object:
            pytest.fail("public Python selection must not bypass wrapper state")
    incoming, outgoing = Wrapper(), Wrapper()
    monkeypatch.setattr(sys, "stdin", incoming)
    monkeypatch.setattr(sys, "stdout", outgoing)
    assert api.streams() == (incoming, outgoing)


def test_process_entry_selection_is_scoped_and_resets_after_failure(
    api: ModuleType, monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_in, raw_out = object(), object()
    incoming = SimpleNamespace(buffer=SimpleNamespace(raw=raw_in))
    outgoing = SimpleNamespace(buffer=SimpleNamespace(raw=raw_out))
    monkeypatch.setattr(sys, "stdin", incoming)
    monkeypatch.setattr(sys, "stdout", outgoing)
    def entry() -> int:
        assert api.streams() == (raw_in, raw_out)
        raise ValueError("entry failed")
    with pytest.raises(ValueError, match="entry failed"):
        api.run(entry)
    assert api.streams() == (incoming, outgoing)
