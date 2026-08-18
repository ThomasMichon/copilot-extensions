"""Tests for claimant-liveness resolution (resource-claims Phase 3b)."""

from __future__ import annotations

import types
from datetime import datetime, timedelta

from agent_worktrees import claimant, tracking


def _iso_ago(**delta) -> str:
    """A naive ISO stamp ``delta`` in the past, matching ``tracking._now_iso``."""
    return (datetime.now() - timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%S")


def _cfg(machine="anomalous-potato"):
    return types.SimpleNamespace(
        machine=machine,
        default_repo=types.SimpleNamespace(anchor="/anchor"),
    )


def _seed_owner(tmp_path, monkeypatch, project, wt_id, *, exists=True,
                machine="anomalous-potato"):
    monkeypatch.setattr("agent_worktrees.config.load_config",
                        lambda *a, **k: _cfg(machine))
    monkeypatch.setattr("agent_worktrees.config.project_dir",
                        lambda name=None: tmp_path / f".{name}")
    wdir = tmp_path / "trees" / wt_id
    if exists:
        wdir.mkdir(parents=True, exist_ok=True)
    tdir = tmp_path / f".{project}" / "worktrees"
    tdir.mkdir(parents=True, exist_ok=True)
    tracking.create_new_record(
        wt_id, f"worktree/{wt_id}", str(wdir), project, machine, "wsl", tdir,
    )


# --- local_claimant_alive (same-machine) ------------------------------------

class TestLocalClaimantAlive:
    def test_present_owner_alive(self, tmp_path, monkeypatch):
        _seed_owner(tmp_path, monkeypatch, "test-chamber", "wt-A")
        assert claimant.local_claimant_alive(
            "anomalous-potato/test-chamber/wt-A#s1") is True

    def test_missing_record_gone(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent_worktrees.config.load_config",
                            lambda *a, **k: _cfg())
        monkeypatch.setattr("agent_worktrees.config.project_dir",
                            lambda name=None: tmp_path / f".{name}")
        assert claimant.local_claimant_alive(
            "anomalous-potato/test-chamber/wt-A") is False

    def test_missing_dir_gone(self, tmp_path, monkeypatch):
        _seed_owner(tmp_path, monkeypatch, "test-chamber", "wt-A", exists=False)
        assert claimant.local_claimant_alive(
            "anomalous-potato/test-chamber/wt-A") is False

    def test_finalized_parent_is_gone(self, tmp_path, monkeypatch):
        # Citadel E1b (#877): a finalized parent whose dir still exists no longer
        # protects its children -- the claimant gate reports it gone.
        _seed_owner(tmp_path, monkeypatch, "test-chamber", "wt-A")
        tdir = tmp_path / ".test-chamber" / "worktrees"
        rec = tracking.load_record(tdir / "wt-A.yaml")
        rec.status = "finalized"
        tracking.save_record(rec, tdir / "wt-A.yaml")
        assert claimant.local_claimant_alive(
            "anomalous-potato/test-chamber/wt-A") is False

    def test_orphaned_parent_is_gone(self, tmp_path, monkeypatch):
        _seed_owner(tmp_path, monkeypatch, "test-chamber", "wt-A")
        tdir = tmp_path / ".test-chamber" / "worktrees"
        rec = tracking.load_record(tdir / "wt-A.yaml")
        rec.status = "orphaned"
        tracking.save_record(rec, tdir / "wt-A.yaml")
        assert claimant.local_claimant_alive(
            "anomalous-potato/test-chamber/wt-A") is False

    def test_cross_machine_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent_worktrees.config.load_config",
                            lambda *a, **k: _cfg("anomalous-potato"))
        assert claimant.local_claimant_alive(
            "emancipation-cube/test-chamber/wt-A") is None

    def test_empty_ref_none(self):
        assert claimant.local_claimant_alive("") is None

    # --- dead-session detection ---------------------------------------------
    # A same-machine owner whose worktree DIR still exists but whose session is
    # confirmed dead (no live process, no fresh liveness hint, days-stale
    # activity) reports gone, so its in-flight claims are released.

    def _age_owner(self, tmp_path, project, wt_id, **fields):
        """Load the seeded owner record, patch fields, save. Returns the path."""
        tdir = tmp_path / f".{project}" / "worktrees"
        path = tdir / f"{wt_id}.yaml"
        rec = tracking.load_record(path)
        for k, v in fields.items():
            setattr(rec, k, v)
        tracking.save_record(rec, path)
        return path

    def test_dead_session_stale_activity_gone(self, tmp_path, monkeypatch):
        # Dir present, but no live session and all activity days-stale -> gone.
        _seed_owner(tmp_path, monkeypatch, "test-chamber", "wt-A")
        self._age_owner(tmp_path, "test-chamber", "wt-A",
                        started_at=_iso_ago(days=30),
                        last_resumed_at=_iso_ago(days=18))
        assert claimant.local_claimant_alive(
            "anomalous-potato/test-chamber/wt-A") is False

    def test_recent_activity_spares_dead_check(self, tmp_path, monkeypatch):
        # Old creation but a recent resume -> still alive (spare).
        _seed_owner(tmp_path, monkeypatch, "test-chamber", "wt-A")
        self._age_owner(tmp_path, "test-chamber", "wt-A",
                        started_at=_iso_ago(days=30),
                        last_resumed_at=_iso_ago(minutes=5))
        assert claimant.local_claimant_alive(
            "anomalous-potato/test-chamber/wt-A") is True

    def test_fresh_bound_live_hint_spares(self, tmp_path, monkeypatch):
        # A bare-resumed owner (invisible to the session scan) with a fresh
        # bound_live=True hint is spared even when created long ago.
        _seed_owner(tmp_path, monkeypatch, "test-chamber", "wt-A")
        self._age_owner(tmp_path, "test-chamber", "wt-A",
                        started_at=_iso_ago(days=30),
                        last_resumed_at=_iso_ago(days=18),
                        bound_live=True, bound_live_at=_iso_ago(minutes=2))
        assert claimant.local_claimant_alive(
            "anomalous-potato/test-chamber/wt-A") is True

    def test_stale_live_hint_not_spared(self, tmp_path, monkeypatch):
        # A stale mux_live=True hint (its *_at days old) is not a heartbeat.
        _seed_owner(tmp_path, monkeypatch, "test-chamber", "wt-A")
        self._age_owner(tmp_path, "test-chamber", "wt-A",
                        started_at=_iso_ago(days=30),
                        last_resumed_at=_iso_ago(days=18),
                        mux_live=True, mux_live_at=_iso_ago(days=10))
        assert claimant.local_claimant_alive(
            "anomalous-potato/test-chamber/wt-A") is False

    def test_confirmed_not_live_stamp_is_not_a_heartbeat(self, tmp_path, monkeypatch):
        # A FRESH bound_live=False stamp means "confirmed NOT live" -- it must
        # not spare a session whose real activity is days-stale.
        _seed_owner(tmp_path, monkeypatch, "test-chamber", "wt-A")
        self._age_owner(tmp_path, "test-chamber", "wt-A",
                        started_at=_iso_ago(days=30),
                        last_resumed_at=_iso_ago(days=18),
                        bound_live=False, bound_live_at=_iso_ago(minutes=1))
        assert claimant.local_claimant_alive(
            "anomalous-potato/test-chamber/wt-A") is False

    def test_dead_after_env_override(self, tmp_path, monkeypatch):
        # Under the default 24h threshold a 2-min-idle owner is alive; a tight
        # env override reclassifies it as dead.
        _seed_owner(tmp_path, monkeypatch, "test-chamber", "wt-A")
        self._age_owner(tmp_path, "test-chamber", "wt-A",
                        started_at=_iso_ago(minutes=2),
                        last_resumed_at=_iso_ago(minutes=2))
        assert claimant.local_claimant_alive(
            "anomalous-potato/test-chamber/wt-A") is True
        monkeypatch.setenv(claimant._DEAD_AFTER_ENV, "60")
        assert claimant.local_claimant_alive(
            "anomalous-potato/test-chamber/wt-A") is False

    # --- anchor owners (permanent; invert the "missing record => gone" rule) ---

    def _seed_anchor(self, tmp_path, monkeypatch, project, *, path_exists=True,
                     make_record=True, machine="anomalous-potato"):
        monkeypatch.setattr("agent_worktrees.config.load_config",
                            lambda *a, **k: _cfg(machine))
        monkeypatch.setattr("agent_worktrees.config.project_dir",
                            lambda name=None: tmp_path / f".{name}")
        adir = tmp_path / "anchors" / project
        if path_exists:
            adir.mkdir(parents=True, exist_ok=True)
        tdir = tmp_path / f".{project}" / "worktrees"
        tdir.mkdir(parents=True, exist_ok=True)
        if make_record:
            tracking.load_or_create_anchor_record(
                str(adir), project, machine, "wsl", tdir)

    def test_anchor_present_is_alive(self, tmp_path, monkeypatch):
        self._seed_anchor(tmp_path, monkeypatch, "spo-core")
        assert claimant.local_claimant_alive(
            "anomalous-potato/spo-core/@anchor") is True

    def test_anchor_missing_ledger_is_unconfirmed(self, tmp_path, monkeypatch):
        # A missing @anchor ledger is NOT proof the enlistment is gone -> spare
        # (the crucial asymmetry vs. a worktree, whose missing record => gone).
        self._seed_anchor(tmp_path, monkeypatch, "spo-core", make_record=False)
        assert claimant.local_claimant_alive(
            "anomalous-potato/spo-core/@anchor") is None

    def test_anchor_removed_checkout_is_gone(self, tmp_path, monkeypatch):
        # Only a removed anchor checkout is a confirmed gone.
        self._seed_anchor(tmp_path, monkeypatch, "spo-core", path_exists=False)
        assert claimant.local_claimant_alive(
            "anomalous-potato/spo-core/@anchor") is False

    def test_anchor_cross_machine_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent_worktrees.config.load_config",
                            lambda *a, **k: _cfg("anomalous-potato"))
        assert claimant.local_claimant_alive(
            "emancipation-cube/spo-core/@anchor") is None


