"""Tests for the multi-account gh auth preflight in __main__."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from dropin_registry import ScanAuthority, ScanSnapshot

from agent_codespaces import __main__ as m
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
         patch("agent_codespaces.gh_account.mapped_accounts",
               return_value=("ThomasMichon", "example-operator")):
        run.return_value = MagicMock(returncode=0, stdout=_STATUS, stderr="")
        msgs = m._gh_auth_preflight()
    joined = "\n".join(msgs)
    assert "example-operator" in joined and "codespace" in joined
    # The account that HAS the scope must not be flagged.
    assert "ThomasMichon" not in joined


def test_preflight_flags_missing_mapped_account():
    with patch("subprocess.run") as run, \
         patch("agent_codespaces.gh_account.mapped_accounts",
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
         patch("agent_codespaces.gh_account.mapped_accounts",
               return_value=("ThomasMichon", "example-operator")):
        run.return_value = MagicMock(returncode=0, stdout=status, stderr="")
        msgs = m._gh_auth_preflight()
    assert msgs == []


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
    with patch.object(m, "_gh_auth_preflight", return_value=[]), \
         patch.object(
             m, "scan_config_providers", return_value=_clean_provider_reports()
         ):
        assert m._cmd_doctor() == 0
    assert "[OK]" in capsys.readouterr().out


def test_doctor_exit_nonzero_on_issues(capsys):
    with patch.object(
        m, "_gh_auth_preflight",
        return_value=["gh token is missing the 'codespace' scope"],
    ), patch.object(
        m, "scan_config_providers", return_value=_clean_provider_reports()
    ):
        assert m._cmd_doctor() == 1
    assert "codespace" in capsys.readouterr().err
