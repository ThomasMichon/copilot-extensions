"""Crash-safe coordination recovery never declares an interrupted update successful."""
from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

LIB = Path(__file__).resolve().parents[1]
SCRIPT = LIB / "installation_context.py"
ARGS = {"kind": "command-binding-publication", "marketplace_id": "legacy",
        "plugin_id": "agent-example"}
ASSESSMENT = "Test-owned fixture; recorded owner and all test children are settled."


@pytest.fixture
def module(monkeypatch):
    spec = importlib.util.spec_from_file_location("recovery_installation_context", SCRIPT)
    result = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, result)
    spec.loader.exec_module(result)
    return result


@pytest.mark.parametrize(("error", "live"), [(5, True), (8, True), (1450, True), (87, False)])
def test_native_open_failure_is_not_implicitly_death(module, error, live):
    assert module._lock_openprocess_failure_means_live(error) is live


@pytest.mark.skipif(os.name != "nt", reason="native Windows OpenProcess dispatch")
@pytest.mark.parametrize(("error", "live"), [(8, True), (1450, True), (87, False)])
def test_windows_pid_probe_classifies_open_failure(module, monkeypatch, error, live):
    import ctypes

    kernel = SimpleNamespace(
        OpenProcess=lambda *_args: 0,
        WaitForSingleObject=lambda *_args: 258,
        CloseHandle=lambda *_args: True,
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: error)
    assert module._pid_is_live(1234) is live


def _orphan(module, path):
    lock = module._DirectoryLock(path, **ARGS)
    lock.acquire()
    owner = lock._owner()
    owner["pid"] = 2_000_000_000
    owner.pop("processIdentity", None)
    module._atomic_write_json(lock.owner_path, owner)
    return lock


