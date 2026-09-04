"""Tests for the citadel paired -harness/-knowledge carve (#957 slice 2).

Covers ``_carve_paired_knowledge`` (the carve-both helper wired into
``_create_worktree_core``) and the ``state-root --pair`` anchor-kind resolver.
"""

from __future__ import annotations

import types

import agent_worktrees.__main__ as m
from agent_worktrees import knowledge_plugins as kp
from agent_worktrees import repos as repos_mod
from agent_worktrees import state_root as sr
from agent_worktrees import tracking as tk


def _state_root(*, path, repo, requires_external=True, bound=True):
    return sr.StateRoot(
        path=path, source="knowledge_repo", repo=repo, stateless=True,
        requires_external=requires_external, bound=bound, error=None,
    )


def _config(machine="test", repo_name="citadel-harness"):
    return types.SimpleNamespace(machine=machine, repo_name=repo_name)


def _common_patches(monkeypatch, tmp_path):
    """Patch tracking dir + best-effort permissions/activity to no-ops."""
    monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tmp_path)
    monkeypatch.setattr(tk.cfg, "tracking_dir", lambda: tmp_path)
    monkeypatch.setattr(
        m.cfg, "project_dir", lambda name=None: tmp_path / f".{name}"
    )
    monkeypatch.setattr(
        tk.cfg, "project_dir", lambda name=None: tmp_path / f".{name}"
    )
    monkeypatch.setattr(m.permissions, "clone_permissions", lambda a, b: False)
    monkeypatch.setattr(m.permissions, "add_trusted_folder", lambda p: False)
    monkeypatch.setattr(m.activity, "log_event", lambda *a, **k: None)


