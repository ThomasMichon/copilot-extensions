"""CodeSpace-side relay helper assets.

These shell scripts are deployed *into* a CodeSpace by ``agent-codespaces
ssh`` so that ADO authentication works over the SSH credential-relay
tunnel:

- ``ado-auth-helper-relay`` -- the relay client. Proxies git-credential
  ``get`` and ``get-access-token`` requests over the SSH RemoteForward
  tunnel to the host's credential relay (and on to Git Credential
  Manager). Installed to ``~/.local/bin/ado-auth-helper-relay``.

- ``ado-auth-helper-wrapper`` -- a smart wrapper installed as
  ``~/ado-auth-helper``. When ``LC_GIT_CREDENTIAL_RELAY`` is set (or the
  tunnel port is reachable) it delegates to ``ado-auth-helper-relay``;
  otherwise it falls back to the CodeSpace's native ``ado-auth-helper``
  (backed up to ``~/.ado-auth-helper-vscode``).

The generic git-credential proxy that used to live alongside these is no
longer needed: the CodeSpace's native Git Credential Helper stack already
includes ``ado-auth-helper``, so git credentials resolve through it.

The host-side relay server lives in :mod:`agent_codespaces.credential_relay`.
"""

from __future__ import annotations

import base64
from importlib import resources

__all__ = ["build_provision_command", "asset_text"]

# Asset filename -> remote install path (relative to $HOME)
_RELAY_CLIENT = "ado-auth-helper-relay"
_WRAPPER = "ado-auth-helper-wrapper"


def asset_text(name: str) -> str:
    """Return the text of a packaged CodeSpace asset."""
    return (resources.files(__package__) / name).read_text(encoding="utf-8")


def _b64(name: str) -> str:
    """Base64-encode a packaged asset for safe transport over SSH."""
    raw = asset_text(name).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def build_provision_command() -> str:
    """Build an idempotent bash command that installs the relay helpers.

    The returned command is safe to run on every SSH connect:

    - writes ``~/.local/bin/ado-auth-helper-relay`` (the relay client)
    - installs the smart wrapper as ``~/ado-auth-helper``, backing up the
      CodeSpace's native helper to ``~/.ado-auth-helper-vscode`` the first
      time (it never backs up our own wrapper over a real backup)

    Assets are transported base64-encoded so arbitrary script content
    survives the SSH command line intact.
    """
    relay_b64 = _b64(_RELAY_CLIENT)
    wrapper_b64 = _b64(_WRAPPER)
    return (
        "set -e; "
        'mkdir -p "$HOME/.local/bin"; '
        # Relay client
        f"printf %s {relay_b64} | base64 -d > \"$HOME/.local/bin/ado-auth-helper-relay\"; "
        'chmod +x "$HOME/.local/bin/ado-auth-helper-relay"; '
        # Smart wrapper -> ~/ado-auth-helper, backing up the native helper once
        'if [ -f "$HOME/ado-auth-helper" ] && '
        '! grep -q ado-auth-helper-relay "$HOME/ado-auth-helper" 2>/dev/null; then '
        'cp -f "$HOME/ado-auth-helper" "$HOME/.ado-auth-helper-vscode"; fi; '
        f"printf %s {wrapper_b64} | base64 -d > \"$HOME/ado-auth-helper\"; "
        'chmod +x "$HOME/ado-auth-helper"'
    )
