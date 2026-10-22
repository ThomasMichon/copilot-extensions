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

from agent_codespaces import authorize, credential_store, gh_account, oauth_device, picker_auth, picker_pool, pool


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
    (1, status_text(scope="codespace") + "API verification failed", "error"),
])
def test_classify(rc, text, expected):
    assert picker_auth.classify(rc, text, "example-user")["status"] == expected


def test_relevant_accounts_ignore_unrelated_stored_failure(monkeypatch):
    from agent_codespaces import account_binding, config

    monkeypatch.setattr(account_binding, "bound_accounts_or_raise", lambda: ())
    monkeypatch.setattr(config, "load_merged_config", lambda **_: SimpleNamespace(repos={"example/repo": {}}))
    monkeypatch.setattr(gh_account, "account_for_repo", lambda _: "example-user")
    monkeypatch.setattr(gh_account, "active_account", lambda: "example-user")
    monkeypatch.setattr(gh_account, "mapped_accounts", lambda: ())
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
    monkeypatch.setattr(picker_auth, "status", lambda *_: (0, status_text(scope="codespace")))
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
    # The vendor CLI's global active slot and named slots are deliberately
    # different from the new credential. Neither login nor token retrieval
    # may touch those slots when adopting/using provider credentials.
    keyring = {("gh:github.com", ""): "account-a-original",
               ("gh:github.com", "account-a"): "account-a-original",
               ("gh:github.com", "example-user"): "account-b-old"}
    original_keyring = dict(keyring)
    monkeypatch.setattr(credential_store.keyring, "set_password",
                        lambda service, user, value: keyring.__setitem__((service, user), value))
    monkeypatch.setattr(credential_store.keyring, "get_password",
                        lambda service, user: keyring.get((service, user)))
    vendor_calls = Mock(side_effect=AssertionError("shared keyring must not be used"))
    monkeypatch.setattr(authorize.subprocess, "run", vendor_calls)
    monkeypatch.setattr(oauth_device, "acquire", lambda progress: "synthetic-new-credential")
    monkeypatch.setattr(oauth_device, "_request", lambda *args, **kwargs: (
        {"login": actual}, {"X-OAuth-Scopes": scope},
    ))
    if expected == "success":
        assert authorize.authorize("example-user", lambda _: None) == "example-user"
        assert list(picker_auth.authorized_configs()) == ["example-user"]
        assert picker_auth.credential_env("example-user")["GH_CONFIG_DIR"].endswith("example-user")
        gh_account.clear_caches()
        monkeypatch.setattr(gh_account.shutil, "which", lambda _: "gh")
        assert gh_account.token_for_account("example-user") == "synthetic-new-credential"
        assert not (tmp_path / "auth/example-user/hosts.yml").exists()
        from agent_codespaces import account_binding, config

        monkeypatch.setattr(gh_account, "mapped_accounts", lambda: ())
        monkeypatch.setattr(gh_account, "active_account", lambda: None)
        monkeypatch.setattr(account_binding, "bound_accounts_or_raise", lambda: ())
        monkeypatch.setattr(config, "load_merged_config", lambda **_: SimpleNamespace(repos={}))
        global_probe = Mock(side_effect=subprocess.TimeoutExpired("unrelated global probe", 15))
        monkeypatch.setattr(picker_auth, "status", global_probe)
        assert picker_auth.probe()["status"] == "ready"
        global_probe.assert_not_called()
    else:
        with pytest.raises(RuntimeError, match="not example-user|codespace scope"):
            authorize.authorize("example-user", lambda _: None)
        assert picker_auth.authorized_configs() == {}
    assert dict(os.environ) == before
    assert all(keyring[key] == value for key, value in original_keyring.items())
    vendor_calls.assert_not_called()


