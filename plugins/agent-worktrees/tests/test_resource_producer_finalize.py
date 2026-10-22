"""Actual finalize and cross-process producer/finalizing lock regression."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

import pytest

from conftest import _published_seed_environment, _seed_directory
from agent_worktrees import config as cfg, finalize, tracking
from test_resource_producer import absence, call
from test_transitive_finalize_integration import MACHINE, PROJECT, _env, _git, _save_worktree


@pytest.fixture(scope="session")
def _final_owner_seed(tmp_path_factory):
    with _seed_directory(tmp_path_factory, "resource-producer-finalize") as (seed, build):
        if build:
            with _published_seed_environment(tmp_path_factory) as patch:
                _env.__wrapped__(seed, patch)
        return seed


def _copy_final_owner(seed, root, monkeypatch):
    for name in ("anchor", "origin.git"):
        shutil.copytree(seed / name, root / name, copy_function=shutil.copyfile)
    records = root / f".{PROJECT}" / "worktrees"
    records.mkdir(parents=True)
    monkeypatch.setattr(cfg, "tracking_dir", lambda: records)
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: root / f".{PROJECT}")
    monkeypatch.setattr(cfg, "install_dir", lambda: root / ".agent-worktrees")
    _git("remote", "set-url", "origin", str(root / "origin.git"), cwd=root / "anchor")
    config = cfg.Config(
        srcroot=str(root), machine=MACHINE, platform="linux",
        repo_name=PROJECT, repos={PROJECT: cfg.RepoConfig(
            anchor=str(root / "anchor"), worktree_root=str(root),
            default_branch="base", remote="origin",
        )},
    )
    _save_worktree(records, "owner")
    path = records / "owner.yaml"
    record = tracking.load_record(path)
    record.repo = config.repo_name
    tracking.save_record(record, path)
    monkeypatch.setattr(cfg, "load_project_config", lambda *a, **k: config)
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    return root, path, config


@pytest.fixture
def final_owner(tmp_path, monkeypatch, _final_owner_seed):
    return _copy_final_owner(_final_owner_seed, tmp_path, monkeypatch)


def _reserve(path, config):
    return call(path, "reserve", {"kind": "container", "namespace": "raw-resource", "recovery": {}},
                ref=tracking.format_claim_ref(config.machine, config.repo_name, "owner"))


def _owner_snapshot(root):
    return {path.relative_to(root): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("attempt", range(2))
def test_final_owner_copies_keep_git_and_owner_state_private(_final_owner_seed, tmp_path, attempt):
    from agent_worktrees import claim_providers

    seed = _final_owner_seed
    seed_before = _owner_snapshot(seed)
    first_root = tmp_path / f"first-{attempt}"
    second_root = tmp_path / f"second-{attempt}"
    with pytest.MonkeyPatch.context() as patch:
        _, first_path, first_config = _copy_final_owner(seed, first_root, patch)
    with pytest.MonkeyPatch.context() as patch:
        _, second_path, second_config = _copy_final_owner(seed, second_root, patch)
        assert cfg.tracking_dir() == second_path.parent
        assert cfg.project_dir() == second_path.parent.parent
        assert cfg.install_dir() == second_root / ".agent-worktrees"
        assert cfg.load_config() is second_config
    second_before = _owner_snapshot(second_root)
    for root, path, config in (
        (first_root, first_path, first_config), (second_root, second_path, second_config),
    ):
        anchor = root / "anchor"
        assert config.default_repo.anchor == str(anchor)
        assert config.default_repo.worktree_root == config.srcroot == str(root)
        assert _git("remote", "get-url", "origin", cwd=anchor) == str(root / "origin.git")
        assert _git("rev-parse", "HEAD", cwd=anchor) == _git(
            "--git-dir", str(root / "origin.git"), "rev-parse", "base", cwd=root,
        )
        assert (anchor / "base.txt").read_text() == "base\n"
        record = tracking.load_record(path)
        assert record.status == "active" and record.resources == []
        assert record.worktree_path == str(root / "gone-owner")
        for relative, data in seed_before.items():
            if relative.parts[0] in {"anchor", "origin.git"}:
                copied = root / relative
                if relative.parts != ("anchor", ".git", "config"):
                    assert copied.read_bytes() == data
                assert not copied.samefile(seed / relative)
                assert relative.name != "alternates"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(cfg, "tracking_dir", lambda: first_path.parent)
        patch.setattr(cfg, "project_dir", lambda name=None: first_path.parent.parent)
        patch.setattr(cfg, "install_dir", lambda: first_root / ".agent-worktrees")
        patch.setattr(cfg, "load_project_config", lambda *a, **k: first_config)
        patch.setattr(cfg, "load_config", lambda *a, **k: first_config)
        anchor = first_root / "anchor"
        _git("config", "fixture.private", "first-only", cwd=anchor)
        (anchor / ".git" / "info" / "exclude").write_text("first-only\n")
        (anchor / "private.txt").write_text("first copy only\n")
        _git("add", "-A", cwd=anchor)
        _git("commit", "-q", "-m", "private copy mutation", cwd=anchor)
        _git("push", "-q", "origin", "HEAD:refs/heads/private-copy", cwd=anchor)
        result = _reserve(first_path, first_config)
        assert result["phase"] == "pending"
        assert finalize.validate_and_finalize("owner", first_config) is False
        patch.setattr(claim_providers, "resolve_claim_status", lambda ref: absence(ref))
        assert call(first_path, "close", {"token": result["token"]},
                    ref=result["owner"])["phase"] == "closed"
        patch.setattr(finalize, "_warn_of_codespace_claims_for_worktree", lambda *a: None)
        patch.setattr(finalize, "_warn_of_dev_slot_claims_for_worktree", lambda *a: None)
        assert finalize.validate_and_finalize("owner", first_config) is True
        assert tracking.load_record(first_path).status == "finalized"
    assert _owner_snapshot(seed) == seed_before
    assert _owner_snapshot(second_root) == second_before
    assert first_config.repos is not second_config.repos
    assert first_config.default_repo is not second_config.default_repo


@pytest.mark.parametrize("mode", ["off", "warn", "block"])
@pytest.mark.parametrize("abandon", [False, True])
@pytest.mark.parametrize("allocation_started", [False, True])
def test_real_finalize_blocks_initially_empty_owner_after_reserve(final_owner, monkeypatch, mode, abandon, allocation_started):
    _, path, config = final_owner
    assert tracking.load_record(path).resources == []
    monkeypatch.setenv("AGENT_WORKTREES_OBLIGATION_GATE", mode)
    result = _reserve(path, config)
    assert result["phase"] == "pending"
    if allocation_started:
        armed = call(path, "begin", {"token": result["token"]}, ref=result["owner"])
        assert armed["phase"] == "allocating"
    assert finalize.validate_and_finalize("owner", config, abandon=abandon, handoff_to="recipient" if abandon else None) is False
    current = tracking.load_record(path)
    assert current.status == "active" and current.resources[0].is_unsettled


def test_actual_final_locked_gate_rejects_late_producer(final_owner, monkeypatch):
    _, path, config = final_owner
    check = finalize._assert_obligations_settled
    def initial_check(record, *args, **kwargs):
        assert check(record, *args, **kwargs) is True
        assert _reserve(path, config)["ok"]
        return True
    monkeypatch.setattr(finalize, "_assert_obligations_settled", initial_check)
    assert finalize.validate_and_finalize("owner", config, abandon=True, handoff_to="recipient") is False
    assert tracking.load_record(path).status == "active"
    assert tracking.load_record(path).resources[0].is_unsettled


def test_confirmed_close_then_actual_finalize(final_owner, monkeypatch):
    from agent_worktrees import claim_providers

    _, path, config = final_owner
    result = _reserve(path, config)
    assert finalize.validate_and_finalize("owner", config) is False
    monkeypatch.setattr(claim_providers, "resolve_claim_status", lambda ref: absence(ref))
    closed = call(path, "close", {"token": result["token"]}, ref=result["owner"])
    assert closed["phase"] == "closed"
    monkeypatch.setattr(finalize, "_warn_of_codespace_claims_for_worktree", lambda *a: None)
    monkeypatch.setattr(finalize, "_warn_of_dev_slot_claims_for_worktree", lambda *a: None)
    assert finalize.validate_and_finalize("owner", config) is True
    current = tracking.load_record(path)
    assert current.status == "finalized" and current.resources[0].producer["phase"] == "closed"


def test_separate_process_producer_waits_for_lock_then_refuses_finalizing(final_owner):
    root, path, config = final_owner
    signal = root / "producer-entered"
    code = """
