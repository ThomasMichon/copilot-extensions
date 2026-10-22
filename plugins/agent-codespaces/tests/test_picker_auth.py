"""Provider authentication stays read-only until explicit authorization."""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_codespaces import authorize, gh_account, picker_auth, picker_pool, pool


def status_text(login="example-user", scope="repo"):
    return (
        f"github.com\n  Logged in to github.com account {login} (keyring)\n"
        f"  - Token scopes: '{scope}'\n"
    )


@pytest.mark.parametrize(("rc", "text", "expected"), [
    (1, "You are not logged into any GitHub hosts. Run gh auth login", "auth_required"),
    (0, status_text(), "scope_blocked"),
    (0, status_text(scope="codespace"), "ready"),
    (1, "Timeout trying to log in to github.com account example-user", "error"),
    (1, "TLS connection failed", "error"),
])
def test_classify(rc, text, expected):
    assert picker_auth.classify(rc, text, "example-user")["status"] == expected


def test_relevant_accounts_ignore_unrelated_active_failure(monkeypatch):
    from agent_codespaces import account_binding, config

    monkeypatch.setattr(account_binding, "bound_accounts_or_raise", lambda: ())
    monkeypatch.setattr(config, "load_merged_config", lambda **_: SimpleNamespace(repos={"example/repo": {}}))
    monkeypatch.setattr(gh_account, "account_for_repo", lambda _: "example-user")
    monkeypatch.setattr(gh_account, "active_account", lambda: "other-user")
    monkeypatch.setattr(picker_auth, "authorized_configs", lambda: {})
    monkeypatch.setattr(picker_auth, "status", lambda *_: (
        1, status_text(scope="codespace") + "Failed to log in to github.com account other-user",
    ))
    login = Mock(side_effect=AssertionError("read must not authorize"))
    monkeypatch.setattr(authorize, "authorize", login)
    assert picker_auth.probe()["status"] == "ready"
    login.assert_not_called()


def test_probe_failure_is_visible(monkeypatch):
    from agent_codespaces import account_binding

    monkeypatch.setattr(account_binding, "bound_accounts_or_raise", Mock(side_effect=RuntimeError("locked")))
    assert picker_auth.probe()["status"] == "error"


@pytest.mark.parametrize("state", ["auth_required", "scope_blocked", "error", "ready"])
def test_one_shot_and_stream_match_and_blocked_does_not_list(monkeypatch, capsys, state):
    auth = {"status": state, "account": "example-user", "message": "" if state == "ready" else state}
    monkeypatch.setattr(picker_auth, "probe", lambda: auth)
    budget = SimpleNamespace(total_cores=64)
    build = Mock(return_value=([], budget))
    monkeypatch.setattr(pool, "build_pool", build)
    monkeypatch.setattr(pool, "picker_payload", lambda *_: {"entries": [], "summary": {}})
    args = SimpleNamespace(budget=None, stale_after=None, stream=False)
    assert picker_pool.command(args) == 0
    payload = json.loads(capsys.readouterr().out)
    stream = io.StringIO()
    monkeypatch.setattr("sys.__stdout__", stream)
    args.stream = True
    assert picker_pool.command(args) == 0
    frames = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert frames[-2]["summary"] == payload["summary"]
    assert payload["summary"]["auth_status"] == state
    if state != "ready":
        build.assert_not_called()


def test_subscription_reprobes_and_revokes_stale_rows(monkeypatch):
    snapshots = iter([
        {"entries": [{"id": "space"}], "summary": {"auth_status": "ready"}},
        {"entries": [], "summary": {"auth_status": "scope_blocked"}},
    ])
    monkeypatch.setattr(picker_pool, "snapshot", lambda *a, **kw: next(snapshots))
    sleeps = iter([None, KeyboardInterrupt])

    def sleep(_):
        if next(sleeps) is KeyboardInterrupt:
            raise KeyboardInterrupt

    monkeypatch.setattr(picker_pool.time, "sleep", sleep)
    stream = io.StringIO()
    monkeypatch.setattr("sys.__stdout__", stream)
    assert picker_pool.command(SimpleNamespace(stream=True, subscribe=True, interval=1)) == 0
    frames = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert {"type": "removed", "id": "space"} in frames
    assert frames[-1]["summary"]["auth_status"] == "scope_blocked"


def test_authorize_requires_explicit_activation(monkeypatch, capsys):
    login = Mock(side_effect=AssertionError("must not run"))
    monkeypatch.setattr(authorize, "authorize", login)
    assert authorize.command(argparse.Namespace(user_initiated=False)) == 1
    assert json.loads(capsys.readouterr().out)["type"] == "error"
    login.assert_not_called()


