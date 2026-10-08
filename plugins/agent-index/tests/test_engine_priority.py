"""Tests for the embedding-engine daemon's own host-politeness throttle.

``agent_index_engine`` is a separate installable package (``server/``), not
normally on this package's own import path, so the module is loaded directly
by file path -- mirroring the pattern used for other standalone entry-point
scripts in this test suite (e.g. ``test_maintenance_tick.py``).

Platform split (see ``app.py``'s own docstrings for the full reasoning):
POSIX's ``os.nice()`` only affects the calling thread (inherited by threads
created *afterward*), so the throttle is applied eagerly at process startup,
in ``main()``, before uvicorn creates its worker/event-loop threads. Windows'
``SetPriorityClass`` is process-wide regardless of timing, but applying it
before the one-time model load was observed to stall that load indefinitely
under host contention at ``IDLE_PRIORITY_CLASS`` -- so Windows instead defers
it to ``_get_pipeline()``, strictly after a successful ``warm_up()``. Both
platforms share a once-per-process-lifetime guard so a later
``/spindown`` + reload cycle never re-triggers (or, on POSIX, compounds --
``os.nice()`` is a relative increment) the throttle.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

APP_PATH = (
    Path(__file__).resolve().parents[1]
    / "server"
    / "src"
    / "agent_index_engine"
    / "app.py"
)

_IS_WINDOWS = sys.platform.startswith("win")


def _load_module():
    spec = importlib.util.spec_from_file_location("agent_index_engine_app", APP_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakePipeline:
    def __init__(self) -> None:
        self.warmed_up = False

    @property
    def is_loaded(self) -> bool:
        return self.warmed_up

    def warm_up(self) -> None:
        self.warmed_up = True

    def unload(self) -> None:
        self.warmed_up = False


def _stub_pipeline_import(monkeypatch: pytest.MonkeyPatch, fake_pipeline: _FakePipeline) -> None:
    """`_get_pipeline()` does `from agent_index_engine.pipeline import
    EmbeddingPipeline` as a local import -- `agent_index_engine` is a
    separate installable package not normally on this suite's path, so
    register a stub in sys.modules rather than relying on it being genuinely
    importable."""
    fake_module = types.ModuleType("agent_index_engine.pipeline")
    fake_module.EmbeddingPipeline = lambda *_a, **_kw: fake_pipeline  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agent_index_engine.pipeline", fake_module)


# -- main(): POSIX eager / Windows deferred -----------------------------------


@pytest.mark.skipif(_IS_WINDOWS, reason="POSIX-only: eager throttle at startup")
def test_main_lowers_priority_eagerly_on_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_module()
    calls: list[str] = []
    monkeypatch.setattr(module, "_ensure_priority_lowered_once", lambda: calls.append("lower"))
    monkeypatch.setattr(module, "run_engine", lambda **_kw: calls.append("run_engine"))

    module.main([])

    assert calls == ["lower", "run_engine"]


@pytest.mark.skipif(not _IS_WINDOWS, reason="Windows-only: main() must NOT throttle directly")
def test_main_does_not_lower_priority_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_module()
    calls: list[str] = []
    monkeypatch.setattr(module, "_ensure_priority_lowered_once", lambda: calls.append("lower"))
    monkeypatch.setattr(module, "run_engine", lambda **_kw: calls.append("run_engine"))

    module.main([])

    assert calls == ["run_engine"]


# -- _get_pipeline(): Windows deferred / POSIX no-op --------------------------


@pytest.mark.skipif(not _IS_WINDOWS, reason="Windows-only deferred throttle path")
def test_get_pipeline_lowers_priority_only_after_warm_up_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The throttle must be applied strictly AFTER ``warm_up()`` returns, not
    before -- otherwise the (slow, one-time) model load itself would run at
    the already-lowered priority, the exact bug this corrected ordering
    fixes."""
    module = _load_module()
    calls: list[str] = []

    fake_pipeline = _FakePipeline()
    real_warm_up = fake_pipeline.warm_up

    def tracking_warm_up() -> None:
        calls.append("warm_up")
        real_warm_up()

    fake_pipeline.warm_up = tracking_warm_up  # type: ignore[method-assign]
    _stub_pipeline_import(monkeypatch, fake_pipeline)

    monkeypatch.setattr(module, "_check_gpu_deps", lambda: True)
    monkeypatch.setattr(module, "_ensure_priority_lowered_once", lambda: calls.append("lower"))
    monkeypatch.setattr(module, "_config", None)
    monkeypatch.setattr(module, "_pipeline", None)

    result = module._get_pipeline()

    assert result is fake_pipeline
    assert calls == ["warm_up", "lower"]


