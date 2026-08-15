"""Tests for `agent-worktrees run` claim journaling (resource-claims).

Covers the pure recognizer/journaling helpers behind the `run` wrapper:
recognizing the resource an inner command produced from its JSON output, and
appending the forward ResourceClaim to the caller's tracking record.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_worktrees import __main__ as m
from agent_worktrees import tracking


class TestClaimFromRunOutput:
    """_claim_from_run_output recognizes the create envelope + bare worktree
    dict, and declines unknown shapes."""

    def test_create_envelope(self):
        out = json.dumps({
            "worktree": {"id": "wt-B", "machine": "anomalous-potato",
                         "repo": "copilot-extensions", "path": "/x"},
            "launch": {"action": "exec"},
        })
        claim = m._claim_from_run_output(out)
        assert claim is not None
        assert claim.kind == "worktree"
        assert claim.ref == "anomalous-potato/copilot-extensions/wt-B"
        assert claim.state == "active" and claim.created_at

    def test_bare_worktree_dict(self):
        out = json.dumps({"id": "wt-C", "machine": "m", "repo": "p"})
        claim = m._claim_from_run_output(out)
        assert claim is not None and claim.ref == "m/p/wt-C"

    def test_missing_repo_falls_back_to_bare_ref(self):
        out = json.dumps({"worktree": {"id": "wt-D", "machine": "m"}})
        claim = m._claim_from_run_output(out)
        # No project -> bare ref (machine alone cannot qualify).
        assert claim is not None and claim.ref == "wt-D"

    def test_non_json_declines(self):
        assert m._claim_from_run_output("not json at all") is None

    def test_unknown_shape_declines(self):
        assert m._claim_from_run_output(json.dumps({"hello": "world"})) is None

    def test_incomplete_worktree_declines(self):
        # id present but no machine -> cannot build a ref.
        assert m._claim_from_run_output(json.dumps({"worktree": {"id": "x"}})) is None

    def test_create_pr_envelope_github(self):
        out = json.dumps({
            "success": True, "pr_opened": True, "provider": "github",
            "number": 42, "url": "https://github.com/o/r/pull/42",
            "pr": {"ref": "https://github.com/o/r/pull/42", "number": 42,
                   "provider": "github", "url": "https://github.com/o/r/pull/42"},
        })
        claim = m._claim_from_run_output(out)
        assert claim is not None
        assert claim.kind == "pr"
        assert claim.ref == "https://github.com/o/r/pull/42"
        assert claim.state == "active" and claim.created_at

    def test_create_pr_envelope_ado(self):
        url = "https://onedrive.visualstudio.com/P/_git/r/pullrequest/2285417"
        out = json.dumps({"success": True, "pr_opened": True, "pr": {"ref": url}})
        claim = m._claim_from_run_output(out)
        assert claim is not None and claim.kind == "pr" and claim.ref == url

    def test_pr_envelope_without_ref_declines(self):
        # A pr sub-dict lacking a ref cannot be journaled.
        out = json.dumps({"pr_opened": True, "pr": {"number": 7}})
        assert m._claim_from_run_output(out) is None

    def test_worktree_envelope_wins_over_pr(self):
        # A worktree envelope is recognized first (create, not create-pr).
        out = json.dumps({
            "worktree": {"id": "wt-B", "machine": "m", "repo": "p"},
            "pr": {"ref": "https://github.com/o/r/pull/1"},
        })
        claim = m._claim_from_run_output(out)
        assert claim is not None and claim.kind == "worktree"


class TestJournalRunClaim:
    """_journal_run_claim appends the produced-resource claim to the caller's
    record (looked up by the owner ref) and is a no-op when the record is
    absent."""

    def _make_caller(self, tmp_path: Path, monkeypatch) -> tracking.WorktreeRecord:
        # The caller record lives in the active project's tracking dir; point
        # tracking_dir() at the temp dir so the lookup resolves there.
        monkeypatch.setattr("agent_worktrees.config.tracking_dir",
                            lambda: tmp_path)
        return tracking.create_new_record(
            "wt-A", "worktree/wt-A", str(tmp_path / "wt-A"), "test-chamber",
            "anomalous-potato", "wsl", tmp_path,
        )

    def test_journals_forward_claim(self, tmp_path: Path, monkeypatch):
        self._make_caller(tmp_path, monkeypatch)
        owner_ref = "anomalous-potato/test-chamber/wt-A#s1"
        out = json.dumps({"worktree": {"id": "wt-B", "machine": "anomalous-potato",
                                       "repo": "copilot-extensions"}})
        claim = m._journal_run_claim(owner_ref, out)
        assert claim is not None
        reloaded = tracking.load_record(tmp_path / "wt-A.yaml")
        assert [c.ref for c in reloaded.resources] == \
            ["anomalous-potato/copilot-extensions/wt-B"]

    def test_idempotent(self, tmp_path: Path, monkeypatch):
        self._make_caller(tmp_path, monkeypatch)
        owner_ref = "anomalous-potato/test-chamber/wt-A#s1"
        out = json.dumps({"worktree": {"id": "wt-B", "machine": "anomalous-potato",
                                       "repo": "copilot-extensions"}})
        m._journal_run_claim(owner_ref, out)
        m._journal_run_claim(owner_ref, out)
        reloaded = tracking.load_record(tmp_path / "wt-A.yaml")
        assert len(reloaded.resources) == 1

    def test_journals_pr_claim(self, tmp_path: Path, monkeypatch):
        self._make_caller(tmp_path, monkeypatch)
        owner_ref = "anomalous-potato/test-chamber/wt-A#s1"
        out = json.dumps({"success": True, "pr_opened": True,
                          "pr": {"ref": "https://github.com/o/r/pull/42"}})
        claim = m._journal_run_claim(owner_ref, out)
        assert claim is not None and claim.kind == "pr"
        reloaded = tracking.load_record(tmp_path / "wt-A.yaml")
        assert [(c.kind, c.ref) for c in reloaded.resources] == \
            [("pr", "https://github.com/o/r/pull/42")]

    def test_missing_caller_record_is_noop(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("agent_worktrees.config.tracking_dir",
                            lambda: tmp_path)
        out = json.dumps({"worktree": {"id": "wt-B", "machine": "m",
                                       "repo": "p"}})
        # No wt-A.yaml exists.
        assert m._journal_run_claim("m/test-chamber/wt-A", out) is None
        self._make_caller(tmp_path, monkeypatch)
        assert m._journal_run_claim("anomalous-potato/test-chamber/wt-A", "junk") is None
        reloaded = tracking.load_record(tmp_path / "wt-A.yaml")
        assert reloaded.resources == []

    def test_anchor_owner_lazily_creates_ledger(self, tmp_path: Path, monkeypatch):
        # A run from a singleton anchor journals onto @anchor, creating the
        # ledger on first use (no pre-existing record).
        import types
        monkeypatch.setattr("agent_worktrees.config.tracking_dir",
                            lambda: tmp_path)
        monkeypatch.setattr("agent_worktrees.config.load_config",
                            lambda *a, **k: types.SimpleNamespace(
                                machine="anomalous-potato", platform="wsl",
                                repo_name="spo-core"))
        adir = tmp_path / "anchors" / "spo-core"
        adir.mkdir(parents=True)
        monkeypatch.setattr(
            "agent_worktrees.repos.find_repo",
            lambda name: types.SimpleNamespace(
                repo_class="singleton", local_path=lambda plat=None: str(adir)))
        owner_ref = "anomalous-potato/spo-core/@anchor"
        out = json.dumps({"success": True, "pr_opened": True,
                          "pr": {"ref": "https://github.com/o/r/pull/7"}})
        assert not (tmp_path / "@anchor.yaml").exists()
        claim = m._journal_run_claim(owner_ref, out)
        assert claim is not None and claim.kind == "pr"
        assert (tmp_path / "@anchor.yaml").exists()
        reloaded = tracking.load_record(tmp_path / "@anchor.yaml")
        assert reloaded.pair_kind == "anchor"
        assert [(c.kind, c.ref) for c in reloaded.resources] == \
            [("pr", "https://github.com/o/r/pull/7")]


class TestResolveAnchorOwnerRef:
    """_resolve_owner_ref falls back to a singleton's @anchor owner when the CWD
    is that repo's anchor (and NOT for a worktree-class anchor)."""

    def _wire(self, tmp_path, monkeypatch, repo_class, *, cwd_in_anchor=True):
        import types
        monkeypatch.delenv("COPILOT_AGENT_SESSION_ID", raising=False)
        adir = tmp_path / "anchors" / "spo-core"
        adir.mkdir(parents=True)
        monkeypatch.setattr("agent_worktrees.config.load_config",
                            lambda *a, **k: types.SimpleNamespace(
                                machine="anomalous-potato", platform="wsl",
                                repo_name="spo-core"))
        monkeypatch.setattr("agent_worktrees.config.project_name",
                            lambda: "spo-core")
        monkeypatch.setattr("agent_worktrees.__main__._infer_worktree_id_from_cwd",
                            lambda config=None: None)  # not a worktree
        monkeypatch.setattr(
            "agent_worktrees.repos.find_repo",
            lambda name: types.SimpleNamespace(
                repo_class=repo_class, local_path=lambda plat=None: str(adir)))
        target = adir if cwd_in_anchor else tmp_path / "elsewhere"
        target.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(target)

    def test_singleton_anchor_resolves_to_anchor_ref(self, tmp_path, monkeypatch):
        self._wire(tmp_path, monkeypatch, "singleton")
        assert m._resolve_owner_ref() == "anomalous-potato/spo-core/@anchor"

    def test_worktree_class_anchor_is_no_owner(self, tmp_path, monkeypatch):
        # The class == singleton gate is the worktree-class mis-fire guard.
        self._wire(tmp_path, monkeypatch, "worktree")
        assert m._resolve_owner_ref() is None

    def test_cwd_outside_anchor_is_no_owner(self, tmp_path, monkeypatch):
        self._wire(tmp_path, monkeypatch, "singleton", cwd_in_anchor=False)
        assert m._resolve_owner_ref() is None


