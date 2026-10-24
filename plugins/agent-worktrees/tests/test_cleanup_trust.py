"""Failed physical reap never revokes trust from a retained checkout."""

from dataclasses import replace
import subprocess
import sys
import time
from types import SimpleNamespace

import agent_procutil as pu
import pytest

from agent_worktrees import cleanup_reap, permissions, sessions


@pytest.mark.parametrize("removed", [False, True])
def test_cleanup_trust_follows_actual_directory_removal(
    tmp_path, monkeypatch, sample_record, removed,
):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    home = tmp_path / ".copilot"
    monkeypatch.setattr(permissions, "_copilot_dir", lambda: home)
    permissions.ensure_trusted_folder(str(checkout))
    record = replace(sample_record, worktree_path=str(checkout))
    monkeypatch.setattr(cleanup_reap, "retirement_blocker", lambda record: None)
    inspections = []

    def inspect(path, **kwargs):
        inspections.append(path)
        return []

    def unexpected_teardown(*args, **kwargs):
        pytest.fail("Cleanup must not terminate processes or mux sessions, even without holders")

    monkeypatch.setattr(sessions, "kill_tmux_session", unexpected_teardown)
    monkeypatch.setattr(cleanup_reap.procs, "terminate_processes_under", unexpected_teardown)
    monkeypatch.setattr(cleanup_reap.procs, "processes_with_cwd_under", inspect)

    def remove(*args, **kwargs):
        if removed:
            checkout.rmdir()
        return removed

    monkeypatch.setattr(cleanup_reap.git_ops, "remove_worktree", remove)
    monkeypatch.setattr(cleanup_reap.git_ops, "delete_branch", lambda *a, **k: True)
    monkeypatch.setattr(cleanup_reap.shutil, "rmtree", lambda *a, **k: None)
    monkeypatch.setattr(cleanup_reap.time, "sleep", lambda *a: None)
    monkeypatch.setattr(cleanup_reap.tracking, "retire_record", lambda *a: False)
    monkeypatch.setattr(cleanup_reap.activity, "log_event", lambda *a, **k: None)
    failures, warnings = cleanup_reap._reap_locked(
        record, SimpleNamespace(state=SimpleNamespace(value="completed")),
        SimpleNamespace(anchor=str(tmp_path / "anchor")), tmp_path,
    )
    assert inspections == [str(checkout)]
    assert failures == (0 if removed else 1)
    assert any("Directory still present" in warning for warning in warnings) is not removed
    _, data = permissions._read_config_jsonc(home / "config.json")
    assert (str(checkout.resolve()) in data["trustedFolders"]) is not removed


def test_cleanup_preserves_checkout_held_by_live_process(
    tmp_path, monkeypatch, sample_record,
):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    home = tmp_path / ".copilot"
    monkeypatch.setattr(permissions, "_copilot_dir", lambda: home)
    permissions.ensure_trusted_folder(str(checkout))
    record = replace(sample_record, worktree_path=str(checkout))
    monkeypatch.setattr(cleanup_reap, "retirement_blocker", lambda record: None)
    monkeypatch.setattr(
        cleanup_reap.procs, "processes_with_cwd_under",
        lambda path, **kwargs: [{"pid": 12345, "name": "holder"}],
    )
    effects = []
    monkeypatch.setattr(sessions, "kill_tmux_session", lambda *a: effects.append("mux"))
    monkeypatch.setattr(
        cleanup_reap.procs, "terminate_processes_under",
        lambda *a: effects.append("terminate") or [],
    )
    monkeypatch.setattr(
        cleanup_reap.git_ops, "remove_worktree", lambda *a: effects.append("remove") or True,
    )
    monkeypatch.setattr(
        cleanup_reap.git_ops, "delete_branch", lambda *a, **k: effects.append("branch") or True,
    )
    monkeypatch.setattr(cleanup_reap.shutil, "rmtree", lambda *a, **k: effects.append("rmtree"))
    monkeypatch.setattr(cleanup_reap.time, "sleep", lambda *a: None)
    monkeypatch.setattr(
        cleanup_reap.tracking, "retire_record", lambda *a: effects.append("retire") or False,
    )
    monkeypatch.setattr(cleanup_reap.activity, "log_event", lambda *a, **k: None)

    failures, warnings = cleanup_reap._reap_locked(
        record, SimpleNamespace(state=SimpleNamespace(value="completed")),
        SimpleNamespace(anchor=str(tmp_path / "anchor")), tmp_path,
    )

    assert effects == []
    assert failures == 1
    assert any("live process" in warning and "12345" in warning for warning in warnings)
    assert checkout.exists()
    _, data = permissions._read_config_jsonc(home / "config.json")
    assert str(checkout.resolve()) in data["trustedFolders"]


