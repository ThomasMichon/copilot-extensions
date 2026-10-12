"""Native process tests for the dependency-free construction ownership seam."""

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "versioned_runtime.py"
pytestmark = pytest.mark.guard
spec = importlib.util.spec_from_file_location("construction_runtime", SOURCE)
vr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vr)


def child(root, body):
    script = (
        "import importlib.util, pathlib, time\n"
        f"s=importlib.util.spec_from_file_location('vr', {str(SOURCE)!r})\n"
        "vr=importlib.util.module_from_spec(s); s.loader.exec_module(vr)\n"
        f"root=pathlib.Path({str(root)!r})\n" + body
    )
    return subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


@pytest.fixture
def owner(tmp_path):
    process = child(
        tmp_path,
        "with vr.construction_lease(root, '1.0'):\n"
        " print('ready', flush=True)\n"
        " input()\n",
    )
    try:
        # communicate is bounded everywhere; this initial readiness read is
        # guarded by the owning pytest runner's per-test timeout.
        assert process.stdout.readline().strip() == "ready"
        yield process
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


def test_competing_process_times_out_without_unlinking_lock(tmp_path, owner):
    lock = next((tmp_path / ".construction-leases").glob("*.lock"))
    identity = lock.stat().st_ino
    contender = child(
        tmp_path,
        "try:\n"
        " with vr.construction_lease(root, '1.0', timeout=0.1): pass\n"
        "except vr.ConstructionTimeout:\n"
        " print('timed-out')\n"
        "else: raise AssertionError('second writer admitted')\n",
    )
    stdout, stderr = contender.communicate(timeout=10)
    assert contender.returncode == 0, stderr
    assert stdout.strip() == "timed-out"
    assert lock.stat().st_ino == identity


def test_owner_death_releases_surviving_file(tmp_path, owner):
    lock = next((tmp_path / ".construction-leases").glob("*.lock"))
    identity = lock.stat().st_ino
    owner.kill()
    owner.communicate(timeout=10)
    with vr.construction_lease(tmp_path, "1.0", timeout=0.5):
        assert lock.stat().st_ino == identity


def test_exception_releases_ownership(tmp_path):
    with pytest.raises(RuntimeError):
        with vr.construction_lease(tmp_path, "1.0"):
            raise RuntimeError("build failed")
    with vr.construction_lease(tmp_path, "1.0", timeout=0):
        pass


def test_versions_and_installations_are_independent(tmp_path, owner):
    with vr.construction_lease(tmp_path, "2.0", timeout=0):
        pass
    with vr.construction_lease(tmp_path / "other-installation", "1.0", timeout=0):
        pass


def test_waiter_revalidates_winner_and_refuses_different_content(tmp_path, owner):
    contender = child(
        tmp_path,
        "print('waiting', flush=True)\n"
        "with vr.construction_lease(root, '1.0', timeout=5):\n"
        " print(vr.check_admission(root, '1.0', payload_hash='same'))\n"
        " print(vr.check_admission(root, '1.0', payload_hash='different'))\n",
    )
    try:
        assert contender.stdout.readline().strip() == "waiting"
        time.sleep(0.15)
        assert contender.poll() is None
        vr.mark_complete(tmp_path, "1.0", payload_hash="same")
        owner.communicate(input="\n", timeout=10)
        stdout, stderr = contender.communicate(timeout=10)
        assert contender.returncode == 0, stderr
        assert stdout.splitlines() == ["reuse", "content-conflict"]
    finally:
        if contender.poll() is None:
            contender.kill()
        contender.communicate(timeout=10)


@pytest.mark.parametrize("kwargs", [
    {"timeout": -1}, {"timeout": float("nan")}, {"timeout": float("inf")},
    {"poll_interval": 0}, {"poll_interval": float("nan")},
])
def test_invalid_budgets_refuse_before_creating_state(tmp_path, kwargs):
    with pytest.raises(ValueError):
        with vr.construction_lease(tmp_path, "1.0", **kwargs):
            pass
    assert not (tmp_path / ".construction-leases").exists()


@pytest.mark.parametrize("version", ["../escape", ".", "..", "", "name.", "a/b", "a\\b"])
def test_invalid_identity_refuses_before_creating_state(tmp_path, version):
    with pytest.raises(ValueError):
        with vr.construction_lease(tmp_path, version):
            pass
    assert not (tmp_path / ".construction-leases").exists()


def test_storage_failure_is_not_contention(tmp_path):
    root = tmp_path / "not-a-directory"
    root.write_text("occupied")
    with pytest.raises(OSError) as error:
        with vr.construction_lease(root, "1.0", timeout=0):
            pass
    assert not isinstance(error.value, vr.ConstructionTimeout)


@pytest.mark.parametrize("mismatch_check", [1, 2])
def test_lock_identity_mismatch_refuses_and_releases(tmp_path, monkeypatch, mismatch_check):
    with vr.construction_lease(tmp_path, "1.0"):
        pass
    lock = next((tmp_path / ".construction-leases").glob("*.lock"))
    alternate = tmp_path / "different-file"
    alternate.write_text("different identity")
    alternate_stat = alternate.stat()
    assert alternate_stat.st_ino != lock.stat().st_ino
    original_lstat = vr.os.lstat
    checks = 0

    def changed_identity(path, *args, **kwargs):
        nonlocal checks
        if Path(path) == lock:
            checks += 1
            if checks == mismatch_check:
                return alternate_stat
        return original_lstat(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(vr.os, "lstat", changed_identity)
        with pytest.raises(OSError, match="construction lease path") as error:
            with vr.construction_lease(tmp_path, "1.0", timeout=0):
                pytest.fail("unsafe lock identity admitted construction")
        assert not isinstance(error.value, vr.ConstructionTimeout)
    assert checks == mismatch_check
    with vr.construction_lease(tmp_path, "1.0", timeout=0):
        pass


def test_timeout_budget_is_bounded(tmp_path, owner):
    start = time.monotonic()
    with pytest.raises(vr.ConstructionTimeout):
        with vr.construction_lease(tmp_path, "1.0", timeout=0.1):
            pass
    assert 0.08 <= time.monotonic() - start < 2


def test_keyboard_interrupt_releases_ownership(tmp_path):
    with pytest.raises(KeyboardInterrupt):
        with vr.construction_lease(tmp_path, "1.0"):
            raise KeyboardInterrupt
    with vr.construction_lease(tmp_path, "1.0", timeout=0):
        pass


def test_case_aliases_share_ownership_on_every_platform(tmp_path):
    with vr.construction_lease(tmp_path, "VERSION"):
        with pytest.raises(vr.ConstructionTimeout):
            with vr.construction_lease(tmp_path, "version", timeout=0):
                pass


def test_identical_first_builders_construct_once(tmp_path):
    processes = []
    try:
        for _ in range(4):
            processes.append(child(
                tmp_path,
                "with vr.construction_lease(root, '1.0', timeout=5):\n"
                " decision=vr.check_admission(root, '1.0', payload_hash='same')\n"
                " if decision == 'construct':\n"
                "  with (root / 'builds').open('a') as f: f.write('build\\n')\n"
                "  time.sleep(0.15)\n"
                "  vr.mark_complete(root, '1.0', payload_hash='same')\n"
                " print(decision)\n",
            ))
        decisions = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=10)
            assert process.returncode == 0, stderr
            decisions.append(stdout.strip())
        assert sorted(decisions) == ["construct", "reuse", "reuse", "reuse"]
        assert (tmp_path / "builds").read_text() == "build\n"
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