class TestLocalClaimantAlive:
    """_local_claimant_alive resolves same-machine owners and biases to sparing:
    only a positively-resolved gone owner returns False; cross-machine is None."""

    def _wire(self, tmp_path, monkeypatch, machine="anomalous-potato"):
        import types
        monkeypatch.setattr("agent_worktrees.config.load_config",
                            lambda *a, **k: types.SimpleNamespace(machine=machine))
        # project_dir(project) -> tmp_path/.<project>
        monkeypatch.setattr("agent_worktrees.config.project_dir",
                            lambda name=None: tmp_path / f".{name}")

    def _make_owner(self, tmp_path, project, wt_id, exists=True):
        wdir = tmp_path / "trees" / wt_id
        if exists:
            wdir.mkdir(parents=True, exist_ok=True)
        tdir = tmp_path / f".{project}" / "worktrees"
        tdir.mkdir(parents=True, exist_ok=True)
        tracking.create_new_record(
            wt_id, f"worktree/{wt_id}", str(wdir), project,
            "anomalous-potato", "wsl", tdir,
        )

    def test_cross_machine_is_unconfirmed(self, tmp_path, monkeypatch):
        self._wire(tmp_path, monkeypatch, machine="anomalous-potato")
        ref = "emancipation-cube/test-chamber/wt-A#s1"
        assert m._local_claimant_alive(ref) is None

    def test_present_owner_is_alive(self, tmp_path, monkeypatch):
        self._wire(tmp_path, monkeypatch)
        self._make_owner(tmp_path, "test-chamber", "wt-A", exists=True)
        assert m._local_claimant_alive("anomalous-potato/test-chamber/wt-A#s1") is True

    def test_missing_record_is_gone(self, tmp_path, monkeypatch):
        self._wire(tmp_path, monkeypatch)
        # no owner record created
        assert m._local_claimant_alive("anomalous-potato/test-chamber/wt-A") is False

    def test_missing_dir_is_gone(self, tmp_path, monkeypatch):
        self._wire(tmp_path, monkeypatch)
        self._make_owner(tmp_path, "test-chamber", "wt-A", exists=False)
        assert m._local_claimant_alive("anomalous-potato/test-chamber/wt-A") is False

    def test_empty_ref_is_none(self, tmp_path, monkeypatch):
        self._wire(tmp_path, monkeypatch)
        assert m._local_claimant_alive("") is None
