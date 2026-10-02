"""Tests for the multi-account gh auth preflight in __main__."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from dropin_registry import ScanAuthority, ScanSnapshot

from agent_codespaces import __main__ as m
from agent_codespaces.auth_preflight import GithubCredentialPreflight
from agent_codespaces.config import ConfigDropinRegistryReport, ConfigProviderReports

_STATUS = """github.com
  x Logged in to github.com account alice (keyring)
  - Active account: true
  - Token scopes: 'codespace', 'gist', 'read:org', 'repo', 'workflow'

  x Logged in to github.com account bob (keyring)
  - Active account: false
  - Token scopes: 'gist', 'read:org', 'repo', 'workflow'
"""


def _clean_config_d_report() -> ConfigDropinRegistryReport:
    return ConfigDropinRegistryReport(
        snapshot=ScanSnapshot(
            registry="config.d",
            authority=ScanAuthority.COMPLETE,
        ),
        active_entries={},
    )


def _clean_provider_reports() -> ConfigProviderReports:
    return ConfigProviderReports(
        active_plugins=ConfigDropinRegistryReport(
            snapshot=ScanSnapshot(
                registry="plugin-manifests",
                authority=ScanAuthority.COMPLETE,
            ),
            active_entries={},
        ),
        config_d=_clean_config_d_report(),
    )


def test_parse_gh_account_scopes():
    parsed = m._parse_gh_account_scopes(_STATUS)
    assert "codespace" in parsed["alice"]
    assert "codespace" not in parsed["bob"]


def test_preflight_flags_mapped_account_missing_codespace_scope():
    with patch("subprocess.run") as run, \
         patch("agent_codespaces.auth_preflight.codespace_scope_accounts",
               return_value=(("alice", "bob"), False)):
        run.return_value = MagicMock(returncode=0, stdout=_STATUS, stderr="")
        msgs = m._gh_auth_preflight()
    joined = "\n".join(msgs)
    assert "bob" in joined and "codespace" in joined
    # The account that HAS the scope must not be flagged.
    assert "alice" not in joined


def test_preflight_flags_missing_mapped_account():
    with patch("subprocess.run") as run, \
         patch("agent_codespaces.auth_preflight.codespace_scope_accounts",
               return_value=(("ghost",), False)), \
         patch("agent_codespaces.__main__._account_login_remedy",
               return_value="run: gh auth login"):
        run.return_value = MagicMock(returncode=0, stdout=_STATUS, stderr="")
        msgs = m._gh_auth_preflight()
    assert any("ghost" in msg and "not logged in" in msg for msg in msgs)


def test_preflight_ignores_accounts_bound_only_to_deleted_codespaces():
    from agent_codespaces.account_binding import AccountBinding

    bindings = [AccountBinding(codespace="cs-gone", account="stale", bound_at=0.0)]
    with patch("subprocess.run") as run, \
         patch("agent_codespaces.account_binding.list_bindings", return_value=bindings), \
         patch("agent_codespaces.lifecycle.list_codespaces", return_value=[]), \
         patch("agent_codespaces.config.load_merged_config", return_value=MagicMock(repos={})):
        run.return_value = MagicMock(returncode=0, stdout=_STATUS, stderr="")
        msgs = m._gh_auth_preflight()
    assert not any("stale" in msg for msg in msgs)


def test_preflight_clean_when_all_scoped():
    status = _STATUS.replace(
        "  - Token scopes: 'gist', 'read:org', 'repo', 'workflow'\n",
        "  - Token scopes: 'codespace', 'gist', 'repo'\n",
    )
    with patch("subprocess.run") as run, \
         patch("agent_codespaces.auth_preflight.codespace_scope_accounts",
               return_value=(("alice", "bob"), False)):
        run.return_value = MagicMock(returncode=0, stdout=status, stderr="")
        msgs = m._gh_auth_preflight()
    assert msgs == []


def test_preflight_ignores_mapped_non_codespace_account_for_other_owner():
    status = """github.com
  x Logged in to github.com account alice (keyring)
  - Active account: true
  - Token scopes: 'gist', 'repo'

  x Logged in to github.com account bob (keyring)
  - Active account: false
  - Token scopes: 'gist', 'repo'