# --- resolve_claimant_alive (fabric) ----------------------------------------

class TestResolveClaimantAlive:
    def test_same_machine_delegates_local(self, tmp_path, monkeypatch):
        _seed_owner(tmp_path, monkeypatch, "test-chamber", "wt-A")
        assert claimant.resolve_claimant_alive(
            "anomalous-potato/test-chamber/wt-A") is True

    def test_remote_disabled_by_env(self, monkeypatch):
        monkeypatch.setattr("agent_worktrees.config.load_config",
                            lambda *a, **k: _cfg("anomalous-potato"))
        monkeypatch.setenv(claimant._NO_REMOTE_ENV, "1")
        called = []
        monkeypatch.setattr(claimant, "_remote_claimant_alive",
                            lambda *a, **k: called.append(1) or True)
        assert claimant.resolve_claimant_alive(
            "emancipation-cube/test-chamber/wt-A") is None
        assert called == []

    def test_remote_disabled_by_flag(self, monkeypatch):
        monkeypatch.setattr("agent_worktrees.config.load_config",
                            lambda *a, **k: _cfg("anomalous-potato"))
        monkeypatch.delenv(claimant._NO_REMOTE_ENV, raising=False)
        assert claimant.resolve_claimant_alive(
            "emancipation-cube/test-chamber/wt-A", allow_remote=False) is None

    def test_cross_machine_invokes_remote(self, monkeypatch):
        monkeypatch.setattr("agent_worktrees.config.load_config",
                            lambda *a, **k: _cfg("anomalous-potato"))
        monkeypatch.delenv(claimant._NO_REMOTE_ENV, raising=False)
        seen = {}

        def _fake_remote(machine, project, owner_ref, *, timeout):
            seen.update(machine=machine, project=project, owner_ref=owner_ref)
            return False

        monkeypatch.setattr(claimant, "_remote_claimant_alive", _fake_remote)
        res = claimant.resolve_claimant_alive("emancipation-cube/test-chamber/wt-A")
        assert res is False
        assert seen["machine"] == "emancipation-cube" and seen["project"] == "test-chamber"


