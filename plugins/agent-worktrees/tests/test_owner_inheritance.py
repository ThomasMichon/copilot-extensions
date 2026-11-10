"""Owner identity comes from explicit arguments or CWD, never launch env."""
from __future__ import annotations

import argparse
import json
import types
from pathlib import Path

import pytest


from agent_worktrees import __main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import state_root
from agent_worktrees import worktree_ops_cli

# ── _build_env producer ──────────────────────────────────────────────────────

def test_build_env_does_not_export_parent_identity_for_worktree(monkeypatch):
    monkeypatch.setattr(cfg, "project_dir", lambda: Path("/proj"))
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "parent-session")
    env = m._build_env(None, None, work_dir="/wt/x")
    assert "AGENT_WORKTREES_OWNER_REF" not in env


def test_build_env_omits_owner_ref_for_anchor(monkeypatch):
    monkeypatch.setattr(cfg, "project_dir", lambda: Path("/proj"))
    env = m._build_env(None, None, work_dir="/repo/anchor")
    assert "AGENT_WORKTREES_OWNER_REF" not in env


def test_build_env_omits_owner_ref_without_work_dir(monkeypatch):
    monkeypatch.setattr(cfg, "project_dir", lambda: Path("/proj"))
    env = m._build_env(None, None)
    assert "AGENT_WORKTREES_OWNER_REF" not in env


# ── cmd_create owner resolution (explicit > cwd; --no-owner / system) ──

def _create_args(**kw):
    ns = argparse.Namespace(
        system=False, no_owner=False, owner_ref=None, owner=None, name=None,
        interface=None, origin=None, json=False,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _patch_core(monkeypatch, captured):
    monkeypatch.setattr(m.cfg, "load_config", lambda: types.SimpleNamespace())

    def fake_core(config, **kw):
        captured["owner_ref"] = kw.get("owner_ref")
        captured["inherit_parent_session"] = kw.get("inherit_parent_session")
        return {"worktree": {"id": "w", "path": "p", "branch": "b"}}

    monkeypatch.setattr(m, "_create_worktree_core", fake_core)
    # cwd inference: pretend the caller is inside worktree "cwd/p/w"
    monkeypatch.setattr(worktree_ops_cli, "_resolve_owner_ref", lambda: "cwd/proj/parent")


def test_cmd_create_explicit_owner_ref_wins(monkeypatch):
    captured: dict = {}
    _patch_core(monkeypatch, captured)
    monkeypatch.setenv("AGENT_WORKTREES_OWNER_REF", "env/proj/parent")
    m.cmd_create(_create_args(owner_ref="explicit/proj/parent"))
    assert captured["owner_ref"] == "explicit/proj/parent"


def test_cmd_create_ignores_inherited_owner_in_favor_of_cwd(monkeypatch):
    captured: dict = {}
    _patch_core(monkeypatch, captured)
    monkeypatch.setenv("AGENT_WORKTREES_OWNER_REF", "env/proj/parent")
    m.cmd_create(_create_args())
    assert captured["owner_ref"] == "cwd/proj/parent"


def test_destination_project_does_not_relabel_cwd_owner(monkeypatch, tmp_path):
    from agent_worktrees import create_destination, execution_spaces, worktree_identity

    monkeypatch.setattr(m, "_INVOCATION_CWD", tmp_path)
    monkeypatch.setattr(cfg, "project_name", lambda: "destination")
    monkeypatch.setattr(create_destination, "_cwd_project", lambda cwd: "origin")
    monkeypatch.setattr(cfg, "load_project_config", lambda project: types.SimpleNamespace(
        default_repo=types.SimpleNamespace(anchor=str(tmp_path / project)),
    ))
    seen = []
    def record(cwd, *, project, anchor):
        seen.append((cwd, project, anchor))
        return types.SimpleNamespace(
            execution_machine="host", repo="origin", worktree_id="parent",
        )
    monkeypatch.setattr(worktree_identity, "tracked_invocation_record", record)
    monkeypatch.setattr(execution_spaces, "require_record_mutation", lambda *args: None)
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "current")
    monkeypatch.setenv("AGENT_WORKTREES_OWNER_REF", "stale/other/wt#old")
    assert worktree_ops_cli._resolve_owner_ref() == "host/origin/parent#current"
    assert seen == [(tmp_path, "origin", tmp_path / "origin")]


