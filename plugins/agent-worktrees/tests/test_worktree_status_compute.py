"""Tests for `_worktree_status_compute`
(agent-worktrees-external-status-accelerator effort, Phase 2).

Mirrors `test_classify_daemon_wiring.py`'s own isolation convention: exercise
the actual production function against isolated, temporary tracking/config
directories, with the expensive git/session/lineage layers stubbed so these
tests never shell out or touch this host's real state.
"""

from __future__ import annotations

import types

import pytest

from agent_worktrees import git_ops, sessions, tracking


def _rec(path, *, worktree_id="wt1", machine="m1"):
    return tracking.WorktreeRecord(
        worktree_id=worktree_id, branch="worktree/" + worktree_id,
        worktree_path=str(path), repo="ext", machine=machine, platform="wsl",
        started_at="2026-06-01T10:00:00", last_resumed_at="2026-06-01T10:00:00",
        resume_count=2, title="a title", status="active", completed_at=None,
        sessions=None, prs=[], git_state=None, session_turns=None,
    )


def _fake_load_config(*, path=None, project=None, **_kw):
    return types.SimpleNamespace(
        default_repo=types.SimpleNamespace(remote="origin", default_branch="master"),
    )


def _wire_common_internals(monkeypatch, m, *, state=git_ops.WorktreeState.UNUSED):
    monkeypatch.setattr(m.cfg, "load_config", _fake_load_config)
    monkeypatch.setattr(
        m.git_ops, "classify_worktree",
        lambda *a, **k: git_ops.WorktreeStateInfo(state=state),
    )
    monkeypatch.setattr(
        m.sessions, "verify_worktree_active",
        lambda record: sessions.LiveVerdict(active=True, mux_live=True, source="mux"),
    )
    monkeypatch.setattr(
        m.disposition_history,
        "read",
        lambda worktree_id, limit=None, tracking_path=None: [],
    )


