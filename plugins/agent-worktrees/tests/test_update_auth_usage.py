"""Fallback availability is not evidence that an acquisition child received it."""

from __future__ import annotations

import json
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import update_telemetry as ut

from agent_worktrees import (
    git_ops,
    installer_run,
    reconcile,
    update_acquisition,
    update_auth as auth,
    update_auth_context as context,
    update_auth_sources as sources,
    update_events,
)

pytestmark = pytest.mark.guard


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    project = tmp_path / "project"
    anchor = tmp_path / "anchor"
    project.mkdir()
    anchor.mkdir()
    monkeypatch.setenv("CE_UPDATE_AUTH", "on")
    monkeypatch.setenv(ut.ENV_HOME, str(tmp_path / "telemetry"))
    for variable in (ut.ENV_RUN_ID, ut.ENV_EVENTS, ut.ENV_OWNER):
        monkeypatch.delenv(variable, raising=False)
    core = auth.source(
        "marketplace:copilot-extensions", "https://example.test/team/core", cwd=project,
    )
    repo = auth.source(
        "anchor:fixture", "https://example.test/team/anchor", purpose="anchor", cwd=anchor,
    )
    monkeypatch.setattr(sources, "marketplaces", lambda cwd: [core])
    monkeypatch.setattr(sources, "anchors", lambda: [repo])
    monkeypatch.setattr(
        auth, "report",
        lambda items: auth.AuthReport([
            auth.SourceAuth(
                item, [], status="fixable_by_fallback", expected_account="fixture",
                alternative={"method": "gh"},
            )
            for item in items
        ]),
    )
    update_events.start()
    try:
        with context.session() as session:
            session.prepare(project, include_anchors=True)
            yield project, anchor
    finally:
        update_events.finish(0)


def _auth_status():
    run = update_events._run
    events, errors = ut.read_events(run.events_path)
    assert errors == 0
    return ut.fold(events, run={"id": run.run_id, "owner": run.owner})["auth"]


def test_preflight_retains_availability_without_claiming_use(prepared):
    project, _ = prepared
    assert context.child_environment(project)["env"]["CE_UPDATE_AUTH_ROUTES"]
    status = _auth_status()
    assert len(status["report"]) == 2
    assert status["fallback_used"] == []


@pytest.mark.parametrize("outcome", ["completed", "timeout", "missing"])
def test_marketplace_usage_requires_a_started_acquisition(prepared, outcome):
    project, _ = prepared

    def runner(argv, **kwargs):
        assert json.loads(kwargs["env"]["CE_UPDATE_AUTH_ROUTES"])
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(argv, 1)
        if outcome == "missing":
            raise FileNotFoundError("missing acquisition executable")
        return subprocess.CompletedProcess(argv, 0, "", "")

    if outcome == "completed":
        context.run_acquisition(
            runner, ["fixture"], context=project, **context.child_environment(project),
        )
    else:
        with pytest.raises(subprocess.TimeoutExpired if outcome == "timeout" else FileNotFoundError):
            context.run_acquisition(
                runner, ["fixture"], context=project, **context.child_environment(project),
            )
    status = _auth_status()
    assert status["fallback_used"] == (
        [] if outcome == "missing" else ["marketplace:copilot-extensions"]
    )
    assert len(status["report"]) == 2


def test_anchor_inspection_does_not_claim_fetch_fallback_use(prepared, monkeypatch):
    _, anchor = prepared
    monkeypatch.setattr(
        git_ops.subprocess, "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "", ""),
    )
    with context.repository_scope(anchor):
        git_ops.git("rev-parse", "--git-dir", cwd=anchor)
        assert _auth_status()["fallback_used"] == []
        git_ops.fetch("origin", cwd=anchor, timeout=None)
    assert _auth_status()["fallback_used"] == ["anchor:fixture"]