import json, sys
from pathlib import Path
from agent_worktrees import config as cfg, tracking
from agent_worktrees.tracking_producer_write import apply_resource_producer
path, signal, machine, project = sys.argv[1:]
config = cfg.Config(srcroot=str(Path(path).parent.parent.parent), machine=machine,
 platform="linux", repo_name=project, repos={project: cfg.RepoConfig(
 anchor=str(Path(path).parent), worktree_root=str(Path(path).parent))})
cfg.load_project_config = lambda *a, **k: config
cfg.project_dir = lambda *a, **k: Path(path).parent.parent
Path(signal).write_text("entered")
print(json.dumps(apply_resource_producer({
 "schema":"copilot-extensions.resource-producer", "version":1,
 "operation":"reserve", "input":{"kind":"container","namespace":"raw-resource","recovery":{}},
 "owner_ref":tracking.format_claim_ref(machine,project,"owner"), "yaml_path":path})))
"""
    # A real separate interpreter competes for the OS sidecar, not a mock or
    # just a thread sharing the in-process RLock.
    with tracking._RecordLock(path, require_sidecar=True):
        child = subprocess.Popen(
            [sys.executable, "-c", code, str(path), str(signal), config.machine, config.repo_name],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=dict(os.environ),
        )
        deadline = time.monotonic() + 10
        while not signal.exists() and time.monotonic() < deadline:
            if child.poll() is not None:
                break
            time.sleep(0.02)
        if not signal.exists() or child.poll() is not None:
            stdout, stderr = child.communicate(timeout=15)
            pytest.fail(f"producer subprocess did not reach locked operation: {stdout}\n{stderr}")
        record = tracking.load_record(path)
        record.status = "finalizing"
        tracking.save_record(record, path)
    stdout, stderr = child.communicate(timeout=15)
    assert child.returncode == 0, stderr
    assert json.loads(stdout)["error"] == "rejected"
    assert tracking.load_record(path).resources == []
