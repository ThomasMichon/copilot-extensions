"""Tests for the deployed CLI's `dev-release`/`dev-status` verbs
(mutable-dev-slot, #3376 Phase 2).

`dev-claim`/`slot`/`activate` stay installer-time only; these two verbs are
the ones that must work from the DEPLOYED `agent-codespaces` CLI without a
source checkout present, by shelling out to the versioned_runtime.py copy the
installer stages at RUNTIME_DIR. See docs/patterns/mutable-dev-slot.md.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_codespaces.__main__ import (
    _cmd_dev_release,
    _cmd_dev_status,
    _resolve_dev_slot_owner,
    _run_versioned_runtime,
    _versioned_runtime_helper_path,
    main,
)


def _fake_ns(owner=None, force=False):
    class Namespace:
        pass

    ns = Namespace()
    ns.owner = owner
    ns.force = force
    return ns


class TestVersionedRuntimeHelperPath:
    def test_absent_when_never_staged(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent_codespaces.__main__.RUNTIME_DIR", tmp_path)
        assert _versioned_runtime_helper_path() is None

    def test_present_when_staged(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent_codespaces.__main__.RUNTIME_DIR", tmp_path)
        (tmp_path / "versioned_runtime.py").write_text("# stub\n")
        assert _versioned_runtime_helper_path() == tmp_path / "versioned_runtime.py"


class TestResolveDevSlotOwner:
    def test_uses_agent_worktrees_when_available(self, monkeypatch):
        monkeypatch.setattr("agent_codespaces.__main__.shutil.which", lambda name: "/usr/bin/agent-worktrees")
        fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="/some/worktree\n", stderr="")
        with patch("agent_codespaces.__main__.subprocess.run", return_value=fake) as run:
            assert _resolve_dev_slot_owner() == "/some/worktree"
        run.assert_called_once()

    def test_falls_back_when_agent_worktrees_absent(self, monkeypatch):
        monkeypatch.setattr("agent_codespaces.__main__.shutil.which", lambda name: None)
        owner = _resolve_dev_slot_owner()
        assert owner  # non-empty
        assert Path(owner).is_absolute()

    def test_falls_back_on_nonzero_exit(self, monkeypatch):
        monkeypatch.setattr("agent_codespaces.__main__.shutil.which", lambda name: "/usr/bin/agent-worktrees")
        fake = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="not a worktree")
        with patch("agent_codespaces.__main__.subprocess.run", return_value=fake):
            owner = _resolve_dev_slot_owner()
        assert Path(owner).is_absolute()


class TestRunVersionedRuntime:
    def test_raises_when_helper_not_staged(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent_codespaces.__main__.RUNTIME_DIR", tmp_path)
        with pytest.raises(RuntimeError, match="has not been staged"):
            _run_versioned_runtime("dev-status")

    def test_invokes_helper_with_root_and_link_name(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agent_codespaces.__main__.RUNTIME_DIR", tmp_path)
        (tmp_path / "versioned_runtime.py").write_text("# stub\n")
        fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")
        with patch("agent_codespaces.__main__.subprocess.run", return_value=fake) as run:
            result = _run_versioned_runtime("dev-status", json_output=True)
        assert result.stdout == "ok"
        called_args = run.call_args.args[0]
        # --json is a GLOBAL flag on versioned_runtime's own parser -- it must
        # precede the subcommand token, not follow it.
        assert called_args[-2:] == ["--json", "dev-status"]
        assert "--root" in called_args
        assert str(tmp_path) in called_args
        assert "--link-name" in called_args
        assert ".venv" in called_args


class TestCmdDevStatus:
    def test_helper_not_staged_reports_error(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("agent_codespaces.__main__.RUNTIME_DIR", tmp_path)
        rc = _cmd_dev_status()
        assert rc == 1
        assert "dev-status" in capsys.readouterr().err

    def test_prints_claim_json(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("agent_codespaces.__main__.RUNTIME_DIR", tmp_path)
        (tmp_path / "versioned_runtime.py").write_text("# stub\n")
        record = {"schema": "copilot-extensions.dev-slot-claim", "owner": "/wt"}
        fake = subprocess.CompletedProcess(args=[], returncode=0, stdout=json.dumps(record), stderr="")
        with patch("agent_codespaces.__main__.subprocess.run", return_value=fake):
            rc = _cmd_dev_status()
        assert rc == 0
        assert json.loads(capsys.readouterr().out) == record

    def test_prints_null_when_unclaimed(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("agent_codespaces.__main__.RUNTIME_DIR", tmp_path)
        (tmp_path / "versioned_runtime.py").write_text("# stub\n")
        fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="null", stderr="")
        with patch("agent_codespaces.__main__.subprocess.run", return_value=fake):
            rc = _cmd_dev_status()
        assert rc == 0
        assert capsys.readouterr().out.strip() == "null"


class TestCmdDevRelease:
    def test_helper_not_staged_reports_error(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("agent_codespaces.__main__.RUNTIME_DIR", tmp_path)
        rc = _cmd_dev_release(_fake_ns(owner="/wt"))
        assert rc == 1
        assert "dev-release" in capsys.readouterr().err

    def test_conflicting_owner_without_force_exits_2(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("agent_codespaces.__main__.RUNTIME_DIR", tmp_path)
        (tmp_path / "versioned_runtime.py").write_text("# stub\n")
        fake = subprocess.CompletedProcess(
            args=[], returncode=2, stdout="", stderr="versioned_runtime: dev slot is already claimed by '/other'",
        )
        with patch("agent_codespaces.__main__.subprocess.run", return_value=fake):
            rc = _cmd_dev_release(_fake_ns(owner="/wt", force=False))
        assert rc == 2
        err = capsys.readouterr().err
        assert "--force" in err

    def test_no_claim_held_is_a_clean_noop(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("agent_codespaces.__main__.RUNTIME_DIR", tmp_path)
        (tmp_path / "versioned_runtime.py").write_text("# stub\n")
        fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="null", stderr="")
        with patch("agent_codespaces.__main__.subprocess.run", return_value=fake):
            rc = _cmd_dev_release(_fake_ns(owner="/wt"))
        assert rc == 0
        assert "nothing to release" in capsys.readouterr().out

    def test_success_restores_previous_version(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("agent_codespaces.__main__.RUNTIME_DIR", tmp_path)
        (tmp_path / "versioned_runtime.py").write_text("# stub\n")
        release_result = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=json.dumps({"owner": "/wt", "previous_version": "1.2.3"}), stderr="",
        )
        activate_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="activated", stderr="")

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return release_result if "dev-release" in cmd else activate_result

        with patch("agent_codespaces.__main__.subprocess.run", side_effect=fake_run):
            rc = _cmd_dev_release(_fake_ns(owner="/wt"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "1.2.3" in out
        assert any("activate" in c for c in calls)

    def test_missing_previous_version_leaves_current_untouched(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("agent_codespaces.__main__.RUNTIME_DIR", tmp_path)
        (tmp_path / "versioned_runtime.py").write_text("# stub\n")
        release_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps({"owner": "/wt"}), stderr="",
        )
        with patch("agent_codespaces.__main__.subprocess.run", return_value=release_result) as run:
            rc = _cmd_dev_release(_fake_ns(owner="/wt"))
        assert rc == 0
        assert "no previous_version" in capsys.readouterr().out
        run.assert_called_once()  # never tries to activate


class TestMainDispatch:
    def test_dev_status_command_routes(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("agent_codespaces.__main__.RUNTIME_DIR", tmp_path)
        rc = main(["dev-status"])
        assert rc == 1  # helper never staged in this tmp_path
        assert "dev-status" in capsys.readouterr().err

    def test_dev_release_command_routes(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("agent_codespaces.__main__.RUNTIME_DIR", tmp_path)
        rc = main(["dev-release", "--owner", "/wt"])
        assert rc == 1
        assert "dev-release" in capsys.readouterr().err
