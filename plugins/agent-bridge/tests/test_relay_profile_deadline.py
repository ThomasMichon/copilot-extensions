"""Native deadline coverage for a provider wrapper retaining child pipes."""

import os
import subprocess
import sys
import time

import pytest

from agent_bridge import agent_registry_relay as relay
from agent_bridge import provider_process, singleton_anchor
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


def test_captured_provider_preserves_explicit_child_environment():
    result = run_captured(
        [sys.executable, "-c", "import os; print(os.environ['REGISTRY_FIXTURE'])"],
        timeout=10,
        env={**os.environ, "REGISTRY_FIXTURE": "selected"},
    )
    assert result.stdout.strip() == "selected"


@pytest.mark.parametrize("returncode,stdout,expected", [
    (0, '{"repos":[{"name":"solo","paths":{}}]}', ["solo"]),
    (23, '{"repos":[{"name":"solo","paths":{}}]}', []),
    (0, "not json", []),
])
def test_singleton_query_keeps_output_and_scrubbed_environment(
    monkeypatch, returncode, stdout, expected,
):
    calls = []
    monkeypatch.setattr(singleton_anchor, "_agent_worktrees_bin", lambda: "fixture-registry")
    for key in singleton_anchor._SCRUBBED_ENV_KEYS:
        monkeypatch.setenv(key, "inherited-runtime")
    monkeypatch.setenv("REGISTRY_FIXTURE", "selected")

    def capture(argv, timeout, *, env):
        calls.append((argv, timeout, env))
        return subprocess.CompletedProcess(argv, returncode, stdout, "")

    monkeypatch.setattr(singleton_anchor, "run_captured", capture)
    assert [repo.name for repo in singleton_anchor.list_singleton_repos()] == expected
    argv, timeout, env = calls[0]
    assert argv == ["fixture-registry", "repos", "list", "--class", "singleton", "--json"]
    assert timeout == singleton_anchor._QUERY_TIMEOUT_S
    assert env["REGISTRY_FIXTURE"] == "selected"
    assert not set(singleton_anchor._SCRUBBED_ENV_KEYS) & env.keys()


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


@pytest.mark.skipif(os.name != "nt", reason="Windows registry shim pipe-drain regression")
def test_singleton_query_timeout_does_not_wait_for_wrapper_descendants(tmp_path, monkeypatch, caplog):
    script = tmp_path / "registry.py"
    ready = tmp_path / "spawned"
    script.write_text(
        "import pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(8)'], creationflags=subprocess.CREATE_NO_WINDOW)\n"
        f"pathlib.Path({str(ready)!r}).write_text(str(child.pid))\n"
        "time.sleep(8)\n",
        encoding="utf-8",
    )
    wrapper = tmp_path / "registry.cmd"
    wrapper.write_text(
        f'@echo off\n"{sys.executable}" "{script}" %*\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(singleton_anchor, "_agent_worktrees_bin", lambda: str(wrapper))
    monkeypatch.setattr(singleton_anchor, "_QUERY_TIMEOUT_S", 2)
    started = time.monotonic()

    assert singleton_anchor.list_singleton_repos() == []

    elapsed = time.monotonic() - started
    assert ready.is_file(), "fixture must spawn the pipe-holding descendant"
    assert elapsed < 6, f"two-second registry deadline took {elapsed:.2f}s"
    assert "singleton repo query failed" in caplog.text