# --- remote transport helpers -----------------------------------------------

class TestRemoteHelpers:
    def test_parse_alive_variants(self):
        assert claimant._parse_alive('{"alive": true}') is True
        assert claimant._parse_alive('{"alive": false}') is False
        assert claimant._parse_alive('{"alive": null}') is None
        # tolerate surrounding banner noise
        assert claimant._parse_alive('login banner\n{"version":1,"alive":true}\n') is True
        assert claimant._parse_alive("not json") is None
        assert claimant._parse_alive("") is None

    def test_remote_probe_cmd_bash(self):
        cmd = claimant._remote_probe_cmd("bash", "test-chamber",
                                         "m/test-chamber/wt-A")
        assert cmd.startswith("bash -lc '")
        assert "test-chamber claimant-liveness m/test-chamber/wt-A --json" in cmd
        # The verb routes via the project binstub directly (no 'agent-worktrees'
        # prefix, which the project binstub does not accept).
        assert "agent-worktrees claimant-liveness" not in cmd

    def test_remote_probe_cmd_pwsh_encoded(self):
        import base64
        cmd = claimant._remote_probe_cmd("pwsh", "test-chamber",
                                         "m/test-chamber/wt-A")
        assert "-EncodedCommand" in cmd
        b64 = cmd.rsplit(" ", 1)[1]
        decoded = base64.b64decode(b64).decode("utf-16-le")
        assert "test-chamber claimant-liveness m/test-chamber/wt-A --json" in decoded

    def test_remote_no_machine_or_project_none(self):
        assert claimant._remote_claimant_alive(None, "p", "ref") is None
        assert claimant._remote_claimant_alive("m", None, "ref") is None

    def test_remote_unresolved_machine_none(self, monkeypatch):
        monkeypatch.setattr(claimant, "_resolve_machine_ssh", lambda k: None)
        assert claimant._remote_claimant_alive(
            "emancipation-cube", "test-chamber", "emancipation-cube/test-chamber/wt-A") is None

    def test_remote_ssh_error_none(self, monkeypatch):
        monkeypatch.setattr(claimant, "_resolve_machine_ssh",
                            lambda k: ("emancipation-cube-alias", "bash"))

        def _boom(*a, **k):
            raise OSError("ssh missing")

        monkeypatch.setattr(claimant.subprocess, "run", _boom)
        assert claimant._remote_claimant_alive(
            "emancipation-cube", "test-chamber", "emancipation-cube/test-chamber/wt-A") is None

    def test_remote_nonzero_returncode_none(self, monkeypatch):
        monkeypatch.setattr(claimant, "_resolve_machine_ssh",
                            lambda k: ("emancipation-cube-alias", "bash"))
        proc = types.SimpleNamespace(returncode=255, stdout="", stderr="down")
        monkeypatch.setattr(claimant.subprocess, "run", lambda *a, **k: proc)
        assert claimant._remote_claimant_alive(
            "emancipation-cube", "test-chamber", "emancipation-cube/test-chamber/wt-A") is None

    def test_remote_success_parses_alive(self, monkeypatch):
        monkeypatch.setattr(claimant, "_resolve_machine_ssh",
                            lambda k: ("emancipation-cube-alias", "bash"))
        proc = types.SimpleNamespace(
            returncode=0, stdout='{"version":1,"alive":false}', stderr="")
        monkeypatch.setattr(claimant.subprocess, "run", lambda *a, **k: proc)
        assert claimant._remote_claimant_alive(
            "emancipation-cube", "test-chamber", "emancipation-cube/test-chamber/wt-A") is False
