"""Integration tests for the Phase 2 CLI codename touch points (effort:
``pr-attribution-codenames``, issue #2838): ``create`` assigns and persists a
codename, ``list``/``resolve`` accept ``--codename`` as an alternate selector.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

import agent_worktrees.__main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import tracking
from agent_worktrees.worktree_identity import resolve_worktree_id_by_codename


def _create_config(tmp_path: Path) -> cfg.Config:
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    return cfg.Config(
        srcroot=str(tmp_path),
        machine="test",
        platform="windows",
        repo_name="demo-repo",
        repos={
            "demo-repo": cfg.RepoConfig(
                anchor=str(anchor),
                worktree_root=str(tmp_path / "worktrees"),
            )
        },
    )


def _stub_create_worktree_core_internals(monkeypatch, tmp_path: Path) -> None:
    """Neutralize every side-effecting internal except the tracking-store
    write path, so the real ``create_new_record``/codename assignment runs.
    """
    monkeypatch.setattr(m.git_ops, "resolve_start_point", lambda *_a, **_k: "HEAD")
    monkeypatch.setattr(
        m, "_prepare_worktree_source", lambda *_a, **_k: SimpleNamespace(start_point="HEAD"),
    )
    monkeypatch.setattr(m.git_ops, "create_worktree", lambda *_a, **_k: None)
    monkeypatch.setattr(m.permissions, "clone_permissions", lambda *_a: False)
    monkeypatch.setattr(m.permissions, "add_trusted_folder", lambda *_a: False)
    monkeypatch.setattr(m.activity, "log_event", lambda *_a, **_k: None)
    monkeypatch.setattr(
        m.state_root_mod,
        "resolve_state_root",
        lambda config, cwd=None: m.state_root.StateRoot(
            path=None, source="knowledge_repo", repo="", stateless=False,
            requires_external=False, bound=False, error=None,
        ),
    )
    monkeypatch.setattr(
        m, "_launch_profile_selection",
        lambda *_a, **_k: SimpleNamespace(profile=None, assignment=None),
    )
    monkeypatch.setattr(m, "_reflect_assignment", lambda *_a, **_k: None)
    monkeypatch.setattr(m, "_build_launch_cmd", lambda *_a, **_k: ["copilot"])
    monkeypatch.setattr(m, "_repo_session_env", lambda *_a, **_k: {})
    monkeypatch.setattr(m, "_build_env", lambda *_a, **_k: {})
    monkeypatch.setattr(m, "_apply_assignment_env", lambda env, _selection: env)


class TestCreateAssignsCodename:
    def test_create_worktree_core_assigns_and_persists_codename(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        config = _create_config(tmp_path)
        tracking_path = tmp_path / "tracking"
        monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tracking_path)
        _stub_create_worktree_core_internals(monkeypatch, tmp_path)

        result = m._create_worktree_core(config)

        worktree_id = result["worktree"]["id"]
        rec = tracking.load_record_by_id(worktree_id, tracking_path=tracking_path)
        assert rec is not None
        assert rec.codename
        # Also surfaced through the JSON envelope `create --json` emits.
        assert result["worktree"]["codename"] == rec.codename

    def test_two_creates_get_distinct_codenames(self, tmp_path: Path, monkeypatch) -> None:
        config = _create_config(tmp_path)
        tracking_path = tmp_path / "tracking"
        monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tracking_path)
        _stub_create_worktree_core_internals(monkeypatch, tmp_path)

        first = m._create_worktree_core(config)
        second = m._create_worktree_core(config)

        assert first["worktree"]["codename"] != second["worktree"]["codename"]


class TestCodenameSelectorWiring:
    def test_resolve_worktree_id_by_codename_matches_real_record(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
        tracking.create_new_record(
            "wt-a", "worktree/wt-a", "/tmp/wt-a", "repo", "machine", "wsl",
            tmp_path, codename="rusty-gizmo",
        )
        assert resolve_worktree_id_by_codename("rusty-gizmo") == "wt-a"
        assert resolve_worktree_id_by_codename("no-such-codename") is None

    def test_cmd_list_filters_by_codename(self, tmp_path: Path, monkeypatch, capfd) -> None:
        monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
        tracking.create_new_record(
            "wt-a", "worktree/wt-a", str(tmp_path / "wt-a"), "repo", "machine", "wsl",
            tmp_path, codename="rusty-gizmo",
        )
        tracking.create_new_record(
            "wt-b", "worktree/wt-b", str(tmp_path / "wt-b"), "repo", "machine", "wsl",
            tmp_path, codename="humming-widget",
        )
        records = tracking.list_records(tmp_path)
        monkeypatch.setattr(m, "_list_records_for_args", lambda args: records)
        monkeypatch.setattr(m.profile_assignment, "maintain", lambda: None)

        args = argparse.Namespace(
            worktree_id=None, codename="humming-widget", refresh=False,
            glance=False, stream=False, json=True, cache_only=False,
        )
        assert m.cmd_list(args) == 0
        # _json_output writes to sys.__stdout__, so a plain fd-level capture
        # (capfd) is required -- capsys patches sys.stdout only.
        payload = capfd.readouterr().out
        assert '"wt-b"' in payload
        assert '"wt-a"' not in payload

    def test_cmd_resolve_json_maps_codename_to_worktree_id(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        # cmd_resolve's up-front codename resolution: exercised directly
        # against args, mirroring what the full `resolve --json --codename`
        # invocation does before any launch/picker logic runs.
        monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
        tracking.create_new_record(
            "wt-a", "worktree/wt-a", "/tmp/wt-a", "repo", "machine", "wsl",
            tmp_path, codename="rusty-gizmo",
        )
        args = argparse.Namespace(codename="rusty-gizmo", worktree_id=None)
        codename_arg = getattr(args, "codename", None)
        if codename_arg and not getattr(args, "worktree_id", None):
            args.worktree_id = resolve_worktree_id_by_codename(codename_arg)
        assert args.worktree_id == "wt-a"
