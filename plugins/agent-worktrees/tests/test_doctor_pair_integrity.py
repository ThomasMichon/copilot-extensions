"""Pair-integrity coverage for ``agent-worktrees doctor``."""

from __future__ import annotations

import json
import types
from pathlib import Path

import yaml

from agent_worktrees import __main__ as main
from agent_worktrees import config as cfg
from agent_worktrees import health
from agent_worktrees import tracking


def _record(
    tracking_dir: Path,
    *,
    worktree_id: str,
    repo: str,
    path: Path,
    role: str,
    pair_ref: str,
) -> tracking.WorktreeRecord:
    path.mkdir(parents=True, exist_ok=True)
    return tracking.create_new_record(
        worktree_id=worktree_id,
        branch=f"worktree/{worktree_id}",
        worktree_path=str(path),
        repo=repo,
        machine="host",
        platform_name="windows",
        tracking_path=tracking_dir,
        pair_id="pair-1",
        pair_role=role,
        pair_ref=pair_ref,
        pair_kind="worktree",
    )


def test_doctor_repairs_misplaced_knowledge_record(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "_home", lambda: tmp_path)
    harness_dir = cfg.project_dir("harness") / "worktrees"
    knowledge_dir = cfg.project_dir("knowledge") / "worktrees"
    harness_path = tmp_path / "harness.worktrees" / "wt-h"
    knowledge_path = tmp_path / "knowledge.worktrees" / "wt-k"

    _record(
        harness_dir,
        worktree_id="wt-h",
        repo="harness",
        path=harness_path,
        role="harness",
        pair_ref="host/knowledge/wt-k",
    )
    _record(
        harness_dir,
        worktree_id="wt-k",
        repo="knowledge",
        path=knowledge_path,
        role="knowledge",
        pair_ref="host/harness/wt-h",
    )

    findings = health.audit_pair_integrity(
        ["harness", "knowledge"],
        apply=False,
    )
    assert len(findings) == 1
    assert findings[0].worktree_id == "wt-k"
    assert findings[0].repairable is True
    assert tracking.load_record_by_id(
        "wt-k", tracking_path=knowledge_dir
    ) is None

    fixed = health.audit_pair_integrity(
        ["harness", "knowledge"],
        apply=True,
    )
    assert fixed[0].repaired is True
    assert tracking.load_record_by_id(
        "wt-k", tracking_path=knowledge_dir
    ) is not None
    assert tracking.load_record_by_id(
        "wt-k", tracking_path=harness_dir
    ) is not None


def test_doctor_discovers_misplaced_record_from_knowledge_cwd(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(cfg, "_home", lambda: tmp_path)
    harness_dir = cfg.project_dir("harness") / "worktrees"
    knowledge_path = tmp_path / "knowledge.worktrees" / "wt-k"
    expected = _record(
        harness_dir,
        worktree_id="wt-k",
        repo="knowledge",
        path=knowledge_path,
        role="knowledge",
        pair_ref="host/harness/wt-h",
    )

    found = health.find_record_by_cwd_across_projects(
        str(knowledge_path),
        ["harness", "knowledge"],
    )
    assert found is not None
    assert found.worktree_id == expected.worktree_id
    assert found.repo == "knowledge"


def test_doctor_json_repairs_pair_from_untracked_knowledge_cwd(
    tmp_path, monkeypatch, capfd
):
    monkeypatch.setattr(cfg, "_home", lambda: tmp_path)
    cfg.set_active_project(None)
    projects_path = cfg.install_dir() / "projects.yaml"
    projects_path.parent.mkdir(parents=True, exist_ok=True)
    projects_path.write_text(
        yaml.safe_dump({
            "projects": {
                "harness": {},
                "knowledge": {},
                "../invalid": {},
            }
        }),
        encoding="utf-8",
    )
    harness_dir = cfg.project_dir("harness") / "worktrees"
    knowledge_dir = cfg.project_dir("knowledge") / "worktrees"
    harness_path = tmp_path / "harness.worktrees" / "wt-h"
    knowledge_path = tmp_path / "knowledge.worktrees" / "wt-k"
    _record(
        harness_dir,
        worktree_id="wt-h",
        repo="harness",
        path=harness_path,
        role="harness",
        pair_ref="host/knowledge/wt-k",
    )
    _record(
        harness_dir,
        worktree_id="wt-k",
        repo="knowledge",
        path=knowledge_path,
        role="knowledge",
        pair_ref="host/harness/wt-h",
    )
    monkeypatch.chdir(knowledge_path)
    monkeypatch.setattr(main.reclaim, "find_bare_orphans", lambda: [])
    monkeypatch.setattr(main, "_find_repo_dir", lambda: None)

    rc = main.cmd_doctor(types.SimpleNamespace(
        fix=True,
        gc_sessions=False,
        json=True,
        projection_budget=0,
    ))
    payload = json.loads(capfd.readouterr().out)

    assert rc == 0
    assert payload["project"] == "knowledge"
    assert payload["project_health_available"] is True
    assert payload["pair_integrity"]["found"] == 1
    assert payload["pair_integrity"]["repaired"] == 1
    assert tracking.load_record_by_id(
        "wt-k", tracking_path=knowledge_dir
    ) is not None
