"""Mixed-version admission writers must reach a proven quiescent boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

import pytest
from agent_procutil import no_window_flags

from agent_containers import lease
from agent_containers.lease_lock import PROTOCOL


@pytest.fixture
def state(monkeypatch, tmp_path):
    monkeypatch.setattr(lease, "ensure_state_dir", lambda: None)
    for name, filename in (
        ("_LOCK_FILE", "leases.lock"),
        ("_DEPLOY_HOLDS_FILE", "holds.json"),
        ("_SESSION_ADMISSIONS_FILE", "admissions.json"),
    ):
        monkeypatch.setattr(lease, name, tmp_path / filename)
    return tmp_path


def test_regular_legacy_lock_is_never_stolen_even_after_age_threshold(state):
    lease._LOCK_FILE.write_text("legacy-owner-token", encoding="ascii")
    os.utime(lease._LOCK_FILE, (1, 1))
    with pytest.raises(lease.ProviderAdmissionError, match="has not quiesced"):
        with lease._lease_lock(timeout=0.05, poll=0.01):
            pytest.fail("legacy writer's unowned inode was replaced")
    assert lease._LOCK_FILE.read_text(encoding="ascii") == "legacy-owner-token"


def test_legacy_release_between_mkdir_and_lstat_retries_cutover(state, monkeypatch):
    lease._LOCK_FILE.write_text("legacy-owner-token", encoding="ascii")
    original = Path.lstat
    released = []

    def lstat(path):
        if path == lease._LOCK_FILE and not released:
            path.unlink()
            released.append(True)
            raise FileNotFoundError("legacy owner released after mkdir")
        return original(path)

    monkeypatch.setattr(Path, "lstat", lstat)
    with lease._lease_lock(timeout=0.1):
        assert released == [True]
        assert lease._LOCK_FILE.is_dir()
        assert (lease._LOCK_FILE / "kernel.lock").is_file()


def test_repeated_legacy_release_races_remain_bounded(state, monkeypatch):
    original_mkdir, original_lstat = Path.mkdir, Path.lstat

    def mkdir(path, *args, **kwargs):
        if path == lease._LOCK_FILE:
            raise FileExistsError("legacy writer appeared")
        return original_mkdir(path, *args, **kwargs)

    def lstat(path):
        if path == lease._LOCK_FILE:
            raise FileNotFoundError("legacy writer released")
        return original_lstat(path)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(lease.ProviderAdmissionError, match="cutover deadline"):
        with lease._lease_lock(timeout=0.05):
            pytest.fail("unbounded cutover retry entered the critical section")


@pytest.mark.parametrize("kind", ["admission", "hold"])
def test_live_legacy_writers_refuse_cutover_even_with_expired_heartbeats(state, kind):
    fields = {
        "container": "pool-1", "token": "legacy", "pid": os.getpid(),
        "host": lease._this_host(), "environment": lease._this_environment(),
        "acquired_at": 1, "heartbeat_at": 1,
    }
    if kind == "hold":
        fields.update(operation="remove", expires_at=2)
        path = lease._DEPLOY_HOLDS_FILE
    else:
        path = lease._SESSION_ADMISSIONS_FILE
    path.write_text(json.dumps({"legacy": fields}), encoding="utf-8")
    with pytest.raises(lease.ProviderAdmissionError, match="writers must quiesce"):
        with lease._lease_lock(timeout=0.1):
            pytest.fail("a live legacy heartbeat writer was ignored")
    assert lease.clear_stale_provider_records() == {
        "deploy_holds": 0, "session_admissions": 0,
    }
    assert json.loads(path.read_text()) == {"legacy": fields}


def test_caller_failures_are_not_reclassified_as_admission_contention(state):
    with pytest.raises(RuntimeError, match="caller failure") as failure:
        with lease._lease_lock():
            raise RuntimeError("caller failure")
    assert type(failure.value) is RuntimeError


@pytest.mark.parametrize("kind", ["admission", "hold"])
def test_foreign_legacy_writer_remains_protected_after_blocked_heartbeat(state, monkeypatch, kind):
    now = 1000.0
    monkeypatch.setattr(lease.time, "time", lambda: now)
    fields = {
        "container": "pool-1", "token": "legacy", "pid": os.getpid(),
        "host": lease._this_host(), "environment": "foreign-environment",
        "acquired_at": now, "heartbeat_at": now,
    }
    if kind == "hold":
        fields.update(operation="remove", expires_at=now + lease.DEPLOY_HOLD_TTL)
        path, ttl = lease._DEPLOY_HOLDS_FILE, lease.DEPLOY_HOLD_TTL
    else:
        path, ttl = lease._SESSION_ADMISSIONS_FILE, lease.SESSION_ADMISSION_TTL
    path.write_text(json.dumps({"legacy": fields}), encoding="utf-8")
    original = path.read_bytes()
    for elapsed in (0, ttl + 1):
        now = 1000.0 + elapsed
        with pytest.raises(lease.ProviderAdmissionError, match="writers must quiesce"):
            with lease._lease_lock(timeout=0.1):
                pytest.fail("foreign legacy writer was admitted after blocking its heartbeat")
        assert lease._LOCK_FILE.is_dir()
        assert lease.clear_stale_provider_records() == {
            "deploy_holds": 0, "session_admissions": 0,
        }
        assert path.read_bytes() == original
    monkeypatch.setattr(lease, "_this_environment", lambda: fields["environment"])
    with pytest.raises(lease.ProviderAdmissionError, match="writers must quiesce"):
        with lease._lease_lock(timeout=0.1):
            pytest.fail("the owner environment ignored its live legacy PID")
    monkeypatch.setattr(lease, "_pid_alive", lambda pid: False)
    with lease._lease_lock(timeout=0.1):
        assert lease._LOCK_FILE.is_dir()


@pytest.mark.parametrize("kind", ["admission", "hold"])
def test_each_environment_can_clear_only_its_verified_dead_legacy_writer(state, monkeypatch, kind):
    monkeypatch.setattr(lease, "_this_host", lambda: "shared-host")
    environment = "windows"
    monkeypatch.setattr(lease, "_this_environment", lambda: environment)
    monkeypatch.setattr(lease, "_pid_alive", lambda pid: False)
    fields = {
        "container": "pool-1", "token": "legacy", "pid": 123,
        "host": "shared-host", "acquired_at": 1, "heartbeat_at": 1,
    }
    if kind == "hold":
        fields.update(operation="remove", expires_at=2)
        path, counter = lease._DEPLOY_HOLDS_FILE, "deploy_holds"
    else:
        path, counter = lease._SESSION_ADMISSIONS_FILE, "session_admissions"
    path.write_text(json.dumps({
        owner: {**fields, "environment": owner} for owner in ("windows", "wsl")
    }), encoding="utf-8")
    for environment, remaining in (("windows", {"wsl"}), ("wsl", set())):
        if environment == "windows":
            with pytest.raises(lease.ProviderAdmissionError, match="writers must quiesce"):
                with lease._lease_lock(timeout=0.1):
                    pytest.fail("normal admission bypassed a foreign legacy writer")
        assert lease.clear_stale_provider_records()[counter] == 1
        assert set(json.loads(path.read_text())) == remaining
        if remaining:
            with pytest.raises(lease.ProviderAdmissionError, match="writers must quiesce"):
                with lease._lease_lock(timeout=0.1):
                    pytest.fail("local cleanup opened admission past a foreign legacy writer")
    with lease._lease_lock(timeout=0.1):
        assert lease._LOCK_FILE.is_dir()


def test_dead_legacy_uncertain_hold_remains_until_expiry(state, monkeypatch):
    now = 1000.0
    monkeypatch.setattr(lease.time, "time", lambda: now)
    monkeypatch.setattr(lease, "_pid_alive", lambda pid: False)
    fields = {
        "container": "pool-1", "token": "legacy", "pid": 123,
        "host": lease._this_host(), "environment": lease._this_environment(),
        "operation": "remove", "acquired_at": 1, "heartbeat_at": 1,
        "expires_at": now + 20, "uncertain": True,
    }
    path = lease._DEPLOY_HOLDS_FILE
    path.write_text(json.dumps({"legacy": fields}), encoding="utf-8")
    original = path.read_bytes()
    assert lease.clear_stale_provider_records() == {
        "deploy_holds": 0, "session_admissions": 0,
    }
    assert path.read_bytes() == original
    now = fields["expires_at"]
    assert lease.clear_stale_provider_records() == {
        "deploy_holds": 1, "session_admissions": 0,
    }
    assert json.loads(path.read_text()) == {}


def test_mixed_version_process_cannot_enter_or_unlink_kernel_namespace(state):
    ready, attempt, result = (state / name for name in ("ready", "attempt", "result"))
    script = r"""
