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


def test_main_does_not_lower_priority_directly(monkeypatch) -> None:
    """``main()`` must NOT throttle the process itself anymore -- doing so
    before the model has loaded was observed to stall the one-time load
    (20s+ even unthrottled) indefinitely under host contention at Windows
    IDLE_PRIORITY_CLASS. The throttle is applied later, by
    ``_get_pipeline()``, only after a successful first load."""
    module = _load_module()

    calls: list[str] = []

    def fake_lower(nice: int) -> None:
        calls.append(f"lower:{nice}")

    monkeypatch.setattr(
        __import__("agent_index.indexing.priority", fromlist=["x"]),
        "lower_current_process_priority",
        fake_lower,
    )
    monkeypatch.setattr(module, "run_engine", lambda **_kw: calls.append("run_engine"))

    module.main([])

    assert calls == ["run_engine"]


class _FakePipeline:
    def __init__(self) -> None:
        self.warmed_up = False

    @property
    def is_loaded(self) -> bool:
        return self.warmed_up

    def warm_up(self) -> None:
        self.warmed_up = True


def test_get_pipeline_lowers_priority_only_after_warm_up(monkeypatch) -> None:
    """The throttle must be applied strictly AFTER ``warm_up()`` returns, not
    before -- otherwise the (slow, one-time) model load itself would run at
    the already-lowered priority, the exact bug this corrected ordering
    fixes."""
    import sys
    import types

    module = _load_module()
    calls: list[str] = []

    fake_pipeline = _FakePipeline()
    real_warm_up = fake_pipeline.warm_up

    def tracking_warm_up() -> None:
        calls.append("warm_up")
        real_warm_up()

    fake_pipeline.warm_up = tracking_warm_up  # type: ignore[method-assign]

    # `_get_pipeline()` does `from agent_index_engine.pipeline import
    # EmbeddingPipeline` as a local import -- `agent_index_engine` is a
    # separate installable package not normally on this suite's path (see
    # module docstring), so register a stub in sys.modules rather than
    # relying on it being genuinely importable.
    fake_pipeline_module = types.ModuleType("agent_index_engine.pipeline")
    fake_pipeline_module.EmbeddingPipeline = lambda *_a, **_kw: fake_pipeline  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agent_index_engine.pipeline", fake_pipeline_module)

    monkeypatch.setattr(module, "_check_gpu_deps", lambda: True)
    monkeypatch.setattr(module, "_lower_own_priority", lambda: calls.append("lower"))
    monkeypatch.setattr(module, "_config", None)
    monkeypatch.setattr(module, "_pipeline", None)

    result = module._get_pipeline()

    assert result is fake_pipeline
    assert calls == ["warm_up", "lower"]


def test_get_pipeline_does_not_relower_priority_once_loaded(monkeypatch) -> None:
    """A second call after the model is already loaded must be a cheap
    no-op -- never re-triggering the throttle (or a second model load)."""
    module = _load_module()
    calls: list[str] = []

    fake_pipeline = _FakePipeline()
    fake_pipeline.warmed_up = True  # already loaded
    monkeypatch.setattr(module, "_pipeline", fake_pipeline)
    monkeypatch.setattr(
        module, "_lower_own_priority", lambda: calls.append("lower")
    )

    result = module._get_pipeline()

    assert result is fake_pipeline
    assert calls == []


def test_lower_own_priority_reads_configured_engine_nice(monkeypatch) -> None:
    module = _load_module()
    calls: list[int] = []

    from agent_index.index_config import IndexConfig
    from agent_index.indexing import priority as priority_module

    monkeypatch.setattr(priority_module, "lower_current_process_priority", calls.append)
    monkeypatch.delenv("AGENT_INDEX_ENGINE_NICE", raising=False)

    module._lower_own_priority()

    assert calls == [IndexConfig().engine_nice]


def test_lower_own_priority_is_best_effort(monkeypatch) -> None:
    """A failure while applying the throttle must never propagate."""
    module = _load_module()

    def boom(nice: int) -> None:
        raise RuntimeError("platform doesn't support this")

    from agent_index.indexing import priority as priority_module

    monkeypatch.setattr(priority_module, "lower_current_process_priority", boom)

    module._lower_own_priority()  # must not raise

