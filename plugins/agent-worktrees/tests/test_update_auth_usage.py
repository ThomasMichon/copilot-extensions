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


@pytest.mark.parametrize("mode", ["on", "off", "report-only"])
def test_plugin_management_auth_stays_child_only_and_respects_mode(tmp_path, monkeypatch, mode):
    from plugin_resolve import plugin_auth

    monkeypatch.setenv("CE_UPDATE_AUTH", mode)
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "inference-value")
    definition = {"source": {"source": "github", "repo": "example/catalog"}}
    monkeypatch.setattr(
        sources, "settings", lambda cwd: SimpleNamespace(marketplaces={"catalog": definition}),
    )
    calls = []

    def environment(selected, original, cwd, **kwargs):
        calls.append((selected, original, cwd, kwargs))
        assert original == {}
        return {"COPILOT_GITHUB_TOKEN": "repository-value"}

    monkeypatch.setattr(plugin_auth, "plugin_management_environment", environment)
    ticks = iter([1.0, 3.0])
    monkeypatch.setattr(context.time, "monotonic", lambda: next(ticks))

    def runner(argv, **kwargs):
        assert kwargs["env"] == (
            {"COPILOT_GITHUB_TOKEN": "repository-value"} if mode == "on" else {}
        )
        assert kwargs["timeout"] == (3 if mode == "on" else 5)
        return subprocess.CompletedProcess(argv, 0, "", "")

    context.run_acquisition(
        runner, ["copilot", "plugin", "update", "sample@catalog"],
        context=tmp_path, marketplace="catalog", env={}, timeout=5,
    )
    assert len(calls) == (1 if mode == "on" else 0)
    assert context.os.environ["COPILOT_GITHUB_TOKEN"] == "inference-value"


def test_plugin_management_auth_deadline_does_not_claim_a_launch(prepared, monkeypatch):
    from plugin_resolve import plugin_auth

    project, _ = prepared
    monkeypatch.setattr(sources, "settings", lambda cwd: SimpleNamespace(
        marketplaces={"copilot-extensions": {
            "source": {"source": "github", "repo": "example/catalog"},
        }},
    ))
    monkeypatch.setattr(
        plugin_auth, "plugin_management_environment", lambda *args, **kwargs: {},
    )
    ticks = iter([1.0, 7.0])
    monkeypatch.setattr(context.time, "monotonic", lambda: next(ticks))
    with pytest.raises(installer_run.InstallerLaunchError, match="before launch"):
        context.run_acquisition(
            lambda *args, **kwargs: pytest.fail("No child may launch after the deadline"),
            ["copilot"], context=project, marketplace="copilot-extensions", timeout=5,
        )
    assert _auth_status()["fallback_used"] == []


def test_anonymous_marketplace_does_not_prepare_plugin_credentials(prepared, monkeypatch):
    project, _ = prepared
    context._current.get().reports[project].results[0].status = "anonymous"
    monkeypatch.setattr(
        sources, "settings", lambda cwd: pytest.fail("Anonymous source needs no credential lookup"),
    )
    result = context.run_acquisition(
        lambda *args, **kwargs: "anonymous", ["copilot"],
        context=project, marketplace="copilot-extensions", env={},
    )
    assert result == "anonymous"


@pytest.mark.parametrize("outcome", ["completed", "timeout", "timeout-bytes"])
def test_plugin_management_diagnostics_redact_the_selected_opaque_token(
    tmp_path, monkeypatch, outcome,
):
    from plugin_resolve import plugin_auth

    monkeypatch.setenv("CE_UPDATE_AUTH", "on")
    monkeypatch.setattr(sources, "settings", lambda cwd: SimpleNamespace(
        marketplaces={"catalog": {"source": {"source": "github", "repo": "example/catalog"}}},
    ))
    token = "opaque-repository-value"
    monkeypatch.setattr(
        plugin_auth, "plugin_management_environment",
        lambda *args, **kwargs: {"COPILOT_GITHUB_TOKEN": token},
    )

    def runner(argv, **kwargs):
        if outcome.startswith("timeout"):
            diagnostic = token.encode() if outcome == "timeout-bytes" else token
            raise subprocess.TimeoutExpired(argv, 5, output=diagnostic, stderr=diagnostic)
        return subprocess.CompletedProcess(argv, 1, token, token)

    if outcome.startswith("timeout"):
        with pytest.raises(subprocess.TimeoutExpired) as caught:
            context.run_acquisition(runner, ["copilot"], marketplace="catalog", context=tmp_path)
        result = caught.value
    else:
        result = context.run_acquisition(
            runner, ["copilot"], marketplace="catalog", context=tmp_path,
        )
    expected = b"[redacted]" if outcome == "timeout-bytes" else "[redacted]"
    assert result.stdout == result.stderr == expected


