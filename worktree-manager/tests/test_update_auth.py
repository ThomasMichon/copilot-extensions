"""Manager auth uses the engine seam, never a second credential implementation."""

from __future__ import annotations

import os

import pytest

from worktree_manager import engine_client as ec, self_install, update_auth

pytestmark = pytest.mark.guard


def test_manager_preserves_working_environment_and_failure_only_overlay(monkeypatch):
    monkeypatch.delenv("CE_UPDATE_AUTH", raising=False)
    calls = []
    result = {
        "results": [{"status": "anonymous", "required": True}],
        "failing": [],
        "environment": {},
    }

    def report(project, args, **kwargs):
        calls.append((project, args, kwargs))
        return result

    monkeypatch.setattr(ec, "run_json", report)
    assert update_auth.environment("https://github.com/example/tools") is None
    assert "--auth-report" in calls[0][1] and "--auth-environment" in calls[0][1]
    result["environment"] = {
        "CE_UPDATE_AUTH_ROUTES": '{"github.com/example":{"method":"gh","account":"mapped"}}',
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "credential.interactive",
        "GIT_CONFIG_VALUE_0": "never",
    }
    result["results"][0]["status"] = "fixable_by_fallback"
    result["failing"] = ["manager"]
    env = update_auth.environment("https://github.com/example/tools")
    assert env["GIT_CONFIG_COUNT"] == "1"
    assert env["CE_UPDATE_AUTH_ROUTES"] == result["environment"]["CE_UPDATE_AUTH_ROUTES"]
    assert env["PYTHONSAFEPATH"] == "1"
    assert all(key not in env for key in ec._PYTHON_PARENT_ENV)
    assert (
        os.environ.get("CE_UPDATE_AUTH_ROUTES") != result["environment"]["CE_UPDATE_AUTH_ROUTES"]
    )


@pytest.mark.parametrize("mode", ["on", "report-only"])
def test_manager_refuses_known_failed_source_before_fetch(monkeypatch, mode):
    monkeypatch.setenv("CE_UPDATE_AUTH", mode)
    monkeypatch.setattr(
        ec,
        "run_json",
        lambda *a, **k: {
            "results": [{"required": True, "status": "auth_required", "fix": "sign in as mapped"}],
            "failing": ["manager"],
            "environment": {"CE_UPDATE_AUTH_ROUTES": "{}"},
        },
    )
    with pytest.raises(ec.EngineError, match="auth_required.*mapped"):
        update_auth.environment("https://github.com/example/tools")


def test_manager_off_and_non_https_sources_do_not_require_engine(monkeypatch):
    monkeypatch.setattr(ec, "run_json", lambda *a, **k: pytest.fail("unexpected engine"))
    monkeypatch.setenv("CE_UPDATE_AUTH", "off")
    assert update_auth.environment("https://github.com/example/tools") is None
    monkeypatch.delenv("CE_UPDATE_AUTH")
    assert update_auth.environment("local-source") is None


def test_self_update_passes_the_overlay_only_to_its_children(tmp_path, monkeypatch):
    calls = []
    env = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "fixture",
    }
    monkeypatch.setattr(update_auth, "environment", lambda _, **kwargs: env)
    monkeypatch.setattr(self_install.shutil, "which", lambda _: "git")
    monkeypatch.setattr(
        self_install.subprocess, "run", lambda argv, **kwargs: calls.append((argv, kwargs))
    )
    monkeypatch.setattr(self_install, "manager_repo", lambda _: "https://github.com/example/tools")
    monkeypatch.setattr(self_install, "manager_ref", lambda _: "main")
    result = self_install.self_update(root=tmp_path)
    assert result.action == "error" and "fetched payload not found" in result.reason
    assert calls and calls[0][1]["env"] is env


def test_engine_environment_retains_auth_and_discards_parent_python(monkeypatch):
    monkeypatch.setenv("CE_UPDATE_AUTH_ROUTES", '{"example":{"method":"deny"}}')
    monkeypatch.setenv("GIT_CONFIG_COUNT", "0")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "caller-global")
    monkeypatch.setenv("PYTHONPATH", "parent-package")
    env = ec._engine_environment()
    assert env["CE_UPDATE_AUTH_ROUTES"] == '{"example":{"method":"deny"}}'
    assert env["GIT_CONFIG_COUNT"] == "0"
    assert env["GIT_CONFIG_GLOBAL"] == "caller-global"
    assert "PYTHONPATH" not in env


def test_self_update_auth_refusal_never_fetches_or_becomes_advisory(tmp_path, monkeypatch):
    monkeypatch.setattr(self_install.shutil, "which", lambda _: "git")

    def refused(_, **kwargs):
        raise update_auth.AuthRequired("sign in as mapped")

    monkeypatch.setattr(update_auth, "environment", refused)
    monkeypatch.setattr(
        self_install.subprocess,
        "run",
        lambda *a, **k: pytest.fail("known-failing source reached Git"),
    )
    result = self_install.self_update(root=tmp_path)
    assert result.action == "auth_required"
    assert result.reason == "sign in as mapped"


def test_manager_source_report_uses_existing_staging_checkout(tmp_path, monkeypatch):
    staging = tmp_path / "staging"
    (staging / ".git").mkdir(parents=True)
    seen = []
    monkeypatch.setattr(self_install.shutil, "which", lambda _: "git")

    def environment(source, *, cwd=None):
        seen.append(cwd)
        raise update_auth.AuthRequired("stop before acquisition")

    monkeypatch.setattr(update_auth, "environment", environment)
    result = self_install.self_update(root=tmp_path)
    assert result.action == "auth_required"
    assert seen == [staging]


def test_manager_environment_transports_cwd_over_engine_seam(tmp_path, monkeypatch):
    calls = []
    monkeypatch.delenv("CE_UPDATE_AUTH", raising=False)

    def report(project, args, **kwargs):
        calls.append(args)
        return {"results": [], "failing": [], "environment": {}}

    monkeypatch.setattr(ec, "run_json", report)
    assert update_auth.environment("https://github.com/team/repo", cwd=tmp_path) is None
    assert calls[0][calls[0].index("--auth-cwd") + 1] == str(tmp_path)