class TestCarvePairedKnowledge:
    def test_returns_none_when_not_stateless(self, monkeypatch, tmp_path):
        _common_patches(monkeypatch, tmp_path)
        monkeypatch.setattr(
            m.state_root_mod, "resolve_state_root",
            lambda c: _state_root(path=None, repo="", requires_external=False,
                                  bound=False),
        )
        out = m._carve_paired_knowledge(
            _config(), harness_id="test-win-ts-ab", timestamp="ts",
            suffix="ab", plat="windows", plat_short="win",
        )
        assert out is None

    def test_env_disable(self, monkeypatch, tmp_path):
        _common_patches(monkeypatch, tmp_path)
        monkeypatch.setenv("AGENT_WORKTREES_NO_PAIR", "1")
        # Even a bound stateless resolve must yield None when disabled.
        monkeypatch.setattr(
            m.state_root_mod, "resolve_state_root",
            lambda c: _state_root(path=str(tmp_path), repo="k"),
        )
        out = m._carve_paired_knowledge(
            _config(), harness_id="h", timestamp="ts", suffix="ab",
            plat="windows", plat_short="win",
        )
        assert out is None

    def test_worktree_class_carves_and_cross_stamps(self, monkeypatch, tmp_path):
        _common_patches(monkeypatch, tmp_path)
        k_anchor = tmp_path / "knowledge"
        k_anchor.mkdir()
        monkeypatch.setattr(
            m.state_root_mod, "resolve_state_root",
            lambda c: _state_root(path=str(k_anchor), repo="citadel-knowledge"),
        )
        entry = repos_mod.RepoEntry(
            name="citadel-knowledge",
            repo_class="worktree",
            remote="https://example.com/citadel-knowledge.git",
            default_branch="main",
        )
        monkeypatch.setattr(repos_mod, "find_repo", lambda n: entry)
        monkeypatch.setattr(
            m.git_ops,
            "resolve_remote_name",
            lambda value, *, cwd: "origin",
        )
        # Stub the git side-effects.
        carved = {}

        def _create_worktree(anchor, wt_path, branch, start_point):
            carved["anchor"] = anchor
            carved["wt_path"] = wt_path
            carved["branch"] = branch
            carved["start_point"] = start_point

        monkeypatch.setattr(
            m.git_ops,
            "prepare_worktree_base",
            lambda *a, **k: types.SimpleNamespace(
                start_point="origin/main",
                fetched=True,
                fetch_error=None,
                anchor=types.SimpleNamespace(
                    updated=True,
                    reason="updated",
                    behind=2,
                ),
            ),
        )
        monkeypatch.setattr(m.git_ops, "create_worktree", _create_worktree)

        stamp = m._carve_paired_knowledge(
            _config(machine="test"), harness_id="test-win-20260806-ab",
            timestamp="20260806", suffix="ab", plat="windows",
            plat_short="win",
        )
        # Harness stamp.
        assert stamp is not None
        assert stamp["pair_kind"] == "worktree"
        assert stamp["pair_role"] == "harness"
        assert stamp["pair_id"] == "20260806-ab"
        assert stamp["pair_ref"] == "test/citadel-knowledge/test-win-20260806-ab-k"
        # A knowledge worktree was carved.
        assert carved["branch"] == "worktree/test-win-20260806-ab-k"
        assert carved["start_point"] == "origin/main"
        # Knowledge tracking record written, cross-stamped back to harness.
        knowledge_tracking = tmp_path / ".citadel-knowledge" / "worktrees"
        krec = tk.load_record_by_id(
            "test-win-20260806-ab-k",
            tracking_path=knowledge_tracking,
        )
        assert krec is not None
        assert krec.repo == "citadel-knowledge"
        assert krec.pair_role == "knowledge"
        assert krec.pair_kind == "worktree"
        assert krec.pair_id == "20260806-ab"
        assert krec.pair_ref == "test/citadel-harness/test-win-20260806-ab"
        assert not (tmp_path / "test-win-20260806-ab-k.yaml").exists()

    def test_non_worktree_class_pairs_anchor(self, monkeypatch, tmp_path):
        _common_patches(monkeypatch, tmp_path)
        k_anchor = tmp_path / "kb-singleton"
        k_anchor.mkdir()
        monkeypatch.setattr(
            m.state_root_mod, "resolve_state_root",
            lambda c: _state_root(path=str(k_anchor), repo="kb"),
        )
        entry = repos_mod.RepoEntry(name="kb", repo_class="singleton")
        monkeypatch.setattr(repos_mod, "find_repo", lambda n: entry)
        called = {"carve": False}
        monkeypatch.setattr(
            m.git_ops, "create_worktree",
            lambda *a, **k: called.__setitem__("carve", True),
        )
        refreshed = {"called": False}
        monkeypatch.setattr(
            m.git_ops,
            "prepare_worktree_base",
            lambda *a, **k: (
                refreshed.__setitem__("called", True)
                or types.SimpleNamespace(
                    start_point="HEAD",
                    fetched=False,
                    fetch_error="offline",
                    anchor=types.SimpleNamespace(
                        updated=False,
                        reason="no-upstream",
                        behind=0,
                    ),
                )
            ),
        )
        stamp = m._carve_paired_knowledge(
            _config(), harness_id="h", timestamp="ts", suffix="ab",
            plat="windows", plat_short="win",
        )
        assert stamp is not None
        assert stamp["pair_kind"] == "anchor"
        assert stamp["pair_role"] == "harness"
        assert stamp["pair_ref"] == "test/kb/kb-singleton"
        # No second worktree carved for a non-worktree-class knowledge repo.
        assert called["carve"] is False
        assert refreshed["called"] is True
        # No knowledge tracking record either.
        assert list(tmp_path.glob("*.yaml")) == []


