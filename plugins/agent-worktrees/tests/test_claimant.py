"""Tests for claimant-liveness resolution (resource-claims Phase 3b)."""

from __future__ import annotations

import types

from agent_worktrees import claimant, tracking


def _cfg(machine="lambda-core"):
    return types.SimpleNamespace(
        machine=machine,
        default_repo=types.SimpleNamespace(anchor="/anchor"),
    )


def _seed_owner(tmp_path, monkeypatch, project, wt_id, *, exists=True,
                machine="lambda-core"):
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
        _seed_owner(tmp_path, monkeypatch, "aperture-labs", "wt-A")
        assert claimant.local_claimant_alive(
            "lambda-core/aperture-labs/wt-A#s1") is True

    def test_missing_record_gone(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent_worktrees.config.load_config",
                            lambda *a, **k: _cfg())
        monkeypatch.setattr("agent_worktrees.config.project_dir",
                            lambda name=None: tmp_path / f".{name}")
        assert claimant.local_claimant_alive(
            "lambda-core/aperture-labs/wt-A") is False

    def test_missing_dir_gone(self, tmp_path, monkeypatch):
        _seed_owner(tmp_path, monkeypatch, "aperture-labs", "wt-A", exists=False)
        assert claimant.local_claimant_alive(
            "lambda-core/aperture-labs/wt-A") is False

    def test_finalized_parent_is_gone(self, tmp_path, monkeypatch):
        # Citadel E1b (#877): a finalized parent whose dir still exists no longer
        # protects its children -- the claimant gate reports it gone.
        _seed_owner(tmp_path, monkeypatch, "aperture-labs", "wt-A")
        tdir = tmp_path / ".aperture-labs" / "worktrees"
        rec = tracking.load_record(tdir / "wt-A.yaml")
        rec.status = "finalized"
        tracking.save_record(rec, tdir / "wt-A.yaml")
        assert claimant.local_claimant_alive(
            "lambda-core/aperture-labs/wt-A") is False

    def test_orphaned_parent_is_gone(self, tmp_path, monkeypatch):
        _seed_owner(tmp_path, monkeypatch, "aperture-labs", "wt-A")
        tdir = tmp_path / ".aperture-labs" / "worktrees"
        rec = tracking.load_record(tdir / "wt-A.yaml")
        rec.status = "orphaned"
        tracking.save_record(rec, tdir / "wt-A.yaml")
        assert claimant.local_claimant_alive(
            "lambda-core/aperture-labs/wt-A") is False

    def test_cross_machine_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent_worktrees.config.load_config",
                            lambda *a, **k: _cfg("lambda-core"))
        assert claimant.local_claimant_alive(
            "borealis/aperture-labs/wt-A") is None

    def test_empty_ref_none(self):
        assert claimant.local_claimant_alive("") is None

    # --- anchor owners (permanent; invert the "missing record => gone" rule) ---

    def _seed_anchor(self, tmp_path, monkeypatch, project, *, path_exists=True,
                     make_record=True, machine="lambda-core"):
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
            "lambda-core/spo-core/@anchor") is True

    def test_anchor_missing_ledger_is_unconfirmed(self, tmp_path, monkeypatch):
        # A missing @anchor ledger is NOT proof the enlistment is gone -> spare
        # (the crucial asymmetry vs. a worktree, whose missing record => gone).
        self._seed_anchor(tmp_path, monkeypatch, "spo-core", make_record=False)
        assert claimant.local_claimant_alive(
            "lambda-core/spo-core/@anchor") is None

    def test_anchor_removed_checkout_is_gone(self, tmp_path, monkeypatch):
        # Only a removed anchor checkout is a confirmed gone.
        self._seed_anchor(tmp_path, monkeypatch, "spo-core", path_exists=False)
        assert claimant.local_claimant_alive(
            "lambda-core/spo-core/@anchor") is False

    def test_anchor_cross_machine_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent_worktrees.config.load_config",
                            lambda *a, **k: _cfg("lambda-core"))
        assert claimant.local_claimant_alive(
            "borealis/spo-core/@anchor") is None


# --- resolve_claimant_alive (fabric) ----------------------------------------

