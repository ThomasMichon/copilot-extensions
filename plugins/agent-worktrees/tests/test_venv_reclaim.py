"""Checkout-local test venvs are rebuildable and must not block retirement.

Removal fails closed: anything not proven to be an unused, complete test venv
is kept.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from single_instance_lease import SingleInstance

from agent_worktrees import cleanup_reap, gc, git_ops, output, procs, tracking, venv_reclaim, venv_reclaim_hooks

HolderScan = Callable[..., tuple[list[dict[str, Any]], Any]]


def _venv(path: Path, *, old: bool = False) -> Path:
    (path / "Lib" / "site-packages").mkdir(parents=True)
    (path / "Scripts").mkdir()
    (path / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
    (path / "Lib" / "site-packages" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    if old:
        stamp = time.time() - 3600
        for item in (path, path / "pyvenv.cfg"):
            os.utime(item, (stamp, stamp))
    return path


def _scan(holders: list[dict[str, Any]], problem: Any = None) -> HolderScan:
    return lambda root, deadline=None: (holders, problem)


@pytest.fixture(autouse=True)
def _private_leases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "leases"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "leases"))


@pytest.fixture
def idle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(venv_reclaim, "_holders", _scan([]))


def _user(venv: Path) -> dict[str, Any]:
    return {"pid": 42, "name": "python", "paths": [str(venv / "Scripts" / "python.exe")]}


def test_recognition_accepts_venvs_groups_and_runner_metadata(tmp_path: Path) -> None:
    root = tmp_path / ".test-venvs"
    first = _venv(root / "plugin-a")
    second = _venv(root / "windows" / "plugin-b")
    (root / ".gitignore").write_text("*\n", encoding="utf-8")
    assert sorted(venv_reclaim.disposable_venvs(root) or []) == sorted([first, second])
    assert venv_reclaim.recognized_roots(tmp_path) == [root]


@pytest.mark.parametrize("intruder", [
    "loose.xml", "reports", "not-a-venv/data.txt", "env/logs",
    "env/capture.json", "env/Lib/reports/session.json", "env/notes",
])
def test_recognition_refuses_anything_that_is_not_a_venv(tmp_path: Path, intruder: str) -> None:
    root = tmp_path / ".test-venvs"
    _venv(root / "env")
    target = root / intruder
    if target.suffix:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("evidence", encoding="utf-8")
    else:
        target.mkdir(parents=True, exist_ok=True)
    assert venv_reclaim.disposable_venvs(root) is None
    assert venv_reclaim.recognized_roots(tmp_path) == []
    assert venv_reclaim.unrecognized_roots(tmp_path) == [root]


def test_nested_evidence_in_a_venv_is_preserved_by_clean(tmp_path: Path, idle: None) -> None:
    env = _venv(tmp_path / ".test-venvs" / "env")
    (env / "Lib" / "reports").mkdir()
    (env / "Lib" / "reports" / "session.json").write_text("{}", encoding="utf-8")
    report = venv_reclaim.clean_test_venvs(tmp_path)
    assert [r["status"] for r in report["roots"]] == ["preserved"]
    assert (env / "Lib" / "reports" / "session.json").exists()


def test_only_disposable_content_requires_every_entry_to_be_rebuildable(tmp_path: Path) -> None:
    _venv(tmp_path / ".test-venvs" / "env")
    (tmp_path / "src" / "__pycache__").mkdir(parents=True)
    assert venv_reclaim.only_disposable_content(tmp_path)
    (tmp_path / "src" / "kept.py").write_text("", encoding="utf-8")
    assert not venv_reclaim.only_disposable_content(tmp_path)


def test_clean_removes_idle_venvs_and_root_but_preserves_unrecognized(tmp_path: Path, idle: None) -> None:
    _venv(tmp_path / ".test-venvs" / "windows" / "env")
    runs = tmp_path / ".test-runs"
    _venv(runs / "env")
    (runs / "summary.json").write_text("{}", encoding="utf-8")
    report = venv_reclaim.clean_test_venvs(tmp_path)
    assert report["removed"] == 1 and report["remaining"] == 0
    assert not (tmp_path / ".test-venvs").exists()
    assert (runs / "env" / "pyvenv.cfg").exists()
    assert {r["path"]: r["status"] for r in report["roots"]}[str(runs)] == "preserved"


def test_dry_run_removes_nothing(tmp_path: Path, idle: None) -> None:
    env = _venv(tmp_path / ".test-venvs" / "env")
    report = venv_reclaim.clean_test_venvs(tmp_path, dry_run=True)
    assert [v["status"] for v in report["venvs"]] == ["would_remove"]
    assert env.exists()


def test_in_use_and_recent_venvs_are_left_and_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    busy = _venv(tmp_path / ".test-venvs" / "busy", old=True)
    fresh = _venv(tmp_path / ".test-venvs" / "fresh")
    monkeypatch.setattr(venv_reclaim, "_holders", _scan([_user(busy)]))
    report = venv_reclaim.clean_test_venvs(tmp_path, min_age_secs=60)
    statuses = {Path(v["path"]).name: v["status"] for v in report["venvs"]}
    assert statuses == {"busy": "in_use", "fresh": "recent"}
    assert busy.exists() and fresh.exists()
    assert "python(42)" in (venv_reclaim.blocker(report) or "")


def test_process_working_inside_root_blocks_the_whole_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _venv(tmp_path / ".test-venvs" / "env")
    monkeypatch.setattr(venv_reclaim, "_holders", _scan([], ("in_use", "live process(es) work inside root: pytest(7)")))
    report = venv_reclaim.clean_test_venvs(tmp_path)
    assert env.exists()
    assert "pytest(7)" in (venv_reclaim.blocker(report) or "")


def test_remove_tree_clears_read_only_files(tmp_path: Path) -> None:
    env = _venv(tmp_path / "env")
    os.chmod(env / "Lib" / "site-packages" / "mod.py", stat.S_IREAD)
    assert venv_reclaim.remove_tree(env)
    assert not env.exists()


def test_clean_test_venvs_command_reports_json(tmp_path: Path, idle: None) -> None:
    _venv(tmp_path / ".test-venvs" / "env")
    args = argparse.Namespace(worktree_dir=str(tmp_path), dry_run=False, json=True, min_age_secs=0.0)
    with output.capture_json_output() as buf:
        assert venv_reclaim_hooks.cmd_clean_test_venvs(args) == 0
    payload = json.loads(buf.getvalue())
    data = payload.get("data", payload)
    assert data["removed"] == 1
    assert not (tmp_path / ".test-venvs").exists()


def test_gc_orphan_holding_only_test_venvs_is_removable(tmp_path: Path) -> None:
    d = tmp_path / "venvonly"
    _venv(d / ".test-venvs" / "windows" / "agent-worktrees")
    assert gc.classify_orphan(d, min_settle_secs=0).action == "remove"


def test_gc_orphan_test_venv_root_with_reports_is_skipped(tmp_path: Path) -> None:
    root = tmp_path / "venvreports" / ".test-venvs"
    _venv(root / "env")
    (root / "reports").mkdir()
    (root / "reports" / "junit.xml").write_text("<testsuite/>", encoding="utf-8")
    assert gc.classify_orphan(tmp_path / "venvreports", min_settle_secs=0).action == "skip"


# Finding 1: gc deletion rechecks live use at action time.
@pytest.mark.parametrize("problem", [None, ("unverified", "process use cannot be verified")])
def test_gc_remove_tree_keeps_a_venv_orphan_that_may_be_in_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, problem: Any,
) -> None:
    d = tmp_path / "orphan"
    env = _venv(d / ".test-venvs" / "env")
    monkeypatch.setattr(venv_reclaim, "_holders", _scan([] if problem else [_user(env)], problem))
    removed, reason = gc.remove_tree(d)
    assert not removed and "retry later" in reason
    assert (env / "pyvenv.cfg").exists()


def test_gc_remove_tree_removes_an_idle_venv_orphan(tmp_path: Path, idle: None) -> None:
    d = tmp_path / "orphan"
    _venv(d / ".test-venvs" / "env")
    assert gc.remove_tree(d) == (True, "removed")
    assert not d.exists()


# Finding 2: session end decides and detaches under the registration lock.
@pytest.fixture
def ended_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, idle: None) -> tuple[Path, Path]:
    from agent_worktrees import activity, config, tracking_session_registry

    checkout = tmp_path / "checkout"
    _venv(checkout / ".test-venvs" / "env", old=True)
    records = tmp_path / "records"
    records.mkdir()
    monkeypatch.setattr(config, "tracking_dir", lambda name=None: records)
    monkeypatch.setattr(tracking, "load_record", lambda path: SimpleNamespace(worktree_path=str(checkout)))
    monkeypatch.setattr(tracking_session_registry, "_record_has_open_session", lambda record: False)
    monkeypatch.setattr(activity, "log_event", lambda *a, **k: None)
    monkeypatch.setenv(venv_reclaim_hooks.SESSION_END_BUDGET_ENV, "20")
    return checkout, records / "wt.yaml"


def test_session_end_removes_idle_venvs(ended_session: tuple[Path, Path]) -> None:
    checkout, _ = ended_session
    report = venv_reclaim_hooks.reclaim_after_session_end("wt")
    assert report is not None and report["removed"] == 1
    assert not (checkout / ".test-venvs").exists() or not any((checkout / ".test-venvs").iterdir())


def test_session_end_keeps_venvs_when_a_session_is_open(
    ended_session: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import tracking_session_registry

    checkout, _ = ended_session
    monkeypatch.setattr(tracking_session_registry, "_record_has_open_session", lambda record: True)
    assert venv_reclaim_hooks.reclaim_after_session_end("wt") is None
    assert (checkout / ".test-venvs" / "env" / "pyvenv.cfg").exists()


def test_session_end_skips_while_registration_holds_the_record_lock(ended_session: tuple[Path, Path]) -> None:
    checkout, yaml_path = ended_session
    held, done = threading.Event(), threading.Event()

    def register() -> None:
        with tracking._RecordLock(yaml_path, require_sidecar=True):
            held.set()
            done.wait(10)

    worker = threading.Thread(target=register)
    worker.start()
    try:
        assert held.wait(10)
        assert venv_reclaim_hooks.reclaim_after_session_end("wt") is None
    finally:
        done.set()
        worker.join(10)
    assert (checkout / ".test-venvs" / "env" / "pyvenv.cfg").exists()


# Finding 4: process inspection sees venv use through invocation and environment.
def _fake_pid(proc: Path, *, cwd: str, exe: str, argv0: str, environ: dict[str, str]) -> tuple[Path, Callable[[str], str]]:
    pid = proc / str(os.getpid() + 1)
    (pid / "fd").mkdir(parents=True)
    (pid / "fd" / "0").write_text("", encoding="utf-8")
    (pid / "maps").write_text("", encoding="utf-8")
    (pid / "cmdline").write_bytes(argv0.encode() + b"\0-m\0pytest\0")
    (pid / "environ").write_bytes(b"\0".join(f"{k}={v}".encode() for k, v in environ.items()))
    (pid / "comm").write_text("python\n", encoding="utf-8")
    links = {str(pid / "cwd"): cwd, str(pid / "exe"): exe, str(pid / "fd" / "0"): str(proc / "null")}

    def readlink(path: str) -> str:
        if path not in links:
            raise FileNotFoundError(path)
        return links[path]

    return pid, readlink


@pytest.mark.parametrize("via", ["argv0", "virtual_env", "path"])
def test_linux_scan_finds_a_venv_whose_exe_resolves_to_the_base_interpreter(tmp_path: Path, via: str) -> None:
    root = tmp_path / ".test-venvs"
    env = _venv(root / "env")
    interpreter = str(env / "bin" / "python")
    environ = {
        "argv0": {}, "virtual_env": {"VIRTUAL_ENV": str(env)},
        "path": {"PATH": str(env / "bin")},
    }[via]
    pid, readlink = _fake_pid(
        tmp_path / "proc", cwd=str(tmp_path), exe=str(tmp_path / "base" / "python3"),
        argv0=interpreter if via == "argv0" else "python", environ=environ,
    )
    scan = venv_reclaim._linux_scan(root, proc=tmp_path / "proc", readlink=readlink, uid=pid.stat().st_uid)
    assert scan is not None
    holders, blind = scan
    assert blind == set()
    assert [h["pid"] for h in holders] == [int(pid.name)]


def test_linux_scan_reports_uninspectable_processes(tmp_path: Path) -> None:
    root = tmp_path / ".test-venvs"
    _venv(root / "env")
    pid, _ = _fake_pid(tmp_path / "proc", cwd=str(tmp_path), exe="x", argv0="python", environ={})

    def denied(path: str) -> str:
        raise PermissionError(path)

    scan = venv_reclaim._linux_scan(root, proc=tmp_path / "proc", readlink=denied, uid=pid.stat().st_uid)
    assert scan == ([], {"python"})


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_unavailable_inspection_counts_as_in_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str,
) -> None:
    env = _venv(tmp_path / ".test-venvs" / "env")
    monkeypatch.setattr(venv_reclaim, "_platform", lambda: platform)
    monkeypatch.setattr(venv_reclaim, "_linux_scan", lambda root, deadline=None: ([], {"ghost"}))
    report = venv_reclaim.clean_test_venvs(tmp_path)
    assert [r["status"] for r in report["roots"]] == ["unverified"]
    assert env.exists()
    assert venv_reclaim.disposal_blocker(report) is not None


# Finding 5: a runner's environment lease keeps its venv.
def test_held_runner_lease_keeps_the_venv(tmp_path: Path, idle: None) -> None:
    env = _venv(tmp_path / ".test-venvs" / "env")
    lease = SingleInstance(venv_reclaim.lease_dir(env), service=venv_reclaim.LEASE_SERVICE)
    lease.acquire()
    try:
        report = venv_reclaim.clean_test_venvs(tmp_path)
    finally:
        lease.release()
    assert [v["status"] for v in report["venvs"]] == ["in_use"]
    assert (env / "pyvenv.cfg").exists()


# Finding 6: preserved test-root content holds whole-checkout disposal.
def test_preserved_test_run_content_blocks_checkout_disposal(tmp_path: Path, idle: None) -> None:
    result = tmp_path / ".test-runs" / "reports" / "result.json"
    result.parent.mkdir(parents=True)
    result.write_text("{}", encoding="utf-8")
    report = venv_reclaim.clean_test_venvs(tmp_path)
    assert venv_reclaim.disposal_blocker(report, allow_unverified=True)
    assert "preserved test-root content" in (venv_reclaim_hooks.data_blocker(tmp_path) or "")
    assert result.exists()


def _reap(record: tracking.WorktreeRecord, tmp_path: Path) -> tuple[int, list[str]]:
    info: Any = SimpleNamespace(state=SimpleNamespace(value="completed"))
    repo: Any = SimpleNamespace(anchor=str(tmp_path / "anchor"), default_branch="main")
    return cleanup_reap._reap_locked(record, info, repo, tmp_path)


@pytest.fixture
def reapable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sample_record: tracking.WorktreeRecord) -> tracking.WorktreeRecord:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    monkeypatch.setattr(cleanup_reap, "retirement_blocker", lambda record: None)
    monkeypatch.setattr(cleanup_reap, "preservation_reason", lambda path: None)
    monkeypatch.setattr(procs, "checkout_retirement_blocker", lambda path: None)
    monkeypatch.setattr(git_ops, "remove_worktree", lambda *a, **k: pytest.fail("checkout must stay"))
    return replace(sample_record, worktree_path=str(checkout))


def test_cleanup_keeps_checkout_with_preserved_test_run_content(
    reapable: tracking.WorktreeRecord, tmp_path: Path, idle: None,
) -> None:
    result = Path(reapable.worktree_path) / ".test-runs" / "reports" / "result.json"
    result.parent.mkdir(parents=True)
    result.write_text("{}", encoding="utf-8")
    failures, warnings = _reap(reapable, tmp_path)
    assert failures == 1 and "preserved test-root content" in warnings[0]
    assert result.exists()


def test_cleanup_keeps_checkout_whose_test_venv_is_in_use(
    reapable: tracking.WorktreeRecord, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _venv(Path(reapable.worktree_path) / ".test-venvs" / "env")
    monkeypatch.setattr(venv_reclaim, "_holders", _scan([_user(env)]))
    failures, warnings = _reap(reapable, tmp_path)
    assert failures == 1 and "in_use" in warnings[0] and "python(42)" in warnings[0]
    assert (env / "pyvenv.cfg").exists()


# Finding 7: the deadline bounds recognition, inspection and removal.
def test_expired_deadline_removes_nothing(tmp_path: Path, idle: None) -> None:
    env = _venv(tmp_path / ".test-venvs" / "env")
    report = venv_reclaim.clean_test_venvs(tmp_path, deadline=time.monotonic() - 1)
    assert report["expired"] and env.exists()
    assert venv_reclaim.disposal_blocker(report) is not None


def test_deadline_reached_during_inspection_defers_removal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _venv(tmp_path / ".test-venvs" / "env")

    def slow(root: Path, deadline: float | None = None) -> tuple[list[dict[str, Any]], None]:
        time.sleep(0.3)
        return [], None

    monkeypatch.setattr(venv_reclaim, "_holders", slow)
    report = venv_reclaim.clean_test_venvs(tmp_path, deadline=time.monotonic() + 0.2)
    assert report["expired"] and [r["status"] for r in report["roots"]] == ["deferred"]
    assert (env / "pyvenv.cfg").exists()


def test_session_end_budget_bounds_the_hook(ended_session: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    checkout, _ = ended_session
    monkeypatch.setenv(venv_reclaim_hooks.SESSION_END_BUDGET_ENV, "0")
    assert venv_reclaim_hooks.reclaim_after_session_end("wt") is None
    assert (checkout / ".test-venvs" / "env" / "pyvenv.cfg").exists()