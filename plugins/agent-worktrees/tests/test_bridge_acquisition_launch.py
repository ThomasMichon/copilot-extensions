"""Bridge launch has native authority and does not use filesystem resume fallback."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from agent_worktrees import bridge_acquisition_launch as launch, embody_resume

pytestmark = pytest.mark.contract("agent_worktrees.bridge_acquisition_launch")


def test_explicit_native_admission_flag_is_not_a_fresh_create_alias():
    parser = argparse.ArgumentParser()
    launch.add_arguments(parser, parser.add_mutually_exclusive_group())
    args = parser.parse_args([
        "--bridge-acquisition", "fresh", "--require-native-admission",
    ])
    assert "existing" in launch.refusal(args, SimpleNamespace(), "worktree-a")

def test_real_embody_parser_accepts_native_admission_modifier():
    from agent_worktrees.__main__ import build_parser

    args = build_parser().parse_args([
        "embody", "--worktree-id", "worktree-a", "--bridge-acquisition", "native-a",
        "--require-native-admission", "--dry-run", "--json",
    ])
    assert args.bridge_acquisition == "native-a"
    assert args.require_native_admission is True
    assert args.dry_run is True


@pytest.mark.parametrize("other", ["--fresh", "--resume-head"])
def test_real_embody_parser_still_rejects_conflicting_intents(other):
    from agent_worktrees.__main__ import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args([
            "embody", "--bridge-acquisition", "native-a", other,
        ])


@pytest.mark.parametrize("intent,expected", [("fresh", None), ("native-a", "native-a")])
def test_exact_native_resume_without_filesystem_fallback(monkeypatch, intent, expected):
    args = argparse.Namespace(bridge_acquisition=intent, seed=None, copilot_args=[])
    record = SimpleNamespace(resolved_head_session="native-a")
    monkeypatch.setattr(launch.sessions, "has_mux_session", lambda _wt: False)
    monkeypatch.setattr(launch, "session_liveness", lambda _sid: "dead")
    assert launch.refusal(args, record, "worktree-a") is None
    assert embody_resume.resume_target(args, record, None, make_new=False) == expected


@pytest.mark.parametrize("state", ["live", "unknown"])
def test_fresh_is_not_permission_to_ignore_a_live_or_unknown_native_head(monkeypatch, state):
    args = argparse.Namespace(bridge_acquisition="fresh", seed=None, copilot_args=[])
    monkeypatch.setattr(launch.sessions, "has_mux_session", lambda _wt: False)
    monkeypatch.setattr(launch, "session_liveness", lambda _sid: state)
    assert "stopped" in launch.refusal(args, SimpleNamespace(resolved_head_session="native-a"), "worktree-a")


def test_wrong_native_identity_and_live_mux_refuse(monkeypatch):
    args = argparse.Namespace(bridge_acquisition="native-b", seed=None, copilot_args=[])
    monkeypatch.setattr(launch.sessions, "has_mux_session", lambda _wt: False)
    assert "identity" in launch.refusal(args, SimpleNamespace(resolved_head_session="native-a"), "worktree-a")
    monkeypatch.setattr(launch.sessions, "has_mux_session", lambda _wt: True)
    assert "mux" in launch.refusal(args, SimpleNamespace(), "worktree-a")


@pytest.mark.parametrize("intent", ["native-a", "fresh"])
def test_finalizing_record_refuses_both_preflight_and_locked_admission(monkeypatch, intent):
    args = argparse.Namespace(bridge_acquisition=intent, seed=None, copilot_args=[])
    record = SimpleNamespace(resolved_head_session="native-a", status="active")
    monkeypatch.setattr(launch.sessions, "has_mux_session", lambda _wt: False)
    monkeypatch.setattr(launch, "session_liveness", lambda _sid: "dead")
    assert launch.refusal(args, record, "worktree-a") is None
    record.status = "finalizing"
    assert "finalizing" in launch.refusal(args, record, "worktree-a")


@pytest.mark.parametrize("cancel,path_override", [
    pytest.param(False, False, id="False"),
    pytest.param(True, False, id="True"),
    pytest.param(False, True, id="explicit-path"),
])
def test_preparation_uses_real_owner_environment_and_checks_cancel_before_launch(
    tmp_path, monkeypatch, cancel, path_override,
):
    from plugin_resolve import preparation
    from plugin_resolve.registration import atomic_json

    subprocess.run(
        ["git", "init", "--quiet", str(tmp_path)], check=True, timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    payload = tmp_path / ".ai" / "helper"
    payload.mkdir(parents=True)
    atomic_json(payload / "plugin.json", {"name": "helper", "version": "2.0.0"})
    atomic_json(payload.parent / "marketplace.json", {
        "name": "local", "owner": {"name": "Synthetic"},
        "plugins": [{"name": "helper", "source": "helper"}],
    })
    settings = tmp_path / ".github" / "copilot"
    settings.mkdir(parents=True)
    atomic_json(settings / "settings.json", {
        "enabledPlugins": {"helper@local": True},
        "extraKnownMarketplaces": {"local": {"source": {"source": "directory", "path": "./.ai"}}},
    })
    root = tmp_path / ".git" / "copilot-venue-plugins" / "launch-requests" / "operation"
    root.mkdir(parents=True)
    path = root / "request.json"
    atomic_json(path, {
        "version": 1, "cwd": str(tmp_path), "worktree_id": "worktree-a",
        "deadline": time.time() + 120, "plan": {"plugins": [], "marketplaces": {}},
    })
    args = argparse.Namespace(
        bridge_acquisition="fresh", bridge_plugin_request=str(path), require_plugin_preparation=True,
    )
    profile = str(tmp_path / "actual-profile")
    environment = {"COPILOT_HOME": profile, "COPILOT_PATH": sys.executable}
    if path_override:
        git = shutil.which("git")
        assert git is not None
        environment["PATH"] = str(Path(git).parent)
        monkeypatch.setenv("PATH", str(tmp_path / "absent-executables"))
    original = preparation.prepare
    original_git = preparation._git
    observed = []
    git_environments = []

    def prepare(*args, **kwargs):
        observed.append(kwargs["environment"]["COPILOT_HOME"])
        return original(*args, **kwargs)

    def probe(*args, **kwargs):
        git_environments.append(dict(kwargs["environment"]))
        return original_git(*args, **kwargs)

    monkeypatch.setattr(preparation, "prepare", prepare)
    monkeypatch.setattr(preparation, "_git", probe)
    before = dict(os.environ)
    context, check_cancelled = launch.prepare_plugins(args, "worktree-a", str(tmp_path), environment)
    assert observed == [profile] and dict(os.environ) == before
    assert all(
        env["PATH"] == environment.get("PATH", before["PATH"])
        and env["COPILOT_HOME"] == profile and env["COPILOT_PATH"] == sys.executable
        for env in git_environments[:2]
    )
    assert len(git_environments) >= 2
    assert context["plugins"] == [{
        "projected_source": "helper@local", "logical_source": "helper@local",
        "version": "2.0.0", "live_marketplace_root": str(tmp_path / ".ai"),
    }]
    assert json.loads(path.with_suffix(".prepared.json").read_text()) == context
    assert not (tmp_path / "actual-profile").exists()
    if cancel:
        path.with_suffix(".cancel").touch()
        with pytest.raises(InterruptedError):
            check_cancelled()
    else:
        check_cancelled()
