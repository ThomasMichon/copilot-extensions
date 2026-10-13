"""Native deadline coverage for a provider wrapper retaining child pipes."""

import os
import sys
import time

import pytest

from agent_bridge import agent_registry_relay as relay
from agent_bridge import provider_process
from agent_bridge.provider_process import run_captured


def test_captured_provider_output_refuses_invalid_utf8():
    command = [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xff')"]
    with pytest.raises(UnicodeDecodeError):
        run_captured(command, timeout=10)
    assert run_captured(command, timeout=10, errors="replace").stdout == "\ufffd"


def test_provider_command_requires_non_breakaway_tree_ownership(monkeypatch):
    spawn = provider_process.spawn_sync_in_kill_on_close_job
    calls = []

    def tracked_spawn(argv, **kwargs):
        calls.append(kwargs)
        return spawn(argv, **kwargs)

    monkeypatch.setattr(provider_process, "spawn_sync_in_kill_on_close_job", tracked_spawn)
    result = run_captured([sys.executable, "-c", "print('profile')"], timeout=10)

    assert result.stdout.strip() == "profile"
    assert calls[0]["require_job"] is True
    assert calls[0]["allow_breakaway"] is False


@pytest.mark.skipif(os.name != "nt", reason="Windows subprocess.run pipe-drain regression")
def test_relay_profile_timeout_does_not_wait_for_wrapper_descendants(tmp_path, monkeypatch):
    script = tmp_path / "provider.py"
    ready = tmp_path / "spawned"
    script.write_text(
        "import pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(8)'], creationflags=subprocess.CREATE_NO_WINDOW)\n"
        f"pathlib.Path({str(ready)!r}).write_text(str(child.pid))\n"
        "time.sleep(8)\n",
        encoding="utf-8",
    )
    wrapper = tmp_path / "provider.cmd"
    wrapper.write_text(
        f'@echo off\n"{sys.executable}" "{script}" %*\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(relay.shutil, "which", lambda name: str(wrapper))
    started = time.monotonic()

    assert relay._relay_profile_via_cli("fixture-provider", timeout=2) is None

    elapsed = time.monotonic() - started
    assert ready.is_file(), "fixture must spawn the pipe-holding descendant"
    assert elapsed < 6, f"two-second profile deadline took {elapsed:.2f}s"