"""
    with patch("subprocess.run") as run, \
         patch("agent_codespaces.account_binding.list_bindings", return_value=[]), \
         patch("agent_codespaces.config.load_merged_config") as load_cfg, \
         patch("agent_codespaces.gh_account.account_for_repo") as account_for_repo:
        load_cfg.return_value = MagicMock(
            repos={"example-org/example-codespaces": object()},
        )
        account_for_repo.side_effect = lambda repo: {
            "example-tools/example-extension": "bob",
            "example-org/example-codespaces": "alice",
        }.get(repo)
        run.return_value = MagicMock(returncode=0, stdout=status, stderr="")
        msgs = m._gh_auth_preflight()
    joined = "\n".join(msgs)
    assert "alice" in joined and "codespace" in joined
    assert "bob" not in joined


def test_preflight_checks_active_account_when_no_codespace_accounts():
    status = """github.com
  x Logged in to github.com account alice (keyring)
  - Active account: true
  - Token scopes: 'gist', 'repo'

  x Logged in to github.com account bob (keyring)
  - Active account: false
  - Token scopes: 'codespace', 'gist', 'repo'
"""
    with patch("subprocess.run") as run, \
         patch("agent_codespaces.auth_preflight.codespace_scope_accounts",
               return_value=((), False)), \
         patch("agent_codespaces.gh_account.active_account",
               return_value="alice"):
        run.return_value = MagicMock(returncode=0, stdout=status, stderr="")
        msgs = m._gh_auth_preflight()
    joined = "\n".join(msgs)
    assert "alice" in joined and "codespace" in joined
    assert "bob" not in joined


def test_preflight_checks_active_account_when_config_mix_includes_ambient():
    status = """github.com
  x Logged in to github.com account alice (keyring)
  - Active account: true
  - Token scopes: 'gist', 'repo'

  x Logged in to github.com account bob (keyring)
  - Active account: false
  - Token scopes: 'codespace', 'gist', 'repo'
"""
    with patch("subprocess.run") as run, \
         patch("agent_codespaces.auth_preflight.codespace_scope_accounts",
               return_value=(("bob",), True)), \
         patch("agent_codespaces.gh_account.active_account",
               return_value="alice"):
        run.return_value = MagicMock(returncode=0, stdout=status, stderr="")
        msgs = m._gh_auth_preflight()
    joined = "\n".join(msgs)
    assert "alice" in joined and "codespace" in joined
    assert "bob" not in joined


def test_credential_account_for_ambient_codespace_uses_active_gh_account(monkeypatch):
    from agent_codespaces import gh_account

    monkeypatch.setattr(
        "agent_codespaces.account_binding.bound_account_or_raise", lambda name: None,
    )
    monkeypatch.setattr(
        "agent_codespaces.lifecycle.account_for_codespace",
        lambda name: None,
    )
    monkeypatch.setattr(gh_account, "active_account", lambda **_kw: "active-user")

    assert gh_account.credential_account_for_codespace("ambient-cs") == "active-user"


def test_credential_account_never_guesses_when_the_binding_is_unreadable(monkeypatch):
    from agent_codespaces import account_binding, gh_account

    def contended(_name):
        raise RuntimeError("Could not acquire account binding lock (held by another process)")

    def must_not_call(*_a, **_kw):
        raise AssertionError("must not fall back to the resolver or ambient account")

    monkeypatch.setattr(account_binding, "bound_account_or_raise", contended)
    monkeypatch.setattr("agent_codespaces.lifecycle.account_for_codespace", must_not_call)
    monkeypatch.setattr(gh_account, "active_account", must_not_call)
    assert gh_account.credential_account_for_codespace("cs-a") is None


def test_credential_account_prefers_the_binding(monkeypatch):
    from agent_codespaces import gh_account

    def must_not_call(*_a, **_kw):
        raise AssertionError("must not probe when a binding exists")

    monkeypatch.setattr(
        "agent_codespaces.account_binding.bound_account_or_raise", lambda name: "bound-user",
    )
    monkeypatch.setattr("agent_codespaces.lifecycle.account_for_codespace", must_not_call)
    monkeypatch.setattr(gh_account, "active_account", must_not_call)
    assert gh_account.credential_account_for_codespace("cs-1") == "bound-user"


def test_active_account_reads_gh_json(monkeypatch):
    from agent_codespaces import gh_account

    payload = json.dumps({
        "hosts": {"github.com": [
            {"state": "success", "active": False, "login": "secondary"},
            {"state": "success", "active": True, "login": "active-user"},
        ]},
    })
    with patch("subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout=payload, stderr="")
        assert gh_account.active_account() == "active-user"
    assert "--active" in run.call_args.args[0]


def test_active_account_survives_a_failed_api_check(monkeypatch):
    from agent_codespaces import gh_account

    for state in ("timeout", "error"):
        payload = json.dumps({"hosts": {"github.com": [
            {"state": state, "active": True, "login": "active-user"}]}})
        with patch("subprocess.run") as run:
            run.return_value = MagicMock(returncode=1, stdout=payload, stderr="")
            assert gh_account.active_account() == "active-user"


def test_preflight_ignores_an_unrelated_account_that_failed_to_log_in():
    status = _STATUS + """
  X Failed to log in to github.com account carol (keyring)
  - The token in keyring is invalid.
