"""CodespaceSource -- SSH ConfigSource for GitHub Codespaces.

A thin wrapper over the shared :class:`ssh_manager.CodespaceConfigSource`: the
``gh codespace ssh --config`` fetch/parse now lives in ssh-manager so the
agent-bridge daemon (which cannot depend on agent-codespaces for the Session-Host
forward) and agent-codespaces share **one** implementation -- no drift if the gh
config format changes. This wrapper only pins the agent-codespaces config-file
location and keeps the ``CodespaceSource`` name for back-compat.
"""

from __future__ import annotations

from ssh_manager import CodespaceConfigSource

from .config import RUNTIME_DIR

# Where generated codespace SSH config files live (kept under the agent-codespaces
# runtime dir for back-compat with existing tooling/inspection).
SSH_CONFIG_DIR = RUNTIME_DIR / "ssh"


class CodespaceSource(CodespaceConfigSource):
    """ConfigSource for one GitHub Codespace (delegates to the shared parser).

    Pins ``gh codespace ssh --config`` to the gh account that owns the
    CodeSpace (multi-account: #195/#190) when an ``account`` is supplied.
    Callers resolve the account (they usually already have a ``CodespaceInfo``
    or can call :func:`lifecycle.account_for_codespace`); the source itself
    stays side-effect-free at construction. Absent an account it uses ambient
    auth -- today's behavior.

    ``token`` -- when given, uses this EXACT pre-minted token directly
    instead of re-deriving one via ``gh_account.env_for_account`` (which
    silently falls back to AMBIENT credentials when it cannot mint one for
    ``account``). A caller that already validated the account (e.g. a
    claim-provider reclaim) should pass its own already-minted token here
    to close that gap entirely, rather than letting this constructor
    re-derive -- and possibly disagree with, moments later -- credentials
    (claim-provider-pattern effort review finding: "Preserve validated
    credentials during status and reclaim").
    """

    def __init__(
        self, codespace_name: str, *, account: str | None = None,
        token: str | None = None,
    ) -> None:
        if token is not None:
            import os

            gh_env = dict(os.environ)
            gh_env["GH_TOKEN"] = token
            gh_env.pop("GITHUB_TOKEN", None)
        else:
            from . import gh_account

            gh_env = gh_account.env_for_account(account) if account else None
        super().__init__(
            codespace_name, config_dir=SSH_CONFIG_DIR, gh_env=gh_env,
        )
