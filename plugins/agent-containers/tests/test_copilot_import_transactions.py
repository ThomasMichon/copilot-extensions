"""Hermetic transaction and failure-path coverage for Copilot config imports."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from agent_procutil import no_window_flags

from agent_containers import resolver
from agent_containers.private_state import private_file_lock


def test_import_fsyncs_directory_after_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "config.json"
    observed: list[Path] = []

    def observe_sync(directory: Path) -> None:
        assert resolver.host_copilot_cli_token("fixture-user", config) == "fixture-secret"
        assert not list(tmp_path.glob(".*.tmp"))
        observed.append(directory)

    monkeypatch.setattr(resolver, "fsync_directory", observe_sync)
    resolver.import_copilot_cli_token("fixture-user", "fixture-secret", config)
    assert observed == [tmp_path]


@pytest.mark.skipif(os.name == "nt", reason="POSIX owner permission bits")
def test_import_restores_owner_bits_under_restrictive_umask(tmp_path: Path) -> None:
    # Isolate the process-wide umask from the test runner and its threads.
    script = """
import os, stat, sys
from pathlib import Path
from agent_containers.resolver import host_copilot_cli_token, import_copilot_cli_token
path = Path(sys.argv[1])
os.umask(0o777)
import_copilot_cli_token("fixture-user", "fixture-secret", path)
assert stat.S_IMODE(path.stat().st_mode) == 0o600
assert host_copilot_cli_token("fixture-user", path) == "fixture-secret"
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "config.json")],
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
        capture_output=True, text=True, timeout=30,
        creationflags=no_window_flags(),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_import_applies_acl_compatible_mode_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked: list[Path] = []
    monkeypatch.setattr(resolver, "strict_posix_modes", lambda path: False)

    def check_empty_temp(path: Path, mode: int) -> None:
        assert mode == 0o600
        assert path.stat().st_size == 0
        checked.append(path)

    monkeypatch.setattr(resolver, "enforce_mode", check_empty_temp)
    resolver.import_copilot_cli_token("fixture-user", "fixture-secret", tmp_path / "config.json")
    assert len(checked) == 1


@pytest.mark.parametrize("failure", ["fsync", "replace"])
def test_import_cleans_its_temp_after_publication_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    config = tmp_path / "config.json"
    original = '{"firstLaunchAt": "fixture"}'
    config.write_text(original, encoding="utf-8")

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("fixture publication failure")

    if failure == "fsync":
        monkeypatch.setattr(resolver.os, "fsync", fail)
    else:
        monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="fixture publication failure"):
        resolver.import_copilot_cli_token("fixture-user", "fixture-secret", config)

    assert config.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob("*.tmp"))
    assert not list(tmp_path.glob(".*.tmp"))
    with private_file_lock(tmp_path / ".config.json.copilot-login.lock", timeout=0):
        pass


def test_import_refuses_unreadable_existing_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "config.json"
    original = '{"firstLaunchAt": "fixture"}'
    config.write_text(original, encoding="utf-8")
    read_text = Path.read_text

    def deny_read(path: Path, *args: object, **kwargs: object) -> str:
        if path == config:
            raise PermissionError("fixture unreadable config")
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", deny_read)
    with pytest.raises(PermissionError):
        resolver.import_copilot_cli_token("fixture-user", "fixture-secret", config)
    assert read_text(config, encoding="utf-8") == original


_IMPORT_WORKER = """
import sys, time
from pathlib import Path
from agent_containers.resolver import import_copilot_cli_token

config, markers, account = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
read_text = Path.read_text
def observe_read(path, *args, **kwargs):
    value = read_text(path, *args, **kwargs)
    if path == config:
        (markers / (account + "-read")).touch()
        if account == "first":
            deadline = time.monotonic() + 15
            while not (markers / "release").exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError("fixture release timeout")
                time.sleep(0.01)
    return value
Path.read_text = observe_read
(markers / (account + "-entered")).touch()
import_copilot_cli_token(account, "fixture-" + account, config)
"""


def _wait_for(path: Path) -> None:
    deadline = time.monotonic() + 10
    while not path.exists():
        if time.monotonic() >= deadline:
            pytest.fail("Concurrent import worker did not reach its fixture barrier")
        time.sleep(0.01)


def test_concurrent_imports_preserve_both_accounts(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_text('{"unrelated": "preserved"}', encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    workers: list[subprocess.Popen[str]] = []
    try:
        for account in ("first", "second"):
            workers.append(subprocess.Popen(
                [sys.executable, "-c", _IMPORT_WORKER, str(config), str(tmp_path), account],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                creationflags=no_window_flags(),
            ))
            _wait_for(tmp_path / f"{account}-entered")
            if account == "first":
                _wait_for(tmp_path / "first-read")
        # The first process holds its read/merge/write transaction open.
        # The second must not read that stale snapshot while the lock is held.
        time.sleep(0.3)
        assert not (tmp_path / "second-read").exists()
    finally:
        (tmp_path / "release").touch()
        for worker in workers:
            stdout, stderr = worker.communicate(timeout=25)
            assert worker.returncode == 0, stderr
            assert stdout == ""

    data = json.loads(config.read_text(encoding="utf-8"))
    assert data["unrelated"] == "preserved"
    assert set(data["authTokens"]) == {
        "https://github.com:first", "https://github.com:second",
    }


def test_config_import_lock_has_bounded_contention(tmp_path: Path) -> None:
    path = tmp_path / "import.lock"
    with private_file_lock(path):
        with pytest.raises(RuntimeError, match="Timed out acquiring"):
            with private_file_lock(path, timeout=0):
                pytest.fail("A competing lock was admitted")
    with private_file_lock(path, timeout=0):
        pass
