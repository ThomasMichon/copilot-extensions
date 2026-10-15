"""Tests for the configurable engine lifecycle modes (engine/lifecycle.py)."""

from __future__ import annotations

import sys

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


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows priority-class spawn flag")
def test_spawn_engine_forces_normal_priority_class_on_windows(monkeypatch, tmp_path):
    """A worker that spawns a missing engine may itself be niced down (e.g.
    AGENT_INDEX_INDEXER_NICE) -- Windows child processes inherit the
    spawning process's priority class by default, which would otherwise
    leak that lowered priority onto the new engine and stall its one-time
    model load (the exact bug this explicit flag prevents; see
    agent_index_engine.app._get_pipeline's deferred-throttle sequencing,
    which only helps if the engine starts at normal priority in the first
    place)."""
    captured: dict = {}

    class _FakeProc:
        pid = 4242

    def fake_popen(cmd, **kwargs):
        captured.update(kwargs)
        return _FakeProc()

    monkeypatch.setattr(lifecycle.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("AGENT_INDEX_DATA_DIR", str(tmp_path))

    lifecycle._spawn_engine(_profile())

    _NORMAL_PRIORITY_CLASS = 0x00000020
    _DETACHED_PROCESS = 0x00000008
    assert captured["creationflags"] & _NORMAL_PRIORITY_CLASS
    # Still ORed with the shared launch's own detach flags, not overwritten.
    assert captured["creationflags"] & _DETACHED_PROCESS


def test_spawn_engine_launches_through_spawn_daemon(monkeypatch, tmp_path):
    """The per-reindex engine runs from the index data dir (never the caller's
    cwd), logs its stdio per model at warning level, and does not break away:
    the indexer that started it stops it."""
    captured: dict = {}
    monkeypatch.setenv("AGENT_INDEX_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AGENT_INDEX_ENGINE_LOG_LEVEL", raising=False)
    monkeypatch.setattr(
        lifecycle, "spawn_daemon", lambda cmd, **kwargs: captured.update(kwargs) or object()
    )

    lifecycle._spawn_engine(_profile())

    assert captured["cwd"] == tmp_path
    assert captured["log_path"].parent == tmp_path / "logs"
    assert captured["log_path"].name.startswith("engine-")
    assert captured["breakaway"] is False
    assert captured["env"]["AGENT_INDEX_ENGINE_LOG_LEVEL"] == "warning"


@pytest.mark.parametrize("override", ["AGENT_INDEX_CONFIG", "AGENT_INDEX_CONFIG_ROOT"])
def test_spawned_engine_reads_a_caller_relative_config(monkeypatch, tmp_path, override):
    """The engine runs from the data dir, but a caller-relative config override
    must still select the device its reader finds from the caller's cwd."""
    import subprocess

    caller = tmp_path / "caller"
    (caller / "cfg").mkdir(parents=True)
    (caller / "cfg" / "config.yaml").write_text("device: cpu\n", encoding="utf-8")
    monkeypatch.chdir(caller)
    monkeypatch.setenv("AGENT_INDEX_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("AGENT_INDEX_CONFIG", raising=False)
    monkeypatch.delenv("AGENT_INDEX_CONFIG_ROOT", raising=False)
    monkeypatch.setenv(override, "cfg/config.yaml" if override == "AGENT_INDEX_CONFIG" else "cfg")
    captured: dict = {}
    monkeypatch.setattr(
        lifecycle, "spawn_daemon", lambda cmd, **kwargs: captured.update(kwargs) or object()
    )

    lifecycle._spawn_engine(_profile())

    assert captured["cwd"] == tmp_path / "data"
    # Run the engine's own reader with the child's environment and cwd.
    child = subprocess.run(
        [sys.executable, "-c", "from agent_index.config import machine_device; print(machine_device())"],
        cwd=captured["cwd"], env=captured["env"], capture_output=True, text=True, timeout=60,
    )
    assert child.returncode == 0, child.stderr
    assert child.stdout.strip() == "cpu"


def test_home_relative_path_overrides_keep_their_expansion(monkeypatch, tmp_path):
    env = {"AGENT_INDEX_CONFIG": "~/cfg/config.yaml", "AGENT_INDEX_HOME": "state"}
    monkeypatch.chdir(tmp_path)
    lifecycle._anchor_relative_path_env(env)
    assert env["AGENT_INDEX_CONFIG"] == "~/cfg/config.yaml"
    assert env["AGENT_INDEX_HOME"] == str(tmp_path / "state")


def test_home_relative_index_home_roots_the_engine_in_the_home(monkeypatch, tmp_path):
    """`AGENT_INDEX_HOME="~/index-state"` must root the engine (cwd and stdio
    log) under the user's home, not under `<caller>/~/...`."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for name in ("AGENT_INDEX_DATA_DIR", "AGENT_INDEX_STATE_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENT_INDEX_HOME", "~/index-state")
    (tmp_path / "caller").mkdir()
    monkeypatch.chdir(tmp_path / "caller")
    captured: dict = {}
    monkeypatch.setattr(
        lifecycle, "spawn_daemon", lambda cmd, **kwargs: captured.update(kwargs) or object()
    )

    lifecycle._spawn_engine(_profile())

    data = home / "index-state" / "data"
    assert captured["cwd"] == data
    assert captured["log_path"].parent == data / "logs"


def test_a_caller_relative_local_model_is_anchored_but_a_hub_id_is_not(monkeypatch, tmp_path):
    (tmp_path / "models" / "local").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    local = {"AGENT_INDEX_MODEL": "models/local"}
    hub = {"AGENT_INDEX_MODEL": "jinaai/jina-embeddings-v2-base-code"}
    lifecycle._anchor_relative_path_env(local)
    lifecycle._anchor_relative_path_env(hub)
    assert local["AGENT_INDEX_MODEL"] == str(tmp_path / "models" / "local")
    assert hub["AGENT_INDEX_MODEL"] == "jinaai/jina-embeddings-v2-base-code"


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
