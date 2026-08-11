"""Tests for agent_worktrees.state_root -- the stateless-harness state-root resolver."""

from __future__ import annotations

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import state_root as sr


def _config(repo_name, *, stateless=False, requires_external_state_root=False,
            knowledge_repo="", anchor="/anchor"):
    return cfg.Config(
        srcroot="/src",
        machine="test",
        platform="linux",
        repo_name=repo_name,
        knowledge_repo=knowledge_repo,
        repos={
            repo_name: cfg.RepoConfig(
                anchor=anchor,
                worktree_root=f"{anchor}.worktrees",
                default_branch="main",
                remote="origin",
                stateless=stateless,
                requires_external_state_root=requires_external_state_root,
            )
        },
    )


@pytest.fixture
def fake_checkouts(monkeypatch):
    """Patch the registry->checkout resolver with an in-memory name->path map."""
    table: dict[str, str] = {}

    def _resolve(name):
        return table.get(name)

    monkeypatch.setattr(sr, "_checkout_path", _resolve)
    return table


# ---------------------------------------------------------------------------
# Non-stateless (backward compatible): the launch repo is the state home.
# ---------------------------------------------------------------------------

def test_non_stateless_uses_git_toplevel(monkeypatch):
    monkeypatch.setattr(sr, "_git_toplevel", lambda cwd: "/work/tree")
    res = sr.resolve_state_root(_config("dotfiles"))
    assert res.path == "/work/tree"
    assert res.source == "launch_repo"
    assert res.stateless is False
    assert res.requires_external is False
    assert res.bound is True
    assert res.error is None


def test_non_stateless_falls_back_to_anchor(monkeypatch, tmp_path):
    monkeypatch.setattr(sr, "_git_toplevel", lambda cwd: None)
    res = sr.resolve_state_root(_config("dotfiles", anchor=str(tmp_path)))
    assert res.path == str(tmp_path)
    assert res.source == "launch_repo"
    assert res.bound is True


def test_non_stateless_unresolvable(monkeypatch):
    monkeypatch.setattr(sr, "_git_toplevel", lambda cwd: None)
    res = sr.resolve_state_root(_config("dotfiles", anchor="/does/not/exist"))
    assert res.path is None
    assert res.bound is False
    assert "could not resolve" in res.error


# ---------------------------------------------------------------------------
# Stateless harness -> the bound knowledge repo (no fallback).
# ---------------------------------------------------------------------------

def test_stateless_bound_resolves_knowledge_repo(fake_checkouts):
    fake_checkouts["citadel-knowledge"] = "/repos/knowledge"
    res = sr.resolve_state_root(
        _config("citadel-harness", stateless=True, knowledge_repo="citadel-knowledge")
    )
    assert res.path == "/repos/knowledge"
    assert res.source == "knowledge_repo"
    assert res.repo == "citadel-knowledge"
    assert res.stateless is True
    assert res.requires_external is True  # stateless implies it
    assert res.bound is True


def test_requires_external_without_stateless_routes_knowledge(fake_checkouts):
    # A repo can require an external state root without being a stateless harness.
    fake_checkouts["knowledge"] = "/repos/k"
    res = sr.resolve_state_root(
        _config("some-repo", requires_external_state_root=True, knowledge_repo="knowledge")
    )
    assert res.path == "/repos/k"
    assert res.source == "knowledge_repo"
    assert res.stateless is False
    assert res.requires_external is True
    assert res.bound is True


def test_requires_external_unbound_refuses(fake_checkouts):
    res = sr.resolve_state_root(
        _config("some-repo", requires_external_state_root=True, knowledge_repo="")
    )
    assert res.path is None
    assert res.bound is False
    assert res.stateless is False
    assert res.requires_external is True
    assert "requires an external state root" in res.error


def test_stateless_unbound_refuses(fake_checkouts):
    res = sr.resolve_state_root(
        _config("citadel-harness", stateless=True, knowledge_repo="")
    )
    assert res.path is None
    assert res.bound is False
    assert res.stateless is True
    assert res.requires_external is True
    assert "no knowledge_repo is bound" in res.error
    # Must NOT fall back to the launch repo tree.
    assert "citadel-harness" in res.error


def test_stateless_bound_but_unregistered(fake_checkouts):
    # knowledge_repo points at a name with no registered checkout.
    res = sr.resolve_state_root(
        _config("citadel-harness", stateless=True, knowledge_repo="ghost")
    )
    assert res.path is None
    assert res.bound is False
    assert "not a registered repo" in res.error


# ---------------------------------------------------------------------------
# Explicit override wins over the binding.
# ---------------------------------------------------------------------------

def test_explicit_override_targets_named_repo(fake_checkouts):
    fake_checkouts["odsp-web"] = "/repos/odsp-web"
    res = sr.resolve_state_root(
        _config("citadel-harness", stateless=True, knowledge_repo="citadel-knowledge"),
        repo_override="odsp-web",
    )
    assert res.path == "/repos/odsp-web"
    assert res.source == "explicit"
    assert res.repo == "odsp-web"