@pytest.mark.parametrize("kind", ["worktree", "singleton"])
def test_cross_project_origin_never_reads_destination_ledger(monkeypatch, tmp_path, kind):
    from agent_worktrees import create_destination, execution_spaces, repos, worktree_identity

    roots = {project: tmp_path / project for project in ("origin", "destination")}
    project_dirs = {project: tmp_path / f".{project}" for project in roots}
    worktree_id = "parent" if kind == "worktree" else "@anchor"
    records = []
    for project, root in roots.items():
        root.mkdir()
        directory = project_dirs[project] / "worktrees"
        directory.mkdir(parents=True)
        path = directory / f"{worktree_id}.yaml"
        path.write_text(json.dumps({
            "worktree_id": worktree_id, "worktree_path": str(root), "branch": "main",
            "repo": project, "machine": f"{project}-exec", "platform": "linux",
        }), encoding="utf-8")
        records.append((path, path.read_bytes()))
    monkeypatch.setattr(m, "_INVOCATION_CWD", roots["origin"])
    monkeypatch.setattr(cfg, "project_name", lambda: "destination")
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: project_dirs[name or "destination"])
    monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: project_dirs[name or "destination"] / "worktrees")
    monkeypatch.setattr(create_destination, "_cwd_project", lambda cwd: "origin")
    monkeypatch.setattr(cfg, "load_project_config", lambda project: types.SimpleNamespace(
        repo_name=project, default_repo=types.SimpleNamespace(
            anchor=str(roots[project] / "anchor" if kind == "worktree" else roots[project]),
        ),
    ))
    monkeypatch.setattr(worktree_identity, "_worktree_id_from_git", lambda *args, **kwargs: (
        worktree_id if kind == "worktree" else None
    ))
    monkeypatch.setattr(execution_spaces, "require_record_mutation", lambda *args: None)
    monkeypatch.setattr(repos, "find_repo", lambda project: repos.RepoEntry(
        name=project, repo_class="singleton",
        paths={platform: str(roots[project]) for platform in ("windows", "linux", "wsl")},
    ))
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "current")
    assert worktree_ops_cli._resolve_owner_ref() == f"origin-exec/origin/{worktree_id}#current"
    assert all(path.read_bytes() == before for path, before in records)


def test_run_uses_the_cwd_owner_project_for_locality_and_admission(monkeypatch):
    from agent_worktrees import execution_spaces

    destination = types.SimpleNamespace(machine="destination-exec", repo_name="destination")
    origin = types.SimpleNamespace(machine="origin-exec", repo_name="origin")
    monkeypatch.setattr(m, "_resolve_owner_ref", lambda: "origin-exec/origin/parent")
    monkeypatch.setattr(cfg, "load_config", lambda: destination)
    monkeypatch.setattr(cfg, "project_name", lambda: "destination")
    monkeypatch.setattr(cfg, "load_project_config", lambda project: origin if project == "origin" else None)
    monkeypatch.setattr(execution_spaces, "require_owner_identity", lambda machine, config: config is origin)
    seen = []
    def readiness(ref, config):
        seen.append((ref, config))
        return types.SimpleNamespace(ready=False, code="owner-not-ready", error="fixture")
    monkeypatch.setattr(worktree_ops_cli.claims_cli, "_coordination_readiness_for_owner_ref", readiness)
    assert worktree_ops_cli.cmd_run(types.SimpleNamespace(
        inner_command=["unused-producer"], owner_ref=None,
    )) == 3
    assert seen == [("origin-exec/origin/parent", origin)]


def test_cmd_create_cwd_fallback_closes_the_gap(monkeypatch):
    captured: dict = {}
    _patch_core(monkeypatch, captured)
    monkeypatch.delenv("AGENT_WORKTREES_OWNER_REF", raising=False)
    m.cmd_create(_create_args())
    # A plain nested create is now auto-parented from the CWD.
    assert captured["owner_ref"] == "cwd/proj/parent"


def test_cmd_create_no_owner_forces_top_level(monkeypatch):
    captured: dict = {}
    _patch_core(monkeypatch, captured)
    monkeypatch.setenv("AGENT_WORKTREES_OWNER_REF", "env/proj/parent")
    m.cmd_create(_create_args(no_owner=True))
    assert captured["owner_ref"] is None
    assert captured["inherit_parent_session"] is False


def test_cmd_create_system_is_never_owned(monkeypatch):
    captured: dict = {}
    _patch_core(monkeypatch, captured)
    monkeypatch.setenv("AGENT_WORKTREES_OWNER_REF", "env/proj/parent")
    m.cmd_create(_create_args(system=True, name="svc"))
    assert captured["owner_ref"] is None
    assert captured["inherit_parent_session"] is False


def test_cmd_create_normal_worktree_inherits_parent_session(monkeypatch):
    captured: dict = {}
    _patch_core(monkeypatch, captured)
    m.cmd_create(_create_args())
    assert captured["inherit_parent_session"] is True


def test_creation_parent_session_suppresses_only_ambient_value(monkeypatch):
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "ambient-session")
    assert m._creation_parent_session(
        None, inherit_ambient=False
    ) is None
    assert m._creation_parent_session(
        None, inherit_ambient=True
    ) == "ambient-session"
    assert m._creation_parent_session(
        "explicit-session", inherit_ambient=False
    ) == "explicit-session"
    assert m._creation_parent_session(
        "", inherit_ambient=True
    ) == ""


def test_cmd_create_emits_structured_coordination_rejection(
    monkeypatch,
    capfd,
):
    monkeypatch.setattr(m.cfg, "load_config", lambda: types.SimpleNamespace())
    root = state_root.StateRoot(
        None,
        "knowledge_repo",
        "",
        True,
        True,
        False,
        error="no knowledge_repo is bound",
    )
    readiness = state_root.CoordinationReadiness(
        False,
        "knowledge_binding_required",
        root,
        error="bind the knowledge repository",
    )
    monkeypatch.setattr(
        m,
        "_create_worktree_core",
        lambda *a, **k: (_ for _ in ()).throw(
            m.CoordinationReadinessFailure(readiness)
        ),
    )

    rc = m.cmd_create(_create_args(
        owner_ref="machine/project/worktree",
        json=True,
    ))

    assert rc == 3
    payload = json.loads(capfd.readouterr().out)
    assert payload["code"] == "knowledge_binding_required"
    assert payload["coordination_readiness"]["version"] == 1
