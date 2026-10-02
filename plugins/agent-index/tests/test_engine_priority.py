"""Tests for the embedding-engine daemon's own host-politeness throttle.

``agent_index_engine`` is a separate installable package (``server/``), not
normally on this package's own import path, so the module is loaded directly
by file path -- mirroring the pattern used for other standalone entry-point
scripts in this test suite (e.g. ``test_maintenance_tick.py``).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

APP_PATH = (
    Path(__file__).resolve().parents[1]
    / "server"
    / "src"
    / "agent_index_engine"
    / "app.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("agent_index_engine_app", APP_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_main_lowers_priority_before_starting_the_server(monkeypatch) -> None:
    """The engine must throttle itself BEFORE ``run_engine`` (and therefore
    before any model loading / uvicorn startup), not after -- a throttle
    applied too late wouldn't have been in effect during the heaviest work."""
    module = _load_module()

    calls: list[str] = []

    def fake_lower(nice: int) -> None:
        calls.append(f"lower:{nice}")

    def fake_run_engine(*, host: str, port: int) -> None:
        calls.append("run_engine")

    from agent_index.index_config import IndexConfig
    from agent_index.indexing import priority as priority_module

    monkeypatch.setattr(priority_module, "lower_current_process_priority", fake_lower)
    monkeypatch.setattr(module, "run_engine", fake_run_engine)
    monkeypatch.delenv("AGENT_INDEX_ENGINE_NICE", raising=False)

    module.main([])

    assert calls == [f"lower:{IndexConfig().engine_nice}", "run_engine"]


def test_main_priority_throttle_is_best_effort(monkeypatch) -> None:
    """A failure while applying the throttle must never block engine startup."""
    module = _load_module()

    def boom(nice: int) -> None:
        raise RuntimeError("platform doesn't support this")

    calls: list[str] = []

    from agent_index.indexing import priority as priority_module

    monkeypatch.setattr(priority_module, "lower_current_process_priority", boom)
    monkeypatch.setattr(module, "run_engine", lambda **_kw: calls.append("run_engine"))

    module.main([])  # must not raise

    assert calls == ["run_engine"]