def test_explicit_override_unregistered(fake_checkouts):
    res = sr.resolve_state_root(
        _config("dotfiles"), repo_override="nope"
    )
    assert res.path is None
    assert res.source == "explicit"
    assert "not a registered repo" in res.error


def test_as_dict_shape(fake_checkouts):
    fake_checkouts["k"] = "/k"
    res = sr.resolve_state_root(
        _config("h", stateless=True, knowledge_repo="k")
    )
    d = res.as_dict()
    assert set(d) == {
        "state_root", "source", "repo", "stateless", "requires_external",
        "bound", "error",
    }
    assert d["state_root"] == "/k"
    assert d["requires_external"] is True
    assert d["bound"] is True


# ---------------------------------------------------------------------------
# state-root --pair: the citadel paired-worktree resolver (#957).
# ---------------------------------------------------------------------------

class TestStateRootPairCLI:
    """Drive ``cmd_state_root_dispatch(['--pair', ...])`` end to end."""

    def _pair_records(self, tracking_dir, harness_path, knowledge_path):
        from agent_worktrees import tracking as tk

        def _rec(wt_id, role, wp, ref):
            rec = tk.WorktreeRecord(
                worktree_id=wt_id, branch=f"worktree/{wt_id}", worktree_path=wp,
                repo="r", machine="test", platform="wsl",
                started_at="2026-06-01T10:00:00",
                last_resumed_at="2026-06-01T10:00:00", resume_count=0,
                title=None, status="active", completed_at=None, sessions=[],
                pair_id="pair1", pair_role=role, pair_ref=ref, pair_kind="worktree",
            )
            tk.save_record(rec, tracking_dir / f"{wt_id}.yaml")

        _rec("wt-harness", "harness", str(harness_path),
             "test/knowledge/wt-knowledge")
        _rec("wt-knowledge", "knowledge", str(knowledge_path),
             "test/harness/wt-harness")

    def test_pair_prints_sibling_path(self, tmp_path, monkeypatch, capsys):
        from agent_worktrees import tracking as tk
        from agent_worktrees.__main__ import cmd_state_root_dispatch

        tracking_dir = tmp_path / "tracking"
        tracking_dir.mkdir()
        harness = tmp_path / "harness"
        knowledge = tmp_path / "knowledge"
        harness.mkdir()
        knowledge.mkdir()
        self._pair_records(tracking_dir, harness, knowledge)
        monkeypatch.setattr(tk.cfg, "tracking_dir", lambda: tracking_dir)
        monkeypatch.setattr("agent_worktrees.__main__.os.getcwd", lambda: str(harness))

        rc = cmd_state_root_dispatch(["--pair"])
        out = capsys.readouterr().out.strip()
        assert rc == 0
        assert out == str(knowledge)

    def test_pair_json(self, tmp_path, monkeypatch, capsys):
        import json as _json

        from agent_worktrees import tracking as tk
        from agent_worktrees.__main__ import cmd_state_root_dispatch

        tracking_dir = tmp_path / "tracking"
        tracking_dir.mkdir()
        harness = tmp_path / "harness"
        knowledge = tmp_path / "knowledge"
        harness.mkdir()
        knowledge.mkdir()
        self._pair_records(tracking_dir, harness, knowledge)
        monkeypatch.setattr(tk.cfg, "tracking_dir", lambda: tracking_dir)
        monkeypatch.setattr("agent_worktrees.__main__.os.getcwd", lambda: str(harness))

        rc = cmd_state_root_dispatch(["--pair", "--json"])
        data = _json.loads(capsys.readouterr().out)
        assert rc == 0
        assert data["paired"] is True and data["pair_id"] == "pair1"
        assert data["self"]["role"] == "harness"
        assert data["sibling"]["worktree_id"] == "wt-knowledge"
        assert data["sibling"]["role"] == "knowledge"
        assert data["sibling"]["path"] == str(knowledge)

    def test_pair_untracked_cwd_exits_3(self, tmp_path, monkeypatch, capsys):
        from agent_worktrees import tracking as tk
        from agent_worktrees.__main__ import cmd_state_root_dispatch

        tracking_dir = tmp_path / "tracking"
        tracking_dir.mkdir()
        monkeypatch.setattr(tk.cfg, "tracking_dir", lambda: tracking_dir)
        monkeypatch.setattr(
            "agent_worktrees.__main__.os.getcwd", lambda: str(tmp_path / "nowhere")
        )
        rc = cmd_state_root_dispatch(["--pair"])
        assert rc == 3

    def test_pair_unpaired_worktree_exits_3(self, tmp_path, monkeypatch, capsys):
        from agent_worktrees import tracking as tk
        from agent_worktrees.__main__ import cmd_state_root_dispatch

        tracking_dir = tmp_path / "tracking"
        tracking_dir.mkdir()
        solo = tmp_path / "solo"
        solo.mkdir()
        rec = tk.WorktreeRecord(
            worktree_id="solo", branch="worktree/solo", worktree_path=str(solo),
            repo="r", machine="test", platform="wsl",
            started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00", resume_count=0,
            title=None, status="active", completed_at=None, sessions=[],
        )
        tk.save_record(rec, tracking_dir / "solo.yaml")
        monkeypatch.setattr(tk.cfg, "tracking_dir", lambda: tracking_dir)
        monkeypatch.setattr("agent_worktrees.__main__.os.getcwd", lambda: str(solo))
        rc = cmd_state_root_dispatch(["--pair"])
        assert rc == 3


