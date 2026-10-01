"""Post-connect remote-domain auth verification.

After a CodeSpace SSH session is established, the relay forwards git-credential
requests back to the host's Git Credential Manager. If the host has no local
auth for a remote's domain, a ``git fetch`` inside the CodeSpace would fail --
ideally fast (the relay now returns ``quit=1``), but the failure is far more
useful if surfaced *up front* rather than discovered mid-fetch.

This module lists the remote workspace's git remotes, extracts their domains,
and verifies the host has local auth for each domain by probing the same
``GitCredentialSource`` the relay uses (non-interactive, fail-fast). Domains
with no resolvable local credential are reported so the caller can fix auth
(e.g. ``az login`` / GCM sign-in) before the agent starts working.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from credential_relay.sources.git_credential import GitCredentialSource

from .provision import DOTFILES_DIR

log = logging.getLogger("agent-codespaces.auth-preflight")

GITHUB_CREDENTIAL_UNAVAILABLE = "github-credential-unavailable"
GITHUB_CREDENTIAL_AMBIGUOUS = "github-credential-ambiguous"

# Remote command that prints the git remotes of both repos a session touches:
# the workspace/product checkout (preferring the reliable $VM_REPO_PATH set by
# many codespaces devcontainers, falling back to $PWD) AND the account dotfiles
# checkout (so its host -- usually github.com -- is auth-verified too). Bounded;
# never prompts; missing checkouts contribute nothing.
REMOTE_LIST_COMMAND = (
    "{ "
    'git -C "${VM_REPO_PATH:-$PWD}" remote -v 2>/dev/null; '
    f'git -C "{DOTFILES_DIR}" remote -v 2>/dev/null; '
    "git remote -v 2>/dev/null; "
    "} || true"
)


def parse_gh_account_scopes(status_text: str) -> dict[str, set[str]]:
    """Parse ``gh auth status`` into ``{login: {scopes}}``."""
    accounts: dict[str, set[str]] = {}
    current: str | None = None
    for line in status_text.splitlines():
        match = re.search(r"account\s+([A-Za-z0-9_](?:[A-Za-z0-9_-]*[A-Za-z0-9_])?)", line)
        if match:
            current = match.group(1)
            accounts.setdefault(current, set())
        if current and "token scopes" in line.lower():
            accounts[current] |= set(re.findall(r"'([^']+)'", line))
    return accounts


def codespace_scope_accounts() -> tuple[tuple[str, ...], bool]:
    """``(explicit_accounts, uses_ambient)`` for CodeSpace operations."""
    accounts: list[str] = []
    uses_ambient = False
    try:
        from . import account_binding

        accounts.extend(account_binding.bound_accounts())
    except Exception:
        log.debug("could not read CodeSpace account bindings", exc_info=True)
    try:
        from . import gh_account
        from .config import load_merged_config

        cfg = load_merged_config(include_cwd=False)
        for repo in cfg.repos.keys():
            account = gh_account.account_for_repo(repo)
            if account:
                accounts.append(account)
            else:
                uses_ambient = True
    except Exception:
        log.debug("could not resolve configured CodeSpace repo accounts", exc_info=True)
    seen: list[str] = []
    for account in accounts:
        if account and account not in seen:
            seen.append(account)
    return tuple(seen), uses_ambient


def _active_scope_findings(
    lowered: dict[str, set[str]], combined: str
) -> list[str]:
    from . import gh_account

    active = gh_account.active_account()
    scopes = lowered.get(active.casefold()) if active else None
    if active and "codespace" not in {scope.casefold() for scope in (scopes or set())}:
        return [
            f"active gh account '{active}' is missing the 'codespace' scope "
            f"-- run: gh auth refresh -h github.com -u {active} -s codespace"
        ]
    if not active and "codespace" not in combined.lower():
        return [
            "gh token is missing the 'codespace' scope (needed for CodeSpace "
            "operations) -- run: gh auth refresh -h github.com -s codespace"
        ]
    return []


def gh_auth_preflight(status_func, account_login_remedy) -> list[str]:
    """Check gh auth and codespace scope only for CodeSpace-serving accounts."""
    msgs: list[str] = []
    rc, combined = status_func()
    if rc == -1:
        return ["gh CLI not found -- install from https://cli.github.com/ then run: gh auth login"]
    if rc == -2:
        return ["gh auth status timed out -- check your network / gh install."]
    if rc != 0 or "not logged" in combined.lower():
        return ["gh is not authenticated -- run: gh auth login"]

    per_account = parse_gh_account_scopes(combined)
    lowered = {login.casefold(): scopes for login, scopes in per_account.items()}
    accounts, uses_ambient = codespace_scope_accounts()
    if not accounts:
        msgs.extend(_active_scope_findings(lowered, combined))
        return msgs
    if uses_ambient:
        msgs.extend(_active_scope_findings(lowered, combined))
    for login in accounts:
        scopes = lowered.get(login.casefold())
        if scopes is None:
            msgs.append(f"CodeSpace gh account '{login}' is not logged in -- {account_login_remedy(login)}")
        elif "codespace" not in {scope.casefold() for scope in scopes}:
            msgs.append(
                f"CodeSpace gh account '{login}' is missing the 'codespace' scope "
                f"-- run: gh auth refresh -h github.com -u {login} -s codespace"
            )
    return msgs


def host_from_url(url: str) -> str | None:
    """Extract the host from a git remote URL.

    Handles ``https://host/path``, ``ssh://git@host/path`` and the scp-like
    ``git@host:path`` form. Returns ``None`` for unparseable / local paths.
    """
    url = url.strip()
    if not url:
        return None

    # scp-like syntax: [user@]host:path (no scheme, has ':' before any '/')
    if "://" not in url:
        if "@" in url:
            url = url.split("@", 1)[1]
        if ":" in url:
            host = url.split(":", 1)[0]
            return host or None
        return None

    parts = urlsplit(url)
    host = parts.hostname
    return host or None


def parse_remote_hosts(remote_output: str) -> list[str]:
    """Parse unique remote hosts from ``git remote -v`` output.

    Output lines look like ``origin\\thttps://host/org/repo (fetch)``.
    Returns hosts in first-seen order, de-duplicated.
    """
    hosts: list[str] = []
    for line in remote_output.splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        host = host_from_url(fields[1])
        if host and host not in hosts:
            hosts.append(host)
    return hosts


async def host_has_auth(
    host: str,
    *,
    source: GitCredentialSource | None = None,
    timeout: float = 15.0,
) -> bool:
    """Whether the host has resolvable local auth for ``host``.

    Probes the local credential store the same way the relay does. With the
    non-interactive env baked into :class:`GitCredentialSource`, a missing
    credential fails fast (no prompt) rather than hanging.
    """
    src = source or GitCredentialSource()
    try:
        response = await src.resolve(
            "get", {"protocol": "https", "host": host}, timeout=timeout,
        )
    except Exception:
        log.debug("Auth probe for %s raised", host, exc_info=True)
        return False
    return bool(response and "password=" in response)


@dataclass(frozen=True)
class GithubCredentialPreflight:
    """Host-side github.com relay credential readiness."""

    ok: bool
    reason_code: str | None = None
    detail: str = ""
    remedy: str = ""
    source: str | None = None
    account: str | None = None

    def to_dict(self) -> dict[str, str | bool | None]:
        return {
            "ok": self.ok,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "remedy": self.remedy,
            "source": self.source,
            "account": self.account,
        }


def github_credential_remedy(account: str | None, *, ambiguous: bool = False) -> str:
    if ambiguous:
        return (
            "bind the CodeSpace to its GitHub account by running the CodeSpace "
            "operation under the intended account; the relay will pass that "
            "account as the GCM username. Optional interim only: "
            "`git config --global credential.https://github.com.username <account>`."
        )
    user = f" --username {account}" if account else ""
    return (
        f"sign in to GitHub in Git Credential Manager: "
        f"`git credential-manager github login{user}`. Optional interim only: "
        "`git config --global credential.https://github.com.username <account>`."
    )


def gcm_github_accounts(*, timeout: float = 10.0) -> list[str]:
    """Best-effort list of Git Credential Manager github.com accounts."""
    try:
        result = subprocess.run(
            ["git", "credential-manager", "github", "list"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception:
        return []
    if result.returncode != 0:
        return []
    accounts: list[str] = []
    for line in (result.stdout or "").splitlines():
        for token in line.replace(",", " ").split():
            if token and token not in {"x-access-token", "github.com"}:
                accounts.append(token)
                break
    unique: list[str] = []
    for account in accounts:
        if account not in unique:
            unique.append(account)
    return unique


async def github_credential_preflight(
    account: str | None = None,
    *,
    git_source: GitCredentialSource | None = None,
    gcm_accounts: list[str] | None = None,
    timeout: float = 15.0,
) -> GithubCredentialPreflight:
    """Verify the relay can produce a github.com git credential on the host.

    Mirrors the runtime relay order for CodeSpaces: non-interactive Git
    Credential Manager with the per-connection username when available.
    """
    login = (account or "").strip() or None
    fields = {"protocol": "https", "host": "github.com"}
    if login:
        fields["username"] = login

    sources = [git_source or GitCredentialSource(github_username=login)]
    for source in sources:
        try:
            response = await source.resolve("get", dict(fields), timeout=timeout)
        except Exception:
            log.debug(
                "github.com credential preflight source %s raised",
                getattr(source, "name", type(source).__name__),
                exc_info=True,
            )
            response = None
        if response and "password=" in response and "quit=1" not in response:
            return GithubCredentialPreflight(
                ok=True,
                source=getattr(source, "name", type(source).__name__),
                account=login,
            )

    accounts = gcm_accounts if gcm_accounts is not None else gcm_github_accounts()
    if not login and len(accounts) > 1:
        return GithubCredentialPreflight(
            ok=False,
            reason_code=GITHUB_CREDENTIAL_AMBIGUOUS,
            detail=(
                "github.com has multiple host GCM accounts and this CodeSpace "
                "has no bound account to pass as username"
            ),
            remedy=github_credential_remedy(None, ambiguous=True),
        )

    return GithubCredentialPreflight(
        ok=False,
        reason_code=GITHUB_CREDENTIAL_UNAVAILABLE,
        detail=(
            "the host relay could not produce a non-interactive github.com "
            f"credential for {login}" if login
            else "the host relay could not produce a non-interactive "
            "github.com credential from Git Credential Manager"
        ),
        remedy=github_credential_remedy(login),
        account=login,
    )


def run_github_credential_preflight(account: str | None = None) -> GithubCredentialPreflight:
    import asyncio

    return asyncio.run(github_credential_preflight(account))


def emit_github_credential_doctor(result: GithubCredentialPreflight) -> None:
    if result.ok:
        print("[OK] github.com credential relay preflight can produce a credential.")
        return
    print("[github-credential] relay credential issue:", file=sys.stderr)
    print(
        f"  - {result.reason_code}: {result.detail}\n"
        f"    Remedy: {result.remedy}",
        file=sys.stderr,
    )


async def verify_remote_auth(
    run_remote: Callable[[str], Awaitable[str]],
    *,
    source: GitCredentialSource | None = None,
    timeout: float = 15.0,
    extra_hosts: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Verify host auth for every domain the session's git remotes use.

    Probes the union of: the **workspace/product** checkout's remotes, the
    **dotfiles** checkout's remotes (both via ``REMOTE_LIST_COMMAND``), and any
    ``extra_hosts`` the caller guarantees (e.g. the configured dotfiles repo's
    host, so github.com is verified even before the dotfiles clone exists).

    ``run_remote`` runs a shell command on the CodeSpace and returns its stdout
    (used to fetch ``git remote -v``). Returns ``(hosts, missing)`` where
    ``hosts`` is every distinct domain checked and ``missing`` is the subset
    with no resolvable local auth. An empty ``hosts`` list means nothing to
    verify (a no-op). A failure listing remotes does not suppress ``extra_hosts``.
    """
    try:
        remote_output = await run_remote(REMOTE_LIST_COMMAND)
    except Exception:
        log.debug("Could not list remote git remotes", exc_info=True)
        remote_output = ""

    hosts = parse_remote_hosts(remote_output or "")
    for host in extra_hosts or []:
        if host and host not in hosts:
            hosts.append(host)
    if not hosts:
        return [], []

    src = source or GitCredentialSource()
    missing: list[str] = []
    for host in hosts:
        if not await host_has_auth(host, source=src, timeout=timeout):
            missing.append(host)
    return hosts, missing


