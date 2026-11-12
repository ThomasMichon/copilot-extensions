"""Guided legacy test-directory reclamation stays explicit and fail-closed."""
from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tools import reclaim_legacy_test_dirs as reclaim

_REAL_IN_USE = reclaim.in_use


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                   env=reclaim.git_environment(),
                   creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)))


@pytest.fixture(autouse=True)
def _no_live_users(monkeypatch):
    """Liveness is a platform provider; tests that exercise it override this."""
    monkeypatch.setattr(reclaim, "in_use", lambda _path, _accepted=frozenset(): (False, ""))


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    (root / ".gitignore").write_text("/.test-venvs/\n.test-runs/\n", encoding="utf-8")
    _git(root, "add", ".gitignore")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "commit", "-q", "-m", "init")
    return root


def _age(target, hours=48.0):
    stamp = time.time() - hours * 3600
    for path in sorted(target.rglob("*"), reverse=True):
        os.utime(path, (stamp, stamp))
    os.utime(target, (stamp, stamp))


def _legacy(root, name=".test-venvs", *, age_hours=48.0, evidence=False):
    env = root / name / "linux" / "plugin"
    env.mkdir(parents=True)
    (env / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
    (env / "python").write_bytes(b"x" * 1024)
    if evidence:
        probe = root / name / "capture-probes"
        probe.mkdir()
        (probe / "report.json").write_text("{}", encoding="utf-8")
    _age(root / name, age_hours)
    return root / name


def _run(*args, capsys):
    code = reclaim.main(["--json", *map(str, args)])
    out = capsys.readouterr().out
    return code, (json.loads(out) if out.strip() else None)


def _plan(repo, tmp_path, target, *extra, capsys):
    plan = tmp_path / "plan.json"
    code, _report = _run("--repo", repo, "--select", target, *extra, "--plan-out", plan,
                         capsys=capsys)
    return code, plan


def test_inventory_classifies_without_deleting(repo, capsys):
    venvs = _legacy(repo, evidence=True)
    runs = repo / ".test-runs"
    runs.mkdir()
    (runs / "log.txt").write_text("x", encoding="utf-8")
    _age(runs)
    code, report = _run("--repo", repo, capsys=capsys)
    assert code == 0
    by_path = {c["path"]: c for c in report["candidates"]}
    assert by_path[str(venvs)]["status"] == "needs-decision"
    assert by_path[str(venvs)]["unclassified_sample"] == ["capture-probes/report.json"]
    assert by_path[str(runs)]["status"] == "needs-decision"
    assert venvs.is_dir() and runs.is_dir()


def test_apply_requires_explicit_writer_quiescence_confirmation(repo, tmp_path, capsys):
    target = _legacy(repo)
    code, plan = _plan(repo, tmp_path, target, capsys=capsys)
    assert code == 0
    with pytest.raises(SystemExit) as failure:
        reclaim.main(["--apply-plan", str(plan)])
    assert failure.value.code == 2
    assert "--confirm-writers-stopped" in capsys.readouterr().err
    assert target.is_dir()


@pytest.mark.skipif(os.name == "nt", reason="POSIX Unix socket")
def test_live_unix_socket_is_refused_even_inside_environment(repo, capsys, monkeypatch):
    target = _legacy(repo)
    endpoint = target / "linux" / "plugin" / "lib"
    endpoint.mkdir()
    monkeypatch.chdir(endpoint)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind("server.sock")
        server.listen(1)
        _age(target)
        code, report = _run("--repo", repo, capsys=capsys)
        assert code == 0
        assert report["candidates"][0]["status"] == "refused"
        assert "special file" in report["candidates"][0]["reason"]
        assert (endpoint / "server.sock").exists()


def test_environment_only_tree_is_ready_and_reclaimed_from_plan(repo, tmp_path, capsys):
    target = _legacy(repo)
    code, plan = _plan(repo, tmp_path, target, capsys=capsys)
    assert code == 0 and target.is_dir()
    code, report = _run("--apply-plan", plan, "--confirm-writers-stopped", capsys=capsys)
    assert code == 0
    assert report["candidates"][0]["status"] == "reclaimed"
    assert not os.path.lexists(target)
    assert not list(repo.glob(".test-venvs.reclaiming-*"))


def test_existing_quarantine_is_not_overwritten(repo, tmp_path, capsys):
    target = _legacy(repo)
    code, plan = _plan(repo, tmp_path, target, capsys=capsys)
    assert code == 0
    quarantine = target.with_name(f"{target.name}.reclaiming-{os.getpid()}")
    quarantine.mkdir()
    before = quarantine.stat().st_ino
    code, report = _run("--apply-plan", plan, "--confirm-writers-stopped", capsys=capsys)
    assert code == 1 and report["candidates"][0]["status"] == "refused"
    assert target.is_dir() and quarantine.stat().st_ino == before


@pytest.mark.parametrize("collision", [False, True])
def test_post_rename_inspection_error_reports_restore_or_collision(
    repo, tmp_path, capsys, monkeypatch, collision,
):
    target = _legacy(repo)
    code, plan = _plan(repo, tmp_path, target, capsys=capsys)
    assert code == 0
    survey = reclaim.survey
    created = []

    def failing_survey(path):
        if ".reclaiming-" in path.name:
            if collision:
                target.mkdir()
                created.append(target.stat().st_ino)
            raise PermissionError("inspection denied")
        return survey(path)

    monkeypatch.setattr(reclaim, "survey", failing_survey)
    code, report = _run("--apply-plan", plan, "--confirm-writers-stopped", capsys=capsys)
    assert code == 1 and report["candidates"][0]["status"] == "refused"
    assert target.is_dir()
    if collision:
        assert target.stat().st_ino == created[0] and not list(target.iterdir())
        assert report["candidates"][0]["errors"]
        assert len(list(repo.glob(".test-venvs.reclaiming-*"))) == 1
    else:
        assert (target / "linux" / "plugin" / "python").is_file()
        assert not list(repo.glob(".test-venvs.reclaiming-*"))


def test_removal_exception_reports_irreversible_partial_outcome(repo, tmp_path, capsys, monkeypatch):
    target = _legacy(repo)
    code, plan = _plan(repo, tmp_path, target, capsys=capsys)
    assert code == 0

    def interrupted(path, _errors):
        (path / "linux" / "plugin" / "python").unlink()
        raise OSError("storage failure")

    monkeypatch.setattr(reclaim, "_remove", interrupted)
    code, report = _run("--apply-plan", plan, "--confirm-writers-stopped", capsys=capsys)
    assert code == 1 and report["candidates"][0]["status"] == "partial"
    assert target.is_dir() and not (target / "linux" / "plugin" / "python").exists()
    assert (target / "linux" / "plugin" / "pyvenv.cfg").is_file()
    assert "removal interrupted" in report["candidates"][0]["errors"][0]


def test_unverifiable_removal_outcome_is_not_reported_as_success(repo, tmp_path, capsys, monkeypatch):
    target = _legacy(repo)
    code, plan = _plan(repo, tmp_path, target, capsys=capsys)
    assert code == 0
    removed = []
    lstat = reclaim.os.lstat

    def inaccessible(path, *args, **kwargs):
        if removed and ".reclaiming-" in os.fspath(path):
            raise PermissionError("outcome unreadable")
        return lstat(path, *args, **kwargs)

    monkeypatch.setattr(reclaim.os, "lstat", inaccessible)
    monkeypatch.setattr(reclaim, "_remove", lambda _path, _errors: removed.append(True))
    code, report = _run("--apply-plan", plan, "--confirm-writers-stopped", capsys=capsys)
    removed.clear()
    assert code == 1 and report["candidates"][0]["status"] == "partial"
    assert "could not be verified" in report["candidates"][0]["reason"]
    assert len(list(repo.glob(".test-venvs.reclaiming-*"))) == 1


def test_later_inspection_timeout_retains_earlier_results(repo, tmp_path, capsys, monkeypatch):
    first, second = _legacy(repo), _legacy(repo, ".test-runs")
    code, plan = _plan(repo, tmp_path, first, "--select", second, capsys=capsys)
    assert code == 0
    evaluate = reclaim.evaluate

    def timeout(checkout, path, **kwargs):
        if path == second:
            raise subprocess.TimeoutExpired("git", 30)
        return evaluate(checkout, path, **kwargs)

    monkeypatch.setattr(reclaim, "evaluate", timeout)
    code, report = _run("--apply-plan", plan, "--confirm-writers-stopped", capsys=capsys)
    assert code == 1
    assert [row["status"] for row in report["candidates"]] == ["reclaimed", "refused"]
    assert not first.exists() and second.is_dir()


@pytest.mark.parametrize("field,value", [
    ("identity", None),
    ("discard_unclassified", "false"),
    ("accepted_uninspectable", "worker"),
])
def test_invalid_later_plan_target_refuses_before_any_deletion(repo, tmp_path, capsys, field, value):
    first = _legacy(repo)
    second = _legacy(repo, ".test-runs")
    code, plan = _plan(repo, tmp_path, first, "--select", second, capsys=capsys)
    assert code == 0
    data = json.loads(plan.read_text(encoding="utf-8"))
    data["targets"][1][field] = value
    plan.write_text(json.dumps(data), encoding="utf-8")
    code, report = _run("--apply-plan", plan, "--confirm-writers-stopped", capsys=capsys)
    assert code == 2 and report is None
    assert first.is_dir() and second.is_dir()


def test_permission_failure_does_not_change_shared_hardlink_permissions(tmp_path, monkeypatch):
    outside = tmp_path / "shared-file"
    outside.write_bytes(b"shared bytes")
    tree = tmp_path / "tree"
    tree.mkdir()
    linked = tree / "linked-file"
    try:
        os.link(outside, linked)
    except OSError:
        pytest.skip("filesystem does not support hard links")
    before = outside.stat().st_mode

    def rmtree(_path, **callbacks):
        retry = callbacks.get("onexc") or callbacks["onerror"]
        retry(os.unlink, str(linked), PermissionError("read-only"))

    monkeypatch.setattr(reclaim.shutil, "rmtree", rmtree)
    errors = []
    reclaim._remove(tree, errors)
    assert errors and "read-only" in errors[0]
    assert outside.stat().st_mode == before
    assert outside.read_bytes() == b"shared bytes"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_permission_failure_preserves_existing_permission_bits(tmp_path, monkeypatch):
    tree = tmp_path / "tree"
    tree.mkdir()
    target = tree / "file"
    target.write_bytes(b"x")
    target.chmod(0o440)

    def rmtree(_path, **callbacks):
        retry = callbacks.get("onexc") or callbacks["onerror"]
        retry(lambda _target: None, str(target), PermissionError("read-only"))

    monkeypatch.setattr(reclaim.shutil, "rmtree", rmtree)
    errors = []
    reclaim._remove(tree, errors)
    assert errors
    assert stat.S_IMODE(target.stat().st_mode) == 0o440


@pytest.mark.parametrize("legacy_error", [False, True])
def test_multi_argument_rmtree_failure_is_reported_without_blind_retry(
    repo, tmp_path, capsys, monkeypatch, legacy_error,
):
    target = _legacy(repo)
    code, plan = _plan(repo, tmp_path, target, capsys=capsys)
    assert code == 0

    def rmtree(path, **callbacks):
        error = PermissionError("open denied")
        detail = (PermissionError, error, None) if legacy_error else error
        report = callbacks.get("onexc") or callbacks["onerror"]
        report(os.open, path, detail)

    monkeypatch.setattr(reclaim.shutil, "rmtree", rmtree)
    code, result = _run("--apply-plan", plan, "--confirm-writers-stopped", capsys=capsys)
    assert code == 1 and result["candidates"][0]["status"] == "partial"
    assert "open denied" in result["candidates"][0]["errors"][0]
    assert (target / "linux" / "plugin" / "python").is_file()
    assert not list(repo.glob(".test-venvs.reclaiming-*"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX fd-based rmtree")
def test_real_rmtree_open_failure_restores_and_reports(repo, tmp_path, capsys, monkeypatch):
    if not reclaim.shutil.rmtree.avoids_symlink_attacks:
        pytest.skip("fd-based rmtree unavailable")
    target = _legacy(repo)
    code, plan = _plan(repo, tmp_path, target, capsys=capsys)
    assert code == 0
    open_directory = os.open
    seen = []

    def denied(path, flags, mode=0o777, *, dir_fd=None):
        if ".reclaiming-" in os.fsdecode(path):
            seen.append(path)
            raise PermissionError("directory open denied")
        return open_directory(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(reclaim.os, "open", denied)
    code, report = _run("--apply-plan", plan, "--confirm-writers-stopped", capsys=capsys)
    assert seen and code == 1
    assert report["candidates"][0]["status"] == "partial"
    assert any("directory open denied" in error for error in report["candidates"][0]["errors"])
    assert (target / "linux" / "plugin" / "python").is_file()
    assert not list(repo.glob(".test-venvs.reclaiming-*"))


@pytest.mark.parametrize("relative", ["reports/session.json", "Lib/reports/session.json", "capture.json"])
def test_evidence_inside_environment_still_requires_discard(repo, tmp_path, capsys, relative):
    target = _legacy(repo)
    evidence = target / "linux" / "plugin" / relative
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text("retained evidence", encoding="utf-8")
    _age(target)
    code, report = _run("--repo", repo, capsys=capsys)
    assert code == 0
    assert report["candidates"][0]["status"] == "needs-decision"
    assert evidence.relative_to(target).as_posix() in report["candidates"][0]["unclassified_sample"]
    code, plan = _plan(repo, tmp_path, target, capsys=capsys)
    assert code == 2 and not plan.exists() and evidence.is_file()


def test_environment_beneath_evidence_directory_requires_discard(repo, tmp_path, capsys):
    target = _legacy(repo)
    environment = target / "reports" / "analysis-env"
    (environment / "lib").mkdir(parents=True)
    (environment / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
    evidence = environment / "lib" / "result.json"
    evidence.write_text("retained evidence", encoding="utf-8")
    _age(target)
    code, report = _run("--repo", repo, capsys=capsys)
    assert code == 0
    assert report["candidates"][0]["status"] == "needs-decision"
    assert "reports/analysis-env/lib/result.json" in report["candidates"][0]["unclassified_sample"]
    code, plan = _plan(repo, tmp_path, target, capsys=capsys)
    assert code == 2 and not plan.exists() and evidence.is_file()


def test_non_ascii_worktree_is_discovered_without_git_path_quoting(repo, tmp_path, capsys):
    sibling = tmp_path / "worktree-\u00e9"
    _git(repo, "worktree", "add", "-q", str(sibling))
    target = _legacy(sibling)
    code, report = _run("--repo", repo, "--checkout", sibling, capsys=capsys)
    assert code == 0
    assert [row["path"] for row in report["candidates"]] == [str(target)]


def test_unclassified_content_requires_target_specific_discard(repo, tmp_path, capsys):
    target = _legacy(repo, evidence=True)
    code, plan = _plan(repo, tmp_path, target, capsys=capsys)
    assert code == 2 and not plan.exists()
    code, plan = _plan(repo, tmp_path, target, "--discard", target, capsys=capsys)
    assert code == 0
    assert json.loads(plan.read_text(encoding="utf-8"))["targets"][0]["discard_unclassified"]
    code, report = _run("--apply-plan", plan, "--confirm-writers-stopped", capsys=capsys)
    assert code == 0 and report["candidates"][0]["status"] == "reclaimed"


def test_discard_decision_cannot_be_dropped_from_plan(repo, tmp_path, capsys):
    target = _legacy(repo, evidence=True)
    code, plan = _plan(repo, tmp_path, target, "--discard", target, capsys=capsys)
    assert code == 0
    data = json.loads(plan.read_text(encoding="utf-8"))
    data["targets"][0]["discard_unclassified"] = False
    plan.write_text(json.dumps(data), encoding="utf-8")
    code, report = _run("--apply-plan", plan, "--confirm-writers-stopped", capsys=capsys)
    assert code == 1
    assert report["candidates"][0]["reason"] == "unclassified content without a discard decision"
    assert (target / "capture-probes" / "report.json").is_file()


def test_new_content_after_review_refuses(repo, tmp_path, capsys):
    target = _legacy(repo)
    code, plan = _plan(repo, tmp_path, target, capsys=capsys)
    assert code == 0
    (target / "new-evidence.json").write_text("{}", encoding="utf-8")
    _age(target)
    code, report = _run("--apply-plan", plan, "--confirm-writers-stopped", capsys=capsys)
    assert code == 1
    assert report["candidates"][0]["reason"] == "changed since the plan was reviewed"
    assert (target / "new-evidence.json").is_file()


def test_only_selected_checkout_is_deleted(repo, tmp_path, capsys):
    sibling = tmp_path / "sibling"
    _git(repo, "worktree", "add", "-q", str(sibling))
    mine, theirs = _legacy(repo), _legacy(sibling)
    code, report = _run("--repo", repo, "--checkout", sibling, capsys=capsys)
    assert [c["path"] for c in report["candidates"]] == [str(theirs)]
    code, plan = _plan(repo, tmp_path, mine, capsys=capsys)
    code, _report = _run("--apply-plan", plan, "--confirm-writers-stopped", capsys=capsys)
    assert code == 0 and not mine.exists() and theirs.is_dir()


def test_unregistered_checkout_selector_is_an_error(repo, tmp_path, capsys):
    code, _report = _run("--repo", repo, "--checkout", tmp_path, capsys=capsys)
    assert code == 2


def test_change_after_review_refuses_at_mutation(repo, tmp_path, capsys):
    target = _legacy(repo)
    code, plan = _plan(repo, tmp_path, target, capsys=capsys)
    (target / "linux" / "plugin" / "python").write_bytes(b"y" * 2048)
    _age(target)
    code, report = _run("--apply-plan", plan, "--confirm-writers-stopped", capsys=capsys)
    assert code == 1
    assert report["candidates"][0]["reason"] == "changed since the plan was reviewed"
    assert target.is_dir()


def test_replaced_directory_refuses_at_mutation(repo, tmp_path, capsys):
    target = _legacy(repo)
    code, plan = _plan(repo, tmp_path, target, capsys=capsys)
    os.rename(target, repo / "moved")
    os.rename(repo / "moved", tmp_path / "moved")
    _legacy(repo)
    code, report = _run("--apply-plan", plan, "--confirm-writers-stopped", capsys=capsys)
    assert report["candidates"][0]["reason"] == "changed since the plan was reviewed"
    assert target.is_dir()


def test_write_between_rename_and_removal_restores_tree(repo, tmp_path, capsys, monkeypatch):
    target = _legacy(repo)
    code, plan = _plan(repo, tmp_path, target, capsys=capsys)
    real = reclaim.survey

    def racing_survey(path):
        if ".reclaiming-" in path.name:
            (path / "linux" / "late.txt").write_text("x", encoding="utf-8")
        return real(path)

    monkeypatch.setattr(reclaim, "survey", racing_survey)
    code, report = _run("--apply-plan", plan, "--confirm-writers-stopped", capsys=capsys)
    assert report["candidates"][0]["reason"] == "changed since the plan was reviewed"
    assert (target / "linux" / "late.txt").is_file()
    assert not list(repo.glob(".test-venvs.reclaiming-*"))


def test_live_user_detected_after_rename_restores_tree(repo, tmp_path, capsys, monkeypatch):
    target = _legacy(repo)
    code, plan = _plan(repo, tmp_path, target, capsys=capsys)
    monkeypatch.setattr(reclaim, "in_use", lambda path, _accepted=frozenset(): (True, "in use by a live process") if ".reclaiming-" in path.name else (False, ""))
    code, report = _run("--apply-plan", plan, "--confirm-writers-stopped", capsys=capsys)
    assert report["candidates"][0]["reason"] == "in use by a live process"
    assert target.is_dir()


def test_recent_tree_is_refused_at_plan_and_apply(repo, tmp_path, capsys):
    target = _legacy(repo)
    code, plan = _plan(repo, tmp_path, target, capsys=capsys)
    _age(target, hours=1)
    code, report = _run("--apply-plan", plan, "--confirm-writers-stopped", capsys=capsys)
    assert report["candidates"][0]["reason"] == "recently modified"
    code, _plan_path = _plan(repo, tmp_path, target, capsys=capsys)
    assert code == 2 and target.is_dir()


def test_tracked_content_is_refused(repo, capsys):
    target = _legacy(repo)
    _git(repo, "add", "-f", ".test-venvs/linux/plugin/python")
    _code, report = _run("--repo", repo, capsys=capsys)
    assert report["candidates"][0]["reason"] == "contains tracked files"
    assert target.is_dir()


def test_inherited_git_selectors_cannot_hide_tracked_files(repo, tmp_path, capsys, monkeypatch):
    target = _legacy(repo)
    _git(repo, "add", "-f", ".test-venvs/linux/plugin/python")
    empty_index = tmp_path / "empty-index"
    monkeypatch.setenv("GIT_INDEX_FILE", str(empty_index))
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "elsewhere"))
    _code, report = _run("--repo", repo, capsys=capsys)
    assert report["candidates"][0]["reason"] == "contains tracked files"
    assert target.is_dir()


def test_unignored_directory_is_refused(repo, capsys):
    (repo / ".gitignore").write_text("", encoding="utf-8")
    _legacy(repo)
    _code, report = _run("--repo", repo, capsys=capsys)
    assert report["candidates"][0]["reason"] == "not ignored by Git"


def test_inner_link_is_refused_and_never_followed(repo, tmp_path, capsys, native_file_symlink):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    target = _legacy(repo)
    native_file_symlink(target / "escape", outside, target_is_directory=True)
    _code, report = _run("--repo", repo, capsys=capsys)
    assert report["candidates"][0]["reason"] == "contains a link or reparse point"
    assert (outside / "keep.txt").is_file() and target.is_dir()


def test_root_link_is_refused(repo, tmp_path, capsys, native_file_symlink):
    outside = tmp_path / "outside"
    outside.mkdir()
    native_file_symlink(repo / ".test-venvs", outside, target_is_directory=True)
    _code, report = _run("--repo", repo, capsys=capsys)
    assert report["candidates"][0]["reason"] == "not a plain directory"
    assert outside.is_dir()


def test_plan_paths_must_stay_registered_legacy_directories(repo, tmp_path, capsys):
    target = _legacy(repo)
    code, plan = _plan(repo, tmp_path, target, capsys=capsys)
    data = json.loads(plan.read_text(encoding="utf-8"))
    victim = repo / "src"
    victim.mkdir()
    data["targets"][0]["path"] = str(victim)
    plan.write_text(json.dumps(data), encoding="utf-8")
    code, report = _run("--apply-plan", plan, "--confirm-writers-stopped", capsys=capsys)
    assert report["candidates"][0]["reason"] == "not a legacy directory of a registered worktree"
    assert victim.is_dir()


def test_partial_removal_is_reported_and_remainder_restored(repo, tmp_path, capsys, monkeypatch):
    target = _legacy(repo)
    code, plan = _plan(repo, tmp_path, target, capsys=capsys)

    def failing_remove(path, errors):
        os.remove(path / "linux" / "plugin" / "python")
        errors.append("simulated failure")

    monkeypatch.setattr(reclaim, "_remove", failing_remove)
    code, report = _run("--apply-plan", plan, "--confirm-writers-stopped", capsys=capsys)
    [result] = report["candidates"]
    assert code == 1 and result["status"] == "partial"
    assert "entries may already be gone" in result["reason"]
    assert (target / "linux" / "plugin" / "pyvenv.cfg").is_file()
    assert not (target / "linux" / "plugin" / "python").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows exclusive rename")
def test_open_handle_blocks_reclaim_on_windows(repo, tmp_path, capsys):
    target = _legacy(repo)
    code, plan = _plan(repo, tmp_path, target, capsys=capsys)
    with open(target / "linux" / "plugin" / "python", "rb"):
        _code, report = _run("--apply-plan", plan, "--confirm-writers-stopped", capsys=capsys)
    assert "rename failed" in report["candidates"][0]["reason"]
    assert target.is_dir()


def test_unverifiable_liveness_refuses(repo, capsys, monkeypatch):
    target = _legacy(repo)
    monkeypatch.setattr(reclaim, "in_use",
                        lambda _path, _accepted=frozenset(): (None, "process use cannot be verified"))
    _code, report = _run("--repo", repo, capsys=capsys)
    assert report["candidates"][0]["reason"] == "process use cannot be verified"
    assert target.is_dir()


def test_unsupported_platform_cannot_verify_liveness(monkeypatch, tmp_path):
    class _NoProc:
        def is_dir(self):
            return False

    monkeypatch.setattr(reclaim, "Path", lambda *args: _NoProc())
    assert reclaim._linux_scan(tmp_path) is None


class _FakeProc:
    """Minimal ``/proc/<pid>`` stand-in for exercising inspection failures."""

    def __init__(self, name, uid, fd_error=None, comm="worker", maps="", maps_error=None):
        self.name, self._uid, self._fd_error, self.comm = name, uid, fd_error, comm
        self.maps, self.maps_error = maps, maps_error

    def stat(self):
        return type("S", (), {"st_uid": self._uid})()

    def __truediv__(self, part):
        return _FakeLink(self, part)


class _FakeLink:
    def __init__(self, proc, part):
        self.proc, self.part = proc, part

    def iterdir(self):
        if self.proc._fd_error:
            raise self.proc._fd_error
        return iter([])

    def read_text(self, **_kwargs):
        if self.part == "maps":
            if self.proc.maps_error:
                raise self.proc.maps_error
            return self.proc.maps
        return self.proc.comm + "\n"

    def __fspath__(self):
        return f"/proc/{self.proc.name}/{self.part}"


def _fake_linux(monkeypatch, procs, readlink=lambda _link: "/elsewhere"):
    real_readlink = reclaim.os.readlink

    def proc_readlink(link, *args, **kwargs):
        if os.fsdecode(link).startswith("/proc/"):
            return readlink(link)
        return real_readlink(link, *args, **kwargs)

    monkeypatch.setattr(reclaim.os, "getuid", lambda: 7, raising=False)
    monkeypatch.setattr(reclaim, "Path", _patched_path(procs))
    monkeypatch.setattr(reclaim.os, "readlink", proc_readlink)
    monkeypatch.setattr(reclaim, "_WINDOWS", False)
    monkeypatch.setattr(reclaim, "in_use", _REAL_IN_USE)


@pytest.mark.parametrize("error,expected", [
    (PermissionError("denied"), (False, {"sshd"})),
    (FileNotFoundError("gone"), (False, set())),
])
def test_linux_fd_inspection_failure_is_not_absence(monkeypatch, tmp_path, error, expected):
    _fake_linux(monkeypatch, [_FakeProc("10", 7, fd_error=error, comm="sshd"), _FakeProc("11", 8)])
    assert reclaim._linux_scan(tmp_path) == expected


def test_linux_readlink_failure_is_not_absence(monkeypatch, tmp_path):
    def readlink(_link):
        raise PermissionError("denied")

    _fake_linux(monkeypatch, [_FakeProc("10", 7, comm="systemd")], readlink)
    assert reclaim._linux_scan(tmp_path) == (False, {"systemd"})
    use, why = reclaim.in_use(tmp_path)
    assert use is None and why.endswith("cannot inspect: systemd")


def test_accepted_uninspectable_process_is_explicit(monkeypatch, tmp_path):
    _fake_linux(monkeypatch, [_FakeProc("10", 7, fd_error=PermissionError("x"), comm="sshd"),
                              _FakeProc("12", 7, fd_error=PermissionError("x"), comm="python3")])
    assert reclaim.in_use(tmp_path, frozenset({"sshd"}))[0] is None
    assert reclaim.in_use(tmp_path, frozenset({"sshd", "python3"})) == (False, "")


def test_linux_mapping_without_open_descriptor_is_detected(monkeypatch, tmp_path):
    mapped = os.path.join(os.path.realpath(tmp_path), "mapped library.so")
    _fake_linux(monkeypatch, [_FakeProc("10", 7, maps=f"100-200 r--p 0 00:01 1 {mapped}\n")])
    assert reclaim._linux_scan(tmp_path) == (True, set())


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink namespace")
@pytest.mark.parametrize("source", ["exe", "maps"])
def test_linux_targets_are_normalized_through_aliases(monkeypatch, tmp_path, source):
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "held").write_bytes(b"x")
    alias = tmp_path / "alias"
    alias.symlink_to(tree, target_is_directory=True)
    target = str(alias / "held")
    maps = f"100-200 r--p 0 00:01 1 {target}\n" if source == "maps" else ""
    _fake_linux(
        monkeypatch, [_FakeProc("10", 7, maps=maps)],
        lambda link: target if source == "exe" and os.fspath(link).endswith("/exe") else "/elsewhere",
    )
    assert reclaim._linux_scan(tree)[0] is True


@pytest.mark.parametrize("target", ["socket:[123]", "anon_inode:[eventpoll]"])
def test_non_path_process_targets_are_not_resolved_under_the_current_directory(
    monkeypatch, tmp_path, target,
):
    monkeypatch.chdir(tmp_path)
    _fake_linux(monkeypatch, [_FakeProc("10", 7)], lambda _link: target)
    assert reclaim._linux_scan(tmp_path) == (False, set())


def test_unreadable_linux_maps_is_not_absence(monkeypatch, tmp_path):
    _fake_linux(monkeypatch, [_FakeProc("10", 7, maps_error=PermissionError("denied"))])
    assert reclaim.in_use(tmp_path)[0] is None


def test_linux_scan_detects_cwd_inside_tree(monkeypatch, tmp_path):
    inside = os.path.join(os.path.realpath(tmp_path), "child")
    _fake_linux(monkeypatch, [_FakeProc("10", 7)],
                lambda link: inside if str(os.fspath(link)).endswith("cwd") else "/x")
    assert reclaim._linux_scan(tmp_path) == (True, set())
    assert reclaim.in_use(tmp_path) == (True, "in use by a live process")


def test_accepted_names_are_recorded_in_plan(repo, tmp_path, capsys):
    target = _legacy(repo)
    code, plan = _plan(repo, tmp_path, target, "--accept-uninspectable", "sshd", capsys=capsys)
    assert code == 0
    assert json.loads(plan.read_text(encoding="utf-8"))["targets"][0]["accepted_uninspectable"] == ["sshd"]


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="Linux /proc scan")
def test_real_proc_scan_detects_child_cwd(tmp_path):
    tree = tmp_path / ".test-venvs"
    tree.mkdir()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], cwd=tree)
    try:
        deadline = time.time() + 10
        while reclaim._linux_scan(tree)[0] is not True and time.time() < deadline:
            time.sleep(0.1)
        assert reclaim._linux_scan(tree)[0] is True
    finally:
        child.kill()
        child.wait()
    assert reclaim._linux_scan(tree)[0] is False


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="Linux /proc scan")
def test_real_proc_scan_detects_closed_descriptor_mapping(tmp_path):
    tree = tmp_path / ".test-venvs"
    tree.mkdir()
    mapped = tree / "mapped.bin"
    mapped.write_bytes(b"x" * 4096)
    code = (
        "import ctypes,mmap,os,sys,time\n"
        "libc=ctypes.CDLL(None,use_errno=True)\n"
        "libc.mmap.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_int,"
        "ctypes.c_int,ctypes.c_int,ctypes.c_long]\n"
        "libc.mmap.restype=ctypes.c_void_p\n"
        "fd=os.open(sys.argv[1],os.O_RDONLY)\n"
        "address=libc.mmap(None,4096,mmap.PROT_READ,mmap.MAP_PRIVATE,fd,0)\n"
        "os.close(fd)\n"
        "assert address != ctypes.c_void_p(-1).value, ctypes.get_errno()\n"
        "print('ready',flush=True)\n"
        "time.sleep(30)\n"
    )
    child = subprocess.Popen([sys.executable, "-c", code, str(mapped)],
                             stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        for descriptor in (Path("/proc") / str(child.pid) / "fd").iterdir():
            target = os.readlink(descriptor)
            assert not (
                target == str(tree.resolve())
                or target.startswith(str(tree.resolve()) + os.sep)
            )
        assert reclaim._linux_scan(tree)[0] is True
    finally:
        child.kill()
        child.wait()
        child.stdout.close()
    assert reclaim._linux_scan(tree)[0] is False


def _patched_path(procs):
    real = Path

    class _ProcRoot:
        def is_dir(self):
            return True

        def iterdir(self):
            return iter(procs)

    def factory(*args):
        if args == ("/proc",):
            return _ProcRoot()
        return real(*args)

    return factory