# ---------------------------------------------------------------------------
# config_source_anchors -- the E1e knowledge-overlay (config-graft) seam.
# ---------------------------------------------------------------------------

def test_config_sources_non_stateless_is_base_only(monkeypatch):
    monkeypatch.setattr(sr, "_git_toplevel", lambda cwd: "/work/tree")
    srcs = sr.config_source_anchors(_config("dotfiles"))
    assert [(s.anchor, s.origin) for s in srcs] == [("/work/tree", "harness")]


def test_config_sources_stateless_grafts_knowledge_overlay(fake_checkouts):
    fake_checkouts["citadel-knowledge"] = "/repos/knowledge"
    srcs = sr.config_source_anchors(
        _config("citadel-harness", stateless=True,
                knowledge_repo="citadel-knowledge"),
        base_anchor="/repos/harness",
    )
    assert [(s.anchor, s.origin) for s in srcs] == [
        ("/repos/harness", "harness"),
        ("/repos/knowledge", "knowledge"),
    ]


def test_config_sources_dedup_when_knowledge_equals_base(fake_checkouts):
    # Degenerate case: the knowledge checkout is the base -- no duplicate overlay.
    fake_checkouts["k"] = "/repos/harness"
    srcs = sr.config_source_anchors(
        _config("h", stateless=True, knowledge_repo="k"),
        base_anchor="/repos/harness",
    )
    assert [s.anchor for s in srcs] == ["/repos/harness"]


def test_config_sources_unbound_stateless_is_base_only(fake_checkouts):
    # require-external but no bound knowledge repo -> base only (no overlay).
    srcs = sr.config_source_anchors(
        _config("h", stateless=True, knowledge_repo=""),
        base_anchor="/repos/harness",
    )
    assert [s.anchor for s in srcs] == ["/repos/harness"]


# ---------------------------------------------------------------------------
# state_repo_definition -- the sessionStart "the user's state repo" injection.
# ---------------------------------------------------------------------------

def test_state_repo_definition_self_hosted_names_path_and_current_repo():
    res = sr.StateRoot("/work/tree", "launch_repo", "dotfiles", False, False, True)
    text = sr.state_repo_definition(res)
    assert "**The user's state repo**" in text
    assert "`/work/tree`" in text
    assert "self-hosted" in text
    assert "\n" not in text  # single self-contained paragraph


def test_state_repo_definition_stateless_names_knowledge_repo():
    res = sr.StateRoot("/repos/knowledge", "knowledge_repo", "kn", True, True, True)
    text = sr.state_repo_definition(res)
    assert "`/repos/knowledge`" in text
    assert "bound knowledge repo" in text


def test_state_repo_definition_explicit_names_repo():
    res = sr.StateRoot("/repos/x", "explicit", "x", False, False, True)
    text = sr.state_repo_definition(res)
    assert "`/repos/x`" in text
    assert "'x'" in text


def test_state_repo_definition_unbound_has_no_path_and_warns():
    res = sr.StateRoot(None, "knowledge_repo", "", True, True, False,
                       error="unbound")
    text = sr.state_repo_definition(res)
    assert "not bound on this machine" in text
    assert "`" not in text  # no backtick-quoted path when unresolved
    assert "**The user's state repo**" in text


def test_state_repo_definition_bound_knowledge_carries_write_routing():
    # A bound knowledge repo => harness and state repo are distinct, so the
    # definition adds the "where changes go" routing clause.
    res = sr.StateRoot("/repos/knowledge", "knowledge_repo", "kn", True, True, True)
    text = sr.state_repo_definition(res)
    assert "Where changes go" in text
    assert "shared harness" in text
    assert "harness repo (your current checkout)" in text


def test_state_repo_definition_self_hosted_has_no_write_routing():
    # Self-hosted: one checkout, so NO routing clause (would be noise).
    res = sr.StateRoot("/work/tree", "launch_repo", "dotfiles", False, False, True)
    text = sr.state_repo_definition(res)
    assert "Where changes go" not in text


def test_state_repo_definition_explicit_has_no_write_routing():
    res = sr.StateRoot("/repos/x", "explicit", "x", False, False, True)
    text = sr.state_repo_definition(res)
    assert "Where changes go" not in text
