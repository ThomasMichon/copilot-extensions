"""Ensure Copilot CLI's own inference identity matches an intended account.

Prototype for ThomasMichon/copilot-extensions#3296. Two identity systems
coexist on a machine: the ``gh`` CLI account (already governed by
``repos.py``'s ``account``/``account_map`` + token-injection) and Copilot
CLI's *own* inference identity, tracked in ``~/.copilot/config.json``
(``lastLoggedInUser``/``loggedInUsers``) and shown at runtime by the
``[managedSettings] self-fetch starting for account ...`` log line. Nothing
enforced the latter before this module.

This intentionally acts at a single choke point -- *before* a new Copilot
session boots (see ``launch-session.ps1``) -- rather than trying to
coordinate already-running concurrent sessions live: many long-lived
``copilot.exe`` processes can share one machine, and racing a shared,
unlocked ``config.json`` against them is far riskier than just guaranteeing
every *new* launch starts correct.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Status = Literal[
    "already-correct",
    "switched",
    "no-cached-token",
    "gh-not-found",
    "copilot-not-found",
    "login-failed",
    "no-target",
]


@dataclass(frozen=True)
class IdentityResult:
    status: Status
    previous: str | None
    target: str | None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("already-correct", "switched")


def _copilot_home() -> Path:
    return Path.home() / ".copilot"


def _copilot_config_path() -> Path:
    return _copilot_home() / "config.json"


def current_login() -> str | None:
    """Return Copilot CLI's currently-recorded login, or None.

    Reads the ``lastLoggedInUser.login`` field of ``~/.copilot/config.json``
    -- the same value the ``self-fetch starting for account`` log line
    reflects. Never raises; a missing/unreadable file just means "unknown".
    """
    path = _copilot_config_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    last = data.get("lastLoggedInUser")
    if isinstance(last, dict):
        login = last.get("login")
        if isinstance(login, str) and login:
            return login
    return None


def intended_account(repo_name: str | None) -> str | None:
    """Resolve the intended Copilot login for a launch.

    ``repo_name`` should be the registered repo/project name for this launch
    (``$script:LaunchProject`` in ``launch-session.ps1``), if known. Falls
    back to the machine's ``default_copilot_account`` when the repo has no
    explicit override, or has no repo context at all (``repo_name=None``).
    """
    if repo_name:
        try:
            from . import repos

            resolved = repos.copilot_account_for(repo_name)
            if resolved:
                return resolved
        except Exception:
            pass
    try:
        from . import config as _config

        return _config.load_config().default_copilot_account or None
    except Exception:
        return None


def _gh_token_for(account: str) -> str | None:
    """Mint a ``gh`` OAuth token for ``account`` without touching the
    machine-global *active* ``gh`` account (mirrors the existing
    ``gh auth token --user <account>`` technique already used in
    ``launch-session.ps1`` for AHP client binding). Returns None (never
    raises) on any failure; the caller turns that into ``no-cached-token``.
    """
    if shutil.which("gh") is None:
        return None
    try:
        proc = subprocess.run(
            ["gh", "auth", "token", "--user", account],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    token = proc.stdout.strip()
    return token or None


def ensure_login(account: str | None, *, dry_run: bool = False) -> IdentityResult:
    """Ensure Copilot CLI's own identity is ``account`` before it next boots.

    Non-interactive: mints ``account``'s already-authenticated ``gh`` token
    and feeds it to ``copilot login --with-token`` (a supported, documented
    token source -- see ``copilot login --help``). Never prints the token.
    A missing/never-logged-in ``gh`` account for ``account`` is reported as
    ``no-cached-token`` rather than attempting an interactive/device-code
    flow, which would block a non-interactive launch.
    """
    previous = current_login()
    if not account:
        return IdentityResult("no-target", previous, None, "no intended account resolved")
    if previous == account:
        return IdentityResult("already-correct", previous, account)
    if dry_run:
        return IdentityResult(
            "switched", previous, account, "dry-run: would switch"
        )
    if shutil.which("gh") is None:
        return IdentityResult("gh-not-found", previous, account, "gh CLI not on PATH")
    if shutil.which("copilot") is None:
        return IdentityResult(
            "copilot-not-found", previous, account, "copilot CLI not on PATH"
        )
    token = _gh_token_for(account)
    if not token:
        return IdentityResult(
            "no-cached-token",
            previous,
            account,
            f"gh has no cached login for '{account}'; run "
            f"'gh auth login' as that account at least once first",
        )
    try:
        proc = subprocess.run(
            ["copilot", "login", "--with-token"],
            input=token,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as exc:  # noqa: BLE001 -- surfaced in .detail, never raised
        return IdentityResult("login-failed", previous, account, str(exc))
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return IdentityResult("login-failed", previous, account, detail)
    return IdentityResult("switched", previous, account, proc.stdout.strip())