# The well-known Azure DevOps resource (app) ID. A REST bearer for Azure DevOps
# is an AAD token for this resource's default scope; the relay mints it via the
# host az identity (get-azure-token). See #77.
ADO_REST_RESOURCE = "499b84ac-1321-427f-aa17-267ca6975798"


class AdoRestAuthError(RuntimeError):
    """The host cannot mint an ADO REST bearer and enforcement is enabled (#77).

    Raised by the connect-time preflight so the caller can abort the connect
    cleanly (a clear message + non-zero exit) instead of proceeding into a
    silent, later ADO-REST failure mid-dispatch.
    """


def _ado_scope(resource: str = ADO_REST_RESOURCE) -> str:
    """The v2 default scope for an ADO/AAD resource (``<resource>/.default``)."""
    r = (resource or ADO_REST_RESOURCE).strip().rstrip("/")
    return r if r.endswith("/.default") else r + "/.default"


async def host_can_mint_ado_token(
    resource: str = ADO_REST_RESOURCE, *, timeout: float = 30.0,
) -> bool:
    """Whether the *host* can mint an ADO REST bearer for ``resource``.

    Exercises the same routing the relay uses: an injected test bearer
    (``InjectedTokenSource``) is honored first when present, then the host az
    identity (``AzLoginSource`` + ``get-azure-token`` with scope normalization).
    A True result means a dispatched agent's ``ado-auth-helper get-access-token``
    will succeed over the relay. Never raises; a probe failure returns False.
    """
    scope = _ado_scope(resource)
    # Mirror the relay's source order: a pre-minted injected bearer (a hermetic
    # test venue that cannot run `az login`) satisfies the gate exactly as it
    # serves the runtime request. Inert when its env var is unset (returns None).
    try:
        from credential_relay.sources.injected_token import InjectedTokenSource

        inj = InjectedTokenSource(allowed_resources=["*"])
        resp = await inj.resolve(
            "get-azure-token", {"scope": scope}, timeout=timeout,
        )
        if resp and "token=" in resp:
            return True
    except Exception:
        log.debug("injected ADO REST token probe raised", exc_info=True)
    try:
        from credential_relay.sources.az_login import AzLoginSource

        src = AzLoginSource(allowed_resources=["*"], cache_ttl_override=0)
        resp = await src.resolve(
            "get-azure-token", {"scope": scope}, timeout=timeout,
        )
    except Exception:
        log.debug("ADO REST token probe raised", exc_info=True)
        return False
    return bool(resp and "token=" in resp)


