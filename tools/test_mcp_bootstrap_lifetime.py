"""Production-lifetime admission must never launder ordinary test containment."""

import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

import mcp_bootstrap_lifetime as lane


@pytest.mark.parametrize("change", [
    {lane.CONTAINMENT: "1"}, {"GITHUB_ACTIONS": "false"},
    {"RUNNER_ENVIRONMENT": "self-hosted"}, {"RUNNER_TEMP": ""},
])
def test_native_lane_rejects_nonadmitted_environment(tmp_path, change):
    environment = {
        "GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_TEMP": str(tmp_path), **change,
    }
    with pytest.raises(RuntimeError, match="uncontained ephemeral hosted"):
        lane.admit(True, environment)


def test_consumer_map_refuses_containment_before_sanitization(tmp_path):
    with pytest.raises(RuntimeError, match="uncontained ephemeral hosted"):
        lane.consumer_environment({
            "GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted",
            "RUNNER_TEMP": str(tmp_path), lane.CONTAINMENT: "1",
        }, {})


def test_consumer_map_keeps_only_fixture_inputs_and_its_lease(tmp_path):
    result = lane.consumer_environment({
        "GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_TEMP": str(tmp_path), "HOME": str(tmp_path / "profile"),
        "PATH": "fixture-tools", "GITHUB_TOKEN": "fixture-never-forwarded",
        "AGENT_MCP_PINNED_CONTEXT": "foreign", "UV_CACHE_DIR": str(tmp_path / "cache"),
    }, {"COPILOT_EXTENSIONS_DAEMON_TEST_CONTEXT": "fixture-lease"})
    assert "GITHUB_TOKEN" not in result and "AGENT_MCP_PINNED_CONTEXT" not in result
    assert result["COPILOT_EXTENSIONS_DAEMON_TEST_CONTEXT"] == "fixture-lease"
    assert result["HOME"] == str(tmp_path / "profile") and result["PATH"] == "fixture-tools"
    assert result["UV_CACHE_DIR"] == str(tmp_path / "cache")


def test_cli_without_explicit_consent_does_not_create_fixture(tmp_path):
    result = subprocess.run(
        [sys.executable, str(Path(lane.__file__))],
        env={**os.environ, "RUNNER_TEMP": str(tmp_path)},
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode != 0 and "ephemeral hosted CI consent" in result.stderr
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("inherited", [(), ("Local\\ce-owned-job-" + "a" * 32,)])
def test_native_caller_scopes_preserve_runner_and_both_fixture_jobs(monkeypatch, inherited):
    lane.source_imports()
    from agent_procutil import _job_scopes
    from windows_normal_process_lifetime import caller_scope_names

    monkeypatch.delenv(_job_scopes.ENVIRONMENT_KEY, raising=False)
    if inherited:
        monkeypatch.setenv(_job_scopes.ENVIRONMENT_KEY, ";".join(inherited))
    outer, inner = ("Local\\ce-owned-job-" + value * 32 for value in ("b", "c"))
    scopes = (*inherited, outer, inner)
    assert caller_scope_names({}, SimpleNamespace(name=outer), ";".join(scopes)) == scopes
    for incomplete in (scopes[:-1], (*inherited, inner), (outer, inner) if inherited else (inner, outer)):
        with pytest.raises(RuntimeError, match="exact inherited and two caller Jobs"):
            caller_scope_names({}, SimpleNamespace(name=outer), ";".join(incomplete))