@pytest.mark.parametrize("error", [KeyboardInterrupt, RuntimeError("vendor unavailable")])
def test_cancel_and_failure_do_not_adopt(tmp_path, monkeypatch, error):
    monkeypatch.setattr(picker_auth, "auth_root", lambda: tmp_path / "auth")
    monkeypatch.setattr(oauth_device, "acquire", Mock(side_effect=error))
    with pytest.raises(type(error) if isinstance(error, Exception) else KeyboardInterrupt):
        authorize.authorize("example-user", lambda _: None)
    assert picker_auth.authorized_configs() == {}
    assert not list((tmp_path / "auth").glob(".pending-*"))


@pytest.mark.parametrize("active", [None, "example-user"])
def test_verified_provider_discovery_does_not_retry_unauthenticated_ambient(
    monkeypatch, active,
):
    from agent_codespaces import account_binding, config, lifecycle

    monkeypatch.setattr(picker_auth, "authorized_configs", lambda: {"example-user": picker_auth.Path("vendor")})
    monkeypatch.setattr(gh_account, "mapped_accounts", lambda: ())
    monkeypatch.setattr(account_binding, "bound_accounts_or_raise", lambda: ())
    monkeypatch.setattr(config, "load_merged_config", lambda **_: SimpleNamespace(repos={}))
    monkeypatch.setattr(gh_account, "active_account", lambda: active)
    listing = Mock(return_value=[])
    monkeypatch.setattr(lifecycle, "_list_codespaces_under", listing)
    assert lifecycle.list_codespaces() == []
    listing.assert_called_once_with("example-user")


def test_context_admission_preserves_handler_signature(monkeypatch):
    from agent_codespaces import __main__ as cli

    admitted = Mock()
    monkeypatch.setattr(cli, "validate_context", admitted)
    args = argparse.Namespace(name="example-space")
    calls = []

    @cli._context_admitted
    def handler(namespace):
        calls.append(namespace)
        return 42

    assert handler(args) == 42
    assert calls == [args]
    admitted.assert_called_once()


def test_pool_alone_receives_annotation_callback(monkeypatch):
    from agent_codespaces import __main__ as cli

    command = Mock(return_value=0)
    monkeypatch.setattr(picker_pool, "command", command)
    args = argparse.Namespace(picker_json=True)
    assert cli._cmd_pool(args) == 0
    command.assert_called_once_with(args, annotate=cli._annotate_pool_members)


def test_inventory_timeout_publishes_error_then_recovers_empty_subscription(monkeypatch):
    auth = {"status": "ready", "account": "example-user", "message": ""}
    monkeypatch.setattr(picker_auth, "probe", lambda: auth)
    monkeypatch.setattr(pool, "build_pool", Mock(side_effect=[
        subprocess.TimeoutExpired("gh codespace list", 30), ([], object()),
    ]))
    monkeypatch.setattr(pool, "picker_payload", lambda *_: {"entries": [], "summary": {}})
    pauses = iter([None, KeyboardInterrupt])

    def sleep(_):
        if next(pauses) is KeyboardInterrupt:
            raise KeyboardInterrupt

    monkeypatch.setattr(picker_pool.time, "sleep", sleep)
    stream = io.StringIO()
    monkeypatch.setattr("sys.__stdout__", stream)
    args = SimpleNamespace(budget=None, stale_after=None, stream=True, subscribe=True, interval=1)
    assert picker_pool.command(args) == 0
    summaries = [
        json.loads(line)["summary"] for line in stream.getvalue().splitlines()
        if json.loads(line)["type"] == "summary"
    ]
    assert [summary["auth_status"] for summary in summaries] == ["error", "ready"]
    assert "TimeoutExpired" in summaries[0]["banner_text"]