async def enforce_host_ado_login(
    resource: str = ADO_REST_RESOURCE, *, timeout: float = 300.0,
) -> bool:
    """Run ``az login --scope <resource>/.default`` on the host to enforce login.

    Interactive: opens the host's browser for the human at the machine. Bounded
    by ``timeout`` so a connect never blocks indefinitely. Returns True if login
    completed AND the host can subsequently mint the ADO REST token. Never
    raises.
    """
    import asyncio
    import shutil

    az = shutil.which("az")
    if not az:
        log.error("#77 enforce login: az CLI not found on PATH -- cannot sign in")
        return False
    scope = _ado_scope(resource)
    argv = (["cmd", "/c", az] if az.lower().endswith((".cmd", ".bat")) else [az])
    argv += ["login", "--scope", scope, "--only-show-errors"]
    log.warning(
        "#77: host cannot mint an ADO REST bearer -- launching `az login "
        "--scope %s` on the host to enforce sign-in (complete it in the "
        "browser).", scope,
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (TimeoutError, asyncio.TimeoutError):
        log.error("#77 enforce login: `az login` timed out after %.0fs", timeout)
        return False
    except Exception:
        log.error("#77 enforce login: `az login` failed to run", exc_info=True)
        return False
    if proc.returncode != 0:
        log.error(
            "#77 enforce login: `az login --scope %s` failed (exit %d): %s",
            scope, proc.returncode, stderr.decode(errors="replace").strip(),
        )
        return False
    # Confirm the identity can now actually mint the token.
    return await host_can_mint_ado_token(resource)
