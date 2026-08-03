"""Tests for the configurable engine lifecycle modes (engine/lifecycle.py)."""

from __future__ import annotations

import pytest

from agent_index.engine import lifecycle
from agent_index.engine.client import EngineUnavailableError
from agent_index.index_config import ModelProfile


@pytest.fixture(autouse=True)
def _clear_spawned():
    lifecycle._spawned.clear()
    yield
    lifecycle._spawned.clear()


class FakeClient:
    """Minimal EngineClient stand-in: only ``health()`` is exercised."""

    def __init__(self, reachable: bool = False):
        self._reachable = reachable

    def health(self) -> dict:
        return {"status": "ok" if self._reachable else "unreachable"}


class FakeProc:
    def __init__(self, alive: bool = True, returncode: int | None = None):
        self.pid = 4242
        self._alive = alive
        self.returncode = returncode
        self.terminated = False
        self.killed = False

    def poll(self):
        return None if self._alive else self.returncode

    def terminate(self):
        self.terminated = True
        self._alive = False
        self.returncode = 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True
        self._alive = False


def _profile(**kw) -> ModelProfile:
    return ModelProfile(model_id="code", model_name="m", **kw)


# -- mode resolution ---------------------------------------------------------


def test_resolve_mode_explicit_is_case_insensitive():
    assert lifecycle._resolve_mode(_profile(engine_mode="subprocess")) == "subprocess"
    assert lifecycle._resolve_mode(_profile(engine_mode="External")) == "external"
    assert lifecycle._resolve_mode(_profile(engine_mode="SYSTEMD")) == "systemd"


def test_resolve_mode_auto_without_unit_is_subprocess(monkeypatch):
    monkeypatch.setattr(lifecycle.shutil, "which", lambda _n: "/usr/bin/systemctl")
    assert lifecycle._resolve_mode(_profile(engine_mode="auto")) == "subprocess"


def test_resolve_mode_auto_with_unit_and_systemctl_is_systemd(monkeypatch):
    monkeypatch.setattr(lifecycle.shutil, "which", lambda _n: "/usr/bin/systemctl")
    assert (
        lifecycle._resolve_mode(_profile(engine_mode="auto", systemd_unit="u.service"))
        == "systemd"
    )


def test_resolve_mode_auto_with_unit_no_systemctl_falls_back_to_subprocess(monkeypatch):
    monkeypatch.setattr(lifecycle.shutil, "which", lambda _n: None)
    assert (
        lifecycle._resolve_mode(_profile(engine_mode="auto", systemd_unit="u.service"))
        == "subprocess"
    )


def test_profile_engine_mode_defaults_from_env(monkeypatch):
    monkeypatch.setenv("AGENT_INDEX_ENGINE_MODE", "external")
    assert ModelProfile(model_id="x", model_name="y").engine_mode == "external"


# -- ensure_engine -----------------------------------------------------------


def test_already_reachable_returns_false():
    assert lifecycle.ensure_engine(_profile(engine_mode="subprocess"),
                                   FakeClient(reachable=True)) is False


def test_external_mode_raises_when_unreachable():
    with pytest.raises(EngineUnavailableError, match="external"):
        lifecycle.ensure_engine(_profile(engine_mode="external"), FakeClient(False))


def test_subprocess_spawns_and_becomes_reachable(monkeypatch):
    proc = FakeProc()
    monkeypatch.setattr(lifecycle, "_spawn_engine", lambda profile: proc)
    calls = {"n": 0}

    def fake_reachable(_client):
        calls["n"] += 1
        return calls["n"] >= 2  # unreachable at the guard, reachable while awaiting

    monkeypatch.setattr(lifecycle, "_reachable", fake_reachable)
    monkeypatch.setattr(lifecycle.time, "sleep", lambda _s: None)

    prof = _profile(engine_mode="subprocess")
    assert lifecycle.ensure_engine(prof, FakeClient(False)) is True
    assert lifecycle._spawned.get("code") is proc


def test_subprocess_early_exit_raises(monkeypatch):
    proc = FakeProc(alive=False, returncode=1)
    monkeypatch.setattr(lifecycle, "_spawn_engine", lambda profile: proc)
    monkeypatch.setattr(lifecycle, "_reachable", lambda _c: False)
    monkeypatch.setattr(lifecycle.time, "sleep", lambda _s: None)
    with pytest.raises(EngineUnavailableError, match="exited early"):
        lifecycle.ensure_engine(_profile(engine_mode="subprocess"), FakeClient(False))


def test_systemd_without_unit_raises():
    with pytest.raises(EngineUnavailableError, match="systemd"):
        lifecycle.ensure_engine(_profile(engine_mode="systemd", systemd_unit=None),
                                FakeClient(False))


def test_systemd_starts_unit_and_becomes_reachable(monkeypatch):
    monkeypatch.setattr(lifecycle, "_systemctl", lambda *a, **k: True)
    calls = {"n": 0}

    def fake_reachable(_client):
        calls["n"] += 1
        return calls["n"] >= 2

    monkeypatch.setattr(lifecycle, "_reachable", fake_reachable)
    monkeypatch.setattr(lifecycle.time, "sleep", lambda _s: None)
    prof = _profile(engine_mode="systemd", systemd_unit="u.service")
    assert lifecycle.ensure_engine(prof, FakeClient(False)) is True


def test_systemd_start_failure_raises(monkeypatch):
    monkeypatch.setattr(lifecycle, "_systemctl", lambda *a, **k: False)
    monkeypatch.setattr(lifecycle, "_reachable", lambda _c: False)
    with pytest.raises(EngineUnavailableError, match="Failed to start engine unit"):
        lifecycle.ensure_engine(_profile(engine_mode="systemd", systemd_unit="u.service"),
                                FakeClient(False))


# -- stop_engine -------------------------------------------------------------


def test_stop_engine_subprocess_terminates_child():
    proc = FakeProc()
    lifecycle._spawned["code"] = proc
    lifecycle.stop_engine(_profile(engine_mode="subprocess"))
    assert proc.terminated is True
    assert "code" not in lifecycle._spawned


def test_stop_engine_external_is_noop():
    # No tracked process, external mode -- must not raise.
    lifecycle.stop_engine(_profile(engine_mode="external"))


def test_stop_engine_systemd_calls_systemctl(monkeypatch):
    stopped = {}
    monkeypatch.setattr(lifecycle, "_systemctl",
                        lambda *a, **k: stopped.update({"args": a}) or True)
    lifecycle.stop_engine(_profile(engine_mode="systemd", systemd_unit="u.service"))
    assert stopped["args"][0] == "stop"
