"""Tests for capability detection + device selection (effort ...-engine-daemon, P7).

Adoption matches the indexer's engine to the host: CUDA when compatible, CPU only
above a floor, else an underpowered CPU-only host is hard-blocked at designation.
The engine also downgrades a configured cuda->cpu at load (vision
§capability-matched-engine-runtime).
"""

from __future__ import annotations

import argparse

import pytest

from agent_index import capability, config
from agent_index.__main__ import cmd_setup


# -- decide_device -----------------------------------------------------------


def test_gpu_host_picks_cuda():
    caps = {"cores": 2, "ram_gb": 4.0, "cuda": True}
    d = capability.decide_device(caps)
    assert d["device"] == "cuda" and d["ok"] is True  # GPU bypasses the CPU floor


def test_beefy_cpu_host_ok():
    caps = {"cores": 8, "ram_gb": 16.0, "cuda": False}
    d = capability.decide_device(caps)
    assert d["device"] == "cpu" and d["ok"] is True


def test_underpowered_cpu_host_blocked():
    caps = {"cores": 2, "ram_gb": 4.0, "cuda": False}
    d = capability.decide_device(caps)
    assert d["device"] == "cpu" and d["ok"] is False


def test_floor_is_boundary_inclusive():
    caps = {"cores": 4, "ram_gb": 8.0, "cuda": False}
    assert capability.decide_device(caps)["ok"] is True
    assert capability.decide_device({"cores": 3, "ram_gb": 8.0, "cuda": False})["ok"] is False
    assert capability.decide_device({"cores": 4, "ram_gb": 7.9, "cuda": False})["ok"] is False


def test_effective_device_downgrades_when_no_cuda(monkeypatch):
    monkeypatch.setattr(capability, "cuda_available", lambda: False)
    assert capability.effective_device("cuda") == "cpu"
    assert capability.effective_device("auto") == "cpu"
    assert capability.effective_device("cpu") == "cpu"


def test_effective_device_keeps_cuda_when_available(monkeypatch):
    monkeypatch.setattr(capability, "cuda_available", lambda: True)
    assert capability.effective_device("cuda") == "cuda"
    assert capability.effective_device("cpu") == "cpu"  # explicit cpu is honored


# -- setup integration -------------------------------------------------------


@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENT_INDEX_ROLE", raising=False)
    monkeypatch.delenv("AGENT_INDEX_DEVICE", raising=False)
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT_INDEX_MACHINE", "boxA")
    return tmp_path


def _args(**kw):
    base = dict(indexer=None, single=False, ssh=None, endpoint=None, repo=None,
                force=False, yes=True, json=True)
    base.update(kw)
    return argparse.Namespace(**base)


def test_setup_host_records_device(monkeypatch, _iso):
    monkeypatch.setattr(capability, "detect", lambda: {"cores": 8, "ram_gb": 16.0, "cuda": False})
    repo = _iso / "repo"; repo.mkdir()
    rc = cmd_setup(_args(single=True, repo=str(repo)))
    assert rc == 0
    assert config.machine_device() == "cpu"
    # recorded device flows into IndexConfig.device
    from agent_index.index_config import IndexConfig
    assert IndexConfig().device == "cpu"


def test_setup_hard_blocks_underpowered_host(monkeypatch, _iso):
    monkeypatch.setattr(capability, "detect", lambda: {"cores": 2, "ram_gb": 4.0, "cuda": False})
    repo = _iso / "repo"; repo.mkdir()
    rc = cmd_setup(_args(single=True, repo=str(repo)))
    assert rc == 1
    # blocked: no role written
    assert not (_iso / "home" / "config.yaml").exists()


def test_setup_force_overrides_block(monkeypatch, _iso):
    monkeypatch.setattr(capability, "detect", lambda: {"cores": 2, "ram_gb": 4.0, "cuda": False})
    repo = _iso / "repo"; repo.mkdir()
    rc = cmd_setup(_args(single=True, force=True, repo=str(repo)))
    assert rc == 0
    assert config.resolve_role() == "host"


def test_setup_client_skips_capability(monkeypatch, _iso):
    # A client designation must never be blocked by local capability.
    def _boom():
        raise AssertionError("capability must not be probed for a client")
    monkeypatch.setattr(capability, "decide_device", lambda *a, **k: _boom())
    repo = _iso / "repo"; repo.mkdir()
    rc = cmd_setup(_args(indexer="boxB", repo=str(repo)))
    assert rc == 0
    assert config.resolve_role() == "client"