"""
    with patch("subprocess.run") as run, \
         patch("agent_codespaces.auth_preflight.codespace_scope_accounts",
               return_value=(("alice",), False)):
        run.return_value = MagicMock(returncode=1, stdout=status, stderr="")
        assert m._gh_auth_preflight() == []


def test_preflight_reports_a_codespace_account_that_failed_to_log_in():
    status = _STATUS + """
  X Failed to log in to github.com account carol (keyring)
  - The token in keyring is invalid.
"""
    with patch("subprocess.run") as run, \
         patch("agent_codespaces.auth_preflight.codespace_scope_accounts",
               return_value=(("carol",), False)), \
         patch("agent_codespaces.__main__._account_login_remedy",
               return_value="run: gh auth login"):
        run.return_value = MagicMock(returncode=1, stdout=status, stderr="")
        msgs = m._gh_auth_preflight()
    assert any("carol" in msg and "not logged in" in msg for msg in msgs)


def test_preflight_unauthenticated_when_no_account_parses():
    with patch("subprocess.run") as run:
        run.return_value = MagicMock(returncode=1, stdout="",
                                     stderr="You are not logged into any GitHub hosts.")
        assert m._gh_auth_preflight() == ["gh is not authenticated -- run: gh auth login"]


def test_preflight_reports_a_status_timeout_as_a_timeout_not_a_missing_scope():
    status = _STATUS + """
  X Timeout trying to log in to github.com account carol (keyring)
"""
    with patch("subprocess.run") as run, \
         patch("agent_codespaces.auth_preflight.codespace_scope_accounts",
               return_value=(("carol",), False)):
        run.return_value = MagicMock(returncode=1, stdout=status, stderr="")
        msgs = m._gh_auth_preflight()
    assert len(msgs) == 1 and "timed out" in msgs[0] and "carol" in msgs[0]
    assert "scope" not in msgs[0]


def test_a_failed_active_account_is_reported_as_not_logged_in():
    status = _STATUS.replace("x Logged in to github.com account alice", "X Failed to log in to github.com account alice")
    with patch("subprocess.run") as run, \
         patch("agent_codespaces.auth_preflight.codespace_scope_accounts", return_value=((), False)), \
         patch("agent_codespaces.gh_account.active_account", return_value="alice"), \
         patch("agent_codespaces.__main__._account_login_remedy", return_value="run: gh auth login"):
        run.return_value = MagicMock(returncode=1, stdout=status, stderr="")
        msgs = m._gh_auth_preflight()
    assert msgs == ["active gh account 'alice' is not logged in -- run: gh auth login"]


def test_fast_credential_account_never_guesses_when_the_binding_is_unreadable(monkeypatch):
    from agent_codespaces import account_binding, gh_account

    def contended(_name, **_kw):
        raise RuntimeError("Could not acquire account binding lock (held by another process)")

    monkeypatch.setattr(account_binding, "bound_account_or_raise", contended)
    monkeypatch.setattr(gh_account, "active_account",
                        lambda **_kw: (_ for _ in ()).throw(AssertionError("must not guess ambient")))
    assert gh_account.fast_credential_account_for_codespace("cs-a") is None


def test_github_credential_preflight_honors_its_timeout():
    import asyncio
    import time

    from agent_codespaces.auth_preflight import github_credential_preflight

    class Hung:
        name = "git-credential"

        async def resolve(self, *_a, **_k):
            await asyncio.sleep(30)

    started = time.monotonic()
    result = asyncio.run(github_credential_preflight(
        "alice", git_source=Hung(), gcm_accounts=[], timeout=0.3))
    assert not result.ok
    assert time.monotonic() - started < 5


def test_fast_credential_account_uses_binding_without_active_probe(monkeypatch):
    from agent_codespaces import gh_account

    monkeypatch.setattr(
        "agent_codespaces.account_binding.bound_account_or_raise",
        lambda name, **_kw: "bound-user",
    )
    monkeypatch.setattr(
        gh_account,
        "active_account",
        lambda **_kw: (_ for _ in ()).throw(AssertionError("must not call active")),
    )

    assert gh_account.fast_credential_account_for_codespace("cs-1") == "bound-user"


def test_fast_credential_account_bounds_the_binding_lock_wait(monkeypatch, tmp_path):
    import time

    from agent_codespaces import account_binding, gh_account

    lock = tmp_path / "account-bindings.lock"
    lock.write_text("", encoding="utf-8")  # a live holder (fresh, not stale)
    monkeypatch.setattr(account_binding, "_LOCK_FILE", lock)
    monkeypatch.setattr(account_binding, "ensure_runtime_dir", lambda: None)
    started = time.monotonic()
    assert gh_account.fast_credential_account_for_codespace("cs-1", timeout=0.3) is None
    assert time.monotonic() - started < 3
    assert lock.exists()  # a short deadline never steals a live holder's lock


def test_fast_credential_account_falls_back_to_active(monkeypatch):
    from agent_codespaces import gh_account

    monkeypatch.setattr(
        "agent_codespaces.account_binding.bound_account_or_raise",
        lambda name, **_kw: None,
    )
    monkeypatch.setattr(gh_account, "active_account", lambda **_kw: "active-user")

    assert gh_account.fast_credential_account_for_codespace("cs-1") == "active-user"


# --- _ambient_codespace_scope (focused ambient gate check, #980) ---------

def test_ambient_scope_ok_when_present():
    with patch("subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout=_STATUS, stderr="")
        ok, remedy = m._ambient_codespace_scope()
    assert ok is True and remedy == ""


def test_ambient_scope_missing_when_absent():
    status = _STATUS.replace("'codespace', ", "")  # strip the scope everywhere
    with patch("subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout=status, stderr="")
        ok, remedy = m._ambient_codespace_scope()
    assert ok is False and "gh auth refresh" in remedy


def test_ambient_scope_unauthenticated():
    with patch("subprocess.run") as run:
        run.return_value = MagicMock(returncode=1, stdout="", stderr="not logged in")
        ok, remedy = m._ambient_codespace_scope()
    assert ok is False and "gh auth login" in remedy


def test_ambient_scope_degrades_to_ok_when_gh_unrunnable():
    """A gh that can't be run (FileNotFound/timeout) must NOT block an op."""
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        ok, remedy = m._ambient_codespace_scope()
    assert ok is True and remedy == ""