@pytest.mark.parametrize("explicit_cwd", [False, True])
def test_standalone_management_resolves_repository_settings_before_credentials(
    tmp_path, monkeypatch, explicit_cwd,
):
    from plugin_resolve import plugin_auth

    project = tmp_path / "project"
    settings = project / ".github" / "copilot" / "settings.json"
    settings.parent.mkdir(parents=True)
    definition = {"source": {"source": "github", "repo": "example/repository-catalog"}}
    settings.write_text(json.dumps({"extraKnownMarketplaces": {"catalog": definition}}))
    monkeypatch.setenv("CE_UPDATE_AUTH", "on")
    if not explicit_cwd:
        monkeypatch.chdir(project)
    calls = []

    def environment(selected, original, cwd, **kwargs):
        calls.append((selected, cwd))
        return {"COPILOT_GITHUB_TOKEN": "repository-value"}

    monkeypatch.setattr(plugin_auth, "plugin_management_environment", environment)
    result = context.run_acquisition(
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, kwargs["env"], ""),
        ["copilot"], marketplace="catalog", **({"cwd": project} if explicit_cwd else {}),
    )
    assert calls == [(definition, project)]
    assert result.stdout["COPILOT_GITHUB_TOKEN"] == "repository-value"


@pytest.mark.parametrize("caller", ["refresh", "browse", "module", "stage"])
def test_best_effort_management_preserves_failure_contract_on_auth_refusal(
    monkeypatch, caller,
):
    import shutil
    from agent_worktrees import update_cli, update_runtime, update_stage

    calls = []

    def acquisition(*args, **kwargs):
        calls.append(kwargs)
        raise installer_run.InstallerLaunchError("credential owner refused")

    monkeypatch.setattr(context, "run_acquisition", acquisition)
    monkeypatch.setattr(context, "blocked", lambda *args: False)
    monkeypatch.setattr(update_cli, "_resolve_copilot", lambda: "copilot")
    monkeypatch.setattr(update_runtime, "_resolve_copilot", lambda: "copilot")
    monkeypatch.setattr(shutil, "which", lambda name: "copilot")
    if caller == "refresh":
        assert update_cli._refresh_marketplace("catalog") is False
    elif caller == "browse":
        assert update_cli._browse_marketplace_plugins("catalog") is None
    elif caller == "module":
        assert update_runtime._refresh_module_payload("sample") is None
    else:
        assert update_stage._run_copilot_update() == (
            False, "copilot plugin authentication preparation failed",
        )
    assert len(calls) == 1


@pytest.mark.parametrize("caller", ["standalone-module", "staged-self"])
def test_standalone_payload_calls_use_plugin_management_boundary(monkeypatch, caller):
    import shutil
    from agent_worktrees import update_runtime, update_stage

    calls = []

    def acquisition(runner, argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "updated", "")

    monkeypatch.setattr(context, "run_acquisition", acquisition)
    if caller == "standalone-module":
        monkeypatch.setattr(update_runtime, "_resolve_copilot", lambda: "copilot")
        update_runtime._refresh_module_payload("sample")
    else:
        monkeypatch.setattr(shutil, "which", lambda name: "copilot")
        assert update_stage._run_copilot_update() == (True, "updated")
    assert len(calls) == 1
    assert calls[0][1]["marketplace"] == "copilot-extensions"
    assert calls[0][0][1:3] == ["plugin", "update"]


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
@pytest.mark.parametrize("windows", [False, True])
def test_activation_state_errors_preserve_actual_usage_evidence(
    prepared, monkeypatch, failure, windows,
):
    from plugin_activation import state

    project, _ = prepared
    monkeypatch.setattr(update_acquisition, "_WINDOWS", windows)
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: None)

    def capture(*args):
        if failure == "capture":
            raise state.PluginStateError("capture refused")
        return state.ActivationSnapshot(args[0], False, False, False, None, None)

    def restore(*args):
        raise state.PluginStateError("restore refused")

    def runner(argv, **kwargs):
        assert kwargs["env"]["CE_UPDATE_AUTH_ROUTES"]
        if kwargs.get("on_settled") is not None:
            kwargs["on_settled"]()
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(state, "capture", capture)
    monkeypatch.setattr(state, "restore", restore)
    monkeypatch.setattr(subprocess, "run", runner)
    monkeypatch.setattr(update_acquisition, "_run_update", runner)
    assert update_acquisition.acquire(
        "fixture", "copilot-extensions", "copilot-fixture", cwd=project,
    ).startswith("plugin state error:")
    assert _auth_status()["fallback_used"] == (
        [] if failure == "capture" else ["marketplace:copilot-extensions"]
    )


@pytest.mark.parametrize("outcome", ["completed", "timeout", "cleanup", "activity", "launch", "missing"])
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
        if outcome == "activity":
            raise installer_run.InstallerActivityError("accounting failed after verified cleanup")
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
