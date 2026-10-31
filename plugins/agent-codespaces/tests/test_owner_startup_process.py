"""Exercise readiness against a cold, isolated child without opening a connection."""

from __future__ import annotations

import subprocess
import sys
import types

import pytest
from agent_codespaces import connection_owner as owner


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
def test_default_budget_observes_delayed_child_beacon(monkeypatch, tmp_path):
    import agent_procutil

    live_file = tmp_path / "connection-owner.live.json"
    stop_file = tmp_path / "stop"
    monkeypatch.setattr(owner, "LIVE_FILE", live_file)
    monkeypatch.setattr(owner, "RUNTIME_DIR", tmp_path)
    real_spawn = agent_procutil.spawn_daemon
    children = []
    script = """
import os, sys, time
from pathlib import Path
from agent_codespaces import connection_owner as owner
root = Path(sys.argv[1])
owner.RUNTIME_DIR = root
owner.LIVE_FILE = root / "connection-owner.live.json"
owner.ensure_runtime_dir = lambda: None
time.sleep(6.0)
root.joinpath("writer-pid").write_text(str(os.getpid()), encoding="ascii")
owner._write_liveness(15.0)
deadline = time.monotonic() + 15.0
while not (root / "stop").exists() and time.monotonic() < deadline:
    time.sleep(0.05)
"""

    def spawn_fixture(argv, **kwargs):
        if argv != [sys.executable, "-m", "agent_codespaces", "owner"]:
            return real_spawn(argv, **kwargs)
        child = real_spawn([sys.executable, "-c", script, str(tmp_path)], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(agent_procutil, "spawn_daemon", spawn_fixture)
    try:
        assert owner.ensure_owner_running(types.SimpleNamespace()) is True
        assert len(children) == 1
        live = owner.read_liveness()
        assert live is not None
        assert live.pid == int((tmp_path / "writer-pid").read_text(encoding="ascii"))
        assert live.process_identity
        assert children[0].poll() is None
        assert not (tmp_path / "connection-owner.json").exists()
    finally:
        stop_file.touch()
        for child in children:
            try:
                child.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5.0)
