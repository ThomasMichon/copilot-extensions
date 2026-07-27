"""Tests for the multi-account gh auth preflight in __main__."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from agent_codespaces import __main__ as m

_STATUS = """github.com
  x Logged in to github.com account ThomasMichon (keyring)
  - Active account: true
  - Token scopes: 'codespace', 'gist', 'read:org', 'repo', 'workflow'

  x Logged in to github.com account tmichon_microsoft (keyring)
  - Active account: false
  - Token scopes: 'gist', 'read:org', 'repo', 'workflow'
"""


def test_parse_gh_account_scopes():
    parsed = m._parse_gh_account_scopes(_STATUS)
    assert "codespace" in parsed["ThomasMichon"]
    assert "codespace" not in parsed["tmichon_microsoft"]


def test_preflight_flags_mapped_account_missing_codespace_scope():
    with patch("subprocess.run") as run, \
         patch("agent_codespaces.gh_account.mapped_accounts",
               return_value=("ThomasMichon", "tmichon_microsoft")):
        run.return_value = MagicMock(returncode=0, stdout=_STATUS, stderr="")
        msgs = m._gh_auth_preflight()
    joined = "\n".join(msgs)
    assert "tmichon_microsoft" in joined and "codespace" in joined
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
               return_value=("ThomasMichon", "tmichon_microsoft")):
        run.return_value = MagicMock(returncode=0, stdout=status, stderr="")
        msgs = m._gh_auth_preflight()
    assert msgs == []
