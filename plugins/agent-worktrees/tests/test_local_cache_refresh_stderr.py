"""Attributable subprocess failure diagnostics for local guidance."""

import subprocess
import json

import pytest

from agent_worktrees import local_cache_refresh as lcr, projection_runtime_assets, push_timeout


@pytest.mark.parametrize("error_type", [OSError, ValueError])
def test_packaged_resolver_fault_is_failed_not_unavailable(tmp_path, monkeypatch, error_type):
    def fail_resolution():
        raise error_type("packaged engine identity fault")

    monkeypatch.setattr(
        projection_runtime_assets, "projection_cli_script", fail_resolution,
    )
    outcomes = []
    assert lcr._resolve_cli_script(tmp_path, timeout=1, outcomes=outcomes) is None
    assert outcomes[-1].status == "failed"
    assert "packaged projection engine unavailable" in outcomes[-1].detail
    assert "packaged engine identity fault" in outcomes[-1].detail


def test_packaged_resolver_returns_own_engine_without_subprocess(tmp_path, monkeypatch):
    expected = tmp_path / "manage-instruction-projections.py"
    monkeypatch.setattr(projection_runtime_assets, "projection_cli_script", lambda: expected)

    def reject_subprocess(*args, **kwargs):
        pytest.fail("packaged engine resolution must not invoke an external resolver")

    monkeypatch.setattr(push_timeout, "run_bounded", reject_subprocess)
    outcomes = []
    assert lcr._resolve_cli_script(tmp_path, timeout=1, outcomes=outcomes) == expected
    assert outcomes == []


@pytest.mark.parametrize("valid_json", [False, True])
def test_renderer_failure_retains_stderr(tmp_path, monkeypatch, valid_json):
    monkeypatch.setattr(lcr, "_resolve_cli_script", lambda *args, **kwargs: tmp_path)
    monkeypatch.setattr(lcr, "_resolve_own_agent_worktrees_command", lambda: None)
    monkeypatch.setattr(
        push_timeout, "run_bounded",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            [], 2, stdout=json.dumps({
                "operation": "render-local-cache", "changed": [], "written": [],
                "removed": [], "unchanged": [], "blocking": 0, "warnings": 0, "findings": [],
            }) if valid_json else "", stderr="renderer could not read declaration",
        ),
    )
    result = lcr.refresh_local_cache(tmp_path)
    assert result.status == "failed"
    assert "2" in result.detail
    assert "renderer could not read declaration" in result.detail
