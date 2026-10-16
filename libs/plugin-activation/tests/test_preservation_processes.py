"""Fresh processes recover only witnessed transactions after real parent death."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from agent_procutil import direct_python, no_window_kwargs

from plugin_activation import PluginStateError, preserve_activation_durably
from test_preservation import IDENTITY, PEER, assert_restored, initialize, journal

_CHILD = r'''
import json, os, sys, time
from pathlib import Path
from plugin_activation import preserve_activation_durably, state
home, ready = Path(sys.argv[1]), Path(sys.argv[3])
mode = sys.argv[2]
identity, peer = "agent-example@market", "agent-peer@market"
def pause():
    pending = ready.with_suffix(".pending")
    pending.write_text(str(os.getpid()), encoding="ascii")
    pending.replace(ready)
    time.sleep(20)
original_write = state.write_json_object_atomic
def write(path, value, *args):
    if mode == "partial" and path == home / "config.json":
        pause()
    original_write(path, value, *args)
if mode == "partial":
    state.write_json_object_atomic = write
with preserve_activation_durably(identity, home) as transaction:
    (home / "config.json").write_text(json.dumps({"installedPlugins": [
        {"name": identity, "enabled": True, "version": "new"},
        {"name": peer, "enabled": True}]}), encoding="utf-8")
    (home / "settings.json").write_text(json.dumps({
        "enabledPlugins": {identity: True, peer: True}, "unrelated": "new"
    }), encoding="utf-8")
    if mode == "running":
        pause()
    transaction.confirm_quiescent()
    if mode == "quiescent":
        pause()
'''


@contextmanager
def holder(home: Path, mode: str):
    ready = home / "ready"
    python, extra_env = direct_python(sys.executable)
    env = {**os.environ, **extra_env, "PYTHONPATH": os.pathsep.join(sys.path)}
    child = subprocess.Popen(
        [python, "-c", _CHILD, str(home), mode, str(ready)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        **no_window_kwargs(),
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "preservation process did not reach the interruption boundary"
        assert child.pid == int(ready.read_text(encoding="ascii"))
        yield child
    finally:
        if child.poll() is None:
            child.kill()
        stdout, stderr = child.communicate(timeout=5)
        assert not stdout, (stdout, stderr)


@pytest.mark.parametrize("mode", ["running", "quiescent", "partial"])
def test_forced_parent_death_keeps_original_and_requires_witness(tmp_path, mode):
    initialize(tmp_path)
    with holder(tmp_path, mode) as child:
        with pytest.raises(PluginStateError, match="custody refused"):
            with preserve_activation_durably(PEER, tmp_path):
                pytest.fail("a second process entered the held home")
        child.kill()
        child.wait(timeout=5)
    record = journal(tmp_path).read_bytes()
    if mode == "running":
        with pytest.raises(PluginStateError, match="settlement is unproven"):
            with preserve_activation_durably(IDENTITY, tmp_path):
                pytest.fail("parent death is not child settlement")
        assert journal(tmp_path).read_bytes() == record
    else:
        with preserve_activation_durably(PEER, tmp_path) as transaction:
            assert_restored(tmp_path)
            transaction.confirm_not_launched()
        assert not journal(tmp_path).exists()


def test_fresh_interpreter_can_recover_without_replaying_the_command(tmp_path):
    initialize(tmp_path)
    with holder(tmp_path, "partial") as child:
        child.kill()
        child.wait(timeout=5)
    recovery = (
        "from pathlib import Path; import sys; "
        "from plugin_activation import preserve_activation_durably; "
        "guard=preserve_activation_durably('agent-peer@market',Path(sys.argv[1])); "
        "transaction=guard.__enter__(); transaction.confirm_not_launched(); "
        "guard.__exit__(None,None,None)"
    )
    python, extra_env = direct_python(sys.executable)
    result = subprocess.run(
        [python, "-c", recovery, str(tmp_path)],
        env={**os.environ, **extra_env, "PYTHONPATH": os.pathsep.join(sys.path)},
        capture_output=True, text=True, timeout=10, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert_restored(tmp_path)
    assert not journal(tmp_path).exists()
