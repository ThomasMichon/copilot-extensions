"""Run-local immutable seed publication and cross-process exclusion."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs
from agent_worktrees import tracking
from conftest import _seed_directory

pytestmark = pytest.mark.contract("agent_worktrees.pr_fixture.run_local_seeds")


def _wait_for(path, process):
    import time

    deadline = time.monotonic() + 10
    while not path.exists():
        assert process.poll() is None, "seed process exited before rendezvous"
        assert time.monotonic() < deadline, f"seed process did not publish {path.name}"
        time.sleep(0.01)


class Factory:
    def __init__(self, root):
        self.root = root
        self.calls = 0

    def mktemp(self, key):
        self.calls += 1
        target = self.root / f"{key}-{self.calls}"
        target.mkdir(parents=True)
        return target


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    root = tmp_path / "sandbox"
    root.mkdir()
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_SANDBOX", str(root))
    return root


def test_distinct_group_factories_reuse_only_completed_seed(sandbox, tmp_path):
    first, second = Factory(tmp_path / "group-one"), Factory(tmp_path / "group-two")
    with _seed_directory(first, "example") as (seed, build):
        assert build
        assert not (sandbox / "s" / "example.ready").exists()
        (seed / "data").write_text("immutable")
    with _seed_directory(second, "example") as (again, build):
        assert not build
        assert again == seed
        assert (again / "data").read_text() == "immutable"
    assert first.calls == second.calls == 0
    assert json.loads((sandbox / "s" / "example.ready").read_text()) == {
        "version": 1, "key": "example", "path": str(seed),
    }


@pytest.mark.parametrize("contained,sandbox_present", [("0", True), ("1", False)])
def test_uncontained_calls_keep_independent_session_directories(
    tmp_path, monkeypatch, contained, sandbox_present,
):
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", contained)
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_SANDBOX", raising=False)
    if sandbox_present:
        monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_SANDBOX", str(tmp_path / "unused"))
    factory = Factory(tmp_path)
    with _seed_directory(factory, "example") as (first, build):
        assert build
    with _seed_directory(factory, "example") as (second, build):
        assert build
    assert first != second and factory.calls == 2
    assert not (tmp_path / "unused").exists()


def test_failed_builder_is_never_published_or_silently_rebuilt(sandbox, tmp_path):
    factory = Factory(tmp_path)
    with pytest.raises(RuntimeError, match="builder failed"):
        with _seed_directory(factory, "example") as (seed, build):
            assert build
            (seed / "partial").write_text("not ready")
            raise RuntimeError("builder failed")
    assert not (sandbox / "s" / "example.ready").exists()
    with pytest.raises(RuntimeError, match="incomplete fixture seed"):
        with _seed_directory(factory, "example"):
            pytest.fail("partial seed was consumed")


@pytest.mark.parametrize("readiness", [
    '{"version": 2}', '{"version": 1}', "[]", "null", "{invalid",
    '{"version": true, "key": "example", "path": "$seed"}',
    '{"version": 1.0, "key": "example", "path": "$seed"}',
    '{"version": 1, "key": "other", "path": "$seed"}',
    '{"version": 1, "key": "example", "path": "other"}',
    '{"version": 1, "key": "example", "path": "$seed", "extra": 1}',
])
def test_malformed_readiness_fails_closed(sandbox, tmp_path, readiness):
    factory = Factory(tmp_path)
    with _seed_directory(factory, "example") as (seed, _build):
        pass
    (sandbox / "s" / "example.ready").write_text(
        readiness.replace("$seed", json.dumps(str(seed))[1:-1]),
    )
    with pytest.raises(ValueError):
        with _seed_directory(factory, "example"):
            pytest.fail("invalid readiness was consumed")


@pytest.mark.parametrize("name", ["sandbox", "s", "example.ready", "example.lock", "example"])
def test_seed_cache_rejects_symlink_controls(sandbox, tmp_path, monkeypatch, name):
    original = Path.is_symlink
    rejected = sandbox if name == "sandbox" else (
        sandbox / "s" if name == "s" else sandbox / "s" / name
    )
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == rejected or original(path))
    message = (
        "existing real absolute test sandbox" if name == "sandbox"
        else "incomplete fixture seed" if name == "example" else "symlink"
    )
    with pytest.raises((ValueError, RuntimeError), match=message):
        with _seed_directory(Factory(tmp_path), "example"):
            pytest.fail("redirected cache path was accepted")


@pytest.mark.parametrize("kind", ["relative", "missing", "file"])
def test_contained_seed_requires_existing_absolute_sandbox(
    sandbox, tmp_path, monkeypatch, kind,
):
    invalid = Path("relative") if kind == "relative" else tmp_path / kind
    if kind == "file":
        invalid.write_text("not a directory")
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_SANDBOX", str(invalid))
    with pytest.raises(ValueError, match="existing real absolute test sandbox"):
        with _seed_directory(Factory(tmp_path), "example"):
            pytest.fail("invalid sandbox was accepted")
    assert not (sandbox / "s").exists()


def test_missing_published_directory_is_not_consumed(sandbox, tmp_path):
    with _seed_directory(Factory(tmp_path), "example") as (seed, _build):
        pass
    seed.rmdir()
    with pytest.raises(ValueError, match="invalid fixture seed readiness"):
        with _seed_directory(Factory(tmp_path), "example"):
            pytest.fail("missing seed was consumed")


def test_sidecar_failure_never_degrades_to_unlocked_build(sandbox, tmp_path, monkeypatch):
    def unavailable_lock(path, *, timeout, require_sidecar):
        assert path == sandbox / "s" / "example.ready"
        assert timeout == 30
        assert require_sidecar is True
        raise TimeoutError("sidecar unavailable")

    monkeypatch.setattr(tracking, "_RecordLock", unavailable_lock)
    with pytest.raises(TimeoutError, match="sidecar unavailable"):
        with _seed_directory(Factory(tmp_path), "example"):
            pytest.fail("unlocked seed was consumed")
    assert not (sandbox / "s" / "example").exists()
    assert not (sandbox / "s" / "example.ready").exists()


def test_process_exit_releases_lock_but_does_not_publish_partial_seed(sandbox, tmp_path):
    script = """
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from conftest import _seed_directory
class Factory:
    def mktemp(self, key):
        raise AssertionError('unexpected group-local factory')
