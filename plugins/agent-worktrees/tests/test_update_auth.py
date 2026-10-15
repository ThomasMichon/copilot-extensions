"""Read-only update auth, failure-only routing, and real Git credential replay."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_worktrees import _git_credential_route as router
from agent_worktrees import (
    config as cfg,
    git_ops,
    update_auth as auth,
    update_auth_context as context,
)
from agent_worktrees import update_auth_process as process
from agent_worktrees import update_auth_sources as sources
from agent_worktrees import update_events, update_runtime

pytestmark = pytest.mark.guard


def _source(owner="team", *, id="marketplace:example", required=True, cwd=None):
    return auth.source(id, f"https://example.test/{owner}/repo", required=required, cwd=cwd)


class Probe:
    def __init__(self, anonymous=False, configured=False, *, gh=True, mismatch=False, fill=False):
        self.anonymous, self.configured = anonymous, configured
        self.gh, self.mismatch, self.fill = gh, mismatch, fill
        self.calls = []
        self.secret = "test-credential-value"

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        env = kwargs["env"]
        if argv[:2] == ["git", "config"]:
            return process.Result(0, "one\0\0two\0")
        if argv[:2] == ["git", "ls-remote"]:
            count = int(env["GIT_CONFIG_COUNT"])
            helper = env[f"GIT_CONFIG_VALUE_{count - 1}"]
            ok = self.configured if helper else self.anonymous
            return process.Result(0 if ok else 1)
        if argv[:3] == ["gh", "auth", "token"]:
            return process.Result(0, self.secret) if self.gh else process.Result(1)
        if argv[:2] == ["gh", "api"]:
            if "user" in argv:
                return process.Result(0, "wrong" if self.mismatch else "mapped")
            return process.Result(0, "123")
        if argv == ["git", "credential", "fill"]:
            return (
                process.Result(0, router.SECRET_KEY + "=" + self.secret)
                if (self.fill)
                else process.Result(1)
            )
        raise AssertionError(argv)


def test_working_setup_untouched_and_public_short_circuits():
    for probe in (Probe(anonymous=True), Probe(configured=True)):
        environment = {"PATH": "fixture"}
        report = auth.report(
            [_source()], env=environment, runner=probe, account_for=lambda _: "mapped"
        )
        assert not report.failing
        assert auth.fallback(report) is None
        assert environment == {"PATH": "fixture"}
        assert not any(argv[0] == "gh" for argv, _ in probe.calls)
        assert report.results[0].helper_chain == ["two"]


def test_fallback_preserves_inherited_command_scope_and_contains_no_token():
    probe = Probe()
    report = auth.report([_source()], env={}, runner=probe, account_for=lambda _: "mapped")
    overlay = auth.fallback(report)
    env = overlay.environment(
        {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "user.name",
            "GIT_CONFIG_VALUE_0": "User",
        }
    )
    assert env["GIT_CONFIG_KEY_0"] == "user.name"
    assert int(env["GIT_CONFIG_COUNT"]) == 5
    assert probe.secret not in json.dumps(env)
    assert overlay.applies_to == ["marketplace:example"]
    assert overlay.denies == []


@pytest.mark.parametrize("report_only", [False, True])
def test_denied_sources_and_report_only_count_actual_applied_fixes(report_only):
    report = auth.report(
        [_source()], env={}, runner=Probe(gh=True), account_for=lambda _: "mapped"
    )
    overlay = auth.fallback(report, apply_fixes=not report_only)
    assert overlay.denies == (["marketplace:example"] if report_only else [])
    assert bool(overlay.applies_to) is not report_only
    routes = json.loads(overlay.variables["CE_UPDATE_AUTH_ROUTES"])
    assert routes["example.test/team/repo"]["method"] == ("deny" if report_only else "gh")


def test_env_gh_fill_order_and_mismatched_account():
    probe = Probe(mismatch=True, fill=True)
    report = auth.report(
        [_source()],
        env={"GH_TOKEN": "environment-fixture", "GH_HOST": "example.test"},
        runner=probe,
        account_for=lambda _: "mapped",
    )
    assert report.results[0].status == "mismatch"
    assert "sign in as mapped" in report.results[0].fix
    alternatives = [
        argv[:3]
        for argv, _ in probe.calls
        if argv[0] == "gh" or (argv[:2] == ["git", "credential"])
    ]
    assert alternatives[0] == ["gh", "api", "--hostname"]
    assert ["gh", "auth", "token"] in alternatives
    assert alternatives[-2] == ["git", "credential", "fill"]
    token_calls = [kwargs for argv, kwargs in probe.calls if argv[:3] == ["gh", "auth", "token"]]
    assert "GH_TOKEN" not in token_calls[0]["env"]
    assert report.summary()["results"][0].get("helper_chain") is None


def test_no_passing_method_denies_and_optional_failure_is_advisory():
    report = auth.report(
        [_source(), _source("optional", id="optional", required=False)],
        env={},
        runner=Probe(gh=False),
        account_for=lambda _: "mapped",
    )
    overlay = auth.fallback(report)
    assert overlay.denies == ["marketplace:example"]
    assert overlay.applies_to == []
    assert json.loads(overlay.variables["CE_UPDATE_AUTH_ROUTES"]) == {
        "example.test/team/repo": {"method": "deny"}
    }


def test_colliding_owner_accounts_fail_closed():
    report = auth.report(
        [_source(id="one"), _source(id="two")],
        env={},
        runner=Probe(),
        account_for=lambda _: "mapped",
    )
    report.results[1].expected_account = "other"
    overlay = auth.fallback(report)
    assert overlay.applies_to == []
    assert overlay.denies == ["one", "two"]


def test_no_incomplete_command_config_is_manufactured():
    with pytest.raises(ValueError, match="incomplete"):
        auth.command_scope({"GIT_CONFIG_COUNT": "1"}, [("credential.helper", "")])


def test_router_deny_and_readonly_store_never_execute(monkeypatch):
    monkeypatch.setattr(router, "run", lambda *a, **k: pytest.fail("unexpected child"))
    env = {
        "CE_UPDATE_AUTH_ROUTES": json.dumps({"example.test/team/repo": {"method": "deny"}}),
        "CE_UPDATE_AUTH_READONLY": "1",
    }
    for operation in ("get", "store", "erase"):
        assert router.route(operation, "host=example.test\npath=team/repo\n", env) == ""
    assert router.route("store", "host=another.test\npath=team/repo\n", env) == ""


def _clean_git_env(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name.startswith("GIT_CONFIG") or name.startswith("CE_UPDATE_AUTH"):
            monkeypatch.delenv(name, raising=False)
    config = tmp_path / "global.cfg"
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.setenv("GCM_INTERACTIVE", "never")
    return config


def test_real_git_router_replays_url_helpers_and_local_reset_without_writes(tmp_path, monkeypatch):
    config = _clean_git_env(monkeypatch, tmp_path)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q", str(checkout)], check=True, **git_ops.no_window_kwargs())
    helper = "!f() { printf '%s\\n' username=original " + router.SECRET_KEY + "=fixture; }; f"
    for key, value in [
        ("credential.helper", "!exit 71"),
        ("credential.https://example.test/working.helper", ""),
        ("credential.https://example.test/working.helper", helper),
    ]:
        subprocess.run(
            ["git", "-C", str(checkout), "config", "--add", key, value],
            check=True,
            **git_ops.no_window_kwargs(),
        )
    local = checkout / ".git" / "config"
    before = config.read_bytes(), local.read_bytes()
    failed = auth.SourceAuth(_source(cwd=checkout), [], status="auth_required")
    overlay = auth.fallback(auth.AuthReport([failed]))
    env = overlay.environment(os.environ)
    result = process.run(
        ["git", "credential", "fill"],
        env=env,
        cwd=checkout,
        input="protocol=https\nhost=example.test\npath=working/repo\n\n",
        timeout=10,
    )
    assert result.status == 0, result.stderr
    answer = router.parse_attributes(result.stdout)
    assert answer["username"] == "original"
    assert answer[router.SECRET_KEY] == "fixture"
    denied = process.run(
        ["git", "credential", "fill"],
        env=env,
        cwd=checkout,
        input="protocol=https\nhost=example.test\npath=team/repo\n\n",
        timeout=10,
    )
    assert denied.status != 0
    assert "fixture" not in denied.stdout
    assert before == (config.read_bytes(), local.read_bytes())


def test_real_git_router_does_not_run_poisoned_local_helpers_after_routing(tmp_path, monkeypatch):
    _clean_git_env(monkeypatch, tmp_path)
    item = auth.SourceAuth(
        _source(),
        [],
        expected_account="mapped",
        status="fixable_by_fallback",
        alternative={"method": "env"},
    )
    overlay = auth.fallback(auth.AuthReport([item]))
    environment = {**os.environ, "GH_TOKEN_TEAM": "env-fixture"}
    env = overlay.environment(environment)
    result = process.run(
        ["git", "-c", "credential.helper=!exit 72", "credential", "fill"],
        env=env,
        input="protocol=https\nhost=example.test\npath=team/repo\n\n",
        timeout=10,
    )
    # An explicit -c on this very invocation can override command-scope config;
    # local-file overrides are covered by the preceding real checkout fixture.
    assert result.status == 0, result.stderr
    assert router.parse_attributes(result.stdout)[router.SECRET_KEY] == "env-fixture"


def test_scoped_overlay_survives_repository_isolation_but_hostile_config_does_not(
    tmp_path,
    monkeypatch,
):
    _clean_git_env(monkeypatch, tmp_path)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.bare")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    overlay = auth.fallback(
        auth.AuthReport([auth.SourceAuth(_source(), [], status="auth_required")])
    )
    token = context._repository.set(overlay)
    try:
        env = context.repository_environment(git_ops.repository_identity_env())
    finally:
        context._repository.reset(token)
    assert "core.bare" not in env.values()
    assert env["GIT_CONFIG_COUNT"] == "4"
    assert "CE_UPDATE_AUTH_ROUTES" in env
    assert "GIT_CONFIG_COUNT" not in git_ops.repository_identity_env()


def test_settings_override_and_disabled_sources_are_derived(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / "repo"
    settings = root / ".github" / "copilot"
    settings.mkdir(parents=True)
    (settings / "settings.json").write_text(
        json.dumps(
            {
                "enabledPlugins": {"x@private": True, "x@optional": False},
                "extraKnownMarketplaces": {
                    "private": {"source": {"source": "github", "repo": "team/private"}},
                    "optional": {"source": {"source": "github", "repo": "other/optional"}},
                    "local": {"source": {"source": "directory", "path": "."}},
                },
            }
        )
    )
    result = sources.marketplaces(root)
    assert [(item.id, item.required) for item in result] == [
        ("marketplace:private", True),
        ("marketplace:optional", False),
    ]


def test_bounded_auth_child_caps_both_streams_and_reports_timeout():
    environment = dict(os.environ)
    large = process.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('x'*2000000); sys.stderr.write('y'*2000000)",
        ],
        env=environment,
        timeout=10,
    )
    assert large.status == 0
    assert large.truncated
    assert len(large.stdout) == len(large.stderr) == 1024 * 1024
    stalled = process.run(
        [sys.executable, "-c", "import time; time.sleep(60)"], env=environment, timeout=0.2
    )
    assert stalled.timed_out


def test_report_only_is_readonly_and_never_calls_update_steps(monkeypatch, capsys):
    monkeypatch.setattr(sources, "marketplaces", lambda _: [_source()])
    monkeypatch.setattr(
        auth,
        "report",
        lambda _: auth.AuthReport([auth.SourceAuth(_source(), [], status="auth_required")]),
    )
    args = argparse.Namespace(
        no_anchor_sync=True, auth_source=[], auth_environment=False, json=True
    )
    assert context.cmd_report(args) == 4
    assert json.loads(capsys.readouterr().out)["all_ok"] is False


def test_failing_repo_does_not_route_another_repo_of_the_same_owner(monkeypatch):
    def delegate(argv, **kwargs):
        return process.Result(0, "username=original\n" + router.SECRET_KEY + "=fixture\n")

    monkeypatch.setattr(router, "run", delegate)
    item = auth.SourceAuth(_source(), [], status="auth_required")
    env = auth.fallback(auth.AuthReport([item])).environment({})
    answer = router.route("get", "protocol=https\nhost=example.test\npath=team/other\n\n", env)
    assert router.parse_attributes(answer)["username"] == "original"


def test_real_git_ops_isolation_retains_only_the_scoped_auth_overlay(tmp_path, monkeypatch):
    _clean_git_env(monkeypatch, tmp_path)
    subprocess.run(
        ["git", "init", "-q", str(tmp_path / "checkout")], check=True, **git_ops.no_window_kwargs()
    )
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.bare")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    overlay = auth.fallback(
        auth.AuthReport([auth.SourceAuth(_source(), [], status="auth_required")])
    )
    token = context._repository.set(overlay)
    try:
        result = git_ops.git(
            "config",
            "--get",
            "credential.https://example.test.helper",
            cwd=tmp_path / "checkout",
            isolated_repository=True,
        )
    finally:
        context._repository.reset(token)
    assert result.stdout.strip() == auth.router_helper()
    assert "GIT_CONFIG_COUNT" not in git_ops.repository_identity_env()


def test_anchor_fetch_failure_is_auth_required(tmp_path, monkeypatch):
    anchor = tmp_path / "anchor"
    (anchor / ".git").mkdir(parents=True)
    repo = cfg.RepoConfig(
        anchor=str(anchor), default_branch="main", worktree_root=str(tmp_path / "worktrees")
    )
    config = cfg.Config(
        srcroot=str(tmp_path),
        machine="test",
        platform="windows",
        repo_name="example",
        repos={"example": repo},
    )
    monkeypatch.setattr(cfg, "load_config", lambda: config)
    monkeypatch.setattr(update_runtime.bare_anchor_sync, "is_bare", lambda _: False)
    monkeypatch.setattr(git_ops, "current_branch", lambda _: "main")
    monkeypatch.setattr(
        git_ops,
        "fast_forward_worktree",
        lambda *a, **k: git_ops.FastForwardResult(
            False, "up-to-date", fetch_error="Authentication failed"
        ),
    )
    recorded = []
    monkeypatch.setattr(update_events, "record", lambda *a, **k: recorded.append(a))
    update_runtime._fast_forward_project_anchors()
    assert ("anchor_sync", "anchor:" + str(anchor), "auth_required") in recorded


def test_auth_metadata_is_folded_without_helper_values(tmp_path, monkeypatch):
    import update_telemetry as ut

    summary = auth.SourceAuth(
        _source(), ["unprinted helper"], status="fixable_by_fallback"
    ).summary()
    result = ut.fold(
        [
            {
                "phase": "auth_report",
                "component": summary["id"],
                "event": "info",
                "details": {"auth": summary, "fallback_applied": True},
            }
        ],
        run={"id": "test", "owner": "engine"},
    )
    assert result["auth"]["fallback_used"] == [summary["id"]]
    assert result["auth"]["report"] == [summary]
    assert "unprinted helper" not in json.dumps(result)
    assert result["run"]["outcome"] == ut.VERIFIED_CURRENT


def test_cli_auth_report_bypasses_manager_and_writes_no_run(tmp_path, monkeypatch, capsys):
    from agent_worktrees import update_cli

    monkeypatch.setattr(sources, "marketplaces", lambda _: [])
    monkeypatch.setattr(
        update_cli,
        "_usable_worktree_manager",
        lambda: pytest.fail("report must not invoke the Manager"),
    )
    args = argparse.Namespace(
        auth_report=True, no_anchor_sync=True, auth_source=[], auth_environment=False, json=True
    )
    assert update_cli.cmd_update(args) == 0
    assert json.loads(capsys.readouterr().out)["all_ok"] is True
    assert update_events._run is None


def test_helper_configuration_failure_is_local_and_missing_git_is_explicit():
    def runner(argv, **kwargs):
        return process.Result(128)

    report = auth.report([_source()], env={}, runner=runner)
    assert report.results[0].status == "unavailable"
    assert auth.fallback(report).denies == ["marketplace:example"]
    with pytest.raises(auth.ResolverUnavailable, match="git is not available"):
        auth.report([_source()], env={}, runner=lambda *a, **k: process.Result(None, missing=True))
