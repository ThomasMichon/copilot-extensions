"""Attributable subprocess failure diagnostics for local guidance."""

import subprocess
import json

import pytest

from agent_worktrees import local_cache_refresh as lcr, push_timeout


@pytest.mark.parametrize("returncode", [0, 1])
def test_resolver_fault_stderr_is_failed_not_unavailable(tmp_path, monkeypatch, returncode):
    monkeypatch.setattr(
        push_timeout, "run_bounded",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            [], returncode, stdout="", stderr="resolver identity fault",
        ),
    )
    outcomes = []
    assert lcr._resolve_cli_script(tmp_path, timeout=1, outcomes=outcomes) is None
    assert outcomes[-1].status == "failed"
    assert "resolver identity fault" in outcomes[-1].detail


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