class TestWorktreeStatusComputeIsolated:
    def test_assembles_all_five_facts_for_a_real_record(self, monkeypatch, tmp_path):
        from agent_worktrees import __main__ as m

        project = "iso-proj"
        tracking_dir = tmp_path / project / "worktrees"
        tracking_dir.mkdir(parents=True)
        wt_path = tmp_path / "wt1"
        wt_path.mkdir()
        tracking.save_record(_rec(wt_path), tracking_dir / "wt1.yaml")

        monkeypatch.setattr(m.cfg, "project_dir", lambda name=None: tmp_path / (name or project))
        _wire_common_internals(monkeypatch, m)

        bundle = m._worktree_status_compute(project, "wt1")

        assert bundle["worktree_id"] == "wt1"
        assert bundle["project"] == project
        assert bundle["machine"] == "m1"
        assert set(bundle["facts"]) == {
            "git_state", "lineage", "liveness", "claims", "disposition",
        }
        assert bundle["facts"]["git_state"]["confirmed"] is True
        assert bundle["facts"]["git_state"]["value"]["state"] == git_ops.WorktreeState.UNUSED.value
        assert bundle["facts"]["liveness"]["value"]["active"] is True
        assert bundle["facts"]["claims"]["value"] == {"resources": [], "owner_ref": None}
        assert bundle["facts"]["disposition"]["value"]["title"] == "a title"
        assert bundle["facts"]["disposition"]["value"]["resume_count"] == 2
        assert bundle["facts"]["lineage"]["confirmed"] is True

    def test_rejects_a_record_whose_stored_identity_does_not_match_the_filename(
        self, monkeypatch, tmp_path
    ):
        """Copilot review finding: `load_record_by_id` resolves the record
        purely by filename (already path-traversal-validated), but never
        checks that the loaded YAML's own `worktree_id` field actually
        matches -- a tampered or concurrently-replaced `wt1.yaml` declaring
        `wt2` would otherwise produce a bundle labeled `wt1` but assembled
        from (and cached under) `wt2`'s facts."""
        from agent_worktrees import __main__ as m

        project = "iso-proj"
        tracking_dir = tmp_path / project / "worktrees"
        tracking_dir.mkdir(parents=True)
        wt_path = tmp_path / "wt1"
        wt_path.mkdir()
        mismatched = _rec(wt_path, worktree_id="wt2")  # declares a different id
        tracking.save_record(mismatched, tracking_dir / "wt1.yaml")

        monkeypatch.setattr(m.cfg, "project_dir", lambda name=None: tmp_path / (name or project))
        _wire_common_internals(monkeypatch, m)

        try:
            m._worktree_status_compute(project, "wt1")
            raised = False
        except ValueError:
            raised = True
        assert raised

    def test_disposition_history_reads_the_explicit_project_not_an_ambient_one(
        self, monkeypatch, tmp_path
    ):
        """Copilot review finding: disposition_history.read() previously
        resolved its sidecar through the ambient active project
        (cfg.tracking_dir()), but a cross-project daemon/CLI caller here
        never sets one -- it must use the explicit `project` argument's own
        tracking directory instead."""
        from agent_worktrees import __main__ as m

        project = "iso-proj"
        tracking_dir = tmp_path / project / "worktrees"
        tracking_dir.mkdir(parents=True)
        wt_path = tmp_path / "wt1"
        wt_path.mkdir()
        tracking.save_record(_rec(wt_path), tracking_dir / "wt1.yaml")

        monkeypatch.setattr(m.cfg, "project_dir", lambda name=None: tmp_path / (name or project))
        _wire_common_internals(monkeypatch, m)

        captured = {}

        def _fake_read(worktree_id, *, limit=None, tracking_path=None):
            captured["worktree_id"] = worktree_id
            captured["tracking_path"] = tracking_path
            return []

        monkeypatch.setattr(m.disposition_history, "read", _fake_read)

        m._worktree_status_compute(project, "wt1")
        assert captured["worktree_id"] == "wt1"
        assert captured["tracking_path"] == tracking_dir

    def test_unresolvable_worktree_raises_rather_than_guessing(self, monkeypatch, tmp_path):
        from agent_worktrees import __main__ as m

        project = "iso-proj"
        tracking_dir = tmp_path / project / "worktrees"
        tracking_dir.mkdir(parents=True)
        monkeypatch.setattr(m.cfg, "project_dir", lambda name=None: tmp_path / (name or project))

        with pytest.raises(ValueError):
            m._worktree_status_compute(project, "no-such-worktree")

    def test_a_failing_fact_reports_unconfirmed_without_raising_the_whole_bundle(
        self, monkeypatch, tmp_path
    ):
        from agent_worktrees import __main__ as m

        project = "iso-proj"
        tracking_dir = tmp_path / project / "worktrees"
        tracking_dir.mkdir(parents=True)
        wt_path = tmp_path / "wt1"
        wt_path.mkdir()
        tracking.save_record(_rec(wt_path), tracking_dir / "wt1.yaml")

        monkeypatch.setattr(m.cfg, "project_dir", lambda name=None: tmp_path / (name or project))
        _wire_common_internals(monkeypatch, m)

        def _boom(*a, **k):
            raise RuntimeError("git access failed")

        monkeypatch.setattr(m.git_ops, "classify_worktree", _boom)

        bundle = m._worktree_status_compute(project, "wt1")
        assert bundle["facts"]["git_state"] == {
            "value": None, "confirmed": False, "observed_at": bundle["facts"]["git_state"]["observed_at"],
        }
        # Every other fact still assembled normally -- one fact's failure
        # never aborts the whole bundle.
        assert bundle["facts"]["liveness"]["confirmed"] is True
        assert bundle["facts"]["claims"]["confirmed"] is True

    def test_fetch_failure_renders_git_state_unconfirmed_but_keeps_the_value(
        self, monkeypatch, tmp_path
    ):
        from agent_worktrees import __main__ as m

        project = "iso-proj"
        tracking_dir = tmp_path / project / "worktrees"
        tracking_dir.mkdir(parents=True)
        wt_path = tmp_path / "wt1"
        wt_path.mkdir()
        tracking.save_record(_rec(wt_path), tracking_dir / "wt1.yaml")

        monkeypatch.setattr(m.cfg, "project_dir", lambda name=None: tmp_path / (name or project))
        _wire_common_internals(monkeypatch, m)
        monkeypatch.setattr(
            m.git_ops, "classify_worktree",
            lambda *a, **k: git_ops.WorktreeStateInfo(
                state=git_ops.WorktreeState.UNUSED,
                fetch_requested=True,
                fetch_failed=True,
            ),
        )

        bundle = m._worktree_status_compute(project, "wt1")
        assert bundle["facts"]["git_state"]["confirmed"] is False
        assert bundle["facts"]["git_state"]["value"] is not None  # last-known value kept

    def test_classification_exception_falls_back_to_the_record_s_last_known_state(
        self, monkeypatch, tmp_path
    ):
        """Copilot review finding: a transient classification failure must
        not present as 'no information' when the durable record already
        carries a last-known git state."""
        from agent_worktrees import __main__ as m

        project = "iso-proj"
        tracking_dir = tmp_path / project / "worktrees"
        tracking_dir.mkdir(parents=True)
        wt_path = tmp_path / "wt1"
        wt_path.mkdir()
        record = _rec(wt_path)
        record.git_state = "dirty"
        tracking.save_record(record, tracking_dir / "wt1.yaml")

        monkeypatch.setattr(m.cfg, "project_dir", lambda name=None: tmp_path / (name or project))
        _wire_common_internals(monkeypatch, m)

        def _boom(*a, **k):
            raise RuntimeError("git access failed")

        monkeypatch.setattr(m.git_ops, "classify_worktree", _boom)

        bundle = m._worktree_status_compute(project, "wt1")
        assert bundle["facts"]["git_state"]["confirmed"] is False
        assert bundle["facts"]["git_state"]["value"] == {"state": "dirty"}

    def test_liveness_probe_exception_falls_back_to_the_record_s_last_known_hints(
        self, monkeypatch, tmp_path
    ):
        """Copilot review finding: a transient liveness-probe failure must
        not erase the durable record's own cached mux_live/bound_live
        hints (see tracking.stamp_mux_live/stamp_bound_live)."""
        from agent_worktrees import __main__ as m

        project = "iso-proj"
        tracking_dir = tmp_path / project / "worktrees"
        tracking_dir.mkdir(parents=True)
        wt_path = tmp_path / "wt1"
        wt_path.mkdir()
        record = _rec(wt_path)
        record.mux_live = True
        record.bound_live = False
        tracking.save_record(record, tracking_dir / "wt1.yaml")

        monkeypatch.setattr(m.cfg, "project_dir", lambda name=None: tmp_path / (name or project))
        _wire_common_internals(monkeypatch, m)

        def _boom(_record):
            raise RuntimeError("liveness probe failed")

        monkeypatch.setattr(m.sessions, "verify_worktree_active", _boom)

        bundle = m._worktree_status_compute(project, "wt1")
        assert bundle["facts"]["liveness"]["confirmed"] is False
        assert bundle["facts"]["liveness"]["value"] == {
            "mux_live": True, "bound_live": False,
        }

    def test_claims_fact_includes_the_owner_ref_backward_link(self, monkeypatch, tmp_path):
        """Copilot review finding: the claims fact previously serialized only
        `record.resources` (the outward ledger); a consumer also needs
        `record.owner_ref` (the inward link) to answer claim ownership in
        both directions, per the vision's own bidirectional contract."""
        from agent_worktrees import __main__ as m

        project = "iso-proj"
        tracking_dir = tmp_path / project / "worktrees"
        tracking_dir.mkdir(parents=True)
        wt_path = tmp_path / "wt1"
        wt_path.mkdir()
        record = _rec(wt_path)
        record.owner_ref = "knowledge:some-parent-worktree"
        tracking.save_record(record, tracking_dir / "wt1.yaml")

        monkeypatch.setattr(m.cfg, "project_dir", lambda name=None: tmp_path / (name or project))
        _wire_common_internals(monkeypatch, m)

        bundle = m._worktree_status_compute(project, "wt1")
        assert bundle["facts"]["claims"]["value"] == {
            "resources": [], "owner_ref": "knowledge:some-parent-worktree",
        }

    def test_each_fact_is_timestamped_at_its_own_observation_not_bundle_start(
        self, monkeypatch, tmp_path
    ):
        """Copilot review finding: `now` was previously captured once before
        every probe and reused for every fact's `observed_at` and the
        bundle's `as_of` -- so a slow fetch=True git probe made those
        timestamps describe the start of the bundle, not when each fact was
        actually observed, and `as_of` could already be stale on return.
        Each fact must be stamped after its own probe, and `as_of` computed
        at completion."""
        from agent_worktrees import __main__ as m
        import time as time_mod

        project = "iso-proj"
        tracking_dir = tmp_path / project / "worktrees"
        tracking_dir.mkdir(parents=True)
        wt_path = tmp_path / "wt1"
        wt_path.mkdir()
        tracking.save_record(_rec(wt_path), tracking_dir / "wt1.yaml")

        monkeypatch.setattr(m.cfg, "project_dir", lambda name=None: tmp_path / (name or project))
        _wire_common_internals(monkeypatch, m)

        # Simulate a slow git probe: time visibly advances between the
        # start of compute and the git_state fact's own observation.
        clock = {"t": 1000.0}

        def _real_advancing_time():
            clock["t"] += 1.0
            return clock["t"]

        monkeypatch.setattr(time_mod, "time", _real_advancing_time)

        bundle = m._worktree_status_compute(project, "wt1")

        git_observed_at = bundle["facts"]["git_state"]["observed_at"]
        disposition_observed_at = bundle["facts"]["disposition"]["observed_at"]
        assert disposition_observed_at > git_observed_at
        # as_of (completion) must be at least as late as every fact's own
        # observed_at -- never earlier, as a start-of-bundle stamp would be.
        assert bundle["as_of"] >= disposition_observed_at
        assert bundle["started_at"] <= git_observed_at

    def test_liveness_fact_is_unconfirmed_when_verify_worktree_active_degrades(
        self, monkeypatch, tmp_path
    ):
        """Copilot review finding: `sessions.verify_worktree_active` is
        fail-open -- it swallows a mux or reclaim probe failure internally
        and returns a default/partial `LiveVerdict` rather than raising, so
        the compute's own `try/except` around it never fires. Hard-coding
        `confirmed=True` for the liveness fact therefore let a degraded
        probe look fully confirmed. Also, serializing that degraded verdict
        directly discarded the record's own last-known `mux_live`/
        `bound_live` hints -- exactly the transient-failure case the
        bundle contract says to retain them for. `LiveVerdict.probes_ok`
        must gate both `confirmed` and whether the last-known hints are
        used instead of the degraded verdict."""
        from agent_worktrees import __main__ as m

        project = "iso-proj"
        tracking_dir = tmp_path / project / "worktrees"
        tracking_dir.mkdir(parents=True)
        wt_path = tmp_path / "wt1"
        wt_path.mkdir()
        record = _rec(wt_path)
        record.mux_live = True
        record.bound_live = False
        tracking.save_record(record, tracking_dir / "wt1.yaml")

        monkeypatch.setattr(m.cfg, "project_dir", lambda name=None: tmp_path / (name or project))
        _wire_common_internals(monkeypatch, m)
        monkeypatch.setattr(
            m.sessions,
            "verify_worktree_active",
            lambda record: sessions.LiveVerdict(
                active=False, mux_live=False, source="none", probes_ok=False,
            ),
        )

        bundle = m._worktree_status_compute(project, "wt1")

        assert bundle["facts"]["liveness"]["confirmed"] is False
        # Not the degraded verdict -- the record's own last-known hints.
        assert bundle["facts"]["liveness"]["value"] == {
            "mux_live": True, "bound_live": False,
        }

