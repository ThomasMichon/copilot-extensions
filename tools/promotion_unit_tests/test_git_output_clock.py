"""Admit the existing actor-free output-idle warning controls."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


if "agent_procutil" not in sys.modules:
    _load("agent_procutil", ROOT / "libs/agent-procutil/src/agent_procutil/__init__.py")

ORIGINAL = _load(
    "promotion_git_output_clock",
    ROOT / "libs/agent-procutil/tests/test_stream_activity.py",
)

stream = ORIGINAL.stream
test_silence_warns_once_but_does_not_replace_the_hard_cap = (
    ORIGINAL.test_soft_timeout_warns_once_without_shortening_the_hard_deadline
)
test_partial_bytes_reset_the_warning_without_renewing_lifetime = (
    ORIGINAL.test_soft_warning_resets_on_partial_output_bytes
)
test_continuing_bytes_cannot_extend_the_hard_cap = (
    ORIGINAL.test_continuing_output_cannot_renew_the_absolute_deadline
)


def test_windows_collector_retains_its_existing_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    package = ModuleType("unit_windows_output")
    package.__path__ = []
    boundary = ModuleType(package.__name__ + ".owned_process")
    setattr(boundary, "_remaining", lambda *_args: 1.0)
    monkeypatch.setitem(sys.modules, package.__name__, package)
    monkeypatch.setitem(sys.modules, boundary.__name__, boundary)
    module = _load(
        package.__name__ + "._windows_owned_output",
        ROOT / "libs/agent-procutil/src/agent_procutil/_windows_owned_output.py",
    )
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: 0.0))
    events: list[str] = []
    job = SimpleNamespace(terminate_and_wait=lambda seconds: events.append("retire"))
    process = SimpleNamespace(
        communicate=lambda text, *, timeout: ("output", "error"),
        wait=lambda *, timeout: events.append("wait"),
    )
    assert module.communicate(
        process, job, input_text=None, deadline=1, argv=["fixture"], timeout=1,
    ) == ("output", "error")
    assert events == ["retire", "wait"]
