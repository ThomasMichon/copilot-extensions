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


def test_fresh_clone_preflight_and_acquisition_share_non_repository_context(tmp_path, monkeypatch):
    seen, git_calls = [], []
    monkeypatch.setattr(self_install.shutil, "which", lambda _: "git")

    def environment(source, *, cwd=None):
        seen.append(cwd)
        assert cwd is not None and cwd.is_dir()
        return {"GIT_CEILING_DIRECTORIES": str(cwd)}

    monkeypatch.setattr(update_auth, "environment", environment)
    monkeypatch.setattr(
        self_install.subprocess, "run", lambda argv, **kwargs: git_calls.append((argv, kwargs))
    )
    self_install.self_update(root=tmp_path)
    assert seen == [tmp_path / "staging"]
    assert git_calls[0][1]["cwd"] == tmp_path / "staging"


def test_first_clone_context_removes_inherited_repository_selectors(tmp_path, monkeypatch):
    monkeypatch.delenv("CE_UPDATE_AUTH", raising=False)
    monkeypatch.setenv("GIT_DIR", "caller-git-dir")
    seen = []

    def report(project, args, **kwargs):
        seen.extend(args)
        return {
            "results": [],
            "failing": [],
            "environment": {"GIT_CEILING_DIRECTORIES": str(tmp_path)},
            "unset_environment": ["GIT_DIR"],
        }

    monkeypatch.setattr(ec, "run_json", report)
    env = update_auth.environment("https://github.com/team/repo", cwd=tmp_path)
    assert "--auth-non-repository" in seen
    assert "GIT_DIR" not in env
    assert env["GIT_CEILING_DIRECTORIES"] == str(tmp_path)


def test_nested_https_sources_keep_existing_git_authentication(monkeypatch, capsys):
    monkeypatch.delenv("CE_UPDATE_AUTH", raising=False)
    monkeypatch.setattr(
        ec,
        "run_json",
        lambda *a, **k: pytest.fail("unsupported source must not be refused by the engine"),
    )
    assert update_auth.environment("https://git.example.test/team/subgroup/tools.git") is None
    assert "existing Git authentication" in capsys.readouterr().err


@pytest.mark.parametrize("relative", ["neither", "root", "source", "both", "environment"])
def test_real_first_clone_runs_from_its_empty_staging_directory(tmp_path, monkeypatch, relative):
    import subprocess

    seed = tmp_path / "source"
    root = tmp_path / "manager"
    real_run = subprocess.run
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}

    def git(*args):
        real_run(["git", *args], check=True, capture_output=True, **flags)

    git("init", "-q", "-b", "main", str(seed))
    git("-C", str(seed), "config", "user.name", "Test")
    git("-C", str(seed), "config", "user.email", "test@example.invalid")
    (seed / "worktree-manager").mkdir()
    (seed / "worktree-manager" / "pyproject.toml").write_text("[project]\nname='fixture'\n")
    git("-C", str(seed), "add", "worktree-manager")
    git("-C", str(seed), "commit", "-qm", "fixture")
    monkeypatch.chdir(tmp_path)
    source = "source" if relative in ("source", "both", "environment") else str(seed)
    install_root = root.relative_to(tmp_path) if relative in ("root", "both") else root
    if relative == "environment":
        monkeypatch.setenv("WORKTREE_MANAGER_ROOT", "manager")
        install_root = None
    monkeypatch.setattr(self_install, "manager_repo", lambda _: source)
    monkeypatch.setattr(self_install, "manager_ref", lambda _: "main")
    monkeypatch.setattr(
        self_install,
        "self_install",
        lambda **kwargs: self_install.SelfInstallResult(
            action="already-current", version=None, root=str(root)
        ),
    )
    monkeypatch.setattr(
        self_install.subprocess, "run", lambda argv, **kwargs: real_run(argv, **kwargs, **flags)
    )
    result = self_install.self_update(root=install_root)
    assert result.action == "already-current"
    assert (root / "staging" / "worktree-manager" / "pyproject.toml").is_file()


@pytest.mark.parametrize("reason", ["missing", "unsupported", "legacy-project-guard"])
def test_optional_auth_report_does_not_prevent_independent_manager_updates(
    monkeypatch, capsys, reason
):
    def unavailable(*args, **kwargs):
        raise ec.EngineError(
            {
                "missing": "engine absent",
                "unsupported": "unrecognized arguments: --auth-report --auth-environment",
                "legacy-project-guard": "Could not resolve a project for 'update'",
            }[reason],
            install_hint=reason == "missing",
        )

    monkeypatch.delenv("CE_UPDATE_AUTH", raising=False)
    monkeypatch.setattr(ec, "run_json", unavailable)
    assert update_auth.environment("https://github.com/team/repo") is None
    assert "existing Git authentication" in capsys.readouterr().err


def test_unsupported_report_shape_preserves_provider_authentication(monkeypatch, capsys):
    monkeypatch.delenv("CE_UPDATE_AUTH", raising=False)
    monkeypatch.setattr(
        ec,
        "run_json",
        lambda *a, **k: {
            "supported": False,
            "reason": "unsupported_https_source",
            "environment": {},
            "results": [],
            "all_ok": False,
        },
    )
    assert update_auth.environment("https://git.example.test/team/repo%20name") is None
    assert "existing Git authentication" in capsys.readouterr().err


@pytest.mark.parametrize(
    "source",
    [
        "https://example.test/team/repo.git",
        "ssh://git@example.test/team/repo.git",
        "git@example.test:team/repo.git",
        "example.test:team/repo.git",
        "file:///tmp/source.git",
        "helper::source",
    ],
)
def test_self_update_keeps_remote_source_semantics(tmp_path, monkeypatch, source):
    calls = []
    monkeypatch.setattr(self_install, "manager_repo", lambda _: source)
    monkeypatch.setattr(self_install, "manager_ref", lambda _: "main")
    monkeypatch.setattr(update_auth, "environment", lambda *a, **k: None)
    monkeypatch.setattr(self_install.shutil, "which", lambda _: "git")
    monkeypatch.setattr(
        self_install.subprocess, "run", lambda argv, **kwargs: calls.append(argv)
    )
    self_install.self_update(root=tmp_path)
    assert calls[0][0:2] == ["git", "clone"]
    assert calls[0][-2] == source