@pytest.mark.parametrize("failure", ["capture", "restore"])
def test_activation_state_errors_preserve_actual_usage_evidence(prepared, monkeypatch, failure):
    from plugin_activation import state

    project, _ = prepared
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: None)

    def capture(*args):
        if failure == "capture":
            raise state.PluginStateError("capture refused")
        return SimpleNamespace()

    def restore(*args):
        raise state.PluginStateError("restore refused")

    def runner(argv, **kwargs):
        assert kwargs["env"]["CE_UPDATE_AUTH_ROUTES"]
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(state, "capture", capture)
    monkeypatch.setattr(state, "restore", restore)
    monkeypatch.setattr(subprocess, "run", runner)
    assert update_acquisition.acquire(
        "fixture", "copilot-extensions", "copilot-fixture", cwd=project,
    ).startswith("plugin state error:")
    assert _auth_status()["fallback_used"] == (
        [] if failure == "capture" else ["marketplace:copilot-extensions"]
    )


@pytest.mark.parametrize("outcome", ["completed", "timeout", "cleanup", "launch", "missing"])
def test_windows_payload_owner_preserves_scoped_auth_and_usage(prepared, monkeypatch, outcome):
    import os

    project, _ = prepared
    monkeypatch.setattr(update_acquisition, "_WINDOWS", True)
    monkeypatch.setattr(update_acquisition, "sleep", lambda seconds: None)
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: project)
    original_environment, calls = dict(os.environ), []

    def run(argv, **kwargs):
        calls.append(argv)
        assert json.loads(kwargs["env"]["CE_UPDATE_AUTH_ROUTES"])
        assert kwargs["env"] is not os.environ
        assert kwargs["require_tree_exit"] is True
        if outcome == "timeout" and len(calls) == 1:
            return installer_run.InstallerRun("timed out (deadline hit during build)", None,
                                              None, False, 0)
        if outcome == "cleanup":
            raise installer_run.InstallerCleanupError("owned Job cleanup refused")
        if outcome == "launch":
            raise installer_run.InstallerLaunchError("owned Job assignment refused before resume")
        if outcome == "missing":
            raise FileNotFoundError("missing acquisition executable")
        return installer_run.InstallerRun("OK", 0, None, False, 0)

    monkeypatch.setattr(installer_run, "run_installer", run)
    status = update_acquisition.acquire(
        "fixture", "copilot-extensions", "fixture", cwd=project, engine=True,
    )
    assert status.startswith("OK") is (outcome in ("completed", "timeout"))
    assert len(calls) == (2 if outcome == "timeout" else 1)
    assert _auth_status()["fallback_used"] == (
        [] if outcome in ("launch", "missing") else ["marketplace:copilot-extensions"]
    )
    assert dict(os.environ) == original_environment


@pytest.mark.parametrize("outcome", ["completed", "timeout"])
def test_shipped_activation_source_accepts_acquisition_runner(tmp_path, monkeypatch, outcome):
    source = (
        Path(__file__).resolve().parents[3]
        / "plugins/customizing-copilot/libs/plugin-activation/src/plugin_activation/state.py"
    )
    spec = importlib.util.spec_from_file_location("shipped_activation_state", source)
    state = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, state)
    spec.loader.exec_module(state)
    home = tmp_path / "copilot"
    home.mkdir()
    settings = home / "settings.json"
    settings.write_text('{"enabledPlugins":{"fixture@market":false}}', encoding="utf-8")
    before = settings.read_bytes()
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        settings.write_text('{"enabledPlugins":{"fixture@market":true}}', encoding="utf-8")
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(argv, 1)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(
        subprocess, "run", lambda *_a, **_k: pytest.fail("injected runner was bypassed"),
    )
    kwargs = {"home": home, "runner": runner, "env": {"FIXTURE": "acquisition"}, "timeout": 1}
    if outcome == "timeout":
        with pytest.raises(subprocess.TimeoutExpired):
            state.run_install_preserving_activation(["fixture"], "fixture@market", **kwargs)
    else:
        result = state.run_install_preserving_activation(
            ["fixture"], "fixture@market", **kwargs,
        )
        assert result.returncode == 0
    assert calls == [(["fixture"], {"env": {"FIXTURE": "acquisition"}, "timeout": 1})]
    assert json.loads(settings.read_bytes()) == json.loads(before)
