"""Accounts catalog -- gh account identities and their (re)login flows.

Manages ``~/.agent-worktrees/accounts.yaml``, the catalog of GitHub account
*identities* (a ``gh`` account login, its host, the OAuth scopes it should
carry, and how to (re)authenticate it).  This is the identity half of the
repo-scoped multi-account layer:

- **accounts.yaml** (this module) answers *"who are the accounts and how do I
  log each one in?"* -- names + login flows + expected scopes.
- **repos.yaml** ``account_map`` (see :mod:`.repos`) answers *"which account
  does a given GitHub owner/org use?"* -- the decoupled org->account map.

Keeping the two apart lets an org->account mapping in ``repos.yaml`` point at a
login whose auth details (host, scopes, login command) live here, reused by the
scope-preflight/self-heal flows (see #247) without duplicating them per repo.

The catalog is purely descriptive: it never runs ``gh`` itself.  Token minting
stays in :func:`agent_worktrees.git_ops.gh_token_for_account` (``gh auth token
--user <login>``); this module only records *how* an account is expected to be
authenticated so a preflight can compare and a self-heal can suggest the right
command.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import output
from .repos import _quote  # reuse the registry's YAML value quoter

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class AccountEntry:
    """A single gh account identity in the catalog."""

    login: str
    host: str = "github.com"
    # OAuth scopes the account is expected to carry.  Used by scope-preflight
    # (#247) to detect a login that is missing e.g. ``codespace``.
    scopes: list[str] = field(default_factory=list)
    # How to (re)authenticate this account -- a ``gh auth login ...`` command
    # (or free-form note).  Surfaced by self-heal so the operator/agent runs
    # the right flow instead of guessing.
    login_flow: str = ""
    notes: str = ""


@dataclass
class AccountsCatalog:
    """The full accounts.yaml content."""

    accounts: dict[str, AccountEntry] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Read / write
# ---------------------------------------------------------------------------


def _accounts_yaml_path() -> Path:
    """Path to the accounts catalog file."""
    return Path.home() / ".agent-worktrees" / "accounts.yaml"


def read_catalog() -> AccountsCatalog:
    """Load accounts.yaml, returning an empty catalog if missing/invalid."""
    path = _accounts_yaml_path()
    if not path.exists():
        return AccountsCatalog()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return AccountsCatalog()
        raw = data.get("accounts", {})
        accounts: dict[str, AccountEntry] = {}
        if isinstance(raw, dict):
            for login, entry in raw.items():
                if not isinstance(entry, dict):
                    # Bare ``login:`` with no body is a valid minimal entry.
                    accounts[str(login)] = AccountEntry(login=str(login))
                    continue
                raw_scopes = entry.get("scopes", [])
                scopes = (
                    [str(s) for s in raw_scopes]
                    if isinstance(raw_scopes, list)
                    else []
                )
                accounts[str(login)] = AccountEntry(
                    login=str(login),
                    host=str(entry.get("host", "github.com") or "github.com"),
                    scopes=scopes,
                    login_flow=str(entry.get("login_flow", "") or ""),
                    notes=str(entry.get("notes", "") or ""),
                )
        return AccountsCatalog(accounts=accounts)
    except Exception:
        return AccountsCatalog()


def write_catalog(catalog: AccountsCatalog) -> None:
    """Write accounts.yaml with hand-formatted YAML (parallels repos.py)."""
    path = _accounts_yaml_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# ~/.agent-worktrees/accounts.yaml",
        "# Catalog of gh account identities and how to (re)authenticate them.",
        "# The org->account MAP lives in repos.yaml (account_map:); this file is",
        "# the identity catalog those logins point at.",
        "",
    ]
    if catalog.accounts:
        lines.append("accounts:")
        for login in sorted(catalog.accounts.keys()):
            e = catalog.accounts[login]
            lines.append(f"  {login}:")
            if e.host and e.host != "github.com":
                lines.append(f"    host: {_quote(e.host)}")
            if e.scopes:
                rendered = ", ".join(_quote(s) for s in e.scopes)
                lines.append(f"    scopes: [{rendered}]")
            if e.login_flow:
                lines.append(f"    login_flow: {_quote(e.login_flow)}")
            if e.notes:
                lines.append(f"    notes: {_quote(e.notes)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


def list_accounts() -> list[AccountEntry]:
    """Return all catalogued accounts, sorted by login."""
    catalog = read_catalog()
    return sorted(catalog.accounts.values(), key=lambda e: e.login)


def find_account(login: str | None) -> AccountEntry | None:
    """Return the catalog entry for ``login`` (case-insensitive), or None."""
    if not login:
        return None
    catalog = read_catalog()
    entry = catalog.accounts.get(login)
    if entry is not None:
        return entry
    for name, e in catalog.accounts.items():
        if name.casefold() == login.casefold():
            return e
    return None


def set_account(
    login: str,
    *,
    host: str | None = None,
    scopes: list[str] | None = None,
    login_flow: str | None = None,
    notes: str | None = None,
) -> AccountEntry:
    """Add or update an account entry.  Merges with any existing entry."""
    catalog = read_catalog()
    existing = catalog.accounts.get(login)
    if existing:
        if host is not None:
            existing.host = host
        if scopes is not None:
            existing.scopes = list(scopes)
        if login_flow is not None:
            existing.login_flow = login_flow
        if notes is not None:
            existing.notes = notes
        entry = existing
    else:
        entry = AccountEntry(
            login=login,
            host=host or "github.com",
            scopes=list(scopes) if scopes else [],
            login_flow=login_flow or "",
            notes=notes or "",
        )
        catalog.accounts[login] = entry
    write_catalog(catalog)
    output.ok(f"Account '{login}' recorded in accounts.yaml")
    return entry


def remove_account(login: str) -> bool:
    """Remove an account entry.  Returns True if it existed."""
    catalog = read_catalog()
    if login in catalog.accounts:
        del catalog.accounts[login]
        write_catalog(catalog)
        output.ok(f"Account '{login}' removed from accounts.yaml")
        return True
    return False


def login_flow_for(login: str | None) -> str | None:
    """Return the recorded login flow (command/note) for ``login``, or None."""
    entry = find_account(login)
    if entry and entry.login_flow:
        return entry.login_flow
    return None


def scopes_for(login: str | None) -> list[str]:
    """Return the expected OAuth scopes for ``login`` (empty if unknown)."""
    entry = find_account(login)
    return list(entry.scopes) if entry else []