# --- _require_codespace_scope gate + doctor (#980) ------------------------

def test_require_scope_proceeds_when_ok():
    with patch.object(m, "_ambient_codespace_scope", return_value=(True, "")):
        assert m._require_codespace_scope("create") is None


def test_require_scope_blocks_when_missing(capsys):
    with patch.object(m, "_ambient_codespace_scope",
                      return_value=(False, "run: gh auth refresh -s codespace")):
        rc = m._require_codespace_scope("create a CodeSpace")
    assert rc == 3
    err = capsys.readouterr().err
    assert "Refusing to create a CodeSpace" in err and "gh auth refresh" in err


def test_require_scope_escape_hatch(monkeypatch):
    monkeypatch.setenv("AGENT_CODESPACES_SKIP_SCOPE_CHECK", "1")
    with patch.object(m, "_ambient_codespace_scope", return_value=(False, "x")):
        assert m._require_codespace_scope("create") is None


def test_doctor_exit_zero_when_clean(capsys):
    async def _ok(_account=None):
        return GithubCredentialPreflight(ok=True, source="git-credential")

    with patch.object(m, "_gh_auth_preflight", return_value=[]), \
         patch.object(
             m, "scan_config_providers", return_value=_clean_provider_reports()
         ), patch(
             "agent_codespaces.auth_preflight.github_credential_preflight",
             _ok,
         ):
        assert m._cmd_doctor() == 0
    assert "[OK]" in capsys.readouterr().out


def test_doctor_exit_nonzero_on_issues(capsys):
    async def _ok(_account=None):
        return GithubCredentialPreflight(ok=True, source="git-credential")

    with patch.object(
        m, "_gh_auth_preflight",
        return_value=["gh token is missing the 'codespace' scope"],
    ), patch.object(
        m, "scan_config_providers", return_value=_clean_provider_reports()
    ), patch(
        "agent_codespaces.auth_preflight.github_credential_preflight",
        _ok,
    ):
        assert m._cmd_doctor() == 1
    assert "codespace" in capsys.readouterr().err


