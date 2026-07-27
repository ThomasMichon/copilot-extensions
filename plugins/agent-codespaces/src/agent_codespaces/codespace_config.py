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
    """

    def __init__(self, codespace_name: str, *, account: str | None = None) -> None:
        from . import gh_account

        gh_env = gh_account.env_for_account(account) if account else None
        super().__init__(
            codespace_name, config_dir=SSH_CONFIG_DIR, gh_env=gh_env,
        )
