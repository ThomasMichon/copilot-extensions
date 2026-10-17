"""Read-only update auth, failure-only routing, and real Git credential replay."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import io
from pathlib import Path
from types import SimpleNamespace

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
            if "--get-urlmatch" in argv:
                return process.Result(1)
            return process.Result(0, "one\0\0two\0")
        if argv[:2] == ["git", "ls-remote"]:
            if json.loads(env.get("CE_UPDATE_AUTH_ROUTES", "{}")):
                return process.Result(0)
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


@pytest.mark.parametrize("engine", [False, True])
@pytest.mark.parametrize("denied", [False, True])
def test_canonical_payload_acquisition_obeys_the_auth_session(
    tmp_path, monkeypatch, engine, denied
):
    from agent_worktrees import reconcile, update_acquisition

    item = _source(id="marketplace:copilot-extensions", cwd=tmp_path)
    monkeypatch.setattr(sources, "marketplaces", lambda _: [item])
    monkeypatch.setattr(
        auth, "report",
        lambda items: auth.AuthReport([
            auth.SourceAuth(
                item, [], status="auth_required" if denied else "fixable_by_fallback",
                expected_account="mapped", alternative={"method": "gh"},
            )
        ]),
    )
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda _: tmp_path)
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(update_acquisition.subprocess, "run", run)
    with context.session():
        result = update_acquisition.acquire(
            "tool", "copilot-extensions", "copilot-fixture", cwd=tmp_path, engine=engine
        )
    assert result == ("auth_required" if denied else "OK")
    if denied:
        assert calls == []
    else:
        assert calls[0][1]["cwd"] == tmp_path
        routes = json.loads(calls[0][1]["env"]["CE_UPDATE_AUTH_ROUTES"])
        assert routes[item.route_key]["method"] == "gh"


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
    assert int(env["GIT_CONFIG_COUNT"]) == 6
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
    environment = {**os.environ, "GH_TOKEN_TEAM": "env-fixture", "GH_HOST": "example.test"}
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
    poisoned = "core.bare" in env.values()
    assert not poisoned
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


@pytest.mark.parametrize("scope", ["probe", "fallback"])
def test_inherited_git_parameters_cannot_replace_readonly_routing(tmp_path, monkeypatch, scope):
    import shlex

    _clean_git_env(monkeypatch, tmp_path)
    writes = tmp_path / "credential-writes"
    helper = (
        "!f() { if test $1 = get; then echo username=original; echo "
        + router.SECRET_KEY
        + "=fixture; else echo $1 >> "
        + shlex.quote(writes.as_posix())
        + "; fi; }; f"
    )
    parameters = " ".join(
        "'" + value.replace("'", "'\\''") + "'"
        for value in ("credential.helper=", "credential.helper=" + helper)
    )
    base = {**os.environ, "GIT_CONFIG_PARAMETERS": parameters}
    attributes = "protocol=https\nhost=example.test\npath=team/repo\n"
    if scope == "probe":
        def probe(argv, **kwargs):
            return process.run(
                ["git", "credential", "approve"],
                input=attributes + "username=test\n" + router.SECRET_KEY + "=fixture\n\n",
                **kwargs,
            )

        assert auth._probe(auth.SourceAuth(_source(cwd=tmp_path), []), base, False, probe) == "ok"
    else:
        overlay = auth.fallback(
            auth.AuthReport([auth.SourceAuth(_source(), [], status="auth_required")])
        )
        env = {**overlay.environment(base), "CE_UPDATE_AUTH_READONLY": "1"}
        denied = process.run(
            ["git", "credential", "fill"], cwd=tmp_path, env=env, input=attributes + "\n"
        )
        assert denied.status != 0
        replay = process.run(
            ["git", "credential", "fill"],
            cwd=tmp_path,
            env=env,
            input=attributes.replace("team/repo", "team/other") + "\n",
        )
        assert replay.status == 0, replay.stderr
        assert router.parse_attributes(replay.stdout)[router.SECRET_KEY] == "fixture"
    assert not writes.exists(), "read-only routing let an inherited helper store credentials"
    assert router.original_environment(base)["GIT_CONFIG_PARAMETERS"] == parameters


@pytest.mark.parametrize(
    ("host", "token_name", "gh_host"),
    [
        ("GitHub.com", "GH_TOKEN", ""),
        ("GitHub.com:443", "GH_TOKEN", ""),
        ("enterprise.test", "GH_ENTERPRISE_TOKEN", "enterprise.test"),
        ("enterprise.test", "GITHUB_ENTERPRISE_TOKEN", "ENTERPRISE.TEST"),
        ("enterprise.test:8443", "GH_ENTERPRISE_TOKEN", "ENTERPRISE.TEST:8443"),
    ],
)
def test_environment_token_selection_matches_host_in_discovery_and_replay(
    host, token_name, gh_host
):
    item = auth.source("source", f"https://{host}/team/repo")
    env = {token_name: "fixture-secret", "GH_HOST": gh_host}
    report = auth.report(
        [item], env=env, runner=Probe(gh=False), account_for=lambda _: "mapped"
    )
    assert report.results[0].status == "fixable_by_fallback"
    assert report.results[0].alternative["method"] == "env"
    answer = router.route(
        "get",
        f"protocol=https\nhost={host.lower()}\npath=team/repo\n\n",
        auth.fallback(report).environment(env),
    )
    assert router.parse_attributes(answer)[router.SECRET_KEY] == "fixture-secret"


def test_default_https_port_discovery_replays_through_real_git(tmp_path, monkeypatch):
    _clean_git_env(monkeypatch, tmp_path)
    item = auth.source("source", "https://github.com:443/team/repo", cwd=tmp_path)
    report = auth.report(
        [item], env={"GH_TOKEN": "fixture-secret"}, runner=Probe(gh=False),
        account_for=lambda _: "mapped",
    )
    assert report.results[0].status == "fixable_by_fallback"
    assert report.results[0].alternative["method"] == "env"
    overlay = auth.fallback(report)
    result = process.run(
        ["git", "credential", "fill"], cwd=tmp_path,
        input="protocol=https\nhost=github.com:443\npath=team/repo\n\n",
        env=overlay.environment({**os.environ, "GH_TOKEN": "fixture-secret"}),
    )
    assert result.status == 0, result.stderr
    assert router.parse_attributes(result.stdout)[router.SECRET_KEY] == "fixture-secret"


@pytest.mark.parametrize("host", ["github.com", "unrelated.test"])
def test_enterprise_environment_token_never_reaches_another_host(host):
    probe = Probe(gh=False)
    report = auth.report(
        [auth.source("source", f"https://{host}/team/repo")],
        env={"GH_ENTERPRISE_TOKEN": "fixture-secret", "GH_HOST": "enterprise.test"},
        runner=probe,
        account_for=lambda _: "mapped",
    )
    assert report.results[0].status == "auth_required"
    assert not any(argv[:2] == ["gh", "api"] for argv, _ in probe.calls)


@pytest.mark.parametrize("branch", ["feature", None])
@pytest.mark.parametrize("bare", [False, True])
@pytest.mark.parametrize("inherited", [False, True])
def test_skipped_anchor_does_not_create_an_auth_requirement(
    tmp_path, monkeypatch, branch, bare, inherited
):
    _clean_git_env(monkeypatch, tmp_path)
    anchor = tmp_path / "anchor"
    subprocess.run(
        ["git", "init", "-q", "-b", branch or "main", str(anchor)],
        check=True, **git_ops.no_window_kwargs(),
    )
    if branch is None:
        subprocess.run(
            ["git", "-C", str(anchor), "-c", "user.name=Test", "-c",
             "user.email=test@example.invalid", "commit", "--allow-empty", "-qm", "fixture"],
            check=True, **git_ops.no_window_kwargs(),
        )
        subprocess.run(
            ["git", "-C", str(anchor), "checkout", "--detach", "-q"],
            check=True, **git_ops.no_window_kwargs(),
        )
    if bare:
        subprocess.run(
            ["git", "-C", str(anchor), "config", "core.bare", "true"],
            check=True, **git_ops.no_window_kwargs(),
        )
    if inherited:
        caller = tmp_path / "caller"
        subprocess.run(
            ["git", "init", "-q", "-b", "main", str(caller)],
            check=True, **git_ops.no_window_kwargs(),
        )
        subprocess.run(
            ["git", "-C", str(caller), "-c", "user.name=Test", "-c",
             "user.email=test@example.invalid", "commit", "--allow-empty", "-qm", "fixture"],
            check=True, **git_ops.no_window_kwargs(),
        )
        monkeypatch.setenv("GIT_DIR", str(caller / ".git"))
        monkeypatch.setenv("GIT_WORK_TREE", str(caller))
        monkeypatch.setenv("GIT_INDEX_FILE", str(caller / ".git" / "index"))
    repo = cfg.RepoConfig(
        anchor=str(anchor), worktree_root=str(tmp_path / "worktrees"), default_branch="main",
        remote="https://example.test/team/repo",
    )
    config = cfg.Config(
        srcroot=str(tmp_path), machine="test", platform="windows",
        repo_name="example", repos={"example": repo},
    )
    monkeypatch.setattr(cfg, "active_project", lambda: "example")
    monkeypatch.setattr(cfg, "load_config", lambda: config)
    monkeypatch.setattr(sources, "marketplaces", lambda _: [])
    monkeypatch.setattr(context, "_marketplace_context", lambda: None)
    monkeypatch.setattr(
        git_ops, "fast_forward_worktree", lambda *a, **k: pytest.fail("skipped anchor fetched")
    )
    seen = []

    def report(items):
        seen.extend(items)
        return auth.AuthReport(
            [auth.SourceAuth(item, [], status="auth_required") for item in items]
        )

    monkeypatch.setattr(auth, "report", report)

    def steps(args):
        update_runtime._fast_forward_project_anchors()
        return 0

    assert context.run_update(argparse.Namespace(no_anchor_sync=False), steps) == 0
    assert seen == []


def test_failing_repo_does_not_route_another_repo_of_the_same_owner(monkeypatch):
    def delegate(argv, **kwargs):
        return process.Result(0, "username=original\n" + router.SECRET_KEY + "=fixture\n")

    monkeypatch.setattr(router, "run", delegate)
    item = auth.SourceAuth(_source(), [], status="auth_required")
    env = auth.fallback(auth.AuthReport([item])).environment({})
    answer = router.route("get", "protocol=https\nhost=example.test\npath=team/other\n\n", env)
    assert router.parse_attributes(answer)["username"] == "original"


@pytest.mark.parametrize("module", ["json", "agent_worktrees"])
def test_real_router_does_not_execute_checkout_local_python(tmp_path, monkeypatch, module):
    _clean_git_env(monkeypatch, tmp_path)
    checkout = tmp_path / "untrusted-checkout"
    checkout.mkdir()
    marker = tmp_path / "shadowed-code-ran"
    shadow = checkout / (module + ".py")
    shadow.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n"
        "raise RuntimeError('checkout-local module executed')\n",
        encoding="utf-8",
    )
    item = auth.SourceAuth(
        _source(cwd=checkout), [], expected_account="mapped",
        status="fixable_by_fallback", alternative={"method": "env"},
    )
    env = auth.fallback(auth.AuthReport([item])).environment({
        **os.environ, "GH_TOKEN_TEAM": "fixture-secret", "GH_HOST": "example.test",
    })
    result = process.run(
        ["git", "credential", "fill"], cwd=checkout, env=env,
        input="protocol=https\nhost=example.test\npath=team/repo\n\n",
    )
    assert not marker.exists(), "credential helper executed code from the checkout"
    assert result.status == 0, result.stderr
    assert router.parse_attributes(result.stdout)[router.SECRET_KEY] == "fixture-secret"


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
    monkeypatch.setattr(
        update_runtime.bare_anchor_sync, "branch_skip_reason", lambda *a, **k: None
    )
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


@pytest.mark.parametrize("equals", [False, True])
def test_cli_explicit_source_report_needs_no_adopted_project(
    tmp_path, monkeypatch, capsys, equals
):
    from agent_worktrees import __main__ as main, update_cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cfg, "_ACTIVE_PROJECT", None)
    monkeypatch.setattr(main, "_resolve_active_project", lambda _: (None, None))
    monkeypatch.setattr(
        update_cli, "_usable_worktree_manager",
        lambda: pytest.fail("report reached the Manager"),
    )
    monkeypatch.setattr(
        main, "_cmd_update_in_plugin", lambda _: pytest.fail("report reached the updater")
    )
    seen = []

    def report(items):
        seen.extend(items)
        return auth.AuthReport([auth.SourceAuth(item, [], status="anonymous") for item in items])

    monkeypatch.setattr(auth, "report", report)
    source = "https://github.com/team/repo"
    flags = ["--auth-source=" + source] if equals else ["--auth-source", source]
    assert main.main([
        "update", "--no-manager", "--auth-report", "--no-anchor-sync",
        "--auth-cwd", str(tmp_path), "--json", *flags,
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["all_ok"] is True
    assert seen[0].cwd == tmp_path and seen[0].url == source
    assert update_events._run is None
    assert not main._is_no_project_invocation(["update"])
    assert not main._is_no_project_invocation(["update", "--auth-source", source])


@pytest.mark.parametrize("method", ["configured", "fill"])
def test_real_configured_router_preserves_non_password_credentials(tmp_path, monkeypatch, method):
    config = _clean_git_env(monkeypatch, tmp_path)
    helper = (
        "!f() { echo capability[]=authtype; "
        "echo authtype=fixture-scheme; echo credential=fixture-secret; }; f"
    )
    subprocess.run(
        ["git", "config", "--file", str(config), "credential.helper", helper],
        check=True, **git_ops.no_window_kwargs(),
    )
    item = auth.SourceAuth(_source(cwd=tmp_path), [])
    request = (
        "capability[]=authtype\ncapability[]=state\n"
        "protocol=https\nhost=example.test\npath=team/repo\n\n"
    )
    captured = []

    def probe(argv, **kwargs):
        result = process.run(["git", "credential", "fill"], input=request, **kwargs)
        captured.append(result)
        return result

    if method == "configured":
        assert auth._probe(item, dict(os.environ), False, probe) == "ok"
    else:
        item.status = "fixable_by_fallback"
        item.alternative = {"method": "fill"}
        result = process.run(
            ["git", "credential", "fill"], cwd=tmp_path, input=request,
            env=auth.fallback(auth.AuthReport([item])).environment(os.environ),
        )
        assert result.status == 0, result.stderr
        captured.append(result)
    response = router.parse_attributes(captured[0].stdout)
    assert response["authtype"] == "fixture-scheme"
    assert response["credential"] == "fixture-secret"
    assert router.SECRET_KEY not in response


@pytest.mark.parametrize("account", [None, "mapped", "other"])
def test_native_credentials_are_discovered_validated_and_replayed(
    tmp_path, monkeypatch, account,
):
    config = _clean_git_env(monkeypatch, tmp_path)
    helper = (
        "!f() { echo capability[]=authtype; echo username=mapped; "
        "echo authtype=fixture-scheme; echo credential=fixture-secret; }; f"
    )
    for key, value in (
        ("credential.helper", helper),
        ("http.https://example.test/.extraHeader", "Authorization: stale"),
    ):
        subprocess.run(
            ["git", "config", "--file", str(config), key, value],
            check=True, **git_ops.no_window_kwargs(),
        )
    before = config.read_bytes()
    item = _source(cwd=tmp_path)
    request = (
        "capability[]=authtype\nprotocol=https\nhost=example.test\npath=team/repo\n\n"
    )
    api_calls, contents_calls = [], []

    def run(argv, **kwargs):
        if argv[:2] == ["git", "config"] or argv == ["git", "credential", "fill"]:
            return process.run(argv, **kwargs)
        if argv[:2] == ["git", "ls-remote"]:
            headers = process.run(
                ["git", "config", "--get-urlmatch", "http.extraHeader", item.url],
                cwd=tmp_path, env=kwargs["env"],
            )
            if headers.stdout.strip():
                return process.Result(1)
            credential = process.run(
                ["git", "credential", "fill"], cwd=tmp_path,
                input=request, env=kwargs["env"],
            )
            response = router.parse_attributes(credential.stdout)
            if response.get("credential") != "fixture-secret":
                return process.Result(1)
            contents_calls.append(response)
            return process.Result(0 if response.get("authtype") == "fixture-scheme" else 1)
        if argv[:3] == ["gh", "auth", "token"]:
            return process.Result(1)
        if argv[-1] == "agent_worktrees.update_auth_probe":
            payload = json.loads(kwargs["input"])
            assert payload == {
                "host": "example.test", "authtype": "fixture-scheme",
                "credential": "fixture-secret",
            }
            api_calls.append(payload)
            return process.Result(0, json.dumps({"status": "ok", "login": "mapped"}))
        pytest.fail("unexpected native credential probe")

    report = auth.report(
        [item], env=dict(os.environ), runner=run, account_for=lambda _: account,
    )
    assert api_calls, "native discovery must validate the actual credential and scheme"
    if account == "other":
        assert report.results[0].status == "mismatch"
        assert auth.fallback(report).denies == [item.id]
        assert contents_calls == []
    else:
        assert report.results[0].status == "fixable_by_fallback"
        assert report.results[0].alternative["method"] == "fill"
        assert contents_calls, "identity alone does not prove repository access"
        overlay = auth.fallback(report)
        assert "fixture-secret" not in json.dumps(overlay.variables)
        replay = process.run(
            ["git", "credential", "fill"], cwd=tmp_path, input=request,
            env=overlay.environment(os.environ),
        )
        assert replay.status == 0, replay.stderr
        response = router.parse_attributes(replay.stdout)
        assert response["authtype"] == "fixture-scheme"
        assert response["credential"] == "fixture-secret"
    assert config.read_bytes() == before


def test_helper_configuration_failure_is_local_and_missing_git_is_explicit():
    def runner(argv, **kwargs):
        return process.Result(128)

    report = auth.report([_source()], env={}, runner=runner)
    assert report.results[0].status == "unavailable"
    assert auth.fallback(report).denies == ["marketplace:example"]
    with pytest.raises(auth.ResolverUnavailable, match="git is not available"):
        auth.report([_source()], env={}, runner=lambda *a, **k: process.Result(None, missing=True))


def test_inherited_updater_snapshot_cannot_restore_repository_selectors(tmp_path, monkeypatch):
    _clean_git_env(monkeypatch, tmp_path)
    monkeypatch.setenv(
        "CE_UPDATE_AUTH_BASE_CONFIG",
        json.dumps(
            {
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "core.bare",
                "GIT_CONFIG_VALUE_0": "true",
            }
        ),
    )
    token = context._repository.set(
        auth.fallback(auth.AuthReport([auth.SourceAuth(_source(), [], status="auth_required")]))
    )
    try:
        env = context.repository_environment(git_ops.repository_identity_env())
    finally:
        context._repository.reset(token)
    poisoned = "core.bare" in env.values()
    assert not poisoned
    assert env["GIT_CONFIG_COUNT"] == "4"


def test_metadata_only_token_does_not_stop_discovery_before_working_git_credential():
    class CandidateProbe(Probe):
        def __call__(self, argv, **kwargs):
            if argv[:2] == ["git", "ls-remote"]:
                self.calls.append((argv, kwargs))
                routes = json.loads(kwargs["env"].get("CE_UPDATE_AUTH_ROUTES", "{}"))
                if routes:
                    key = "GH_TOKEN_TEAM"
                    return process.Result(0 if kwargs["env"].get(key) == self.secret else 1)
                return process.Result(1)
            return super().__call__(argv, **kwargs)

    probe = CandidateProbe()
    report = auth.report(
        [_source()],
        env={
            "GH_TOKEN": "metadata-only",
            "GH_HOST": "example.test",
        },
        account_for=lambda _: "mapped",
        runner=probe,
    )
    assert report.results[0].alternative["method"] == "gh"
    candidate_calls = [
        kwargs
        for argv, kwargs in probe.calls
        if argv[:2] == ["git", "ls-remote"]
        and json.loads(kwargs["env"].get("CE_UPDATE_AUTH_ROUTES", "{}"))
    ]
    assert len(candidate_calls) == 2
    assert all(call["env"]["CE_UPDATE_AUTH_READONLY"] == "1" for call in candidate_calls)


def test_anchor_preflight_uses_isolated_environment_for_every_probe():
    probe = Probe(configured=True)
    item = auth.source(
        "anchor:test", "https://example.test/team/repo", purpose="anchor", cwd=Path.cwd()
    )
    auth.report(
        [item],
        env={
            "GIT_DIR": "other-checkout",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "credential.helper",
            "GIT_CONFIG_VALUE_0": "wrong-helper",
        },
        runner=probe,
        account_for=lambda _: "mapped",
    )
    assert all("GIT_DIR" not in kwargs["env"] for _, kwargs in probe.calls)
    assert all("wrong-helper" not in kwargs["env"].values() for _, kwargs in probe.calls)


def test_registered_siblings_keep_their_own_account_overrides(monkeypatch):
    from agent_worktrees import repos

    registry = repos.ReposRegistry(
        repos={
            name: repos.RepoEntry(
                name=name, remote=f"https://github.com/team/{name}", account=f"account-{name}"
            )
            for name in ("one", "two")
        }
    )
    monkeypatch.setattr(repos, "read_registry", lambda: registry)
    report = auth.report(
        [auth.source(name, f"https://github.com/team/{name}") for name in ("one", "two")],
        env={},
        runner=Probe(),
    )
    assert [item.expected_account for item in report.results] == ["account-one", "account-two"]


@pytest.mark.parametrize("remote", [
    "https://github.com:443/Team/Repo.git",
    "https://GitHub.COM/Team/Repo.git",
    "ssh://git@github.com:22/Team/Repo.git",
    "git@GitHub.COM:Team/Repo.git",
])
@pytest.mark.parametrize("explicit", ["", "override"])
def test_registered_public_remote_normalization_preserves_account_policy(
    monkeypatch, remote, explicit,
):
    from agent_worktrees import repo_accounts, repos

    entry = repos.RepoEntry(name="fixture", remote=remote, account=explicit)
    registry = repos.ReposRegistry(repos={"fixture": entry}, account_map={"team": "mapped"})
    monkeypatch.setattr(repos, "read_registry", lambda: registry)
    monkeypatch.setattr(
        repos, "write_registry", lambda *_: pytest.fail("account lookup must be read-only"),
    )

    assert repo_accounts.account_for_remote("https://github.com/team/repo") == (
        explicit or "mapped"
    )
    assert registry.repos["fixture"] is entry
    assert entry.remote == remote


@pytest.mark.parametrize("remote_form", ["https", "ssh", "scp"])
@pytest.mark.parametrize("public_port", ["", ":443"])
def test_same_slug_on_different_hosts_uses_its_own_registered_account(
    monkeypatch, remote_form, public_port,
):
    from agent_worktrees import repos

    enterprise = {
        "https": "https://enterprise.test/team/repo.git",
        "ssh": "ssh://git@enterprise.test/team/repo.git",
        "scp": "git@enterprise.test:team/repo.git",
    }[remote_form]
    registry = repos.ReposRegistry(repos={
        "public": repos.RepoEntry(
            name="public", remote="https://github.com/team/repo.git", account="public-account",
        ),
        "enterprise": repos.RepoEntry(
            name="enterprise", remote=enterprise, account="enterprise-account",
        ),
    })
    monkeypatch.setattr(repos, "read_registry", lambda: registry)
    selected = [
        auth.source("public", f"https://github.com{public_port}/team/repo"),
        auth.source("enterprise", "https://enterprise.test/team/repo"),
        auth.source("unregistered", "https://unrelated.test/team/repo"),
    ]
    report = auth.report(selected, env={}, runner=Probe(gh=False))
    assert [item.expected_account for item in report.results] == [
        "public-account", "enterprise-account", None,
    ]


def test_managed_anchor_does_not_inject_legacy_header_even_without_fallback(monkeypatch, tmp_path):
    item = auth.source(
        "anchor:test", "https://github.com/team/repo", purpose="anchor", cwd=tmp_path
    )
    monkeypatch.setattr(git_ops, "_remote_url", lambda *a, **k: item.url)
    monkeypatch.setattr(
        git_ops,
        "_auth_config_args_for_url",
        lambda _: pytest.fail("legacy header bypassed the auth report"),
    )
    with context.session() as session:
        session.anchor_reports[tmp_path] = auth.AuthReport(
            [auth.SourceAuth(item, [], status="ok")]
        )
        session.anchor_overlays[tmp_path] = None
        with context.repository_scope(tmp_path):
            assert git_ops._auth_config_args("origin", cwd=tmp_path) == []


def test_success_sweeps_owned_posix_session_without_touching_inherited_sessions(monkeypatch):
    captured = []
    monkeypatch.setattr(process, "os", SimpleNamespace(name="posix", getpgrp=lambda: 123))
    monkeypatch.setattr(process, "contained_test_mode", lambda: False)
    monkeypatch.setattr(
        process.installer_run, "_sweep_exited_session", lambda child: captured.append(child.pid)
    )

    def spawn(*args, **kwargs):
        child = SimpleNamespace(
            pid=456,
            returncode=0,
            stdin=io.BytesIO(),
            stdout=io.BytesIO(b"ok"),
            stderr=io.BytesIO(),
            wait=lambda **k: 0,
            poll=lambda: 0,
        )
        return child, None

    monkeypatch.setattr(process, "spawn_sync_in_kill_on_close_job", spawn)
    assert process.run(["fixture"], env={}).status == 0
    assert captured == [456]
    captured.clear()
    assert process.run(["fixture"], env={"CE_UPDATE_AUTH_PROCESS_GROUP": "123"}).status == 0
    assert captured == []


def _assert_posix_descendant_exited(pid, started):
    from agent_worktrees import installer_run
    import signal
    import time

    assert started is not None, "helper did not capture its descendant's identity"
    try:
        until = time.monotonic() + 5
        while installer_run._start_time(pid) == started and time.monotonic() < until:
            time.sleep(0.05)
        assert installer_run._start_time(pid) != started, "successful probe left a live helper"
    finally:
        if installer_run._start_time(pid) == started:
            member = installer_run._bind(pid, started)
            if member is not None:
                try:
                    installer_run._send(member, signal.SIGKILL)
                finally:
                    if member.pidfd is not None:
                        os.close(member.pidfd)


def test_posix_descendant_assertion_leaves_a_reused_pid_untouched(monkeypatch):
    from agent_worktrees import installer_run
    import time

    monkeypatch.setattr(installer_run, "_start_time", lambda pid: "replacement")
    ticks = iter([0, 6])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(os, "kill", lambda *args: pytest.fail("signalled a reused PID"))
    monkeypatch.setattr(
        installer_run, "_bind",
        lambda *args: pytest.fail("bound a process after its identity changed"),
    )
    _assert_posix_descendant_exited(456, "original")


@pytest.mark.parametrize("binding", ["gone", "fallback", "pidfd"])
def test_posix_descendant_assertion_cleans_up_only_a_bound_identity(monkeypatch, binding):
    from agent_worktrees import installer_run
    import signal
    import time

    member = installer_run._Member(456, "original", 42 if binding == "pidfd" else None)
    sent, closed, bound = [], [], []
    monkeypatch.setattr(installer_run, "_start_time", lambda pid: "original")
    ticks = iter([0, 6])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(os, "kill", lambda *args: pytest.fail("used an unbound PID"))
    monkeypatch.setattr(os, "close", closed.append)
    monkeypatch.setattr(installer_run, "_send", lambda *args: sent.append(args))

    def bind(pid, started):
        bound.append((pid, started))
        return None if binding == "gone" else member

    monkeypatch.setattr(installer_run, "_bind", bind)
    with pytest.raises(AssertionError, match="successful probe left a live helper"):
        _assert_posix_descendant_exited(456, "original")
    assert bound == [(456, "original")]
    assert sent == ([] if binding == "gone" else [(member, signal.SIGKILL)])
    assert closed == ([42] if binding == "pidfd" else [])


@pytest.mark.skipif(os.name == "nt", reason="native POSIX session lifetime")
def test_successful_posix_helper_cannot_leave_a_redirected_descendant(tmp_path, monkeypatch):
    from agent_worktrees import installer_run

    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    child_code = (
        "import json,subprocess,sys; "
        f"sys.path.insert(0,{str(Path(installer_run.__file__).parents[1])!r}); "
        "from agent_worktrees.installer_run import _start_time; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
        "print(json.dumps([p.pid,_start_time(p.pid)]),flush=True)"
    )
    result = process.run([sys.executable, "-c", child_code], env=os.environ)
    pid, started = json.loads(result.stdout.strip())
    _assert_posix_descendant_exited(pid, started)
    assert result.status == 0


@pytest.mark.parametrize("core_denied", [False, True])
def test_normal_update_only_blocks_on_acquired_marketplaces(monkeypatch, core_denied):
    core = _source(id="marketplace:copilot-extensions")
    unrelated = _source("private", id="marketplace:private")
    monkeypatch.setattr(sources, "marketplaces", lambda _: [core, unrelated])
    monkeypatch.setattr(context, "_marketplace_context", lambda: None)

    def report(items):
        return auth.AuthReport([
            auth.SourceAuth(
                item, [],
                status="auth_required" if item == unrelated or core_denied else "ok",
            )
            for item in items
        ])

    monkeypatch.setattr(auth, "report", report)
    args = argparse.Namespace(no_anchor_sync=True)
    assert context.run_update(args, lambda _: 0) == (4 if core_denied else 0)
    standalone = argparse.Namespace(no_anchor_sync=True, json=True)
    assert context.cmd_report(standalone) == 4


@pytest.mark.parametrize("user_required", [False, True])
@pytest.mark.parametrize("telemetry", [False, True])
def test_inventory_only_auth_context_remains_advisory(
    tmp_path, monkeypatch, user_required, telemetry,
):
    import update_telemetry as ut

    project = tmp_path / "project"
    monkeypatch.setattr(context, "_marketplace_context", lambda: project)
    home = tmp_path / "telemetry"
    monkeypatch.setenv(ut.ENV_HOME, str(home))
    for variable in (ut.ENV_RUN_ID, ut.ENV_EVENTS, ut.ENV_OWNER):
        monkeypatch.delenv(variable, raising=False)
    if not telemetry:
        monkeypatch.setattr(update_events, "_ut", None)
    monkeypatch.setattr(
        sources, "marketplaces",
        lambda cwd: [_source(id="marketplace:copilot-extensions", cwd=cwd)],
    )
    probed = []

    def report(items):
        probed.extend(item.cwd for item in items)
        return auth.AuthReport([
            auth.SourceAuth(item, [], status="auth_required" if item.cwd is None else "anonymous")
            for item in items
        ])

    monkeypatch.setattr(auth, "report", report)
    monkeypatch.setattr(update_runtime, "_core_helper", lambda name, local: local)
    monkeypatch.setattr(
        update_runtime, "_refresh_marketplace",
        lambda marketplace, cwd: not context.blocked(cwd, marketplace),
    )
    monkeypatch.setattr(
        update_runtime, "_browse_marketplace_plugins",
        lambda *args, **kwargs: {"active", "inventory", "required"},
    )
    monkeypatch.setattr(
        update_runtime, "_update_one_plugin_payload",
        lambda name, marketplace, cwd: "auth_required" if context.blocked(cwd, marketplace) else "OK",
    )
    target = update_runtime._RegisteredPluginTarget
    activation = update_runtime._PluginActivation
    targets = {
        "inventory": target(None, activation.INACTIVE),
        "active": target(project, activation.ACTIVE),
    }
    if user_required:
        targets["required"] = target(None, activation.ACTIVE)
    update_events.start()
    try:
        rc = context.run_update(
            argparse.Namespace(no_anchor_sync=True),
            lambda _: 0 if update_runtime._update_registered_plugins(targets) else 1,
        )
        assert update_events.finish(rc) == (4 if user_required else 0)
    finally:
        update_events.finish(1)
    assert probed.count(None) == 1
    if telemetry:
        status = json.loads((home / "update-status.json").read_text(encoding="utf-8"))
        assert status["run"]["outcome"] == (
            ut.AUTH_REQUIRED if user_required else ut.VERIFIED_CURRENT
        )


def test_required_auth_context_cannot_be_downgraded_to_advisory(monkeypatch):
    monkeypatch.setattr(
        sources, "marketplaces",
        lambda cwd: [_source(id="marketplace:copilot-extensions", cwd=cwd)],
    )
    monkeypatch.setattr(
        auth, "report",
        lambda items: auth.AuthReport([
            auth.SourceAuth(item, [], status="auth_required") for item in items
        ]),
    )
    recorded = []
    monkeypatch.setattr(
        update_events, "auth_result",
        lambda summary, **kwargs: recorded.append(summary["required"]),
    )
    with context.session() as session:
        session.prepare(None, required=False)
        assert session.blocked(None, "marketplace:copilot-extensions")
        assert not session.unresolved
        session.prepare(None, required=True)
        session.prepare(None, required=False)
        assert session.unresolved
    assert recorded == [False, True]


def test_user_local_settings_override_marketplace_source_and_activation(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    user = tmp_path / ".copilot"
    user.mkdir()
    (user / "settings.json").write_text(
        json.dumps(
            {
                "enabledPlugins": {"tool@private": True},
                "extraKnownMarketplaces": {
                    "private": {"source": {"source": "github", "repo": "team/old"}}
                },
            }
        )
    )
    (user / "settings.local.json").write_text(
        json.dumps(
            {
                "enabledPlugins": {"tool@private": False},
                "extraKnownMarketplaces": {
                    "private": {"source": {"source": "github", "repo": "team/current"}}
                },
            }
        )
    )
    selected = sources.marketplaces(None)
    assert selected[0].repo == "current"
    assert not selected[0].required


def test_report_seam_preserves_manager_acquisition_cwd(tmp_path, monkeypatch, capsys):
    seen = []

    def report(items):
        seen.extend(items)
        return auth.AuthReport([])

    monkeypatch.setattr(auth, "report", report)
    args = argparse.Namespace(
        auth_source=["https://github.com/team/repo"],
        auth_cwd=str(tmp_path),
        no_anchor_sync=True,
        auth_environment=False,
        json=True,
    )
    assert context.cmd_report(args) == 0
    assert seen[0].cwd == tmp_path


def test_fallback_resets_only_the_failed_repositories_http_headers():
    item = auth.SourceAuth(
        _source(),
        [],
        expected_account="mapped",
        status="fixable_by_fallback",
        alternative={"method": "gh"},
    )
    overlay = auth.fallback(auth.AuthReport([item]))
    assert (f"http.{item.source.url}.extraHeader", "") in overlay.entries
    assert not any(key.lower() == "http.extraheader" for key, _ in overlay.entries)


def test_reachable_remote_with_unborn_head_is_not_an_auth_failure(tmp_path, monkeypatch):
    _clean_git_env(monkeypatch, tmp_path)
    remote = tmp_path / "unborn.git"
    subprocess.run(
        ["git", "init", "--bare", "-q", str(remote)], check=True, **git_ops.no_window_kwargs()
    )
    item = auth.SourceAuth(_source(), [])

    def local_probe(argv, **kwargs):
        return process.run(
            [str(remote) if part == item.source.url else part for part in argv], **kwargs
        )

    assert auth._probe(item, dict(os.environ), True, local_probe) == "ok"


@pytest.mark.parametrize("failed", ["marketplace", "anchor"])
def test_same_url_different_contexts_do_not_share_a_credential_overlay(
    tmp_path, monkeypatch, failed
):
    market = _source(id="marketplace:copilot-extensions", cwd=tmp_path / "market")
    anchor = auth.source("anchor:test", market.url, purpose="anchor", cwd=tmp_path / "anchor")
    monkeypatch.setattr(sources, "marketplaces", lambda _: [market])
    monkeypatch.setattr(sources, "anchors", lambda: [anchor])

    def report(items):
        return auth.AuthReport(
            [
                auth.SourceAuth(
                    item, [], status="auth_required" if item.purpose == failed else "ok"
                )
                for item in items
            ]
        )

    monkeypatch.setattr(auth, "report", report)
    with context.session() as session:
        session.prepare(market.cwd, include_anchors=True)
        if failed == "anchor":
            assert session.environment(market.cwd) is None
        else:
            with context.repository_scope(anchor.cwd):
                env = context.repository_environment({})
            assert "CE_UPDATE_AUTH_ROUTES" not in env


@pytest.mark.parametrize("code", [401, 403])
def test_git_returned_error_http_auth_status_is_not_a_generic_failure(monkeypatch, code):
    recorded = []
    monkeypatch.setattr(update_events, "record", lambda *a, **k: recorded.append(a))
    with context.session() as session:
        context.anchor_failure("example", f"fatal: The requested URL returned error: {code}")
        assert session.unresolved
        assert not session.anchor_failed
    assert recorded[-1][-1] == "auth_required"


def test_local_only_anchor_does_not_abort_auth_source_discovery(tmp_path, monkeypatch):
    _clean_git_env(monkeypatch, tmp_path)
    anchor = tmp_path / "local-only"
    subprocess.run(["git", "init", "-q", str(anchor)], check=True, **git_ops.no_window_kwargs())
    repo = cfg.RepoConfig(
        anchor=str(anchor), worktree_root=str(tmp_path / "worktrees"), default_branch="main"
    )
    config = cfg.Config(
        srcroot=str(tmp_path),
        machine="test",
        platform="windows",
        repo_name="example",
        repos={"example": repo},
    )
    monkeypatch.setattr(cfg, "active_project", lambda: "example")
    monkeypatch.setattr(cfg, "load_config", lambda: config)
    assert sources.anchors() == []


def test_first_clone_report_ignores_caller_repository_helper_reset(tmp_path, monkeypatch, capsys):
    config = _clean_git_env(monkeypatch, tmp_path)
    config.write_text("[credential]\n    helper = global-fixture\n", encoding="utf-8")
    caller = tmp_path / "caller"
    subprocess.run(["git", "init", "-q", str(caller)], check=True, **git_ops.no_window_kwargs())
    subprocess.run(
        ["git", "-C", str(caller), "config", "--add", "credential.helper", ""],
        check=True,
        **git_ops.no_window_kwargs(),
    )
    staging = caller / "manager" / "staging"
    staging.mkdir(parents=True)
    chains = []

    def report(items, *, env=None):
        for item in items:
            chains.append(auth.helper_chain(item, dict(os.environ) if env is None else env))
        return auth.AuthReport([])

    monkeypatch.setattr(auth, "report", report)
    args = argparse.Namespace(
        auth_source=["https://github.com/team/repo"],
        auth_cwd=str(staging),
        auth_non_repository=True,
        no_anchor_sync=True,
        auth_environment=True,
        json=True,
    )
    assert context.cmd_report(args) == 0
    assert chains == [["global-fixture"]]
    payload = json.loads(capsys.readouterr().out)
    assert payload["environment"]["GIT_CEILING_DIRECTORIES"] == str(staging.parent)


def test_helper_diagnostics_preserve_a_trailing_empty_reset():
    chain = auth.helper_chain(
        _source(), {}, runner=lambda *a, **k: process.Result(0, "global-fixture\0\0")
    )
    assert chain == []


def test_unsupported_explicit_source_reports_a_structured_decline(capsys):
    args = argparse.Namespace(
        auth_source=["https://git.example.test/team/repo%20name"],
        auth_cwd=None,
        auth_non_repository=False,
        no_anchor_sync=True,
        auth_environment=True,
        json=True,
    )
    assert context.cmd_report(args) == 3
    result = json.loads(capsys.readouterr().out)
    assert result["supported"] is False
    assert result["all_ok"] is False


@pytest.mark.parametrize("suffix", ["", ".git"])
@pytest.mark.parametrize("account", [None, "mapped"])
@pytest.mark.parametrize("authority", ["example.test", "github.com:443", "enterprise.test:8443"])
def test_fill_discovery_and_replay_keep_the_actual_source_path(
    tmp_path, monkeypatch, suffix, account, authority,
):
    config = _clean_git_env(monkeypatch, tmp_path)
    path = "team/repo" + suffix
    username_guard = (
        'case "$line" in username=*) exit 0 ;; esac; '
        if account is None else ""
    )
    helper = (
        '!f() { matched=; host_matched=; while IFS= read -r line; do '
        + username_guard
        + 'if test "$line" = host='
        + authority
        + '; then host_matched=1; fi; '
        + 'if test "$line" = path='
        + path
        + '; then matched=1; fi; done; if test "$matched$host_matched" = 11; '
        + "then printf '%s\\n' username=mapped "
        + router.SECRET_KEY
        + "=fixture; fi; }; f"
    )
    subprocess.run(
        ["git", "config", "--file", str(config), "credential.helper", helper],
        check=True,
        **git_ops.no_window_kwargs(),
    )
    subprocess.run(
        ["git", "config", "--file", str(config), "credential.useHttpPath", "true"],
        check=True,
        **git_ops.no_window_kwargs(),
    )
    before = config.read_bytes()
    probe = Probe(gh=False)

    def run(argv, **kwargs):
        if argv == ["git", "credential", "fill"]:
            return process.run(argv, **kwargs)
        return probe(argv, **kwargs)

    item = auth.source("source", f"https://{authority}/{path}", cwd=tmp_path)
    report = auth.report(
        [item], env=dict(os.environ), runner=run, account_for=lambda _: account
    )
    assert report.results[0].status == "fixable_by_fallback"
    assert report.results[0].alternative["method"] == "fill"
    result = process.run(
        ["git", "credential", "fill"],
        cwd=tmp_path,
        env=auth.fallback(report).environment(os.environ),
        input=f"protocol=https\nhost={authority}\npath={path}\n\n",
    )
    assert result.status == 0, result.stderr
    assert router.parse_attributes(result.stdout)[router.SECRET_KEY] == "fixture"
    assert config.read_bytes() == before


@pytest.mark.parametrize("working_header", [False, True])
def test_public_source_header_fallback_is_failure_only_and_source_scoped(
    tmp_path, monkeypatch, working_header,
):
    config = _clean_git_env(monkeypatch, tmp_path)
    configured_header = "X-Fixture: working" if working_header else "Authorization: stale"
    subprocess.run(
        ["git", "config", "--file", str(config),
         "http.https://example.test/.extraHeader", configured_header],
        check=True, **git_ops.no_window_kwargs(),
    )
    before = config.read_bytes()
    item = _source(cwd=tmp_path)

    def effective_headers(url, env):
        result = process.run(
            ["git", "config", "--get-urlmatch", "http.extraHeader", url],
            cwd=tmp_path, env=env,
        )
        assert result.status in (0, 1)
        return result.stdout.strip()

    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[:2] == ["git", "config"]:
            return process.run(argv, **kwargs)
        if argv[:2] == ["git", "ls-remote"]:
            headers = effective_headers(item.url, kwargs["env"])
            return process.Result(0 if headers in ("", "X-Fixture: working") else 1)
        pytest.fail("public fallback must not discover credentials")

    report = auth.report(
        [item], env=dict(os.environ), runner=run,
        account_for=lambda _: pytest.fail("public source does not need an account"),
    )
    if working_header:
        assert not report.failing
        assert auth.fallback(report) is None
    else:
        assert report.results[0].status == "fixable_by_fallback"
        assert report.results[0].visibility == "public"
        assert report.results[0].alternative["method"] == "anonymous"
        overlay = auth.fallback(report)
        env = overlay.environment(os.environ)
        assert effective_headers(item.url, env) == ""
        assert effective_headers("https://example.test/team/other", env) == configured_header
        assert router.route(
            "get", "protocol=https\nhost=example.test\npath=team/repo\n\n", env,
        ) == ""
        readonly = auth.fallback(report, apply_fixes=False)
        assert readonly.applies_to == [] and readonly.denies == [item.id]
    assert config.read_bytes() == before


@pytest.mark.parametrize("invocation", ["nested", "transported", "project"])
def test_report_uses_the_normal_update_marketplace_context(
    tmp_path, monkeypatch, capsys, invocation
):
    from agent_worktrees import update_cli

    root = tmp_path / "checkout"
    settings = root / ".github" / "copilot"
    settings.mkdir(parents=True)
    (settings / "settings.json").write_text(
        json.dumps(
            {
                "enabledPlugins": {"tool@private": True},
                "extraKnownMarketplaces": {
                    "private": {"source": {"source": "github", "repo": "team/private"}}
                },
            }
        ),
        encoding="utf-8",
    )
    nested = root / "src"
    nested.mkdir()
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(update_cli._core(), "_INVOCATION_CWD", None)
    monkeypatch.delenv("AGENT_WORKTREES_UPDATE_CONTEXT", raising=False)
    monkeypatch.setattr(update_cli._core(), "_project_update_context", lambda: root)
    monkeypatch.chdir(nested if invocation == "nested" else unrelated)
    if invocation == "transported":
        monkeypatch.setenv("AGENT_WORKTREES_UPDATE_CONTEXT", str(nested))
    elif invocation == "project":
        monkeypatch.setattr(update_cli._core(), "_invocation_update_context", lambda: None)
    seen = []

    def report(items):
        seen.extend(items)
        return auth.AuthReport(
            [auth.SourceAuth(item, [], status="auth_required") for item in items]
        )

    monkeypatch.setattr(auth, "report", report)
    args = argparse.Namespace(
        auth_source=[], no_anchor_sync=True, auth_environment=False, json=True
    )
    assert context.cmd_report(args) == 4
    assert [(item.id, item.required, item.cwd) for item in seen] == [
        ("marketplace:private", True, root)
    ]
    assert json.loads(capsys.readouterr().out)["all_ok"] is False
