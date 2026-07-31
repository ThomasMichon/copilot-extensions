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
            "worktree": {"id": "wt-B", "machine": "lambda-core",
                         "repo": "copilot-extensions", "path": "/x"},
            "launch": {"action": "exec"},
        })
        claim = m._claim_from_run_output(out)
        assert claim is not None
        assert claim.kind == "worktree"
        assert claim.ref == "lambda-core/copilot-extensions/wt-B"
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
            "wt-A", "worktree/wt-A", str(tmp_path / "wt-A"), "aperture-labs",
            "lambda-core", "wsl", tmp_path,
        )

    def test_journals_forward_claim(self, tmp_path: Path, monkeypatch):
        self._make_caller(tmp_path, monkeypatch)
        owner_ref = "lambda-core/aperture-labs/wt-A#s1"
        out = json.dumps({"worktree": {"id": "wt-B", "machine": "lambda-core",
                                       "repo": "copilot-extensions"}})
        claim = m._journal_run_claim(owner_ref, out)
        assert claim is not None
        reloaded = tracking.load_record(tmp_path / "wt-A.yaml")
        assert [c.ref for c in reloaded.resources] == \
            ["lambda-core/copilot-extensions/wt-B"]

    def test_idempotent(self, tmp_path: Path, monkeypatch):
        self._make_caller(tmp_path, monkeypatch)
        owner_ref = "lambda-core/aperture-labs/wt-A#s1"
        out = json.dumps({"worktree": {"id": "wt-B", "machine": "lambda-core",
                                       "repo": "copilot-extensions"}})
        m._journal_run_claim(owner_ref, out)
        m._journal_run_claim(owner_ref, out)
        reloaded = tracking.load_record(tmp_path / "wt-A.yaml")
        assert len(reloaded.resources) == 1

    def test_missing_caller_record_is_noop(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("agent_worktrees.config.tracking_dir",
                            lambda: tmp_path)
        out = json.dumps({"worktree": {"id": "wt-B", "machine": "m",
                                       "repo": "p"}})
        # No wt-A.yaml exists.
        assert m._journal_run_claim("m/aperture-labs/wt-A", out) is None

    def test_unrecognized_output_is_noop(self, tmp_path: Path, monkeypatch):
        self._make_caller(tmp_path, monkeypatch)
        assert m._journal_run_claim("lambda-core/aperture-labs/wt-A", "junk") is None
        reloaded = tracking.load_record(tmp_path / "wt-A.yaml")
        assert reloaded.resources == []


class TestLocalClaimantAlive:
    """_local_claimant_alive resolves same-machine owners and biases to sparing:
    only a positively-resolved gone owner returns False; cross-machine is None."""

    def _wire(self, tmp_path, monkeypatch, machine="lambda-core"):
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
            "lambda-core", "wsl", tdir,
        )

    def test_cross_machine_is_unconfirmed(self, tmp_path, monkeypatch):
        self._wire(tmp_path, monkeypatch, machine="lambda-core")
        ref = "borealis/aperture-labs/wt-A#s1"
        assert m._local_claimant_alive(ref) is None

    def test_present_owner_is_alive(self, tmp_path, monkeypatch):
        self._wire(tmp_path, monkeypatch)
        self._make_owner(tmp_path, "aperture-labs", "wt-A", exists=True)
        assert m._local_claimant_alive("lambda-core/aperture-labs/wt-A#s1") is True

    def test_missing_record_is_gone(self, tmp_path, monkeypatch):
        self._wire(tmp_path, monkeypatch)
        # no owner record created
        assert m._local_claimant_alive("lambda-core/aperture-labs/wt-A") is False

    def test_missing_dir_is_gone(self, tmp_path, monkeypatch):
        self._wire(tmp_path, monkeypatch)
        self._make_owner(tmp_path, "aperture-labs", "wt-A", exists=False)
        assert m._local_claimant_alive("lambda-core/aperture-labs/wt-A") is False

    def test_empty_ref_is_none(self, tmp_path, monkeypatch):
        self._wire(tmp_path, monkeypatch)
        assert m._local_claimant_alive("") is None