with _seed_directory(Factory(), 'interrupted') as (seed, build):
    assert build
    (seed / 'partial').write_text('not complete')
    os._exit(2)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(Path(__file__).parent)],
        env=os.environ.copy(), capture_output=True, text=True, timeout=15,
        **no_window_kwargs(),
    )
    assert result.returncode == 2, result.stderr
    assert not (sandbox / "s" / "interrupted.ready").exists()
    with pytest.raises(RuntimeError, match="incomplete fixture seed"):
        with _seed_directory(Factory(tmp_path), "interrupted"):
            pytest.fail("aborted builder was consumed")


@pytest.mark.parametrize("key", ["../escape", ".hidden", "nested/name"])
def test_seed_keys_cannot_escape_cache(sandbox, tmp_path, key):
    with pytest.raises(ValueError, match="invalid fixture seed key"):
        with _seed_directory(Factory(tmp_path), key):
            pytest.fail("invalid key was accepted")
    assert not (sandbox / "s").exists()


def test_two_processes_publish_once_and_reuse_after_group_cleanup(sandbox, tmp_path):
    script = """
import json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from conftest import _seed_directory
from agent_worktrees import tracking
root = Path(sys.argv[2])
role = sys.argv[3]
if role == 'waiter':
    original_try = tracking._RecordLock._sidecar_try
    def observed_try(self):
        acquired = original_try(self)
        if not acquired:
            (root / 'contended').write_text('actual cross-process lock contention')
        return acquired
    tracking._RecordLock._sidecar_try = observed_try
class Factory:
    def mktemp(self, key):
        raise AssertionError('run-local seed used group-local factory')
with _seed_directory(Factory(), 'concurrent') as (seed, build):
    if build:
        with (root / 'builds').open('a') as out:
            out.write('built\\n')
        (root / 'building').write_text('lock held')
        deadline = time.monotonic() + 10
        while not (root / 'contended').exists():
            assert time.monotonic() < deadline, 'second process never contended'
            time.sleep(0.01)
        (seed / 'data').write_text('complete')
    assert (seed / 'data').read_text() == 'complete'
    print(json.dumps(str(seed)))
"""
    processes = []
    try:
        for role in ("owner", "waiter"):
            processes.append(subprocess.Popen(
                [sys.executable, "-c", script, str(Path(__file__).parent),
                 str(sandbox), role],
                env=os.environ.copy(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, **no_window_kwargs(),
            ))
            if role == "owner":
                _wait_for(sandbox / "building", processes[-1])
        paths = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=15)
            assert process.returncode == 0, stderr
            paths.append(json.loads(stdout))
        assert paths[0] == paths[1] == str(sandbox / "s" / "concurrent")
        assert (sandbox / "contended").is_file()
        assert (sandbox / "builds").read_text().splitlines() == ["built"]
        group = tmp_path / "group-temp"
        group.mkdir()
        (group / "temporary").write_text("group only")
        shutil.rmtree(group)
        assert not group.exists()
        with _seed_directory(Factory(tmp_path), "concurrent") as (seed, build):
            assert not build and (seed / "data").read_text() == "complete"
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