@pytest.mark.skipif(_IS_WINDOWS, reason="POSIX-only: _get_pipeline() must NOT throttle directly")
def test_get_pipeline_does_not_lower_priority_on_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_module()
    calls: list[str] = []

    fake_pipeline = _FakePipeline()
    _stub_pipeline_import(monkeypatch, fake_pipeline)

    monkeypatch.setattr(module, "_check_gpu_deps", lambda: True)
    monkeypatch.setattr(module, "_ensure_priority_lowered_once", lambda: calls.append("lower"))
    monkeypatch.setattr(module, "_config", None)
    monkeypatch.setattr(module, "_pipeline", None)

    result = module._get_pipeline()

    assert result is fake_pipeline
    assert calls == []


def test_get_pipeline_does_not_relower_priority_once_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second call after the model is already loaded must be a cheap
    no-op -- never re-triggering the throttle (or a second model load)."""
    module = _load_module()
    calls: list[str] = []

    fake_pipeline = _FakePipeline()
    fake_pipeline.warmed_up = True  # already loaded
    monkeypatch.setattr(module, "_pipeline", fake_pipeline)
    monkeypatch.setattr(module, "_ensure_priority_lowered_once", lambda: calls.append("lower"))

    result = module._get_pipeline()

    assert result is fake_pipeline
    assert calls == []


# -- the once-per-process-lifetime guard, platform-agnostic ------------------


def test_ensure_priority_lowered_once_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_module()
    calls: list[str] = []
    monkeypatch.setattr(module, "_priority_lowered", False)
    monkeypatch.setattr(module, "_priority_baseline", None)
    monkeypatch.setattr(module, "_lower_own_priority", lambda: calls.append("lower"))

    module._ensure_priority_lowered_once()
    module._ensure_priority_lowered_once()
    module._ensure_priority_lowered_once()

    assert calls == ["lower"]


@pytest.mark.skipif(not _IS_WINDOWS, reason="Windows-only restore capability")
def test_ensure_priority_lowered_once_captures_baseline_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-normal starting class (an engine deliberately launched niced,
    or above-normal) must be captured so a later restore returns to THAT
    class, not a hardcoded NORMAL_PRIORITY_CLASS."""
    module = _load_module()
    monkeypatch.setattr(module, "_priority_lowered", False)
    monkeypatch.setattr(module, "_priority_baseline", None)
    monkeypatch.setattr(module, "_lower_own_priority", lambda: None)

    from agent_index.indexing import priority as priority_module

    monkeypatch.setenv("AGENT_INDEX_ENGINE_NICE", "5")
    _ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000
    monkeypatch.setattr(
        priority_module, "get_current_priority_class", lambda: _ABOVE_NORMAL_PRIORITY_CLASS
    )

    module._ensure_priority_lowered_once()

    assert module._priority_baseline == _ABOVE_NORMAL_PRIORITY_CLASS


