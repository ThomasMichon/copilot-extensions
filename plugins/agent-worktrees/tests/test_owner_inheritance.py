"""Ph6 ambient owner identity + claim inheritance.

The producer (``_build_env`` exporting ``AGENT_WORKTREES_OWNER_REF`` for a
launched worktree via ``_self_owner_ref``) and the consumer (``cmd_create``
resolving explicit > env > cwd, with ``--no-owner`` opt-out).
"""
from __future__ import annotations

import argparse
import json
import types
from pathlib import Path

import pytest

from agent_worktrees import __main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import state_root

# ── _self_owner_ref ──────────────────────────────────────────────────────────

def test_self_owner_ref_none_for_empty():
    assert m._self_owner_ref(None) is None
    assert m._self_owner_ref("") is None


def test_self_owner_ref_none_for_non_worktree(monkeypatch):
    # A path that resolves to no worktree (e.g. the anchor) is top-level.
    monkeypatch.setattr(m.tracking, "find_worktree_id_by_cwd", lambda p: None)
    assert m._self_owner_ref("/repo/anchor") is None


def test_self_owner_ref_composes_qualified_ref(monkeypatch):
    monkeypatch.setattr(m, "_worktree_id_from_git", lambda p: None)
    monkeypatch.setattr(m.tracking, "find_worktree_id_by_cwd", lambda p: "wt-x")
    monkeypatch.setattr(m.cfg, "load_config",
                        lambda: types.SimpleNamespace(machine="dev6", repo_name="proj"))
    monkeypatch.setattr(m.cfg, "project_name", lambda: "proj")
    monkeypatch.delenv("COPILOT_AGENT_SESSION_ID", raising=False)
    assert m._self_owner_ref("/wt/x") == "dev6/proj/wt-x"


def test_self_owner_ref_prefers_git_identity(monkeypatch):
    # git-identity is authoritative; the tracked-path match is only a fallback.
    monkeypatch.setattr(m, "_worktree_id_from_git", lambda p: "wt-git")
    monkeypatch.setattr(m.tracking, "find_worktree_id_by_cwd",
                        lambda p: pytest.fail("should not reach tracked-path fallback"))
    monkeypatch.setattr(m.cfg, "load_config",
                        lambda: types.SimpleNamespace(machine="dev6", repo_name="proj"))
    monkeypatch.setattr(m.cfg, "project_name", lambda: "proj")
    monkeypatch.delenv("COPILOT_AGENT_SESSION_ID", raising=False)
    assert m._self_owner_ref("/wt/x") == "dev6/proj/wt-git"


def test_self_owner_ref_includes_session(monkeypatch):
    monkeypatch.setattr(m, "_worktree_id_from_git", lambda p: None)
    monkeypatch.setattr(m.tracking, "find_worktree_id_by_cwd", lambda p: "wt-x")
    monkeypatch.setattr(m.cfg, "load_config",
                        lambda: types.SimpleNamespace(machine="dev6", repo_name="proj"))
    monkeypatch.setattr(m.cfg, "project_name", lambda: "proj")
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "sess1")
    assert m._self_owner_ref("/wt/x") == "dev6/proj/wt-x#sess1"


def test_self_owner_ref_degrades_on_error(monkeypatch):
    monkeypatch.setattr(m, "_worktree_id_from_git", lambda p: None)

    def boom(_p):
        raise RuntimeError("no records")
    monkeypatch.setattr(m.tracking, "find_worktree_id_by_cwd", boom)
    assert m._self_owner_ref("/wt/x") is None


# ── _build_env producer ──────────────────────────────────────────────────────

def test_build_env_exports_owner_ref_for_worktree(monkeypatch):
    monkeypatch.setattr(cfg, "project_dir", lambda: Path("/proj"))
    monkeypatch.setattr(m, "_self_owner_ref", lambda wd: "dev6/proj/wt-x")
    env = m._build_env(None, None, work_dir="/wt/x")
    assert env["AGENT_WORKTREES_OWNER_REF"] == "dev6/proj/wt-x"


def test_build_env_omits_owner_ref_for_anchor(monkeypatch):
    monkeypatch.setattr(cfg, "project_dir", lambda: Path("/proj"))
    monkeypatch.setattr(m, "_self_owner_ref", lambda wd: None)
    env = m._build_env(None, None, work_dir="/repo/anchor")
    assert "AGENT_WORKTREES_OWNER_REF" not in env


def test_build_env_omits_owner_ref_without_work_dir(monkeypatch):
    monkeypatch.setattr(cfg, "project_dir", lambda: Path("/proj"))
    env = m._build_env(None, None)
    assert "AGENT_WORKTREES_OWNER_REF" not in env


# ── cmd_create owner resolution (explicit > env > cwd; --no-owner / system) ──

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
    monkeypatch.setattr(m, "_resolve_owner_ref", lambda: "cwd/proj/parent")


def test_cmd_create_explicit_owner_ref_wins(monkeypatch):
    captured: dict = {}
    _patch_core(monkeypatch, captured)
    monkeypatch.setenv("AGENT_WORKTREES_OWNER_REF", "env/proj/parent")
    m.cmd_create(_create_args(owner_ref="explicit/proj/parent"))
    assert captured["owner_ref"] == "explicit/proj/parent"


def test_cmd_create_env_over_cwd(monkeypatch):
    captured: dict = {}
    _patch_core(monkeypatch, captured)
    monkeypatch.setenv("AGENT_WORKTREES_OWNER_REF", "env/proj/parent")
    m.cmd_create(_create_args())
    assert captured["owner_ref"] == "env/proj/parent"


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
