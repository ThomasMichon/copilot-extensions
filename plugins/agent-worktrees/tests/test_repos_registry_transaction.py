"""The ordinary registry writer and Configuration share one conflict fence."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import subprocess
import sys
import threading

from agent_procutil import no_window_kwargs
import pytest

from agent_worktrees import repos
from agent_worktrees.repos_registry_transaction import RegistryConflictError, registry_lock


@pytest.fixture
def registry_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    repos.set_srcroot(str(tmp_path / "src"))
    return tmp_path


def test_stale_ordinary_writer_cannot_clobber_preference(registry_home):
    before = repos.read_registry()
    repos.set_account_map("example", "new-login")
    current = repos._repos_yaml_path().read_bytes()
    before.srcroot["linux"] = "/stale"
    with pytest.raises(RegistryConflictError, match="changed"):
        repos.write_registry(before)
    assert repos._repos_yaml_path().read_bytes() == current


def test_writer_refreshes_own_baseline(registry_home):
    registry = repos.read_registry()
    for value in ("first", "second"):
        registry.account_map["example"] = value
        repos.write_registry(registry)
    assert repos.read_registry().account_map["example"] == "second"


def test_registry_writes_preserve_publication_authority_guard(registry_home, monkeypatch):
    from agent_worktrees import pr_authority, tracking

    entered = []
    original = tracking._atomic_write

    @contextmanager
    def guard():
        entered.append(True)
        try:
            yield
        finally:
            entered.pop()

    def checked_write(path, content):
        assert entered
        return original(path, content)

    monkeypatch.setattr(pr_authority, "guard", guard)
    monkeypatch.setattr(tracking, "_atomic_write", checked_write)
    repos.set_account_map("example", "guarded")
    assert not entered
    assert repos.read_registry().account_map["example"] == "guarded"


def test_unread_or_invalid_registry_cannot_replace_existing(registry_home):
    path = repos._repos_yaml_path()
    with pytest.raises(RegistryConflictError, match="read before"):
        repos.write_registry(repos.ReposRegistry())
    path.write_text("repos: [broken", encoding="utf-8")
    invalid = repos.read_registry()
    with pytest.raises(RegistryConflictError):
        repos.write_registry(invalid)
    assert path.read_text(encoding="utf-8") == "repos: [broken"


def test_changed_destination_requires_fresh_read(registry_home, monkeypatch):
    registry = repos.read_registry()
    monkeypatch.setenv("AGENT_HOME", str(registry_home / "different"))
    with pytest.raises(RegistryConflictError):
        repos.write_registry(registry)
    assert not repos._repos_yaml_path().exists()


def test_two_threads_have_exactly_one_winner(registry_home):
    barrier = threading.Barrier(2)

    def mutate(login):
        registry = repos.read_registry(strict=True)
        registry.account_map["example"] = login
        barrier.wait(timeout=5)
        try:
            repos.write_registry(registry)
            return "written"
        except RegistryConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(mutate, ("first", "second"))) == ["conflict", "written"]


def test_lock_covers_real_process_ordinary_writer(registry_home):
    path = repos._repos_yaml_path()
    script = (
        "from agent_worktrees import repos; "
        "repos.set_account_map('example', 'child-login')"
    )
    child = None
    try:
        with registry_lock(path):
            child = subprocess.Popen(
                [sys.executable, "-c", script], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, **no_window_kwargs(),
            )
            # The child cannot commit while the same native lock is held.
            with pytest.raises(subprocess.TimeoutExpired):
                child.communicate(timeout=0.3)
            assert "example" not in repos.read_registry().account_map
        stdout, stderr = child.communicate(timeout=15)
        assert child.returncode == 0, (stdout, stderr)
        assert repos.read_registry().account_map["example"] == "child-login"
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.communicate(timeout=5)


def test_read_only_absent_registry_creates_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "fresh"))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    assert not repos.read_registry(strict=True).repos
    assert not (tmp_path / "fresh").exists()
