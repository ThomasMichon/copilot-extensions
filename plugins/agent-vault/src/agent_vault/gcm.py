"""Git credential delegation to a Git Credential Manager (GCM) helper.

Some hosts (e.g. GitHub, Azure DevOps over HTTPS) authenticate git with an OAuth
token that only an interactive Git Credential Manager sign-in can mint and cache.
This module lets the vault delegate a git-credential request for an allowlisted
host to the local GCM, returning the resolved credential -- so a caller reaches
one credential surface (the vault) for both stored secrets and GCM-cached tokens.

The mechanism is generic: the host allowlist is configuration-driven
(``VAULT_GCM_HOSTS``), and delegation is independent of the KeePass database --
it never unlocks the vault. A headless or forwarded caller passes
``allow_prompt=False`` so a cache miss fails fast instead of popping a browser or
device-code prompt where the caller cannot complete it.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("agent-vault.gcm")

IS_WINDOWS = os.name == "nt"

# Hosts eligible for GCM delegation (space-separated fnmatch globs). Defaults to
# the common HTTPS-git hosts whose auth relies on a GCM-cached OAuth token.
# Override via VAULT_GCM_HOSTS (empty disables delegation entirely).
DEFAULT_GCM_HOSTS = "github.com gist.github.com dev.azure.com *.visualstudio.com"
GCM_HOSTS_ENV = "VAULT_GCM_HOSTS"
GCM_TIMEOUT_ENV = "VAULT_GCM_TIMEOUT"
DEFAULT_GCM_TIMEOUT = 120

# Set in the delegated child's environment so a nested invocation of our own
# git-credential helper detects the recursion and bails instead of looping.
RECURSION_GUARD_ENV = "GIT_CREDENTIAL_VAULT_FORWARDING"


def gcm_hosts() -> list[str]:
    """The configured GCM delegation allowlist (may be empty)."""
    raw = os.environ.get(GCM_HOSTS_ENV)
    if raw is None:
        raw = DEFAULT_GCM_HOSTS
    return raw.split()


def _gcm_timeout() -> int:
    try:
        return int(os.environ.get(GCM_TIMEOUT_ENV, str(DEFAULT_GCM_TIMEOUT)))
    except (TypeError, ValueError):
        return DEFAULT_GCM_TIMEOUT


def normalize_host(host: str) -> str:
    """Normalize a host for comparison: lowercase, strip the default HTTPS port."""
    host = host.lower().strip()
    if host.endswith(":443"):
        host = host[:-4]
    return host


def is_gcm_allowed(host: str) -> bool:
    """Whether a host matches the configured GCM delegation allowlist."""
    host = normalize_host(host)
    return any(fnmatch.fnmatch(host, pattern.lower()) for pattern in gcm_hosts())


def _find_gcm() -> str | None:
    """Locate git-credential-manager or git-credential-manager-core."""
    for name in ("git-credential-manager", "git-credential-manager-core"):
        path = shutil.which(name)
        if path:
            return path
    # Windows: GCM ships with Git for Windows under its install tree.
    if IS_WINDOWS:
        git_path = shutil.which("git")
        if git_path:
            git_dir = Path(git_path).resolve().parent.parent
            for subdir in ("mingw64/bin", "mingw64/libexec/git-core"):
                candidate = git_dir / subdir / "git-credential-manager.exe"
                if candidate.is_file():
                    return str(candidate)
    return None


_gcm_path_cache: str | None | bool = False  # False = not yet resolved


def _get_gcm_path() -> str | None:
    """Cached GCM path lookup (resolved once per process)."""
    global _gcm_path_cache
    if _gcm_path_cache is False:
        _gcm_path_cache = _find_gcm()
        if _gcm_path_cache:
            log.info("GCM found at %s", _gcm_path_cache)
    return _gcm_path_cache


def _parse_credential_output(stdout: str) -> dict | None:
    """Parse git-credential protocol output into a dict.

    Returns the dict (with ``ok: True``) when it carries at least username and
    password, else ``None``.
    """
    if not stdout or not stdout.strip():
        return None
    result: dict[str, str] = {}
    for line in stdout.strip().splitlines():
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key and value:
            result[key] = value
    if "username" in result and "password" in result:
        return {"ok": True, **result}
    return None


def git_credential_fill(
    protocol: str,
    host: str,
    path: str = "",
    username: str = "",
    allow_prompt: bool = True,
) -> dict | None:
    """Delegate to GCM to obtain a credential for a host.

    Returns a dict with protocol/host/username/password on success, ``None`` on
    failure. Prefers calling git-credential-manager directly to avoid recursion
    through our own credential helper, then falls back to ``git credential fill``
    with a recursion guard. When ``allow_prompt`` is False, GCM is forced
    non-interactive so a cache miss fails fast instead of prompting where the
    caller cannot see it.
    """
    stdin_lines = [f"protocol={protocol}", f"host={normalize_host(host)}"]
    if path:
        stdin_lines.append(f"path={path}")
    if username:
        stdin_lines.append(f"username={username}")
    stdin_lines.append("")  # blank line terminates the request
    stdin_text = "\n".join(stdin_lines) + "\n"

    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    if not allow_prompt:
        env["GCM_INTERACTIVE"] = "never"  # cache only; no GUI/device-code
    env[RECURSION_GUARD_ENV] = "1"

    timeout = _gcm_timeout()

    # Strategy 1: call GCM directly (avoids the credential-helper chain).
    gcm = _get_gcm_path()
    if gcm:
        try:
            r = subprocess.run(  # noqa: S603 -- resolved GCM path, fixed args
                [gcm, "get"],
                input=stdin_text, capture_output=True, text=True,
                timeout=timeout, env=env,
            )
            result = _parse_credential_output(r.stdout)
            if result:
                log.debug("GCM (direct) returned credentials for %s", host)
                return result
        except subprocess.TimeoutExpired:
            log.warning("GCM timed out for %s (%ds)", host, timeout)
        except Exception as exc:  # noqa: BLE001 -- best-effort delegation
            log.debug("GCM direct call failed: %s", exc)

    # Strategy 2: fall back to `git credential fill` with the recursion guard.
    git = shutil.which("git")
    if git:
        try:
            r = subprocess.run(  # noqa: S603 -- resolved git path, fixed args
                [git, "credential", "fill"],
                input=stdin_text, capture_output=True, text=True,
                timeout=timeout, env=env,
            )
            result = _parse_credential_output(r.stdout)
            if result:
                log.debug("git credential fill returned credentials for %s", host)
                return result
        except subprocess.TimeoutExpired:
            log.warning("git credential fill timed out for %s (%ds)", host, timeout)
        except Exception as exc:  # noqa: BLE001 -- best-effort delegation
            log.debug("git credential fill failed: %s", exc)

    return None


def git_credential_action(request: dict) -> dict:
    """Resolve the daemon ``git-credential`` action by delegating to GCM.

    Independent of KeePassXC -- does not require a vault unlock. Honors
    ``allow_prompt`` from the request (False for forwarded/headless callers).
    """
    protocol = request.get("protocol", "https")
    host = request.get("host", "")
    path = request.get("path", "")
    username = request.get("username", "")
    allow_prompt = bool(request.get("allow_prompt", True))

    if not host:
        return {"ok": False, "error": "No host provided"}
    if not is_gcm_allowed(host):
        return {"ok": False, "error": f"Host not in GCM allowlist: {host}"}

    result = git_credential_fill(protocol, host, path, username, allow_prompt=allow_prompt)
    if result:
        return result
    return {"ok": False, "error": f"GCM returned no credentials for {host}"}