@pytest.mark.parametrize("active_rows", [[], [{"id": "active-space"}]])
def test_mapped_only_account_scope_blocks_before_inventory(monkeypatch, active_rows):
    from agent_codespaces import account_binding, config

    monkeypatch.setattr(gh_account, "mapped_accounts", lambda: ("mapped-user",))
    monkeypatch.setattr(gh_account, "active_account", lambda: "example-user")
    monkeypatch.setattr(account_binding, "bound_accounts_or_raise", lambda: ())
    monkeypatch.setattr(config, "load_merged_config", lambda **_: SimpleNamespace(repos={}))
    monkeypatch.setattr(picker_auth, "authorized_configs", lambda: {})
    monkeypatch.setattr(picker_auth, "status", lambda: (
        0, status_text(scope="codespace") + status_text("mapped-user", "repo"),
    ))
    inventory = Mock(return_value=(active_rows, object()))
    monkeypatch.setattr(pool, "build_pool", inventory)
    payload = picker_pool.snapshot(SimpleNamespace(budget=None, stale_after=None))
    assert payload["summary"]["auth_status"] == "scope_blocked"
    assert payload["summary"]["auth_account"] == "mapped-user"
    inventory.assert_not_called()


def test_posix_keyring_failure_uses_private_file(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("POSIX private-file fallback")
    path = tmp_path / "account"
    path.mkdir(mode=0o700)
    monkeypatch.setattr(credential_store.keyring, "set_password", Mock(
        side_effect=credential_store.keyring.errors.NoKeyringError("unavailable"),
    ))
    credential_store.save(path, "example-user", "synthetic-credential")
    assert (path / "store-kind").read_text() == "file"
    assert (path / "hosts.yml").stat().st_mode & 0o777 == 0o600
    assert credential_store.load(path, "example-user") == "synthetic-credential"


def test_missing_owned_keyring_credential_never_uses_global_keyring(tmp_path, monkeypatch):
    path = tmp_path / "account"
    path.mkdir()
    (path / "store-kind").write_text("keyring", encoding="utf-8")
    get = Mock(return_value=None)
    monkeypatch.setattr(credential_store.keyring, "get_password", get)
    with pytest.raises(oauth_device.CredentialUnavailable):
        credential_store.load(path, "example-user")
    assert get.call_args.args[0].startswith("agent-codespaces:")


def test_unknown_ambient_identity_is_selection_error_not_missing_auth(monkeypatch):
    from agent_codespaces import account_binding, config

    monkeypatch.setattr(gh_account, "mapped_accounts", lambda: ())
    monkeypatch.setattr(gh_account, "active_account", lambda: None)
    monkeypatch.setattr(account_binding, "bound_accounts_or_raise", lambda: ())
    monkeypatch.setattr(config, "load_merged_config", lambda **_: SimpleNamespace(repos={}))
    monkeypatch.setattr(picker_auth, "authorized_configs", lambda: {})
    monkeypatch.setattr(picker_auth, "status", lambda: (0, status_text(scope="codespace")))
    result = picker_auth.probe()
    assert result["status"] == "error"
    assert "selection" in result["message"]
    assert picker_auth.classify(
        0, status_text(scope="codespace") + "  - Active account: true\n", "",
    )["account"] == "example-user"


def test_held_subscription_reads_rotated_provider_credential(monkeypatch):
    current = ["old-credential"]
    used = []
    monkeypatch.setattr(picker_auth, "credential_token", lambda _: current[0])
    monkeypatch.setattr(picker_auth, "probe", lambda: {
        "status": "ready", "account": "example-user", "message": "",
    })

    def build(**_):
        used.append(gh_account.token_for_account("example-user"))
        return [], object()

    monkeypatch.setattr(pool, "build_pool", build)
    monkeypatch.setattr(pool, "picker_payload", lambda *_: {"entries": [], "summary": {}})
    ticks = iter([None, KeyboardInterrupt])

    def sleep(_):
        if next(ticks) is KeyboardInterrupt:
            raise KeyboardInterrupt
        current[0] = "new-credential"

    monkeypatch.setattr(picker_pool.time, "sleep", sleep)
    monkeypatch.setattr("sys.__stdout__", io.StringIO())
    args = SimpleNamespace(budget=None, stale_after=None, stream=True, subscribe=True, interval=1)
    assert picker_pool.command(args) == 0
    assert used == ["old-credential", "new-credential"]