@pytest.mark.parametrize("system", ["Darwin", "Windows", "Linux"])
def test_cleanup_defers_when_cwd_inspection_is_unavailable(
    tmp_path, monkeypatch, sample_record, system,
):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    record = replace(sample_record, worktree_path=str(checkout))
    monkeypatch.setattr(cleanup_reap, "retirement_blocker", lambda record: None)
    monkeypatch.setattr(cleanup_reap.procs.platform, "system", lambda: system)

    def unavailable(*args, **kwargs):
        raise OSError("backend unavailable")

    if system == "Windows":
        monkeypatch.setattr(cleanup_reap.procs, "_win_kernel32", unavailable)
    elif system == "Linux":
        monkeypatch.setattr(cleanup_reap.procs, "_iter_processes_posix", unavailable)

    def unexpected_removal(*args, **kwargs):
        pytest.fail("Unavailable cwd inspection must not permit checkout removal")

    monkeypatch.setattr(cleanup_reap.git_ops, "remove_worktree", unexpected_removal)
    failures, warnings = cleanup_reap._reap_locked(
        record, SimpleNamespace(state=SimpleNamespace(value="completed")),
        SimpleNamespace(anchor=str(tmp_path / "anchor")), tmp_path,
    )
    assert failures == 1
    assert any("inspection unavailable" in warning for warning in warnings)
    assert checkout.exists()


@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("process")
@pytest.mark.skipif(sys.platform not in {"win32", "linux"}, reason="native cwd reader unavailable")
def test_cleanup_waits_for_real_checkout_holder_to_exit(
    tmp_path, monkeypatch, sample_record,
):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    ready = tmp_path / "ready"
    record = replace(sample_record, worktree_path=str(checkout))
    monkeypatch.setattr(cleanup_reap, "retirement_blocker", lambda record: None)
    home = tmp_path / ".copilot"
    monkeypatch.setattr(permissions, "_copilot_dir", lambda: home)
    permissions.ensure_trusted_folder(str(checkout))
    effects = []

    def unexpected_teardown(*args, **kwargs):
        pytest.fail("Cleanup must not tear down the process owner's resources")

    def remove(*args, **kwargs):
        effects.append("remove")
        checkout.rmdir()
        return True

    monkeypatch.setattr(sessions, "kill_tmux_session", unexpected_teardown)
    monkeypatch.setattr(cleanup_reap.procs, "terminate_processes_under", unexpected_teardown)
    monkeypatch.setattr(cleanup_reap.git_ops, "remove_worktree", remove)
    monkeypatch.setattr(
        cleanup_reap.git_ops, "delete_branch", lambda *a, **k: effects.append("branch") or True,
    )
    monkeypatch.setattr(
        cleanup_reap.tracking, "retire_record", lambda *a: effects.append("retire") or False,
    )
    monkeypatch.setattr(cleanup_reap.activity, "log_event", lambda *a, **k: None)
    args = (
        record, SimpleNamespace(state=SimpleNamespace(value="completed")),
        SimpleNamespace(anchor=str(tmp_path / "anchor")), tmp_path,
    )
    child = subprocess.Popen(
        [
            sys._base_executable, "-I", "-c",
            "import pathlib,sys; pathlib.Path(sys.argv[1]).touch(); sys.stdin.buffer.read(1)",
            str(ready),
        ],
        cwd=checkout, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, **pu.no_window_kwargs(),
    )
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "test-owned child did not become ready"
        failures, warnings = cleanup_reap._reap_locked(*args)
        assert failures == 1
        assert any(str(child.pid) in warning for warning in warnings)
        assert child.poll() is None
        assert checkout.exists()
        assert effects == []
        _, data = permissions._read_config_jsonc(home / "config.json")
        assert str(checkout.resolve()) in data["trustedFolders"]
        child.communicate(input=b"x", timeout=5)
        assert child.returncode == 0
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
        if child.stdin:
            child.stdin.close()

    failures, _ = cleanup_reap._reap_locked(*args)
    assert failures == 0
    assert effects == ["remove", "branch", "retire"]
    assert not checkout.exists()
    _, data = permissions._read_config_jsonc(home / "config.json")
    assert str(checkout.resolve()) not in data["trustedFolders"]
