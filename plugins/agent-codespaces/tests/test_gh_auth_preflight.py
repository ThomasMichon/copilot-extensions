"""Tests for the multi-account gh auth preflight in __main__."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from dropin_registry import ScanAuthority, ScanSnapshot

from agent_codespaces import __main__ as m
from agent_codespaces.auth_preflight import GithubCredentialPreflight
from agent_codespaces.config import ConfigDropinRegistryReport, ConfigProviderReports

_STATUS = """github.com
  x Logged in to github.com account ThomasMichon (keyring)
  - Active account: true
  - Token scopes: 'codespace', 'gist', 'read:org', 'repo', 'workflow'

  x Logged in to github.com account example-operator (keyring)
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
    assert "codespace" in parsed["ThomasMichon"]
    assert "codespace" not in parsed["example-operator"]


def test_preflight_flags_mapped_account_missing_codespace_scope():
    with patch("subprocess.run") as run, \
         patch("agent_codespaces.auth_preflight.codespace_scope_accounts",
               return_value=("ThomasMichon", "example-operator")):
        run.return_value = MagicMock(returncode=0, stdout=_STATUS, stderr="")
        msgs = m._gh_auth_preflight()
    joined = "\n".join(msgs)
    assert "example-operator" in joined and "codespace" in joined
    # The account that HAS the scope must not be flagged.
    assert "ThomasMichon" not in joined


def test_preflight_flags_missing_mapped_account():
    with patch("subprocess.run") as run, \
         patch("agent_codespaces.auth_preflight.codespace_scope_accounts",
               return_value=("ghost",)), \
         patch("agent_codespaces.__main__._account_login_remedy",
               return_value="run: gh auth login"):
        run.return_value = MagicMock(returncode=0, stdout=_STATUS, stderr="")
        msgs = m._gh_auth_preflight()
    assert any("ghost" in msg and "not logged in" in msg for msg in msgs)


def test_preflight_clean_when_all_scoped():
    status = _STATUS.replace(
        "  - Token scopes: 'gist', 'read:org', 'repo', 'workflow'\n",
        "  - Token scopes: 'codespace', 'gist', 'repo'\n",
    )
    with patch("subprocess.run") as run, \
         patch("agent_codespaces.auth_preflight.codespace_scope_accounts",
               return_value=("ThomasMichon", "example-operator")):
        run.return_value = MagicMock(returncode=0, stdout=status, stderr="")
        msgs = m._gh_auth_preflight()
    assert msgs == []


def test_preflight_ignores_mapped_non_codespace_account_for_other_owner():
    status = """github.com
  x Logged in to github.com account nakanaki_microsoft (keyring)
  - Active account: true
  - Token scopes: 'gist', 'repo'

  x Logged in to github.com account namankanakiya (keyring)
  - Active account: false
  - Token scopes: 'gist', 'repo'
"""
    with patch("subprocess.run") as run, \
         patch("agent_codespaces.account_binding.bound_accounts", return_value=()), \
         patch("agent_codespaces.config.load_merged_config") as load_cfg, \
         patch("agent_codespaces.gh_account.account_for_repo") as account_for_repo:
        load_cfg.return_value = MagicMock(
            repos={"odsp-microsoft/example-codespaces": object()},
        )
        account_for_repo.side_effect = lambda repo: {
            "ThomasMichon/copilot-extensions": "namankanakiya",
            "odsp-microsoft/example-codespaces": "nakanaki_microsoft",
        }.get(repo)
        run.return_value = MagicMock(returncode=0, stdout=status, stderr="")
        msgs = m._gh_auth_preflight()
    joined = "\n".join(msgs)
    assert "nakanaki_microsoft" in joined and "codespace" in joined
    assert "namankanakiya" not in joined


def test_credential_account_for_ambient_codespace_uses_active_gh_account(monkeypatch):
    from agent_codespaces import gh_account

    monkeypatch.setattr(
        "agent_codespaces.lifecycle.account_for_codespace",
        lambda name: None,
    )
    monkeypatch.setattr(gh_account, "active_account", lambda **_kw: "active-user")

    assert gh_account.credential_account_for_codespace("ambient-cs") == "active-user"


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


def test_fast_credential_account_uses_binding_without_active_probe(monkeypatch):
    from agent_codespaces import gh_account

    monkeypatch.setattr(
        "agent_codespaces.account_binding.bound_account",
        lambda name: "bound-user",
    )
    monkeypatch.setattr(
        gh_account,
        "active_account",
        lambda **_kw: (_ for _ in ()).throw(AssertionError("must not call active")),
    )

    assert gh_account.fast_credential_account_for_codespace("cs-1") == "bound-user"


def test_fast_credential_account_falls_back_to_active(monkeypatch):
    from agent_codespaces import gh_account

    monkeypatch.setattr(
        "agent_codespaces.account_binding.bound_account",
        lambda name: None,
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