import errno, os, sys, time, uuid
from pathlib import Path
lock,ready,attempt,result=map(Path,sys.argv[1:])
fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
token=uuid.uuid4().hex
os.write(fd,token.encode('ascii'))
os.close(fd)
ready.write_text('held')
deadline=time.monotonic()+15
while not attempt.exists():
    if time.monotonic()>deadline: raise RuntimeError('fixture signal timeout')
    time.sleep(.01)
if lock.read_text()==token: lock.unlink()
ready.write_text('released')
while attempt.read_text()!='contend':
    if time.monotonic()>deadline: raise RuntimeError('fixture signal timeout')
    time.sleep(.01)
try:
    fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
except OSError as error:
    if error.errno not in (errno.EEXIST, errno.EACCES, errno.EPERM, errno.EISDIR):
        raise
    try:
        if error.errno==errno.EEXIST and time.time()-lock.stat().st_mtime>.3: lock.unlink()
    except OSError:
        pass
    result.write_text('blocked')
else:
    os.close(fd)
    result.write_text('entered')
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(lease._LOCK_FILE), str(ready), str(attempt), str(result)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=no_window_flags(),
    )

    def wait(path, expected):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if path.exists() and path.read_text() == expected:
                return
            if child.poll() is not None:
                stdout, stderr = child.communicate(timeout=5)
                pytest.fail(f"legacy fixture exited before its boundary: {stdout} {stderr}")
            time.sleep(0.01)
        pytest.fail("legacy fixture did not reach its bounded boundary")

    try:
        wait(ready, "held")
        with pytest.raises(lease.ProviderAdmissionError, match="has not quiesced"):
            with lease._lease_lock(timeout=0.05, poll=0.01):
                pytest.fail("new writer entered a legacy critical section")
        admission = asdict(lease.SessionAdmission(
            container="pool-1", token="legacy", pid=child.pid,
            host=lease._this_host(), environment=lease._this_environment(),
            acquired_at=time.time(), heartbeat_at=time.time(),
        ))
        admission.pop("lock_protocol")
        lease._SESSION_ADMISSIONS_FILE.write_text(json.dumps({"legacy": admission}))
        attempt.write_text("release")
        wait(ready, "released")
        with pytest.raises(lease.ProviderAdmissionError, match="writers must quiesce"):
            with lease._lease_lock(timeout=0.1):
                pytest.fail("new writer admitted a live legacy wrapper")
        assert lease._LOCK_FILE.is_dir()
        os.utime(lease._LOCK_FILE, (1, 1))
        attempt.write_text("contend")
        wait(result, "blocked")
        stdout, stderr = child.communicate(timeout=5)
        assert child.returncode == 0, (stdout, stderr)
        with lease._lease_lock(timeout=0.1):
            assert lease._LOCK_FILE.is_dir()
            assert (lease._LOCK_FILE / "kernel.lock").is_file()
        assert lease.SessionAdmission(**{**admission, "lock_protocol": PROTOCOL}).lock_protocol == PROTOCOL
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)