class TestCreatePairPluginComposition:
    def test_stamps_pair_before_composing_plugins(self, monkeypatch, tmp_path):
        record = tk.WorktreeRecord(
            worktree_id="wt-h",
            branch="worktree/wt-h",
            worktree_path=str(tmp_path / "harness"),
            repo="citadel-harness",
            machine="test",
            platform="windows",
            started_at="2026-09-02T00:00:00",
            last_resumed_at="2026-09-02T00:00:00",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            sessions=[],
        )
        stamp = {
            "pair_id": "pair-1",
            "pair_role": "harness",
            "pair_ref": "test/knowledge/wt-k",
            "pair_kind": "worktree",
        }
        saved = []

        def _save(current):
            saved.append(
                (
                    current.pair_id,
                    current.pair_role,
                    current.pair_ref,
                    current.pair_kind,
                )
            )

        composed = []

        def _compose(*, cwd, config):
            assert saved == [
                ("pair-1", "harness", "test/knowledge/wt-k", "worktree")
            ]
            composed.append((cwd, config))
            return {"action": "composed"}

        config = _config()
        monkeypatch.setattr(tk, "save_record", _save)
        monkeypatch.setattr(kp, "compose_from_pair", _compose)

        result = m._stamp_and_compose_paired_knowledge(
            config, record, str(tmp_path / "harness"), stamp
        )

        assert result == {"action": "composed"}
        assert composed == [(str(tmp_path / "harness"), config)]

    def test_unpaired_create_does_not_compose_plugins(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            kp,
            "compose_from_pair",
            lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("unpaired create must not compose")
            ),
        )

        assert (
            m._stamp_and_compose_paired_knowledge(
                _config(), object(), str(tmp_path / "harness"), None
            )
            is None
        )

    def test_composition_failure_preserves_created_pair_identity(
        self, monkeypatch, tmp_path, capsys
    ):
        record = types.SimpleNamespace(
            pair_id=None,
            pair_role=None,
            pair_ref=None,
            pair_kind=None,
        )
        saved = []
        monkeypatch.setattr(
            tk,
            "save_record",
            lambda current: saved.append(current.pair_id),
        )
        monkeypatch.setattr(
            kp,
            "compose_from_pair",
            lambda **_kwargs: (_ for _ in ()).throw(
                kp.KnowledgePluginError("settings are malformed")
            ),
        )

        result = m._stamp_and_compose_paired_knowledge(
            _config(),
            record,
            str(tmp_path / "harness"),
            {
                "pair_id": "pair-1",
                "pair_role": "harness",
                "pair_ref": "test/knowledge/wt-k",
                "pair_kind": "worktree",
            },
        )

        assert saved == ["pair-1"]
        assert record.pair_ref == "test/knowledge/wt-k"
        assert result == {"action": "error", "error": "settings are malformed"}
        assert "launch preflight will retry" in capsys.readouterr().err


class TestStateRootPairAnchor:
    def test_pair_anchor_resolves_via_state_root(
        self, monkeypatch, tmp_path, capsys
    ):
        tracking_dir = tmp_path / "tracking"
        tracking_dir.mkdir()
        harness = tmp_path / "harness"
        harness.mkdir()
        k_anchor = tmp_path / "kb"
        k_anchor.mkdir()
        rec = tk.WorktreeRecord(
            worktree_id="wt-h", branch="worktree/wt-h",
            worktree_path=str(harness), repo="citadel-harness", machine="test",
            platform="wsl", started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00", resume_count=0, title=None,
            status="active", completed_at=None, sessions=[],
            pair_id="p", pair_role="harness", pair_ref="test/kb/kb",
            pair_kind="anchor",
        )
        tk.save_record(rec, tracking_dir / "wt-h.yaml")
        monkeypatch.setattr(tk.cfg, "tracking_dir", lambda: tracking_dir)
        monkeypatch.setattr(
            "agent_worktrees.__main__.os.getcwd", lambda: str(harness)
        )
        monkeypatch.setattr(
            m.cfg, "load_config", lambda: types.SimpleNamespace(knowledge_repo="kb")
        )
        monkeypatch.setattr(
            m.state_root_mod, "resolve_state_root",
            lambda c: _state_root(path=str(k_anchor), repo="kb"),
        )
        rc = m.cmd_state_root_dispatch(["--pair"])
        out = capsys.readouterr().out.strip()
        assert rc == 0
        assert out == str(k_anchor)