def _run(*args):
    return subprocess.run(
        [sys._base_executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def _cli(action, path, *extra):
    return _run(action, "--lock-path", path, "--kind", ARGS["kind"],
                "--marketplace-id", ARGS["marketplace_id"], "--plugin-id", ARGS["plugin_id"],
                *extra)


def test_standalone_inspection_and_evidence_bound_repair(module, tmp_path):
    path = tmp_path / "publication.lock"
    orphan = _orphan(module, path)
    original = orphan.owner_path.read_bytes()
    inspected = _cli("lock-inspect", path)
    assert inspected.returncode == 0, inspected.stderr
    snapshot = json.loads(inspected.stdout)
    assert snapshot["state"] == "stale"
    assert "token" not in inspected.stdout and "processIdentity" not in inspected.stdout
    refused = _cli("lock-repair", path, "--expected-evidence", "changed",
                   "--inactive-owner-evidence", ASSESSMENT)
    assert refused.returncode != 0 and orphan.owner_path.read_bytes() == original
    repaired = _cli("lock-repair", path, "--expected-evidence", snapshot["expectedEvidence"],
                    "--inactive-owner-evidence", ASSESSMENT)
    assert repaired.returncode == 0, repaired.stderr
    receipt = json.loads(repaired.stdout)
    assert receipt["state"] == "quarantined"
    assert Path(receipt["backupPath"], "owner.json").read_bytes() == original
    assert not path.exists()
    assert "coordination-only" in receipt["scope"]
    with module._DirectoryLock(path, **ARGS) as next_owner:
        assert next_owner.acquired


@pytest.mark.parametrize("state", ["live", "legacy", "foreign", "unknown"])
def test_repair_preserves_live_foreign_and_unknown_owners(module, tmp_path, monkeypatch, state):
    path = tmp_path / "publication.lock"
    lock = module._DirectoryLock(path, **ARGS)
    lock.acquire()
    owner = lock._owner()
    if state == "legacy":
        owner.pop("processIdentity", None)
    elif state == "foreign":
        owner["host"] = "another-host"
    elif state == "unknown":
        monkeypatch.setattr(module, "_runtime_publication_process_identity", lambda _pid: None)
    module._atomic_write_json(lock.owner_path, owner)
    original = lock.owner_path.read_bytes()
    inspection = module.inspect_installation_lock(path, **ARGS)
    assert inspection["state"] != "stale"
    with pytest.raises(module.InstallationContextError, match="not provably stale"):
        module.repair_installation_lock(path, **ARGS,
                                        expected_evidence=inspection["expectedEvidence"],
                                        inactive_owner_evidence=ASSESSMENT)
    assert lock.owner_path.read_bytes() == original
    lock.release()


def test_changed_receipt_refuses_repair(module, tmp_path):
    lock = _orphan(module, tmp_path / "publication.lock")
    inspection = module.inspect_installation_lock(lock.path, **ARGS)
    owner = lock._owner()
    owner["token"] = "replacement"
    module._atomic_write_json(lock.owner_path, owner)
    with pytest.raises(module.InstallationContextError, match="changed since inspection"):
        module.repair_installation_lock(lock.path, **ARGS,
                                        expected_evidence=inspection["expectedEvidence"],
                                        inactive_owner_evidence=ASSESSMENT)
    assert lock._owner()["token"] == "replacement"


def test_dead_owner_with_corrupt_optional_birth_is_recoverable(module, tmp_path):
    lock = _orphan(module, tmp_path / "publication.lock")
    owner = lock._owner()
    owner["processIdentity"] = "incomplete-or-unsupported"
    module._atomic_write_json(lock.owner_path, owner)
    inspected = module.inspect_installation_lock(lock.path, **ARGS)
    assert inspected["state"] == "stale"
    result = module.repair_installation_lock(
        lock.path, **ARGS, expected_evidence=inspected["expectedEvidence"],
        inactive_owner_evidence=ASSESSMENT,
    )
    assert result["state"] == "quarantined"
    assert json.loads(Path(result["backupPath"], "owner.json").read_text()) == owner


@pytest.mark.parametrize("contents", [None, "{", '{"host":"another-host"}'])
def test_manual_bypass_requires_evidence_and_cannot_override_foreign_owner(module, tmp_path, contents):
    path = tmp_path / "publication.lock"
    path.mkdir()
    if contents is not None:
        (path / "owner.json").write_text(contents, encoding="utf-8")
    if contents and "another-host" in contents:
        with pytest.raises(module.InstallationContextError, match="foreign"):
            module.inspect_installation_lock(path, **ARGS, allow_incomplete=True)
        return
    inspection = module.inspect_installation_lock(path, **ARGS, allow_incomplete=True)
    assert inspection["state"] == "manual-assessment-required"
    with pytest.raises(module.InstallationContextError, match="inactivity assessment"):
        module.repair_installation_lock(
            path, **ARGS, expected_evidence=inspection["expectedEvidence"],
        )
    result = module.repair_installation_lock(
        path, **ARGS, expected_evidence=inspection["expectedEvidence"],
        inactive_owner_evidence="Test-owned residue; no writer was started.",
    )
    assert result["state"] == "quarantined"
    assert "no writer" in result["inactiveOwnerEvidence"]
    backup = Path(result["backupPath"])
    assert backup.is_dir()
    assert not path.exists()
    if contents is not None:
        assert (backup / "owner.json").read_text() == contents


def test_manual_evidence_does_not_override_a_live_owner(module, tmp_path):
    lock = module._DirectoryLock(tmp_path / "publication.lock", **ARGS)
    lock.acquire()
    try:
        inspection = module.inspect_installation_lock(lock.path, **ARGS, allow_incomplete=True)
        with pytest.raises(module.InstallationContextError, match="not provably stale"):
            module.repair_installation_lock(
                lock.path, **ARGS, expected_evidence=inspection["expectedEvidence"],
                inactive_owner_evidence="This assertion cannot authorize stealing a live lock.",
            )
        lock.assert_owned()
    finally:
        lock.release()


@pytest.mark.parametrize("token", ["", None, 123])
@pytest.mark.parametrize("state", ["stale", "live", "foreign", "wrong-scope"])
def test_manual_recovery_of_invalid_token_preserves_owner_guards(
    module, tmp_path, token, state,
):
    lock = _orphan(module, tmp_path / "publication.lock")
    owner = lock._owner()
    owner["token"] = token
    if state == "live":
        owner["pid"] = os.getpid()
    elif state == "foreign":
        owner["host"] = "another-host"
    elif state == "wrong-scope":
        owner["marketplaceId"] = "another-marketplace"
    module._atomic_write_json(lock.owner_path, owner)
    original = lock.owner_path.read_bytes()
    with pytest.raises(module.InstallationContextError, match="incomplete"):
        module.inspect_installation_lock(lock.path, **ARGS)
    if state != "stale":
        with pytest.raises(module.InstallationContextError, match="live owner|foreign|scope"):
            module.inspect_installation_lock(lock.path, **ARGS, allow_incomplete=True)
        assert lock.owner_path.read_bytes() == original
        return
    inspection = module.inspect_installation_lock(lock.path, **ARGS, allow_incomplete=True)
    assert inspection["state"] == "manual-assessment-required"
    with pytest.raises(module.InstallationContextError, match="inactivity assessment"):
        module.repair_installation_lock(
            lock.path, **ARGS, expected_evidence=inspection["expectedEvidence"],
        )
    result = module.repair_installation_lock(
        lock.path, **ARGS, expected_evidence=inspection["expectedEvidence"],
        inactive_owner_evidence=ASSESSMENT,
    )
    assert result["state"] == "quarantined"
    assert Path(result["backupPath"], "owner.json").read_bytes() == original


def test_changed_directory_identity_refuses_quarantine(module, tmp_path, monkeypatch):
    lock = _orphan(module, tmp_path / "publication.lock")
    expected, _owner = module._lock_snapshot(lock)
    snapshot = module._lock_snapshot
    calls = 0

    def replace_before_move(candidate, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            os.rename(lock.path, tmp_path / "original-preserved")
            _orphan(module, lock.path)
        return snapshot(candidate, **kwargs)

    monkeypatch.setattr(module, "_lock_snapshot", replace_before_move)
    with pytest.raises(module.InstallationContextError, match="changed before quarantine"):
        module._quarantine_stale_lock(lock, expected)
    assert (tmp_path / "original-preserved" / "owner.json").is_file()
    assert lock.owner_path.is_file()
    assert not list(tmp_path.glob("publication.lock.recovery-*/lock"))


def test_receipt_write_failure_after_move_is_not_success(module, tmp_path, monkeypatch):
    lock = _orphan(module, tmp_path / "publication.lock")
    expected, _owner = module._lock_snapshot(lock)
    write_json = module._atomic_write_json

    def fail_completion(path, document):
        if document.get("state") == "quarantined":
            raise OSError("test-owned interrupted receipt completion")
        write_json(path, document)

    monkeypatch.setattr(module, "_atomic_write_json", fail_completion)
    with pytest.raises(OSError, match="interrupted receipt"):
        module._quarantine_stale_lock(lock, expected)
    assert not lock.path.exists()
    backups = list(tmp_path.glob("publication.lock.recovery-*"))
    assert len(backups) == 1
    assert (backups[0] / "lock" / "owner.json").is_file()
    assert json.loads((backups[0] / "recovery.json").read_text())["state"] == "prepared"


def test_quarantine_flushes_both_rename_parents(module, tmp_path, monkeypatch):
    lock = _orphan(module, tmp_path / "publication.lock")
    expected, _owner = module._lock_snapshot(lock)
    flushed = []
    monkeypatch.setattr(module, "_sync_lock_directory", flushed.append)
    result = module._quarantine_stale_lock(lock, expected)
    assert flushed == [lock.path.parent, Path(result["backupPath"]).parent, lock.path.parent]


def test_concurrent_repairs_cannot_quarantine_a_replacement(module, tmp_path):
    lock = _orphan(module, tmp_path / "publication.lock")
    inspected = module.inspect_installation_lock(lock.path, **ARGS)
    command = [
        sys._base_executable, str(SCRIPT), "lock-repair", "--lock-path", str(lock.path),
        "--kind", ARGS["kind"], "--marketplace-id", ARGS["marketplace_id"],
        "--plugin-id", ARGS["plugin_id"], "--expected-evidence", inspected["expectedEvidence"],
        "--inactive-owner-evidence", ASSESSMENT,
    ]
    children = []
    try:
        for _ in range(2):
            children.append(subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            ))
        for child in children:
            child.communicate(timeout=20)
        assert sorted(child.returncode for child in children) == [0, 1]
        assert len(list(tmp_path.glob("publication.lock.recovery-*/lock"))) == 1
        with module._DirectoryLock(lock.path, **ARGS) as current:
            current.assert_owned()
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=10)


@pytest.mark.parametrize("failure", ["missing", "malformed", "extra", "link"])
def test_incomplete_and_unsafe_locks_are_not_automatically_stolen(module, tmp_path, failure):
    path = tmp_path / "publication.lock"
    path.mkdir()
    if failure == "malformed":
        (path / "owner.json").write_text("{", encoding="utf-8")
    elif failure == "extra":
        (path / "unexpected").write_text("retain", encoding="utf-8")
    elif failure == "link":
        target = tmp_path / "target.json"
        target.write_text("{}", encoding="utf-8")
        try:
            (path / "owner.json").symlink_to(target)
        except OSError:
            pytest.skip("symlink privilege unavailable")
    with pytest.raises(module.InstallationContextError):
        module.inspect_installation_lock(path, **ARGS)
    assert path.is_dir()


def test_process_identity_detects_pid_reuse_and_prior_boot(module, tmp_path, monkeypatch):
    path = tmp_path / "publication.lock"
    lock = module._DirectoryLock(path, **ARGS)
    lock.acquire()
    owner = lock._owner()
    owner["processIdentity"] = "windows:1"
    module._atomic_write_json(lock.owner_path, owner)
    monkeypatch.setattr(module, "_pid_is_live", lambda _pid: True)
    monkeypatch.setattr(module, "_runtime_publication_process_identity", lambda _pid: "windows:2")
    inspection = module.inspect_installation_lock(path, **ARGS)
    assert inspection["state"] == "stale"
    with pytest.raises(module.InstallationContextError, match="descendants"):
        module._DirectoryLock(path, **ARGS).acquire()
    module.repair_installation_lock(
        path, **ARGS, expected_evidence=inspection["expectedEvidence"],
        inactive_owner_evidence=ASSESSMENT,
    )
    replacement = module._DirectoryLock(path, **ARGS)
    replacement.acquire()
    assert replacement.token != owner["token"]
    replacement.release()


def test_real_child_death_releases_guard_and_recovers_owner(module, tmp_path):
    path = tmp_path / "publication.lock"
    ready = tmp_path / "ready"
    child_code = (
        "import sys,time; from pathlib import Path; "
        f"sys.path.insert(0,{str(LIB)!r}); import installation_context as m; "
        f"lock=m._DirectoryLock(Path({str(path)!r}),**{ARGS!r}); lock.acquire(); "
        f"Path({str(ready)!r}).write_text('ready'); time.sleep(60)"
    )
    child = subprocess.Popen(
        [sys._base_executable, "-c", child_code], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        deadline = time.monotonic() + 20
        while not ready.exists() and time.monotonic() < deadline:
            if child.poll() is not None:
                pytest.fail(child.communicate()[1].decode())
            time.sleep(.02)
        assert ready.exists()
        child.kill()
        child.communicate(timeout=10)
        inspection = module.inspect_installation_lock(path, **ARGS)
        with pytest.raises(module.InstallationContextError, match="descendants"):
            module._DirectoryLock(path, **ARGS).acquire()
        module.repair_installation_lock(
            path, **ARGS, expected_evidence=inspection["expectedEvidence"],
            inactive_owner_evidence=ASSESSMENT,
        )
        replacement = module._DirectoryLock(path, **ARGS)
        replacement.acquire()
        replacement.release()
        backups = list(tmp_path.glob("publication.lock.recovery-*"))
        assert len(backups) == 1
        receipt = json.loads((backups[0] / "recovery.json").read_text())
        assert receipt["state"] == "quarantined"
        assert receipt["inspection"]["pid"] == child.pid
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)


def test_kernel_releases_mutation_guard_after_child_death(module, tmp_path):
    path = tmp_path / "publication.lock"
    ready = tmp_path / "guard-ready"
    code = (
        "import sys,time\nfrom pathlib import Path\n"
        f"sys.path.insert(0,{str(LIB)!r})\nimport installation_context as m\n"
        f"with m._lock_mutation_guard(Path({str(path)!r}),deadline=time.monotonic()+5):\n"
        f" Path({str(ready)!r}).write_text('ready')\n time.sleep(60)\n"
    )
    child = subprocess.Popen(
        [sys._base_executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        deadline = time.monotonic() + 20
        while not ready.exists() and time.monotonic() < deadline:
            if child.poll() is not None:
                pytest.fail(child.communicate()[1].decode())
            time.sleep(.02)
        assert ready.exists()
        with pytest.raises(module.InstallationContextError, match="guard remained busy"):
            with module._lock_mutation_guard(path, deadline=time.monotonic() + .05):
                pytest.fail("two live holders acquired one guard")
        child.kill()
        child.communicate(timeout=10)
        with module._DirectoryLock(path, **ARGS):
            assert path.is_dir()
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)


@pytest.mark.skipif(os.name != "nt", reason="native Windows surviving-child regression")
def test_dead_admitting_parent_does_not_admit_overlapping_installer(module, tmp_path):
    path = tmp_path / "publication.lock"
    ready, heartbeat, stop = (tmp_path / name for name in ("ready", "heartbeat", "stop"))
    child_code = (
        "import time\nfrom pathlib import Path\n"
        f"stop=Path({str(stop)!r})\nheartbeat=Path({str(heartbeat)!r})\n"
        "while not stop.exists():\n heartbeat.write_text(str(time.monotonic()))\n time.sleep(.02)\n"
    )
    parent_code = (
        "import sys,time,json,subprocess\nfrom pathlib import Path\n"
        f"sys.path.insert(0,{str(LIB)!r})\nimport installation_context as m\n"
        f"lock=m._DirectoryLock(Path({str(path)!r}),**{ARGS!r})\nlock.acquire()\n"
        f"child=subprocess.Popen([sys.executable,'-c',{child_code!r}],"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,"
        "creationflags=subprocess.CREATE_NO_WINDOW)\n"
        f"Path({str(ready)!r}).write_text(json.dumps({{'child':child.pid}}))\ntime.sleep(60)\n"
    )
    parent = subprocess.Popen(
        [sys._base_executable, "-c", parent_code], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    child_pid = None
    try:
        deadline = time.monotonic() + 20
        while not (ready.exists() and heartbeat.exists()) and time.monotonic() < deadline:
            if parent.poll() is not None:
                pytest.fail(parent.communicate()[1].decode())
            time.sleep(.02)
        assert ready.exists() and heartbeat.exists()
        child_pid = json.loads(ready.read_text())["child"]
        owner_before = (path / "owner.json").read_bytes()
        parent.kill()
        parent.communicate(timeout=10)
        before = heartbeat.stat().st_mtime_ns
        time.sleep(.2)
        assert module._pid_is_live(child_pid)
        assert heartbeat.stat().st_mtime_ns > before
        with pytest.raises(module.InstallationContextError, match="descendants"):
            module._DirectoryLock(path, **ARGS, timeout_seconds=.5).acquire()
        inspected = module.inspect_installation_lock(path, **ARGS)
        refused = _cli("lock-repair", path, "--expected-evidence", inspected["expectedEvidence"])
        assert refused.returncode != 0
        assert (path / "owner.json").read_bytes() == owner_before
        assert not list(tmp_path.glob("publication.lock.recovery-*"))
        stop.touch()
        deadline = time.monotonic() + 10
        while module._pid_is_live(child_pid) and time.monotonic() < deadline:
            time.sleep(.02)
        assert not module._pid_is_live(child_pid)
        module.repair_installation_lock(
            path, **ARGS, expected_evidence=inspected["expectedEvidence"],
            inactive_owner_evidence="Owned installer child exited after stop; native PID is absent.",
        )
        with module._DirectoryLock(path, **ARGS) as replacement:
            replacement.assert_owned()
    finally:
        stop.touch()
        if parent.poll() is None:
            parent.kill()
        parent.communicate(timeout=10)
        if child_pid is not None and module._pid_is_live(child_pid):
            os.kill(child_pid, signal.SIGTERM)
