"""Tests for the process-boundary engine client (Phase 6b).

The Worktree Manager reaches the agent-worktrees engine ONLY by shelling out to
its ``--json`` verbs (never importing it). These tests drive that seam with a
faked ``subprocess.run`` + ``engine_path`` so no real engine is required, and
assert the parsing, the version-skew retry, and the error paths.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from worktree_manager import engine_client as ec


@pytest.fixture(autouse=True)
def _reset_engine_resolution(monkeypatch):
    ec.set_engine_command(None)
    monkeypatch.setattr(ec, "_INHERITED_ENGINE_COMMAND", None)
    monkeypatch.delenv(ec.ENGINE_ARGV_ENV, raising=False)
    monkeypatch.delenv(ec.ENGINE_CMD_ENV, raising=False)


def _fake_completed(cmd, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)


def _install_fake(monkeypatch, handler):
    """Point the client at a fake engine binstub + a scripted ``subprocess.run``."""
    monkeypatch.delenv(ec.ENGINE_ARGV_ENV, raising=False)
    monkeypatch.delenv(ec.ENGINE_CMD_ENV, raising=False)
    monkeypatch.setattr(ec, "_INHERITED_ENGINE_COMMAND", None)
    ec.set_engine_command(None)
    monkeypatch.setattr(
        ec, "installed_engine_command", lambda: ["/fake/agent-worktrees"]
    )
    monkeypatch.setattr(ec.subprocess, "run",
                        lambda cmd, **kw: handler(cmd, kw))


_ONE_WT = {
    "version": 1,
    "worktrees": [
        {
            "id": "example-cloud1-win-20260813-1200-ab12",
            "repo": "dotfiles",
            "machine": "cloud1",
            "branch": "worktree/x",
            "title": "fix the thing",
            "state": "wip",
            "ahead": 2,
            "behind": 1,
            "dirty": True,
            "status": "active",
            "path": "/w/x",
        }
    ],
}


def test_list_worktrees_parses_rows(monkeypatch):
    def handler(cmd, kw):
        assert "--project" in cmd and "dotfiles" in cmd
        assert "list" in cmd and "--json" in cmd and "--classify" in cmd
        return _fake_completed(cmd, stdout=json.dumps(_ONE_WT))

    _install_fake(monkeypatch, handler)
    wts = ec.list_worktrees("dotfiles")
    assert len(wts) == 1
    w = wts[0]
    assert w.repo == "dotfiles" and w.machine == "cloud1"
    assert w.state == "wip" and w.ahead == 2 and w.behind == 1 and w.dirty
    assert w.id4 == "ab12"
    assert w.sync_tag == "\u21912\u21931"
    assert w.title == "fix the thing"


def test_title_null_is_none(monkeypatch):
    payload = {"version": 1, "worktrees": [{"id": "aaaa", "title": "null"}]}
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(cmd, stdout=json.dumps(payload)))
    (w,) = ec.list_worktrees("dotfiles")
    assert w.title is None
    assert w.id4 == "aaaa"


def test_engine_absent_raises_install_hint(monkeypatch):
    monkeypatch.delenv(ec.ENGINE_ARGV_ENV, raising=False)
    monkeypatch.delenv(ec.ENGINE_CMD_ENV, raising=False)
    monkeypatch.setattr(ec, "_INHERITED_ENGINE_COMMAND", None)
    ec.set_engine_command(None)
    monkeypatch.setattr(ec, "installed_engine_command", lambda: None)
    assert ec.engine_available() is False
    with pytest.raises(ec.EngineError) as ei:
        ec.list_worktrees("dotfiles")
    assert ei.value.install_hint is True


def test_classify_rejection_retries_without(monkeypatch):
    calls = []

    def handler(cmd, kw):
        calls.append(list(cmd))
        if "--classify" in cmd:
            # An older engine rejects the unknown flag.
            return _fake_completed(cmd, returncode=2, stderr="unrecognized arguments: --classify")
        return _fake_completed(cmd, stdout=json.dumps(_ONE_WT))

    _install_fake(monkeypatch, handler)
    wts = ec.list_worktrees("dotfiles")
    assert len(wts) == 1
    # First attempt carried --classify; the retry dropped it.
    assert any("--classify" in c for c in calls)
    assert any("--classify" not in c for c in calls)


def test_error_envelope_is_surfaced(monkeypatch):
    payload = json.dumps({"version": 1, "error": "no such project"})
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(cmd, returncode=1, stdout=payload))
    with pytest.raises(ec.EngineError) as ei:
        ec.list_worktrees("dotfiles", classify=False)
    assert "no such project" in str(ei.value)


def test_invalid_json_raises(monkeypatch):
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(cmd, stdout="not json"))
    with pytest.raises(ec.EngineError):
        ec.list_worktrees("dotfiles", classify=False)


def test_timeout_raises_engine_error(monkeypatch):
    monkeypatch.delenv(ec.ENGINE_ARGV_ENV, raising=False)
    monkeypatch.delenv(ec.ENGINE_CMD_ENV, raising=False)
    ec.set_engine_command(None)
    monkeypatch.setattr(
        ec, "installed_engine_command", lambda: ["/fake/agent-worktrees"]
    )

    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(ec.subprocess, "run", boom)
    with pytest.raises(ec.EngineError) as ei:
        ec.list_worktrees("dotfiles", classify=False)
    assert "timed out" in str(ei.value)


def test_empty_worktrees_list(monkeypatch):
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(
        cmd, stdout=json.dumps({"version": 1, "worktrees": []})))
    assert ec.list_worktrees("dotfiles") == []


def test_inherited_provider_argv_wins(monkeypatch):
    monkeypatch.setenv(
        ec.ENGINE_ARGV_ENV,
        json.dumps(["/runtime/python", "-m", "agent_worktrees"]),
    )
    monkeypatch.delenv(ec.ENGINE_CMD_ENV, raising=False)
    ec.set_engine_command(None)
    assert ec.engine_base_command() == [
        "/runtime/python", "-m", "agent_worktrees"
    ]


def test_explicit_command_override_wins_over_inherited_provider(monkeypatch):
    monkeypatch.setenv(
        ec.ENGINE_ARGV_ENV,
        json.dumps(["/runtime/python", "-m", "agent_worktrees"]),
    )
    monkeypatch.setenv(ec.ENGINE_CMD_ENV, "/fake/engine")
    ec.set_engine_command(None)
    assert ec.engine_base_command() == ["/fake/engine"]


def test_accept_inherited_provider_removes_child_environment(monkeypatch):
    inherited = ["/runtime/python", "-m", "agent_worktrees"]
    monkeypatch.setenv(ec.ENGINE_ARGV_ENV, json.dumps(inherited))
    monkeypatch.setattr(ec, "_INHERITED_ENGINE_COMMAND", None)
    assert ec.accept_inherited_engine_command() is None
    assert ec.ENGINE_ARGV_ENV not in ec.os.environ
    assert ec.engine_base_command() == inherited


def test_invalid_inherited_provider_argv_fails_closed(monkeypatch):
    monkeypatch.setenv(ec.ENGINE_ARGV_ENV, '{"not":"argv"}')
    ec.set_engine_command(None)
    with pytest.raises(ec.EngineError, match=ec.ENGINE_ARGV_ENV):
        ec.engine_base_command()


def test_accept_invalid_inherited_provider_keeps_recovery_commands_usable(
    monkeypatch,
):
    monkeypatch.setenv(ec.ENGINE_ARGV_ENV, '{"not":"argv"}')
    warning = ec.accept_inherited_engine_command()
    assert ec.ENGINE_ARGV_ENV not in ec.os.environ
    assert warning and ec.ENGINE_ARGV_ENV in warning


def test_installed_engine_command_validates_manifest(monkeypatch, tmp_path):
    root = tmp_path / ".agent-worktrees"
    slot = root / "versions" / "1.2.3"
    python = slot / ("Scripts/python.exe" if ec.os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    (slot / ".install-complete.json").write_text("{}", encoding="utf-8")
    (root / "current-version").write_text("1.2.3", encoding="utf-8")
    (root / "deploy-manifest.json").write_text(
        json.dumps({
            "service": "agent-worktrees",
            "source": {
                "plugin": "agent-worktrees",
                "version": "1.2.3",
            },
            "venv": str(slot),
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AGENT_HOME", raising=False)
    assert ec.installed_engine_command() == [
        str(python), "-m", "agent_worktrees"
    ]


def test_installed_engine_command_allows_manifest_version_skew(monkeypatch, tmp_path):
    root = tmp_path / ".agent-worktrees"
    slot = root / "versions" / "1.2.3"
    slot.mkdir(parents=True)
    (slot / ".install-complete.json").write_text("{}", encoding="utf-8")
    (root / "current-version").write_text("1.2.3", encoding="utf-8")
    (root / "deploy-manifest.json").write_text(
        json.dumps({
            "service": "agent-worktrees",
            "source": {
                "plugin": "agent-worktrees",
                "version": "1.2.2",
            },
            "venv": str(slot),
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AGENT_HOME", raising=False)
    python = slot / ("Scripts/python.exe" if ec.os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("", encoding="utf-8")
    assert ec.installed_engine_command() == [
        str(python), "-m", "agent_worktrees"
    ]


def test_installed_engine_command_falls_back_to_last_known_good(
    monkeypatch, tmp_path
):
    root = tmp_path / ".agent-worktrees"
    slot = root / "versions" / "1.2.2"
    python = slot / ("Scripts/python.exe" if ec.os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    (slot / ".install-complete.json").write_text("{}", encoding="utf-8")
    (root / "current-version").write_text("missing", encoding="utf-8")
    (root / "last-known-good").write_text("1.2.2", encoding="utf-8")
    (root / "deploy-manifest.json").write_text(
        json.dumps({
            "service": "agent-worktrees",
            "source": {"plugin": "agent-worktrees", "version": "1.2.3"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AGENT_HOME", raising=False)
    assert ec.installed_engine_command() == [
        str(python), "-m", "agent_worktrees"
    ]


def test_run_json_can_preserve_structured_nonzero_result(monkeypatch):
    payload = {"ok": False, "reason": "in use"}
    _install_fake(
        monkeypatch,
        lambda cmd, kw: _fake_completed(
            cmd, returncode=1, stdout=json.dumps(payload)
        ),
    )
    assert ec.run_json(
        "dotfiles", ["restart", "wt-1", "--json"], allow_nonzero=True
    ) == payload


def test_run_json_nonzero_without_envelope_preserves_stderr(monkeypatch):
    _install_fake(
        monkeypatch,
        lambda cmd, kw: _fake_completed(
            cmd, returncode=2, stderr="unrecognized arguments: --future-flag"
        ),
    )
    with pytest.raises(ec.EngineError, match="--future-flag"):
        ec.run_json(
            "dotfiles",
            ["cleanup", "--future-flag", "--json"],
            allow_nonzero=True,
        )


def test_captured_engine_call_is_tui_safe(monkeypatch):
    seen = {}

    def handler(cmd, kw):
        seen.update(kw)
        return _fake_completed(cmd, stdout='{"version":1,"worktrees":[]}')

    _install_fake(monkeypatch, handler)
    ec.list_worktrees("dotfiles")
    assert seen["stdin"] is subprocess.DEVNULL
    assert seen["env"]["PYTHONSAFEPATH"] == "1"
    if ec.os.name == "nt":
        assert seen["creationflags"] == subprocess.CREATE_NO_WINDOW


# ── resolve_launch_plan (slice 3) ─────────────────────────────────────────────

_RESUME_PLAN = {
    "action": "exec",
    "work_dir": "/w/x",
    "status_path": "/w/x",
    "cmd": ["copilot", "--resume=sess123"],
    "env": {"COPILOT_CUSTOM_INSTRUCTIONS_DIRS": "/home/u/.dotfiles"},
    "worktree_id": "m-win-1200-ab12",
    "post_exit": True,
    "no_mux": True,
}


def test_resolve_resume_parses_plan(monkeypatch):
    def handler(cmd, kw):
        assert "resolve" in cmd and "--json" in cmd
        assert "--worktree-id" in cmd and "m-win-1200-ab12" in cmd
        assert "--new" not in cmd
        return _fake_completed(cmd, stdout=json.dumps(_RESUME_PLAN))

    _install_fake(monkeypatch, handler)
    plan = ec.resolve_launch_plan("dotfiles", worktree_id="m-win-1200-ab12")
    assert plan.is_exec and plan.no_mux is True
    assert plan.cmd == ["copilot", "--resume=sess123"]
    assert plan.work_dir == "/w/x" and plan.worktree_id == "m-win-1200-ab12"
    assert plan.post_exit is True


def test_resolve_new_sends_new_flag(monkeypatch):
    def handler(cmd, kw):
        assert "--new" in cmd and "--worktree-id" not in cmd
        return _fake_completed(cmd, stdout=json.dumps(_RESUME_PLAN))

    _install_fake(monkeypatch, handler)
    plan = ec.resolve_launch_plan("dotfiles", new=True)
    assert plan.is_exec


def test_resolve_base_sends_base_flag(monkeypatch):
    def handler(cmd, kw):
        assert "--base" in cmd
        assert "--new" not in cmd and "--worktree-id" not in cmd
        return _fake_completed(cmd, stdout=json.dumps(_RESUME_PLAN))

    _install_fake(monkeypatch, handler)
    plan = ec.resolve_launch_plan("dotfiles", base=True)
    assert plan.is_exec


def test_resolve_base_skew_retries_non_json_plan(monkeypatch):
    calls = []

    def handler(cmd, kw):
        calls.append(list(cmd))
        if "--json" in cmd:
            return _fake_completed(
                cmd,
                returncode=2,
                stdout=json.dumps({
                    "version": 1,
                    "error": "--json requires --worktree-id or --new",
                }),
            )
        return _fake_completed(cmd, stdout=json.dumps(_RESUME_PLAN))

    _install_fake(monkeypatch, handler)
    plan = ec.resolve_launch_plan("dotfiles", base=True)
    assert plan.is_exec
    assert any("--json" in call for call in calls)
    assert any("--base" in call and "--json" not in call for call in calls)


def test_resolve_remote_sends_environment_and_action(monkeypatch):
    remote = {
        "action": "remote",
        "ssh_alias": "example-wsl",
        "remote_command": "dotfiles --worktree-id wt-1 --bare-resume --no-mux",
        "machine": "example",
        "display_name": "Example WSL",
    }

    def handler(cmd, kw):
        assert cmd[-8:] == [
            "--worktree-id",
            "wt-1",
            "--bare-resume",
            "--machine",
            "Example",
            "--environment",
            "WSL",
            "--target-no-mux",
        ]
        return _fake_completed(cmd, stdout=json.dumps(remote))

    _install_fake(monkeypatch, handler)
    plan = ec.resolve_launch_plan(
        "dotfiles",
        worktree_id="wt-1",
        bare_resume=True,
        target_machine="Example",
        target_environment="WSL",
        target_no_mux=True,
    )
    assert plan.action == "remote"
    assert plan.raw["ssh_alias"] == "example-wsl"


def test_resolve_remote_skew_reports_feature_unavailable(monkeypatch):
    _install_fake(
        monkeypatch,
        lambda cmd, kw: _fake_completed(
            cmd,
            returncode=2,
            stderr="unrecognized arguments: --environment WSL",
        ),
    )
    with pytest.raises(ec.EngineFeatureUnavailable):
        ec.resolve_launch_plan(
            "dotfiles",
            new=True,
            target_machine="Example",
            target_environment="WSL",
        )


def test_resolve_remote_real_error_is_not_misclassified(monkeypatch):
    payload = json.dumps({"version": 1, "error": "no such worktree"})
    _install_fake(
        monkeypatch,
        lambda cmd, kw: _fake_completed(cmd, returncode=1, stdout=payload),
    )
    with pytest.raises(ec.EngineError, match="no such worktree") as error:
        ec.resolve_launch_plan(
            "dotfiles",
            worktree_id="missing",
            target_machine="Example",
            target_environment="WSL",
        )
    assert not isinstance(error.value, ec.EngineFeatureUnavailable)


def test_resolve_remote_base_skew_does_not_fall_back_locally(monkeypatch):
    calls = []

    def handler(cmd, kw):
        calls.append(list(cmd))
        return _fake_completed(
            cmd,
            returncode=2,
            stderr="unrecognized arguments: --base --machine Example",
        )

    _install_fake(monkeypatch, handler)
    with pytest.raises(ec.EngineFeatureUnavailable):
        ec.resolve_launch_plan(
            "dotfiles",
            base=True,
            target_machine="Example",
            target_environment="WSL",
        )
    assert len(calls) == 1


def test_resolve_requires_a_target(monkeypatch):
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(cmd))
    with pytest.raises(ec.EngineError):
        ec.resolve_launch_plan("dotfiles")


def test_resolve_worktree_and_new_are_exclusive(monkeypatch):
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(cmd))
    with pytest.raises(ec.EngineError):
        ec.resolve_launch_plan("dotfiles", worktree_id="x", new=True)


def test_resolve_base_and_new_are_exclusive(monkeypatch):
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(cmd))
    with pytest.raises(ec.EngineError):
        ec.resolve_launch_plan("dotfiles", base=True, new=True)


def test_resolve_none_action(monkeypatch):
    payload = {"action": "none", "exit_code": 0}
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(cmd, stdout=json.dumps(payload)))
    plan = ec.resolve_launch_plan("dotfiles", worktree_id="x")
    assert plan.action == "none" and not plan.is_exec and plan.exit_code == 0


def test_resolve_unwraps_nested_launch(monkeypatch):
    nested = {"worktree": {"id": "x"}, "launch": _RESUME_PLAN}
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(cmd, stdout=json.dumps(nested)))
    plan = ec.resolve_launch_plan("dotfiles", worktree_id="m-win-1200-ab12")
    assert plan.cmd == ["copilot", "--resume=sess123"]


def test_resolve_bare_resume_skew_retries_without_flag(monkeypatch):
    calls = []

    def handler(cmd, kw):
        calls.append(list(cmd))
        if "--bare-resume" in cmd:
            return _fake_completed(cmd, returncode=2,
                                   stderr="unrecognized arguments: --bare-resume")
        return _fake_completed(cmd, stdout=json.dumps(_RESUME_PLAN))

    _install_fake(monkeypatch, handler)
    plan = ec.resolve_launch_plan("dotfiles", worktree_id="x", bare_resume=True)
    assert plan.is_exec
    assert any("--bare-resume" in c for c in calls)
    assert any("--bare-resume" not in c for c in calls)


def test_resolve_error_envelope_surfaced(monkeypatch):
    payload = json.dumps({"version": 1, "error": "no such worktree"})
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(cmd, returncode=1, stdout=payload))
    with pytest.raises(ec.EngineError) as ei:
        ec.resolve_launch_plan("dotfiles", worktree_id="nope")
    assert "no such worktree" in str(ei.value)
