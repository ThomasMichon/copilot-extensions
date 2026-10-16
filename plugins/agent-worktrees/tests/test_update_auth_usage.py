"""Fallback availability is not evidence that an acquisition child received it."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest
import update_telemetry as ut

from agent_worktrees import (
    git_ops,
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
