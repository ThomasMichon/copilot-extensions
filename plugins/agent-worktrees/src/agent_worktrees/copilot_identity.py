"""Ensure Copilot CLI's own inference identity matches an intended account.

Prototype for ThomasMichon/copilot-extensions#3296. Two identity systems
coexist on a machine: the ``gh`` CLI account (already governed by
``repos.py``'s ``account``/``account_map`` + token-injection) and Copilot
CLI's *own* inference identity, tracked in ``~/.copilot/config.json``
(``lastLoggedInUser``/``loggedInUsers``) and shown at runtime by the
``[managedSettings] self-fetch starting for account ...`` log line. Nothing
enforced the latter before this module.

IMPORTANT -- this is a machine-wide shared identity, not a per-process one.
``~/.copilot/config.json`` is one file every ``copilot.exe`` process on the
machine reads, and live observation confirms an already-running session
picks up a change to it during its own lifetime (its periodic managedSettings
self-fetch reflects the new account within the same process, not just at its
own startup). So switching this file while OTHER Copilot sessions are
already running is not merely uncoordinated -- it can splice a single
already-running session's billing across two accounts mid-flight, invalidate
prompt caching tied to the prior identity, and provoke authentication errors,
all while costing real compute/token spend for no benefit. ``ensure_login``
therefore refuses an actual switch (returns ``other-sessions-active``) when
any other Copilot process is currently running, unless the caller passes
``force=True`` with informed consent. This intentionally still does not try
to *coordinate* with those other sessions (e.g. asking them to re-read
config) -- it only avoids stepping on them silently.

Machine-wide **off by default**: the whole feature is gated behind
``config.yaml``'s ``copilot_identity_switch_enabled`` (see
:func:`switch_enabled`, consulted by the ``copilot-identity ensure`` CLI
dispatch). Deliberately a config-file setting rather than an
environment-variable opt-out, so the choice is durable and visible in the
committed/machine-local config rather than only in a shell's environment.
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
    "other-sessions-active",
    "disabled",
]


@dataclass(frozen=True)
class IdentityResult:
    status: Status
    previous: str | None
    target: str | None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("already-correct", "switched", "disabled")


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


def switch_enabled() -> bool:
    """Return whether the identity-switch feature is enabled for this
    machine (``config.yaml``'s ``copilot_identity_switch_enabled``, default
    false). This is the single gate consulted by both the automatic
    ``launch-session.ps1`` check and the manual ``copilot-identity ensure``
    CLI command -- there is no separate environment-variable opt-out.
    Any resolution failure is treated as disabled (fails closed).
    """
    try:
        from . import config as _config

        return bool(_config.load_config().copilot_identity_switch_enabled)
    except Exception:
        return False


def other_copilot_sessions_running() -> int:
    """Count Copilot CLI processes on this machine other than the caller.

    Best-effort (see :func:`agent_worktrees.procs.count_processes_named`):
    an enumeration failure reports 0, which callers must treat as "unknown,
    proceed with normal caution" rather than a verified all-clear -- this
    function can undercount but is not expected to overcount.
    """
    try:
        from . import procs

        return procs.count_processes_named("copilot")
    except Exception:
        return 0


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


def ensure_login(
    account: str | None, *, dry_run: bool = False, force: bool = False
) -> IdentityResult:
    """Ensure Copilot CLI's own identity is ``account`` before it next boots.

    Non-interactive: mints ``account``'s already-authenticated ``gh`` token
    and feeds it to ``copilot login --with-token`` (a supported, documented
    token source -- see ``copilot login --help``). Never prints the token.
    A missing/never-logged-in ``gh`` account for ``account`` is reported as
    ``no-cached-token`` rather than attempting an interactive/device-code
    flow, which would block a non-interactive launch.

    Safety gate: refuses to perform an actual switch (returns
    ``other-sessions-active``) when any other Copilot CLI process is
    currently running on the machine, unless ``force=True``. See this
    module's docstring for why -- the shared identity file is not scoped to
    the process about to launch, and switching it underneath an already
    running session risks splicing that session's billing across accounts,
    invalidating its prompt cache, and causing auth errors. This check only
    applies when a real switch would happen (``previous != account``); it
    never blocks the already-correct or dry-run paths.
    """
    previous = current_login()
    if not account:
        return IdentityResult("no-target", previous, None, "no intended account resolved")
    if previous == account:
        return IdentityResult("already-correct", previous, account)
    if not force and not dry_run:
        other = other_copilot_sessions_running()
        if other > 0:
            return IdentityResult(
                "other-sessions-active",
                previous,
                account,
                f"{other} other Copilot CLI process(es) running on this "
                "machine; switching now risks splicing their billing across "
                "accounts, invalidating their prompt cache, and auth errors. "
                "Close them first, or pass force=True to override with "
                "informed consent.",
            )
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