@pytest.mark.parametrize(("actual", "scope", "expected"), [
    ("example-user", "codespace", "success"),
    ("other-user", "codespace", "mismatch"),
    ("example-user", "repo", "scope"),
])
def test_vendor_result_verified_before_adoption(tmp_path, monkeypatch, actual, scope, expected):
    monkeypatch.setattr(picker_auth, "auth_root", lambda: tmp_path / "auth")
    before = dict(os.environ)
    commands = []

    def login(env, progress):
        assert "GH_TOKEN" not in env and "GITHUB_TOKEN" not in env
        directory = picker_auth.Path(env["GH_CONFIG_DIR"])
        (directory / "hosts.yml").write_text("synthetic vendor config", encoding="utf-8")
        progress("Open https://github.com/login/device and enter ABCD-EFGH")

    monkeypatch.setattr(authorize, "_login", login)

    def run(argv, **kw):
        commands.append(argv)
        assert kw["env"]["GH_CONFIG_DIR"] != os.environ.get("GH_CONFIG_DIR")
        return subprocess.CompletedProcess(argv, 0, actual + "\n", "")

    monkeypatch.setattr(authorize.subprocess, "run", run)
    monkeypatch.setattr(picker_auth, "status", lambda *_: (0, status_text(actual, scope)))
    if expected == "success":
        assert authorize.authorize("example-user", lambda _: None) == "example-user"
        assert list(picker_auth.authorized_configs()) == ["example-user"]
        assert picker_auth.credential_env("example-user")["GH_CONFIG_DIR"].endswith("example-user")
    else:
        with pytest.raises(RuntimeError, match="not example-user|codespace scope"):
            authorize.authorize("example-user", lambda _: None)
        assert picker_auth.authorized_configs() == {}
    assert dict(os.environ) == before
    assert all("switch" not in command for command in commands)
    assert not list((tmp_path / "auth").glob(".pending-*"))


@pytest.mark.parametrize("error", [KeyboardInterrupt, RuntimeError("vendor unavailable")])
def test_cancel_and_failure_do_not_adopt(tmp_path, monkeypatch, error):
    monkeypatch.setattr(picker_auth, "auth_root", lambda: tmp_path / "auth")
    monkeypatch.setattr(authorize, "_login", Mock(side_effect=error))
    with pytest.raises(type(error) if isinstance(error, Exception) else KeyboardInterrupt):
        authorize.authorize("example-user", lambda _: None)
    assert picker_auth.authorized_configs() == {}
    assert not list((tmp_path / "auth").glob(".pending-*"))


@pytest.mark.parametrize("cancel", [False, True])
def test_vendor_login_process_boundary_and_cleanup(monkeypatch, cancel):
    proc = Mock()
    proc.stdout = io.StringIO(
        "Copy this code: ABCD-EFGH\nOpen https://github.com/login/device\n"
        "gho_syntheticsecret\n"
    )
    proc.returncode = 0
    proc.poll.return_value = None if cancel else 0
    if cancel:
        proc.stdin.write.side_effect = KeyboardInterrupt
    job = Mock()
    spawn = Mock(return_value=(proc, job))
    monkeypatch.setattr(authorize, "spawn_sync_in_kill_on_close_job", spawn)

    class ImmediateThread:
        def __init__(self, *, target, **_):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(authorize.threading, "Thread", ImmediateThread)
    messages = []
    if cancel:
        with pytest.raises(KeyboardInterrupt):
            authorize._login({"GH_CONFIG_DIR": "synthetic"}, messages.append)
        proc.terminate.assert_called_once()
    else:
        authorize._login({"GH_CONFIG_DIR": "synthetic"}, messages.append)
        assert "ABCD-EFGH" in messages[-1]
        assert "gho_syntheticsecret" not in messages[-1]
    argv = spawn.call_args.args[0]
    assert argv == ["gh", "auth", "login", "--hostname", "github.com",
                    "--git-protocol", "https", "--web", "--scopes", "codespace"]
    assert spawn.call_args.kwargs["require_job"] is True
    assert spawn.call_args.kwargs["allow_breakaway"] is False
    job.close.assert_called_once()


@pytest.mark.parametrize("active", [None, "example-user"])
def test_verified_provider_discovery_does_not_retry_unauthenticated_ambient(
    monkeypatch, active,
):
    from agent_codespaces import account_binding, lifecycle

    monkeypatch.setattr(picker_auth, "authorized_configs", lambda: {"example-user": picker_auth.Path("vendor")})
    monkeypatch.setattr(gh_account, "mapped_accounts", lambda: ())
    monkeypatch.setattr(account_binding, "bound_accounts", lambda: ())
    monkeypatch.setattr(gh_account, "active_account", lambda: active)
    listing = Mock(return_value=[])
    monkeypatch.setattr(lifecycle, "_list_codespaces_under", listing)
    assert lifecycle.list_codespaces() == []
    listing.assert_called_once_with("example-user")
