"""Crash-safe coordination recovery never declares an interrupted update successful."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
SCRIPT = LIB / "installation_context.py"
ARGS = {"kind": "command-binding-publication", "marketplace_id": "legacy",
        "plugin_id": "agent-example"}


@pytest.fixture
def module(monkeypatch):
    spec = importlib.util.spec_from_file_location("recovery_installation_context", SCRIPT)
    result = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, result)
    spec.loader.exec_module(result)
    return result


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
    refused = _cli("lock-repair", path, "--expected-evidence", "changed")
    assert refused.returncode != 0 and orphan.owner_path.read_bytes() == original
    repaired = _cli("lock-repair", path, "--expected-evidence", snapshot["expectedEvidence"])
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
                                        expected_evidence=inspection["expectedEvidence"])
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
                                        expected_evidence=inspection["expectedEvidence"])
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
    with pytest.raises(module.InstallationContextError, match="manual recovery"):
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


def test_concurrent_repairs_cannot_quarantine_a_replacement(module, tmp_path):
    lock = _orphan(module, tmp_path / "publication.lock")
    inspected = module.inspect_installation_lock(lock.path, **ARGS)
    command = [
        sys._base_executable, str(SCRIPT), "lock-repair", "--lock-path", str(lock.path),
        "--kind", ARGS["kind"], "--marketplace-id", ARGS["marketplace_id"],
        "--plugin-id", ARGS["plugin_id"], "--expected-evidence", inspected["expectedEvidence"],
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
    with pytest.warns(RuntimeWarning, match="Recovered stale"):
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
        with pytest.warns(RuntimeWarning, match="Recovered stale"):
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