def test_doctor_reports_github_credential_issue(capsys):
    async def _fail(_account=None):
        return GithubCredentialPreflight(
            ok=False,
            reason_code="github-credential-unavailable",
            detail="no github credential",
            remedy="sign in",
        )

    with patch.object(m, "_gh_auth_preflight", return_value=[]), \
         patch.object(
             m, "scan_config_providers", return_value=_clean_provider_reports()
         ), patch(
             "agent_codespaces.auth_preflight.github_credential_preflight",
             _fail,
         ):
        assert m._cmd_doctor() == 1
    err = capsys.readouterr().err
    assert "github-credential-unavailable" in err
    assert "sign in" in err


def test_doctor_checks_every_serving_account_not_only_the_active_one(capsys):
    seen = []

    async def _per_account(account=None):
        seen.append(account)
        if account == "bob":
            return GithubCredentialPreflight(
                ok=False, reason_code="github-credential-unavailable",
                detail="no GCM entry for bob", remedy="sign in as bob", account="bob",
            )
        return GithubCredentialPreflight(ok=True, source="git-credential", account=account)

    with patch.object(m, "_gh_auth_preflight", return_value=[]), \
         patch.object(m, "scan_config_providers", return_value=_clean_provider_reports()), \
         patch("agent_codespaces.auth_preflight.codespace_scope_accounts",
               return_value=(("bob",), False)), \
         patch("agent_codespaces.gh_account.active_account", return_value="alice"), \
         patch("agent_codespaces.auth_preflight.github_credential_preflight", _per_account):
        assert m._cmd_doctor() == 1
    assert seen == ["bob"]  # ambient alice is not a serving account here
    assert "no GCM entry for bob" in capsys.readouterr().err


def test_doctor_adds_the_active_account_when_ambient_ownership_is_in_use(capsys):
    seen = []

    async def _ok(account=None):
        seen.append(account)
        return GithubCredentialPreflight(ok=True, source="git-credential", account=account)

    with patch.object(m, "_gh_auth_preflight", return_value=[]), \
         patch.object(m, "scan_config_providers", return_value=_clean_provider_reports()), \
         patch("agent_codespaces.auth_preflight.codespace_scope_accounts",
               return_value=(("bob",), True)), \
         patch("agent_codespaces.gh_account.active_account", return_value="alice"), \
         patch("agent_codespaces.auth_preflight.github_credential_preflight", _ok):
        assert m._cmd_doctor() == 0
    assert seen == ["bob", "alice"]
    out = capsys.readouterr().out
    assert "for 'bob'" in out and "for 'alice'" in out


def test_serving_accounts_skip_bindings_for_deleted_codespaces():
    from agent_codespaces import auth_preflight as ap
    from agent_codespaces.account_binding import AccountBinding

    bindings = [
        AccountBinding(codespace="cs-live", account="alice", bound_at=0.0),
        AccountBinding(codespace="cs-gone", account="stale", bound_at=0.0),
    ]
    live = [SimpleNamespace(name="cs-live")]
    with patch("agent_codespaces.account_binding.list_bindings", return_value=bindings), \
         patch("agent_codespaces.lifecycle.list_codespaces", return_value=live), \
         patch("agent_codespaces.config.load_merged_config",
               return_value=MagicMock(repos={})):
        assert ap.codespace_scope_accounts(live_only=True) == (("alice",), False)
        assert ap.codespace_scope_accounts() == (("alice", "stale"), False)
    with patch("agent_codespaces.account_binding.list_bindings", return_value=bindings), \
         patch("agent_codespaces.lifecycle.list_codespaces", side_effect=RuntimeError("offline")), \
         patch("agent_codespaces.config.load_merged_config",
               return_value=MagicMock(repos={})):
        assert ap.codespace_scope_accounts(live_only=True) == (("alice", "stale"), False)


def test_serving_accounts_include_ambient_and_mapped_owners_of_live_codespaces():
    from agent_codespaces import auth_preflight as ap
    from agent_codespaces.account_binding import AccountBinding

    bindings = [AccountBinding(codespace="cs-bound", account="alice", bound_at=0.0)]
    live = [SimpleNamespace(name="cs-bound", account="alice"),
            SimpleNamespace(name="cs-ambient", account=""),
            SimpleNamespace(name="cs-mapped", account="carol")]
    with patch("agent_codespaces.account_binding.list_bindings", return_value=bindings), \
         patch("agent_codespaces.lifecycle.list_codespaces", return_value=live), \
         patch("agent_codespaces.config.load_merged_config",
               return_value=MagicMock(repos={})):
        assert ap.codespace_scope_accounts(live_only=True) == (("alice", "carol"), True)