@pytest.mark.skipif(not _IS_WINDOWS, reason="Windows-only restore capability")
def test_ensure_priority_lowered_once_skips_baseline_when_throttle_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``engine_nice <= 0`` means the throttle is a no-op -- nothing was
    changed, so there must be nothing to restore later either (a later
    /spindown must not call ``set_priority_class`` at all)."""
    module = _load_module()
    monkeypatch.setattr(module, "_priority_lowered", False)
    monkeypatch.setattr(module, "_priority_baseline", None)
    monkeypatch.setattr(module, "_lower_own_priority", lambda: None)

    from agent_index.indexing import priority as priority_module

    monkeypatch.setenv("AGENT_INDEX_ENGINE_NICE", "0")
    calls: list[str] = []
    monkeypatch.setattr(
        priority_module, "get_current_priority_class", lambda: calls.append("queried") or 0x20
    )

    module._ensure_priority_lowered_once()

    assert calls == []  # never even queried -- the throttle never ran
    assert module._priority_baseline is None

    restore_calls: list[str] = []
    monkeypatch.setattr(priority_module, "set_priority_class", lambda _c: restore_calls.append("set"))
    module._reset_priority_for_reload()
    assert restore_calls == []  # nothing to restore


@pytest.mark.skipif(not _IS_WINDOWS, reason="Windows-only restore capability")
def test_reset_priority_for_reload_restores_captured_baseline_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module()
    calls: list[int] = []
    _ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000
    monkeypatch.setattr(module, "_priority_lowered", True)
    monkeypatch.setattr(module, "_priority_baseline", _ABOVE_NORMAL_PRIORITY_CLASS)

    from agent_index.indexing import priority as priority_module

    monkeypatch.setattr(priority_module, "set_priority_class", calls.append)

    module._reset_priority_for_reload()

    assert calls == [_ABOVE_NORMAL_PRIORITY_CLASS]
    assert module._priority_lowered is False
    assert module._priority_baseline is None


@pytest.mark.skipif(not _IS_WINDOWS, reason="Windows-only restore capability")
def test_reset_priority_for_reload_is_noop_when_never_lowered(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_module()
    calls: list[int] = []
    monkeypatch.setattr(module, "_priority_lowered", False)
    monkeypatch.setattr(module, "_priority_baseline", None)

    from agent_index.indexing import priority as priority_module

    monkeypatch.setattr(priority_module, "set_priority_class", calls.append)

    module._reset_priority_for_reload()

    assert calls == []


@pytest.mark.skipif(_IS_WINDOWS, reason="POSIX has nothing to restore (no root)")
def test_reset_priority_for_reload_is_noop_on_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_module()
    calls: list[int] = []
    monkeypatch.setattr(module, "_priority_lowered", True)
    monkeypatch.setattr(module, "_priority_baseline", None)

    from agent_index.indexing import priority as priority_module

    monkeypatch.setattr(priority_module, "set_priority_class", calls.append)

    module._reset_priority_for_reload()

    assert calls == []
    # POSIX never resets the guard -- there is nothing to protect against on
    # a later reload (see module docstring): it was already lowered once,
    # eagerly, at process startup, and stays that way for the process's
    # whole lifetime.
    assert module._priority_lowered is True


# -- regression: spinup -> spindown -> spinup must not compound/re-stall -----


@pytest.mark.skipif(not _IS_WINDOWS, reason="exercises the Windows defer+restore lifecycle")
def test_spinup_spindown_spinup_does_not_compound_or_restall(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact scenario review flagged: on Windows, a /spindown must
    restore the captured baseline priority so the NEXT /spinup's warm_up()
    doesn't inherit an already-lowered priority and reproduce the
    load-stall bug a second time; the throttle itself must not compound
    across the cycle."""
    from fastapi.testclient import TestClient

    module = _load_module()

    fake_pipeline = _FakePipeline()
    _stub_pipeline_import(monkeypatch, fake_pipeline)
    monkeypatch.setattr(module, "_check_gpu_deps", lambda: True)
    monkeypatch.setattr(module, "_config", None)
    monkeypatch.setattr(module, "_pipeline", None)
    monkeypatch.setattr(module, "_priority_lowered", False)
    monkeypatch.setattr(module, "_priority_baseline", None)
    monkeypatch.setenv("AGENT_INDEX_ENGINE_NICE", "5")

    lower_calls: list[int] = []
    restore_calls: list[int] = []
    monkeypatch.setattr(module, "_lower_own_priority", lambda: lower_calls.append(1))

    from agent_index.indexing import priority as priority_module

    _NORMAL_PRIORITY_CLASS = 0x00000020
    monkeypatch.setattr(
        priority_module, "get_current_priority_class", lambda: _NORMAL_PRIORITY_CLASS
    )
    monkeypatch.setattr(priority_module, "set_priority_class", restore_calls.append)

    with TestClient(module.app) as client:
        r1 = client.post("/spinup")
        assert r1.status_code == 200
        assert lower_calls == [1]  # lowered once after the first load

        r2 = client.post("/spindown")
        assert r2.status_code == 200
        assert restore_calls == [_NORMAL_PRIORITY_CLASS]  # restored to the captured baseline
        assert module._priority_lowered is False

        r3 = client.post("/spinup")
        assert r3.status_code == 200
        assert lower_calls == [1, 1]  # lowered again for the second load -- never compounded



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