class TestResolveClaimantAlive:
    def test_same_machine_delegates_local(self, tmp_path, monkeypatch):
        _seed_owner(tmp_path, monkeypatch, "aperture-labs", "wt-A")
        assert claimant.resolve_claimant_alive(
            "lambda-core/aperture-labs/wt-A") is True

    def test_remote_disabled_by_env(self, monkeypatch):
        monkeypatch.setattr("agent_worktrees.config.load_config",
                            lambda *a, **k: _cfg("lambda-core"))
        monkeypatch.setenv(claimant._NO_REMOTE_ENV, "1")
        called = []
        monkeypatch.setattr(claimant, "_remote_claimant_alive",
                            lambda *a, **k: called.append(1) or True)
        assert claimant.resolve_claimant_alive(
            "borealis/aperture-labs/wt-A") is None
        assert called == []

    def test_remote_disabled_by_flag(self, monkeypatch):
        monkeypatch.setattr("agent_worktrees.config.load_config",
                            lambda *a, **k: _cfg("lambda-core"))
        monkeypatch.delenv(claimant._NO_REMOTE_ENV, raising=False)
        assert claimant.resolve_claimant_alive(
            "borealis/aperture-labs/wt-A", allow_remote=False) is None

    def test_cross_machine_invokes_remote(self, monkeypatch):
        monkeypatch.setattr("agent_worktrees.config.load_config",
                            lambda *a, **k: _cfg("lambda-core"))
        monkeypatch.delenv(claimant._NO_REMOTE_ENV, raising=False)
        seen = {}

        def _fake_remote(machine, project, owner_ref, *, timeout):
            seen.update(machine=machine, project=project, owner_ref=owner_ref)
            return False

        monkeypatch.setattr(claimant, "_remote_claimant_alive", _fake_remote)
        res = claimant.resolve_claimant_alive("borealis/aperture-labs/wt-A")
        assert res is False
        assert seen["machine"] == "borealis" and seen["project"] == "aperture-labs"


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
        cmd = claimant._remote_probe_cmd("bash", "aperture-labs",
                                         "m/aperture-labs/wt-A")
        assert cmd.startswith("bash -lc '")
        assert "aperture-labs claimant-liveness m/aperture-labs/wt-A --json" in cmd
        # The verb routes via the project binstub directly (no 'agent-worktrees'
        # prefix, which the project binstub does not accept).
        assert "agent-worktrees claimant-liveness" not in cmd

    def test_remote_probe_cmd_pwsh_encoded(self):
        import base64
        cmd = claimant._remote_probe_cmd("pwsh", "aperture-labs",
                                         "m/aperture-labs/wt-A")
        assert "-EncodedCommand" in cmd
        b64 = cmd.rsplit(" ", 1)[1]
        decoded = base64.b64decode(b64).decode("utf-16-le")
        assert "aperture-labs claimant-liveness m/aperture-labs/wt-A --json" in decoded

    def test_remote_no_machine_or_project_none(self):
        assert claimant._remote_claimant_alive(None, "p", "ref") is None
        assert claimant._remote_claimant_alive("m", None, "ref") is None

    def test_remote_unresolved_machine_none(self, monkeypatch):
        monkeypatch.setattr(claimant, "_resolve_machine_ssh", lambda k: None)
        assert claimant._remote_claimant_alive(
            "borealis", "aperture-labs", "borealis/aperture-labs/wt-A") is None

    def test_remote_ssh_error_none(self, monkeypatch):
        monkeypatch.setattr(claimant, "_resolve_machine_ssh",
                            lambda k: ("borealis-alias", "bash"))

        def _boom(*a, **k):
            raise OSError("ssh missing")

        monkeypatch.setattr(claimant.subprocess, "run", _boom)
        assert claimant._remote_claimant_alive(
            "borealis", "aperture-labs", "borealis/aperture-labs/wt-A") is None

    def test_remote_nonzero_returncode_none(self, monkeypatch):
        monkeypatch.setattr(claimant, "_resolve_machine_ssh",
                            lambda k: ("borealis-alias", "bash"))
        proc = types.SimpleNamespace(returncode=255, stdout="", stderr="down")
        monkeypatch.setattr(claimant.subprocess, "run", lambda *a, **k: proc)
        assert claimant._remote_claimant_alive(
            "borealis", "aperture-labs", "borealis/aperture-labs/wt-A") is None

    def test_remote_success_parses_alive(self, monkeypatch):
        monkeypatch.setattr(claimant, "_resolve_machine_ssh",
                            lambda k: ("borealis-alias", "bash"))
        proc = types.SimpleNamespace(
            returncode=0, stdout='{"version":1,"alive":false}', stderr="")
        monkeypatch.setattr(claimant.subprocess, "run", lambda *a, **k: proc)
        assert claimant._remote_claimant_alive(
            "borealis", "aperture-labs", "borealis/aperture-labs/wt-A") is False
